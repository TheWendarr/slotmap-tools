"""Stage 7 - prove the sheet is right, and leave evidence a person or an LLM can look at."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from .layout import HEADER_IN
from .plan import GEO, Plan, crs_for, radius_pt, scale_m_per_pt


def _check(name, status, detail):
    return {"check": name, "status": status, "detail": detail}


def run_checks(plan: Plan, st: pd.DataFrame, total: int, stated_total, excluded_slots: int,
               dropped: List[str], fuzzy: List[str], render_info: Dict) -> List[dict]:
    out = []
    mapped = int(st.SLOTS.sum())
    ok = mapped + excluded_slots == total and (stated_total is None or stated_total == total)
    out.append(_check("totals", "pass" if ok else "fail",
                      f"mapped {mapped} + not mapped (0-slot or dropped) {excluded_slots} = {mapped + excluded_slots}; table total {total}; "
                      f"stated total {stated_total}"))
    owned = [g for f in plan.frames for g in f.members]
    dup = sorted({g for g in owned if owned.count(g) > 1})
    missing = sorted(set(st.GID) - set(owned))
    out.append(_check("ownership", "pass" if not dup and not missing else "fail",
                      {"labelled_twice": dup, "not_labelled": missing}))
    outside = []
    for fid, info in render_info.get("frames", {}).items():
        lost = sorted(set(info["owned"]) - set(info["owned_visible"]))
        outside += [f"{info['letter']}:{g}" for g in lost]
    out.append(_check("inside_frame", "pass" if not outside else "fail", outside))
    # separation in owning frames (after displacement)
    worst = []
    for f in plan.frames:
        if f.kind == "world" or len(f.members) < 2:
            continue
        proj = crs_for(f)
        sub = st.set_index("GID").loc[f.members]
        ll = np.array([f.display.get(g, (r.LON, r.LAT)) for g, r in sub.iterrows()], float)
        p = proj.transform_points(GEO, ll[:, 0], ll[:, 1])[:, :2] / scale_m_per_pt(f)
        r = [radius_pt(s, plan.symbol_k) for s in sub.SLOTS]
        mn = min(float(np.hypot(*(p[i] - p[j]))) - r[i] - r[j] for i in range(len(p)) for j in range(i + 1, len(p)))
        worst.append((f.letter, round(mn, 1)))
    bad = [w for w in worst if w[1] < 0]
    out.append(_check("symbol_separation", "pass" if not bad else "warn",
                      {"min_gap_pt_by_frame": dict(worst)}))
    displaced = [f"{f.letter}: {n}" for f in plan.frames for n in f.notes]
    out.append(_check("displacement", "info" if displaced else "pass", displaced))
    lab_over, lab_out = [], []
    for fid, info in render_info.get("frames", {}).items():
        lab_over += [f"{info['letter']}:{k}" for k in info.get("overlapping", []) if not k.startswith(("place:", "area:"))]
        lab_out += [f"{info['letter']}:{k}" for k in info.get("outside", []) if not k.startswith(("place:", "area:"))]
    out.append(_check("labels", "pass" if not lab_over and not lab_out else "warn",
                      {"overlapping": lab_over, "outside_frame": lab_out}))
    out.append(_check("resolution", "warn" if dropped else ("info" if fuzzy else "pass"),
                      {"fuzzy_accepted": fuzzy, "dropped_unresolved": dropped}))
    if plan.warnings:
        out.append(_check("planner", "warn", plan.warnings))
    return out


def write_crops(png: Path, plan: Plan, out_dir: Path, dpi: int) -> List[str]:
    """7.5 One PNG per frame (+ panel) so a reviewer can zoom in without opening the whole sheet."""
    try:
        from PIL import Image
    except Exception:
        return []
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(png)
    W, H = plan.page
    sx, sy = im.size[0] / W, im.size[1] / H
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in plan.frames:
        x, y, w, h = f.slot
        box = (int((x - 0.1) * sx), int((H - y - h - 0.05) * sy), int((x + w + 0.1) * sx), int((H - y + 0.1) * sy))
        p = out_dir / f"frame_{f.letter}_{f.fid.replace('/', '_')}.png"
        im.crop(box).save(p)
        paths.append(str(p))
    p = out_dir / "panel.png"
    im.crop((int((W - 5.8) * sx), 0, im.size[0], im.size[1])).save(p)
    paths.append(str(p))
    p = out_dir / "overview.png"
    im.resize((2400, int(2400 * im.size[1] / im.size[0]))).save(p)
    paths.append(str(p))
    return paths


def write_report(path: Path, checks: List[dict], extra: Dict) -> dict:
    status = "fail" if any(c["status"] == "fail" for c in checks) else (
        "warn" if any(c["status"] == "warn" for c in checks) else "pass")
    rep = {"status": status, "checks": checks, **extra}
    Path(path).write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return rep
