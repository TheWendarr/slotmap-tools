"""6.4 Automatic label placement.

Every label is a two-line block (name + detail) anchored to a symbol. Candidates are tried in a
preferred order at three distances (the outer two get a leader line). Each candidate is measured
with the real renderer and scored by how much it overlaps already-placed labels, symbols, and the
frame edge. The least-bad candidate wins; anything that still overlaps is reported to QA.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import matplotlib.patheffects as pe
from matplotlib.transforms import Bbox

HALO = [pe.withStroke(linewidth=2.6, foreground="white")]
DIRS = {  # ha, unit x, unit y, stacking mode
    "E": ("left", 1, 0, "mid"), "W": ("right", -1, 0, "mid"), "N": ("center", 0, 1, "up"),
    "S": ("center", 0, -1, "down"), "NE": ("left", .72, .72, "up"), "NW": ("right", -.72, .72, "up"),
    "SE": ("left", .72, -.72, "down"), "SW": ("right", -.72, -.72, "down"),
    "C": ("center", 0, 0, "mid"),
}
ORDER = ["E", "W", "NE", "SE", "NW", "SW", "N", "S"]


def _overlap(a: Bbox, b: Bbox) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return w * h if w > 0 and h > 0 else 0.0


@dataclass
class Placed:
    key: str
    bbox: Bbox
    overlap: float
    outside: float
    leader: bool


@dataclass
class LabelPlacer:
    ax: object
    renderer: object
    ink: str = "#1f2328"
    muted: str = "#5f636b"
    obstacles: List[Bbox] = field(default_factory=list)
    placed: List[Placed] = field(default_factory=list)

    def pt(self, v: float) -> float:
        return self.renderer.points_to_pixels(v)

    def add_circle(self, xy_data: Tuple[float, float], r_pt: float, pad_pt: float = 1.0) -> None:
        x, y = self.ax.transData.transform(xy_data)
        r = self.pt(r_pt + pad_pt)
        self.obstacles.append(Bbox.from_extents(x - r, y - r, x + r, y + r))

    def add_bbox(self, bb: Bbox) -> None:
        self.obstacles.append(bb)

    # ------------------------------------------------------------------ core
    def _make(self, xy, name, detail, d, r_pt, mult, fs, extra):
        ha, ux, uy, mode = DIRS[d]
        g = 3.2
        dist = (r_pt + g) * mult + (0 if mult == 1 else 4)
        dx, dy = ux * dist + extra[0], uy * dist + extra[1]
        kw = dict(xy=xy, textcoords="offset points", ha=ha, zorder=30, path_effects=HALO, annotation_clip=False)
        if mode == "mid":
            n_xy, n_va, d_xy, d_va = (dx, dy + 0.6), "bottom", (dx, dy - 0.6), "top"
        elif mode == "up":
            d_xy, d_va, n_xy, n_va = (dx, dy), "bottom", (dx, dy + fs * 1.02), "bottom"
        else:
            n_xy, n_va, d_xy, d_va = (dx, dy), "top", (dx, dy - fs * 1.2), "top"
        arts = [self.ax.annotate(name, xytext=n_xy, va=n_va, fontsize=fs, weight="semibold", color=self.ink, **kw)]
        if detail:
            arts.append(self.ax.annotate(detail, xytext=d_xy, va=d_va, fontsize=fs * 0.8, color=self.muted, **kw))
        leader = None
        if mult > 1 or extra != (0, 0):
            leader = self.ax.annotate("", xy=xy, xytext=(dx - ux * 1.5, dy - uy * 1.5), textcoords="offset points",
                                      arrowprops=dict(arrowstyle="-", color=self.ink, lw=0.6, shrinkA=0,
                                                      shrinkB=r_pt + 0.5), zorder=29)
        return arts, leader

    def _bbox(self, arts) -> Bbox:
        return Bbox.union([a.get_window_extent(self.renderer) for a in arts]).expanded(1.0, 1.0)

    def _score(self, bb: Bbox, own: Optional[Bbox]) -> Tuple[float, float, float]:
        ov = sum(_overlap(bb, o) for o in self.obstacles if own is None or o is not own)
        ov += sum(_overlap(bb, p.bbox) for p in self.placed)
        fr = self.ax.bbox
        inside = _overlap(bb, fr)
        outside = bb.width * bb.height - inside
        return ov, outside, ov + 25 * outside

    def place(self, key: str, xy, name: str, detail: str = "", r_pt: float = 3.0, fs: float = 8.4,
              prefer: Sequence[str] = ORDER, fixed: Optional[dict] = None, optional: bool = False,
              mults: Sequence[float] = (1.0, 2.0, 3.0)) -> Optional[Placed]:
        """Place one label. `fixed` = {'dir','dx','dy'} forces a position. `optional` labels are dropped
        (returns None) instead of being placed with overlaps."""
        cands = []
        if fixed:
            cands = [(fixed.get("dir", "E"), 1.0, (float(fixed.get("dx", 0)), float(fixed.get("dy", 0))))]
        else:
            for m in mults:
                for i, d in enumerate(prefer):
                    cands.append((d, m, (0.0, 0.0)))
        best = None
        for idx, (d, m, extra) in enumerate(cands):
            arts, leader = self._make(xy, name, detail, d, r_pt, m, fs, extra)
            bb = self._bbox(arts)
            ov, out, sc = self._score(bb, None)
            sc += idx * 0.5 + (40 if m > 1 else 0)          # mild preference for order and no leader
            for a in arts:
                a.remove()
            if leader is not None:
                leader.remove()
            if best is None or sc < best[0]:
                best = (sc, d, m, extra, ov, out)
            if ov == 0 and out == 0:
                break
        sc, d, m, extra, ov, out = best
        if optional and (ov > 0 or out > 0):
            return None
        arts, leader = self._make(xy, name, detail, d, r_pt, m, fs, extra)
        bb = self._bbox(arts)
        p = Placed(key, bb, ov, out, leader is not None)
        self.placed.append(p)
        return p

    def place_text(self, key: str, xy, text: str, style: dict, prefer=("E", "W", "N", "S"),
                   offset_pt: float = 4.0, optional: bool = True) -> Optional[Placed]:
        """Single-line helper for reference places (grey italic)."""
        best = None
        for d in prefer:
            ha, ux, uy, mode = DIRS[d]
            va = "center" if uy == 0 else ("bottom" if uy > 0 else "top")
            t = self.ax.annotate(text, xy=xy, xytext=(ux * offset_pt, uy * offset_pt), textcoords="offset points",
                                 ha=ha, va=va, path_effects=HALO, annotation_clip=False, **style)
            bb = Bbox(t.get_window_extent(self.renderer).get_points()).expanded(1.0, 1.0)
            ov, out, sc = self._score(bb, None)
            if ov == 0 and out == 0:
                self.placed.append(Placed(key, bb, 0, 0, False))
                return self.placed[-1]
            t.remove()
            if best is None or sc < best[0]:
                best = (sc, d)
        return None

    def stats(self) -> dict:
        return {"labels": len(self.placed),
                "overlapping": [p.key for p in self.placed if p.overlap > 0.5],
                "outside": [p.key for p in self.placed if p.outside > 0.5],
                "leaders": [p.key for p in self.placed if p.leader]}
