"""Stage 5 - decide which map frames the sheet needs, where they sit and what each shows (pure Python).

Same rules as the main branch: regions (CONUS / Alaska / Hawaii / territories / overseas country groups),
detail frames only where symbols collide at the parent's scale, one symbol scale for the whole sheet,
equal-area projections, honest displacement of anything still coincident.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import layout as L
from .aggregate import US_TERRITORIES
from .config import PlanConfig, SheetConfig
from .geo import CONUS_BOUNDS, LAEA, haversine_km, mean_lonlat, proj_for
from .gazetteer import COUNTRY_A3

COUNTRY_TITLE = {"KOREA": "South Korea", "USA": "United States", "UNITED ARAB EMIRATES": "United Arab Emirates"}
EUROPE = {"GERMANY", "ITALY", "BELGIUM", "POLAND", "KOSOVO", "UNITED KINGDOM", "SPAIN", "NETHERLANDS", "ROMANIA",
          "LITHUANIA", "ESTONIA", "LATVIA", "HUNGARY", "TURKEY"}
MIDDLE_EAST = {"KUWAIT", "QATAR", "BAHRAIN", "IRAQ", "SYRIA", "JORDAN", "SAUDI ARABIA", "UNITED ARAB EMIRATES"}
Station = Dict[str, object]


@dataclass
class Frame:
    fid: str
    kind: str                       # world | region | detail
    title: str
    parent: Optional[str] = None
    members: List[str] = field(default_factory=list)
    proj: str = "laea"              # equalearth | albers_conus | laea
    center: Tuple[float, float] = (0.0, 0.0)
    halfwidth_km: float = 60.0
    extent: Tuple[float, float, float, float] = (0, 0, 0, 0)      # x0, x1, y0, y1 (projected metres)
    slot: Tuple[float, float, float, float] = (0, 0, 0, 0)        # x, y, w, h on the page (inches, incl. header)
    letter: str = ""
    focus_a3: List[str] = field(default_factory=list)
    priority: float = 0.0
    depth: int = 1
    display: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def projection(self):
        return proj_for(self.proj, self.center)


@dataclass
class Plan:
    frames: List[Frame]
    symbol_k: float
    world_k: float
    page: Tuple[float, float]
    warnings: List[str] = field(default_factory=list)

    def by_id(self) -> Dict[str, Frame]:
        return {f.fid: f for f in self.frames}

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- helpers
def scale_m_per_pt(frame: Frame) -> float:
    x0, x1, _, _ = frame.extent
    return (x1 - x0) / (frame.slot[2] * 72.0)


def radius_pt(slots: float, k: float) -> float:
    return math.sqrt(max(slots, 0) * k / math.pi)


def fit_extent(frame: Frame, lons, lats, map_aspect: float, pad_frac: float, min_hw_km: float) -> None:
    lons, lats = list(lons), list(lats)
    if frame.proj == "albers_conus":
        x0, x1, y0, y1 = CONUS_BOUNDS
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - 40e3
        hw = max((x1 - x0) / 2 * 1.04, (y1 - y0) / 2 * 1.08 / map_aspect)
        frame.extent = (cx - hw, cx + hw, cy - hw * map_aspect, cy + hw * map_aspect)
        frame.halfwidth_km = hw / 1000
        return
    if frame.proj == "equalearth":
        return
    if "center" not in frame.notes:
        c = mean_lonlat(lons, lats)
        prov = LAEA(c[0], c[1])
        p = [prov.fwd(x, y) for x, y in zip(lons, lats)]
        bx = (min(q[0] for q in p) + max(q[0] for q in p)) / 2
        by = (min(q[1] for q in p) + max(q[1] for q in p)) / 2
        c = prov.inv(bx, by)
        frame.center = (round(c[0], 5), round(c[1], 5))
    proj = frame.projection()
    p = [proj.fwd(x, y) for x, y in zip(lons, lats)]
    xs, ys = [q[0] for q in p], [q[1] for q in p]
    sx = (max(xs) - min(xs)) if len(p) > 1 else 0.0
    sy = (max(ys) - min(ys)) if len(p) > 1 else 0.0
    need_x = max(max(abs(v) for v in xs) if p else 0, sx / 2) * (1 + 2 * pad_frac)
    need_y = max(max(abs(v) for v in ys) if p else 0, sy / 2) * (1 + 2 * pad_frac)
    hw = max(need_x, need_y / map_aspect, min_hw_km * 1000)
    if "halfwidth" in frame.notes:
        hw = frame.halfwidth_km * 1000
    frame.halfwidth_km = round(hw / 1000, 3)
    frame.extent = (-hw, hw, -hw * map_aspect, hw * map_aspect)


# --------------------------------------------------------------------------- 5.1 regions
def region_groups(st: List[Station], pc: PlanConfig) -> List[Frame]:
    frames = []  # type: List[Frame]
    us = [s for s in st if s["COUNTRY_C"] == "USA"]
    for th, fid, title, proj in [("CONUS", "conus", "Continental United States", "albers_conus"),
                                 ("Alaska", "alaska", "Alaska", "laea"), ("Hawaii", "hawaii", "Hawaii", "laea")]:
        m = [s["GID"] for s in us if s["THEATER"] == th]
        if m:
            frames.append(Frame(fid, "region", title, "world", m, proj, focus_a3=["USA"]))
    for code, name in US_TERRITORIES.items():
        m = [s["GID"] for s in us if s["POSTAL"] == code]
        if m:
            frames.append(Frame(code.lower(), "region", name, "world", m, "laea", focus_a3=["USA"]))
    ov = [s for s in st if s["COUNTRY_C"] != "USA"]
    countries = list(dict.fromkeys(s["COUNTRY_C"] for s in ov))
    parent = {c: c for c in countries}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for i, a in enumerate(countries):
        A = [s for s in ov if s["COUNTRY_C"] == a]
        for b in countries[i + 1:]:
            B = [s for s in ov if s["COUNTRY_C"] == b]
            d = min(haversine_km(x["LON"], x["LAT"], y["LON"], y["LAT"]) for x in A for y in B)
            if d <= pc.region_merge_km:
                parent[find(a)] = find(b)
    groups = {}  # type: Dict[str, List[str]]
    for c in countries:
        groups.setdefault(find(c), []).append(c)
    for _, cs in groups.items():
        m = [s for s in ov if s["COUNTRY_C"] in cs]
        cs = sorted(cs, key=lambda c: -sum(s["SLOTS"] for s in m if s["COUNTRY_C"] == c))
        names = [COUNTRY_TITLE.get(c, c.title()) for c in cs]
        if len(names) <= 2:
            title = " & ".join(names)
        elif set(cs) <= EUROPE:
            title = "Europe"
        elif set(cs) <= MIDDLE_EAST:
            title = "Middle East"
        else:
            title = "{} & neighbours".format(names[0])
        fid = "-".join(c.lower().replace(" ", "_") for c in cs)
        frames.append(Frame(fid, "region", title, "world", [s["GID"] for s in m], "laea",
                            focus_a3=[COUNTRY_A3.get(c, "") for c in cs]))
    return frames


# --------------------------------------------------------------------------- 5.3/5.4 collisions
def _positions_pt(frame: Frame, gidx: Dict[str, Station], gids: List[str]) -> List[Tuple[float, float]]:
    proj = frame.projection()
    s = scale_m_per_pt(frame)
    out = []
    for g in gids:
        x, y = proj.fwd(float(gidx[g]["LON"]), float(gidx[g]["LAT"]))
        out.append((x / s, y / s))
    return out


def find_clusters(frame: Frame, gidx: Dict[str, Station], k: float, gap_pt: float,
                  merge_pt: float = 30.0) -> List[List[str]]:
    gids = list(frame.members)
    if len(gids) < 2:
        return []
    pos = _positions_pt(frame, gidx, gids)
    r = [radius_pt(gidx[g]["SLOTS"], k) for g in gids]
    n = len(gids)
    gap = [[math.inf] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            gap[i][j] = gap[j][i] = math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]) - r[i] - r[j]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def groups():
        g = {}  # type: Dict[int, List[int]]
        for i in range(n):
            g.setdefault(find(i), []).append(i)
        return [v for v in g.values() if len(v) > 1]

    for i in range(n):
        for j in range(i + 1, n):
            if gap[i][j] < gap_pt:
                parent[find(i)] = find(j)
    changed = True
    while changed:
        changed = False
        gs = groups()
        for a in range(len(gs)):
            for b in range(a + 1, len(gs)):
                if min(gap[i][j] for i in gs[a] for j in gs[b]) < merge_pt and find(gs[a][0]) != find(gs[b][0]):
                    parent[find(gs[a][0])] = find(gs[b][0])
                    changed = True
    for g in groups():
        lox, hix = min(pos[i][0] for i in g), max(pos[i][0] for i in g)
        loy, hiy = min(pos[i][1] for i in g), max(pos[i][1] for i in g)
        for i in range(n):
            if find(i) != find(g[0]) and lox <= pos[i][0] <= hix and loy <= pos[i][1] <= hiy:
                parent[find(i)] = find(g[0])
    return [[gids[i] for i in g] for g in groups()]


def _home_place(lon: float, lat: float, pl: List[Dict[str, object]], radius_km: float = 80.0):
    best = None
    for c in pl:
        if abs(c["longitude"] - lon) > 1.2 or abs(c["latitude"] - lat) > 0.8:
            continue
        d = haversine_km(lon, lat, c["longitude"], c["latitude"])
        if d > radius_km:
            continue
        score = d - 8.0 * math.log10(float(c["pop_max"]))
        if best is None or score < best[0]:
            best = (score, c)
    if best is None:
        return None
    c = best[1]
    return re.sub(r",\s*D\.?\s*C\.?$", "", str(c["name"]).strip()), float(c["longitude"]), float(c["latitude"])


def name_detail(gids: List[str], gidx: Dict[str, Station], pl: List[Dict[str, object]]) -> str:
    sub = sorted((gidx[g] for g in gids), key=lambda s: -s["SLOTS"])
    if not pl:
        return "{} area".format(sub[0]["NAME"])
    homes = {}  # type: Dict[str, List[float]]
    order = []  # type: List[str]
    for s in sub:
        h = _home_place(float(s["LON"]), float(s["LAT"]), pl)
        key, hx, hy = h if h else (s["NAME"], float(s["LON"]), float(s["LAT"]))
        if key not in homes:
            homes[key] = [0.0, hx, hy]
            order.append(key)
        homes[key][0] += max(float(s["SLOTS"]), 0.1)
    ranked = sorted(order, key=lambda k: -homes[k][0])
    first = ranked[0]
    fx, fy = homes[first][1], homes[first][2]
    far = [n for n in ranked[1:] if haversine_km(fx, fy, homes[n][1], homes[n][2]) > 60]
    return "{} & {}".format(first, far[0]) if far else "{} area".format(first)


# --------------------------------------------------------------------------- 5.7 displacement
def displace_coincident(frame: Frame, gidx: Dict[str, Station], k: float, gap_pt: float) -> List[str]:
    gids = list(frame.members)
    if len(gids) < 2:
        return []
    proj = frame.projection()
    p = [list(proj.fwd(float(gidx[g]["LON"]), float(gidx[g]["LAT"]))) for g in gids]
    s = scale_m_per_pt(frame)
    r = [radius_pt(gidx[g]["SLOTS"], k) * s for g in gids]
    moved = set()
    for _ in range(60):
        any_move = False
        for i in range(len(gids)):
            for j in range(i + 1, len(gids)):
                vx, vy = p[j][0] - p[i][0], p[j][1] - p[i][1]
                d = math.hypot(vx, vy)
                need = r[i] + r[j] + gap_pt * s
                if d < need:
                    ux, uy = (vx / d, vy / d) if d > 1e-6 else (1.0, 0.0)
                    push = (need - d) / 2 + 1e-6
                    p[i][0] -= ux * push; p[i][1] -= uy * push
                    p[j][0] += ux * push; p[j][1] += uy * push
                    moved.update([gids[i], gids[j]])
                    any_move = True
        if not any_move:
            break
    if not moved:
        return []
    for idx, g in enumerate(gids):
        if g in moved:
            frame.display[g] = proj.inv(p[idx][0], p[idx][1])
    names = [gidx[g]["NAME"] for g in gids if g in moved]
    km = max(haversine_km(gidx[g]["LON"], gidx[g]["LAT"], *frame.display[g]) for g in moved)
    return ["{} symbols offset up to {:.1f} km for legibility".format(" and ".join(names), km)]


# --------------------------------------------------------------------------- 5.8 driver
def symbol_scales(st: List[Station], theaters: List[Dict[str, object]], cfg: SheetConfig) -> Tuple[float, float]:
    mx = max(max(int(s["SLOTS"]) for s in st), 1)
    k = math.pi * (cfg.symbol_max_diameter_pt / 2) ** 2 / mx
    tmax = max(max(int(t["SLOTS"]) for t in theaters), 1)
    kw = min(k, math.pi * (cfg.world_max_diameter_pt / 2) ** 2 / tmax)
    return k, kw


def _apply_overrides(f: Frame, cfg: SheetConfig) -> None:
    o = cfg.frame_overrides.get(f.fid) or cfg.frame_overrides.get(f.title)
    if not o:
        return
    if "title" in o:
        f.title = o["title"]
    if "center" in o:
        f.center = tuple(o["center"])
        f.notes.append("center")
    if "halfwidth_km" in o:
        f.halfwidth_km = float(o["halfwidth_km"])
        f.notes.append("halfwidth")


def _world_center(st: List[Station]) -> float:
    lons = [((float(s["LON"]) + 180) % 360) - 180 for s in st]
    for c in (-10.0, 150.0, 60.0, -100.0):
        if max(abs(((lo - c + 180) % 360) - 180) for lo in lons) <= 165:
            return c
    return -10.0


def plan_sheet(st: List[Station], theaters: List[Dict[str, object]], cfg: SheetConfig,
               places: Optional[List[Dict[str, object]]] = None) -> Plan:
    pc = cfg.plan
    k, kw = symbol_scales(st, theaters, cfg)
    gidx = {s["GID"]: s for s in st}

    def prio(f: Frame, conus_bonus: bool = True) -> float:
        return len(f.members) + (100 if f.fid == "conus" and conus_bonus else 0) + \
            0.001 * sum(gidx[g]["SLOTS"] for g in f.members)

    regions = region_groups(st, pc)
    for f in regions:
        f.priority = prio(f)
    warnings = []  # type: List[str]
    merge_km = pc.region_merge_km
    cap = min(pc.max_frames, L.capacity() + 1)
    while len(regions) + 1 > cap and merge_km < 20000:
        merge_km *= 1.6
        pc2 = PlanConfig(**dict(asdict(pc), region_merge_km=merge_km))
        regions = region_groups(st, pc2)
        for f in regions:
            f.priority = len(f.members) + (100 if f.fid == "conus" else 0)
        warnings.append("Overseas countries merged at {:.0f} km to fit the page.".format(merge_km))
    frames = list(regions)
    blocked = set()
    limit = cap - 1

    def layout_and_fit():
        slots = L.assign([dict(fid=f.fid, priority=f.priority, kind=f.kind) for f in frames], cfg.page)
        for f in frames:
            s = slots[f.fid]
            f.slot = s.as_tuple()
            _apply_overrides(f, cfg)
            if len(f.members) == 1:
                min_hw = pc.single_halfwidth_km
            else:
                min_hw = pc.min_halfwidth_km if f.kind == "detail" else pc.region_min_halfwidth_km
            fit_extent(f, [gidx[g]["LON"] for g in f.members], [gidx[g]["LAT"] for g in f.members],
                       s.map_aspect, pc.pad_frac, min_hw)
        return slots

    for _ in range(8):
        layout_and_fit()
        new = []  # type: List[Frame]
        for f in frames:
            if f.depth >= pc.max_depth:
                continue
            for cl in find_clusters(f, gidx, k, pc.cluster_gap_pt, pc.cluster_merge_pt):
                key = frozenset(cl)
                if key in blocked or len(cl) == len(f.members):
                    continue
                child = Frame("{}/{}".format(f.fid, cl[0]), "detail", name_detail(cl, gidx, places or []), f.fid,
                              list(cl), "laea", focus_a3=f.focus_a3, depth=f.depth + 1)
                child.priority = len(cl) + 0.001 * sum(gidx[g]["SLOTS"] for g in cl)
                new.append(child)
        if not new:
            break
        room = limit - len(frames)
        new.sort(key=lambda c: -c.priority)
        for c in new[max(room, 0):]:
            blocked.add(frozenset(c.members))
            warnings.append("No room for a detail frame for {}; symbols are displaced in the parent frame "
                            "instead.".format(", ".join(gidx[g]["NAME"] for g in c.members)))
        for c in new[:max(room, 0)]:
            par = next(f for f in frames if f.fid == c.parent)
            par.members = [g for g in par.members if g not in c.members]
            frames.append(c)
    slots = layout_and_fit()
    letters = L.letters_in_reading_order(slots)
    for f in frames:
        f.letter = letters[f.fid]
    world = Frame("world", "world", "World — {} by theater".format(cfg.value_label), None, [], "equalearth",
                  (_world_center(st), 0.0), slot=slots["world"].as_tuple(), letter="W", depth=0)
    world.extent = world.projection().extent()
    for f in frames:
        f.notes = [n for n in f.notes if n not in ("center", "halfwidth")]
        f.notes.extend(displace_coincident(f, gidx, k, pc.displace_gap_pt))
    frames.sort(key=lambda f: f.letter)
    return Plan([world] + frames, k, kw, (cfg.page.width_in, cfg.page.height_in), warnings)
