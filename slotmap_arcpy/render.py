"""Stage 6 - build the sheet as an ArcGIS Pro layout and export it (ArcPy, Pro 2.5+).

ArcPy in Pro 2.x cannot create layouts, map frames or text from scratch, but it can export a layout to a
.pagx file (plain JSON) and import one back. So:

1. A template project holds a layout named `slotmap_template` with ONE map frame, ONE point text and ONE
   rectangle (any size, any style). See pro_template/TEMPLATE.md.
2. That layout is exported to JSON once per run. Its three elements are the samples every element of the
   sheet is cloned from (positions, sizes, colours, fonts and text changed in the JSON).
3. One map per frame is written into the same file, with the frame's equal-area projection and a water
   background. The file is imported, then ArcPy adds the Natural Earth layers and the station layers,
   symbolizes them, sets labels (Maplex) and each frame's extent, and exports PDF/PNG and a copy of the project.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import math
import os
import re
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import basemap as B
from .config import SheetConfig
from .layout import HEADER_IN
from .plan import Frame, Plan, radius_pt

INK, MUTED, FAINT = "#1f2328", "#5f636b", "#8d8f94"
WATER, LAND, LAND_OTHER = "#d6e2ea", "#ecebe6", "#f5f4f0"
BORDER, ADMIN1, URBAN = "#9e9b92", "#c4c1b8", "#dedad0"
EDGE, CARD, CARD_EDGE = "#2b1d14", "#f7f6f2", "#dcd9d0"
TEMPLATE_LAYOUT = "slotmap_template"
SHORT_WHERE = {"United Kingdom": "UK", "Netherlands": "Neth.", "United Arab Emirates": "UAE",
               "Saudi Arabia": "Saudi Arab.", "Philippines": "Philippines"}


class TemplateError(RuntimeError):
    pass


def rgb(hexstr: str, alpha: float = 100) -> List[float]:
    h = hexstr.lstrip("#")
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha]


def nice(v: float) -> float:
    e = 10 ** math.floor(math.log10(v))
    m = v / e
    return (1 if m < 2 else 2 if m < 5 else 5) * e


def text_width_in(text: str, size_pt: float, bold: bool = False) -> float:
    """Rough width of one line of Arial-like text (good enough to fit titles and wrap the footer)."""
    longest = max((len(t) for t in str(text).split("\n")), default=0)
    return longest * size_pt * (0.57 if bold else 0.52) / 72.0


def unit(n: int, label: str) -> str:
    return label[:-1] if n == 1 and label.endswith("s") else label


# --------------------------------------------------------------------------- JSON (.pagx) builder
def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            for x in _walk(v):
                yield x
    elif isinstance(o, list):
        for v in o:
            for x in _walk(v):
                yield x


def _find(o, type_name: str) -> Optional[dict]:
    for d in _walk(o):
        if d.get("type") == type_name:
            return d
    return None


def _color(values: List[float]) -> dict:
    return {"type": "CIMRGBColor", "values": [float(v) for v in values]}


class PagxBuilder:
    """Clone the template's map frame / text / rectangle into a new layout document."""

    def __init__(self, doc: dict):
        self.doc = doc
        lay = doc.get("layoutDefinition") or {}
        els = lay.get("elements") or []
        self.mapframe = next((e for e in els if e.get("type") == "CIMMapFrame"), None)
        self.text = next((e for e in els if e.get("type") == "CIMGraphicElement"
                          and (e.get("graphic") or {}).get("type") == "CIMTextGraphic"), None)
        self.poly = next((e for e in els if e.get("type") == "CIMGraphicElement"
                          and (e.get("graphic") or {}).get("type") == "CIMPolygonGraphic"), None)
        missing = [n for n, v in (("a map frame", self.mapframe), ("a point text element", self.text),
                                  ("a rectangle graphic", self.poly)) if v is None]
        if missing:
            raise TemplateError("The template layout '{}' needs {}. See pro_template/TEMPLATE.md.".format(
                lay.get("name", "?"), ", ".join(missing)))
        uri = (self.mapframe.get("view") or {}).get("viewableObjectPath")
        maps = doc.get("mapDefinitions") or []
        self.mapdef = next((m for m in maps if m.get("uRI") == uri), maps[0] if maps else None)
        if self.mapdef is None:
            raise TemplateError("The template's map frame has no map. Point the map frame at a map and save.")
        self._ring3 = self._has_z(self.poly)
        self.elements = []  # type: List[dict]   # back-to-front
        self.maps = []  # type: List[dict]
        self.names = set()

    @staticmethod
    def _has_z(el) -> bool:
        poly = (el.get("graphic") or {}).get("polygon") or {}
        rings = poly.get("rings") or [[[0, 0]]]
        return len(rings[0][0]) > 2

    def _ring(self, pts: Sequence[Tuple[float, float]]) -> List[List[float]]:
        pts = list(pts)
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return [[round(x, 4), round(y, 4), 0.0] if self._ring3 else [round(x, 4), round(y, 4)] for x, y in pts]

    def _rect_ring(self, x, y, w, h):
        return self._ring([(x, y), (x, y + h), (x + w, y + h), (x + w, y), (x, y)])

    def _name(self, base: str) -> str:
        n, i = base, 1
        while n in self.names:
            i += 1
            n = "{}_{}".format(base, i)
        self.names.add(n)
        return n

    # ---------------------------------------------------------------- maps + frames
    def add_map(self, name: str, uri: str, wkt: str, extent: Tuple[float, float, float, float],
                background: str = WATER) -> dict:
        m = copy.deepcopy(self.mapdef)
        m["name"] = name
        m["uRI"] = uri
        m["layers"] = []
        if "standaloneTables" in m:
            m["standaloneTables"] = []
        m["spatialReference"] = {"wkt": wkt}
        x0, x1, y0, y1 = extent
        m["defaultExtent"] = {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1, "spatialReference": {"wkt": wkt}}
        m["backgroundColor"] = _color(rgb(background))
        self.maps.append(m)
        return m

    def add_mapframe(self, name: str, map_uri: str, x: float, y: float, w: float, h: float) -> dict:
        e = copy.deepcopy(self.mapframe)
        e["name"] = self._name(name)
        e["frame"] = {"rings": [self._rect_ring(x, y, w, h)]}
        e.setdefault("view", {})["viewableObjectPath"] = map_uri
        e["anchor"] = "BottomLeftCorner"
        e["visible"] = True
        if "rotationCenter" in e:
            e["rotationCenter"] = {"x": x, "y": y}
        self.elements.append(e)
        return e

    # ---------------------------------------------------------------- graphics
    def add_poly(self, name: str, pts: Sequence[Tuple[float, float]], fill: Optional[str],
                 stroke: Optional[str] = None, stroke_w: float = 0.6, fill_alpha: float = 100) -> dict:
        e = copy.deepcopy(self.poly)
        e["name"] = self._name(name)
        ring = self._ring(pts)
        g = e["graphic"]
        g["polygon"] = {"rings": [ring]}
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        e["frame"] = {"rings": [self._rect_ring(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))]}
        e["anchor"] = "BottomLeftCorner"
        e["visible"] = True
        if "rotationCenter" in e:
            e["rotationCenter"] = {"x": min(xs), "y": min(ys)}
        sym = (g.get("symbol") or {}).get("symbol") or {}
        layers = [l for l in sym.get("symbolLayers") or [] if l.get("type") in ("CIMSolidFill", "CIMSolidStroke")]
        fills = [l for l in layers if l["type"] == "CIMSolidFill"]
        strokes = [l for l in layers if l["type"] == "CIMSolidStroke"]
        if not fills:
            fills = [{"type": "CIMSolidFill", "enable": True, "color": _color([0, 0, 0, 100])}]
        if not strokes:
            strokes = [{"type": "CIMSolidStroke", "enable": True, "capStyle": "Round", "joinStyle": "Round",
                        "miterLimit": 10, "width": 1, "color": _color([0, 0, 0, 100])}]
        f, s = fills[0], strokes[0]
        f["enable"] = fill is not None
        f["color"] = _color(rgb(fill or "#ffffff", fill_alpha))
        s["enable"] = stroke is not None
        s["width"] = float(stroke_w)
        s["color"] = _color(rgb(stroke or "#000000"))
        sym["type"] = "CIMPolygonSymbol"
        sym["symbolLayers"] = [s, f]            # stroke drawn over fill
        g["symbol"] = {"type": "CIMSymbolReference", "symbol": sym}
        self.elements.append(e)
        return e

    def add_rect(self, name, x, y, w, h, fill, stroke=None, stroke_w=0.6):
        return self.add_poly(name, [(x, y), (x, y + h), (x + w, y + h), (x + w, y)], fill, stroke, stroke_w)

    def add_circle(self, name, cx, cy, r, fill, stroke=EDGE, stroke_w=0.7, n=48):
        pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        return self.add_poly(name, pts, fill, stroke, stroke_w)

    def add_text(self, name: str, x: float, y: float, text: str, size: float, color: str = INK,
                 bold: bool = False, italic: bool = False, ha: str = "left", va: str = "center",
                 font: str = "Arial") -> dict:
        e = copy.deepcopy(self.text)
        e["name"] = self._name(name)
        g = e["graphic"]
        g["text"] = text
        g["shape"] = {"x": round(x, 4), "y": round(y, 4)}
        g["placement"] = "Unspecified"
        ts = (g.get("symbol") or {}).get("symbol") or {"type": "CIMTextSymbol"}
        ts["height"] = float(size)
        ts["fontFamilyName"] = font
        ts["fontStyleName"] = "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
        ts["horizontalAlignment"] = {"left": "Left", "center": "Center", "right": "Right"}[ha]
        ts["verticalAlignment"] = {"top": "Top", "center": "Center", "bottom": "Bottom", "baseline": "Baseline"}[va]
        fill = _find(ts.get("symbol"), "CIMSolidFill")
        if fill is None:
            ts["symbol"] = {"type": "CIMPolygonSymbol",
                            "symbolLayers": [{"type": "CIMSolidFill", "enable": True, "color": _color(rgb(color))}]}
        else:
            fill["enable"] = True
            fill["color"] = _color(rgb(color))
        g["symbol"] = {"type": "CIMSymbolReference", "symbol": ts}
        lines = str(text).count("\n") + 1
        w = text_width_in(text, size, bold)
        h = lines * size * 1.2 / 72
        x0 = x if ha == "left" else x - w / 2 if ha == "center" else x - w
        y0 = y - h / 2 if va == "center" else y - h if va == "top" else y
        e["frame"] = {"rings": [self._rect_ring(x0, y0, max(w, 0.01), max(h, 0.01))]}
        e["anchor"] = {("left", "center"): "LeftMidPoint", ("center", "center"): "CenterPoint",
                       ("right", "center"): "RightMidPoint", ("left", "top"): "TopLeftCorner",
                       ("right", "top"): "TopRightCorner", ("center", "top"): "TopMidPoint",
                       ("left", "bottom"): "BottomLeftCorner", ("right", "bottom"): "BottomRightCorner",
                       ("center", "bottom"): "BottomMidPoint"}.get((ha, va if va != "baseline" else "bottom"),
                                                                  "BottomLeftCorner")
        e["visible"] = True
        if "rotationCenter" in e:
            e["rotationCenter"] = {"x": x, "y": y}
        self.elements.append(e)
        return e

    def finish(self, name: str, uri: str, width_in: float, height_in: float) -> dict:
        doc = copy.deepcopy(self.doc)
        lay = doc["layoutDefinition"]
        lay["name"] = name
        lay["uRI"] = uri
        page = lay.setdefault("page", {"type": "CIMPage"})
        page["width"] = float(width_in)
        page["height"] = float(height_in)
        page["units"] = {"uwkid": 109008}          # inches
        lay["elements"] = list(reversed(self.elements))   # CIM lists the top-most element first
        doc["mapDefinitions"] = self.maps
        doc["layerDefinitions"] = []
        for k in ("tableDefinitions", "standaloneTableDefinitions"):
            if k in doc:
                doc[k] = []
        return doc


