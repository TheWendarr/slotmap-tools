"""Standard-library tests for slotmap_arcpy. Run from the repo root with ArcGIS Pro's Python (or any 3.7+):

    python -m unittest discover -s tests_arcpy -v

Without ArcPy installed, the ArcPy stages run against tests_arcpy/fake_arcpy (a stand-in that checks the
2.9 API surface and the layout JSON). With real ArcPy, those tests run only when SLOTMAP_TEMPLATE points
at a template project, and then they build real outputs.
"""
import csv
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
REAL_ARCPY = importlib.util.find_spec("arcpy") is not None
if not REAL_ARCPY:
    sys.path.insert(0, str(HERE / "fake_arcpy"))

from slotmap_arcpy import places  # noqa: E402
from slotmap_arcpy.batch import BatchOptions, make_splits, parse_code, read_rows, run_batch  # noqa: E402
from slotmap_arcpy.geo import CONUS, LAEA, Albers, EqualEarth  # noqa: E402
from slotmap_arcpy.gazetteer import Gazetteer  # noqa: E402
from slotmap_arcpy.xlsx import read_xlsx, rows_to_records  # noqa: E402

DATA = HERE / "data"


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="slotmap_"))
        self.data_dir = self.tmp / "nodata"          # no Natural Earth / towns unless a test adds them
        self.data_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)


class TestReading(Tmp):
    def test_xlsx(self):
        book = read_xlsx(DATA / "CMF35.xlsx")
        recs = rows_to_records(next(iter(book.values())))
        self.assertEqual(len(recs), 7)
        self.assertEqual(recs[0]["LOCATION"], "FORT HOOD")
        self.assertEqual(recs[2]["STATE"], "")
        self.assertEqual(recs[6]["TOTAUTH"], "0")

    def test_csv_total_row(self):
        from slotmap_arcpy.table import ingest
        t = self.tmp / "t.csv"
        t.write_text("LOCATION,STATENAME,CNTRY,TOTAUTH\nFORT HOOD,Texas,USA,3\nFORT BLISS,Texas,USA,2\n,,,6\n")
        ing = ingest(t)
        self.assertTrue(any("total row says 6" in e for e in ing.errors))


class TestGeo(unittest.TestCase):
    def test_round_trips(self):
        for p in (LAEA(10, 50), Albers(**CONUS)):
            for lon, lat in [(8, 49), (-77, 38.9), (-122, 47), (127, 37)]:
                x, y = p.fwd(lon, lat)
                lo, la = p.inv(x, y)
                self.assertAlmostEqual(lo, lon, 6)
                self.assertAlmostEqual(la, lat, 6)

    def test_equal_earth_reference(self):
        # PROJ: +proj=eqearth +R=6371007.181 at (10E, 50N)
        x, y = EqualEarth(0).fwd(10, 50)
        self.assertAlmostEqual(x, 790228.05, 1)
        self.assertAlmostEqual(y, 5998559.60, 1)


class TestCodesAndSplits(Tmp):
    def test_parse_code(self):
        c = parse_code("35G3O")
        self.assertEqual((c.cmf, c.mos, c.level, c.kind), ("35", "35G", "3", "enlisted"))
        self.assertEqual(parse_code("35G34").level, "3")
        w = parse_code("255A0", "CW2")
        self.assertEqual((w.cmf, w.mos, w.level, w.kind), ("25", "255A", "CW2", "warrant"))
        self.assertEqual(parse_code("35D00").kind, "officer")

    def test_splits(self):
        rows = read_rows(DATA / "CMF35.xlsx")[0] + read_rows(DATA / "CMF25.xlsx")[0]
        sp, _ = make_splits(rows, BatchOptions(tables=[], out_dir=self.tmp))
        self.assertEqual([s.name for s in sp], ["25D3", "25D4", "35F1", "35F3", "35G1", "35G2"])
        by = {s.name: s for s in sp}
        self.assertEqual(by["35G1"].slots, 10)
        self.assertEqual(by["35G1"].codes, ["35G1F", "35G1O", "35G1S"])
        rows = read_rows(DATA / "CMF35.xlsx")[0]
        sp2, _ = make_splits(rows, BatchOptions(tables=[], out_dir=self.tmp, by="both", mos=["35G"]))
        self.assertEqual([s.name for s in sp2], ["35G1", "35G2", "35G_ALL"])

    def test_compo_needs_column(self):
        rows = read_rows(DATA / "CMF35.xlsx")[0]
        with self.assertRaises(ValueError):
            make_splits(rows, BatchOptions(tables=[], out_dir=self.tmp, compo="AC"))


class TestGazetteer(Tmp):
    def test_city_lookup(self):
        p = places.cache_path(self.data_dir)
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps([
            {"name": "Dublin", "lat": "37.70", "lng": "-121.94", "country": "US", "admin1": "CA"},
            {"name": "Dublin", "lat": "32.54", "lng": "-82.90", "country": "US", "admin1": "GA"},
            {"name": "Brunssum", "lat": "50.95", "lng": "5.97", "country": "NL", "admin1": "05"}]))
        places.clear_cache()
        g = Gazetteer.load(data_dir=self.data_dir)
        m = g.resolve("DUBLIN CA (CITY)", "CA", "USA")
        self.assertEqual(m.status, "city")
        self.assertAlmostEqual(m.lat, 37.70)
        self.assertEqual(g.resolve("BRUNSSUM (CITY)", "", "NETHERLANDS").status, "city")
        self.assertEqual(g.resolve("DUBLIN (CITY)", "", "USA").status, "unresolved")
        places.clear_cache()

    def test_known_spellings(self):
        g = Gazetteer.load(data_dir=self.data_dir)
        self.assertEqual(g.resolve("JBSA FT SAM HOUST (ADMIN)", "TX", "USA").gid, "sam_houston")
        self.assertEqual(g.resolve("WIESBADEN CLAY KS", "", "GERMANY").gid, "clay")


