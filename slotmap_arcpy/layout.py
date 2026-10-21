"""Page geometry: pack the planned frames into rows, give each a slot and a letter (pure Python)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .config import PageConfig

HEADER_IN = 0.34   # strip above each frame for letter + title + stats
GAP = 0.15         # gap between frames
TITLE_H = 1.45     # title band at the top of the page
FOOTER_H = 0.95    # footer band
MAX_ROW3 = 6


class LayoutError(RuntimeError):
    pass


@dataclass
class Slot:
    x: float
    y: float
    w: float
    h: float           # includes the header strip
    row: int

    @property
    def area(self) -> float:
        return self.w * (self.h - HEADER_IN)

    @property
    def map_aspect(self) -> float:   # height / width of the map area
        return (self.h - HEADER_IN) / self.w

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (round(self.x, 3), round(self.y, 3), round(self.w, 3), round(self.h, 3))


def capacity() -> int:
    """Max non-world frames the row template can hold: 2 (top) + 1 main + 3 (side) + 6 (bottom)."""
    return 2 + 1 + 3 + MAX_ROW3


def map_area(page: PageConfig) -> Tuple[float, float, float, float]:
    x0 = page.margin_in
    x1 = page.width_in - page.margin_in - page.panel_width_in - 0.2
    y0 = FOOTER_H
    y1 = page.height_in - TITLE_H
    return x0, y0, x1, y1


def make_slots(n_other: int, page: PageConfig) -> Tuple[Slot, Slot, List[Slot]]:
    """Return (world_slot, main_slot, other_slots) for n_other additional frames."""
    if n_other > capacity() - 1:
        raise LayoutError(f"{n_other + 2} frames requested; the layout holds at most {capacity() + 1}.")
    x0, y0, x1, y1 = map_area(page)
    W, H = x1 - x0, y1 - y0
    k1 = min(2, n_other)
    k2 = min(3, n_other - k1)
    k3 = n_other - k1 - k2
    if k3:
        hs = [0.33, 0.42, 0.25]
    else:
        hs = [0.42, 0.58, 0.0]
    usable = H - GAP * (2 if k3 else 1)
    h1, h2, h3 = (usable * f for f in hs)
    top1 = y1
    row1_y = top1 - h1
    row2_y = row1_y - GAP - h2
    row3_y = row2_y - GAP - h3

    others: List[Slot] = []
    # row 1: world + k1
    ww = W * 0.47 if k1 else W
    world = Slot(x0, row1_y, ww, h1, 0)
    if k1:
        each = (W - ww - GAP * k1) / k1
        for i in range(k1):
            others.append(Slot(x0 + ww + GAP + i * (each + GAP), row1_y, each, h1, 0))
    # row 2: main + side column(s)
    if k2 == 0:
        main = Slot(x0, row2_y, W, h2, 1)
    else:
        mw = W * (0.60 if k2 >= 2 else 0.66)
        main = Slot(x0, row2_y, mw, h2, 1)
        sx, sw = x0 + mw + GAP, W - mw - GAP
        if k2 == 1:
            others.append(Slot(sx, row2_y, sw, h2, 1))
        elif k2 == 2:
            hh = (h2 - GAP) / 2
            others.append(Slot(sx, row2_y + hh + GAP, sw, hh, 1))
            others.append(Slot(sx, row2_y, sw, hh, 1))
        else:
            cw = (sw - GAP) * 0.55
            others.append(Slot(sx, row2_y, cw, h2, 1))
            rx, rw = sx + cw + GAP, sw - cw - GAP
            hh = (h2 - GAP) / 2
            others.append(Slot(rx, row2_y + hh + GAP, rw, hh, 1))
            others.append(Slot(rx, row2_y, rw, hh, 1))
    # row 3: strip
    if k3:
        each = (W - GAP * (k3 - 1)) / k3
        for i in range(k3):
            others.append(Slot(x0 + i * (each + GAP), row3_y, each, h3, 2))
    return world, main, others


def assign(frames: List[dict], page: PageConfig) -> Dict[str, Slot]:
    """frames: [{'fid','priority','kind'}] excluding the world. First by priority becomes the main frame.

    Larger slots go to higher-priority frames. Returns {fid: Slot} including 'world'.
    """
    order = sorted(frames, key=lambda f: -f["priority"])
    world, main, others = make_slots(len(order) - 1, page)
    out = {"world": world}
    if order:
        out[order[0]["fid"]] = main
    slots = sorted(others, key=lambda s: -s.area)
    for f, s in zip(order[1:], slots):
        out[f["fid"]] = s
    return out


def letters_in_reading_order(slots: Dict[str, Slot]) -> Dict[str, str]:
    keys = [k for k in slots if k != "world"]
    keys.sort(key=lambda k: (slots[k].row, round(slots[k].x, 2), -slots[k].y))
    return {k: chr(ord("A") + i) for i, k in enumerate(keys)}