# --------------------------------------------------------------------------- the sheet
class SheetBuilder:
    """Everything that goes on the page, in page inches (origin bottom-left), written into a PagxBuilder."""

    def __init__(self, pb: PagxBuilder, plan: Plan, st: List[dict], th: List[dict], cfg: SheetConfig, meta: dict):
        self.pb, self.plan, self.st, self.th, self.cfg, self.meta = pb, plan, st, th, cfg, meta
        self.W, self.H = cfg.page.width_in, cfg.page.height_in
        self.font = cfg.fonts[0] if cfg.fonts else "Arial"
        self.colors = cfg.classes.colors
        self.footer_notes = []  # type: List[str]

    def T(self, name, x, y, text, size, **kw):
        kw.setdefault("font", self.font)
        return self.pb.add_text(name, x, y, text, size, **kw)

    def fit_size(self, text: str, size: float, max_w: float, min_size: float, bold: bool = False) -> float:
        while size > min_size and text_width_in(text, size, bold) > max_w:
            size -= 0.5
        return size

    # ---------------------------------------------------------------- frames
    def frame_map_rect(self, f: Frame) -> Tuple[float, float, float, float]:
        x, y, w, h = f.slot
        return x, y, w, h - HEADER_IN

    def header(self, f: Frame, vis: List[dict]) -> None:
        x, y, w, h = f.slot
        yy = y + h - HEADER_IN + 0.16
        n = len(vis)
        s = sum(int(v["SLOTS"]) for v in vis)
        pct = 100.0 * s / self.meta["total"] if self.meta["total"] else 0
        if f.kind == "world" or w > 4:
            stats = "{} station{} · {} {} · {:.1f}%".format(n, "" if n == 1 else "s", s, unit(s, self.cfg.value_label), pct)
        else:
            stats = "{} {} · {:.1f}%".format(s, unit(s, self.cfg.value_label), pct)
        self.pb.add_rect("hdr_box_" + f.letter, x, yy - 0.13, 0.3, 0.26, INK)
        self.T("hdr_letter_" + f.letter, x + 0.15, yy, f.letter, 10.5, color="#ffffff", bold=True, ha="center")
        stats_w = text_width_in(stats, 8.4)
        room = w - 0.4 - stats_w - 0.15
        size = self.fit_size(f.title, 11, room, 7.5, bold=True)
        self.T("hdr_title_" + f.letter, x + 0.4, yy, f.title, size, bold=True)
        if text_width_in(f.title, size, True) <= room:
            self.T("hdr_stats_" + f.letter, x + w, yy, stats, 8.4, color=MUTED, ha="right")

    def scalebar(self, f: Frame) -> None:
        if f.kind == "world":
            return
        mx, my, mw, mh = self.frame_map_rect(f)
        x0, x1, _, _ = f.extent
        m_per_in = (x1 - x0) / mw
        L = nice((x1 - x0) * 0.28)
        bw = L / m_per_in
        bx, by, t = mx + mw * 0.05, my + mh * 0.05, max(0.04, mh * 0.009)
        self.pb.add_rect("sb_a_" + f.letter, bx, by, bw / 2, t, INK, INK, 0.5)
        self.pb.add_rect("sb_b_" + f.letter, bx + bw / 2, by, bw / 2, t, "#ffffff", INK, 0.5)
        km = L / 1000
        txt = "{:g} km  ({:.0f} mi)".format(km, km * 0.621371) if km >= 5 else "{:g} km".format(km)
        self.T("sb_t_" + f.letter, bx + bw + 0.08, by + t / 2, txt, 7)

    # ---------------------------------------------------------------- title + KPIs
    def title(self) -> None:
        cfg, st, total = self.cfg, self.st, self.meta["total"]
        title = cfg.title
        if cfg.mos and cfg.title == "Where the Slots Are: Authorized Positions by Duty Station":
            title = "Where the Slots Are: {} Authorized Positions by Duty Station".format(cfg.mos)
        ty = self.H - 0.95
        top = st[0]
        kpis = [("{:.0f}%".format(top["PCT_CALC"]), "of all {} are at\n{}, {}".format(cfg.value_label, top["NAME"],
                                                                                      top["WHERE"]))]
        th = self.th[0]
        if th["THEATER"] != top["WHERE"]:
            where = "the continental\nUnited States" if th["THEATER"] == "CONUS" else th["THEATER"]
            kpis.append(("{:.0f}%".format(th["PCT"]), "are in {}".format(where)))
        ovs = [s for s in st if s["COUNTRY_C"] != "USA"]
        if ovs:
            n_c = len({s["COUNTRY_C"] for s in ovs})
            so = sum(s["SLOTS"] for s in ovs)
            kpis.append(("{:.0f}%".format(100.0 * so / total), "are overseas\n({} {}, {} countr{})".format(
                so, cfg.value_label, n_c, "y" if n_c == 1 else "ies")))
        x_end = self.W - 0.4
        left = x_end
        for i, (big, small) in enumerate(reversed(kpis)):
            x = x_end - 2.0 - i * 2.55
            self.T("kpi_big_{}".format(i), x, ty + 0.05, big, 27, color=self.colors[-1], bold=True, ha="right")
            self.T("kpi_small_{}".format(i), x + 0.1, ty + 0.05, small, 9.5)
            left = min(left, x - text_width_in(big, 27, True))
        max_w = left - 0.4 - 0.35
        self.T("title", 0.4, ty + 0.28, title, self.fit_size(title, 30, max_w, 18, True), bold=True)
        n_all = len(st) + len(self.meta["excluded"])
        sub = cfg.subtitle
        if sub == "auto":
            ex = ""
            if self.meta["excluded"]:
                ex = "; {} {} 0 {}".format(", ".join(n.title() for n in self.meta["excluded"]),
                                          "has" if len(self.meta["excluded"]) == 1 else "have", cfg.value_label)
            sub = ("{} authorized {} across {} duty stations ({} mapped{}).  Circle size = number of {}.  Color = how "
                   "likely an assignment there is, from that station’s share of all {}.").format(
                       total, cfg.value_label, n_all, len(st), ex, cfg.value_label, cfg.value_label)
        size = self.fit_size(sub, 13, max_w, 9.5)
        if text_width_in(sub, size) > max_w:
            sub = "\n".join(textwrap.wrap(sub, max(40, int(len(sub) * max_w / text_width_in(sub, size))))[:2])
        self.T("subtitle", 0.4, ty - 0.32, sub, size, color=MUTED)

    # ---------------------------------------------------------------- right-hand panel
    def card(self, name, x, y, w, h, title):
        self.pb.add_rect(name, x, y, w, h, CARD, CARD_EDGE, 0.6)
        self.T(name + "_title", x + 0.18, y + h - 0.24, title, 12.5, bold=True)

    def panel(self) -> None:
        cfg = self.cfg
        page = cfg.page
        px = self.W - page.margin_in - page.panel_width_in
        pw = page.panel_width_in
        ytop, ybot = self.H - 1.45, 0.95
        k, kw = self.plan.symbol_k, self.plan.world_k
        reduced = kw < k * 0.999
        lh = 4.3 + (0.22 if reduced else 0)
        ly0 = ytop - lh
        self.card("legend", px, ly0, pw, lh, "How to read the symbols")
        self.T("legend_size", px + 0.18, ly0 + lh - 0.55, "SIZE — number of authorized {}".format(cfg.value_label),
               8.6, color=MUTED, bold=True)
        mx = max(int(s["SLOTS"]) for s in self.st)
        samples = sorted({1, int(nice(max(mx / 8, 1))), int(nice(max(mx / 3, 1))), mx})
        xs = px + 0.4
        for v in samples:
            rr = radius_pt(v, k) / 72
            self.pb.add_circle("legend_c{}".format(v), xs + rr, ly0 + lh - 1.25, rr, "#ffffff", EDGE, 0.8)
            self.T("legend_v{}".format(v), xs + rr, ly0 + lh - 1.87, str(v), 8.5, ha="center")
            xs += 2 * rr + 0.45
        self.T("legend_color", px + 0.18, ly0 + lh - 2.15, "COLOR — likelihood class (share of all {} {})".format(
            self.meta["total"], cfg.value_label), 8.6, color=MUTED, bold=True)
        br = cfg.classes.breaks_pct
        n_cls = len(cfg.classes.labels)
        counts = {}  # type: Dict[int, int]
        for s in self.st:
            counts[int(s["CLS"])] = counts.get(int(s["CLS"]), 0) + 1
        for i, c in enumerate(range(n_cls - 1, -1, -1)):
            yy = ly0 + lh - 2.48 - i * 0.27
            if c == n_cls - 1:
                rng = "{:g}% or more".format(br[c - 1])
            elif c == 0:
                rng = "under {:g}%".format(br[0])
            else:
                rng = "{:g} – {:g}%".format(br[c - 1], br[c])
            rng += " of {}".format(cfg.value_label)
            self.pb.add_circle("legend_cls{}".format(c), px + 0.32, yy, 0.075, self.colors[c], EDGE, 0.7)
            self.T("legend_cls_l{}".format(c), px + 0.52, yy, cfg.classes.labels[c], 9, bold=True)
            self.T("legend_cls_r{}".format(c), px + 1.65, yy, rng, 8.6, color=MUTED)
            n = counts.get(c, 0)
            self.T("legend_cls_n{}".format(c), px + pw - 0.2, yy, "{} station{}".format(n, "" if n == 1 else "s"),
                   8.6, color=MUTED, ha="right")
        cav = ("Share of slots is a proxy for likelihood, not a probability:\nactual assignment also depends on "
               "vacancies, rank, timing\nand the needs of the Army.")
        if reduced:
            cav += "\nWorld-frame circles are drawn at a reduced scale."
        self.T("legend_caveat", px + 0.18, ly0 + 0.15, cav, 7.8, color=MUTED, italic=True, va="bottom")
        # theater bars
        th = self.th
        tht = 0.75 + 0.255 * len(th)
        ty0 = ly0 - 0.25 - tht
        self.card("theaters", px, ty0, pw, tht, "{} by theater".format(cfg.value_label.capitalize()))
        mxs = max(t["SLOTS"] for t in th) or 1
        for i, t in enumerate(th):
            yy = ty0 + tht - 0.62 - i * 0.255
            lab = "Continental U.S." if t["THEATER"] == "CONUS" else t["THEATER"]
            self.T("th_l{}".format(i), px + 1.45, yy, lab, 8.8, ha="right")
            bw = max(2.75 * t["SLOTS"] / mxs, 0.02)
            self.pb.add_rect("th_b{}".format(i), px + 1.55, yy - 0.085, bw, 0.17, self.colors[t["CLS"]], EDGE, 0.4)
            self.T("th_v{}".format(i), px + 1.6 + bw, yy, "{}  ·  {:.1f}%".format(t["SLOTS"], t["PCT"]), 8.4)
        ky0, kh = ybot, ty0 - 0.25 - ybot
        self.card("key", px, ky0, pw, kh, "Station key — ranked by " + cfg.value_label)
        self.key(px, ky0, pw, kh)

    def key(self, px, ky0, pw, kh) -> None:
        st = self.st
        hy = ky0 + kh - 0.55
        avail = hy - 0.22 - ky0 - 0.12
        n = len(st)
        two_col = avail / max(n, 1) < 0.155
        per_col = int(math.ceil(n / 2.0)) if two_col else n
        rowh = min(0.24, avail / max(per_col, 1))
        size = max(7.0, min(9.5, rowh * 72 / 1.25))      # type fills the card, within 7-9.5 pt
        rowh = size * 1.25 / 72
        cap = int(avail / rowh) * (2 if two_col else 1)
        shown = st if n <= cap else st[:cap - 1]
        if two_col:
            per_col = int(math.ceil(len(shown) / 2.0))
        cols_spec = [(px, pw)] if not two_col else [(px, pw / 2), (px + pw / 2, pw / 2)]
        letter_of = {g: f.letter for f in self.plan.frames for g in f.members}
        for ci, (cx, cw) in enumerate(cols_spec):
            rows = shown[ci * per_col:(ci + 1) * per_col]
            if not rows:
                continue
            if not two_col:
                cols = [("rank", cx + 0.42, "#", "right"), ("name", cx + 0.58, "Duty station", "left"),
                        ("where", cx + 2.62, "Where", "left"), ("frame", cx + 3.38, "Frame", "center"),
                        ("slots", cx + 3.98, self.cfg.value_label.capitalize(), "right"),
                        ("pct", cx + 4.52, "%", "right")]
            else:
                cols = [("rank", cx + 0.3, "#", "right"), ("name", cx + 0.42, "Duty station", "left"),
                        ("frame", cx + cw - 0.62, "Fr.", "center"), ("slots", cx + cw - 0.22, "N", "right")]
            max_name = 22 if two_col else 30
            for key, x, head, ha in cols:
                self.T("key_h_{}_{}".format(ci, key), x, hy, head, 8, color=MUTED, bold=True, ha=ha)
                lines = []
                for s in rows:
                    if key == "rank":
                        lines.append(str(s["RANK"]))
                    elif key == "name":
                        nm = str(s.get("LBL_NAME") or s["NAME"])
                        nm = nm if len(nm) <= max_name else nm[:max_name - 1] + "…"
                        r_, g_, b_, _ = rgb(self.colors[int(s["CLS"])])
                        lines.append('<CLR red="{}" green="{}" blue="{}">●</CLR> {}'.format(r_, g_, b_, nm))
                    elif key == "where":
                        wh = SHORT_WHERE.get(str(s["WHERE"]), str(s["WHERE"]))
                        lines.append(wh if len(wh) <= 11 else wh[:10] + "…")
                    elif key == "frame":
                        lines.append(letter_of.get(s["GID"], "–"))
                    elif key == "slots":
                        lines.append(str(s["SLOTS"]))
                    else:
                        lines.append("{:.1f}".format(s["PCT_CALC"]))
                self.T("key_c_{}_{}".format(ci, key), x, hy - 0.2, "\n".join(lines), size, ha=ha, va="top",
                       color=MUTED if key in ("rank", "where", "pct") else INK)
        if len(shown) < n:
            self.T("key_more", px + 0.2, ky0 + 0.1, "+ {} more stations — see duty_station_table.csv".format(
                n - len(shown)), 8, color=MUTED, italic=True, va="bottom")

    # ---------------------------------------------------------------- footer
    def footer(self) -> None:
        cfg, m = self.cfg, self.meta
        s = ["Data: {}; {} {}, {} duty stations{}.".format(m["source_name"], m["total"], cfg.value_label,
                                                            len(self.st) + len(m["excluded"]),
                                                            " ({})".format(cfg.mos) if cfg.mos else "")]
        s.extend(cfg.notes)
        if m["excluded"]:
            s.append("Not mapped (0 {}): {}.".format(cfg.value_label, ", ".join(n.title() for n in m["excluded"])))
        if m["merged"]:
            if len(m["merged"]) <= 6:
                s.append("Duplicate source rows combined for {}.".format(", ".join(m["merged"])))
            else:
                s.append("Several source rows for the same station were combined at {} stations (see N_RECORDS "
                         "in duty_station_table.csv).".format(len(m["merged"])))
        if m["dropped"]:
            s.append("Dropped (no coordinates): {}.".format(", ".join(m["dropped"])))
        if m["fuzzy"]:
            s.append("Name matches accepted by similarity: {}.".format("; ".join(m["fuzzy"])))
        if m["towns"]:
            s.append("Placed at the town centre (GeoNames): {}.".format("; ".join(t.split(" → ")[1] for t in m["towns"])))
        s.append("Point locations are approximate installation-level placements, not surveyed coordinates.")
        s.append("Projections: Equal Earth (world), Albers Equal Area Conic (continental U.S.), Lambert Azimuthal "
                 "Equal Area centred on each other frame — all equal-area, so circle sizes are comparable across frames.")
        for f in self.plan.frames:
            for n in f.notes:
                s.append("Frame {}: {}.".format(f.letter, n))
        s.append("World frame: circles are theater totals at the slot-weighted centre of their stations; black dots "
                 "are the stations. Lettered boxes show where each detail frame sits."
                 + (" Basemap: Natural Earth." if m.get("basemap") else ""))
        date = cfg.date if cfg.date != "auto" else _dt.date.today().strftime("%d %b %Y").lstrip("0")
        s.append("Prepared {}{} with slotmap-tools (ArcPy edition).".format(
            date, " by {}".format(cfg.prepared_by) if cfg.prepared_by else ""))
        width = int((self.W - 0.8) / text_width_in("x", 8.2))
        lines = textwrap.wrap(" ".join(s), width)
        if len(lines) > 4:
            lines = lines[:4]
            lines[-1] = lines[-1][: width - 30] + " … (full notes in qa_report.json)"
        self.T("footer", 0.4, 0.88, "\n".join(lines), 8.2, color=MUTED, va="top")
        self.footer_notes = s


