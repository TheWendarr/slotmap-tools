"""Natural Earth layers: where they live, how to fetch them (where the internet is reachable), and the
populated-places table used to name detail frames (read with the stdlib DBF reader)."""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from .dbf import read_dbf

RAW = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master"
LAYERS = {
    "countries": ("10m_cultural", "ne_10m_admin_0_countries"),
    "world": ("50m_cultural", "ne_50m_admin_0_countries"),
    "admin1": ("10m_cultural", "ne_10m_admin_1_states_provinces_lakes"),
    "lakes": ("10m_physical", "ne_10m_lakes"),
    "urban": ("10m_cultural", "ne_10m_urban_areas"),
    "places": ("10m_cultural", "ne_10m_populated_places_simple"),
}


def ne_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "natural_earth"


def shp(data_dir: Optional[Path], key: str) -> Optional[Path]:
    if data_dir is None:
        return None
    p = ne_dir(data_dir) / (LAYERS[key][1] + ".shp")
    return p if p.exists() else None


def available(data_dir: Optional[Path]) -> bool:
    return data_dir is not None and all(shp(data_dir, k) for k in ("countries", "world", "admin1", "lakes"))


def fetch(data_dir: Path, force: bool = False) -> Path:
    d = ne_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    for key, (folder, stem) in LAYERS.items():
        for ext in ("shp", "shx", "dbf", "prj", "cpg"):
            p = d / "{}.{}".format(stem, ext)
            if p.exists() and not force:
                continue
            url = "{}/{}/{}.{}".format(RAW, folder, stem, ext)
            try:
                print("[fetch] " + url)
                urllib.request.urlretrieve(url, str(p))
            except Exception as e:  # .cpg is optional
                if ext != "cpg":
                    raise RuntimeError("Could not download {}: {}".format(url, e))
    return d


_PLACES = {}  # type: Dict[str, List[Dict[str, object]]]


def places(data_dir: Optional[Path]) -> List[Dict[str, object]]:
    """[{name, pop_max, latitude, longitude}] for towns of 50,000+ (empty without Natural Earth)."""
    p = shp(data_dir, "places")
    if p is None:
        return []
    k = str(p)
    if k not in _PLACES:
        rows = []
        for r in read_dbf(p.with_suffix(".dbf"), ["name", "pop_max", "latitude", "longitude"]):
            low = {kk.lower(): v for kk, v in r.items()}
            if low.get("pop_max") and low["pop_max"] >= 50000 and low.get("latitude") is not None:
                rows.append({"name": low["name"], "pop_max": low["pop_max"], "latitude": low["latitude"],
                             "longitude": low["longitude"]})
        _PLACES[k] = rows
    return _PLACES[k]
