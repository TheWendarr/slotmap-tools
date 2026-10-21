# AGENTS.md — instructions for an LLM building a slot map sheet

> **ArcPy edition.** If the machine has only the standard library and ArcPy (ArcGIS Pro), use
> `python -m slotmap_arcpy …` instead of `slotmap …` (same commands, JSON configs instead of YAML, a template
> project from `pro_template/TEMPLATE.md`). Read `ARCPY.md` first. Do not install packages on such a machine.

You are operating **slotmap-tools**: it turns a table of authorized slots by duty station (any MOS)
into GIS layers (GeoPackage + Esri FileGDB) and a print-ready multi-frame map sheet (PDF/PNG).
Your job is to get the user's table into the tool cleanly, run it, check the result, and tune the
sheet through **configuration**, not code edits.

Read this file fully before running anything. `docs/FUNCTIONS.md` lists every function by stage;
`docs/CONFIG.md` lists every config key; `docs/DESIGN.md` explains the cartographic rules.

---

## 0. Setup (once per machine)

```bash
python -m pip install -e ".[dev]"      # or: conda env create -f environment.yml && conda activate slotmap
slotmap fetch-basemap                   # Natural Earth, ~90 MB, cached in ~/.cache/slotmap/natural_earth
python -m pytest -q                     # should pass offline
```

FileGDB output needs a GDAL build with OpenFileGDB **write** support (GDAL ≥ 3.6; pip wheels of
pyogrio and conda-forge both have it). If it is missing the run still succeeds and writes the GeoPackage.

## 0.5 Position exports (POSCO tables): use `batch`

If the user hands you one or more standard position exports (columns like `LOCATION, STATE, CNTRY,
POSCO, …, TOTAUTH`) and wants maps by skill level / MOS / CMF, run **one command** instead of steps 1–5:

```bash
slotmap batch data/*.xlsx -o work/batch --prepared-by "<name>"     # add --by both for all-levels sheets too
```

- Exit `3`: open `work/batch/gazetteer_local_TODO.csv` (one row per missing station, with the sheets that
  need it), add sourced coordinates as in step 2, save as `gazetteer_local.csv`, re-run the same command.
- Then review as in step 5: `work/batch/index.md` first, then each `…/out/crops/overview.png`.
- Tune through `overrides/<MOS>.yaml` or `overrides/<MOS><level>.yaml` (same keys as `sheet.yaml`), not by
  editing the generated `sheet.yaml` files — those are rewritten on every run.
- Stations you confirm belong in `src/slotmap/data/installations.csv` (with the source in `notes`) when the
  user wants them kept for every future export; otherwise in `gazetteer_local.csv`.

## 1. Get the table into a file

Accepted: `.csv`, `.tsv`, `.xlsx`, `.xls`. Header names are matched loosely (`LOCATION`,
`DUTY STATION`, `TOTAUTH`, `AUTHORIZED`, `Total Percent`, `CNTRY`, …). Optional `LAT`/`LON`
columns override the gazetteer for that row.

**If the user gives a screenshot or PDF**, transcribe it into `templates/slots_template.csv`
format yourself:

- One row per source row, in source order. Copy names exactly as printed (do not "fix" them).
- Keep repeated rows (e.g. a station listed twice) — the tool merges them and notes it.
- Copy the grand-total row as the last row with an empty station name (`,,,223,`).
- Percent column is optional; copy it if present.
- After writing the file, **re-read the image and compare row by row**. Then run `slotmap check`
  (step 2) — it fails if the rows don't sum to the total row, which catches most misreads.

Never invent slot counts. If a value is unreadable, ask the user.

## 2. Resolve station names → coordinates

```bash
slotmap check path/to/table.csv -o work/<MOS>
```

Exit code `3` means some stations need attention. Open `work/<MOS>/resolution_report.csv`:

| RES_STATUS | Meaning | What you do |
|---|---|---|
| `exact`, `normalized`, `table` | Found | Nothing |
| `fuzzy` | Close match by spelling | Confirm the suggestion is the same place. If yes, add the input name as an alias in `gazetteer_local.csv` (preferred) or set `accept_fuzzy: true` in the config |
| `unresolved` | Not in the gazetteer | Add a row to `gazetteer_local.csv` |

`work/<MOS>/gazetteer_local_TODO.csv` is pre-filled with the rows to add. Fill `id`, `name`,
`state` (postal code, U.S. only), `country`, `lat`, `lon`, keep the original spelling in `aliases`,
save it as **`gazetteer_local.csv`** in the working directory (or pass `--gazetteer`), and re-run
`check`. Look things up with `slotmap gazetteer find "FT SOMEWHERE"`.

Coordinates rule: use a public source (installation website, Wikipedia infobox, GNIS/GeoNames) or
ask the user. Do **not** guess. Put the source in `notes` and set `coord_quality` to `approx`
(installation-level) or `surveyed`. Installation renames (Fort Liberty ↔ Fort Bragg, etc.) are
aliases of one row, not two rows.

## 3. Configure the sheet

```bash
slotmap init work/<MOS> --mos 25D        # writes work/<MOS>/sheet.yaml + a blank slots.csv
```

