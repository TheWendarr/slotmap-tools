"""8.3 `slotmap` command line (same conventions as fgdb-tools: argparse, Options dataclass, int exit codes)."""
from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import SheetConfig
from .pipeline import RunOptions, run


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("table", help="Slot table: .csv / .tsv / .xlsx")
    p.add_argument("--config", "-c", default=None, help="sheet.yaml (titles, classes, overrides)")
    p.add_argument("--out", "-o", default=None, help="Output folder (default: out/<table stem>)")
    p.add_argument("--sheet", default=None, help="Excel sheet name")
    p.add_argument("--gazetteer", default=None, help="Local gazetteer CSV (default: ./gazetteer_local.csv)")
    p.add_argument("--basemap", default=None, help="Natural Earth folder (default: ~/.cache/slotmap/natural_earth)")
    p.add_argument("--allow-unresolved", action="store_true", help="Drop stations without coordinates instead of stopping")
    p.add_argument("--quiet", action="store_true")


def _opts(a, stop_after: str) -> RunOptions:
    out = Path(a.out) if a.out else Path("out") / Path(a.table).stem
    return RunOptions(table=Path(a.table), out_dir=out, config=Path(a.config) if a.config else None, sheet=a.sheet,
                      gazetteer_local=Path(a.gazetteer) if a.gazetteer else None,
                      basemap_dir=Path(a.basemap) if a.basemap else None, stop_after=stop_after,
                      allow_unresolved=a.allow_unresolved, write_gdb=not getattr(a, "no_gdb", False),
                      exports=list(getattr(a, "export", []) or []), quiet=a.quiet)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slotmap", description="Duty-station slot tables -> GIS layers + map sheet.")
    p.add_argument("--version", action="version", version=f"slotmap {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="Start a new sheet folder: sheet.yaml + table template")
    s.add_argument("folder")
    s.add_argument("--mos", default="")

    s = sub.add_parser("fetch-basemap", help="Download Natural Earth layers (~90 MB, once)")
    s.add_argument("--dest", default=None)
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("check", help="Ingest + resolve only; fast loop for fixing names")
    _common(s)
    s = sub.add_parser("plan", help="Through frame planning; writes frame_plan.json + mockup PNG")
    _common(s)
    s = sub.add_parser("data", help="Through data products (GeoPackage + FileGDB)")
    _common(s)
    s.add_argument("--no-gdb", action="store_true")
    s.add_argument("--export", action="append", choices=["geojson", "shp", "kml"])
    s = sub.add_parser("run", help="Everything: data, plan, mockup, sheet, QA")
    _common(s)
    s.add_argument("--no-gdb", action="store_true", help="Skip the Esri FileGDB output")
    s.add_argument("--export", action="append", choices=["geojson", "shp", "kml"], help="Also export layers (repeatable)")

    s = sub.add_parser("batch", help="Position exports in, every map sheet out (split by CMF / MOS / skill level)")
    s.add_argument("tables", nargs="+", help="Tables, folders or glob patterns (.xlsx/.xls/.csv/.tsv)")
    s.add_argument("--out", "-o", default="out/batch", help="Output folder (default: out/batch)")
    s.add_argument("--config", "-c", default=None, help="Base sheet.yaml applied to every sheet")
    s.add_argument("--overrides", default=None,
                   help="Folder of per-MOS tweaks: 35G.yaml (every 35G sheet), 35G3.yaml or 35G_SL3.yaml (one sheet), "
                        "35.yaml (whole CMF). Default: ./overrides if it exists")
    s.add_argument("--by", choices=["level", "mos", "both"], default="level",
                   help="level = one sheet per skill level (grade for WO/officer); mos = one per MOS; both")
    s.add_argument("--compo", choices=["ALL", "AC", "RC"], default="ALL", help="Filter on a COMPO column")
    s.add_argument("--mos", nargs="*", default=[], help="Only these MOS (e.g. --mos 35G 35F)")
    s.add_argument("--cmf", nargs="*", default=[], help="Only these CMFs (e.g. --cmf 35 25)")
    s.add_argument("--sheet", default=None, help="Excel sheet name (default: every sheet with data)")
    s.add_argument("--gazetteer", default=None, help="Local gazetteer CSV (default: ./gazetteer_local.csv)")
    s.add_argument("--basemap", default=None)
    s.add_argument("--prepared-by", default=None, help="Footer credit for every sheet")
    s.add_argument("--title", default=None, help="Title template; fields {mos} {level} {level_long} {grade} {cmf} {name}")
    s.add_argument("--allow-unresolved", action="store_true",
                   help="Build even if some stations have no coordinates (they are listed as dropped)")
    s.add_argument("--jobs", "-j", type=int, default=0, help="Sheets built in parallel (default: CPUs, max 4)")
    s.add_argument("--no-gdb", action="store_true")
    s.add_argument("--export", action="append", choices=["geojson", "shp", "kml"])
    s.add_argument("--zip", action="store_true", help="Also zip the whole output folder")
    s.add_argument("--list", action="store_true", help="Only list the sheets that would be built")
    s.add_argument("--quiet", action="store_true")

    s = sub.add_parser("gazetteer", help="Search the installation gazetteer")
    s.add_argument("action", choices=["find", "list"])
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--country", default="")
    s.add_argument("--local", default=None)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "init":
        d = Path(a.folder)
        d.mkdir(parents=True, exist_ok=True)
        cfg = SheetConfig(mos=a.mos)
        cfg.save(d / "sheet.yaml")
        tmpl = Path(__file__).resolve().parents[2] / "templates" / "slots_template.csv"
        if tmpl.exists():
            shutil.copy(tmpl, d / "slots.csv")
        else:
            (d / "slots.csv").write_text("LOCATION,STATENAME,CNTRY,TOTAUTH,Total Percent\n", encoding="utf-8")
        print(f"[DONE] {d}/sheet.yaml and {d}/slots.csv created. Fill slots.csv, then: "
              f"slotmap run {d}/slots.csv -c {d}/sheet.yaml -o {d}/out")
        return 0
    if a.cmd == "fetch-basemap":
        from . import places
        from .basemap import fetch
        print(f"[DONE] basemap in {fetch(Path(a.dest) if a.dest else None, a.force)}")
        print(f"[DONE] town list in {places.fetch(Path(a.dest) if a.dest else None, a.force)}")
        return 0
    if a.cmd == "batch":
        import os
        from .batch import DEFAULT_TITLE, BatchOptions, run_batch
        ov = Path(a.overrides) if a.overrides else (Path("overrides") if Path("overrides").is_dir() else None)
        jobs = a.jobs or min(4, os.cpu_count() or 1)
        return run_batch(BatchOptions(
            tables=[Path(t) for t in a.tables], out_dir=Path(a.out), config=Path(a.config) if a.config else None,
            overrides=ov, by=a.by, compo=a.compo, mos=a.mos, cmf=a.cmf, sheet=a.sheet,
            gazetteer_local=Path(a.gazetteer) if a.gazetteer else None,
            basemap_dir=Path(a.basemap) if a.basemap else None, allow_unresolved=a.allow_unresolved, jobs=jobs,
            write_gdb=not a.no_gdb, exports=a.export or [], prepared_by=a.prepared_by,
            title=a.title or DEFAULT_TITLE, zip=a.zip, list_only=a.list, quiet=a.quiet))
    if a.cmd == "gazetteer":
        from .gazetteer import Gazetteer, canon_country
        g = Gazetteer.load(Path(a.local) if a.local else None)
        df = g.df
        if a.country:
            df = df[df.country == canon_country(a.country)]
        if a.action == "find":
            m = g.resolve(a.query, country=a.country)
            print(f"{m.status} ({m.score:.2f}): {m.name} [{m.gid}] {m.lat}, {m.lon}")
            if m.suggestions:
                print("suggestions:", "; ".join(m.suggestions))
            return 0 if m.status != "unresolved" else 3
        print(df[["id", "name", "state", "country", "lat", "lon"]].to_string(index=False))
        return 0
    stop = {"check": "check", "plan": "plan", "data": "data", "run": "render"}[a.cmd]
    return run(_opts(a, stop))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
