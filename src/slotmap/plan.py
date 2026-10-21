"""Stage 5 - decide which map frames the sheet needs, where they sit, and what each one shows."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cartopy.crs as ccrs
import numpy as np
import pandas as pd

from . import layout as L
from .aggregate import US_TERRITORIES, country_display
from .config import SheetConfig
from .gazetteer import COUNTRY_A3

GEO = ccrs.PlateCarree()
CONUS_PROJ = dict(central_longitude=-96, central_latitude=37.5, standard_parallels=(29.5, 45.5))
CONUS_BOUNDS = (-2356105.0, 2256152.0, -1334000.0, 1564784.0)   # lower-48 in the Albers above
COUNTRY_TITLE = {"KOREA": "South Korea", "USA": "United States", "UNITED ARAB EMIRATES": "United Arab Emirates"}
EARTH_KM_PER_DEG = 111.32
EUROPE = {"GERMANY", "ITALY", "BELGIUM", "POLAND", "KOSOVO", "UNITED KINGDOM", "SPAIN", "NETHERLANDS", "ROMANIA",
          "LITHUANIA", "ESTONIA", "LATVIA", "HUNGARY", "TURKEY"}
MIDDLE_EAST = {"KUWAIT", "QATAR", "BAHRAIN", "IRAQ", "SYRIA", "JORDAN", "SAUDI ARABIA", "UNITED ARAB EMIRATES"}


@dataclass
class Frame:
    fid: str
    kind: str                       # world | region | detail
    title: str
    parent: Optional[str] = None
    members: List[str] = field(default_factory=list)   # GIDs labelled in this frame
    proj: str = "laea"              # equalearth | albers_conus | laea
    center: Tuple[float, float] = (0.0, 0.0)
    halfwidth_km: float = 60.0
    extent: Tuple[float, float, float, float] = (0, 0, 0, 0)
    slot: Tuple[float, float, float, float] = (0, 0, 0, 0)
    letter: str = ""
    focus_a3: List[str] = field(default_factory=list)
    priority: float = 0.0
    depth: int = 1
    display: Dict[str, Tuple[float, float]] = field(default_factory=dict)   # displaced lon/lat per GID
    notes: List[str] = field(default_factory=list)


@dataclass
class Plan:
    frames: List[Frame]
    symbol_k: float                 # pt² per slot
    world_k: float
    page: Tuple[float, float]
    warnings: List[str] = field(default_factory=list)

    def by_id(self) -> Dict[str, Frame]:
        return {f.fid: f for f in self.frames}

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Plan":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        fr = []
        for f in raw["frames"]:
            f["center"] = tuple(f["center"]); f["extent"] = tuple(f["extent"]); f["slot"] = tuple(f["slot"])
            f["display"] = {k: tuple(v) for k, v in f["display"].items()}
            fr.append(Frame(**f))
        raw["frames"] = fr
        raw["page"] = tuple(raw["page"])
        return cls(**raw)


# ---------------------------------------------------------------------------- geometry helpers
def crs_for(frame: Frame):
    if frame.proj == "equalearth":
        return ccrs.EqualEarth(central_longitude=frame.center[0])
    if frame.proj == "albers_conus":
        return ccrs.AlbersEqualArea(**CONUS_PROJ)
    return ccrs.LambertAzimuthalEqualArea(central_longitude=frame.center[0], central_latitude=frame.center[1])


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def mean_lonlat(lons, lats, w=None) -> Tuple[float, float]:
    """Spherical mean (safe across the antimeridian)."""
    lons, lats = np.radians(np.asarray(lons, float)), np.radians(np.asarray(lats, float))
    w = np.ones_like(lons) if w is None else np.asarray(w, float) + 1e-9
    x = np.sum(w * np.cos(lats) * np.cos(lons)); y = np.sum(w * np.cos(lats) * np.sin(lons)); z = np.sum(w * np.sin(lats))
    return float(np.degrees(np.arctan2(y, x))), float(np.degrees(np.arctan2(z, math.hypot(x, y))))


def fit_extent(frame: Frame, lons, lats, map_aspect: float, pad_frac: float, min_hw_km: float) -> None:
    """5.2 Centre on the members' bbox (in a provisional LAEA) and size to hold them with padding."""
    if frame.proj == "albers_conus":
        x0, x1, y0, y1 = CONUS_BOUNDS
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - 40e3
        hw = max((x1 - x0) / 2 * 1.04, (y1 - y0) / 2 * 1.08 / map_aspect)
        frame.extent = (cx - hw, cx + hw, cy - hw * map_aspect, cy + hw * map_aspect)
        frame.halfwidth_km = hw / 1000
        return
    if frame.proj == "equalearth":
        return
    if "center" in frame.notes:          # overridden
        c = frame.center
    else:
        c = mean_lonlat(lons, lats)
        prov = ccrs.LambertAzimuthalEqualArea(central_longitude=c[0], central_latitude=c[1])
        p = prov.transform_points(GEO, np.asarray(lons, float), np.asarray(lats, float))
        bx = (p[:, 0].min() + p[:, 0].max()) / 2; by = (p[:, 1].min() + p[:, 1].max()) / 2
        c = tuple(GEO.transform_point(bx, by, prov))
        frame.center = (round(c[0], 5), round(c[1], 5))
    proj = crs_for(frame)
    p = proj.transform_points(GEO, np.asarray(lons, float), np.asarray(lats, float))
    sx = (p[:, 0].max() - p[:, 0].min()) if len(p) > 1 else 0.0
    sy = (p[:, 1].max() - p[:, 1].min()) if len(p) > 1 else 0.0
    need_x = max(np.abs(p[:, 0]).max() if len(p) else 0, sx / 2) * (1 + 2 * pad_frac)
    need_y = max(np.abs(p[:, 1]).max() if len(p) else 0, sy / 2) * (1 + 2 * pad_frac)
    hw = max(need_x, need_y / map_aspect, min_hw_km * 1000)
    if "halfwidth" in frame.notes:
        hw = frame.halfwidth_km * 1000
    frame.halfwidth_km = round(hw / 1000, 3)
    frame.extent = (-hw, hw, -hw * map_aspect, hw * map_aspect)


