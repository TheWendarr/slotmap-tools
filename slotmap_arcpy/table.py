"""Stage 1 - read a slot table (CSV, TSV, XLSX; XLS through ArcPy) and normalize it to canonical rows."""
from __future__ import annotations

import csv
import io
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CANON = ["STATION", "STATE", "COUNTRY", "SLOTS", "PCT_SRC", "LAT", "LON"]
ALIASES = {
    "STATION": ["LOCATION", "DUTY STATION", "DUTYSTATION", "DUTY_STATION", "INSTALLATION", "STATION", "POST",
                "BASE", "LOC", "LOCATION NAME", "DUTY LOCATION", "ASSIGNMENT LOCATION", "UNIT LOCATION"],
    "STATE": ["STATENAME", "STATE", "STATE NAME", "ST", "STATE_NAME", "STATE/PROVINCE"],
    "COUNTRY": ["CNTRY", "COUNTRY", "CTRY", "NATION", "COUNTRY NAME"],
    "SLOTS": ["TOTAUTH", "TOT AUTH", "TOTAL AUTH", "AUTH", "AUTHS", "AUTHORIZED", "AUTHORIZATIONS", "SLOTS",
              "POSITIONS", "BILLETS", "COUNT", "REQ", "REQUIRED", "TOTAL"],
    "PCT_SRC": ["TOTAL PERCENT", "PERCENT", "PCT", "TOTAL PCT", "TOTAL_PCT", "%", "PERCENT OF TOTAL", "SHARE"],
    "LAT": ["LAT", "LATITUDE", "Y"],
    "LON": ["LON", "LONG", "LONGITUDE", "LNG", "X"],
}
BLANK_STATE = {"", "NO TITLE CODE FOUND", "N/A", "NA", "NONE", "-", "NULL", "NAN"}

Row = Dict[str, object]


@dataclass
class IngestResult:
    rows: List[Row]
    stated_total: Optional[int]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def hkey(s: str) -> str:
    return re.sub(r"[^A-Z0-9%]+", " ", str(s).upper()).strip()


def read_any(path: Path, sheet: Optional[str] = None) -> "OrderedDict[str, List[OrderedDict]]":
    """{sheet/file name: records}. Every sheet of a workbook when sheet is None."""
    from .xlsx import read_xlsx, rows_to_records
    path = Path(path)
    suf = path.suffix.lower()
    out = OrderedDict()  # type: OrderedDict
    if suf in (".xlsx", ".xlsm"):
        book = read_xlsx(path, sheet)
        for nm, rows in book.items():
            out[nm] = rows_to_records(rows)
        return out
    if suf == ".xls":
        out[sheet or path.stem] = _read_xls_arcpy(path, sheet)
        return out
    if suf in (".csv", ".tsv", ".tab", ".txt"):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if suf in (".tsv", ".tab"):
            dialect = csv.excel_tab
        else:
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
        rd = csv.reader(io.StringIO(text), dialect)
        out[path.name] = _records(list(rd))
        return out
    raise ValueError("Unsupported table type {!r}. Use .xlsx, .csv or .tsv (or transcribe into "
                     "templates/slots_template.csv).".format(suf))


def _records(rows: List[List[str]]) -> List["OrderedDict[str, str]"]:
    from .xlsx import rows_to_records
    return rows_to_records(rows)


def _read_xls_arcpy(path: Path, sheet: Optional[str]) -> List["OrderedDict[str, str]"]:
    """Legacy .xls: let ArcPy convert it to a scratch table, then read it with a cursor."""
    import arcpy  # noqa
    out_tbl = "memory\\xls_{}".format(abs(hash(str(path))) % 10 ** 8)
    if sheet:
        arcpy.conversion.ExcelToTable(str(path), out_tbl, sheet)
    else:
        arcpy.conversion.ExcelToTable(str(path), out_tbl)
    names = [f.name for f in arcpy.ListFields(out_tbl) if f.type not in ("OID", "Geometry")]
    recs = []
    with arcpy.da.SearchCursor(out_tbl, names) as cur:
        for r in cur:
            recs.append(OrderedDict((n, "" if v is None else str(v)) for n, v in zip(names, r)))
    arcpy.management.Delete(out_tbl)
    return recs


