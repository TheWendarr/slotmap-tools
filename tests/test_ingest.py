import pandas as pd

from slotmap.ingest import clean_values, ingest, normalize_columns, split_total_row


def test_header_aliases():
    df = pd.DataFrame(columns=["LOCATION", "STATENAME", "CNTRY", "TOTAUTH", "Total Percent"])
    out, unmatched = normalize_columns(df)
    assert {"STATION", "STATE", "COUNTRY", "SLOTS", "PCT_SRC"} <= set(out.columns)
    assert unmatched == []


def test_percent_and_placeholder_state():
    df, _ = normalize_columns(pd.DataFrame({"LOCATION": ["X"], "STATENAME": ["No Title Code Found"],
                                            "CNTRY": ["GERMANY"], "TOTAUTH": ["3"], "Total Percent": ["1.3%"]}))
    df = clean_values(df)
    assert df.STATE.iloc[0] == "" and df.SLOTS.iloc[0] == 3 and df.PCT_SRC.iloc[0] == 1.3


def test_total_row_detected(example_csv):
    r = ingest(example_csv)
    assert r.stated_total == 227 and int(r.table.SLOTS.sum()) == 227 and not r.errors


def test_total_mismatch_is_an_error(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("LOCATION,TOTAUTH\nFORT HOOD,3\nFORT BLISS,2\n,9\n")
    r = ingest(p)
    assert any("sum to 5" in e for e in r.errors)


def test_xlsx_roundtrip(tmp_path, example_csv):
    p = tmp_path / "t.xlsx"
    pd.read_csv(example_csv, dtype=str, keep_default_na=False).to_excel(p, index=False)
    assert int(ingest(p).table.SLOTS.sum()) == 227


def test_split_total_word():
    df = pd.DataFrame({"STATION": ["A", "TOTAL"], "SLOTS": [2.0, 2.0]})
    out, stated = split_total_row(df)
    assert len(out) == 1 and stated == 2
