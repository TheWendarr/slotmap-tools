"""Sheet configuration: everything a person or an LLM is expected to tweak lives here."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass
class ClassScheme:
    """Likelihood classes from a station's share (%) of all slots. len(labels) == len(breaks_pct) + 1."""
    breaks_pct: List[float] = field(default_factory=lambda: [2.0, 5.0, 10.0])
    labels: List[str] = field(default_factory=lambda: ["Less likely", "Possible", "Likely", "Most likely"])
    colors: List[str] = field(default_factory=lambda: ["#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"])

    def validate(self) -> None:
        if len(self.labels) != len(self.breaks_pct) + 1 or len(self.colors) != len(self.labels):
            raise ValueError("classes: need len(labels) == len(colors) == len(breaks_pct) + 1")
        if sorted(self.breaks_pct) != list(self.breaks_pct):
            raise ValueError("classes.breaks_pct must be ascending")


@dataclass
class PageConfig:
    width_in: float = 24.0          # ARCH C landscape
    height_in: float = 18.0
    margin_in: float = 0.4
    panel_width_in: float = 5.25    # right-hand legend / key column
    dpi: int = 200


@dataclass
class PlanConfig:
    max_frames: int = 13                 # including the world frame (layout holds up to 13)
    region_merge_km: float = 900.0       # overseas countries closer than this share a frame
    cluster_merge_pt: float = 30.0       # colliding clusters closer than this share one detail frame
    cluster_gap_pt: float = 1.0          # symbols closer than this edge-to-edge gap (pt, at frame scale) need a detail frame
    region_min_halfwidth_km: float = 30.0  # smallest half width for a multi-station region frame
    min_halfwidth_km: float = 15.0       # smallest detail-frame half width before displacement kicks in
    single_halfwidth_km: float = 60.0    # half width for a frame that holds one station
    pad_frac: float = 0.22               # padding around station extent (fraction of span) for labels
    max_depth: int = 2                   # region -> detail (2). 3 would allow detail-of-detail.
    displace_gap_pt: float = 2.0         # visible gap enforced between near-coincident symbols


@dataclass
class SheetConfig:
    title: str = "Where the Slots Are: Authorized Positions by Duty Station"
    subtitle: str = "auto"               # "auto" writes totals + how to read size/colour
    mos: str = ""                        # e.g. "25D" - goes into title tag and file names
    prepared_by: str = ""
    date: str = "auto"                   # "auto" = today, e.g. "17 Oct 2026"
    value_label: str = "slots"           # what one unit is called ("slots", "authorizations", "billets")
    exclude_zero: bool = True            # stations with 0 slots are listed in the footer, not mapped
    accept_fuzzy: bool = False           # fuzzy gazetteer matches must be confirmed unless True
    fuzzy_auto_accept: float = 0.92      # ...except matches at least this similar, which are accepted and footnoted
    use_places: bool = True              # place "(CITY)" names, and otherwise-unknown town names, at the town centre
    symbol_max_diameter_pt: float = 36.0 # diameter of the largest station circle
    world_max_diameter_pt: float = 64.0  # cap for theater circles on the world frame
    fonts: List[str] = field(default_factory=lambda: ["Inter", "Arial", "Helvetica", "DejaVu Sans"])
    outputs: List[str] = field(default_factory=lambda: ["pdf", "png"])
    classes: ClassScheme = field(default_factory=ClassScheme)
    page: PageConfig = field(default_factory=PageConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    # Overrides keyed by frame id (see frame_plan.json) -> {"title": str, "halfwidth_km": float, "center": [lon, lat]}
    frame_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Overrides keyed by station id or raw station name -> {"dir": "E|W|N|S|NE|NW|SE|SW", "dx": pt, "dy": pt, "name": str}
    label_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Extra footer sentences.
    notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path: Optional[Path]) -> "SheetConfig":
        if path is None or not Path(path).exists():
            cfg = cls()
        else:
            text = Path(path).read_text(encoding="utf-8")
            if str(path).lower().endswith((".yaml", ".yml")):
                if yaml is None:
                    raise RuntimeError("PyYAML is required to read YAML configs: pip install pyyaml")
                raw = yaml.safe_load(text) or {}
            else:
                raw = json.loads(text)
            cfg = cls.from_dict(raw)
        cfg.classes.validate()
        return cfg

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SheetConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        kw = dict(raw)
        if "classes" in kw:
            kw["classes"] = ClassScheme(**kw["classes"])
        if "page" in kw:
            kw["page"] = PageConfig(**kw["page"])
        if "plan" in kw:
            kw["plan"] = PlanConfig(**kw["plan"])
        return cls(**kw)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> None:
        path = Path(path)
        if path.suffix.lower() in (".yaml", ".yml"):
            if yaml is None:
                raise RuntimeError("PyYAML is required to write YAML configs")
            path.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True), encoding="utf-8")
        else:
            path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
