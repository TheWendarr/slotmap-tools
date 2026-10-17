"""Stage 4 - build the feature classes / tables and write GeoPackage (+ FileGDB, + exports)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
from shapely.geometry import Point, Polygon

from . import fgdb
from .plan import GEO, Plan, crs_for


def build_points(records: pd.DataFrame, stations: pd.DataFrame) -> gpd.GeoDataFrame:
    """4.1 One point per resolved source record, carrying record + group fields."""
    r = records[records.LAT.notna()].copy()
    grp = stations.set_index("GID")[["GRP", "THEATER", "CLS", "RANK"]]
    r = r.join(grp, on="GID")
    g = stations.groupby("GRP").agg(GRP_SLOTS=("SLOTS", "sum"), GRP_N=("GID", "count"))
    r = r.join(g, on="GRP")
    total = int(records.SLOTS.sum())
    r["PCT_CALC"] = (100 * r.SLOTS / total).round(2) if total else 0.0
    r["GRP_PCT"] = (100 * r.GRP_SLOTS / total).round(1) if total else 0.0
    cols = ["SRC_ROW", "STATION", "NAME", "GID", "STATE", "COUNTRY", "SLOTS", "PCT_SRC", "PCT_CALC", "GRP",
            "GRP_SLOTS", "GRP_PCT", "GRP_N", "THEATER", "CLS", "RANK", "RES_STATUS", "COORD_QUALITY", "LAT", "LON"]
    r = r[[c for c in cols if c in r.columns]]
    return gpd.GeoDataFrame(r, geometry=[Point(x, y) for x, y in zip(r.LON, r.LAT)], crs="EPSG:4326")


def build_station_table(stations: pd.DataFrame, letters: Dict[str, str]) -> pd.DataFrame:
    """4.2"""
    t = stations.copy()
    t["FRAME"] = t.GID.map(letters).fillna("")
    t = t.rename(columns={"WHERE": "LOC_LABEL"})   # WHERE is a reserved word in FileGDB
    cols = ["RANK", "NAME", "STATION", "GID", "LOC_LABEL", "THEATER", "SLOTS", "PCT_CALC", "PCT_SRC", "N_RECORDS",
            "SRC_ROWS", "CLS", "FRAME", "LAT", "LON", "COORD_QUALITY", "RES_STATUS"]
    return t[[c for c in cols if c in t.columns]]


def build_station_points(table: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(table, geometry=[Point(x, y) for x, y in zip(table.LON, table.LAT)], crs="EPSG:4326")


def build_frame_extents(plan: Plan) -> gpd.GeoDataFrame:
    """4.3 Footprint polygon of every frame (densified, WGS84)."""
    rows, geoms = [], []
    for f in plan.frames:
        if f.kind == "world":
            continue
        proj = crs_for(f)
        x0, x1, y0, y1 = f.extent
        n = 40
        xs = np.r_[np.linspace(x0, x1, n), np.full(n, x1), np.linspace(x1, x0, n), np.full(n, x0)]
        ys = np.r_[np.full(n, y0), np.linspace(y0, y1, n), np.full(n, y1), np.linspace(y1, y0, n)]
        p = GEO.transform_points(proj, xs, ys)
        geoms.append(Polygon(p[:, :2]))
        rows.append(dict(LETTER=f.letter, FRAME_ID=f.fid, TITLE=f.title, KIND=f.kind, PARENT=f.parent or "",
                         N_OWNED=len(f.members), HALFWIDTH_KM=round(f.halfwidth_km, 2), PROJ=f.proj,
                         CENTER_LON=f.center[0], CENTER_LAT=f.center[1]))
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


def write_geopackage(layers: Dict[str, pd.DataFrame], path: Path) -> None:
    """4.4 Spatial layers and attribute tables in one .gpkg (rebuilt each run)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    for name, df in layers.items():
        pyogrio.write_dataframe(df, str(path), layer=name, driver="GPKG", append=path.exists())


def write_all(layers: Dict[str, pd.DataFrame], out_dir: Path, slug: str, gdb: bool = True,
              exports: Optional[List[str]] = None) -> Dict[str, object]:
    out: Dict[str, object] = {}
    gpkg = out_dir / f"{slug}.gpkg"
    write_geopackage(layers, gpkg)
    out["gpkg"] = str(gpkg)
    if gdb:
        if fgdb.filegdb_supported():
            p = out_dir / f"{slug}.gdb"
            out["gdb"] = str(p)
            out["gdb_layers"] = fgdb.write_filegdb(layers, p, if_exists="replace")
        else:
            out["gdb_skipped"] = "GDAL build lacks OpenFileGDB write support"
    for fmt in exports or []:
        spatial = {k: v for k, v in layers.items() if isinstance(v, gpd.GeoDataFrame)}
        out[f"export_{fmt}"] = [str(p) for p in fgdb.export_layers(spatial, out_dir / "exports" / fmt, fmt)]
    return out