Set at least `mos` and, if wanted, `title`, `prepared_by`, `value_label`. Leave the rest at
defaults on the first run.

## 4. Plan and review the layout

```bash
slotmap plan path/to/table.csv -c work/<MOS>/sheet.yaml -o work/<MOS>/out
```

This writes `frame_plan.json` and `<slug>_mockup.png` (a wireframe: which frames exist, what each
holds, and which frame is a detail of which). Look at the mockup image. Typical adjustments,
all in `sheet.yaml`:

- A detail frame has a poor auto title (named from the nearest city) →
  `frame_overrides: {"<frame id>": {title: "Rhine–Main & Kaiserslautern"}}`. Frame ids are in
  `frame_plan.json` (`fid`), e.g. `germany-poland/clay`.
- A frame is too tight or too wide → `halfwidth_km` (and optionally `center: [lon, lat]`) in the
  same override.
- Too many / too few detail frames → `plan.cluster_gap_pt` (higher = more detail frames),
  `plan.max_frames` (≤ 13).
- Overseas countries grouped wrongly → `plan.region_merge_km`.

## 5. Build everything and run QA

```bash
slotmap run path/to/table.csv -c work/<MOS>/sheet.yaml -o work/<MOS>/out
```

Outputs in `work/<MOS>/out/`:

| File | What |
|---|---|
| `<slug>_sheet.pdf` / `.png` | The map sheet (PDF is vector, fonts embedded) |
| `<slug>_mockup.png` | Layout wireframe |
| `<slug>.gpkg`, `<slug>.gdb` | Layers `auth_points`, `duty_stations`, `frame_extents`; tables `duty_station_table`, `group_totals`, `theater_totals` |
| `duty_station_table.csv` | Ranked station table with frame letters |
| `frame_plan.json` | Every frame: id, letter, title, extent, members, displacement notes |
| `qa_report.json` | Pass/warn/fail per check, footer text, per-frame label stats |
| `crops/frame_*.png`, `crops/panel.png`, `crops/overview.png` | Images for visual review |

**Always look at the images** — open `crops/overview.png`, then each `crops/frame_*.png`. Check:

1. Every station label is readable and next to its own circle; no label is cut off at a frame edge.
2. Labels don't sit on top of each other; leader lines point at the right circle.
3. Lettered boxes on parent frames match the frame letters.
4. The station key shows every station, and the totals in the header/subtitle match the table.
5. Footer notes (exclusions, merged duplicates, displaced symbols) are true.

Fix label problems with `label_overrides` (keyed by station `GID` from `duty_station_table.csv`
or the raw station name): `{"hood": {dir: "S"}}`, `{"white_house": {dir: "N", dy: 14}}`
(non-zero `dx`/`dy` draws a leader line). Re-run `slotmap run` and look again. Repeat until the
QA status is `pass` (or `warn` with every warning explained to the user).

`qa_report.json` statuses: `fail` (totals don't reconcile, a station is missing or outside its
frame — must fix), `warn` (label overlaps, fuzzy matches, planner compromises — review), `info`
(symbol displacement — make sure the footer note is acceptable).

## 6. Deliver

Give the user the sheet PDF/PNG and the `.gdb`/`.gpkg`, and say in plain words: how many slots and
stations were mapped, anything excluded or merged, any coordinates you added and their sources,
and any QA warnings left. Do not claim the locations are surveyed — they are installation-level.

---

## Rules

- **Config, not code.** For one sheet, change `sheet.yaml` or `gazetteer_local.csv`. Change code
  only to fix a bug or add a capability, then add a test in `tests/` and run `pytest`.
- **Totals are sacred.** Mapped + not-mapped slots must equal the table total; the QA check enforces it.
- **Share ≠ probability.** The likelihood classes are a proxy (share of slots). Keep the caveat.
- **Data handling.** Authorization tables can be controlled information (CUI/FOUO) depending on
  the source. Keep real tables in `data/` or `work/` (both git-ignored) and do not commit or upload
  them anywhere the user hasn't approved. Installation coordinates in the gazetteer are public.

## Exit codes

`0` ok · `1` QA fail · `2` input/validation error (see `ingest_errors.json`) · `3` stations need coordinates/confirmation.

## Repo map

```
src/slotmap/
  ingest.py      stage 1  read + normalize + validate the table
  gazetteer.py   stage 2  name → coordinates (data/installations.csv + gazetteer_local.csv)
  aggregate.py   stage 3  merge duplicates, shares, theaters, classes, ranks
  products.py    stage 4  feature classes → GeoPackage / FileGDB / exports
  fgdb.py                 FileGDB writer adapted from fgdb-tools
  plan.py        stage 5  regions, clustering → detail frames, extents, displacement
  layout.py               page rows/slots, frame letters
  basemap.py              Natural Earth fetch/cache/clip
  labels.py      stage 6  automatic label placement
  render.py      stage 6  the sheet
  mockup.py      stage 6  layout wireframe
  qa.py          stage 7  checks, crops, report
  pipeline.py    stage 8  orchestration (RunOptions → exit code)
  cli.py                  `slotmap` command
```
