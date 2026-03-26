from dataclasses import dataclass, field
from pathlib import Path

import yaml

VALID_BUTTONS = frozenset({
    "A", "B", "X", "Y", "L", "R", "Start", "Select",
    "Up", "Down", "Left", "Right",
})

DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "mappings.yaml"


@dataclass
class Settings:
    hold_ms: int = 80
    release_gap_ms: int = 20


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    mappings: dict[str, list[str]] = field(default_factory=dict)


def load(path: Path = DEFAULT_CONFIG) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    s = raw.get("settings", {})
    settings = Settings(
        hold_ms=int(s.get("hold_ms", 80)),
        release_gap_ms=int(s.get("release_gap_ms", 20)),
    )

    mappings = {}
    for key, buttons in raw.get("mappings", {}).items():
        unknown = [b for b in buttons if b not in VALID_BUTTONS]
        if unknown:
            raise ValueError(f"Unknown button(s) for {key!r}: {unknown}")
        mappings[str(key)] = buttons

    return Config(settings=settings, mappings=mappings)
