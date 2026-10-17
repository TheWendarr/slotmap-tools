# slotmap-tools

Turn a table of **authorized slots by duty station** — for any MOS — into:

- **GIS data**: an Esri **File Geodatabase** and a **GeoPackage** with a point feature class per
  source record, a duty-station point layer, frame-footprint polygons, and station / state-country /
  theater total tables;
- a **print-ready map sheet** (24 × 18 in PDF + PNG): a world locator frame, automatically planned
  regional and detail frames so every station can be told apart, area-proportional symbols, a
  likelihood colour scale, a ranked station key, and a footer written from the data;
- **QA evidence**: `qa_report.json` plus per-frame image crops, so a person or an LLM can check the result.

It is built to be driven by an LLM: point Claude (or another coding agent) at this repo and
`AGENTS.md` tells it how to take your table — even a screenshot — through to a finished sheet.

![example sheet](examples/example_25X_sheet_preview.png)
*Synthetic example (`examples/example_25X.csv`) — numbers are made up.*

## Relationship to fgdb-tools

This repo grew out of [fgdb-tools](https://github.com/TheWendarr/fgdb-tools) and keeps its
conventions: `src/` layout, a pyogrio/GDAL `OpenFileGDB` writer, FileGDB-safe layer names,
`unique/skip/fail` handling for existing feature classes, GeoJSON/Shapefile/KML export, frozen
`Options` dataclasses and integer exit codes. `slotmap/fgdb.py` is that writer adapted for this
pipeline; if `fgdb-tools` is installed (`pip install -e ".[fgdb]"`), its name sanitizer is used
directly so both tools name feature classes the same way. Open the `.gdb` in ArcGIS Pro or use
`fgdb-export` on it like any other geodatabase.

## Install

```bash
git clone <this repo> && cd slotmap-tools
python -m pip install -e ".[dev]"     # or: conda env create -f environment.yml
slotmap fetch-basemap                 # Natural Earth layers, ~90 MB, cached once
python -m pytest -q
```

Python ≥ 3.10. FileGDB writing needs GDAL ≥ 3.6 (included in current pyogrio wheels and conda-forge).

## Quick start

```bash
slotmap init work/25D --mos 25D                      # sheet.yaml + blank slots.csv
# put your table in data/ (git-ignored) or fill work/25D/slots.csv
slotmap check data/25D.csv -o work/25D/out           # names -> coordinates; fix anything flagged
slotmap plan  data/25D.csv -c work/25D/sheet.yaml -o work/25D/out   # frame plan + mockup PNG
slotmap run   data/25D.csv -c work/25D/sheet.yaml -o work/25D/out   # everything + QA
```

Try it on the example: `slotmap run examples/example_25X.csv -c examples/example_25X.yaml -o out/example`.

### Input table

CSV, TSV or Excel. Headers are matched loosely; these all work:

| Meaning | Accepted headers (examples) |
|---|---|
| Station | `LOCATION`, `DUTY STATION`, `INSTALLATION`, `POST` |
| State | `STATENAME`, `STATE`, `ST` (`No Title Code Found` = blank) |
| Country | `CNTRY`, `COUNTRY` (blank = USA) |
| Slots | `TOTAUTH`, `AUTHORIZED`, `AUTH`, `SLOTS`, `BILLETS` |
| Percent (optional) | `Total Percent`, `PCT`, `%` |
| Coordinates (optional) | `LAT`/`LATITUDE`, `LON`/`LONGITUDE` |

A trailing row with a number and no station name is read as the table's stated total and checked.
Repeated rows for one installation are combined (and noted on the sheet).

### Station names

`src/slotmap/data/installations.csv` holds about 100 Army and joint installations at home and
overseas, with aliases (including the 2023 names that were reverted in 2025, e.g. Fort Liberty →
Fort Bragg). Add or correct places in your own `gazetteer_local.csv` (same columns; it overrides
the bundled file). When a name can't be resolved the run stops with exit code 3 and writes
`gazetteer_local_TODO.csv` for you to fill.

### Outputs

| File | Contents |
|---|---|
| `<mos>_sheet.pdf / .png` | The map sheet |
| `<mos>_mockup.png` | Layout wireframe for review |
| `<mos>.gdb`, `<mos>.gpkg` | `auth_points`, `duty_stations`, `frame_extents`, `duty_station_table`, `group_totals`, `theater_totals` |
| `duty_station_table.csv` | Ranked stations with frame letters |
| `frame_plan.json` | Frame ids, letters, extents, members |
| `resolution_report.csv` | How each name was matched |
| `qa_report.json`, `crops/` | Checks and review images |

## How the sheet is planned

1. Stations are grouped into region frames: continental U.S., Alaska, Hawaii, U.S. territories,
   and overseas countries (neighbouring countries share a frame).
2. Each frame is sized on the page (largest frame for the most stations) and given an equal-area
   extent that fits its stations.
3. Where symbols would overlap at a frame's scale, those stations move into a **detail frame**
   (e.g. the National Capital Region, Colorado Springs). Nearby clusters share one detail frame.
4. Repeat until nothing overlaps or the page is full; anything still coincident is nudged apart
   in its own frame and the footer says so.
5. Labels are placed automatically; frame letters follow reading order.

Every choice can be overridden in `sheet.yaml` — see [docs/CONFIG.md](docs/CONFIG.md).
Design rationale: [docs/DESIGN.md](docs/DESIGN.md). Function inventory: [docs/FUNCTIONS.md](docs/FUNCTIONS.md).

## Using it with an LLM

Open the repo in Claude Code (or give an agent the folder) and say, for example:

> Build the slot map sheet for this 35F table (screenshot attached). Prepared by SSG McGowan.

`AGENTS.md` (and `CLAUDE.md`, `.claude/skills/build-slot-sheet/`) tell the agent to transcribe
and verify the table, resolve names with sourced coordinates, review the mockup, run QA, look at
every crop, tune labels through config, and report what it changed.

## Caveats

- Coordinates are installation-level approximations, not surveyed points.
- "Likelihood" is the station's share of slots. Real assignment odds depend on vacancies, rank,
  timing and Army needs; the legend says this.
- Authorization data may be controlled (CUI/FOUO) depending on its source. `data/`, `work/` and
  `out/` are git-ignored; keep real tables there and don't push them to a public repo.

## License

MIT — see [LICENSE](LICENSE).
