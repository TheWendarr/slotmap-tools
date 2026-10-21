"""Stage 2 - resolve station names to coordinates: bundled installations + local overrides + town list."""
from __future__ import annotations

import csv
import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import places

GAZ_COLS = ["id", "name", "state", "country", "lat", "lon", "aliases", "coord_quality", "notes"]
LOCAL_DEFAULT = Path("gazetteer_local.csv")
DATA_FILE = Path(__file__).resolve().parent / "data" / "installations.csv"

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


def default_data_dir() -> Path:
    """Where the Natural Earth layers and the town list live (copy ~/.cache/slotmap from another machine)."""
    env = os.environ.get("SLOTMAP_DATA")
    return Path(env) if env else Path.home() / ".cache" / "slotmap"


@dataclass
class Match:
    status: str             # exact | normalized | city | fuzzy | table | unresolved
    gid: Optional[str]
    name: str
    lat: Optional[float]
    lon: Optional[float]
    score: float
    suggestions: List[str] = field(default_factory=list)
    coord_quality: str = ""


def _read_csv(path) -> List[Dict[str, str]]:
    with open(str(path), encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


class Gazetteer:
    """2.1 Bundled installations + optional local overrides (local wins)."""

    def __init__(self, rows: List[Dict[str, object]], data_dir: Optional[Path] = None):
        self.rows = rows
        self.data_dir = data_dir
        self.by_id = {r["id"]: r for r in rows}
        self.exact = {}  # type: Dict[str, int]
        self.norm = {}  # type: Dict[str, int]
        self._keys = []  # type: List[Tuple[int, str, str]]
        for i, r in enumerate(rows):
            keys = [r["name"], r["id"]] + [a for a in str(r["aliases"] or "").split("|") if a]
            for k in keys:
                self.exact.setdefault(str(k).strip().upper(), i)
                self.norm.setdefault(normalize_key(k), i)
            for k in [r["name"]] + [a for a in str(r["aliases"]).split("|") if a]:
                self._keys.append((i, normalize_key(k), r["country"]))

    @classmethod
    def load(cls, local: Optional[Path] = None, data_dir: Optional[Path] = None) -> "Gazetteer":
        base = _read_csv(DATA_FILE)
        local = Path(local) if local else LOCAL_DEFAULT
        rows, seen = [], set()
        frames = []
        if local.exists():
            loc = _read_csv(local)
            for r in loc:
                for c in GAZ_COLS:
                    r.setdefault(c, "")
                    r[c] = r[c] or ""
                r["coord_quality"] = r["coord_quality"] or "local"
            frames.append(loc)
        frames.append(base)
        for fr in frames:
            for r in fr:
                if not r.get("id") or r["id"] in seen:
                    continue
                try:
                    lat, lon = float(r["lat"]), float(r["lon"])
                except (TypeError, ValueError):
                    continue
                seen.add(r["id"])
                rows.append(dict(r, lat=lat, lon=lon, country=canon_country(r.get("country", "")),
                                 aliases=r.get("aliases", "") or "", state=r.get("state", "") or ""))
        return cls(rows, data_dir if data_dir is not None else default_data_dir())

    def _row(self, i: int, status: str, score: float) -> Match:
        r = self.rows[i]
        return Match(status, r["id"], r["name"], float(r["lat"]), float(r["lon"]), score, [], r["coord_quality"])

    def resolve(self, name: str, state: str = "", country: str = "", cutoff: float = 0.84,
                use_places: bool = True) -> Match:
        """2.3 exact alias -> normalized alias -> town ("(CITY)" names) -> fuzzy -> town fallback -> unresolved."""
        key = str(name).strip().upper()
        if key in self.exact:
            return self._row(self.exact[key], "exact", 1.0)
        for strip in (False, True):
            nk = normalize_key(name, strip_tags=strip)
            if nk in self.norm:
                return self._row(self.norm[nk], "normalized", 1.0)
        nk = normalize_key(name)
        cc = canon_country(country)
        postal = state_postal(state)
        city = places.lookup(name, cc or "USA", postal, self.data_dir) if use_places else None
        if city and places.is_city_tagged(name):
            return Match("city", None, city[2], city[0], city[1], 1.0, [], "city")
        cands = []  # type: List[Tuple[float, int, str]]
        for i, k, c in self._keys:
            if cc and c not in (cc, ""):
                continue
            cands.append((difflib.SequenceMatcher(None, nk, k).ratio(), i, self.rows[i]["name"]))
        cands.sort(key=lambda t: (-t[0], t[1]))
        seen, sugg = set(), []
        for sc, i, nm in cands:
            if nm not in seen:
                seen.add(nm)
                sugg.append("{} ({:.2f})".format(nm, sc))
            if len(sugg) == 3:
                break
        if cands and cands[0][0] >= cutoff:
            m = self._row(cands[0][1], "fuzzy", cands[0][0])
            m.suggestions = sugg
            return m
        if city:
            return Match("city", None, city[2], city[0], city[1], 0.9, sugg, "city")
        return Match("unresolved", None, display_name(name), None, None, cands[0][0] if cands else 0.0, sugg)


def resolve_rows(rows: List[Dict[str, object]], gaz: Gazetteer, accept_fuzzy: bool = False,
                 fuzzy_auto_accept: float = 0.92, use_places: bool = True) -> List[Dict[str, object]]:
    """2.4 Add GID/NAME/LAT/LON/RES_* to every row (in place) and return the resolution report rows."""
    cache = {}  # type: Dict[Tuple[str, str, str], Match]
    report = []
    gstate = {r["id"]: r["state"] for r in gaz.rows}
    for r in rows:
        ck = (str(r["STATION"]), str(r["STATE"]), str(r["COUNTRY"]))
        if ck not in cache:
            cache[ck] = gaz.resolve(ck[0], ck[1], ck[2], use_places=use_places)
        m = cache[ck]
        lat, lon, status, quality = m.lat, m.lon, m.status, m.coord_quality
        if r.get("LAT") is not None and r.get("LON") is not None:
            lat, lon, status, quality = r["LAT"], r["LON"], "table", "table"
        postal = state_postal(r["STATE"])
        if m.gid:
            gid = m.gid
        elif status == "city":
            gid = "city_" + normalize_key(m.name).lower().replace(" ", "_") + ("_" + postal.lower() if postal else "")
        else:
            gid = "tbl_" + normalize_key(r["STATION"]).lower().replace(" ", "_")
        review = status == "unresolved" or (status == "fuzzy" and not accept_fuzzy and m.score < fuzzy_auto_accept)
        cc = canon_country(r["COUNTRY"]) or "USA"
        if cc == "USA" and not postal:
            postal = gstate.get(gid, "") if m.gid else ""
        r.update(GID=gid, NAME=m.name, LAT=lat, LON=lon, RES_STATUS=status, RES_SCORE=round(m.score, 3),
                 COORD_QUALITY=quality, SUGGESTIONS="; ".join(m.suggestions), NEEDS_REVIEW=review,
                 COUNTRY_C=cc, POSTAL=postal)
        report.append({k: r[k] for k in ("SRC_ROW", "STATION", "GID", "NAME", "LAT", "LON", "RES_STATUS",
                                          "RES_SCORE", "COORD_QUALITY", "SUGGESTIONS", "NEEDS_REVIEW")})
    return report
