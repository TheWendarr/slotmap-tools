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
        from .basemap import fetch
        print(f"[DONE] basemap in {fetch(Path(a.dest) if a.dest else None, a.force)}")
        return 0
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
