"""6.11 Wireframe of a frame plan, for design review before the full render."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .config import SheetConfig  # noqa: E402
from .layout import HEADER_IN  # noqa: E402
from .plan import Plan  # noqa: E402
from .render import setup_fonts  # noqa: E402

NOTE = "#2563eb"
PRINCIPLES = [
    "World frame first: answers “where on the globe”; lettered boxes tie every detail frame back to it.",
    "Frame letters run in reading order and appear on boxes, frame headers and the station key.",
    "Detail frames are created only where symbols would collide at the parent's scale.",
    "The frame with the most stations gets the largest slot (visual hierarchy).",
    "One symbol size scale on the whole sheet: area proportional to slots, so circles compare across frames.",
    "Redundant encoding: size and color both carry the share of slots (color-blind-safe ramp).",
    "Labels are placed automatically, with leader lines only where space is tight.",
    "Muted basemap (figure–ground); warm symbols are the only saturated color.",
    "Equal-area projections in every frame so circle areas are not distorted.",
    "Footer is written from the data: exclusions, merges, displacements, accuracy.",
]


def render_mockup(plan: Plan, st: pd.DataFrame, cfg: SheetConfig, out_path: Path) -> Path:
    setup_fonts(cfg.fonts)
    W, H = plan.page
    fig = plt.figure(figsize=(W, H), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    names = st.set_index("GID").NAME
    ax.text(0.4, H - 0.67, cfg.title, fontsize=26, weight="bold", color="#1f2328", va="center")
    ax.text(W - 0.4, H - 0.67, "LAYOUT MOCKUP · frame plan, not the final map", fontsize=13, color=NOTE,
            ha="right", weight="bold", va="center")
    for f in plan.frames:
        x, y, w, h = f.slot
        ax.add_patch(Rectangle((x, y), w, h - HEADER_IN, fc="#eceae4", ec="#333", lw=0.8))
        ax.add_patch(Rectangle((x, y + h - HEADER_IN + 0.04), 0.3, 0.26, fc="#1f2328", ec="none"))
        ax.text(x + 0.15, y + h - HEADER_IN + 0.17, f.letter, color="white", ha="center", va="center",
                fontsize=10, weight="bold")
        ax.text(x + 0.4, y + h - HEADER_IN + 0.17, f.title, fontsize=10.5, weight="semibold", va="center")
        info = f"{f.kind} · {f.proj} · half-width {f.halfwidth_km:,.0f} km" if f.kind != "world" else "theater totals"
        ax.text(x + 0.12, y + h - HEADER_IN - 0.18, info, fontsize=7.5, color="#5f636b", va="top")
        lines = [names[g] for g in f.members][:14]
        if len(f.members) > 14:
            lines.append(f"+{len(f.members) - 14} more")
        ax.text(x + 0.12, y + h - HEADER_IN - 0.42, "\n".join(lines), fontsize=7.5, color="#1f2328", va="top",
                linespacing=1.35)
        if f.parent and f.parent != "world":
            ax.text(x + w - 0.1, y + 0.1, f"detail of {next(p.letter for p in plan.frames if p.fid == f.parent)}",
                    fontsize=7.5, color=NOTE, ha="right", va="bottom", style="italic")
    px = W - cfg.page.margin_in - cfg.page.panel_width_in
    ax.add_patch(Rectangle((px, 0.95), cfg.page.panel_width_in, H - 2.4, fc="#f7f6f2", ec="#d9d6cd"))
    ax.text(px + 0.2, H - 1.75, "Legend · theater bars · station key", fontsize=11, weight="bold", va="top")
    ax.text(px + 0.2, H - 2.3, "Design principles applied", fontsize=10, weight="bold", color=NOTE, va="top")
    for i, p in enumerate(PRINCIPLES):
        ax.text(px + 0.2, H - 2.7 - i * 0.62, f"{i + 1}.", fontsize=9, color=NOTE, weight="bold", va="top")
        ax.text(px + 0.5, H - 2.7 - i * 0.62, _wrap(p, 52), fontsize=8.6, color="#1f2328", va="top", linespacing=1.3)
    if plan.warnings:
        ax.text(0.4, 0.55, "Planner warnings: " + " | ".join(plan.warnings), fontsize=8.5, color="#b45309")
    fig.savefig(out_path, dpi=100)
    plt.close(fig)
    return Path(out_path)


def _wrap(s: str, n: int) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(s, n))