# --------------------------------------------------------------------------- ArcPy side
def _arcpy():
    import arcpy  # noqa
    return arcpy


def open_template(path: Path):
    arcpy = _arcpy()
    aprx = arcpy.mp.ArcGISProject(str(path))
    lyts = aprx.listLayouts(TEMPLATE_LAYOUT) or aprx.listLayouts()
    if not lyts:
        raise TemplateError("{} has no layout. Add a layout named '{}' (see pro_template/TEMPLATE.md).".format(path, TEMPLATE_LAYOUT))
    return aprx, lyts[0]


def export_template_json(path: Path, work: Path) -> dict:
    aprx, lyt = open_template(path)
    out = Path(work) / "_template_layout.pagx"
    lyt.exportToPAGX(str(out))
    del aprx
    return json.loads(out.read_text(encoding="utf-8-sig"))


def check_template(path: Path, work: Path) -> List[str]:
    """Human-readable findings about a template project (used by `slotmap_arcpy check-template`)."""
    doc = export_template_json(path, work)
    pb = PagxBuilder(doc)
    return ["layout: {}".format(doc["layoutDefinition"].get("name")),
            "map frame: {} -> map '{}'".format(pb.mapframe.get("name"), pb.mapdef.get("name")),
            "text sample: {}".format(pb.text.get("name")), "rectangle sample: {}".format(pb.poly.get("name")),
            "CIM version: {}".format(doc.get("version", "?"))]


