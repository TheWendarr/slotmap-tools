"""Stage 9 - batch: one or more position exports in, every map sheet out.

`slotmap batch data/*.xlsx -o out/batch` reads every table, reads each row's position code (POSCO),
splits the rows by CMF -> MOS -> skill level (enlisted) or grade (warrant/officer, when a grade
column exists), resolves every station name once for the whole batch, writes a table + sheet.yaml
for each split, builds every sheet, and writes an index of results.

    out/batch/
      index.csv, index.md          one row per sheet: totals, stations, top station, QA status
      sheets/35G1_sheet.pdf ...    every finished sheet in one folder
      35/35G/SL1/table.csv         the split table that was mapped (with a total row)
      35/35G/SL1/sheet.yaml        the generated config (copy into an overrides folder to tune)
      35/35G/SL1/out/...           everything `slotmap run` writes (GIS layers, QA, crops)
      gazetteer_local_TODO.csv     only if stations still need coordinates
"""
from __future__ import annotations

import copy
import glob
import json
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from .config import SheetConfig
from .gazetteer import GAZ_COLS, Gazetteer, resolve_table
from .ingest import _hkey, clean_values, normalize_columns, read_table, split_total_row

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

EXIT_OK, EXIT_QA_FAIL, EXIT_INPUT, EXIT_UNRESOLVED = 0, 1, 2, 3

CODE_ALIASES = ["POSCO", "POS CO", "POSITION CODE", "POSN CODE", "MOS", "PMOS", "DMOS", "MOSC", "MOS CODE",
                "AOC", "AOC MOS", "MOS AOC", "AOC/MOS", "SPECIALTY"]
GRADE_ALIASES = ["GRADE", "GRD", "PAY GRADE", "PAYGRADE", "RANK", "AUTH GRADE", "POS GRADE"]
COMPO_ALIASES = ["COMPO", "COMPONENT", "CMP", "COMP"]
COMPO_AC = {"1", "AC", "ACTIVE", "RA", "COMPO 1"}
COMPO_RC = {"2", "3", "6", "NG", "ARNG", "AR", "USAR", "RC", "COMPO 2", "COMPO 3"}

# enlisted skill level -> grades it is written against (AR 611-1)
SKILL_GRADES = {"1": "PVT–SPC", "2": "SGT", "3": "SSG", "4": "SFC", "5": "MSG–SGM"}

DEFAULT_TITLE = "{mos} {level_long}: Authorized Positions by Duty Station"


# --------------------------------------------------------------------------- position codes
@dataclass(frozen=True)
class Code:
    cmf: str            # "35"
    mos: str            # "35G", "255A", "35D"
    level: str          # "1".."5" (skill level), a grade ("CW2"), or "ALL"
    kind: str           # enlisted | warrant | officer | other


_ENL = re.compile(r"^(\d\d)([A-Z])([1-5])")
_WO = re.compile(r"^(\d{3})([A-Z])")
_OFF = re.compile(r"^(\d\d)([A-Z])")


def parse_code(code: str, grade: str = "") -> Code:
    """9.1 '35G3O' -> CMF 35, MOS 35G, SL 3.  '255A0' -> 255A by grade.  '35D00' -> 35D by grade."""
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
    """(folder / tag, long label, short grade label) for a split."""
    if code.level == "ALL":
        return "ALL", "All Skill Levels" if code.kind == "enlisted" else "All Grades", ""
    if code.kind == "enlisted":
        g = SKILL_GRADES.get(code.level, "")
        return f"SL{code.level}", f"Skill Level {code.level}" + (f" ({g})" if g else ""), g
    return code.level, code.level, code.level


# --------------------------------------------------------------------------- options
@dataclass(frozen=True)
class BatchOptions:
    tables: Sequence[Path]
    out_dir: Path
    config: Optional[Path] = None             # base sheet.yaml applied to every sheet
    overrides: Optional[Path] = None          # folder of <MOS>.yaml / <MOS><level>.yaml / <MOS>_<tag>.yaml
    by: str = "level"                         # level | mos | both
    compo: str = "ALL"                        # ALL | AC | RC (needs a COMPO column)
    mos: Sequence[str] = ()                   # keep only these MOS (e.g. 35G 35F)
    cmf: Sequence[str] = ()                   # keep only these CMFs (e.g. 35 25)
    sheet: Optional[str] = None               # Excel sheet name; default: every sheet with a code column
    gazetteer_local: Optional[Path] = None
    basemap_dir: Optional[Path] = None
    allow_unresolved: bool = False
    jobs: int = 1
    write_gdb: bool = True
    exports: Sequence[str] = ()
    prepared_by: Optional[str] = None
    title: str = DEFAULT_TITLE
    zip: bool = False
    list_only: bool = False
    quiet: bool = False


