import json

import pandas as pd
import pytest

from slotmap import places
from slotmap.batch import (EXIT_OK, EXIT_UNRESOLVED, BatchOptions, make_splits, parse_code, read_rows,
                           run_batch)
from slotmap.gazetteer import Gazetteer

HDR = ["LOCATION", "STATE", "STATENAME", "CNTRY", "POSCO", "POSCOTITLE", "TOTREQ", "TOTAUTH"]


def _export(path, rows):
    pd.DataFrame(rows, columns=HDR).to_excel(path, index=False)
    return path


@pytest.fixture
def cmf35(tmp_path):
    return _export(tmp_path / "CMF35.xlsx", [
        ["FORT HOOD", "TX", "Texas", "USA", "35G1O", "GEOINT", 5, 5],
        ["FORT HOOD", "TX", "Texas", "USA", "35G1S", "GEOINT", 2, 2],        # merged with the row above
        ["CAMP HUMPHREYS", None, "No Title Code Found", "KOREA, REPUBLIC OF", "35G1F", "GEOINT", 3, 3],
        ["FORT GORDON", "GA", "Georgia", "USA", "35G2O", "GEOINT", 4, 4],
        ["FORT CARSON", "CO", "Colorado", "USA", "35F1O", "INTEL ANALYST", 6, 6],
        ["FORT BRAGG", "NC", "North Carolina", "USA", "35F3O", "INTEL ANALYST", 2, 2],
        ["FORT EUSTIS", "VA", "Virginia", "USA", "35F3O", "INTEL ANALYST", 1, 0],  # 0 slots: footer only
    ])


@pytest.fixture
def cmf25(tmp_path):
    return _export(tmp_path / "CMF25.xlsx", [
        ["FORT GORDON", "GA", "Georgia", "USA", "25D3O", "CYBER NET DEF", 7, 7],
        ["FORT MEADE", "MD", "Maryland", "USA", "25D3O", "CYBER NET DEF", 3, 3],
        ["SCHOFIELD BARRACKS", "HI", "Hawaii", "USA", "25D4O", "CYBER NET DEF", 2, 2],
    ])


def test_parse_code():
    c = parse_code("35G3O")
    assert (c.cmf, c.mos, c.level, c.kind) == ("35", "35G", "3", "enlisted")
    assert parse_code("35G34").level == "3"                     # SQI digit is not the skill level
    w = parse_code("255A0", "CW2")
    assert (w.cmf, w.mos, w.level, w.kind) == ("25", "255A", "CW2", "warrant")
    o = parse_code("35D00")
    assert (o.mos, o.level, o.kind) == ("35D", "ALL", "officer")


def test_splits_by_cmf_mos_level(cmf35, cmf25):
    rows = pd.concat([read_rows(cmf35)[0], read_rows(cmf25)[0]], ignore_index=True)
    sp, _ = make_splits(rows, BatchOptions(tables=[], out_dir="x"))
    names = [s.name for s in sp]
    assert names == ["25D3", "25D4", "35F1", "35F3", "35G1", "35G2"]
    by = {s.name: s for s in sp}
    assert int(by["35G1"].rows.SLOTS.sum()) == 10 and str(by["35G1"].folder) == "35/35G/SL1"
    assert by["35G1"].codes == ["35G1F", "35G1O", "35G1S"]
    sp2, _ = make_splits(rows, BatchOptions(tables=[], out_dir="x", by="both", mos=["35G"]))
    assert [s.name for s in sp2] == ["35G1", "35G2", "35G_ALL"]
    sp3, _ = make_splits(rows, BatchOptions(tables=[], out_dir="x", cmf=["25"]))
    assert [s.name for s in sp3] == ["25D3", "25D4"]


def test_compo_needs_column(cmf35):
    rows, _ = read_rows(cmf35)
    with pytest.raises(ValueError):
        make_splits(rows, BatchOptions(tables=[], out_dir="x", compo="AC"))


