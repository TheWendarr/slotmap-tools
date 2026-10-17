"""Natural Earth basemap: fetch once, cache, load, clip per frame."""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Dict, Optional

import geopandas as gpd
import numpy as np
from shapely.geometry import box

RAW = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master"
LAYERS = {
    "countries": ("10m_cultural", "ne_10m_admin_0_countries", ["ADM0_A3", "NAME", "CONTINENT", "geometry"]),
    "world": ("50m_cultural", "ne_50m_admin_0_countries", ["ADM0_A3", "geometry"]),
    "admin1": ("10m_cultural", "ne_10m_admin_1_states_provinces_lakes", ["adm0_a3", "postal", "name", "geometry"]),
    "lakes": ("10m_physical", "ne_10m_lakes", ["scalerank", "geometry"]),
    "urban": ("10m_cultural", "ne_10m_urban_areas", ["geometry"]),
    "places": ("10m_cultural", "ne_10m_populated_places_simple",
               ["name", "adm0_a3", "adm1name", "pop_max", "scalerank", "latitude", "longitude", "geometry"]),
}


def cache_dir(explicit: Optional[Path] = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("SLOTMAP_BASEMAP")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "slotmap" / "natural_earth"


def fetch(dest: Optional[Path] = None, force: bool = False, verbose: bool = True) -> Path:
    """6.1 Download the Natural Earth shapefiles (~90 MB) from GitHub."""
    d = cache_dir(dest)
    d.mkdir(parents=True, exist_ok=True)
    for key, (folder, stem, _) in LAYERS.items():
        for ext in ("shp", "shx", "dbf", "prj", "cpg"):
            p = d / f"{stem}.{ext}"
            if p.exists() and not force:
                continue
            url = f"{RAW}/{folder}/{stem}.{ext}"
            if verbose:
                print(f"[fetch] {url}")
            urllib.request.urlretrieve(url, p)
    return d


def available(d: Optional[Path] = None) -> bool:
    d = cache_dir(d)
    return all((d / f"{stem}.shp").exists() for _, stem, _ in LAYERS.values())


class Basemap:
    def __init__(self, layers: Dict[str, gpd.GeoDataFrame]):
        self.layers = layers

    @classmethod
    def load(cls, d: Optional[Path] = None) -> Optional["Basemap"]:
        d = cache_dir(d)
        if not available(d):
            return None
        out = {}
        for key, (_, stem, cols) in LAYERS.items():
            g = gpd.read_file(d / f"{stem}.shp", encoding="utf-8")
            out[key] = g[[c for c in cols if c in g.columns]]
        return cls(out)

    def get(self, key: str) -> gpd.GeoDataFrame:
        return self.layers[key]

    def clip(self, key: str, lon: np.ndarray, lat: np.ndarray, pad: float = 1.0, where=None) -> gpd.GeoDataFrame:
        g = self.layers[key]
        if where is not None:
            g = g[where(g)]
        b = box(float(np.min(lon)) - pad, float(np.min(lat)) - pad, float(np.max(lon)) + pad, float(np.max(lat)) + pad)
        sub = g[g.intersects(b)].copy()
        if key != "places":
            sub["geometry"] = sub.geometry.intersection(b)
        return sub[~sub.geometry.is_empty]