@dataclass
class Split:
    code: Code
    tag: str
    level_long: str
    grade: str
    rows: pd.DataFrame
    sources: List[str] = field(default_factory=list)
    codes: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:                    # 35G1, 35G_ALL, 255A_CW2
        return f"{self.code.mos}{self.code.level}" if self.code.kind == "enlisted" and self.tag != "ALL" \
            else f"{self.code.mos}_{self.tag}"

    @property
    def folder(self) -> Path:
        return Path(self.code.cmf or "other") / self.code.mos / self.tag


def _log(o: BatchOptions, msg: str) -> None:
    if not o.quiet:
        print(msg, flush=True)


# --------------------------------------------------------------------------- reading
def expand_inputs(items: Sequence[Path]) -> List[Path]:
    """Files, folders (every .xlsx/.xls/.csv/.tsv inside) and glob patterns."""
    out: List[Path] = []
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
        if p.name.startswith("~$"):          # Excel lock files
            continue
        if p.resolve() not in seen:
            seen.add(p.resolve())
            uniq.append(p)
    return uniq


def _find_col(cols: Sequence[str], aliases: Sequence[str]) -> Optional[str]:
    keys = {_hkey(a) for a in aliases}
    for c in cols:
        if _hkey(c) in keys:
            return c
    return None


def read_rows(path: Path, sheet: Optional[str] = None) -> Tuple[pd.DataFrame, List[str]]:
    """9.2 Read every relevant sheet of one file into canonical rows + CODE/GRADE/COMPO/SOURCE."""
    path = Path(path)
    notes: List[str] = []
    frames: List[Tuple[str, pd.DataFrame]] = []
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls") and sheet is None:
        book = pd.read_excel(path, sheet_name=None, dtype=str)
        for nm, df in book.items():
            frames.append((f"{path.name}[{nm}]" if len(book) > 1 else path.name, df))
    else:
        frames.append((path.name, read_table(path, sheet)))
    out = []
    for src, raw in frames:
        raw = raw.dropna(how="all")
        if raw.empty:
            continue
        code_c = _find_col(raw.columns, CODE_ALIASES)
        grade_c = _find_col(raw.columns, GRADE_ALIASES)
        compo_c = _find_col(raw.columns, COMPO_ALIASES)
        df, _ = normalize_columns(raw)
        if (df.STATION.fillna("").astype(str).str.strip() == "").all():
            notes.append(f"{src}: no station/location column - skipped")
            continue
        df = clean_values(df)
        df, stated = split_total_row(df)
        if df.SLOTS.isna().all():
            notes.append(f"{src}: no slot/authorization column - skipped")
            continue
        df["CODE"] = df[code_c].fillna("").astype(str).str.strip() if code_c else ""
        df["GRADE"] = df[grade_c].fillna("").astype(str).str.strip() if grade_c else ""
        df["COMPO"] = df[compo_c].fillna("").astype(str).str.strip().str.upper() if compo_c else ""
        df["SOURCE"] = src
        df["HAS_COMPO"] = bool(compo_c)
        if stated is not None and int(round(df.SLOTS.fillna(0).sum())) != stated:
            notes.append(f"{src}: rows sum to {int(df.SLOTS.fillna(0).sum())} but the total row says {stated}")
        if not code_c:
            df["CODE"] = path.stem
            notes.append(f"{src}: no position-code column; the whole sheet is one map named '{path.stem}'")
        out.append(df)
    if not out:
        return pd.DataFrame(), notes
    return pd.concat(out, ignore_index=True), notes