def test_batch_end_to_end_multi_cmf(tmp_path, cmf35, cmf25, no_basemap):
    out = tmp_path / "batch"
    ov = tmp_path / "overrides"
    ov.mkdir()
    (ov / "35G.yaml").write_text("value_label: authorizations\n")
    (ov / "35G1.yaml").write_text("notes: ['Tuned for 35G SL1 only.']\n")
    code = run_batch(BatchOptions(tables=[tmp_path / "*.xlsx"], out_dir=out, overrides=ov, basemap_dir=no_basemap,
                                  write_gdb=False, prepared_by="SSG Test", jobs=1, quiet=True))
    assert code == EXIT_OK
    idx = pd.read_csv(out / "index.csv")
    assert set(idx.sheet) == {"25D3", "25D4", "35F1", "35F3", "35G1", "35G2"}
    assert int(idx.slots.sum()) == 34
    assert set(idx.qa) <= {"pass", "warn"}
    assert len(list((out / "sheets").glob("*_sheet.pdf"))) == 6
    cfg = (out / "35" / "35G" / "SL1" / "sheet.yaml").read_text()
    assert "authorizations" in cfg and "Tuned for 35G SL1 only." in cfg and "SSG Test" in cfg
    assert "authorizations" not in (out / "35" / "35G" / "SL2" / "sheet.yaml").read_text().split("value_label")[0]
    qa = json.loads((out / "35" / "35F" / "SL3" / "out" / "qa_report.json").read_text())
    assert any("Fort Eustis" in n for n in qa["footer_notes"])


def test_batch_stops_once_for_unknown_station(tmp_path, no_basemap):
    t = _export(tmp_path / "x.xlsx", [["CAMP NOWHERE", None, None, "GERMANY", "35G1O", "", 2, 2],
                                      ["CAMP NOWHERE", None, None, "GERMANY", "35G2O", "", 1, 1],
                                      ["FORT HOOD", "TX", "Texas", "USA", "35G2O", "", 1, 1]])
    out = tmp_path / "b"
    assert run_batch(BatchOptions(tables=[t], out_dir=out, basemap_dir=no_basemap, quiet=True)) == EXIT_UNRESOLVED
    todo = pd.read_csv(out / "gazetteer_local_TODO.csv")
    assert len(todo) == 1 and "35G1 35G2" in todo.notes.iloc[0]
    code = run_batch(BatchOptions(tables=[t], out_dir=out, basemap_dir=no_basemap, allow_unresolved=True,
                                  write_gdb=False, quiet=True))
    assert code == EXIT_OK
    idx = pd.read_csv(out / "index.csv").set_index("sheet")
    assert idx.loc["35G1", "qa"] == "skipped"                  # its only station has no coordinates
    assert idx.loc["35G2", "qa"] == "warn"                     # Camp Nowhere dropped, totals still reconcile
    qa = json.loads((out / "35" / "35G" / "SL2" / "out" / "qa_report.json").read_text())
    assert {c["check"]: c["status"] for c in qa["checks"]}["totals"] == "pass"


def test_city_tagged_names_use_town_list(tmp_path, monkeypatch):
    bm = tmp_path / "natural_earth"
    bm.mkdir()
    monkeypatch.setenv("SLOTMAP_BASEMAP", str(bm))
    p = places.cache_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps([
        {"name": "Dublin", "lat": "37.70", "lng": "-121.94", "country": "US", "admin1": "CA"},
        {"name": "Dublin", "lat": "32.54", "lng": "-82.90", "country": "US", "admin1": "GA"},
        {"name": "Brunssum", "lat": "50.95", "lng": "5.97", "country": "NL", "admin1": "05"}]))
    places._index.cache_clear()
    g = Gazetteer.load()
    m = g.resolve("DUBLIN CA (CITY)", "CA", "USA")
    assert m.status == "city" and abs(m.lat - 37.70) < 1e-6
    assert g.resolve("BRUNSSUM (CITY)", "", "NETHERLANDS").status == "city"
    assert g.resolve("DUBLIN (CITY)", "", "USA").status == "unresolved"     # no state: ambiguous, not guessed
    places._index.cache_clear()
