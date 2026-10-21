# CLAUDE.md

Follow **AGENTS.md** in this folder — it is the operating guide for building a slot map sheet with this repo
(setup, transcribing tables, resolving station names, config tuning, QA review, delivery, rules).

Position exports (POSCO tables), any number of MOSs/CMFs — one command:

```bash
slotmap batch data/*.xlsx -o work/batch --prepared-by "<name>"   # exit 3 -> fill gazetteer_local_TODO.csv, re-run
```

Single table, step by step:

```bash
slotmap check data/<table>.csv -o work/<MOS>            # fix names until exit code 0
slotmap plan  data/<table>.csv -c work/<MOS>/sheet.yaml -o work/<MOS>/out   # look at the mockup
slotmap run   data/<table>.csv -c work/<MOS>/sheet.yaml -o work/<MOS>/out   # look at crops/, read qa_report.json
```

Tune with `sheet.yaml` and `gazetteer_local.csv`, not code. Run `python -m pytest -q` after any code change.
