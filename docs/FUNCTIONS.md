# Function inventory

This is every operation that was done by hand (or by one-off script) to produce the first
authorized-slots map sheet, rewritten as the functions this repo implements. Each row names
the module and function that now covers it.

Stages run in order; each stage writes something to disk so a person or an LLM can inspect
it before the next stage runs.

## 1. Ingest — get the table into a clean, typed form

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 1.1 | `read_table(path, sheet=None)` | Read CSV / TSV / XLSX / XLS into a DataFrame. (Screenshots and PDFs are transcribed by the LLM into `templates/slots_template.csv` first — see AGENTS.md.) | `ingest.py` |
| 1.2 | `normalize_columns(df)` | Map header variants (`LOCATION`, `DUTY STATION`, `TOTAUTH`, `Total Percent`, `CNTRY`, …) onto the canonical schema `STATION, STATE, COUNTRY, SLOTS, PCT_SRC, LAT, LON`. | `ingest.py` |
| 1.3 | `clean_values(df)` | Strip text, parse `"0.4%"` → `0.4`, coerce slot counts to int, treat `"No Title Code Found"` as a blank state. | `ingest.py` |
| 1.4 | `split_total_row(df)` | Detect and remove the trailing grand-total row (the bare `223`), keep its value as the stated total. | `ingest.py` |
| 1.5 | `validate_table(df, stated_total)` | Sum of slots = stated total; percents sum ≈ 100; no negatives or blank station names; reports warnings and errors. | `ingest.py` |

## 2. Resolve — turn station names into coordinates

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 2.1 | `Gazetteer.load(local=...)` | Load the bundled installation gazetteer plus a user-maintained local override file. | `gazetteer.py` |
| 2.2 | `normalize_key(name)` | Uppercase, drop punctuation and admin tags `(ADMIN)`, `(AIN)`, `(CITY)`, expand `FT`→`FORT`, `AFB`, `AAF`, etc. | `gazetteer.py` |
| 2.3 | `Gazetteer.resolve(name, state, country)` | Exact alias → normalized alias → town (`(CITY)` names) → fuzzy match (country-filtered) → town fallback → unresolved, with a status and confidence. | `gazetteer.py` |
| 2.4 | `resolve_table(df, gaz)` | Resolve every row; table LAT/LON columns win over the gazetteer; fuzzy matches ≥ `fuzzy_auto_accept` are accepted and footnoted; write `resolution_report.csv`. | `gazetteer.py` |
| 2.6 | `places.lookup(name, country, postal)` | Town-centre placement from GeoNames (pop ≥ 1,000; fetched by `fetch-basemap`). U.S. towns must match the state; elsewhere the name must be unique in the country. | `places.py` |
| 2.5 | `display_name(raw)` | Human label (`HICKAM AIR FORCE BASE (MILITARY AIRPORT)` → `Hickam AFB`). | `gazetteer.py` |

## 3. Aggregate — the numbers the map shows

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 3.1 | `merge_duplicates(df)` | Combine repeated rows for one installation (Fort Lewis 1 + 11 → 12) and keep a record count. | `aggregate.py` |
| 3.2 | `compute_shares(df, total)` | `PCT_CALC = slots / total × 100` (the source percents are rounded and do not sum to 100). | `aggregate.py` |
| 3.3 | `assign_theater(row)` | CONUS / Alaska / Hawaii / U.S. territory / overseas country. | `aggregate.py` |
| 3.4 | `classify(pct, breaks)` | Likelihood class from share of slots (default breaks 2 / 5 / 10 %). | `aggregate.py` |
| 3.5 | `group_totals(df)` | Totals by state (U.S.) or country (overseas) — the `GRP_AUTH / GRP_PCT` fields. | `aggregate.py` |
| 3.6 | `theater_totals(df)` | Totals by theater with slot-weighted centroid for the world frame. | `aggregate.py` |
| 3.7 | `rank_stations(df)` | Rank by slots, ties by name — order of the station key. | `aggregate.py` |

## 4. Data products — the feature classes

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 4.1 | `build_points(records)` | One point per source record, WGS84, with record + group fields. | `products.py` |
| 4.2 | `build_station_table(stations)` | One row per duty station (slots, calculated %, source %, record count). | `products.py` |
| 4.3 | `build_frame_extents(plan)` | Polygon feature class of every map frame's footprint (re-use in ArcGIS Pro layouts). | `products.py` |
| 4.4 | `write_geopackage(layers, path)` | GeoPackage with all layers/tables. | `products.py` |
| 4.5 | `write_filegdb(layers, path, if_exists)` | Esri File Geodatabase via GDAL OpenFileGDB — the fgdb-tools writer, with its name sanitizing and `unique/skip/fail` rules. | `fgdb.py` |
| 4.6 | `export_layers(...)` | Optional GeoJSON / Shapefile / KML exports (fgdb-tools export behaviour). | `fgdb.py` |

