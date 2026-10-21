"""Stage 6 - draw the map sheet from a plan."""
from __future__ import annotations

import datetime as _dt
import math
import textwrap
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import cartopy.crs as ccrs
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

from .config import SheetConfig  # noqa: E402
from .labels import HALO, LabelPlacer  # noqa: E402
from .layout import HEADER_IN  # noqa: E402
from .plan import CONUS_PROJ, GEO, Frame, Plan, crs_for  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)

INK, MUTED, FAINT = "#1f2328", "#5f636b", "#8d8f94"
WATER, LAND, LAND_OTHER = "#d6e2ea", "#ecebe6", "#f5f4f0"
BORDER, ADMIN1, URBAN = "#9e9b92", "#c4c1b8", "#dedad0"
EDGE = "#2b1d14"


@dataclass
class SheetMeta:
    total: int
    source_name: str
    excluded: List[str] = field(default_factory=list)       # names of zero-slot stations not mapped
    merged: List[str] = field(default_factory=list)         # names combined from duplicate rows
    fuzzy: List[str] = field(default_factory=list)          # "input -> matched" strings
    dropped: List[str] = field(default_factory=list)        # unresolved and dropped
    towns: List[str] = field(default_factory=list)          # "input -> town" placed at a town centre
    approx_coords: bool = True


# --------------------------------------------------------------------------- fonts
def setup_fonts(families: List[str]) -> str:
    for d in ("/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/inter"):
        p = Path(d)
        if p.exists():
            for f in list(p.glob("*.otf")) + list(p.glob("*.ttf")):
                try:
                    font_manager.fontManager.addfont(str(f))
                except Exception:
                    pass
    have = {f.name for f in font_manager.fontManager.ttflist}
    fam = next((f for f in families if f in have), "DejaVu Sans")
    plt.rcParams.update({"font.family": fam, "pdf.fonttype": 42, "svg.fonttype": "none"})
    return fam


def unit(n: int, label: str) -> str:
    if n == 1 and label.endswith("s"):
        return label[:-1]
    return label


def nice(v: float) -> float:
    e = 10 ** math.floor(math.log10(v)); m = v / e
    return (1 if m < 2 else 2 if m < 5 else 5) * e


# --------------------------------------------------------------------------- frame pieces
def boundary_lonlat(proj, ext, n=60):
    x0, x1, y0, y1 = ext
    xs = np.r_[np.linspace(x0, x1, n), np.full(n, x1), np.linspace(x1, x0, n), np.full(n, x0)]
    ys = np.r_[np.full(n, y0), np.linspace(y0, y1, n), np.full(n, y1), np.linspace(y1, y0, n)]
    p = GEO.transform_points(proj, xs, ys)
    return p[:, 0], p[:, 1]


def draw_basemap(ax, frame: Frame, proj, bm) -> None:
    """6.2"""
    ax.set_facecolor(WATER)
    if bm is None:
        return
    lon, lat = boundary_lonlat(proj, frame.extent)
    local = frame.kind == "detail" or frame.halfwidth_km < 150
    c = bm.clip("countries", lon, lat)
    focus = [a for a in frame.focus_a3 if a]
    if focus:
        ax.add_geometries(c[~c.ADM0_A3.isin(focus)].geometry, GEO, fc=LAND_OTHER, ec="none", zorder=1)
        ax.add_geometries(c[c.ADM0_A3.isin(focus)].geometry, GEO, fc=LAND, ec="none", zorder=1)
    else:
        ax.add_geometries(c.geometry, GEO, fc=LAND, ec="none", zorder=1)
    if local:
        ax.add_geometries(bm.clip("urban", lon, lat, 0.2).geometry, GEO, fc=URBAN, ec="none", zorder=1.2)
        lakes = bm.clip("lakes", lon, lat, 0.2)
    else:
        lakes = bm.clip("lakes", lon, lat, 0.2, where=lambda g: g.scalerank <= 2)
    ax.add_geometries(lakes.geometry, GEO, fc=WATER, ec="none", zorder=1.3)
    where = (lambda g: g.adm0_a3 == "USA") if frame.proj == "albers_conus" else None
    a1 = bm.clip("admin1", lon, lat, where=where)
    ax.add_geometries(a1.geometry, GEO, fc="none", ec=ADMIN1, lw=0.45, zorder=1.5)
    ax.add_geometries(c.geometry, GEO, fc="none", ec=BORDER, lw=0.8, zorder=1.6)


