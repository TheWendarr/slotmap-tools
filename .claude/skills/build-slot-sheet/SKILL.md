---
name: build-slot-sheet
description: Build an authorized-slots-by-duty-station map sheet (PDF/PNG + FileGDB/GeoPackage) for an MOS table with slotmap-tools. Use when the user provides a slot/authorization table, screenshot, or spreadsheet and wants the map sheet or GIS layers.
---

# Build a slot map sheet

1. Read `AGENTS.md` at the repo root. It is the full procedure; this skill is the checklist.
2. Table in hand as CSV/XLSX? If it is an image or PDF, transcribe it to the columns in
   `templates/slots_template.csv`, keep the grand-total row, and compare against the source row by row.
3. `slotmap check <table> -o work/<MOS>` → resolve every `fuzzy`/`unresolved` station via
   `gazetteer_local.csv` with a sourced coordinate. Never guess coordinates.
4. `slotmap init work/<MOS> --mos <MOS>` → set `mos`, `title`, `prepared_by` in `sheet.yaml`.
5. `slotmap plan …` → view `<slug>_mockup.png`; rename detail frames via `frame_overrides`.
6. `slotmap run …` → open `crops/overview.png` and every `crops/frame_*.png`; read `qa_report.json`.
   Fix labels with `label_overrides`, re-run until QA is `pass` (or every `warn` is explained).
7. Deliver the sheet PDF/PNG and the `.gdb`/`.gpkg`; state totals, exclusions, merges, added
   coordinates (with sources) and remaining warnings. Keep real tables out of git (`data/`, `work/`).