# --------------------------------------------------------------------------- splitting
def make_splits(rows: pd.DataFrame, o: BatchOptions) -> Tuple[List[Split], List[str]]:
    """9.3 Group rows into one Split per (CMF, MOS, level) - plus per-MOS 'ALL' sheets for by=mos/both."""
    notes: List[str] = []
    if rows.empty:
        return [], notes
    rows = rows.copy()
    parsed = [parse_code(c, g) for c, g in zip(rows.CODE, rows.GRADE)]
    # a sheet with no code column: CODE is the file stem; keep it whole
    rows["_CMF"] = [p.cmf for p in parsed]
    rows["_MOS"] = [p.mos for p in parsed]
    rows["_LVL"] = [p.level for p in parsed]
    rows["_KIND"] = [p.kind for p in parsed]
    other = rows._KIND == "other"
    if other.any():
        rows.loc[other, "_MOS"] = rows.loc[other, "CODE"].str.upper().str.replace(r"[^A-Z0-9]+", "_", regex=True)
        rows.loc[other, "_LVL"] = "ALL"

    comp = o.compo.upper()
    if comp != "ALL":
        if not rows.HAS_COMPO.all():
            missing = sorted(rows[~rows.HAS_COMPO].SOURCE.unique())
            raise ValueError(f"--compo {comp} needs a COMPO/COMPONENT column; missing in {missing}. "
                             "Use --compo ALL or export with the component column.")
        keep = rows.COMPO.isin(COMPO_AC if comp == "AC" else COMPO_RC)
        notes.append(f"Component filter {comp}: kept {int(keep.sum())} of {len(rows)} rows")
        rows = rows[keep]
    if o.mos:
        want = {m.upper() for m in o.mos}
        rows = rows[rows._MOS.isin(want)]
    if o.cmf:
        want = {c.zfill(2) for c in o.cmf}
        rows = rows[rows._CMF.isin(want)]

    splits: List[Split] = []
    by = o.by.lower()
    if by in ("level", "both"):
        for (cmf, mos, lvl, kind), g in rows.groupby(["_CMF", "_MOS", "_LVL", "_KIND"], sort=True):
            code = Code(cmf, mos, lvl, kind)
            tag, long, grade = level_names(code)
            splits.append(Split(code, tag, long, grade, g, sorted(g.SOURCE.unique()), sorted(g.CODE.unique())))
    if by in ("mos", "both"):
        for (cmf, mos), g in rows.groupby(["_CMF", "_MOS"], sort=True):
            if by == "both" and g._LVL.nunique() == 1:
                continue                          # the level sheet already is the whole MOS
            code = Code(cmf, mos, "ALL", g._KIND.iloc[0])
            splits.append(Split(code, "ALL", level_names(code)[1], "", g, sorted(g.SOURCE.unique()),
                                sorted(g.CODE.unique())))
    return splits, notes


def write_split_table(sp: Split, path: Path) -> int:
    """Canonical table with a total row, so the pipeline reconciles totals."""
    t = sp.rows[["STATION", "STATE", "COUNTRY", "SLOTS", "CODE"]].copy()
    t.columns = ["LOCATION", "STATE", "CNTRY", "TOTAUTH", "POSCO"]
    t["TOTAUTH"] = t.TOTAUTH.fillna(0).round().astype(int)
    total = int(t.TOTAUTH.sum())
    t = pd.concat([t, pd.DataFrame([{"LOCATION": "", "STATE": "", "CNTRY": "", "TOTAUTH": total, "POSCO": ""}])])
    path.parent.mkdir(parents=True, exist_ok=True)
    t.to_csv(path, index=False)
    return total


# --------------------------------------------------------------------------- config
def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif k == "notes" and isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + [n for n in v if n not in out[k]]
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_yaml(p: Path) -> dict:
    if not p.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required: pip install pyyaml")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def override_files(sp: Split, folder: Optional[Path]) -> List[Path]:
    """Most general first: CMF, MOS, then MOS+level (both '35G3.yaml' and '35G_SL3.yaml' work)."""
    if not folder:
        return []
    names = [f"CMF{sp.code.cmf}.yaml", f"{sp.code.cmf}.yaml", f"{sp.code.mos}.yaml",
             f"{sp.code.mos}_{sp.tag}.yaml", f"{sp.name}.yaml"]
    seen, out = set(), []
    for n in names:
        p = Path(folder) / n
        if n not in seen and p.exists():
            seen.add(n)
            out.append(p)
    return out