def scale_m_per_pt(frame: Frame) -> float:
    x0, x1, _, _ = frame.extent
    return (x1 - x0) / (frame.slot[2] * 72.0)


# ---------------------------------------------------------------------------- 5.1 regions
def region_groups(st: pd.DataFrame, cfg: SheetConfig) -> List[Frame]:
    frames: List[Frame] = []
    us = st[st.COUNTRY_C == "USA"]
    for th, fid, title, proj in [("CONUS", "conus", "Continental United States", "albers_conus"),
                                 ("Alaska", "alaska", "Alaska", "laea"), ("Hawaii", "hawaii", "Hawaii", "laea")]:
        m = us[us.THEATER == th]
        if len(m):
            frames.append(Frame(fid, "region", title, "world", list(m.GID), proj, focus_a3=["USA"]))
    for code, name in US_TERRITORIES.items():
        m = us[us.POSTAL == code]
        if len(m):
            frames.append(Frame(code.lower(), "region", name, "world", list(m.GID), "laea", focus_a3=["USA"]))
    # overseas: single-linkage of countries by nearest-station distance
    ov = st[st.COUNTRY_C != "USA"]
    countries = list(dict.fromkeys(ov.COUNTRY_C))
    parent = {c: c for c in countries}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for i, a in enumerate(countries):
        for b in countries[i + 1:]:
            A, B = ov[ov.COUNTRY_C == a], ov[ov.COUNTRY_C == b]
            d = min(haversine_km(x1, y1, x2, y2) for x1, y1 in zip(A.LON, A.LAT) for x2, y2 in zip(B.LON, B.LAT))
            if d <= cfg.plan.region_merge_km:
                parent[find(a)] = find(b)
    groups: Dict[str, List[str]] = {}
    for c in countries:
        groups.setdefault(find(c), []).append(c)
    for root, cs in groups.items():
        m = ov[ov.COUNTRY_C.isin(cs)]
        cs = sorted(cs, key=lambda c: -m[m.COUNTRY_C == c].SLOTS.sum())
        names = [COUNTRY_TITLE.get(c, c.title()) for c in cs]
        if len(names) <= 2:
            title = " & ".join(names)
        elif set(cs) <= EUROPE:
            title = "Europe"
        elif set(cs) <= MIDDLE_EAST:
            title = "Middle East"
        else:
            title = f"{names[0]} & neighbours"
        fid = "-".join(c.lower().replace(" ", "_") for c in cs)
        frames.append(Frame(fid, "region", title, "world", list(m.GID), "laea",
                            focus_a3=[COUNTRY_A3.get(c, "") for c in cs]))
    return frames