def _sr(arcpy, wkt: str):
    sr = arcpy.SpatialReference()
    sr.loadFromString(wkt)
    return sr


def _simple(lyr, fill: Optional[str], outline: Optional[str], width: float = 0.5, fill_alpha: float = 100) -> None:
    sym = lyr.symbology
    if not hasattr(sym, "renderer"):
        return
    if sym.renderer.type != "SimpleRenderer":
        sym.updateRenderer("SimpleRenderer")
    s = sym.renderer.symbol
    s.color = {"RGB": rgb(fill or "#ffffff", fill_alpha if fill else 0)}
    s.outlineColor = {"RGB": rgb(outline or "#ffffff", 100 if outline else 0)}
    s.outlineWidth = width if outline else 0
    lyr.symbology = sym


def _unique(lyr, field: str, spec: Dict[str, Tuple[str, float, str, float]]) -> None:
    """spec: value -> (fill hex, size pt, outline hex, outline width)."""
    sym = lyr.symbology
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = [field]
    for grp in sym.renderer.groups:
        for itm in grp.items:
            v = str(itm.values[0][0]) if itm.values and itm.values[0] else ""
            if v not in spec:
                continue
            fill, size, edge, ew = spec[v]
            itm.symbol.color = {"RGB": rgb(fill)}
            itm.symbol.size = float(size)
            itm.symbol.outlineColor = {"RGB": rgb(edge)}
            itm.symbol.outlineWidth = float(ew)
    lyr.symbology = sym