def draw_scalebar(ax, ext, fsx=1.0) -> None:
    """6.6"""
    x0, x1, y0, y1 = ext
    L = nice((x1 - x0) * 0.28)
    bx, by, t = x0 + (x1 - x0) * 0.05, y0 + (y1 - y0) * 0.05, (y1 - y0) * 0.009
    for i, fc in enumerate([INK, "white"]):
        ax.add_patch(Rectangle((bx + i * L / 2, by), L / 2, t, fc=fc, ec=INK, lw=0.6, zorder=20))
    km = L / 1000
    txt = f"{km:g} km  ({km * 0.621371:.0f} mi)" if km >= 5 else f"{km:g} km"
    ax.text(bx + L + (x1 - x0) * 0.015, by + t / 2, txt, fontsize=7 * fsx, va="center", color=INK, zorder=20,
            path_effects=HALO)


def station_xy(frame: Frame, proj, st: pd.DataFrame) -> pd.DataFrame:
    lon = np.array([frame.display.get(g, (x, y))[0] for g, x, y in zip(st.GID, st.LON, st.LAT)], float)
    lat = np.array([frame.display.get(g, (x, y))[1] for g, x, y in zip(st.GID, st.LON, st.LAT)], float)
    p = proj.transform_points(GEO, lon, lat)
    out = st.assign(X=p[:, 0], Y=p[:, 1])
    x0, x1, y0, y1 = frame.extent
    return out[(out.X >= x0) & (out.X <= x1) & (out.Y >= y0) & (out.Y <= y1)]