def build_config(sp: Split, o: BatchOptions, total: int) -> Tuple[dict, List[str]]:
    base = SheetConfig.load(o.config).to_dict()
    auto = {
        "mos": sp.name,
        "title": o.title.format(mos=sp.code.mos, level=sp.tag, level_long=sp.level_long, grade=sp.grade,
                                cmf=sp.code.cmf, name=sp.name),
        "notes": [],
    }
    if sp.code.kind == "enlisted" and sp.tag != "ALL":
        variants = ", ".join(sp.codes[:8]) + (" …" if len(sp.codes) > 8 else "")
        auto["notes"].append(f"Skill level {sp.code.level} = all {sp.code.mos}{sp.code.level}x position codes "
                             f"combined ({variants}).")
    elif sp.tag == "ALL" and len(sp.codes) > 1:
        auto["notes"].append(f"All {sp.code.mos} position codes combined.")
    if o.compo.upper() != "ALL":
        auto["notes"].append(f"Component filter: {o.compo.upper()} positions only.")
    if len(sp.sources) > 1:
        auto["notes"].append(f"Combined from {len(sp.sources)} source tables: {', '.join(sp.sources)}.")
    if o.prepared_by is not None:
        auto["prepared_by"] = o.prepared_by
    cfg = _deep_merge(base, {k: v for k, v in auto.items() if k != "notes"})
    cfg["notes"] = list(base.get("notes", [])) + auto["notes"]
    used = []
    for p in override_files(sp, o.overrides):
        cfg = _deep_merge(cfg, _read_yaml(p))
        used.append(str(p))
    SheetConfig.from_dict(cfg).classes.validate()
    return cfg, used


# --------------------------------------------------------------------------- resolution (once)
def preflight_resolve(splits: List[Split], o: BatchOptions, out: Path) -> Tuple[pd.DataFrame, List[str]]:
    """9.4 Resolve every station of every split in one pass. Returns (rows needing coordinates, blocking names).

    A station only blocks the batch if it has slots in at least one sheet; 0-slot stations are listed in the
    footer and never mapped.
    """
    cfg = SheetConfig.load(o.config)
    allrows = []
    for sp in splits:
        r = sp.rows[["STATION", "STATE", "COUNTRY", "SLOTS", "PCT_SRC", "LAT", "LON", "SRC_ROW"]].copy()
        r["SHEET"] = sp.name
        allrows.append(r)
    if not allrows:
        return pd.DataFrame(), []
    df = pd.concat(allrows, ignore_index=True)
    df["SRC_ROW"] = range(1, len(df) + 1)
    gaz = Gazetteer.load(o.gazetteer_local)
    _, rep = resolve_table(df, gaz, cfg.accept_fuzzy, cfg.fuzzy_auto_accept, cfg.use_places)
    rep = rep.merge(df[["SRC_ROW", "SHEET", "SLOTS", "STATE", "COUNTRY"]], on="SRC_ROW")
    rep.to_csv(out / "resolution_report.csv", index=False)
    need = rep[rep.NEEDS_REVIEW]
    if need.empty:
        return need, []
    agg = need.groupby("STATION").agg(STATE=("STATE", "first"), COUNTRY=("COUNTRY", "first"),
                                      NAME=("NAME", "first"), RES_STATUS=("RES_STATUS", "first"),
                                      SUGGESTIONS=("SUGGESTIONS", "first"), GID=("GID", "first"),
                                      SLOTS=("SLOTS", "sum"),
                                      SHEETS=("SHEET", lambda s: " ".join(sorted(set(s))))).reset_index()
    blocking = agg[agg.SLOTS > 0]
    if len(blocking):
        todo = pd.DataFrame({
            "id": [g if s == "fuzzy" else "" for g, s in zip(blocking.GID, blocking.RES_STATUS)],
            "name": blocking.NAME, "state": blocking.STATE.fillna(""), "country": blocking.COUNTRY.fillna(""),
            "lat": "", "lon": "", "aliases": blocking.STATION, "coord_quality": "approx",
            "notes": "needed by " + blocking.SHEETS + "; suggestions: " + blocking.SUGGESTIONS.fillna("")})[GAZ_COLS]
        todo.to_csv(out / "gazetteer_local_TODO.csv", index=False)
    return agg, blocking.STATION.tolist()


