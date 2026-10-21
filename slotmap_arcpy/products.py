"""Stage 4 - tables (CSV, stdlib) and GIS data (FileGDB + GeoPackage, ArcPy).

Feature classes (WGS84):
  auth_points        one point per source record
  duty_stations      one point per installation (the station table with geometry)
  frame_extents      footprint of every map frame, with LETTER and PARENT (draws the lettered boxes)
  sheet_points       one point per (frame, station) at its displayed position - what the sheet draws
  theater_points     theater totals at their slot-weighted centre (world frame)
Tables: duty_station_table, group_totals, theater_totals
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .geo import boundary_lonlat
from .plan import Plan, radius_pt

STATION_COLS = ["RANK", "NAME", "STATION", "GID", "LOC_LABEL", "THEATER", "SLOTS", "PCT_CALC", "PCT_SRC", "N_RECORDS",
                "SRC_ROWS", "CLS", "FRAME", "LAT", "LON", "COORD_QUALITY", "RES_STATUS"]


def write_csv(path: Path, rows: List[Dict[str, object]], cols: Optional[Sequence[str]] = None) -> None:
    cols = list(cols) if cols else (list(rows[0].keys()) if rows else [])
    with open(str(path), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})


def station_table(st_all: List[Dict[str, object]], letters: Dict[str, str]) -> List[Dict[str, object]]:
    out = []
    for s in st_all:
        r = dict(s)
        r["FRAME"] = letters.get(s["GID"], "")
        r["LOC_LABEL"] = s.get("WHERE", "")          # WHERE is a reserved word in FileGDB
        out.append({c: r.get(c) for c in STATION_COLS})
    return out


def symkey(cls: int, diameter_pt: float) -> str:
    return "c{}_d{:.1f}".format(int(cls), round(diameter_pt * 2) / 2)


def unit(n: int, label: str) -> str:
    return label[:-1] if n == 1 and label.endswith("s") else label


def sheet_points(plan: Plan, st: List[Dict[str, object]], value_label: str) -> List[Dict[str, object]]:
    """Every station each frame shows (owned or not), at its displayed position, with its symbol key."""
    out = []
    for f in plan.frames:
        proj = f.projection()
        x0, x1, y0, y1 = f.extent
        for s in st:
            lon, lat = f.display.get(s["GID"], (float(s["LON"]), float(s["LAT"])))
            if f.kind != "world":
                x, y = proj.fwd(lon, lat)
                if not (x0 <= x <= x1 and y0 <= y <= y1):
                    continue
            d = 2 * radius_pt(s["SLOTS"], plan.symbol_k)
            n = int(s["SLOTS"])
            out.append({"FRAME": f.letter, "GID": s["GID"], "NAME": s["NAME"],
                        "LBL_NAME": s.get("LBL_NAME") or s["NAME"],
                        "DETAIL": "{} {} · {:.1f}%".format(n, unit(n, value_label), float(s["PCT_CALC"])),
                        "SLOTS": n, "PCT": float(s["PCT_CALC"]), "CLS": int(s["CLS"]),
                        "SYMKEY": "dot" if f.kind == "world" else symkey(int(s["CLS"]), d),
                        "OWNED": 1 if s["GID"] in f.members else 0, "LON": lon, "LAT": lat})
    return out


def theater_points(plan: Plan, th: List[Dict[str, object]], value_label: str) -> List[Dict[str, object]]:
    out = []
    for t in th:
        d = 2 * radius_pt(t["SLOTS"], plan.world_k)
        n = int(t["SLOTS"])
        name = "CONUS" if t["THEATER"] == "CONUS" else t["THEATER"]
        out.append({"THEATER": t["THEATER"], "LBL_NAME": name, "SLOTS": n, "PCT": round(float(t["PCT"]), 1),
                    "DETAIL": "{} {} · {:.1f}%".format(n, unit(n, value_label), float(t["PCT"])),
                    "CLS": int(t["CLS"]), "SYMKEY": symkey(int(t["CLS"]), d), "LON": t["LON"], "LAT": t["LAT"]})
    return out


def frame_extents(plan: Plan) -> List[Tuple[Dict[str, object], List[Tuple[float, float]]]]:
    out = []
    for f in plan.frames:
        if f.kind == "world":
            continue
        ring = boundary_lonlat(f.projection(), f.extent, 30)
        out.append(({"LETTER": f.letter, "FRAME_ID": f.fid, "TITLE": f.title, "KIND": f.kind,
                     "PARENT": f.parent or "", "HALF_KM": round(f.halfwidth_km, 3)}, ring))
    return out


# --------------------------------------------------------------------------- ArcPy writers
def _ftype(values) -> Tuple[str, Optional[int]]:
    vals = [v for v in values if v is not None and v != ""]
    if vals and all(isinstance(v, bool) or isinstance(v, int) for v in vals):
        return "LONG", None
    if vals and all(isinstance(v, (int, float)) for v in vals):
        return "DOUBLE", None
    ln = max([len(str(v)) for v in vals] + [1])
    return "TEXT", max(16, min(1024, ln * 2))


def _create(arcpy, ws: str, name: str, geom: Optional[str], rows: List[Dict[str, object]],
            cols: Sequence[str], sr) -> str:
    out = os.path.join(ws, name)
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    if geom:
        arcpy.management.CreateFeatureclass(ws, name, geom, spatial_reference=sr)
    else:
        arcpy.management.CreateTable(ws, name)
    types = {}
    for c in cols:
        t, ln = _ftype([r.get(c) for r in rows])
        types[c] = t
        if ln:
            arcpy.management.AddField(out, c, t, field_length=ln)
        else:
            arcpy.management.AddField(out, c, t)
    return out


def _value(v, t):
    if v is None or v == "":
        return None
    if t == "LONG":
        return int(v)
    if t == "DOUBLE":
        return float(v)
    return str(v)


def write_gis(out_dir: Path, slug: str, recs: List[Dict[str, object]], table: List[Dict[str, object]],
              groups: List[Dict[str, object]], theaters: List[Dict[str, object]], plan: Plan,
              sheet_pts: List[Dict[str, object]], theater_pts: List[Dict[str, object]],
              gpkg: bool = True) -> Dict[str, str]:
    """Write the FileGDB (always) and a GeoPackage copy (optional). Returns {'gdb': path, 'gpkg': path}."""
    import arcpy
    arcpy.env.overwriteOutput = True
    sr = arcpy.SpatialReference(4326)
    gdb = Path(out_dir) / "{}.gdb".format(slug)
    if not arcpy.Exists(str(gdb)):
        arcpy.management.CreateFileGDB(str(out_dir), gdb.name)
    ws = str(gdb)
    written = {"gdb": ws}

    def points(name, rows, cols):
        fc = _create(arcpy, ws, name, "POINT", rows, cols, sr)
        types = {c: _ftype([r.get(c) for r in rows])[0] for c in cols}
        with arcpy.da.InsertCursor(fc, ["SHAPE@XY"] + list(cols)) as cur:
            for r in rows:
                if r.get("LON") is None or r.get("LAT") is None:
                    continue
                cur.insertRow([(float(r["LON"]), float(r["LAT"]))] + [_value(r.get(c), types[c]) for c in cols])
        return fc

    def table_(name, rows, cols):
        tb = _create(arcpy, ws, name, None, rows, cols, sr)
        types = {c: _ftype([r.get(c) for r in rows])[0] for c in cols}
        with arcpy.da.InsertCursor(tb, list(cols)) as cur:
            for r in rows:
                cur.insertRow([_value(r.get(c), types[c]) for c in cols])
        return tb

    rec_cols = ["SRC_ROW", "STATION", "NAME", "GID", "STATE", "COUNTRY", "SLOTS", "PCT_SRC", "RES_STATUS",
                "COORD_QUALITY", "LAT", "LON"]
    points("auth_points", recs, rec_cols)
    points("duty_stations", [r for r in table if r.get("LAT") is not None], STATION_COLS)
    sp_cols = ["FRAME", "GID", "NAME", "LBL_NAME", "DETAIL", "SLOTS", "PCT", "CLS", "SYMKEY", "OWNED"]
    points("sheet_points", sheet_pts, sp_cols)
    points("theater_points", theater_pts, ["THEATER", "LBL_NAME", "DETAIL", "SLOTS", "PCT", "CLS", "SYMKEY"])
    ext = frame_extents(plan)
    cols = ["LETTER", "FRAME_ID", "TITLE", "KIND", "PARENT", "HALF_KM"]
    fc = _create(arcpy, ws, "frame_extents", "POLYGON", [a for a, _ in ext], cols, sr)
    types = {c: _ftype([a.get(c) for a, _ in ext])[0] for c in cols}
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + cols) as cur:
        for attrs, ring in ext:
            poly = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in ring]), sr)
            cur.insertRow([poly] + [_value(attrs[c], types[c]) for c in cols])
    table_("duty_station_table", table, [c for c in STATION_COLS if c not in ("LAT", "LON")])
    table_("group_totals", groups, ["GRP", "THEATER", "SLOTS", "STATIONS", "PCT"])
    table_("theater_totals", theaters, ["THEATER", "SLOTS", "STATIONS", "PCT", "CLS", "LON", "LAT"])
    if gpkg:
        try:
            gp = Path(out_dir) / "{}.gpkg".format(slug)
            if not gp.exists():
                arcpy.management.CreateSQLiteDatabase(str(gp), "GEOPACKAGE")
            for name in ("auth_points", "duty_stations", "frame_extents", "theater_points"):
                arcpy.conversion.FeatureClassToFeatureClass(os.path.join(ws, name), str(gp), name)
            for name in ("duty_station_table", "group_totals", "theater_totals"):
                arcpy.conversion.TableToTable(os.path.join(ws, name), str(gp), name)
            written["gpkg"] = str(gp)
        except Exception as e:  # GeoPackage is a convenience copy; the FileGDB is the product
            written["gpkg_error"] = "{}: {}".format(type(e).__name__, e)
    return written
