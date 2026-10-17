"""Stage 1 - read a slot table in whatever shape it arrives and normalize it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

CANON = ["STATION", "STATE", "COUNTRY", "SLOTS", "PCT_SRC", "LAT", "LON"]

# header variants seen in HRC / assignment exports and hand-made sheets (compared after normalizing)
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
BLANK_STATE = {"", "NO TITLE CODE FOUND", "N/A", "NA", "NONE", "-", "NULL"}


@dataclass
class IngestResult:
    table: pd.DataFrame
    stated_total: Optional[int]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _hkey(s: str) -> str:
    return re.sub(r"[^A-Z0-9%]+", " ", str(s).upper()).strip()


def read_table(path: Path, sheet: Optional[str] = None) -> pd.DataFrame:
    """1.1 Read CSV/TSV/XLSX/XLS as text (types are fixed later)."""
    path = Path(path)
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=sheet or 0, dtype=str)
    if suf in (".tsv", ".tab"):
        return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    if suf in (".csv", ".txt"):
        return pd.read_csv(path, dtype=str, keep_default_na=False, sep=None, engine="python")
    raise ValueError(f"Unsupported table type {suf!r}. Transcribe images/PDFs into templates/slots_template.csv first.")


def normalize_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """1.2 Rename recognised headers to the canonical schema. Returns (df, unmatched_headers)."""
    lookup = {_hkey(a): canon for canon, al in ALIASES.items() for a in al}
    rename, used, unmatched = {}, set(), []
    for col in df.columns:
        canon = lookup.get(_hkey(col))
        if canon and canon not in used:
            rename[col] = canon
            used.add(canon)
        else:
            unmatched.append(str(col))
    out = df.rename(columns=rename)
    for c in CANON:
        if c not in out.columns:
            out[c] = ""
    return out[CANON + [c for c in out.columns if c not in CANON]], unmatched


def _num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s == "" or s.upper() in ("NAN", "NONE"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def clean_values(df: pd.DataFrame) -> pd.DataFrame:
    """1.3 Trim text, parse numbers and percents, blank out placeholder states."""
    out = df.copy()
    for c in ("STATION", "STATE", "COUNTRY"):
        out[c] = out[c].fillna("").astype(str).str.strip()
    out.loc[out.STATE.str.upper().isin(BLANK_STATE), "STATE"] = ""
    out["SLOTS"] = out.SLOTS.map(_num)
    out["PCT_SRC"] = out.PCT_SRC.map(_num)
    out["LAT"] = out.LAT.map(_num)
    out["LON"] = out.LON.map(_num)
    out["SRC_ROW"] = range(1, len(out) + 1)
    return out


def split_total_row(df: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[int]]:
    """1.4 Drop grand-total / blank rows. A row with a number but no station name is the stated total."""
    stated = None
    is_blank_name = df.STATION.str.strip() == ""
    is_total_word = df.STATION.str.upper().str.match(r"^(GRAND\s+)?TOTALS?$")
    total_rows = df[(is_blank_name | is_total_word) & df.SLOTS.notna()]
    if len(total_rows):
        stated = int(round(total_rows.SLOTS.iloc[-1]))
    keep = ~(is_blank_name | is_total_word)
    return df[keep].reset_index(drop=True), stated


def validate_table(df: pd.DataFrame, stated_total: Optional[int]) -> Tuple[List[str], List[str]]:
    """1.5 Return (warnings, errors)."""
    warn, err = [], []
    if df.empty:
        err.append("No station rows found.")
        return warn, err
    if df.SLOTS.isna().any():
        bad = df[df.SLOTS.isna()].STATION.tolist()
        err.append(f"Missing slot counts for: {bad}")
    if (df.SLOTS.dropna() < 0).any():
        err.append("Negative slot counts present.")
    if (df.SLOTS.dropna() % 1 != 0).any():
        warn.append("Non-integer slot counts were rounded.")
    total = int(round(df.SLOTS.fillna(0).sum()))
    if stated_total is not None and stated_total != total:
        err.append(f"Rows sum to {total} but the table's total row says {stated_total}. Re-check the transcription.")
    if df.PCT_SRC.notna().any():
        psum = df.PCT_SRC.fillna(0).sum()
        if abs(psum - 100) > 2.5:
            warn.append(f"Source percents sum to {psum:.1f}, not ~100.")
    has_ll = df.LAT.notna() & df.LON.notna()
    if (df.LAT.notna() ^ df.LON.notna()).any():
        warn.append("Some rows have only one of LAT/LON; those coordinates are ignored.")
    if has_ll.any():
        bad = df[has_ll & ((df.LAT.abs() > 90) | (df.LON.abs() > 180))]
        if len(bad):
            err.append(f"Out-of-range coordinates on rows {bad.SRC_ROW.tolist()}")
    return warn, err


def ingest(path: Path, sheet: Optional[str] = None) -> IngestResult:
    raw = read_table(path, sheet)
    df, unmatched = normalize_columns(raw)
    df = clean_values(df)
    df, stated = split_total_row(df)
    warn, err = validate_table(df, stated)
    if (df.STATION == "").all() or "STATION" not in df:
        err.append("Could not find a station/location column.")
    if unmatched:
        warn.append(f"Ignored columns: {unmatched}")
    df["SLOTS"] = df.SLOTS.fillna(0).round().astype(int)
    return IngestResult(df, stated, warn, err)
