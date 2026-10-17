"""Esri File Geodatabase + vector export helpers.

Adapted from fgdb-tools (https://github.com/TheWendarr/fgdb-tools, MIT): the same pyogrio/GDAL
OpenFileGDB write path, feature-class name sanitizing, and unique/skip/fail behaviour when a
name already exists. If fgdb-tools is installed, its `sanitize_fc_name` is used directly so the
two tools agree on naming.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import geopandas as gpd
import pandas as pd

try:
    import pyogrio  # type: ignore
except Exception:  # pragma: no cover
    pyogrio = None  # type: ignore

try:  # prefer the original implementation when available
    from fgdb_tools.common import sanitize_fc_name as _fc_name  # type: ignore
except Exception:  # pragma: no cover - fgdb-tools is optional
    _fc_name = None

IF_EXISTS = ("unique", "skip", "fail", "replace")
EXPORT_DRIVERS = {"geojson": ("GeoJSON", ".geojson"), "shp": ("ESRI Shapefile", ".shp"), "kml": ("KML", ".kml")}


def sanitize_fc_name(name: str, max_len: int = 120) -> str:
    """FileGDB-friendly name: letters/numbers/underscore, starts with a letter."""
    if _fc_name is not None:
        return _fc_name(name, max_len)
    n = re.sub(r"\s+", "_", (name or "").strip())
    n = re.sub(r"[^A-Za-z0-9_]+", "_", n)
    n = re.sub(r"_+", "_", n).strip("_") or "fc"
    if not re.match(r"^[A-Za-z]", n):
        n = f"fc_{n}"
    return n[:max_len]


def filegdb_supported() -> bool:
    if pyogrio is None:
        return False
    mode = pyogrio.list_drivers(write=True).get("OpenFileGDB")
    return mode is not None and "w" in str(mode)


def _existing_layers(path: Path) -> List[str]:
    if not path.exists():
        return []
    try:
        return [str(r[0]) for r in pyogrio.list_layers(str(path))]
    except Exception:
        return []


def write_filegdb(layers: Dict[str, pd.DataFrame], gdb: Path, if_exists: str = "replace") -> List[str]:
    """4.5 Write GeoDataFrames (feature classes) and plain DataFrames (tables) into a .gdb.

    if_exists: replace = rebuild the whole .gdb; unique = suffix _2, _3...; skip; fail.
    Returns the layer names written.
    """
    if not filegdb_supported():
        raise RuntimeError("This GDAL build cannot write OpenFileGDB. Use a conda-forge GDAL >= 3.6, "
                           "or rely on the GeoPackage output.")
    if if_exists not in IF_EXISTS:
        raise ValueError(f"if_exists must be one of {IF_EXISTS}")
    gdb = Path(gdb)
    if gdb.suffix.lower() != ".gdb":
        raise ValueError("FileGDB path must end with .gdb")
    if if_exists == "replace" and gdb.exists():
        shutil.rmtree(gdb)
    used = set(_existing_layers(gdb))
    written = []
    for raw_name, df in layers.items():
        name = sanitize_fc_name(raw_name)
        if name in used:
            if if_exists == "skip":
                continue
            if if_exists == "fail":
                raise FileExistsError(f"{name} already exists in {gdb}")
            i = 2
            while f"{name}_{i}" in used:
                i += 1
            name = f"{name}_{i}"
        used.add(name)
        frame = df if isinstance(df, gpd.GeoDataFrame) else pd.DataFrame(df)
        frame = frame.copy()
        for c in frame.columns:  # Int64 needs ArcGIS Pro 3.2+; int32 works everywhere
            if str(frame[c].dtype) in ("int64", "Int64") and frame[c].abs().max() < 2**31:
                frame[c] = frame[c].astype("int32")
        frame.columns = [sanitize_fc_name(c, 64) for c in frame.columns]
        if isinstance(df, gpd.GeoDataFrame):
            frame = gpd.GeoDataFrame(frame, geometry=sanitize_fc_name(df.geometry.name, 64), crs=df.crs)
        pyogrio.write_dataframe(frame, str(gdb), layer=name, driver="OpenFileGDB", append=False)
        written.append(name)
    return written


def export_layers(layers: Dict[str, gpd.GeoDataFrame], out_dir: Path, fmt: str = "geojson",
                  epsg: Optional[int] = None) -> List[Path]:
    """4.6 One file per spatial layer (fgdb-export behaviour). KML forces EPSG:4326."""
    fmt = fmt.lower()
    if fmt not in EXPORT_DRIVERS:
        raise ValueError(f"fmt must be one of {list(EXPORT_DRIVERS)}")
    driver, ext = EXPORT_DRIVERS[fmt]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, gdf in layers.items():
        if not isinstance(gdf, gpd.GeoDataFrame):
            continue
        g = gdf.to_crs(4326 if fmt == "kml" else (epsg or gdf.crs))
        p = out_dir / f"{sanitize_fc_name(name)}{ext}"
        if fmt == "shp":  # shapefile field names are capped at 10 chars
            g = g.rename(columns={c: c[:10] for c in g.columns if c != g.geometry.name})
        pyogrio.write_dataframe(g, str(p), driver=driver)
        paths.append(p)
    return paths