# --------------------------------------------------------------------------- one sheet
def _run_one(args: Tuple) -> dict:
    """Runs in a worker process."""
    (name, table, cfg_path, out_dir, gaz, bm, allow_unres, gdb, exports, source) = args
    import contextlib
    import io

    from .pipeline import RunOptions, run
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = run(RunOptions(table=Path(table), out_dir=Path(out_dir), config=Path(cfg_path),
                                  gazetteer_local=Path(gaz) if gaz else None,
                                  basemap_dir=Path(bm) if bm else None, allow_unresolved=allow_unres,
                                  write_gdb=gdb, exports=list(exports), quiet=False, source_name=source))
        except Exception as e:  # keep the batch going; report it
            import traceback
            traceback.print_exc()
            code = -1
            print(f"[ERROR] {type(e).__name__}: {e}")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "run.log").write_text(buf.getvalue(), encoding="utf-8")
    return {"name": name, "exit": code}


def summarize(sp: Split, sheet_dir: Path, result: dict) -> dict:
    out = sheet_dir / "out"
    row = {"cmf": sp.code.cmf, "mos": sp.code.mos, "level": sp.tag, "grade": sp.grade, "sheet": sp.name,
           "slots": int(sp.rows.SLOTS.fillna(0).sum()), "stations_mapped": "", "top_station": "", "top_pct": "",
           "qa": "error", "warnings": "", "exit": result.get("exit"), "pdf": "", "folder": str(sheet_dir)}
    qa_p = out / "qa_report.json"
    if qa_p.exists():
        qa = json.loads(qa_p.read_text(encoding="utf-8"))
        row["qa"] = qa.get("status", "")
        row["warnings"] = "; ".join(f"{c['check']}" for c in qa.get("checks", []) if c["status"] in ("warn", "fail"))
        for f in qa.get("summary", {}).get("outputs", {}).get("sheet", []):
            if f.endswith(".pdf"):
                row["pdf"] = f
    t_p = out / "duty_station_table.csv"
    if t_p.exists():
        t = pd.read_csv(t_p)
        m = t[t.SLOTS > 0].sort_values("SLOTS", ascending=False)
        row["stations_mapped"] = int(len(m[m.LAT.notna()]))
        if len(m):
            row["top_station"] = m.iloc[0].NAME
            row["top_pct"] = round(float(m.iloc[0].PCT_CALC), 1)
    elif result.get("exit") == 3:
        row["qa"] = "needs coordinates"
    elif result.get("exit") == 2:
        err = out / "ingest_errors.json"
        row["qa"] = "skipped"
        row["warnings"] = "; ".join(json.loads(err.read_text())) if err.exists() else "input error"
    return row