# ---------------------------------------------------------------------------- 5.3/5.4 collisions
def _positions_pt(frame: Frame, st: pd.DataFrame, gids: List[str]) -> np.ndarray:
    proj = crs_for(frame)
    sub = st.set_index("GID").loc[gids]
    p = proj.transform_points(GEO, sub.LON.values.astype(float), sub.LAT.values.astype(float))[:, :2]
    return p / scale_m_per_pt(frame)


def radius_pt(slots: float, k: float) -> float:
    return math.sqrt(max(slots, 0) * k / math.pi)


def find_clusters(frame: Frame, st: pd.DataFrame, k: float, gap_pt: float, merge_pt: float = 30.0) -> List[List[str]]:
    """5.3 + 5.4: groups of stations whose symbols collide at this frame's scale.

    Pass 1 unions colliding pairs. Pass 2 merges whole clusters that sit within `merge_pt` of each other
    (so Wiesbaden and Kaiserslautern share one detail frame instead of two). Pass 3 pulls in any other
    member that falls inside a cluster's bounding box.
    """
    gids = list(frame.members)
    if len(gids) < 2:
        return []
    pos = _positions_pt(frame, st, gids)
    slots = st.set_index("GID").loc[gids].SLOTS.values
    r = np.array([radius_pt(s, k) for s in slots])
    n = len(gids)
    gap = np.full((n, n), np.inf)
    for i in range(n):
        for j in range(i + 1, n):
            gap[i, j] = gap[j, i] = float(np.hypot(*(pos[i] - pos[j]))) - r[i] - r[j]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def groups():
        g: Dict[int, List[int]] = {}
        for i in range(n):
            g.setdefault(find(i), []).append(i)
        return [v for v in g.values() if len(v) > 1]

    for i in range(n):
        for j in range(i + 1, n):
            if gap[i, j] < gap_pt:
                parent[find(i)] = find(j)
    changed = True
    while changed:
        changed = False
        gs = groups()
        for a in range(len(gs)):
            for b in range(a + 1, len(gs)):
                if min(gap[i, j] for i in gs[a] for j in gs[b]) < merge_pt and find(gs[a][0]) != find(gs[b][0]):
                    parent[find(gs[a][0])] = find(gs[b][0]); changed = True
    for g in groups():
        lo, hi = pos[g].min(axis=0), pos[g].max(axis=0)
        for i in range(n):
            if find(i) != find(g[0]) and np.all(pos[i] >= lo) and np.all(pos[i] <= hi):
                parent[find(i)] = find(g[0])
    return [[gids[i] for i in g] for g in groups()]


def _home_place(lon: float, lat: float, pl: pd.DataFrame, radius_km: float = 80.0):
    """The place a station 'belongs to': near, and big. Returns (name, lon, lat) or None."""
    cand = pl[(pl.longitude.between(lon - 1.2, lon + 1.2)) & (pl.latitude.between(lat - 0.8, lat + 0.8))]
    cand = cand[cand.pop_max >= 50000]
    if not len(cand):
        return None
    d = np.array([haversine_km(lon, lat, x, y) for x, y in zip(cand.longitude, cand.latitude)])
    ok = d <= radius_km
    if not ok.any():
        return None
    cand, d = cand[ok], d[ok]
    score = d - 8.0 * np.log10(cand.pop_max.astype(float).values)   # km, traded against size
    r = cand.iloc[int(np.argmin(score))]
    return re.sub(r",\s*D\.?\s*C\.?$", "", str(r["name"]).strip()), float(r.longitude), float(r.latitude)


