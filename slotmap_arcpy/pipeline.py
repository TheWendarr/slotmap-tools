"""Stage 8 - run every stage for one table. Stages 1-5 and QA are pure Python; 4 (GIS) and 6 (layout) use ArcPy."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import aggregate as A
from . import basemap as B
from .config import SheetConfig
from .gazetteer import GAZ_COLS, Gazetteer, default_data_dir, resolve_rows
from .plan import plan_sheet
from .products import sheet_points, station_table, theater_points, write_csv
from .qa import run_checks, write_report
from .table import ingest

EXIT_OK, EXIT_QA_FAIL, EXIT_INPUT, EXIT_UNRESOLVED = 0, 1, 2, 3
DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "pro_template" / "slotmap_template.aprx"


@dataclass(frozen=True)
class RunOptions:
    table: Path
    out_dir: Path
    config: Optional[Path] = None
    sheet: Optional[str] = None
    gazetteer_local: Optional[Path] = None
    data_dir: Optional[Path] = None             # Natural Earth + town list (default ~/.cache/slotmap)
    template: Optional[Path] = None             # template .aprx (default pro_template/slotmap_template.aprx)
    stop_after: str = "render"                  # check | plan | data | render
    allow_unresolved: bool = False
    write_gpkg: bool = True
    quiet: bool = False
    source_name: Optional[str] = None


def slugify(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower() or "sheet"


def _log(o, msg):
    if not o.quiet:
        print(msg)
        sys.stdout.flush()


def run(o: RunOptions) -> int:
    cfg = SheetConfig.load(o.config)
    out = Path(o.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = slugify(cfg.mos or Path(o.table).stem)
    data_dir = Path(o.data_dir) if o.data_dir else default_data_dir()
    summary = {"slug": slug, "table": str(o.table), "outputs": {}}

    ing = ingest(Path(o.table), o.sheet)
    for w in ing.warnings:
        _log(o, "[WARN] " + w)
    if ing.errors:
        for e in ing.errors:
            print("[ERROR] " + e, file=sys.stderr)
        (out / "ingest_errors.json").write_text(json.dumps(ing.errors, indent=2))
        return EXIT_INPUT
    rows = ing.rows
    total = sum(int(r["SLOTS"]) for r in rows)
    _log(o, "[INFO] {} rows, {} {}".format(len(rows), total, cfg.value_label))

    gaz = Gazetteer.load(o.gazetteer_local, data_dir)
    rep = resolve_rows(rows, gaz, cfg.accept_fuzzy, cfg.fuzzy_auto_accept, cfg.use_places)
    by_station = {}
    for r in rows:
        by_station[r["STATION"]] = by_station.get(r["STATION"], 0) + int(r["SLOTS"])
    zero_names = {k for k, v in by_station.items() if v <= 0} if cfg.exclude_zero else set()
    for r in rep:
        r["NEEDS_REVIEW"] = bool(r["NEEDS_REVIEW"]) and r["STATION"] not in zero_names
    write_csv(out / "resolution_report.csv", rep)
    review = [r for r in rep if r["NEEDS_REVIEW"]]
    dropped, dropped_slots = [], 0
    if review:
        seen = set()
        todo = []
        for r in review:
            if r["STATION"] in seen:
                continue
            seen.add(r["STATION"])
            todo.append({"id": r["GID"] if r["RES_STATUS"] == "fuzzy" else "", "name": r["NAME"], "state": "",
                         "country": "", "lat": "", "lon": "", "aliases": r["STATION"], "coord_quality": "approx",
                         "notes": "suggestions: " + r["SUGGESTIONS"]})
            _log(o, "[REVIEW] {!r}: {}; suggestions: {}".format(r["STATION"], r["RES_STATUS"], r["SUGGESTIONS"]))
        write_csv(out / "gazetteer_local_TODO.csv", todo, GAZ_COLS)
        if not o.allow_unresolved:
            print("[STOP] {} station(s) need coordinates or confirmation. Fill {} and save it as "
                  "gazetteer_local.csv, then re-run.".format(len(todo), out / "gazetteer_local_TODO.csv"),
                  file=sys.stderr)
            return EXIT_UNRESOLVED
        bad = {r["SRC_ROW"] for r in review if r["LAT"] is None}
        dropped = sorted({r["STATION"] for r in review if r["LAT"] is None})
        dropped_slots = sum(int(r["SLOTS"]) for r in rows if r["SRC_ROW"] in bad)
        rows = [r for r in rows if r["SRC_ROW"] not in bad]
    if o.stop_after == "check":
        _log(o, "[DONE] check passed")
        return EXIT_OK

    for r in rows:   # label overrides by GID or raw name
        lo = cfg.label_overrides.get(r["GID"]) or cfg.label_overrides.get(r["STATION"]) or {}
        if lo.get("name"):
            r["NAME"] = lo["name"]
    st_all = A.enrich(A.merge_duplicates(rows), total, cfg.classes.breaks_pct)
    zero = [s for s in st_all if s["SLOTS"] <= 0] if cfg.exclude_zero else []
    zgid = {s["GID"] for s in zero}
    st = A.rank([s for s in st_all if s["GID"] not in zgid])
    if not st:
        msg = "Nothing to map: no station with {} has coordinates.".format(cfg.value_label)
        print("[ERROR] " + msg, file=sys.stderr)
        (out / "ingest_errors.json").write_text(json.dumps([msg], indent=2))
        return EXIT_INPUT
    th = A.theater_totals(st, total, cfg.classes.breaks_pct)
    groups = A.group_totals(st_all, total)
    merged = [s["NAME"] for s in st_all if s["N_RECORDS"] > 1]
    fuzzy = sorted({"{} → {}".format(r["STATION"], r["NAME"]) for r in rep
                    if r["RES_STATUS"] == "fuzzy" and not r["NEEDS_REVIEW"]})
    towns = sorted({"{} → {}".format(r["STATION"], r["NAME"]) for r in rep
                    if r["RES_STATUS"] == "city" and r["STATION"] not in zero_names})

    plan = plan_sheet(st, th, cfg, B.places(data_dir))
    plan.save(out / "frame_plan.json")
    for w in plan.warnings:
        _log(o, "[WARN] " + w)
    _log(o, "[INFO] frames: " + ", ".join("{}={} ({})".format(f.letter, f.title, len(f.members)) for f in plan.frames))
    if o.stop_after == "plan":
        return EXIT_OK

    letters = {g: f.letter for f in plan.frames for g in f.members}
    table = station_table(st_all, letters)
    write_csv(out / "duty_station_table.csv", table)
    sp = sheet_points(plan, st, cfg.value_label)
    tp = theater_points(plan, th, cfg.value_label)
    checks = run_checks(plan, st, total, ing.stated_total, sum(s["SLOTS"] for s in zero) + dropped_slots,
                        dropped, fuzzy)
    meta = {"total": total, "source_name": o.source_name or Path(o.table).name,
            "excluded": [s["STATION"] for s in zero], "merged": merged, "fuzzy": fuzzy, "dropped": dropped,
            "towns": towns, "basemap": B.available(data_dir)}
    extra = {"summary": summary, "frames": {f.letter: {"title": f.title, "members": f.members} for f in plan.frames}}
    if o.stop_after in ("data", "render"):
        from .products import write_gis
        summary["outputs"].update(write_gis(out, slug, rows, table, groups, th, plan, sp, tp, o.write_gpkg))
        _log(o, "[INFO] wrote {}".format(summary["outputs"].get("gdb")))
    if o.stop_after == "render":
        from .render import TemplateError, render_layout
        tmpl = Path(o.template) if o.template else DEFAULT_TEMPLATE
        if not tmpl.exists():
            print("[ERROR] template project not found: {} (see pro_template/TEMPLATE.md)".format(tmpl), file=sys.stderr)
            return EXIT_INPUT
        if not B.available(data_dir):
            _log(o, "[WARN] Natural Earth not found in {} - maps are drawn without a basemap.".format(
                B.ne_dir(data_dir)))
        try:
            info = render_layout(plan, st, th, cfg, meta, summary["outputs"]["gdb"], tmpl, out, slug, data_dir, sp, tp)
        except TemplateError as e:
            print("[ERROR] " + str(e), file=sys.stderr)
            return EXIT_INPUT
        summary["outputs"]["sheet"] = info["files"]
        extra["footer_notes"] = info["footer_notes"]
        if info["warnings"]:
            checks.append({"check": "render", "status": "warn", "detail": info["warnings"]})
    rep_json = write_report(out / "qa_report.json", checks, extra)
    for c in checks:
        if c["status"] != "pass":
            _log(o, "[QA:{}] {}: {}".format(c["status"], c["check"], c["detail"]))
    _log(o, "[DONE] QA {} - {}".format(rep_json["status"], ", ".join(summary["outputs"].get("sheet", [])) or out))
    return EXIT_QA_FAIL if rep_json["status"] == "fail" else EXIT_OK
