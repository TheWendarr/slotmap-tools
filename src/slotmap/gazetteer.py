"""Stage 2 - resolve station names to coordinates with a bundled + local gazetteer."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

GAZ_COLS = ["id", "name", "state", "country", "lat", "lon", "aliases", "coord_quality", "notes"]
LOCAL_DEFAULT = Path("gazetteer_local.csv")

COUNTRY_CANON = {
    "USA": "USA", "US": "USA", "UNITED STATES": "USA", "UNITED STATES OF AMERICA": "USA",
    "KOREA, REPUBLIC OF": "KOREA", "REPUBLIC OF KOREA": "KOREA", "SOUTH KOREA": "KOREA", "KOREA": "KOREA",
    "ROK": "KOREA", "GERMANY": "GERMANY", "DEU": "GERMANY", "JAPAN": "JAPAN", "KUWAIT": "KUWAIT",
    "POLAND": "POLAND", "ITALY": "ITALY", "BELGIUM": "BELGIUM", "QATAR": "QATAR", "BAHRAIN": "BAHRAIN",
    "KOSOVO": "KOSOVO", "DJIBOUTI": "DJIBOUTI", "UNITED KINGDOM": "UNITED KINGDOM", "UK": "UNITED KINGDOM",
    "SPAIN": "SPAIN", "NETHERLANDS": "NETHERLANDS", "ROMANIA": "ROMANIA", "LITHUANIA": "LITHUANIA",
    "ESTONIA": "ESTONIA", "LATVIA": "LATVIA", "HUNGARY": "HUNGARY", "TURKEY": "TURKEY", "TURKIYE": "TURKEY",
    "IRAQ": "IRAQ", "SYRIA": "SYRIA", "JORDAN": "JORDAN", "SAUDI ARABIA": "SAUDI ARABIA",
    "UNITED ARAB EMIRATES": "UNITED ARAB EMIRATES", "UAE": "UNITED ARAB EMIRATES", "HONDURAS": "HONDURAS",
    "CUBA": "CUBA", "AUSTRALIA": "AUSTRALIA", "PHILIPPINES": "PHILIPPINES", "SINGAPORE": "SINGAPORE",
}
COUNTRY_DISPLAY = {"USA": "USA", "KOREA": "S. Korea", "UNITED ARAB EMIRATES": "UAE"}
# Natural Earth ADM0_A3 codes, used to tint the focus country and pick frame names
COUNTRY_A3 = {"USA": "USA", "KOREA": "KOR", "GERMANY": "DEU", "JAPAN": "JPN", "KUWAIT": "KWT", "POLAND": "POL",
              "ITALY": "ITA", "BELGIUM": "BEL", "QATAR": "QAT", "BAHRAIN": "BHR", "KOSOVO": "KOS",
              "DJIBOUTI": "DJI", "UNITED KINGDOM": "GBR", "SPAIN": "ESP", "NETHERLANDS": "NLD", "ROMANIA": "ROU",
              "LITHUANIA": "LTU", "ESTONIA": "EST", "LATVIA": "LVA", "HUNGARY": "HUN", "TURKEY": "TUR",
              "IRAQ": "IRQ", "SYRIA": "SYR", "JORDAN": "JOR", "SAUDI ARABIA": "SAU",
              "UNITED ARAB EMIRATES": "ARE", "HONDURAS": "HND", "CUBA": "CUB", "AUSTRALIA": "AUS",
              "PHILIPPINES": "PHL", "SINGAPORE": "SGP"}

US_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO",
    "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY", "PUERTO RICO": "PR", "GUAM": "GU",
    "VIRGIN ISLANDS": "VI", "AMERICAN SAMOA": "AS", "NORTHERN MARIANA ISLANDS": "MP",
}

_TAG = re.compile(r"\((ADMIN|ADMINISTRATION|AIN|CITY|COMM SITE|MILITARY AIRPORT|[A-Z]{2})\)")
_SUBS = [(r"\bFT\b", "FORT"), (r"\bAIR FORCE BASE\b", "AFB"), (r"\bAIR BASE\b", "AB"),
         (r"\bARMY AIRFIELD\b", "AAF"), (r"\bSPACE FORCE BASE\b", "SFB"), (r"\bJOINT BASE\b", "JB"),
         (r"\bMILITARY RESERVATION\b", ""), (r"\bKASERNE\b", "KASERNE"), (r"\bCP\b", "CAMP"),
         (r"Ä", "AE"), (r"Ö", "OE"), (r"Ü", "UE"), (r"ß", "SS")]


def normalize_key(name: str, strip_tags: bool = True) -> str:
    """2.2 Comparable key for a station name."""
    s = str(name or "").upper().strip()
    if strip_tags:
        s = _TAG.sub(" ", s)
    for pat, rep in _SUBS:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s.replace("-", " "))
    return re.sub(r"\s+", " ", s).strip()


def canon_country(c: str) -> str:
    c = str(c or "").strip().upper()
    if not c:
        return ""
    return COUNTRY_CANON.get(c, c)


def state_postal(s: str) -> str:
    s = str(s or "").strip().upper()
    if not s:
        return ""
    if len(s) == 2 and s in US_STATES.values():
        return s
    return US_STATES.get(s, "")


def display_name(raw: str) -> str:
    """2.5 Fallback label when a station is not in the gazetteer."""
    s = _TAG.sub("", str(raw)).strip()
    s = re.sub(r"\s*\(.*?\)\s*", " ", s).strip()
    s = s.title()
    for a, b in [("Afb", "AFB"), ("Aaf", "AAF"), ("Sfb", "SFB"), ("Jb ", "JB "), (" Ab", " AB"), ("Usag", "USAG")]:
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s)


@dataclass
class Match:
    status: str             # exact | normalized | fuzzy | table | unresolved
    gid: Optional[str]
    name: str
    lat: Optional[float]
    lon: Optional[float]
    score: float
    suggestions: List[str]
    coord_quality: str = ""


class Gazetteer:
    """2.1 Bundled installations + optional local overrides (local wins)."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        self.exact: Dict[str, int] = {}
        self.norm: Dict[str, int] = {}
        for i, r in self.df.iterrows():
            keys = [r["name"], r["id"]] + [a for a in str(r["aliases"] or "").split("|") if a]
            for k in keys:
                self.exact.setdefault(str(k).strip().upper(), i)
                self.norm.setdefault(normalize_key(k), i)

    @classmethod
    def load(cls, local: Optional[Path] = None) -> "Gazetteer":
        with resources.files("slotmap.data").joinpath("installations.csv").open("r", encoding="utf-8") as fh:
            base = pd.read_csv(fh, dtype=str, keep_default_na=False)
        frames = []
        local = Path(local) if local else LOCAL_DEFAULT
        if local.exists():
            loc = pd.read_csv(local, dtype=str, keep_default_na=False)
            for c in GAZ_COLS:
                if c not in loc:
                    loc[c] = ""
            loc["coord_quality"] = loc["coord_quality"].replace("", "local")
            frames.append(loc[GAZ_COLS])
        frames.append(base[GAZ_COLS])
        df = pd.concat(frames, ignore_index=True).drop_duplicates("id", keep="first")
        df["lat"] = df.lat.astype(float)
        df["lon"] = df.lon.astype(float)
        df["country"] = df.country.map(canon_country)
        return cls(df)

    def _row(self, i: int, status: str, score: float) -> Match:
        r = self.df.iloc[i]
        return Match(status, r["id"], r["name"], float(r["lat"]), float(r["lon"]), score, [], r["coord_quality"])

    def resolve(self, name: str, state: str = "", country: str = "", cutoff: float = 0.84) -> Match:
        """2.3 exact alias -> normalized alias -> fuzzy (same country) -> unresolved."""
        key = str(name).strip().upper()
        if key in self.exact:
            return self._row(self.exact[key], "exact", 1.0)
        for strip in (False, True):
            nk = normalize_key(name, strip_tags=strip)
            if nk in self.norm:
                return self._row(self.norm[nk], "normalized", 1.0)
        nk = normalize_key(name)
        cc = canon_country(country)
        pool = self.df if not cc else self.df[(self.df.country == cc) | (self.df.country == "")]
        cands: List[Tuple[float, int, str]] = []
        for i, r in pool.iterrows():
            for k in [r["name"]] + [a for a in str(r["aliases"]).split("|") if a]:
                sc = difflib.SequenceMatcher(None, nk, normalize_key(k)).ratio()
                cands.append((sc, i, r["name"]))
        cands.sort(reverse=True)
        seen, sugg = set(), []
        for sc, i, nm in cands:
            if nm not in seen:
                seen.add(nm)
                sugg.append(f"{nm} ({sc:.2f})")
            if len(sugg) == 3:
                break
        if cands and cands[0][0] >= cutoff:
            m = self._row(cands[0][1], "fuzzy", cands[0][0])
            m.suggestions = sugg
            return m
        return Match("unresolved", None, display_name(name), None, None, cands[0][0] if cands else 0.0, sugg)