class TestBatchTablesOnly(Tmp):
    def test_multi_cmf_without_arcpy(self):
        out = self.tmp / "b"
        ov = self.tmp / "ov"
        ov.mkdir()
        (ov / "35G.json").write_text('{"value_label": "authorizations"}')
        (ov / "35G1.json").write_text('{"notes": ["Tuned for 35G SL1 only."]}')
        code = run_batch(BatchOptions(tables=[DATA / "*.xlsx"], out_dir=out, overrides=ov, data_dir=self.data_dir,
                                      prepared_by="SSG Test", quiet=True, stop_after="tables"))
        self.assertEqual(code, 0)
        with open(str(out / "index.csv"), encoding="utf-8") as fh:
            idx = list(csv.DictReader(fh))
        self.assertEqual({r["sheet"] for r in idx}, {"25D3", "25D4", "35F1", "35F3", "35G1", "35G2"})
        self.assertEqual(sum(int(r["slots"]) for r in idx), 34)
        cfg = json.loads((out / "35" / "35G" / "SL1" / "sheet.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["value_label"], "authorizations")
        self.assertIn("Tuned for 35G SL1 only.", cfg["notes"])
        self.assertEqual(cfg["prepared_by"], "SSG Test")
        self.assertEqual(json.loads((out / "35" / "35G" / "SL2" / "sheet.json").read_text())["value_label"],
                         "authorizations")
        self.assertEqual(json.loads((out / "35" / "35F" / "SL1" / "sheet.json").read_text())["value_label"], "slots")

    def test_unknown_station_stops_once(self):
        t = self.tmp / "x.csv"
        t.write_text("LOCATION,STATE,CNTRY,POSCO,TOTAUTH\nCAMP NOWHERE,,GERMANY,35G1O,2\n"
                     "CAMP NOWHERE,,GERMANY,35G2O,1\nFORT HOOD,TX,USA,35G2O,1\n")
        out = self.tmp / "b"
        self.assertEqual(run_batch(BatchOptions(tables=[t], out_dir=out, data_dir=self.data_dir, quiet=True,
                                                stop_after="tables")), 3)
        with open(str(out / "gazetteer_local_TODO.csv"), encoding="utf-8") as fh:
            todo = list(csv.DictReader(fh))
        self.assertEqual(len(todo), 1)
        self.assertIn("35G1 35G2", todo[0]["notes"])
        code = run_batch(BatchOptions(tables=[t], out_dir=out, data_dir=self.data_dir, quiet=True,
                                      allow_unresolved=True, stop_after="tables"))
        self.assertEqual(code, 0)
        with open(str(out / "index.csv"), encoding="utf-8") as fh:
            idx = {r["sheet"]: r for r in csv.DictReader(fh)}
        self.assertEqual(idx["35G1"]["qa"], "skipped")
        qa = json.loads((out / "35" / "35G" / "SL2" / "out" / "qa_report.json").read_text())
        self.assertEqual({c["check"]: c["status"] for c in qa["checks"]}["totals"], "pass")


@unittest.skipIf(REAL_ARCPY and not os.environ.get("SLOTMAP_TEMPLATE"),
                 "real ArcPy present: set SLOTMAP_TEMPLATE to a template .aprx to run the full build")
class TestArcpyBuild(Tmp):
    def test_full_batch(self):
        tmpl = Path(os.environ["SLOTMAP_TEMPLATE"]) if REAL_ARCPY else self.tmp / "template.aprx"
        if not REAL_ARCPY:
            tmpl.write_text("")
        out = self.tmp / "b"
        code = run_batch(BatchOptions(tables=[DATA / "CMF35.xlsx"], out_dir=out, data_dir=self.data_dir,
                                      template=tmpl, quiet=True))
        self.assertEqual(code, 0)
        with open(str(out / "index.csv"), encoding="utf-8") as fh:
            idx = list(csv.DictReader(fh))
        self.assertTrue(all(r["qa"] in ("pass", "warn") for r in idx), idx)
        self.assertEqual(len(list((out / "sheets").glob("*_sheet.pdf"))), 4)
        sheet = out / "35" / "35G" / "SL1" / "out"
        for name in ("35g1.gdb", "35g1_sheet.png", "35g1.aprx", "35g1_layout.pagx", "qa_report.json"):
            self.assertTrue((sheet / name).exists(), name)
        if not REAL_ARCPY:
            import arcpy
            doc = json.loads((sheet / "35g1_layout.pagx").read_text(encoding="utf-8"))
            self.assertTrue(arcpy.validate_pagx(doc))
            frames = [e for e in doc["layoutDefinition"]["elements"] if e["type"] == "CIMMapFrame"]
            self.assertEqual(len(frames), len(doc["mapDefinitions"]))
            texts = [e["graphic"]["text"] for e in doc["layoutDefinition"]["elements"]
                     if e.get("graphic", {}).get("type") == "CIMTextGraphic"]
            self.assertTrue(any(t.startswith("35G Skill Level 1") for t in texts))


if __name__ == "__main__":
    unittest.main()