def write_index(rows: List[dict], out: Path, notes: List[str]) -> None:
    idx = pd.DataFrame(rows)
    idx.to_csv(out / "index.csv", index=False)
    lines = ["# Slot map batch", ""]
    for n in notes:
        lines.append(f"- {n}")
    if notes:
        lines.append("")
    lines += ["| Sheet | CMF | MOS | Level | Grade | Slots | Stations | Largest share | QA |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        top = f"{r['top_station']} ({r['top_pct']}%)" if r["top_station"] else ""
        qa = r["qa"] + (f" — {r['warnings']}" if r["warnings"] else "")
        lines.append(f"| {r['sheet']} | {r['cmf']} | {r['mos']} | {r['level']} | {r['grade']} | {r['slots']} | "
                     f"{r['stations_mapped']} | {top} | {qa} |")
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- main
def run_batch(o: BatchOptions) -> int:
    out = Path(o.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = expand_inputs(o.tables)
    if not files:
        print("[ERROR] no input tables found", file=sys.stderr)
        return EXIT_INPUT
    notes: List[str] = []
    parts = []
    for f in files:
        if not f.exists():
            print(f"[ERROR] not found: {f}", file=sys.stderr)
            return EXIT_INPUT
        rows, n = read_rows(f, o.sheet)
        notes += n
        if not rows.empty:
            parts.append(rows)
            _log(o, f"[INFO] {f.name}: {len(rows)} rows, {int(rows.SLOTS.fillna(0).sum())} slots")
    if not parts:
        for n in notes:
            print(f"[ERROR] {n}", file=sys.stderr)
        return EXIT_INPUT
    rows = pd.concat(parts, ignore_index=True)
    try:
        splits, n = make_splits(rows, o)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return EXIT_INPUT
    notes += n
    if not splits:
        print("[ERROR] nothing left to map after filters", file=sys.stderr)
        return EXIT_INPUT
    for n in notes:
        _log(o, f"[NOTE] {n}")
    _log(o, f"[INFO] {len(splits)} sheet(s): " + ", ".join(sp.name for sp in splits))
    if o.list_only:
        for sp in splits:
            print(f"{sp.name:12s} CMF {sp.code.cmf:3s} {sp.code.mos:5s} {sp.level_long:28s} "
                  f"{int(sp.rows.SLOTS.fillna(0).sum()):6d} slots  {sp.rows.STATION.nunique():4d} stations  "
                  f"codes: {' '.join(sp.codes)}")
        return EXIT_OK

    # resolve every station once
    need, blocking = preflight_resolve(splits, o, out)
    if len(need):
        for r in need.itertuples():
            tag = "BLOCKS" if r.STATION in blocking else "0 slots, not mapped"
            _log(o, f"[REVIEW] {r.STATION!r} ({r.RES_STATUS}; {tag}; sheets {r.SHEETS}); suggestions: {r.SUGGESTIONS}")
    if blocking and not o.allow_unresolved:
        print(f"[STOP] {len(blocking)} station(s) with slots need coordinates. Fill {out / 'gazetteer_local_TODO.csv'}"
              f", save it as gazetteer_local.csv (or pass --gazetteer), and re-run; or add --allow-unresolved to "
              "build now and list them on each sheet as dropped.", file=sys.stderr)
        return EXIT_UNRESOLVED

    # write tables + configs
    jobs = []
    meta: Dict[str, Tuple[Split, Path]] = {}
    for sp in splits:
        d = out / sp.folder
        d.mkdir(parents=True, exist_ok=True)
        total = write_split_table(sp, d / "table.csv")
        cfg, used = build_config(sp, o, total)
        if used:
            _log(o, f"[INFO] {sp.name}: overrides {', '.join(Path(u).name for u in used)}")
        (d / "sheet.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
        jobs.append((sp.name, str(d / "table.csv"), str(d / "sheet.yaml"), str(d / "out"),
                     str(o.gazetteer_local) if o.gazetteer_local else None,
                     str(o.basemap_dir) if o.basemap_dir else None, o.allow_unresolved, o.write_gdb,
                     tuple(o.exports), ", ".join(sp.sources)))
        meta[sp.name] = (sp, d)

    results: Dict[str, dict] = {}
    n_jobs = max(1, int(o.jobs or 1))
    if n_jobs == 1 or len(jobs) == 1:
        for j in jobs:
            _log(o, f"[RUN] {j[0]} …")
            results[j[0]] = _run_one(j)
            _log(o, f"[RUN] {j[0]} exit {results[j[0]]['exit']}")
    else:
        import multiprocessing as mp
        with ProcessPoolExecutor(max_workers=n_jobs, mp_context=mp.get_context("spawn")) as ex:
            futs = {ex.submit(_run_one, j): j[0] for j in jobs}
            for fu in as_completed(futs):
                r = fu.result()
                results[r["name"]] = r
                _log(o, f"[RUN] {r['name']} exit {r['exit']}")

    # collect
    sheets = out / "sheets"
    sheets.mkdir(exist_ok=True)
    rows_idx = []
    for sp in splits:
        _, d = meta[sp.name]
        row = summarize(sp, d, results.get(sp.name, {}))
        if row["pdf"] and Path(row["pdf"]).exists():
            dst = sheets / f"{sp.name}_sheet.pdf"
            shutil.copy(row["pdf"], dst)
            row["pdf"] = str(dst)
        rows_idx.append(row)
    write_index(rows_idx, out, notes)
    if o.zip:
        base = shutil.make_archive(str(out.with_name(out.name + "_slotmaps")), "zip", root_dir=out)
        _log(o, f"[INFO] zip: {base}")
    bad = [r for r in rows_idx if r["qa"] in ("fail", "error")]
    for r in rows_idx:
        _log(o, f"[DONE] {r['sheet']:12s} {r['qa']:6s} {r['slots']:5d} slots  {r['stations_mapped']} stations"
                + (f"  ({r['warnings']})" if r["warnings"] else ""))
    _log(o, f"[DONE] index: {out / 'index.md'}; sheets: {sheets}")
    return EXIT_QA_FAIL if bad else EXIT_OK
