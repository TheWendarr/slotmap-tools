"""Stage 3 - merge, share, classify, group and rank."""
from __future__ import annotations

from bisect import bisect_right
from typing import List

import numpy as np
import pandas as pd

from .gazetteer import COUNTRY_DISPLAY

US_TERRITORIES = {"PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands", "AS": "American Samoa",
                  "MP": "Northern Mariana Islands"}


def country_display(c: str) -> str:
    return COUNTRY_DISPLAY.get(c, c.title())


def assign_theater(country_c: str, postal: str) -> str:
    """3.3"""
    if country_c == "USA":
        if postal == "AK":
            return "Alaska"
        if postal == "HI":
            return "Hawaii"
        if postal in US_TERRITORIES:
            return US_TERRITORIES[postal]
        return "CONUS"
    return country_display(country_c).replace("S. Korea", "Korea")


def classify(pct: float, breaks: List[float]) -> int:
    """3.4 index into the class scheme (0 = lowest)."""
    return bisect_right(breaks, pct) if pct > 0 else 0


def merge_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """3.1 One row per resolved installation (GID)."""
    g = df.groupby("GID", sort=False)
    out = g.agg(STATION=("STATION", "first"), NAME=("NAME", "first"), LAT=("LAT", "first"), LON=("LON", "first"),
                SLOTS=("SLOTS", "sum"), PCT_SRC=("PCT_SRC", lambda s: float(s.fillna(0).sum()) if s.notna().any() else np.nan),
                N_RECORDS=("SRC_ROW", "count"), SRC_ROWS=("SRC_ROW", lambda s: ",".join(map(str, s))),
                COUNTRY_C=("COUNTRY_C", "first"), POSTAL=("POSTAL", "first"), RES_STATUS=("RES_STATUS", "first"),
                COORD_QUALITY=("COORD_QUALITY", "first")).reset_index()
    return out


def compute_shares(st: pd.DataFrame, total: int) -> pd.DataFrame:
    """3.2"""
    st = st.copy()
    st["PCT_CALC"] = (100.0 * st.SLOTS / total).round(1) if total else 0.0
    return st


def enrich(st: pd.DataFrame, total: int, breaks: List[float]) -> pd.DataFrame:
    """3.2-3.7 in one pass: shares, theater, group, class, rank, location label."""
    st = compute_shares(st, total)
    st["THEATER"] = [assign_theater(c, p) for c, p in zip(st.COUNTRY_C, st.POSTAL)]
    st["GRP"] = [p if c == "USA" and p else country_display(c) for c, p in zip(st.COUNTRY_C, st.POSTAL)]
    st["WHERE"] = st.GRP
    st["CLS"] = [classify(100.0 * s / total if total else 0, breaks) for s in st.SLOTS]
    st = rank_stations(st)
    return st


def rank_stations(st: pd.DataFrame) -> pd.DataFrame:
    """3.7"""
    st = st.sort_values(["SLOTS", "NAME"], ascending=[False, True]).reset_index(drop=True)
    st["RANK"] = np.arange(1, len(st) + 1)
    return st


def group_totals(st: pd.DataFrame, total: int) -> pd.DataFrame:
    """3.5 by state (US) / country (overseas)."""
    g = st.groupby(["GRP", "THEATER"]).agg(SLOTS=("SLOTS", "sum"), STATIONS=("GID", "count")).reset_index()
    g["PCT"] = (100.0 * g.SLOTS / total).round(1)
    return g.sort_values("SLOTS", ascending=False).reset_index(drop=True)


def theater_totals(st: pd.DataFrame, total: int, breaks: List[float]) -> pd.DataFrame:
    """3.6 with slot-weighted centroid (stations with 0 slots carry no weight)."""
    d = st.assign(w=st.SLOTS.clip(lower=0) + 1e-9)
    d = d.assign(wx=d.LON * d.w, wy=d.LAT * d.w)
    th = d.groupby("THEATER").agg(SLOTS=("SLOTS", "sum"), STATIONS=("GID", "count"), wx=("wx", "sum"),
                                  wy=("wy", "sum"), w=("w", "sum")).reset_index()
    th["LON"], th["LAT"] = th.wx / th.w, th.wy / th.w
    th["PCT"] = 100.0 * th.SLOTS / total if total else 0.0
    th["CLS"] = [classify(p, breaks) for p in th.PCT]
    return th.drop(columns=["wx", "wy", "w"]).sort_values("SLOTS", ascending=False).reset_index(drop=True)
