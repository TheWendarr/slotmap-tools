"""Stage 9 - batch: one or more position exports in, every map sheet out (stdlib + ArcPy).

    python -m slotmap_arcpy batch data\\35G.xlsx data\\CMF25.xlsx -o out\\batch --prepared-by "SSG McGowan"

Splits rows by CMF -> MOS -> skill level (enlisted) or grade (warrant/officer, when a GRADE column exists),
resolves every station once, writes table.csv + sheet.json per split, builds each sheet, and writes
index.csv / index.md and a sheets\\ folder with every PDF.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import SheetConfig, deep_merge, read_json, write_json
from .gazetteer import GAZ_COLS, Gazetteer, default_data_dir, resolve_rows
from .products import write_csv
from .table import column_map, find_col, normalize, read_any, split_total_row

EXIT_OK, EXIT_QA_FAIL, EXIT_INPUT, EXIT_UNRESOLVED = 0, 1, 2, 3

CODE_ALIASES = ["POSCO", "POS CO", "POSITION CODE", "POSN CODE", "MOS", "PMOS", "DMOS", "MOSC", "MOS CODE",
                "AOC", "AOC MOS", "MOS AOC", "AOC/MOS", "SPECIALTY"]
GRADE_ALIASES = ["GRADE", "GRD", "PAY GRADE", "PAYGRADE", "RANK", "AUTH GRADE", "POS GRADE"]
COMPO_ALIASES = ["COMPO", "COMPONENT", "CMP", "COMP"]
COMPO_AC = {"1", "AC", "ACTIVE", "RA", "COMPO 1"}
COMPO_RC = {"2", "3", "6", "NG", "ARNG", "AR", "USAR", "RC", "COMPO 2", "COMPO 3"}
SKILL_GRADES = {"1": "PVT–SPC", "2": "SGT", "3": "SSG", "4": "SFC", "5": "MSG–SGM"}
DEFAULT_TITLE = "{mos} {level_long}: Authorized Positions by Duty Station"


@dataclass(frozen=True)
class Code:
    cmf: str
    mos: str
    level: str
    kind: str


_ENL = re.compile(r"^(\d\d)([A-Z])([1-5])")
_WO = re.compile(r"^(\d{3})([A-Z])")
_OFF = re.compile(r"^(\d\d)([A-Z])")


def parse_code(code: str, grade: str = "") -> Code:
    c = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
    g = re.sub(r"\s+", "", str(grade or "").upper()) or "ALL"
    if g in ("NAN", "NONE"):
        g = "ALL"
    m = _WO.match(c)
    if m:
        return Code(c[:2], c[:4], g, "warrant")
    m = _ENL.match(c)
    if m:
        return Code(m.group(1), m.group(1) + m.group(2), m.group(3), "enlisted")
    m = _OFF.match(c)
    if m:
        return Code(m.group(1), m.group(1) + m.group(2), g, "officer")
    return Code("", c or "UNKNOWN", g, "other")


def level_names(code: Code) -> Tuple[str, str, str]:
    if code.level == "ALL":
        return "ALL", "All Skill Levels" if code.kind == "enlisted" else "All Grades", ""
    if code.kind == "enlisted":
        g = SKILL_GRADES.get(code.level, "")
        return "SL" + code.level, "Skill Level {}".format(code.level) + (" ({})".format(g) if g else ""), g
    return code.level, code.level, code.level


@dataclass(frozen=True)
class BatchOptions:
    tables: Sequence[Path]
    out_dir: Path
    config: Optional[Path] = None
    overrides: Optional[Path] = None
    by: str = "level"
    compo: str = "ALL"
    mos: Sequence[str] = ()
    cmf: Sequence[str] = ()
    sheet: Optional[str] = None
    gazetteer_local: Optional[Path] = None
    data_dir: Optional[Path] = None
    template: Optional[Path] = None
    allow_unresolved: bool = False
    jobs: int = 1
    write_gpkg: bool = True
    prepared_by: Optional[str] = None
    title: str = DEFAULT_TITLE
    zip: bool = False
    list_only: bool = False
    quiet: bool = False
    stop_after: str = "render"


@dataclass
class Split:
    code: Code
    tag: str
    level_long: str
    grade: str
    rows: List[dict]
    sources: List[str] = field(default_factory=list)
    codes: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        if self.code.kind == "enlisted" and self.tag != "ALL":
            return "{}{}".format(self.code.mos, self.code.level)
        return "{}_{}".format(self.code.mos, self.tag)

    @property
    def folder(self) -> Path:
        return Path(self.code.cmf or "other") / self.code.mos / self.tag

    @property
    def slots(self) -> int:
        return sum(int(round(r["SLOTS"] or 0)) for r in self.rows)


def _log(o, msg):
    if not o.quiet:
        print(msg)
        sys.stdout.flush()


def expand_inputs(items: Sequence[Path]) -> List[Path]:
    out = []  # type: List[Path]
    for it in items:
        s = str(it)
        if any(ch in s for ch in "*?["):
            out += [Path(p) for p in sorted(glob.glob(s))]
        elif Path(s).is_dir():
            for ext in ("*.xlsx", "*.xlsm", "*.xls", "*.csv", "*.tsv"):
                out += sorted(Path(s).glob(ext))
        else:
            out.append(Path(s))
    seen, uniq = set(), []
    for p in out:
        if p.name.startswith("~$"):
            continue
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    return uniq


def read_rows(path: Path, sheet: Optional[str] = None) -> Tuple[List[dict], List[str]]:
    path = Path(path)
    notes = []  # type: List[str]
    book = read_any(path, sheet)
    out = []
    for nm, recs in book.items():
        src = "{}[{}]".format(path.name, nm) if len(book) > 1 else path.name
        if not recs:
            continue
        cols = list(recs[0].keys())
        mp, _ = column_map(cols)
        if "STATION" not in mp:
            notes.append("{}: no station/location column - skipped".format(src))
            continue
        if "SLOTS" not in mp:
            notes.append("{}: no slot/authorization column - skipped".format(src))
            continue
        code_c, grade_c, compo_c = find_col(cols, CODE_ALIASES), find_col(cols, GRADE_ALIASES), find_col(cols, COMPO_ALIASES)
        rows, _ = normalize(recs)
        rows, stated = split_total_row(rows)
        for r in rows:
            r["CODE"] = str(r.get(code_c, "")).strip() if code_c else path.stem
            r["GRADE"] = str(r.get(grade_c, "")).strip() if grade_c else ""
            r["COMPO"] = str(r.get(compo_c, "")).strip().upper() if compo_c else ""
            r["SOURCE"] = src
            r["HAS_COMPO"] = bool(compo_c)
        s = int(round(sum(r["SLOTS"] or 0 for r in rows)))
        if stated is not None and s != stated:
            notes.append("{}: rows sum to {} but the total row says {}".format(src, s, stated))
        if not code_c:
            notes.append("{}: no position-code column; the whole sheet is one map named '{}'".format(src, path.stem))
        out += rows
    return out, notes


def make_splits(rows: List[dict], o: BatchOptions) -> Tuple[List[Split], List[str]]:
    notes = []  # type: List[str]
    if not rows:
        return [], notes
    for r in rows:
        p = parse_code(r["CODE"], r["GRADE"])
        if p.kind == "other":
            p = Code("", re.sub(r"[^A-Z0-9]+", "_", str(r["CODE"]).upper()), "ALL", "other")
        r["_CODE"] = p
    comp = o.compo.upper()
    if comp != "ALL":
        if not all(r["HAS_COMPO"] for r in rows):
            missing = sorted({r["SOURCE"] for r in rows if not r["HAS_COMPO"]})
            raise ValueError("--compo {} needs a COMPO/COMPONENT column; missing in {}. Use --compo ALL or "
                             "export with the component column.".format(comp, missing))
        keep = [r for r in rows if r["COMPO"] in (COMPO_AC if comp == "AC" else COMPO_RC)]
        notes.append("Component filter {}: kept {} of {} rows".format(comp, len(keep), len(rows)))
        rows = keep
    if o.mos:
        want = {m.upper() for m in o.mos}
        rows = [r for r in rows if r["_CODE"].mos in want]
    if o.cmf:
        want = {c.zfill(2) for c in o.cmf}
        rows = [r for r in rows if r["_CODE"].cmf in want]
    splits = []  # type: List[Split]
    by = o.by.lower()
    if by in ("level", "both"):
        g = OrderedDict()  # type: OrderedDict
        for r in rows:
            g.setdefault(r["_CODE"], []).append(r)
        for code in sorted(g, key=lambda c: (c.cmf, c.mos, c.level, c.kind)):
            rs = g[code]
            tag, long_, grade = level_names(code)
            splits.append(Split(code, tag, long_, grade, rs, sorted({r["SOURCE"] for r in rs}),
                                sorted({r["CODE"] for r in rs})))
    if by in ("mos", "both"):
        g2 = OrderedDict()  # type: OrderedDict
        for r in rows:
            g2.setdefault((r["_CODE"].cmf, r["_CODE"].mos), []).append(r)
        for key in sorted(g2):
            rs = g2[key]
            if by == "both" and len({r["_CODE"].level for r in rs}) == 1:
                continue
            code = Code(key[0], key[1], "ALL", rs[0]["_CODE"].kind)
            splits.append(Split(code, "ALL", level_names(code)[1], "", rs, sorted({r["SOURCE"] for r in rs}),
                                sorted({r["CODE"] for r in rs})))
    return splits, notes


def write_split_table(sp: Split, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"LOCATION": r["STATION"], "STATE": r["STATE"], "CNTRY": r["COUNTRY"],
             "TOTAUTH": int(round(r["SLOTS"] or 0)), "POSCO": r["CODE"]} for r in sp.rows]
    total = sum(r["TOTAUTH"] for r in rows)
    rows.append({"LOCATION": "", "STATE": "", "CNTRY": "", "TOTAUTH": total, "POSCO": ""})
    write_csv(path, rows, ["LOCATION", "STATE", "CNTRY", "TOTAUTH", "POSCO"])
    return total


def override_files(sp: Split, folder: Optional[Path]) -> List[Path]:
    if not folder:
        return []
    names = ["CMF{}.json".format(sp.code.cmf), "{}.json".format(sp.code.cmf), "{}.json".format(sp.code.mos),
             "{}_{}.json".format(sp.code.mos, sp.tag), "{}.json".format(sp.name)]
    out, seen = [], set()
    for n in names:
        p = Path(folder) / n
        if n not in seen and p.exists():
            seen.add(n)
            out.append(p)
    return out


def build_config(sp: Split, o: BatchOptions) -> Tuple[dict, List[str]]:
    base = SheetConfig.load(o.config).to_dict()
    auto_notes = []
    if sp.code.kind == "enlisted" and sp.tag != "ALL":
        variants = ", ".join(sp.codes[:8]) + (" …" if len(sp.codes) > 8 else "")
        auto_notes.append("Skill level {} = all {}{}x position codes combined ({}).".format(
            sp.code.level, sp.code.mos, sp.code.level, variants))
    elif sp.tag == "ALL" and len(sp.codes) > 1:
        auto_notes.append("All {} position codes combined.".format(sp.code.mos))
    if o.compo.upper() != "ALL":
        auto_notes.append("Component filter: {} positions only.".format(o.compo.upper()))
    if len(sp.sources) > 1:
        auto_notes.append("Combined from {} source tables: {}.".format(len(sp.sources), ", ".join(sp.sources)))
    cfg = dict(base)
    cfg["mos"] = sp.name
    cfg["title"] = o.title.format(mos=sp.code.mos, level=sp.tag, level_long=sp.level_long, grade=sp.grade,
                                  cmf=sp.code.cmf, name=sp.name)
    if o.prepared_by is not None:
        cfg["prepared_by"] = o.prepared_by
    cfg["notes"] = list(base.get("notes", [])) + auto_notes
    used = []
    for p in override_files(sp, o.overrides):
        cfg = deep_merge(cfg, read_json(p))
        used.append(str(p))
    SheetConfig.from_dict(cfg).classes.validate()
    return cfg, used


def preflight_resolve(splits: List[Split], o: BatchOptions, out: Path) -> Tuple[List[dict], List[str]]:
    cfg = SheetConfig.load(o.config)
    allrows = []
    for sp in splits:
        for r in sp.rows:
            allrows.append({"STATION": r["STATION"], "STATE": r["STATE"], "COUNTRY": r["COUNTRY"],
                            "SLOTS": int(round(r["SLOTS"] or 0)), "LAT": r["LAT"], "LON": r["LON"],
                            "SRC_ROW": len(allrows) + 1, "SHEET": sp.name})
    gaz = Gazetteer.load(o.gazetteer_local, o.data_dir or default_data_dir())
    rep = resolve_rows(allrows, gaz, cfg.accept_fuzzy, cfg.fuzzy_auto_accept, cfg.use_places)
    for r, a in zip(rep, allrows):
        r["SHEET"], r["SLOTS"], r["STATE"], r["COUNTRY"] = a["SHEET"], a["SLOTS"], a["STATE"], a["COUNTRY"]
    write_csv(out / "resolution_report.csv", rep)
    need = OrderedDict()  # type: OrderedDict
    for r in rep:
        if not r["NEEDS_REVIEW"]:
            continue
        e = need.setdefault(r["STATION"], dict(STATION=r["STATION"], STATE=r["STATE"], COUNTRY=r["COUNTRY"],
                                               NAME=r["NAME"], RES_STATUS=r["RES_STATUS"],
                                               SUGGESTIONS=r["SUGGESTIONS"], GID=r["GID"], SLOTS=0, SHEETS=set()))
        e["SLOTS"] += r["SLOTS"]
        e["SHEETS"].add(r["SHEET"])
    agg = []
    for e in need.values():
        e["SHEETS"] = " ".join(sorted(e["SHEETS"]))
        agg.append(e)
    blocking = [e for e in agg if e["SLOTS"] > 0]
    if blocking:
        write_csv(out / "gazetteer_local_TODO.csv", [{
            "id": e["GID"] if e["RES_STATUS"] == "fuzzy" else "", "name": e["NAME"], "state": e["STATE"] or "",
            "country": e["COUNTRY"] or "", "lat": "", "lon": "", "aliases": e["STATION"], "coord_quality": "approx",
            "notes": "needed by {}; suggestions: {}".format(e["SHEETS"], e["SUGGESTIONS"])} for e in blocking], GAZ_COLS)
    return agg, [e["STATION"] for e in blocking]


def _run_one(args: tuple) -> dict:
    (name, table, cfg_path, out_dir, gaz, data_dir, template, allow_unres, gpkg, source, stop_after) = args
    import contextlib
    import io
    import traceback
    from .pipeline import RunOptions, run
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = run(RunOptions(table=Path(table), out_dir=Path(out_dir), config=Path(cfg_path),
                                  gazetteer_local=Path(gaz) if gaz else None,
                                  data_dir=Path(data_dir) if data_dir else None,
                                  template=Path(template) if template else None, allow_unresolved=allow_unres,
                                  write_gpkg=gpkg, source_name=source, stop_after=stop_after))
        except Exception as e:
            traceback.print_exc()
            print("[ERROR] {}: {}".format(type(e).__name__, e))
            code = -1
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "run.log").write_text(buf.getvalue(), encoding="utf-8")
    return {"name": name, "exit": code}


def summarize(sp: Split, d: Path, result: dict) -> dict:
    out = d / "out"
    row = {"cmf": sp.code.cmf, "mos": sp.code.mos, "level": sp.tag, "grade": sp.grade, "sheet": sp.name,
           "slots": sp.slots, "stations_mapped": "", "top_station": "", "top_pct": "", "qa": "error",
           "warnings": "", "exit": result.get("exit"), "pdf": "", "folder": str(d)}
    qa_p = out / "qa_report.json"
    if qa_p.exists():
        qa = json.loads(qa_p.read_text(encoding="utf-8"))
        row["qa"] = qa.get("status", "")
        row["warnings"] = "; ".join(c["check"] for c in qa.get("checks", []) if c["status"] in ("warn", "fail"))
        for f in qa.get("summary", {}).get("outputs", {}).get("sheet", []):
            if f.endswith(".pdf"):
                row["pdf"] = f
    t_p = out / "duty_station_table.csv"
    if t_p.exists():
        import csv
        with open(str(t_p), encoding="utf-8") as fh:
            t = [r for r in csv.DictReader(fh) if r["SLOTS"] and int(r["SLOTS"]) > 0 and r["LAT"]]
        t.sort(key=lambda r: -int(r["SLOTS"]))
        row["stations_mapped"] = len(t)
        if t:
            row["top_station"] = t[0]["NAME"]
            row["top_pct"] = round(float(t[0]["PCT_CALC"]), 1)
    elif result.get("exit") == 3:
        row["qa"] = "needs coordinates"
    elif result.get("exit") == 2:
        err = out / "ingest_errors.json"
        row["qa"] = "skipped"
        row["warnings"] = "; ".join(json.loads(err.read_text())) if err.exists() else "input error (see run.log)"
    return row


def write_index(rows: List[dict], out: Path, notes: List[str]) -> None:
    write_csv(out / "index.csv", rows)
    lines = ["# Slot map batch", ""]
    lines += ["- " + n for n in notes]
    if notes:
        lines.append("")
    lines += ["| Sheet | CMF | MOS | Level | Grade | Slots | Stations | Largest share | QA |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        top = "{} ({}%)".format(r["top_station"], r["top_pct"]) if r["top_station"] else ""
        qa = r["qa"] + (" — " + r["warnings"] if r["warnings"] else "")
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["sheet"], r["cmf"], r["mos"], r["level"], r["grade"], r["slots"], r["stations_mapped"], top, qa))
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_batch(o: BatchOptions) -> int:
    out = Path(o.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = expand_inputs(o.tables)
    if not files:
        print("[ERROR] no input tables found", file=sys.stderr)
        return EXIT_INPUT
    notes, rows = [], []  # type: List[str], List[dict]
    for f in files:
        if not f.exists():
            print("[ERROR] not found: {}".format(f), file=sys.stderr)
            return EXIT_INPUT
        r, n = read_rows(f, o.sheet)
        notes += n
        rows += r
        if r:
            _log(o, "[INFO] {}: {} rows, {} slots".format(f.name, len(r), int(sum(x["SLOTS"] or 0 for x in r))))
    if not rows:
        for n in notes:
            print("[ERROR] " + n, file=sys.stderr)
        return EXIT_INPUT
    try:
        splits, n = make_splits(rows, o)
    except ValueError as e:
        print("[ERROR] {}".format(e), file=sys.stderr)
        return EXIT_INPUT
    notes += n
    if not splits:
        print("[ERROR] nothing left to map after filters", file=sys.stderr)
        return EXIT_INPUT
    for n in notes:
        _log(o, "[NOTE] " + n)
    _log(o, "[INFO] {} sheet(s): {}".format(len(splits), ", ".join(sp.name for sp in splits)))
    if o.list_only:
        for sp in splits:
            print("{:12s} CMF {:3s} {:5s} {:28s} {:6d} slots  {:4d} stations  codes: {}".format(
                sp.name, sp.code.cmf, sp.code.mos, sp.level_long, sp.slots, len({r["STATION"] for r in sp.rows}),
                " ".join(sp.codes)))
        return EXIT_OK
    need, blocking = preflight_resolve(splits, o, out)
    for e in need:
        tag = "BLOCKS" if e["STATION"] in blocking else "0 slots, not mapped"
        _log(o, "[REVIEW] {!r} ({}; {}; sheets {}); suggestions: {}".format(
            e["STATION"], e["RES_STATUS"], tag, e["SHEETS"], e["SUGGESTIONS"]))
    if blocking and not o.allow_unresolved:
        print("[STOP] {} station(s) with slots need coordinates. Fill {}, save it as gazetteer_local.csv (or pass "
              "--gazetteer), and re-run; or add --allow-unresolved to build now and list them on each sheet as "
              "dropped.".format(len(blocking), out / "gazetteer_local_TODO.csv"), file=sys.stderr)
        return EXIT_UNRESOLVED
    jobs, meta = [], {}
    for sp in splits:
        d = out / sp.folder
        d.mkdir(parents=True, exist_ok=True)
        write_split_table(sp, d / "table.csv")
        cfg, used = build_config(sp, o)
        if used:
            _log(o, "[INFO] {}: overrides {}".format(sp.name, ", ".join(Path(u).name for u in used)))
        write_json(d / "sheet.json", cfg)
        jobs.append((sp.name, str(d / "table.csv"), str(d / "sheet.json"), str(d / "out"),
                     str(o.gazetteer_local) if o.gazetteer_local else None,
                     str(o.data_dir) if o.data_dir else None, str(o.template) if o.template else None,
                     o.allow_unresolved, o.write_gpkg, ", ".join(sp.sources), o.stop_after))
        meta[sp.name] = (sp, d)
    results = {}  # type: Dict[str, dict]
    if max(1, int(o.jobs or 1)) == 1 or len(jobs) == 1:
        for j in jobs:
            _log(o, "[RUN] {} …".format(j[0]))
            results[j[0]] = _run_one(j)
            _log(o, "[RUN] {} exit {}".format(j[0], results[j[0]]["exit"]))
    else:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=int(o.jobs), mp_context=mp.get_context("spawn")) as ex:
            futs = {ex.submit(_run_one, j): j[0] for j in jobs}
            for fu in as_completed(futs):
                r = fu.result()
                results[r["name"]] = r
                _log(o, "[RUN] {} exit {}".format(r["name"], r["exit"]))
    sheets = out / "sheets"
    sheets.mkdir(exist_ok=True)
    idx = []
    for sp in splits:
        _, d = meta[sp.name]
        row = summarize(sp, d, results.get(sp.name, {}))
        if row["pdf"] and Path(row["pdf"]).exists():
            dst = sheets / "{}_sheet.pdf".format(sp.name)
            shutil.copy(row["pdf"], str(dst))
            row["pdf"] = str(dst)
        idx.append(row)
    write_index(idx, out, notes)
    if o.zip:
        base = shutil.make_archive(str(out.parent / (out.name + "_slotmaps")), "zip", root_dir=str(out))
        _log(o, "[INFO] zip: {}".format(base))
    for r in idx:
        _log(o, "[DONE] {:12s} {:6s} {:5d} slots  {} stations{}".format(
            r["sheet"], r["qa"], r["slots"], r["stations_mapped"], "  ({})".format(r["warnings"]) if r["warnings"] else ""))
    _log(o, "[DONE] index: {}; sheets: {}".format(out / "index.md", sheets))
    return EXIT_QA_FAIL if any(r["qa"] in ("fail", "error") for r in idx) else EXIT_OK
