"""Town-centre placement for "(CITY)" names, from GeoNames (pop >= 1000, CC BY 4.0) via cities.json.

The file is copied onto the machine once (or fetched with `fetch-basemap` where the internet is reachable)
into <data folder>/places/cities.json. Lookups are offline.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CITIES_URL = "https://raw.githubusercontent.com/lutangar/cities.json/master/cities.json"

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
_INDEX = {}  # type: Dict[str, Dict[Tuple[str, str], List[Tuple[str, float, float, str]]]]


def cache_path(data_dir: Path) -> Path:
    return Path(data_dir) / "places" / "cities.json"


def fetch(data_dir: Path, force: bool = False) -> Path:
    import urllib.request
    p = cache_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if force or not p.exists() or p.stat().st_size < 1000000:
        print("[fetch] " + CITIES_URL)
        urllib.request.urlretrieve(CITIES_URL, str(p))
    return p


def _key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"\bSAINT\b", "ST", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _index(path: Path):
    k = str(path)
    if k not in _INDEX:
        with open(k, encoding="utf-8") as fh:
            data = json.load(fh)
        idx = {}  # type: Dict[Tuple[str, str], List[Tuple[str, float, float, str]]]
        for d in data:
            try:
                idx.setdefault((d["country"], _key(d["name"])), []).append(
                    (d.get("admin1", ""), float(d["lat"]), float(d["lng"]), d["name"]))
            except (KeyError, ValueError):
                continue
        _INDEX[k] = idx
    return _INDEX[k]


def clear_cache() -> None:
    _INDEX.clear()


def is_city_tagged(name: str) -> bool:
    return bool(_CITY_TAG.search(str(name)))


def city_query(name: str, postal: str) -> str:
    s = _CITY_TAG.sub(" ", str(name)).upper()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    if postal and s.endswith(" " + postal):
        s = s[: -len(postal)].strip(" ,")
    return s


def lookup(name: str, country_c: str, postal: str, data_dir: Optional[Path]) -> Optional[Tuple[float, float, str]]:
    if data_dir is None:
        return None
    p = cache_path(data_dir)
    if not p.exists():
        return None
    cc = COUNTRY_ISO2.get(country_c or "USA")
    if not cc:
        return None
    hits = _index(p).get((cc, _key(city_query(name, postal))), [])
    if cc == "US":
        if not postal:
            return None
        hits = [h for h in hits if h[0] == postal]
    if len(hits) != 1:
        return None
    _, lat, lon, disp = hits[0]
    return lat, lon, disp
