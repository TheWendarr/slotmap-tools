"""Stage 7 - checks that need no rendering (pure Python) + the report writer."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional

from .plan import Plan, radius_pt, scale_m_per_pt


def _check(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def display_xy(frame, s) -> tuple:
    lon, lat = frame.display.get(s["GID"], (float(s["LON"]), float(s["LAT"])))
    return frame.projection().fwd(lon, lat)


def run_checks(plan: Plan, st: List[Dict[str, object]], total: int, stated_total: Optional[int],
               not_mapped_slots: int, dropped: List[str], fuzzy: List[str]) -> List[dict]:
    out = []
    mapped = sum(int(s["SLOTS"]) for s in st)
    ok = mapped + not_mapped_slots == total and (stated_total is None or stated_total == total)
    out.append(_check("totals", "pass" if ok else "fail",
                      "mapped {} + not mapped (0-slot or dropped) {} = {}; table total {}; stated total {}".format(
                          mapped, not_mapped_slots, mapped + not_mapped_slots, total, stated_total)))
    owned = [g for f in plan.frames for g in f.members]
    dup = sorted({g for g in owned if owned.count(g) > 1})
    missing = sorted({s["GID"] for s in st} - set(owned))
    out.append(_check("ownership", "pass" if not dup and not missing else "fail",
                      {"labelled_twice": dup, "not_labelled": missing}))
    gidx = {s["GID"]: s for s in st}
    outside = []
    for f in plan.frames:
        if f.kind == "world":
            continue
        x0, x1, y0, y1 = f.extent
        for g in f.members:
            x, y = display_xy(f, gidx[g])
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                outside.append("{}:{}".format(f.letter, g))
    out.append(_check("inside_frame", "pass" if not outside else "fail", outside))
    worst = {}
    for f in plan.frames:
        if f.kind == "world" or len(f.members) < 2:
            continue
        s_ = scale_m_per_pt(f)
        pts = [(display_xy(f, gidx[g]), radius_pt(gidx[g]["SLOTS"], plan.symbol_k)) for g in f.members]
        mn = min(math.hypot(a[0][0] - b[0][0], a[0][1] - b[0][1]) / s_ - a[1] - b[1]
                 for i, a in enumerate(pts) for b in pts[i + 1:])
        worst[f.letter] = round(mn, 1)
    bad = [k for k, v in worst.items() if v < 0]
    out.append(_check("symbol_separation", "pass" if not bad else "warn", {"min_gap_pt_by_frame": worst}))
    displaced = ["{}: {}".format(f.letter, n) for f in plan.frames for n in f.notes]
    out.append(_check("displacement", "info" if displaced else "pass", displaced))
    out.append(_check("resolution", "warn" if dropped else ("info" if fuzzy else "pass"),
                      {"fuzzy_accepted": fuzzy, "dropped_unresolved": dropped}))
    if plan.warnings:
        out.append(_check("planner", "warn", plan.warnings))
    return out


def write_report(path: Path, checks: List[dict], extra: Dict) -> dict:
    status = "fail" if any(c["status"] == "fail" for c in checks) else (
        "warn" if any(c["status"] == "warn" for c in checks) else "pass")
    rep = dict(status=status, checks=checks, **extra)
    Path(path).write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return rep
