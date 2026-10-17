import pandas as pd

from slotmap.gazetteer import Gazetteer, display_name, normalize_key, resolve_table


def test_normalize_key():
    assert normalize_key("FT HUACHUCA") == "FORT HUACHUCA"
    assert normalize_key("SEMBACH (ADMIN)") == "SEMBACH"
    assert normalize_key("Scott Air Force Base") == "SCOTT AFB"


def test_resolution_paths():
    g = Gazetteer.load()
    assert g.resolve("FORT HOOD").status == "exact"
    assert g.resolve("FORT CAVAZOS").gid == "hood"                       # renamed-installation alias
    assert g.resolve("MAINZ-KASTEL (AIN)").status == "normalized"
    m = g.resolve("FORT HUACHUKA", country="USA")                       # typo
    assert m.status == "fuzzy" and m.gid == "huachuca"
    assert g.resolve("CAMP DOES NOT EXIST", country="USA").status == "unresolved"


def test_table_coordinates_win():
    g = Gazetteer.load()
    df = pd.DataFrame({"STATION": ["FORT HOOD"], "STATE": ["Texas"], "COUNTRY": ["USA"], "SLOTS": [1],
                       "PCT_SRC": [None], "LAT": [30.0], "LON": [-97.0], "SRC_ROW": [1]})
    out, rep = resolve_table(df, g)
    assert out.LAT.iloc[0] == 30.0 and rep.RES_STATUS.iloc[0] == "table"


def test_local_gazetteer_overrides(tmp_path):
    p = tmp_path / "gazetteer_local.csv"
    p.write_text("id,name,state,country,lat,lon,aliases,coord_quality,notes\n"
                 "camp_new,Camp New,,GERMANY,50.0,9.0,CAMP NEW,surveyed,\n")
    g = Gazetteer.load(p)
    m = g.resolve("CAMP NEW")
    assert m.gid == "camp_new" and m.coord_quality == "surveyed"


def test_display_name():
    assert display_name("FORT SOMEWHERE (ADMIN)") == "Fort Somewhere"
