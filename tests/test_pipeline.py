import json

import geopandas as gpd
import pyogrio

from slotmap.pipeline import EXIT_OK, EXIT_UNRESOLVED, RunOptions, run


def test_end_to_end_offline(tmp_path, example_csv, no_basemap):
    out = tmp_path / "out"
    code = run(RunOptions(table=example_csv, out_dir=out, basemap_dir=no_basemap, quiet=True))
    assert code == EXIT_OK
    rep = json.loads((out / "qa_report.json").read_text())
    assert rep["status"] in ("pass", "warn")
    gpkg = next(out.glob("*.gpkg"))
    layers = [r[0] for r in pyogrio.list_layers(gpkg)]
    assert {"auth_points", "duty_stations", "duty_station_table", "frame_extents"} <= set(layers)
    pts = gpd.read_file(gpkg, layer="auth_points")
    assert int(pts.SLOTS.sum()) == 227
    assert (out / "frame_plan.json").exists() and list(out.glob("*_sheet.pdf")) and list(out.glob("*_mockup.png"))


def test_unresolved_stops_and_writes_todo(tmp_path, no_basemap):
    t = tmp_path / "t.csv"
    t.write_text("LOCATION,STATENAME,CNTRY,TOTAUTH\nFORT HOOD,Texas,USA,3\nCAMP NOWHERE,,GERMANY,2\n")
    out = tmp_path / "out"
    assert run(RunOptions(table=t, out_dir=out, basemap_dir=no_basemap, quiet=True)) == EXIT_UNRESOLVED
    todo = (out / "gazetteer_local_TODO.csv").read_text()
    assert "CAMP NOWHERE" in todo
    # fill it in and re-run
    local = tmp_path / "gazetteer_local.csv"
    local.write_text(todo.replace(",,,local", ",50.1,8.6,local").replace("Camp Nowhere,,,", "Camp Nowhere,,GERMANY,")
                     .replace(",,GERMANY,,", ",camp_nowhere,GERMANY,", 1))
    lines = local.read_text().splitlines()
    lines[1] = "camp_nowhere,Camp Nowhere,,GERMANY,50.1,8.6,CAMP NOWHERE,local,"
    local.write_text("\n".join(lines) + "\n")
    code = run(RunOptions(table=t, out_dir=out, gazetteer_local=local, basemap_dir=no_basemap,
                          stop_after="data", write_gdb=False, quiet=True))
    assert code == EXIT_OK
