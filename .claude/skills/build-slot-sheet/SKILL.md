---
name: build-slot-sheet
description: Build an authorized-slots-by-duty-station map sheet (PDF/PNG + FileGDB/GeoPackage) for an MOS table with slotmap-tools. Use when the user provides a slot/authorization table, screenshot, or spreadsheet and wants the map sheet or GIS layers.
---

# Build a slot map sheet

1. Read `AGENTS.md` at the repo root. It is the full procedure; this skill is the checklist.
2. Standard position export(s) with a POSCO column, maps wanted per skill level / MOS / CMF? Run
   `slotmap batch <files or folder> -o work/batch --prepared-by "<name>"` and go to step 7 for review
   (`work/batch/index.md`, then each sheet's `out/crops/`). Exit 3 → fill `work/batch/gazetteer_local_TODO.csv`
   with sourced coordinates, save as `gazetteer_local.csv`, re-run. Tune via `overrides/<MOS>.yaml`.
   Otherwise continue with step 3.
3. Table in hand as CSV/XLSX? If it is an image or PDF, transcribe it to the columns in
   `templates/slots_template.csv`, keep the grand-total row, and compare against the source row by row.
4. `slotmap check <table> -o work/<MOS>` → resolve every `fuzzy`/`unresolved` station via
   `gazetteer_local.csv` with a sourced coordinate. Never guess coordinates.
5. `slotmap init work/<MOS> --mos <MOS>` → set `mos`, `title`, `prepared_by` in `sheet.yaml`.
6. `slotmap plan …` → view `<slug>_mockup.png`; rename detail frames via `frame_overrides`.
7. `slotmap run …` → open `crops/overview.png` and every `crops/frame_*.png`; read `qa_report.json`.
   Fix labels with `label_overrides`, re-run until QA is `pass` (or every `warn` is explained).
8. Deliver the sheet PDF/PNG and the `.gdb`/`.gpkg`; state totals, exclusions, merges, added
   coordinates (with sources) and remaining warnings. Keep real tables out of git (`data/`, `work/`).
