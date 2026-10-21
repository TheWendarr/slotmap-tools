"""Stage 2b - place towns and cities when a table names one (e.g. "MIAMI FL (CITY)").

Source: GeoNames populated places with population >= 1000 (CC BY 4.0), as packaged in
github.com/lutangar/cities.json. Fetched once by `slotmap fetch-basemap` and cached next to the basemap.
A city match is a city-centre placement, so it is labelled `coord_quality = city` and noted on the sheet.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CITIES_URL = "https://raw.githubusercontent.com/lutangar/cities.json/master/cities.json"

# canonical country (see gazetteer.COUNTRY_CANON) -> ISO 3166-1 alpha-2 used by GeoNames
COUNTRY_ISO2 = {
    "USA": "US", "KOREA": "KR", "GERMANY": "DE", "JAPAN": "JP", "KUWAIT": "KW", "POLAND": "PL", "ITALY": "IT",
    "BELGIUM": "BE", "QATAR": "QA", "BAHRAIN": "BH", "KOSOVO": "XK", "DJIBOUTI": "DJ", "UNITED KINGDOM": "GB",
    "SPAIN": "ES", "NETHERLANDS": "NL", "ROMANIA": "RO", "LITHUANIA": "LT", "ESTONIA": "EE", "LATVIA": "LV",
    "HUNGARY": "HU", "TURKEY": "TR", "IRAQ": "IQ", "SYRIA": "SY", "JORDAN": "JO", "SAUDI ARABIA": "SA",
    "UNITED ARAB EMIRATES": "AE", "HONDURAS": "HN", "CUBA": "CU", "AUSTRALIA": "AU", "PHILIPPINES": "PH",
    "SINGAPORE": "SG", "FRANCE": "FR", "PORTUGAL": "PT", "GREECE": "GR", "BULGARIA": "BG", "NORWAY": "NO",
    "DENMARK": "DK", "CANADA": "CA", "MEXICO": "MX", "EGYPT": "EG", "ISRAEL": "IL", "THAILAND": "TH",
    "AUSTRIA": "AT", "SWITZERLAND": "CH", "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ", "SLOVAKIA": "SK",
    "GEORGIA": "GE", "UKRAINE": "UA", "FINLAND": "FI", "SWEDEN": "SE", "IRELAND": "IE", "OMAN": "OM",
}

_CITY_TAG = re.compile(r"\(\s*(CITY|TOWN|VILLAGE)\s*\)", re.I)


def cache_path(basemap_dir: Optional[Path] = None) -> Path:
    from .basemap import cache_dir
    return cache_dir(basemap_dir).parent / "places" / "cities.json"


def fetch(basemap_dir: Optional[Path] = None, force: bool = False, verbose: bool = True) -> Path:
    p = cache_path(basemap_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if force or not p.exists() or p.stat().st_size < 1_000_000:
        if verbose:
            print(f"[fetch] {CITIES_URL}")
        urllib.request.urlretrieve(CITIES_URL, p)
    return p


def _key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"\bSAINT\b", "ST", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=4)
def _index(path: str) -> Dict[Tuple[str, str], List[Tuple[str, float, float, str]]]:
    """(country2, name key) -> [(admin1, lat, lon, display name)]"""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    idx: Dict[Tuple[str, str], List[Tuple[str, float, float, str]]] = {}
    for d in data:
        try:
            idx.setdefault((d["country"], _key(d["name"])), []).append(
                (d.get("admin1", ""), float(d["lat"]), float(d["lng"]), d["name"]))
        except (KeyError, ValueError):
            continue
    return idx


def available(basemap_dir: Optional[Path] = None) -> bool:
    return cache_path(basemap_dir).exists()


def is_city_tagged(name: str) -> bool:
    return bool(_CITY_TAG.search(str(name)))


def city_query(name: str, postal: str) -> str:
    """'SPRINGFIELD VA (CITY)' -> 'SPRINGFIELD'; 'LEWISTON ID (CITY)' -> 'LEWISTON'."""
    s = _CITY_TAG.sub(" ", str(name)).upper()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    if postal and s.endswith(" " + postal):
        s = s[: -len(postal)].strip(" ,")
    return s


def lookup(name: str, country_c: str, postal: str = "", basemap_dir: Optional[Path] = None
           ) -> Optional[Tuple[float, float, str]]:
    """Return (lat, lon, display) for a uniquely identified town, else None.

    U.S. towns must match the state; elsewhere the town must be unique in its country.
    """
    p = cache_path(basemap_dir)
    if not p.exists():
        return None
    cc = COUNTRY_ISO2.get(country_c or "USA")
    if not cc:
        return None
    hits = _index(str(p)).get((cc, _key(city_query(name, postal))), [])
    if cc == "US":
        if not postal:
            return None
        hits = [h for h in hits if h[0] == postal]
    if len(hits) != 1:
        return None
    _, lat, lon, disp = hits[0]
    return lat, lon, disp