## 5. Plan — decide the map frames

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 5.1 | `region_groups(stations)` | Top-level frames: CONUS, Alaska, Hawaii, territories, and overseas countries merged when close (Germany + Poland). | `plan.py` |
| 5.2 | `fit_extent(points, aspect, pad, min_halfwidth)` | Equal-area extent that holds the stations and matches the frame's shape. | `plan.py` |
| 5.3 | `find_collisions(frame, slot_size, symbol_scale)` | Which stations would overlap (symbol + label room) at that frame's scale. | `plan.py` |
| 5.4 | `cluster_stations(...)` | Single-linkage clustering of colliding stations → child "detail" frames (NCR, Colorado Springs, Savannah, Rhine–Main). | `plan.py` |
| 5.5 | `name_frame(...)` | Frame title from the nearest large populated place / region names. | `plan.py` |
| 5.6 | `make_layout(frames, page)` | Pack frames into page rows (world + main + side column + bottom strip); bigger slots go to frames with more stations; letters assigned in reading order. | `layout.py` |
| 5.7 | `displace_coincident(frame)` | Offset near-identical points (Baumholder / Smith Barracks) and record a footer note. | `plan.py` |
| 5.8 | `plan_sheet(...)` | Iterate 5.1–5.7 until no collisions remain or `max_frames` is reached; write `frame_plan.json`. | `plan.py` |

## 6. Render — draw the sheet

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 6.1 | `Basemap.fetch / load` | Download and cache Natural Earth layers; clip per frame. | `basemap.py` |
| 6.2 | `draw_basemap(ax, frame)` | Water, land (focus vs other countries), urban areas, lakes, admin-1 and country lines. | `render.py` |
| 6.3 | `draw_symbols(ax, stations, k)` | Area-proportional circles, one size scale on the whole sheet, colour by class. | `render.py` |
| 6.4 | `LabelPlacer.place(...)` | Automatic two-line station labels: tries 8 positions, then leader lines, scoring overlaps with other labels, symbols and the frame edge. | `labels.py` |
| 6.5 | `draw_reference_places / area_labels` | Grey orientation cities and country/state names, only where they do not collide. | `render.py` |
| 6.6 | `draw_scalebar(ax, extent)` | Nice-number km (mi) bar per frame. | `render.py` |
| 6.7 | `draw_extent_boxes(parent, child)` | Lettered locator boxes linking each frame to its parent. | `render.py` |
| 6.8 | `draw_world(ax, theaters)` | Equal Earth world frame with theater totals and station dots. | `render.py` |
| 6.9 | `draw_panel(...)` | Legend (size + class), theater bars, ranked station key with frame letters. | `render.py` |
| 6.10 | `draw_title_kpis_footer(...)` | Title, subtitle, KPI tiles, auto-written footer notes (sources, exclusions, displacement, projections). | `render.py` |
| 6.11 | `render_mockup(plan)` | Wireframe of the frame plan for design review before the full render. | `mockup.py` |
| 6.12 | `save_outputs(fig)` | PDF (vector, fonts embedded) and PNG. | `render.py` |

## 7. QA — prove the sheet is right

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 7.1 | `check_totals` | Mapped + excluded slots = table total; stated total matches. | `qa.py` |
| 7.2 | `check_ownership` | Every mapped station is labelled in exactly one frame and lies inside it. | `qa.py` |
| 7.3 | `check_separation` | Minimum symbol gap in each owning frame; lists displaced stations. | `qa.py` |
| 7.4 | `check_labels` | Label collisions and labels that run outside their frame. | `qa.py` |
| 7.5 | `write_crops` | One PNG crop per frame for visual review by a person or an LLM. | `qa.py` |
| 7.6 | `write_report` | `qa_report.json` with pass/warn/fail per check. | `qa.py` |

## 8. Orchestrate

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 8.1 | `SheetConfig.load / save` | YAML config: titles, class breaks, colours, page size, overrides. | `config.py` |
| 8.2 | `run_pipeline(opts)` | All stages, each one skippable/re-runnable. | `pipeline.py` |
| 8.3 | `slotmap` CLI | `init`, `check`, `plan`, `data`, `run`, `batch`, `fetch-basemap`, `gazetteer`. | `cli.py` |

## 9. Batch — many exports, every sheet

| # | Function | What it covers | Module |
|---|----------|----------------|--------|
| 9.1 | `parse_code(posco, grade)` | `35G3O` → CMF 35, MOS 35G, skill level 3 (enlisted); `255A0` → warrant MOS 255A, split by grade; `35D00` → officer AOC 35D, split by grade. | `batch.py` |
| 9.2 | `read_rows(path)` | Every sheet of a workbook (or a CSV) → canonical rows + position code, grade and component columns. | `batch.py` |
| 9.3 | `make_splits(rows, opts)` | One split per CMF/MOS/level (`--by level`), per MOS (`--by mos`) or both; `--mos`, `--cmf`, `--compo` filters. | `batch.py` |
| 9.4 | `preflight_resolve(splits)` | Resolve every station of the whole batch once; one `gazetteer_local_TODO.csv` listing which sheets need each name. 0-slot stations never block. | `batch.py` |
| 9.5 | `build_config(split)` | Generated `sheet.yaml`: title, MOS tag, footer notes (codes combined, sources, component filter), then the base config and `overrides/` files merged on top. | `batch.py` |
| 9.6 | `run_batch(opts)` | Write tables + configs, build sheets in parallel, copy PDFs to `sheets/`, write `index.csv` / `index.md`, optional zip. | `batch.py` |