def _labels(arcpy, lyr, expression: str, where: Optional[str], size: float, warnings: List[str],
            bold: bool = False) -> None:
    lyr.showLabels = True
    try:
        cim = lyr.getDefinition("V2")
        lc = cim.labelClasses[0]
        lc.expressionEngine = "Arcade"
        lc.expression = expression
        if where:
            lc.whereClause = where
        lc.visibility = True
        ts = lc.textSymbol.symbol
        ts.height = float(size)
        if bold:
            ts.fontStyleName = "Bold"
        try:
            halo = arcpy.cim.CreateCIMObjectFromClassName("CIMPolygonSymbol", "V2")
            fill = arcpy.cim.CreateCIMObjectFromClassName("CIMSolidFill", "V2")
            col = arcpy.cim.CreateCIMObjectFromClassName("CIMRGBColor", "V2")
            col.values = [255, 255, 255, 85]
            fill.color = col
            halo.symbolLayers = [fill]
            ts.haloSymbol = halo
            ts.haloSize = 1.0
        except Exception:
            pass
        mp = getattr(lc, "maplexLabelPlacementProperties", None)
        if mp is not None:
            mp.neverRemoveLabel = True
        cim.labelVisibility = True
        lyr.setDefinition(cim)
    except Exception as e:
        warnings.append("label settings for {}: {}: {}".format(lyr.name, type(e).__name__, e))
        try:
            lc = lyr.listLabelClasses()[0]
            lc.expression = expression
            if where:
                lc.SQLQuery = where
        except Exception:
            pass