def name_detail(gids: List[str], st: pd.DataFrame, basemap) -> str:
    """Name a detail frame after the town(s) its stations belong to, weighted by slots.

    One town holds the frame -> "Savannah area". Towns far apart (> 60 km) share it -> "Wiesbaden & Stuttgart".
    """
    sub = st.set_index("GID").loc[gids]
    top_station = sub.sort_values("SLOTS", ascending=False).NAME.iloc[0]
    pl = basemap.get("places") if basemap is not None else None
    if pl is None or not len(pl):
        return f"{top_station} area"
    homes: Dict[str, List[float]] = {}
    for r in sub.itertuples():
        h = _home_place(float(r.LON), float(r.LAT), pl)
        key, hx, hy = h if h else (r.NAME, float(r.LON), float(r.LAT))
        e = homes.setdefault(key, [0.0, hx, hy])
        e[0] += max(float(r.SLOTS), 0.1)
    ranked = sorted(homes.items(), key=lambda kv: -kv[1][0])
    first, (_, fx, fy) = ranked[0]
    far = [n for n, (_, x, y) in ranked[1:] if haversine_km(fx, fy, x, y) > 60]
    if far:
        return f"{first} & {far[0]}"
    return f"{first} area"


# ---------------------------------------------------------------------------- 5.7 displacement
def displace_coincident(frame: Frame, st: pd.DataFrame, k: float, gap_pt: float) -> List[str]:
    """Push apart symbols in this frame that overlap at its final scale. Records display positions."""
    gids = [g for g in frame.members]
    if len(gids) < 2:
        return []
    proj = crs_for(frame)
    sub = st.set_index("GID").loc[gids]
    p = proj.transform_points(GEO, sub.LON.values.astype(float), sub.LAT.values.astype(float))[:, :2].copy()
    s = scale_m_per_pt(frame)
    r = np.array([radius_pt(v, k) for v in sub.SLOTS.values]) * s
    moved = set()
    for _ in range(60):
        any_move = False
        for i in range(len(gids)):
            for j in range(i + 1, len(gids)):
                v = p[j] - p[i]
                d = float(np.hypot(*v))
                need = r[i] + r[j] + gap_pt * s
                if d < need:
                    u = v / d if d > 1e-6 else np.array([1.0, 0.0])
                    push = (need - d) / 2 + 1e-6
                    p[i] -= u * push; p[j] += u * push
                    moved.update([gids[i], gids[j]]); any_move = True
        if not any_move:
            break
    notes = []
    if moved:
        ll = GEO.transform_points(proj, p[:, 0], p[:, 1])
        for idx, g in enumerate(gids):
            if g in moved:
                frame.display[g] = (float(ll[idx, 0]), float(ll[idx, 1]))
        names = sub.loc[list(moved)].NAME.tolist()
        km = max(haversine_km(sub.loc[g].LON, sub.loc[g].LAT, *frame.display[g]) for g in moved)
        notes.append(f"{' and '.join(names)} symbols offset up to {km:.1f} km for legibility")
    return notes


# ---------------------------------------------------------------------------- 5.8 driver
def symbol_scales(st: pd.DataFrame, theaters: pd.DataFrame, cfg: SheetConfig) -> Tuple[float, float]:
    mx = max(int(st.SLOTS.max()), 1)
    k = math.pi * (cfg.symbol_max_diameter_pt / 2) ** 2 / mx
    tmax = max(int(theaters.SLOTS.max()), 1)
    kw = min(k, math.pi * (cfg.world_max_diameter_pt / 2) ** 2 / tmax)
    return k, kw


def _apply_overrides(f: Frame, cfg: SheetConfig) -> None:
    o = cfg.frame_overrides.get(f.fid) or cfg.frame_overrides.get(f.title)
    if not o:
        return
    if "title" in o:
        f.title = o["title"]
    if "center" in o:
        f.center = tuple(o["center"]); f.notes.append("center")
    if "halfwidth_km" in o:
        f.halfwidth_km = float(o["halfwidth_km"]); f.notes.append("halfwidth")


