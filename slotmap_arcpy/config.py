"""Sheet configuration (JSON - the standard library has no YAML reader).

Same keys as the main branch's sheet.yaml, so a config can be converted one to one. Unknown keys are an error.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    max_frames: int = 13
    region_merge_km: float = 900.0
    cluster_merge_pt: float = 30.0
    cluster_gap_pt: float = 1.0
    region_min_halfwidth_km: float = 30.0
    min_halfwidth_km: float = 15.0
    single_halfwidth_km: float = 60.0
    pad_frac: float = 0.22
    max_depth: int = 2
    displace_gap_pt: float = 2.0


@dataclass
class SheetConfig:
    title: str = "Where the Slots Are: Authorized Positions by Duty Station"
    subtitle: str = "auto"
    mos: str = ""
    prepared_by: str = ""
    date: str = "auto"
    value_label: str = "slots"
    exclude_zero: bool = True
    accept_fuzzy: bool = False
    fuzzy_auto_accept: float = 0.92
    use_places: bool = True
    symbol_max_diameter_pt: float = 36.0
    world_max_diameter_pt: float = 64.0
    fonts: List[str] = field(default_factory=lambda: ["Arial"])      # first entry is used for layout text
    outputs: List[str] = field(default_factory=lambda: ["pdf", "png", "aprx"])
    classes: ClassScheme = field(default_factory=ClassScheme)
    page: PageConfig = field(default_factory=PageConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    frame_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    label_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # {"gid": {"name": "..."}}
    notes: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[Path]) -> "SheetConfig":
        if path is None or not Path(path).exists():
            cfg = cls()
        else:
            cfg = cls.from_dict(read_json(Path(path)))
        cfg.classes.validate()
        return cfg

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SheetConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError("Unknown config keys: {}".format(sorted(unknown)))
        kw = copy.deepcopy(raw)
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
        write_json(Path(path), self.to_dict())


def read_json(path: Path) -> Dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8-sig")
    if str(path).lower().endswith((".yaml", ".yml")):
        raise ValueError("{}: this edition reads JSON configs (sheet.json). Convert the YAML file to JSON."
                         .format(path))
    return json.loads(text) if text.strip() else {}


def write_json(path: Path, data: Any) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif k == "notes" and isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + [n for n in v if n not in out[k]]
        else:
            out[k] = copy.deepcopy(v)
    return out