# --------------------------------------------------------------------------- renderer
class SheetRenderer:
    def __init__(self, plan: Plan, st: pd.DataFrame, theaters: pd.DataFrame, cfg: SheetConfig, meta: SheetMeta,
                 basemap=None):
        self.plan, self.st, self.th, self.cfg, self.meta, self.bm = plan, st, theaters, cfg, meta, basemap
        self.W, self.H = plan.page
        self.fsx = max(0.7, min(1.2, self.W / 24.0))
        self.colors = cfg.classes.colors
        self.k, self.kw = plan.symbol_k, plan.world_k
        self.axes: Dict[str, object] = {}
        self.projs: Dict[str, object] = {}
        self.placers: Dict[str, LabelPlacer] = {}
        self.qa: Dict[str, dict] = {}

    # helpers
    def r(self, slots, k=None) -> float:
        return math.sqrt(max(slots, 0) * (k or self.k) / math.pi)

    def detail(self, slots: int, pct: float) -> str:
        return f"{slots} {unit(slots, self.cfg.value_label)} · {pct:.1f}%"

    def fs(self, v: float) -> float:
        return v * self.fsx

    def frame_rect(self, f: Frame):
        x, y, w, h = f.slot
        return [x / self.W, y / self.H, w / self.W, (h - HEADER_IN) / self.H]

    # ------------------------------------------------------------------ frames
    def draw_header(self, f: Frame, stats: str) -> None:
        x, y, w, h = f.slot
        yy = y + h - HEADER_IN + 0.16
        t = self.top
        t.add_patch(Rectangle((x, yy - 0.13), 0.3, 0.26, fc=INK, ec="none"))
        t.text(x + 0.15, yy, f.letter, color="white", ha="center", va="center", fontsize=self.fs(10.5), weight="bold")
        sh = t.text(x + w, yy, stats, fontsize=self.fs(8.4), color=MUTED, va="center", ha="right")
        stats_w = sh.get_window_extent(renderer=self.renderer).width / self.fig.dpi
        th, _ = self._fit(x + 0.4, yy, f.title, 11, w - 0.4 - stats_w - 0.15, 7.5, weight="semibold", color=INK,
                          va="center")
        if th.get_window_extent(renderer=self.renderer).width / self.fig.dpi > w - 0.4 - stats_w - 0.15:
            sh.set_visible(False)        # a narrow frame keeps its title; the stats are in the station key

    def stats_text(self, vis: pd.DataFrame, compact: bool) -> str:
        n, s = len(vis), int(vis.SLOTS.sum())
        pct = 100 * s / self.meta.total if self.meta.total else 0
        if compact:
            return f"{s} {unit(s, self.cfg.value_label)} · {pct:.1f}%"
        return f"{n} station{'s' if n != 1 else ''} · {s} {unit(s, self.cfg.value_label)} · {pct:.1f}%"

    def draw_frame(self, f: Frame) -> None:
        proj = crs_for(f)
        ax = self.fig.add_axes(self.frame_rect(f), projection=proj)
        ax.set_extent(f.extent, crs=proj)
        ax.apply_aspect()
        ax.spines["geo"].set_edgecolor("#2f3237"); ax.spines["geo"].set_linewidth(0.9)
        self.axes[f.fid], self.projs[f.fid] = ax, proj
        draw_basemap(ax, f, proj, self.bm)
        if f.proj == "albers_conus" and self.bm is not None:
            us = self.bm.get("admin1")
            us = us[(us.adm0_a3 == "USA") & ~us.postal.isin(["AK", "HI", "DC", "RI", "DE", "CT", "NJ", "MD", "MA", "VT", "NH"])]
            for r in us.itertuples():
                p = r.geometry.representative_point()
                ax.text(p.x, p.y, r.postal, transform=GEO, fontsize=self.fs(7), color="#b3b0a7", ha="center",
                        va="center", zorder=2, clip_on=True)
        vis = station_xy(f, proj, self.st).sort_values("SLOTS", ascending=False)
        ax.scatter(vis.X, vis.Y, s=self.k * vis.SLOTS.clip(lower=0.3), c=[self.colors[c] for c in vis.CLS],
                   ec=EDGE, lw=0.7, zorder=10)
        placer = LabelPlacer(ax, self.renderer)
        for r in vis.itertuples():
            placer.add_circle((r.X, r.Y), self.r(r.SLOTS))
        own = vis[vis.GID.isin(f.members)]
        for r in own.itertuples():
            ov = self.cfg.label_overrides.get(r.GID) or self.cfg.label_overrides.get(r.STATION) or {}
            name = ov.get("name", r.NAME)
            fixed = ov if "dir" in ov else None
            placer.place(r.GID, (r.X, r.Y), name, self.detail(r.SLOTS, r.PCT_CALC), self.r(r.SLOTS),
                         self.fs(8.4), fixed=fixed)
        if f.proj != "albers_conus":
            self.reference_places(f, ax, proj, placer, vis)
            self.area_labels(f, ax, proj, placer)
        draw_scalebar(ax, f.extent, self.fsx)
        self.placers[f.fid] = placer
        compact = f.slot[2] < 3.6
        self.draw_header(f, self.stats_text(vis, compact))
        self.qa[f.fid] = {"letter": f.letter, "title": f.title, "visible": vis.GID.tolist(),
                          "owned": f.members, "owned_visible": own.GID.tolist(), **placer.stats()}

    def reference_places(self, f, ax, proj, placer, vis) -> None:
        if self.bm is None:
            return
        lon, lat = boundary_lonlat(proj, f.extent, 20)
        pl = self.bm.get("places")
        pl = pl[pl.longitude.between(lon.min(), lon.max()) & pl.latitude.between(lat.min(), lat.max())]
        if pl.empty:
            return
        p = proj.transform_points(GEO, pl.longitude.values.astype(float), pl.latitude.values.astype(float))
        pl = pl.assign(X=p[:, 0], Y=p[:, 1])
        x0, x1, y0, y1 = f.extent
        pl = pl[(pl.X > x0) & (pl.X < x1) & (pl.Y > y0) & (pl.Y < y1)].sort_values("pop_max", ascending=False)
        mpp = (x1 - x0) / (f.slot[2] * 72)
        n_max = 3 if f.kind == "detail" else 4
        done = 0
        for r in pl.itertuples():
            if done >= n_max:
                break
            d = np.hypot(vis.X - r.X, vis.Y - r.Y) / mpp - np.array([self.r(s) for s in vis.SLOTS])
            if len(d) and d.min() < 4:
                continue
            name = str(r.name)
            res = placer.place_text(f"place:{name}", (r.X, r.Y), name,
                                    dict(fontsize=self.fs(7.4), style="italic", color=FAINT, zorder=9))
            if res is not None:
                ax.plot(r.X, r.Y, marker="s", ms=3.2, mfc="white", mec=FAINT, mew=0.9, zorder=8)
                done += 1

    def area_labels(self, f, ax, proj, placer) -> None:
        if self.bm is None or f.kind != "region" or f.halfwidth_km < 25:
            return
        lon, lat = boundary_lonlat(proj, f.extent, 20)
        c = self.bm.clip("countries", lon, lat, pad=0)
        if c.empty:
            return
        cp = c.to_crs(proj.proj4_init)
        x0, x1, y0, y1 = f.extent
        from shapely.geometry import box
        fb = box(x0, y0, x1, y1)
        for r, g in zip(c.itertuples(), cp.geometry):
            if r.ADM0_A3 == "USA":
                continue
            gi = g.intersection(fb)
            if gi.is_empty or gi.area < 0.06 * fb.area:
                continue
            pt = gi.representative_point()
            placer.place_text(f"area:{r.NAME}", (pt.x, pt.y), str(r.NAME).upper(),
                              dict(fontsize=self.fs(7.2), color="#a3a19a", fontweight="medium", zorder=2),
                              prefer=("C",), offset_pt=0)

    def draw_world(self, f: Frame) -> None:
        proj = crs_for(f)
        ax = self.fig.add_axes(self.frame_rect(f), projection=proj)
        ax.set_global()
        ax.apply_aspect()
        ax.set_facecolor(WATER)
        if self.bm is not None:
            ax.add_geometries(self.bm.get("world").geometry, GEO, fc=LAND, ec="white", lw=0.35, zorder=1)
        ax.gridlines(color="white", lw=0.4, alpha=0.8, xlocs=range(-180, 181, 30), ylocs=range(-60, 91, 30))
        ax.spines["geo"].set_edgecolor("#8d8f94"); ax.spines["geo"].set_linewidth(0.7)
        self.axes[f.fid], self.projs[f.fid] = ax, proj
        th = self.th
        p = proj.transform_points(GEO, th.LON.values.astype(float), th.LAT.values.astype(float))
        th = th.assign(X=p[:, 0], Y=p[:, 1])
        ax.scatter(th.X, th.Y, s=self.kw * th.SLOTS, c=[self.colors[c] for c in th.CLS], ec=EDGE, lw=0.8,
                   zorder=10, alpha=0.92)
        sp = proj.transform_points(GEO, self.st.LON.values.astype(float), self.st.LAT.values.astype(float))
        ax.scatter(sp[:, 0], sp[:, 1], s=3, c=INK, zorder=12, lw=0)
        placer = LabelPlacer(ax, self.renderer)
        for r in th.itertuples():
            placer.add_circle((r.X, r.Y), self.r(r.SLOTS, self.kw))
        for r in th.itertuples():
            placer.place(f"theater:{r.THEATER}", (r.X, r.Y), r.THEATER,
                         f"{r.SLOTS} {unit(r.SLOTS, self.cfg.value_label)} · {r.PCT:.1f}%",
                         self.r(r.SLOTS, self.kw), self.fs(8.2),
                         prefer=["W", "E", "S", "N", "SW", "NW", "SE", "NE"])
        self.placers[f.fid] = placer
        self.draw_header(f, f"{len(self.st)} stations · {self.meta.total} {self.cfg.value_label}")

    def draw_extent_boxes(self) -> None:
        """6.7"""
        frames = self.plan.by_id()
        for f in self.plan.frames:
            if not f.parent or f.parent not in self.axes:
                continue
            pax, pproj = self.axes[f.parent], self.projs[f.parent]
            lon, lat = boundary_lonlat(crs_for(f), f.extent, 30)
            pax.plot(lon, lat, transform=GEO, color=INK, lw=0.9 if f.parent == "world" else 1.1, zorder=25)
            p = pproj.transform_points(GEO, lon, lat)[:, :2]
            corners = {"tl": (tuple(p[90]), (-2, 2), "right", "bottom") if f.parent == "world" else (tuple(p[90]), (0, 2), "left", "bottom"),
                       "tr": (tuple(p[60]), (2, 2), "left", "bottom"),
                       "br": (tuple(p[30]), (2, -2), "left", "top"),
                       "bl": (tuple(p[0]), (-2, -2), "right", "top")}
            placer = self.placers.get(f.parent)
            best = None
            for key in ("tl", "tr", "br", "bl"):
                xy, off, ha, va = corners[key]
                t = pax.annotate(f.letter, xy=xy, xytext=off, textcoords="offset points", ha=ha, va=va,
                                 fontsize=self.fs(8.6), weight="bold", color="white", zorder=31,
                                 bbox=dict(boxstyle="square,pad=0.18", fc=INK, ec="none"), annotation_clip=False)
                bb = t.get_window_extent(self.renderer)
                score = placer._score(bb, None)[2] if placer else 0
                if best is None or score < best[0]:
                    if best is not None:
                        best[1].remove()
                    best = (score, t)
                else:
                    t.remove()
                if score == 0:
                    break
            if placer and best:
                placer.add_bbox(best[1].get_window_extent(self.renderer))
        _ = frames

    # ------------------------------------------------------------------ page furniture
    def card(self, x, y, w, h, title):
        self.under.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.06",
                                            fc="#f7f6f2", ec="#d9d6cd", lw=0.8))
        self.top.text(x + 0.18, y + h - 0.24, title, fontsize=self.fs(12.5), weight="bold", color=INK, va="center")

    def draw_panel(self) -> None:
        """6.9 legend + theater bars + station key."""
        cfg, t, fs = self.cfg, self.top, self.fs
        page = cfg.page
        px = self.W - page.margin_in - page.panel_width_in
        pw = page.panel_width_in
        ytop = self.H - 1.45
        ybot = 0.95
        # legend
        reduced = self.kw < self.k * 0.999
        lh = 4.3 + (0.22 if reduced else 0)
        ly0 = ytop - lh
        self.card(px, ly0, pw, lh, "How to read the symbols")
        t.text(px + 0.18, ly0 + lh - 0.55, f"SIZE — number of authorized {cfg.value_label}", fontsize=fs(8.6),
               weight="semibold", color=MUTED, va="center")
        mx = int(self.st.SLOTS.max())
        samples = sorted({1, int(nice(max(mx / 8, 1))), int(nice(max(mx / 3, 1))), mx})
        xs = px + 0.4
        for v in samples:
            rr = self.r(v) / 72
            t.scatter([xs + rr], [ly0 + lh - 1.25], s=self.k * v, fc="white", ec=EDGE, lw=0.8, zorder=5)
            t.text(xs + rr, ly0 + lh - 1.87, str(v), ha="center", va="center", fontsize=fs(8.5), color=INK)
            xs += 2 * rr + 0.45
        t.text(px + 0.18, ly0 + lh - 2.15, f"COLOR — likelihood class (share of all {self.meta.total} {cfg.value_label})",
               fontsize=fs(8.6), weight="semibold", color=MUTED, va="center")
        cnt = self.st.CLS.value_counts()
        br = cfg.classes.breaks_pct
        n_cls = len(cfg.classes.labels)
        for i, c in enumerate(range(n_cls - 1, -1, -1)):
            yy = ly0 + lh - 2.48 - i * 0.27
            rng = (f"{br[c - 1]:g}% or more" if c == n_cls - 1 else
                   f"under {br[0]:g}%" if c == 0 else f"{br[c - 1]:g} – {br[c]:g}%") + f" of {cfg.value_label}"
            t.scatter([px + 0.32], [yy], s=95, c=cfg.classes.colors[c], ec=EDGE, lw=0.7)
            t.text(px + 0.52, yy, cfg.classes.labels[c], fontsize=fs(9), weight="semibold", color=INK, va="center")
            t.text(px + 1.65, yy, rng, fontsize=fs(8.6), color=MUTED, va="center")
            n = int(cnt.get(c, 0))
            t.text(px + pw - 0.2, yy, f"{n} station{'s' if n != 1 else ''}", fontsize=fs(8.6), color=MUTED,
                   va="center", ha="right")
        cav = ("Share of slots is a proxy for likelihood, not a probability:\nactual assignment also depends on "
               "vacancies, rank, timing\nand the needs of the Army.")
        if reduced:
            cav += "\nWorld-frame circles are drawn at a reduced scale."
        t.text(px + 0.18, ly0 + 0.15, cav, fontsize=fs(7.8), color=MUTED, style="italic", va="bottom", linespacing=1.35)
        # theater bars
        th = self.th
        tht = 0.75 + 0.255 * len(th)
        ty0 = ly0 - 0.25 - tht
        self.card(px, ty0, pw, tht, f"{cfg.value_label.capitalize()} by theater")
        mxs = th.SLOTS.max()
        for i, r in enumerate(th.itertuples()):
            yy = ty0 + tht - 0.62 - i * 0.255
            lab = "Continental U.S." if r.THEATER == "CONUS" else r.THEATER
            t.text(px + 1.45, yy, lab, fontsize=fs(8.8), ha="right", va="center", color=INK)
            t.add_patch(Rectangle((px + 1.55, yy - 0.085), 2.75 * r.SLOTS / mxs, 0.17, fc=self.colors[r.CLS],
                                  ec=EDGE, lw=0.4))
            t.text(px + 1.6 + 2.75 * r.SLOTS / mxs, yy, f"{r.SLOTS}  ·  {r.PCT:.1f}%", fontsize=fs(8.4),
                   va="center", color=INK, weight="medium")
        # key
        ky0, kh = ybot, ty0 - 0.25 - ybot
        self.card(px, ky0, pw, kh, "Station key — ranked by " + cfg.value_label)
        self.draw_key(px, ky0, pw, kh)

    def draw_key(self, px, ky0, pw, kh) -> None:
        t, fs, st = self.top, self.fs, self.st
        hy = ky0 + kh - 0.55
        avail = hy - 0.22 - ky0 - 0.12
        n = len(st)
        two_col = avail / n < 0.155
        per_col = math.ceil(n / 2) if two_col else n
        rowh = min(0.24, avail / per_col)
        shown = st
        if rowh < 0.13:
            per_col = int(avail / 0.13) if not two_col else int(avail / 0.13)
            cap = per_col * (2 if two_col else 1) - 1
            shown = st.head(cap)
            rowh = 0.13
        cols_spec = [(px, pw)] if not two_col else [(px, pw / 2), (px + pw / 2, pw / 2)]
        for ci, (cx, cw) in enumerate(cols_spec):
            rows = shown.iloc[ci * per_col:(ci + 1) * per_col]
            if not two_col:
                cols = {"rank": cx + 0.42, "dot": cx + 0.58, "name": cx + 0.74, "where": cx + 2.62, "frame": cx + 3.38,
                        "slots": cx + 3.98, "pct": cx + 4.52, "bar": cx + 4.62}
                heads = [("rank", "#", "right"), ("name", "Duty station", "left"), ("where", "Where", "left"),
                         ("frame", "Frame", "center"), ("slots", self.cfg.value_label.capitalize(), "right"),
                         ("pct", "%", "right")]
            else:
                cols = {"rank": cx + 0.3, "dot": cx + 0.42, "name": cx + 0.54, "frame": cx + cw - 0.62,
                        "slots": cx + cw - 0.22}
                heads = [("rank", "#", "right"), ("name", "Duty station", "left"), ("frame", "Fr.", "center"),
                         ("slots", "N", "right")]
            for k_, t_, ha in heads:
                t.text(cols[k_], hy, t_, fontsize=fs(8), weight="semibold", color=MUTED, ha=ha, va="center")
            for i, r in enumerate(rows.itertuples()):
                yy = hy - 0.24 - i * rowh
                if i % 2 == 0:
                    t.add_patch(Rectangle((cx + 0.1, yy - rowh / 2), cw - 0.2, rowh, fc="#ecebe5", ec="none", zorder=0))
                f8 = fs(8.3 if not two_col else 7.4)
                t.text(cols["rank"], yy, str(r.RANK), fontsize=f8 * 0.96, color=MUTED, ha="right", va="center")
                t.scatter([cols["dot"]], [yy], s=30 if not two_col else 20, c=self.colors[r.CLS], ec=EDGE, lw=0.5, zorder=3)
                nm = r.NAME if not two_col or len(r.NAME) < 24 else r.NAME[:22] + "…"
                t.text(cols["name"], yy, nm, fontsize=f8, color=INK, va="center", weight="medium")
                if "where" in cols:
                    t.text(cols["where"], yy, r.WHERE, fontsize=f8 * 0.96, color=MUTED, va="center")
                t.add_patch(Rectangle((cols["frame"] - 0.08, yy - 0.075), 0.16, 0.15, fc=INK, ec="none", zorder=3))
                t.text(cols["frame"], yy, self.letter_of.get(r.GID, "–"), fontsize=f8 * 0.92, color="white",
                       va="center", ha="center", weight="bold", zorder=4)
                t.text(cols["slots"], yy, str(r.SLOTS), fontsize=f8, color=INK, ha="right", va="center", weight="semibold")
                if "pct" in cols:
                    t.text(cols["pct"], yy, f"{r.PCT_CALC:.1f}", fontsize=f8 * 0.96, color=MUTED, ha="right", va="center")
                    mx = st.SLOTS.max()
                    t.add_patch(Rectangle((cols["bar"], yy - rowh * 0.28), 0.5 * r.SLOTS / mx, rowh * 0.56,
                                          fc=self.colors[r.CLS], ec="none"))
        if len(shown) < n:
            t.text(px + 0.2, ky0 + 0.1, f"+ {n - len(shown)} more stations — see duty_station_table.csv",
                   fontsize=fs(8), color=MUTED, style="italic", va="bottom")

    def _fit(self, x: float, y: float, text: str, size: float, max_w: float, min_size: float, **kw):
        """Draw text at the largest size <= `size` whose width fits `max_w` inches (never below min_size)."""
        h = self.top.text(x, y, text, fontsize=self.fs(size), **kw)
        while size > min_size:
            bb = h.get_window_extent(renderer=self.renderer)
            if bb.width / self.fig.dpi <= max_w:
                break
            size -= 0.5
            h.set_fontsize(self.fs(size))
        return h, size

    def draw_title(self) -> None:
        cfg, t, fs, st, total = self.cfg, self.top, self.fs, self.st, self.meta.total
        title = cfg.title
        if cfg.mos and cfg.title == "Where the Slots Are: Authorized Positions by Duty Station":
            title = f"Where the Slots Are: {cfg.mos} Authorized Positions by Duty Station"
        ty = self.H - 0.95
        top = st.iloc[0]
        kpis = [(f"{top.PCT_CALC:.0f}%", f"of all {cfg.value_label} are at\n{top.NAME}, {top.WHERE}")]
        th = self.th.iloc[0]
        if th.THEATER != top.WHERE:
            where = "the continental\nUnited States" if th.THEATER == "CONUS" else th.THEATER
            kpis.append((f"{th.PCT:.0f}%", f"are in {where}"))
        ovs = st[st.COUNTRY_C != "USA"]
        if len(ovs):
            n_c = ovs.COUNTRY_C.nunique()
            kpis.append((f"{100 * ovs.SLOTS.sum() / total:.0f}%",
                         f"are overseas\n({ovs.SLOTS.sum()} {cfg.value_label}, {n_c} countr{'y' if n_c == 1 else 'ies'})"))
        x_end = self.W - 0.4
        left_kpi = x_end
        for i, (big, small) in enumerate(reversed(kpis)):
            x = x_end - 2.0 - i * 2.55
            h = t.text(x, ty + 0.05, big, fontsize=fs(27), weight="bold", color=cfg.classes.colors[-1], va="center",
                       ha="right")
            t.text(x + 0.1, ty + 0.05, small, fontsize=fs(9.5), color=INK, va="center", linespacing=1.3)
            left_kpi = min(left_kpi, h.get_window_extent(renderer=self.renderer).x0 / self.fig.dpi)
        max_w = left_kpi - 0.4 - 0.35
        _, size = self._fit(0.4, ty + 0.28, title, 30, max_w, 18, weight="bold", color=INK, va="center")
        self.title_size = size
        n_all = len(st) + len(self.meta.excluded)
        sub = cfg.subtitle
        if sub == "auto":
            ex = f"; {', '.join(n.title() for n in self.meta.excluded)} {'has' if len(self.meta.excluded) == 1 else 'have'} 0 {cfg.value_label}" if self.meta.excluded else ""
            sub = (f"{total} authorized {cfg.value_label} across {n_all} duty stations ({len(st)} mapped{ex}).  "
                   f"Circle size = number of {cfg.value_label}.  Color = how likely an assignment there is, from that "
                   f"station’s share of all {cfg.value_label}.")
        h, ssize = self._fit(0.4, ty - 0.32, sub, 13, max_w, 9.5, color=MUTED, va="center")
        if h.get_window_extent(renderer=self.renderer).width / self.fig.dpi > max_w:
            # still too long at the minimum size: wrap onto two lines
            width = max(40, int(len(sub) * max_w / (h.get_window_extent(renderer=self.renderer).width / self.fig.dpi)))
            h.set_text("\n".join(textwrap.wrap(sub, width)[:2]))
            h.set_linespacing(1.15)

    def draw_footer(self) -> None:
        cfg, m = self.cfg, self.meta
        s = [f"Data: {m.source_name}; {m.total} {cfg.value_label}, {len(self.st) + len(m.excluded)} duty stations"
             + (f" ({cfg.mos})" if cfg.mos else "") + "."]
        s.extend(cfg.notes)                      # what the user (or the batch) says about the data comes first
        if m.excluded:
            s.append(f"Not mapped (0 {cfg.value_label}): {', '.join(n.title() for n in m.excluded)}.")
        if m.merged:
            if len(m.merged) <= 6:
                s.append(f"Duplicate source rows combined for {', '.join(m.merged)}.")
            else:
                s.append(f"Several source rows for the same station were combined at {len(m.merged)} stations "
                         "(see N_RECORDS in duty_station_table.csv).")
        if m.dropped:
            s.append(f"Dropped (no coordinates): {', '.join(m.dropped)}.")
        if m.fuzzy:
            s.append(f"Name matches accepted by similarity: {'; '.join(m.fuzzy)}.")
        if getattr(m, "towns", None):
            s.append(f"Placed at the town centre (GeoNames): {'; '.join(t.split(' → ')[1] for t in m.towns)}.")
        if m.approx_coords:
            s.append("Point locations are approximate installation-level placements, not surveyed coordinates.")
        s.append("Projections: Equal Earth (world), Albers Equal Area Conic (continental U.S.), Lambert Azimuthal Equal "
                 "Area centred on each other frame — all equal-area, so circle sizes are comparable across frames.")
        for f in self.plan.frames:
            for n in f.notes:
                s.append(f"Frame {f.letter}: {n}.")
        s.append("World frame: circles are theater totals at the slot-weighted centre of their stations; black dots are "
                 "the stations. Lettered boxes show where each detail frame sits. Basemap: Natural Earth.")
        date = cfg.date if cfg.date != "auto" else _dt.date.today().strftime("%d %b %Y").lstrip("0")
        s.append(f"Prepared {date}" + (f" by {cfg.prepared_by}" if cfg.prepared_by else "") + " with slotmap-tools.")
        width = int((self.W - 0.8) * 11.6 / self.fsx)
        lines = textwrap.wrap(" ".join(s), width)
        max_lines = 4
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][: width - 30] + " … (full notes in qa_report.json)"
        for i, ln in enumerate(lines):
            self.top.text(0.4, 0.78 - i * 0.2, ln, fontsize=self.fs(8.2), color=MUTED, va="center")
        self.footer_notes = s

    # ------------------------------------------------------------------ main
    def render(self, out_base: Path) -> Dict[str, object]:
        setup_fonts(self.cfg.fonts)
        self.fig = plt.figure(figsize=(self.W, self.H), facecolor="white")
        self.renderer = self.fig.canvas.get_renderer()
        self.under = self.fig.add_axes([0, 0, 1, 1], zorder=-1)
        self.top = self.fig.add_axes([0, 0, 1, 1], zorder=50)
        for a in (self.under, self.top):
            a.set_xlim(0, self.W); a.set_ylim(0, self.H); a.axis("off")
        self.letter_of = {g: f.letter for f in self.plan.frames for g in f.members}
        for f in self.plan.frames:
            if f.kind == "world":
                self.draw_world(f)
            else:
                self.draw_frame(f)
        self.draw_extent_boxes()
        self.draw_title()
        self.draw_panel()
        self.draw_footer()
        out_base = Path(out_base)
        written = []
        for ext in self.cfg.outputs:
            p = out_base.with_suffix(f".{ext}")
            self.fig.savefig(p, dpi=self.cfg.page.dpi if ext == "png" else None)
            written.append(str(p))
        plt.close(self.fig)
        return {"files": written, "frames": self.qa, "footer_notes": self.footer_notes}