def find_col(cols, aliases) -> Optional[str]:
    keys = {hkey(a) for a in aliases}
    for c in cols:
        if hkey(c) in keys:
            return c
    return None


def column_map(cols: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """canonical name -> source column (first match wins), and the unmatched columns."""
    lookup = {}
    for canon, al in ALIASES.items():
        for a in al:
            lookup.setdefault(hkey(a), canon)
    mapping, unmatched = {}, []
    for c in cols:
        canon = lookup.get(hkey(c))
        if canon and canon not in mapping:
            mapping[canon] = c
        else:
            unmatched.append(str(c))
    return mapping, unmatched


def num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s == "" or s.upper() in ("NAN", "NONE", "NULL"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize(records: List[Dict[str, str]]) -> Tuple[List[Row], List[str]]:
    """Canonical keys + typed values. Original columns are kept alongside."""
    if not records:
        return [], []
    cols = list(records[0].keys())
    mp, unmatched = column_map(cols)
    out = []
    for i, rec in enumerate(records, 1):
        r = dict(rec)  # type: Row
        for c in ("STATION", "STATE", "COUNTRY"):
            r[c] = str(rec.get(mp[c], "") if c in mp else "").strip()
        if str(r["STATE"]).upper() in BLANK_STATE:
            r["STATE"] = ""
        for c in ("SLOTS", "PCT_SRC", "LAT", "LON"):
            r[c] = num(rec.get(mp[c])) if c in mp else None
        r["SRC_ROW"] = i
        out.append(r)
    return out, unmatched


def split_total_row(rows: List[Row]) -> Tuple[List[Row], Optional[int]]:
    stated = None
    keep = []
    for r in rows:
        name = str(r["STATION"]).strip()
        is_total = name == "" or re.match(r"^(GRAND\s+)?TOTALS?$", name.upper()) is not None
        if is_total:
            if r["SLOTS"] is not None:
                stated = int(round(r["SLOTS"]))
            continue
        keep.append(r)
    return keep, stated


def validate(rows: List[Row], stated: Optional[int]) -> Tuple[List[str], List[str]]:
    warn, err = [], []
    if not rows:
        return warn, ["No station rows found."]
    miss = [r["STATION"] for r in rows if r["SLOTS"] is None]
    if miss:
        err.append("Missing slot counts for: {}".format(miss))
    if any((r["SLOTS"] or 0) < 0 for r in rows):
        err.append("Negative slot counts present.")
    if any(r["SLOTS"] is not None and float(r["SLOTS"]) % 1 for r in rows):
        warn.append("Non-integer slot counts were rounded.")
    total = int(round(sum(r["SLOTS"] or 0 for r in rows)))
    if stated is not None and stated != total:
        err.append("Rows sum to {} but the table's total row says {}. Re-check the transcription.".format(total, stated))
    pcts = [r["PCT_SRC"] for r in rows if r["PCT_SRC"] is not None]
    if pcts and abs(sum(pcts) - 100) > 2.5:
        warn.append("Source percents sum to {:.1f}, not ~100.".format(sum(pcts)))
    for r in rows:
        if (r["LAT"] is None) != (r["LON"] is None):
            warn.append("Some rows have only one of LAT/LON; those coordinates are ignored.")
            break
    bad = [r["SRC_ROW"] for r in rows if r["LAT"] is not None and r["LON"] is not None
           and (abs(r["LAT"]) > 90 or abs(r["LON"]) > 180)]
    if bad:
        err.append("Out-of-range coordinates on rows {}".format(bad))
    return warn, err


def ingest(path: Path, sheet: Optional[str] = None) -> IngestResult:
    book = read_any(path, sheet)
    records = next(iter(book.values())) if book else []
    rows, unmatched = normalize(records)
    if rows and all(r["STATION"] == "" for r in rows):
        return IngestResult([], None, [], ["Could not find a station/location column."])
    rows, stated = split_total_row(rows)
    warn, err = validate(rows, stated)
    if unmatched:
        warn.append("Ignored columns: {}".format(unmatched))
    for r in rows:
        r["SLOTS"] = int(round(r["SLOTS"] or 0))
        if r["LAT"] is None or r["LON"] is None:
            r["LAT"] = r["LON"] = None
    return IngestResult(rows, stated, warn, err)
