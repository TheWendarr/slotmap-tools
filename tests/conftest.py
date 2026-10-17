import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example_csv():
    return ROOT / "examples" / "example_25X.csv"


@pytest.fixture
def no_basemap(tmp_path, monkeypatch):
    """Render without Natural Earth so tests run offline."""
    d = tmp_path / "empty_basemap"
    d.mkdir()
    monkeypatch.setenv("SLOTMAP_BASEMAP", str(d))
    return d