def plan_sheet(st: pd.DataFrame, theaters: pd.DataFrame, cfg: SheetConfig, basemap=None) -> Plan:
    """Iterate regions -> layout -> extents -> collisions -> detail frames until stable."""
    pc = cfg.plan
    k, kw = symbol_scales(st, theaters, cfg)
    regions = region_groups(st, cfg)
    for f in regions:
        f.priority = len(f.members) + (100 if f.fid == "conus" else 0) + 0.001 * st[st.GID.isin(f.members)].SLOTS.sum()
    warnings: List[str] = []
    # too many regions for the page: merge overseas regions with a wider radius
    merge_km = pc.region_merge_km
    while len(regions) + 1 > min(pc.max_frames, L.capacity() + 1) and merge_km < 20000:
        merge_km *= 1.6
        cfg2 = SheetConfig.from_dict({**{}, "plan": {**asdict(pc), "region_merge_km": merge_km}})
        regions = region_groups(st, cfg2)
        for f in regions:
            f.priority = len(f.members) + (100 if f.fid == "conus" else 0)
        warnings.append(f"Overseas countries merged at {merge_km:.0f} km to fit the page.")
    frames = list(regions)
    blocked: set = set()     # clusters we decided to displace instead of zoom
    gidx = st.set_index("GID")
    limit = min(pc.max_frames, L.capacity() + 1) - 1      # non-world frames

    def layout_and_fit() -> Dict[str, L.Slot]:
        slots = L.assign([dict(fid=f.fid, priority=f.priority, kind=f.kind) for f in frames], cfg.page)
        for f in frames:
            s = slots[f.fid]
            f.slot = s.as_tuple()
            _apply_overrides(f, cfg)
            m = gidx.loc[f.members]
            if len(f.members) == 1:
                min_hw = pc.single_halfwidth_km
            else:
                min_hw = pc.min_halfwidth_km if f.kind == "detail" else pc.region_min_halfwidth_km
            fit_extent(f, m.LON.values, m.LAT.values, s.map_aspect, pc.pad_frac, min_hw)
        return slots

    for _ in range(8):
        layout_and_fit()
        new: List[Frame] = []
        for f in frames:
            if f.depth >= pc.max_depth:
                continue
            for cl in find_clusters(f, st, k, pc.cluster_gap_pt, pc.cluster_merge_pt):
                key = frozenset(cl)
                if key in blocked or len(cl) == len(f.members):
                    continue
                child = Frame(f"{f.fid}/{cl[0]}", "detail", name_detail(cl, st, basemap), f.fid, list(cl), "laea",
                              focus_a3=f.focus_a3, depth=f.depth + 1)
                child.priority = len(cl) + 0.001 * gidx.loc[cl].SLOTS.sum()
                new.append(child)
        if not new:
            break
        room = limit - len(frames)
        new.sort(key=lambda c: -c.priority)
        for c in new[max(room, 0):]:
            blocked.add(frozenset(c.members))
            warnings.append(f"No room for a detail frame for {', '.join(gidx.loc[c.members].NAME)}; "
                            "symbols are displaced in the parent frame instead.")
        for c in new[:max(room, 0)]:
            par = next(f for f in frames if f.fid == c.parent)
            par.members = [g for g in par.members if g not in c.members]
            frames.append(c)
    slots = layout_and_fit()

    letters = L.letters_in_reading_order(slots)
    for f in frames:
        f.letter = letters[f.fid]
    world = Frame("world", "world", f"World — {cfg.value_label} by theater", None, [], "equalearth",
                  (_world_center(st), 0.0), slot=slots["world"].as_tuple(), letter="W", depth=0)
    # displacement at the final scale (anything still overlapping, e.g. two names for one installation)
    for f in frames:
        f.notes = [n for n in f.notes if n not in ("center", "halfwidth")]
        f.notes.extend(displace_coincident(f, st, k, pc.displace_gap_pt))
    frames.sort(key=lambda f: f.letter)
    return Plan([world] + frames, k, kw, (cfg.page.width_in, cfg.page.height_in), warnings)


def _world_center(st: pd.DataFrame) -> float:
    """Central meridian for the world frame: the usual Atlantic-centred view when every station stays
    clear of the edge, otherwise Pacific- or Indian-Ocean-centred."""
    lons = np.mod(st.LON.values.astype(float) + 180, 360) - 180
    for c in (-10.0, 150.0, 60.0, -100.0):
        off = np.abs(np.mod(lons - c + 180, 360) - 180)
        if off.max() <= 165:
            return c
    return -10.0
