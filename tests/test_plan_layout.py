from slotmap.aggregate import classify
from slotmap.config import PageConfig, SheetConfig
from slotmap.layout import assign, capacity, letters_in_reading_order


def test_classify_breaks():
    b = [2, 5, 10]
    assert [classify(p, b) for p in (0.4, 2.0, 4.9, 5.4, 32.3)] == [0, 1, 1, 2, 3]


def test_layout_fills_capacity_without_overlap():
    page = PageConfig()
    frames = [dict(fid=f"f{i}", priority=20 - i, kind="region") for i in range(capacity())]
    slots = assign(frames, page)
    rects = list(slots.values())
    for i, a in enumerate(rects):
        assert a.x >= 0 and a.x + a.w <= page.width_in - page.panel_width_in
        for b in rects[i + 1:]:
            ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
            oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
            assert ox <= 1e-6 or oy <= 1e-6
    letters = letters_in_reading_order(slots)
    assert sorted(letters.values()) == [chr(65 + i) for i in range(capacity())]


def test_config_roundtrip(tmp_path):
    c = SheetConfig(mos="25D")
    c.save(tmp_path / "s.yaml")
    assert SheetConfig.load(tmp_path / "s.yaml").mos == "25D"
