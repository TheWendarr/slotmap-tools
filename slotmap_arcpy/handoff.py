"""Hand-off prompt for an LLM helping on an ArcPy-only machine. Print it with:

    python -m slotmap_arcpy.handoff > handoff_prompt.md
"""

PROMPT = r'''You are helping me run and finish a Python tool called **slotmap_arcpy** on a restricted Windows machine. Read this whole prompt before doing anything.

## The machine

- ArcGIS Pro **2.x**, so Python **3.7** with ArcPy. Run everything from **Start ▸ ArcGIS ▸ Python Command Prompt**, inside the folder that contains `slotmap_arcpy\`.
- **Only the Python standard library and ArcPy are available.** Do not suggest `pip`, `conda`, pandas, numpy, matplotlib, openpyxl or any download. Assume no internet unless I say otherwise. Code must stay Python 3.7 compatible: no walrus operator, no `list[str]` style hints, nothing newer than 3.7.
- Only `.py` files were copied to this machine. Documentation, data files and the template project did not come over. Everything you need about them is in this prompt.
- The input tables can be controlled information (CUI). Keep them in `data\`, `work\` or `out\`, and never suggest uploading them anywhere.

## What the tool does

It takes Army position exports and builds print-ready **map sheets** of authorized slots by duty station. Each sheet is a 24×18 in ARCH C landscape page with:
- a world frame showing theater totals
- regional frames and automatic zoomed-in detail frames
- area-proportional circles, with a likelihood colour scale from each station's share of slots
- a ranked station key and a footer written from the data

It also writes a **FileGDB + GeoPackage**. The exports are standard workbooks with the columns `LOCATION, STATE, STATENAME, CNTRY, POSCO, POSCOTITLE, …, TOTAUTH`.

`batch` splits the rows by position code: `35G3O` is CMF 35, MOS 35G, skill level 3 (SSG). Every `35G3x` variant is combined on one sheet. Warrant (`255A0`) and officer (`35D00`) codes are split by a GRADE column if the export has one. The tool handles several MOSs per file and several files or CMFs per run.

Typical use:

```
python -m slotmap_arcpy batch data\35G.xlsx --prepared-by "SSG McGowan"
python -m slotmap_arcpy batch data\CMF35.xlsx data\CMF25.xlsx -o out\cmf35_25 --by both --zip
python -m slotmap_arcpy batch data --mos 35G 35F --list
python -m slotmap_arcpy batch data\35G.xlsx --tables-only
```

`--tables-only` runs everything except the ArcPy stages.

Output in `out\batch\`:
- `index.md` and `index.csv`: one row per sheet with slots, stations mapped, largest share and QA status
- `sheets\`: every PDF
- one folder per split, for example `35\35G\SL3\`, holding `table.csv`, `sheet.json` and `out\`
- each `out\` folder holds `<sheet>.gdb`, `<sheet>.gpkg`, `<sheet>_sheet.pdf/.png`, `<sheet>.aprx`, `qa_report.json`, `duty_station_table.csv`, `frame_plan.json`, `crops\` and `run.log`

Exit codes: `0` ok (some sheets may be `warn`) · `1` a sheet failed QA or crashed · `2` input or template problem · `3` stations need coordinates.

## Code map (all in `slotmap_arcpy\`)

Pure standard library (already tested on another machine; these give the same results as the original tool):

| Module | Job |
|---|---|
| `xlsx.py` | reads .xlsx with `zipfile` and `xml.etree` |
| `table.py` | reads CSV/TSV/XLSX (.xls goes through `arcpy.conversion.ExcelToTable`); loose header matching; total-row check |
| `installations_data.py` | about 130 bundled installations and town entries with spelling aliases |
| `gazetteer.py` | name matching: exact → normalized → town list → fuzzy (≥ 0.92 auto-accepted and footnoted) → unresolved. `gazetteer_local.csv` in the working folder overrides the bundled list |
| `places.py` | optional GeoNames town list at `<data dir>\places\cities.json` |
| `aggregate.py` | merges duplicate rows, computes shares, classes, theaters and ranks |
| `geo.py` | Lambert Azimuthal Equal Area, Albers (CONUS) and Equal Earth math, plus the matching Esri WKT |
| `layout.py` | packs the frames onto the page |
| `plan.py` | regions, collision-driven detail frames, displacement, detail-frame names |
| `batch.py` | splitting, the one-pass name check, per-sheet configs, index files |
| `qa.py`, `pipeline.py`, `cli.py` | QA checks, the per-sheet pipeline and the command line |
| `config.py` | JSON config (`sheet.json`); there is no YAML |

ArcPy stages (**written from the Pro 2.9 documentation but never run on real ArcPy — expect to debug these**):

| Module | Job |
|---|---|
| `products.py` → `write_gis()` | `CreateFileGDB`, `CreateFeatureclass`, `AddField`, `da.InsertCursor`; GeoPackage via `CreateSQLiteDatabase(..., "GEOPACKAGE")` + `FeatureClassToFeatureClass` / `TableToTable`. Layers: `auth_points`, `duty_stations`, `frame_extents`, `sheet_points` (one point per frame and station at its displayed position, with `SYMKEY`, `OWNED` and `FRAME`), `theater_points`, plus three tables |
| `render.py` | builds the sheet; see the next section |

Tests: `tests_arcpy\test_slotmap_arcpy.py` uses `unittest` and builds its own workbook fixtures. Without ArcPy it swaps in `tests_arcpy\fake_arcpy`, a stand-in limited to the documented 2.9 API. With real ArcPy, the end-to-end test runs only when the environment variable `SLOTMAP_TEMPLATE` points at the template `.aprx`.

## How `render.py` works (the part most likely to need fixes)

ArcPy in Pro 2.x cannot create layouts, map frames or text elements. The workaround:

1. A template project, `pro_template\slotmap_template.aprx`, has a layout named `slotmap_template` with one map frame, one point text element and one rectangle.
2. `export_template_json()` calls `Layout.exportToPAGX()` and loads the .pagx JSON (`CIMLayoutDocument`).
3. `PagxBuilder` deep-copies the sample `CIMMapFrame`, `CIMGraphicElement`/`CIMTextGraphic` and `CIMGraphicElement`/`CIMPolygonGraphic` for every frame, text, box and circle. It sets positions in page inches, sizes, fonts, colours, alignment and text (which includes `<CLR>`/`<FNT>`/`<BOL>` formatting tags). It also writes one `CIMMap` per frame, cloned from the template's map, with `layers: []`, the frame's projection as `{"wkt": ...}` and a water `backgroundColor`. The element list is reversed so that the first item is the top-most.
4. `aprx.importDocument(pagx)` loads the result. The tool then works through each `MF_<letter>` map frame. It sets `map.spatialReference` and adds the Natural Earth shapefiles with `map.addDataFromPath` and simple symbology. It adds `sheet_points` with a definition query and a unique-value renderer on `SYMKEY` (colour by class, size = circle diameter in points), `theater_points` for the world frame, and `frame_extents` as hollow lettered boxes. Labels come from layer CIM: Arcade expression, `whereClause OWNED = 1`, height, halo, Maplex `neverRemoveLabel`. Finally it calls `mf.camera.setExtent()`.
5. It exports the layout to PDF and PNG and each map frame to PNG, then calls `aprx.saveACopy()`. The template is never saved.

Likely failure points and where they live in the code:
- **Pro rejects or misreads the generated .pagx**: `PagxBuilder.add_*` and `finish()`. Compare with a layout made by hand and exported from Pro.
- **Label CIM attribute names**: `_labels()`. These are wrapped in try/except, and failures are recorded as `render` warnings in `qa_report.json`.
- **Symbology**: `_simple()` and `_unique()`.
- **Draw order of added layers**: points must sit above polygons, and lakes above countries.
- **Text sizing**: `text_width_in()` estimates widths and wraps text, so titles or footers may need tuning.
- **Export keyword arguments.**

Fix the code, keep it stdlib + ArcPy and Python 3.7, and add or adjust a test where possible. `python -m slotmap_arcpy check-template` exports and checks the template. The generated `<sheet>_layout.pagx` stays in each `out\` folder for inspection.

## What didn't come over, and how to handle it

1. **Template project.** Walk me through making it in Pro:
   - New project from the **Map** template, named `slotmap_template`, in the `pro_template` folder, with "Create a new folder for this project" unchecked.
   - Insert ▸ New Layout (any size), then rename the layout to exactly `slotmap_template`.
   - Insert ▸ Map Frame onto the project map.
   - Insert ▸ Text (point text), with any text.
   - Insert ▸ Rectangle.
   - Save and close Pro.
   - Then run `python -m slotmap_arcpy check-template`.
   Size, style and position of these elements don't matter.
2. **Natural Earth basemap.** The tool looks for `%USERPROFILE%\.cache\slotmap\natural_earth\` (or `--data-dir` / the `SLOTMAP_DATA` variable) containing these shapefiles: `ne_10m_admin_0_countries`, `ne_50m_admin_0_countries`, `ne_10m_admin_1_states_provinces_lakes`, `ne_10m_lakes`, `ne_10m_urban_areas` and `ne_10m_populated_places_simple`. Without them, sheets are drawn on plain water-coloured frames, and the footer leaves out the basemap credit. If the machine has other country/state polygons (for example Esri Data & Maps), you may adapt `render._basemap()` and `basemap.available()` to use them. Ask me what exists first.
3. **GeoNames town list** (`places\cities.json`). Without it, "(CITY)" names that aren't bundled in `installations_data.py` stop the batch, with one `gazetteer_local_TODO.csv` that lists which sheets need each name. Every 35G name is already bundled.
4. **Documentation.** This prompt replaces it.

## Rules

- **Never guess coordinates.** A missing station goes in `gazetteer_local.csv` (columns `id,name,state,country,lat,lon,aliases,coord_quality,notes`). Coordinates come from a source I can confirm. Put the source in `notes` and set `coord_quality` to `approx`. Keep the original spelling in `aliases`, separated by `|`.
- **Totals are sacred.** Mapped + not-mapped (0-slot or dropped) slots must equal the table total. The `totals` QA check enforces this.
- **Share of slots is a proxy for likelihood, not a probability.** Keep that caveat on the sheet.
- **Config, not code, for tuning one sheet.** Put JSON files in an `overrides\` folder, merged most-general first: `35.json` (CMF), `35G.json` (MOS), `35G3.json` or `35G_SL3.json` (one sheet). Keys include `title`, `prepared_by`, `value_label`, `notes`, `frame_overrides` (`{"conus/stewart": {"title": "...", "halfwidth_km": 40, "center": [lon, lat]}}`), `label_overrides` (`{"gordon": {"name": "Fort Eisenhower"}}`), `classes`, `page` and `plan`. Change code only to fix a bug, and say what you changed.
- The planning stages are proven. Don't rewrite them to fix a rendering problem.

## Start here

1. `python -m unittest discover -s tests_arcpy -v`. With real ArcPy present, the end-to-end test is skipped until `SLOTMAP_TEMPLATE` is set.
2. Build the template, then run `python -m slotmap_arcpy check-template`.
3. `python -m slotmap_arcpy batch <my export>.xlsx --tables-only`, and read `out\batch\index.md`.
4. The full `batch` run. If it fails, read the sheet's `out\run.log` and `qa_report.json`, find the failing ArcPy call, and fix it.
5. Open `crops\overview.png` and each `crops\frame_*.png`. Check:
   - every label sits next to its own circle
   - lettered boxes match the frame letters
   - the key lists every station
   - the footer notes are true

Ask me before any change that can't be undone, and tell me plainly which QA warnings remain at the end.
'''


if __name__ == "__main__":
    import sys
    out = sys.stdout
    try:
        out.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    out.write(PROMPT)
