# Cartographic design rules

These are the principles from the original hand-built sheet, and where the code enforces each one.

| # | Principle | Why | Where in code |
|---|---|---|---|
| 1 | **World frame first**, showing theater totals, with lettered boxes for every frame | Answers "where on the globe" before any detail | `render.SheetRenderer.draw_world`, `draw_extent_boxes` |
| 2 | **Frame letters in reading order**, repeated on boxes, frame headers and the station key | One lookup path from a key row to its frame | `layout.letters_in_reading_order` |
| 3 | **Detail frames only where symbols collide** at the parent's scale; clusters that sit together share one frame | Every station is distinguishable without adding needless frames | `plan.find_clusters`, `plan.plan_sheet` |
| 4 | **Visual hierarchy**: the frame with the most stations gets the largest slot | Reader's eye goes where most of the information is | `layout.assign` |
| 5 | **One symbol scale on the whole sheet**, area proportional to slots | A 5-slot circle in Korea is the same size as a 5-slot circle in Texas | `plan.symbol_scales` (`symbol_k`) |
| 6 | **Redundant encoding**: size = slots, colour = share class (YlOrRd, colour-blind safe) | Works in greyscale print and for colour-blind readers | `config.ClassScheme`, `aggregate.classify` |
| 7 | **Equal-area projections** everywhere (Equal Earth, Albers, LAEA centred per frame) | Circle areas are not inflated by projection | `plan.crs_for` |
| 8 | **Automatic labels** with two lines (name; slots · %), leader lines only when needed | Labels stay attached to their symbols without manual placement | `labels.LabelPlacer` |
| 9 | **Muted basemap** (figure–ground): grey land, pale water, focus country slightly darker | The warm symbols are the only saturated colour | `render` palette constants, `draw_basemap` |
| 10 | **Footer written from the data**: sources, exclusions, merges, displaced symbols, accuracy | The sheet carries its own caveats | `render.draw_footer` |
| 11 | **Honest displacement**: coincident points are nudged apart only in their owning frame, and the footer says by how much | Distinguishable without silently moving data | `plan.displace_coincident` |
| 12 | **Likelihood is labelled as a proxy** | Share of slots is not a probability of assignment | legend caveat in `render.draw_panel` |

## Layout grammar

```
┌──────────────── title ─────────────────────────────── KPI tiles ┐
│ W world (47%)        │ slot │ slot │                │  legend    │
│──────────────────────┴──────┴──────│                │  theater   │
│ main frame (largest)     │ side │ s│                │  bars      │
│                          │ col. │ s│                │  station   │
│───────────────────────────────────── bottom strip ─ │  key       │
│ s │ s │ s │ s │ s │ s                               │            │
└──────────────────────── footer ────────────────────────────────┘
```

`layout.make_slots` fills rows in this order: 2 slots beside the world frame, up to 3 in the side
column, up to 6 in the bottom strip. Rows that are not needed are removed and the others grow.
