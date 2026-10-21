"""Stage 3 - merge, share, classify, group and rank (pure Python)."""
from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict
from typing import Dict, List

from .gazetteer import COUNTRY_DISPLAY

US_TERRITORIES = {"PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands", "AS": "American Samoa",
                  "MP": "Northern Mariana Islands"}
Station = Dict[str, object]


def country_display(c: str) -> str:
    return COUNTRY_DISPLAY.get(c, c.title())


def assign_theater(country_c: str, postal: str) -> str:
    if country_c == "USA":
        if postal == "AK":
            return "Alaska"
        if postal == "HI":
            return "Hawaii"
        if postal in US_TERRITORIES:
            return US_TERRITORIES[postal]
        return "CONUS"
    return country_display(country_c).replace("S. Korea", "Korea")


def classify(pct: float, breaks: List[float]) -> int:
    return bisect_right(breaks, pct) if pct > 0 else 0


def merge_duplicates(rows: List[Dict[str, object]]) -> List[Station]:
    """One station per resolved installation (GID), in first-seen order."""
    out = OrderedDict()  # type: OrderedDict
    for r in rows:
        g = r["GID"]
        if g not in out:
            out[g] = dict(GID=g, STATION=r["STATION"], NAME=r["NAME"], LAT=r["LAT"], LON=r["LON"], SLOTS=0,
                          PCT_SRC=None, N_RECORDS=0, SRC_ROWS=[], COUNTRY_C=r["COUNTRY_C"], POSTAL=r["POSTAL"],
                          RES_STATUS=r["RES_STATUS"], COORD_QUALITY=r["COORD_QUALITY"])
        s = out[g]
        s["SLOTS"] += int(r["SLOTS"])
        if r.get("PCT_SRC") is not None:
            s["PCT_SRC"] = (s["PCT_SRC"] or 0.0) + float(r["PCT_SRC"])
        s["N_RECORDS"] += 1
        s["SRC_ROWS"].append(str(r["SRC_ROW"]))
    for s in out.values():
        s["SRC_ROWS"] = ",".join(s["SRC_ROWS"])
    return list(out.values())


def enrich(st: List[Station], total: int, breaks: List[float]) -> List[Station]:
    for s in st:
        s["PCT_CALC"] = round(100.0 * s["SLOTS"] / total, 1) if total else 0.0
        s["THEATER"] = assign_theater(s["COUNTRY_C"], s["POSTAL"])
        s["GRP"] = s["POSTAL"] if s["COUNTRY_C"] == "USA" and s["POSTAL"] else country_display(s["COUNTRY_C"])
        s["WHERE"] = s["GRP"]
        s["CLS"] = classify(100.0 * s["SLOTS"] / total if total else 0, breaks)
    return rank(st)


def rank(st: List[Station]) -> List[Station]:
    st = sorted(st, key=lambda s: (-s["SLOTS"], s["NAME"]))
    for i, s in enumerate(st, 1):
        s["RANK"] = i
    return st


def group_totals(st: List[Station], total: int) -> List[Dict[str, object]]:
    g = OrderedDict()  # type: OrderedDict
    for s in st:
        k = (s["GRP"], s["THEATER"])
        e = g.setdefault(k, {"GRP": s["GRP"], "THEATER": s["THEATER"], "SLOTS": 0, "STATIONS": 0})
        e["SLOTS"] += s["SLOTS"]
        e["STATIONS"] += 1
    out = list(g.values())
    for e in out:
        e["PCT"] = round(100.0 * e["SLOTS"] / total, 1) if total else 0.0
    return sorted(out, key=lambda e: -e["SLOTS"])


def theater_totals(st: List[Station], total: int, breaks: List[float]) -> List[Dict[str, object]]:
    """Slot-weighted centre per theater (0-slot stations carry no weight)."""
    import math
    acc = OrderedDict()  # type: OrderedDict
    for s in st:
        e = acc.setdefault(s["THEATER"], {"THEATER": s["THEATER"], "SLOTS": 0, "STATIONS": 0,
                                         "x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0})
        w = max(s["SLOTS"], 0) + 1e-9
        la, lo = math.radians(float(s["LAT"])), math.radians(float(s["LON"]))
        e["x"] += w * math.cos(la) * math.cos(lo)
        e["y"] += w * math.cos(la) * math.sin(lo)
        e["z"] += w * math.sin(la)
        e["SLOTS"] += s["SLOTS"]
        e["STATIONS"] += 1
    out = []
    for e in acc.values():
        lon = math.degrees(math.atan2(e["y"], e["x"]))
        lat = math.degrees(math.atan2(e["z"], math.hypot(e["x"], e["y"])))
        pct = 100.0 * e["SLOTS"] / total if total else 0.0
        out.append({"THEATER": e["THEATER"], "SLOTS": e["SLOTS"], "STATIONS": e["STATIONS"], "LON": lon,
                    "LAT": lat, "PCT": pct, "CLS": classify(pct, breaks)})
    return sorted(out, key=lambda e: -e["SLOTS"])