LABEL_EXPR = ('Concatenate(["<BOL>" + $feature.LBL_NAME + "</BOL>", "<FNT size=\'6.2\'><CLR red=\'95\' '
              'green=\'99\' blue=\'107\'>" + $feature.DETAIL + "</CLR></FNT>"], TextFormatting.NewLine)')


def _sym_spec(points: List[dict], colors: List[str]) -> Dict[str, Tuple[str, float, str, float]]:
    spec = {}
    for p in points:
        k = p["SYMKEY"]
        if k == "dot":
            spec[k] = (INK, 2.6, INK, 0.0)
        else:
            d = float(k.split("_d")[1])
            spec[k] = (colors[int(p["CLS"])], max(d, 2.0), EDGE, 0.7)
    return spec


def render_layout(plan: Plan, st: List[dict], th: List[dict], cfg: SheetConfig, meta: dict, gdb: str,
                  template: Path, out_dir: Path, slug: str, data_dir: Optional[Path],
                  sheet_pts: List[dict], theater_pts: List[dict]) -> dict:
    """Build, populate and export the layout. Returns {'files': [...], 'footer_notes': [...], 'warnings': [...]}"""
    arcpy = _arcpy()
    out_dir = Path(out_dir)
    warnings = []  # type: List[str]
    doc = export_template_json(template, out_dir)
    pb = PagxBuilder(doc)
    sb = SheetBuilder(pb, plan, st, th, cfg, meta)
    tag = re.sub(r"[^A-Za-z0-9]+", "_", slug)
    lname = "Slotmap {}".format(cfg.mos or slug)
    gidx = {s["GID"]: s for s in st}
    # maps + frames (+ headers and scale bars)
    for f in plan.frames:
        uri = "CIMPATH=map/slotmap_{}_{}.xml".format(tag.lower(), f.letter.lower())
        wkt = f.projection().wkt()
        pb.add_map("{} {}".format(cfg.mos or slug, f.letter), uri, wkt, f.extent)
        x, y, w, h = sb.frame_map_rect(f)
        pb.add_mapframe("MF_" + f.letter, uri, x, y, w, h)
        vis = list(st) if f.kind == "world" else [gidx[p["GID"]] for p in sheet_pts if p["FRAME"] == f.letter]
        sb.header(f, vis)
        sb.scalebar(f)
    sb.title()
    sb.panel()
    sb.footer()
    pagx = out_dir / "{}_layout.pagx".format(slug)
    pagx.write_text(json.dumps(pb.finish(lname, "CIMPATH=layout/slotmap_{}.xml".format(tag.lower()),
                                         cfg.page.width_in, cfg.page.height_in)), encoding="utf-8")
    aprx, _ = open_template(template)
    aprx.importDocument(str(pagx))
    lyts = aprx.listLayouts(lname)
    if not lyts:
        raise TemplateError("Importing {} did not create layout '{}'.".format(pagx, lname))
    lyt = lyts[0]
    mfs = {mf.name: mf for mf in lyt.listElements("MAPFRAME_ELEMENT")}
    pts_fc = os.path.join(gdb, "sheet_points")
    ext_fc = os.path.join(gdb, "frame_extents")
    th_fc = os.path.join(gdb, "theater_points")
    spec = _sym_spec(sheet_pts, cfg.classes.colors)
    th_spec = _sym_spec(theater_pts, cfg.classes.colors)
    has_bm = B.available(data_dir)
    for f in plan.frames:
        mf = mfs.get("MF_" + f.letter)
        if mf is None:
            warnings.append("map frame MF_{} missing after import".format(f.letter))
            continue
        m = mf.map
        m.spatialReference = _sr(arcpy, f.projection().wkt())
        if has_bm:
            _basemap(m, f, data_dir, warnings)
        if f.kind == "world":
            lyr = m.addDataFromPath(ext_fc)
            lyr.name = "Frame boxes"
            lyr.definitionQuery = "PARENT = 'world'"
            _simple(lyr, None, INK, 1.0)
            _labels(arcpy, lyr, "$feature.LETTER", None, 8.6, warnings, bold=True)
            lyr = m.addDataFromPath(th_fc)
            lyr.name = "Theaters"
            _unique(lyr, "SYMKEY", th_spec)
            _labels(arcpy, lyr, LABEL_EXPR, None, 7.5, warnings)
            lyr = m.addDataFromPath(pts_fc)
            lyr.name = "Stations"
            lyr.definitionQuery = "FRAME = 'W'"
            _unique(lyr, "SYMKEY", spec)
        else:
            children = [c for c in plan.frames if c.parent == f.fid]
            if children:
                lyr = m.addDataFromPath(ext_fc)
                lyr.name = "Detail frame boxes"
                lyr.definitionQuery = "PARENT = '{}'".format(f.fid.replace("'", "''"))
                _simple(lyr, None, INK, 1.0)
                _labels(arcpy, lyr, "$feature.LETTER", None, 8.6, warnings, bold=True)
            lyr = m.addDataFromPath(pts_fc)
            lyr.name = "Stations"
            lyr.definitionQuery = "FRAME = '{}'".format(f.letter)
            _unique(lyr, "SYMKEY", {k: v for k, v in spec.items() if k != "dot"})
            _labels(arcpy, lyr, LABEL_EXPR, "OWNED = 1", 7.5, warnings)
        x0, x1, y0, y1 = f.extent
        try:
            mf.camera.setExtent(arcpy.Extent(x0, y0, x1, y1))
        except Exception as e:
            warnings.append("extent for frame {}: {}".format(f.letter, e))
    files = []
    base = out_dir / "{}_sheet".format(slug)
    if "pdf" in cfg.outputs:
        lyt.exportToPDF(str(base.with_suffix(".pdf")), resolution=300)
        files.append(str(base.with_suffix(".pdf")))
    if "png" in cfg.outputs:
        lyt.exportToPNG(str(base.with_suffix(".png")), resolution=cfg.page.dpi)
        files.append(str(base.with_suffix(".png")))
    crops = out_dir / "crops"
    crops.mkdir(exist_ok=True)
    for f in plan.frames:
        mf = mfs.get("MF_" + f.letter)
        if mf is None:
            continue
        try:
            p = crops / "frame_{}_{}.png".format(f.letter, f.fid.replace("/", "_"))
            mf.exportToPNG(str(p), resolution=150)
        except Exception as e:
            warnings.append("crop {}: {}".format(f.letter, e))
    try:
        lyt.exportToPNG(str(crops / "overview.png"), resolution=100)
    except Exception as e:
        warnings.append("overview: {}".format(e))
    if "aprx" in cfg.outputs:
        p = out_dir / "{}.aprx".format(slug)
        aprx.saveACopy(str(p))
        files.append(str(p))
    del aprx
    return {"files": files, "footer_notes": sb.footer_notes, "warnings": warnings, "pagx": str(pagx)}


