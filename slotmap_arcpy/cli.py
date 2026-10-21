"""`python -m slotmap_arcpy <command>` - stdlib argparse, integer exit codes."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import SheetConfig

STOPS = {"check": "check", "plan": "plan", "tables": "tables", "data": "data", "run": "render"}


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("table", help="Slot table: .xlsx / .csv / .tsv (.xls needs ArcPy)")
    p.add_argument("--config", "-c", default=None, help="sheet.json (titles, classes, overrides)")
    p.add_argument("--out", "-o", default=None, help="Output folder (default: out/<table stem>)")
    p.add_argument("--sheet", default=None, help="Excel sheet name")
    p.add_argument("--gazetteer", default=None, help="Local gazetteer CSV (default: .\\gazetteer_local.csv)")
    p.add_argument("--data-dir", default=None, help="Folder holding natural_earth\\ and places\\ "
                                                     "(default: %%USERPROFILE%%\\.cache\\slotmap or SLOTMAP_DATA)")
    p.add_argument("--template", default=None, help="Template .aprx (default: pro_template\\slotmap_template.aprx)")
    p.add_argument("--allow-unresolved", action="store_true")
    p.add_argument("--no-gpkg", action="store_true", help="FileGDB only")
    p.add_argument("--quiet", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m slotmap_arcpy",
                                description="Duty-station slot tables -> FileGDB + ArcGIS Pro map sheet (stdlib + ArcPy).")
    p.add_argument("--version", action="version", version="slotmap_arcpy " + __version__)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("init", help="New sheet folder: sheet.json + blank slots.csv")
    s.add_argument("folder")
    s.add_argument("--mos", default="")
    for name, hlp in [("check", "Read + resolve names only (no ArcPy)"),
                      ("plan", "Through frame planning: frame_plan.json (no ArcPy)"),
                      ("tables", "Everything except GIS and layout: CSVs + QA (no ArcPy)"),
                      ("data", "Through the FileGDB / GeoPackage (ArcPy)"),
                      ("run", "Everything: data, layout, PDF/PNG, .aprx, QA (ArcPy)")]:
        _common(sub.add_parser(name, help=hlp))
    s = sub.add_parser("batch", help="Position exports in, every map sheet out (split by CMF / MOS / skill level)")
    s.add_argument("tables", nargs="+", help="Tables, folders or wildcard patterns")
    s.add_argument("--out", "-o", default="out/batch")
    s.add_argument("--config", "-c", default=None, help="Base sheet.json applied to every sheet")
    s.add_argument("--overrides", default=None, help="Folder of 35G.json / 35G3.json / 35.json tweaks "
                                                    "(default: .\\overrides if it exists)")
    s.add_argument("--by", choices=["level", "mos", "both"], default="level")
    s.add_argument("--compo", choices=["ALL", "AC", "RC"], default="ALL")
    s.add_argument("--mos", nargs="*", default=[])
    s.add_argument("--cmf", nargs="*", default=[])
    s.add_argument("--sheet", default=None)
    s.add_argument("--gazetteer", default=None)
    s.add_argument("--data-dir", default=None)
    s.add_argument("--template", default=None)
    s.add_argument("--prepared-by", default=None)
    s.add_argument("--title", default=None, help="Fields: {mos} {level} {level_long} {grade} {cmf} {name}")
    s.add_argument("--allow-unresolved", action="store_true")
    s.add_argument("--jobs", "-j", type=int, default=1, help="Sheets in parallel (each starts its own ArcPy)")
    s.add_argument("--no-gpkg", action="store_true")
    s.add_argument("--zip", action="store_true")
    s.add_argument("--list", action="store_true")
    s.add_argument("--tables-only", action="store_true", help="Stop before ArcPy (CSV tables + QA per sheet)")
    s.add_argument("--quiet", action="store_true")
    s = sub.add_parser("check-template", help="Check the template project and list what was found")
    s.add_argument("--template", default=None)
    s = sub.add_parser("fetch-basemap", help="Download Natural Earth + the town list (needs internet)")
    s.add_argument("--data-dir", default=None)
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("gazetteer", help="Search the installation gazetteer")
    s.add_argument("action", choices=["find", "list"])
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--country", default="")
    s.add_argument("--local", default=None)
    s.add_argument("--data-dir", default=None)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    if a.cmd == "init":
        d = Path(a.folder)
        d.mkdir(parents=True, exist_ok=True)
        SheetConfig(mos=a.mos).save(d / "sheet.json")
        tmpl = Path(__file__).resolve().parents[1] / "templates" / "slots_template.csv"
        if tmpl.exists():
            shutil.copy(str(tmpl), str(d / "slots.csv"))
        else:
            (d / "slots.csv").write_text("LOCATION,STATENAME,CNTRY,TOTAUTH,Total Percent\n", encoding="utf-8")
        print("[DONE] {0}\\sheet.json and {0}\\slots.csv created.".format(d))
        return 0
    if a.cmd == "fetch-basemap":
        from . import basemap, places
        from .gazetteer import default_data_dir
        dd = Path(a.data_dir) if a.data_dir else default_data_dir()
        print("[DONE] basemap in {}".format(basemap.fetch(dd, a.force)))
        print("[DONE] town list in {}".format(places.fetch(dd, a.force)))
        return 0
    if a.cmd == "check-template":
        from .pipeline import DEFAULT_TEMPLATE
        from .render import TemplateError, check_template
        t = Path(a.template) if a.template else DEFAULT_TEMPLATE
        if not t.exists():
            print("[ERROR] not found: {} (see pro_template/TEMPLATE.md)".format(t), file=sys.stderr)
            return 2
        work = Path("out") / "_template_check"
        work.mkdir(parents=True, exist_ok=True)
        try:
            for line in check_template(t, work):
                print("[OK] " + line)
        except TemplateError as e:
            print("[ERROR] {}".format(e), file=sys.stderr)
            return 2
        return 0
    if a.cmd == "gazetteer":
        from .gazetteer import Gazetteer, canon_country
        g = Gazetteer.load(Path(a.local) if a.local else None, Path(a.data_dir) if a.data_dir else None)
        if a.action == "find":
            m = g.resolve(a.query, country=a.country)
            print("{} ({:.2f}): {} [{}] {}, {}".format(m.status, m.score, m.name, m.gid, m.lat, m.lon))
            if m.suggestions:
                print("suggestions: " + "; ".join(m.suggestions))
            return 0 if m.status != "unresolved" else 3
        cc = canon_country(a.country) if a.country else ""
        for r in g.rows:
            if not cc or r["country"] == cc:
                print("{:16s} {:34s} {:3s} {:15s} {:9.4f} {:10.4f}".format(r["id"], r["name"], r["state"], r["country"],
                                                                           r["lat"], r["lon"]))
        return 0
    if a.cmd == "batch":
        from .batch import DEFAULT_TITLE, BatchOptions, run_batch
        ov = Path(a.overrides) if a.overrides else (Path("overrides") if Path("overrides").is_dir() else None)
        return run_batch(BatchOptions(
            tables=[Path(t) for t in a.tables], out_dir=Path(a.out), config=Path(a.config) if a.config else None,
            overrides=ov, by=a.by, compo=a.compo, mos=a.mos, cmf=a.cmf, sheet=a.sheet,
            gazetteer_local=Path(a.gazetteer) if a.gazetteer else None,
            data_dir=Path(a.data_dir) if a.data_dir else None, template=Path(a.template) if a.template else None,
            allow_unresolved=a.allow_unresolved, jobs=a.jobs, write_gpkg=not a.no_gpkg, prepared_by=a.prepared_by,
            title=a.title or DEFAULT_TITLE, zip=a.zip, list_only=a.list, quiet=a.quiet,
            stop_after="tables" if a.tables_only else "render"))
    from .pipeline import RunOptions, run
    out = Path(a.out) if a.out else Path("out") / Path(a.table).stem
    return run(RunOptions(table=Path(a.table), out_dir=out, config=Path(a.config) if a.config else None,
                          sheet=a.sheet, gazetteer_local=Path(a.gazetteer) if a.gazetteer else None,
                          data_dir=Path(a.data_dir) if a.data_dir else None,
                          template=Path(a.template) if a.template else None, stop_after=STOPS[a.cmd],
                          allow_unresolved=a.allow_unresolved, write_gpkg=not a.no_gpkg, quiet=a.quiet))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