def resolve_table(df: pd.DataFrame, gaz: Gazetteer, accept_fuzzy: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """2.4 Add GID/NAME/LAT/LON/RES_STATUS to every row. Table coordinates win.

    Returns (resolved_df, report_df). Rows that are unresolved (or fuzzy without accept_fuzzy) have NEEDS_REVIEW=True.
    """
    out = df.copy()
    rows = []
    for r in out.itertuples():
        m = gaz.resolve(r.STATION, r.STATE, r.COUNTRY)
        lat, lon, status, quality = m.lat, m.lon, m.status, m.coord_quality
        if pd.notna(r.LAT) and pd.notna(r.LON):
            lat, lon, status, quality = r.LAT, r.LON, "table", "table"
        gid = m.gid or ("tbl_" + normalize_key(r.STATION).lower().replace(" ", "_"))
        review = status == "unresolved" or (status == "fuzzy" and not accept_fuzzy)
        rows.append(dict(SRC_ROW=r.SRC_ROW, STATION=r.STATION, GID=gid, NAME=m.name, LAT=lat, LON=lon,
                         RES_STATUS=status, RES_SCORE=round(m.score, 3), COORD_QUALITY=quality,
                         SUGGESTIONS="; ".join(m.suggestions), NEEDS_REVIEW=review))
    rep = pd.DataFrame(rows)
    out = out.drop(columns=["LAT", "LON"]).merge(rep.drop(columns=["STATION"]), on="SRC_ROW", how="left")
    out["COUNTRY_C"] = out.COUNTRY.map(canon_country)
    out.loc[out.COUNTRY_C == "", "COUNTRY_C"] = "USA"
    out["POSTAL"] = out.STATE.map(state_postal)
    # fill state from gazetteer for US rows that came without one
    gstate = dict(zip(gaz.df.id, gaz.df.state))
    miss = (out.COUNTRY_C == "USA") & (out.POSTAL == "")
    out.loc[miss, "POSTAL"] = out.loc[miss, "GID"].map(gstate).fillna("")
    return out, rep