def _basemap(m, f: Frame, data_dir: Path, warnings: List[str]) -> None:
    try:
        world = f.kind == "world"
        local = f.kind == "detail" or f.halfwidth_km < 150
        cpath = str(B.shp(data_dir, "world" if world else "countries"))
        focus = [a for a in f.focus_a3 if a]
        lyr = m.addDataFromPath(cpath)
        lyr.name = "Countries"
        _simple(lyr, LAND_OTHER if focus else LAND, BORDER, 0.8)
        if focus:
            lyr = m.addDataFromPath(cpath)
            lyr.name = "Focus countries"
            lyr.definitionQuery = "ADM0_A3 IN ({})".format(", ".join("'{}'".format(a) for a in focus))
            _simple(lyr, LAND, BORDER, 0.8)
        if local and B.shp(data_dir, "urban"):
            lyr = m.addDataFromPath(str(B.shp(data_dir, "urban")))
            lyr.name = "Urban areas"
            _simple(lyr, URBAN, None)
        lyr = m.addDataFromPath(str(B.shp(data_dir, "lakes")))
        lyr.name = "Lakes"
        if not local:
            lyr.definitionQuery = "scalerank <= 2"
        _simple(lyr, WATER, None)
        if not world:
            lyr = m.addDataFromPath(str(B.shp(data_dir, "admin1")))
            lyr.name = "States / provinces"
            if f.proj == "albers_conus":
                lyr.definitionQuery = "adm0_a3 = 'USA'"
            _simple(lyr, None, ADMIN1, 0.45)
    except Exception as e:
        warnings.append("basemap for frame {}: {}: {}".format(f.letter, type(e).__name__, e))
