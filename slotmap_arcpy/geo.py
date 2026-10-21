"""Equal-area projection math in pure Python, plus the matching Esri WKT for ArcPy.

Planning (frame extents, symbol collisions, displacement) runs on a sphere with the WGS84 authalic radius.
ArcPy renders with the same projections on the WGS84 ellipsoid; the difference (< 0.3 %) is absorbed by
frame padding.
"""
from __future__ import annotations

import math
from typing import List, Tuple

R = 6371007.181          # WGS84 authalic radius (m)
CONUS = dict(lon0=-96.0, lat0=37.5, lat1=29.5, lat2=45.5)
CONUS_BOUNDS = (-2356105.0, 2256152.0, -1334000.0, 1564784.0)   # x0, x1, y0, y1 of the lower 48

GCS_WGS84 = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
             'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def mean_lonlat(lons, lats, w=None) -> Tuple[float, float]:
    """Spherical mean (safe across the antimeridian)."""
    lons, lats = list(lons), list(lats)
    w = [1.0] * len(lons) if w is None else [float(v) + 1e-9 for v in w]
    x = y = z = 0.0
    for lo, la, ww in zip(lons, lats, w):
        lo, la = math.radians(lo), math.radians(la)
        x += ww * math.cos(la) * math.cos(lo)
        y += ww * math.cos(la) * math.sin(lo)
        z += ww * math.sin(la)
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))


# --------------------------------------------------------------------------- projections
class LAEA:
    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        self.p0 = math.radians(lat0)

    def fwd(self, lon: float, lat: float) -> Tuple[float, float]:
        l = math.radians(lon - self.lon0)
        p = math.radians(lat)
        c = 1 + math.sin(self.p0) * math.sin(p) + math.cos(self.p0) * math.cos(p) * math.cos(l)
        k = math.sqrt(2 / max(c, 1e-12))
        x = R * k * math.cos(p) * math.sin(l)
        y = R * k * (math.cos(self.p0) * math.sin(p) - math.sin(self.p0) * math.cos(p) * math.cos(l))
        return x, y

    def inv(self, x: float, y: float) -> Tuple[float, float]:
        rho = math.hypot(x, y)
        if rho < 1e-9:
            return self.lon0, self.lat0
        c = 2 * math.asin(min(1.0, rho / (2 * R)))
        p = math.asin(math.cos(c) * math.sin(self.p0) + y * math.sin(c) * math.cos(self.p0) / rho)
        l = math.atan2(x * math.sin(c), rho * math.cos(self.p0) * math.cos(c) - y * math.sin(self.p0) * math.sin(c))
        return _wrap(self.lon0 + math.degrees(l)), math.degrees(p)

    def wkt(self) -> str:
        return ('PROJCS["slotmap_LAEA",{},PROJECTION["Lambert_Azimuthal_Equal_Area"],'
                'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
                'PARAMETER["Central_Meridian",{:.6f}],PARAMETER["Latitude_Of_Origin",{:.6f}],'
                'UNIT["Meter",1.0]]').format(GCS_WGS84, self.lon0, self.lat0)


class Albers:
    def __init__(self, lon0: float, lat0: float, lat1: float, lat2: float):
        self.lon0, self.lat0, self.lat1, self.lat2 = lon0, lat0, lat1, lat2
        p1, p2, p0 = map(math.radians, (lat1, lat2, lat0))
        self.n = (math.sin(p1) + math.sin(p2)) / 2
        self.C = math.cos(p1) ** 2 + 2 * self.n * math.sin(p1)
        self.rho0 = R * math.sqrt(self.C - 2 * self.n * math.sin(p0)) / self.n

    def fwd(self, lon: float, lat: float) -> Tuple[float, float]:
        th = self.n * math.radians(_wrap(lon - self.lon0))
        rho = R * math.sqrt(max(self.C - 2 * self.n * math.sin(math.radians(lat)), 0)) / self.n
        return rho * math.sin(th), self.rho0 - rho * math.cos(th)

    def inv(self, x: float, y: float) -> Tuple[float, float]:
        rho = math.hypot(x, self.rho0 - y)
        th = math.atan2(x, self.rho0 - y)
        s = (self.C - (rho * self.n / R) ** 2) / (2 * self.n)
        return _wrap(self.lon0 + math.degrees(th / self.n)), math.degrees(math.asin(max(-1.0, min(1.0, s))))

    def wkt(self) -> str:
        return ('PROJCS["slotmap_Albers_CONUS",{},PROJECTION["Albers"],PARAMETER["False_Easting",0.0],'
                'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",{}],'
                'PARAMETER["Standard_Parallel_1",{}],PARAMETER["Standard_Parallel_2",{}],'
                'PARAMETER["Latitude_Of_Origin",{}],UNIT["Meter",1.0]]').format(
                    GCS_WGS84, self.lon0, self.lat1, self.lat2, self.lat0)


class EqualEarth:
    A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
    M = math.sqrt(3) / 2

    def __init__(self, lon0: float = 0.0):
        self.lon0 = lon0

    def fwd(self, lon: float, lat: float) -> Tuple[float, float]:
        l = math.radians(_wrap(lon - self.lon0))
        th = math.asin(self.M * math.sin(math.radians(lat)))
        t2 = th * th
        t6 = t2 * t2 * t2
        d = 9 * self.A4 * t6 * t2 + 7 * self.A3 * t6 + 3 * self.A2 * t2 + self.A1
        x = R * 2 * math.sqrt(3) * l * math.cos(th) / (3 * d)
        y = R * th * (self.A4 * t6 * t2 + self.A3 * t6 + self.A2 * t2 + self.A1)
        return x, y

    def extent(self) -> Tuple[float, float, float, float]:
        x = self.fwd(self.lon0 + 179.999, 0)[0]
        y = self.fwd(0, 90)[1]
        return -x, x, -y, y

    def wkt(self) -> str:
        return ('PROJCS["slotmap_Equal_Earth",{},PROJECTION["Equal_Earth"],PARAMETER["False_Easting",0.0],'
                'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",{}],UNIT["Meter",1.0]]').format(
                    GCS_WGS84, self.lon0)


def _wrap(lon: float) -> float:
    return (lon + 180.0) % 360.0 - 180.0


def proj_for(kind: str, center: Tuple[float, float]):
    if kind == "equalearth":
        return EqualEarth(center[0])
    if kind == "albers_conus":
        return Albers(**CONUS)
    return LAEA(center[0], center[1])


def boundary_lonlat(proj, ext, n: int = 40) -> List[Tuple[float, float]]:
    """Frame outline (x0, x1, y0, y1 in projected metres) as a closed lon/lat ring."""
    x0, x1, y0, y1 = ext
    pts = []
    for i in range(n):
        pts.append((x0 + (x1 - x0) * i / n, y0))
    for i in range(n):
        pts.append((x1, y0 + (y1 - y0) * i / n))
    for i in range(n):
        pts.append((x1 - (x1 - x0) * i / n, y1))
    for i in range(n):
        pts.append((x0, y1 - (y1 - y0) * i / n))
    pts.append(pts[0])
    return [proj.inv(x, y) for x, y in pts]
