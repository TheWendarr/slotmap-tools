"""8.2 Orchestrate every stage. Each stage writes to the output folder so it can be inspected."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pandas as pd

from . import basemap as B
from .aggregate import enrich, group_totals, merge_duplicates, theater_totals
from .config import SheetConfig
from .gazetteer import GAZ_COLS, Gazetteer, resolve_table
from .ingest import ingest
from .plan import Plan, plan_sheet

EXIT_OK, EXIT_QA_FAIL, EXIT_INPUT, EXIT_UNRESOLVED = 0, 1, 2, 3
STAGES = ("check", "plan", "data", "render")


@dataclass(frozen=True)
class RunOptions:
    table: Path
    out_dir: Path
    config: Optional[Path] = None
    sheet: Optional[str] = None                 # Excel sheet name
    gazetteer_local: Optional[Path] = None
    basemap_dir: Optional[Path] = None
    stop_after: str = "render"                  # check | plan | data | render
    allow_unresolved: bool = False
    write_gdb: bool = True
    exports: List[str] = field(default_factory=list)
    quiet: bool = False


def slugify(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower() or "sheet"


def _log(opts, msg):
    if not opts.quiet:
        print(msg)


def run(opts: RunOptions) -> int:
    cfg = SheetConfig.load(opts.config)
    out = Path(opts.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = slugify(cfg.mos or Path(opts.table).stem)
    summary = {"slug": slug, "table": str(opts.table), "outputs": {}}

    # 1 ingest
    ing = ingest(Path(opts.table), opts.sheet)
    for w in ing.warnings:
        _log(opts, f"[WARN] {w}")
    if ing.errors:
        for e in ing.errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        (out / "ingest_errors.json").write_text(json.dumps(ing.errors, indent=2))
        return EXIT_INPUT
    total = int(ing.table.SLOTS.sum())
    _log(opts, f"[INFO] {len(ing.table)} rows, {total} {cfg.value_label}"
         + (f" (table total row: {ing.stated_total})" if ing.stated_total is not None else ""))

    # 2 resolve
    gaz = Gazetteer.load(opts.gazetteer_local)
    df, rep = resolve_table(ing.table, gaz, cfg.accept_fuzzy)
    rep.to_csv(out / "resolution_report.csv", index=False)
    df.to_csv(out / f"{slug}_normalized.csv", index=False)
    review = rep[rep.NEEDS_REVIEW]
    counts = rep.RES_STATUS.value_counts().to_dict()
    _log(opts, f"[INFO] resolution: {counts}")
    dropped: List[str] = []
    if len(review):
        todo = pd.DataFrame({"id": [r.GID if r.RES_STATUS == "fuzzy" else "" for r in review.itertuples()],
                             "name": review.NAME, "state": "", "country": "", "lat": "", "lon": "",
                             "aliases": review.STATION, "coord_quality": "local",
                             "notes": "suggestions: " + review.SUGGESTIONS})[GAZ_COLS]
        todo.to_csv(out / "gazetteer_local_TODO.csv", index=False)
        for r in review.itertuples():
            _log(opts, f"[REVIEW] {r.STATION!r}: {r.RES_STATUS}; suggestions: {r.SUGGESTIONS}")
        if not opts.allow_unresolved:
            print(f"[STOP] {len(review)} station(s) need coordinates or confirmation. Fill "
                  f"{out / 'gazetteer_local_TODO.csv'} and save it as gazetteer_local.csv (or add LAT/LON columns "
                  "to the table, or set accept_fuzzy: true), then re-run.", file=sys.stderr)
            return EXIT_UNRESOLVED
        bad = review[review.LAT.isna()]
        dropped = bad.STATION.tolist()
        df = df[~df.SRC_ROW.isin(bad.SRC_ROW)]
    if opts.stop_after == "check":
        _log(opts, "[DONE] check passed")
        return EXIT_OK

    # 3 aggregate
    st_all = merge_duplicates(df)
    st_all = enrich(st_all, total, cfg.classes.breaks_pct)
    zero = st_all[st_all.SLOTS <= 0] if cfg.exclude_zero else st_all.iloc[0:0]
    st = st_all[~st_all.GID.isin(zero.GID)].reset_index(drop=True)
    st = st.sort_values(["SLOTS", "NAME"], ascending=[False, True]).reset_index(drop=True)
    st["RANK"] = range(1, len(st) + 1)
    th = theater_totals(st, total, cfg.classes.breaks_pct)
    grp = group_totals(st_all, total)
    merged = st_all[st_all.N_RECORDS > 1].NAME.tolist()
    fuzzy = [f"{r.STATION} → {r.NAME}" for r in rep[rep.RES_STATUS == "fuzzy"].itertuples() if not r.NEEDS_REVIEW]

    # 4 plan
    bm = B.Basemap.load(opts.basemap_dir)
    if bm is None:
        _log(opts, "[WARN] Natural Earth basemap not found - run `slotmap fetch-basemap`. Rendering without it.")
    plan: Plan = plan_sheet(st, th, cfg, bm)
    plan.save(out / "frame_plan.json")
    from .mockup import render_mockup
    render_mockup(plan, st, cfg, out / f"{slug}_mockup.png")
    summary["outputs"]["plan"] = str(out / "frame_plan.json")
    summary["outputs"]["mockup"] = str(out / f"{slug}_mockup.png")
    for w in plan.warnings:
        _log(opts, f"[WARN] {w}")
    _log(opts, "[INFO] frames: " + ", ".join(f"{f.letter}={f.title} ({len(f.members)})" for f in plan.frames))
    if opts.stop_after == "plan":
        _log(opts, f"[DONE] plan + mockup in {out}")
        return EXIT_OK

    # 5 data products
    from .products import (build_frame_extents, build_points, build_station_points, build_station_table,
                           write_all)
    letters = {g: f.letter for f in plan.frames for g in f.members}
    table = build_station_table(st_all, letters)
    table.to_csv(out / "duty_station_table.csv", index=False)
    layers = {
        "auth_points": build_points(df, st_all),
        "duty_stations": build_station_points(table[table.LAT.notna()]),
        "duty_station_table": table.drop(columns=["LAT", "LON"]),
        "group_totals": grp,
        "theater_totals": th,
        "frame_extents": build_frame_extents(plan),
    }
    summary["outputs"].update(write_all(layers, out, slug, gdb=opts.write_gdb, exports=list(opts.exports)))
    _log(opts, f"[INFO] wrote {summary['outputs'].get('gpkg')}" +
         (f" and {summary['outputs']['gdb']}" if "gdb" in summary["outputs"] else ""))
    if opts.stop_after == "data":
        return EXIT_OK

    # 6 render + 7 QA
    from .qa import run_checks, write_crops, write_report
    from .render import SheetMeta, SheetRenderer
    meta = SheetMeta(total=total, source_name=Path(opts.table).name, excluded=zero.STATION.tolist(),
                     merged=merged, fuzzy=fuzzy, dropped=dropped,
                     approx_coords=bool((st.COORD_QUALITY.isin(["approx", "region"])).any()))
    info = SheetRenderer(plan, st, th, cfg, meta, bm).render(out / f"{slug}_sheet")
    summary["outputs"]["sheet"] = info["files"]
    png = next((p for p in info["files"] if p.endswith(".png")), None)
    crops = write_crops(Path(png), plan, out / "crops", cfg.page.dpi) if png else []
    checks = run_checks(plan, st, total, ing.stated_total, int(zero.SLOTS.sum()), dropped, fuzzy, info)
    rep_json = write_report(out / "qa_report.json", checks, {"summary": summary, "crops": crops,
                                                             "footer_notes": info["footer_notes"],
                                                             "frames": info["frames"]})
    for c in checks:
        if c["status"] != "pass":
            _log(opts, f"[QA:{c['status']}] {c['check']}: {c['detail']}")
    _log(opts, f"[DONE] QA {rep_json['status']} - sheet: {', '.join(info['files'])}")
    return EXIT_QA_FAIL if rep_json["status"] == "fail" else EXIT_OK
