# Config reference (`sheet.yaml`)

Every key is optional. `slotmap init <folder> --mos <MOS>` writes a file with all defaults.

## Top level

| Key | Default | Meaning |
|---|---|---|
| `title` | `Where the Slots Are: Authorized Positions by Duty Station` | Sheet title. With the default title, `mos` is inserted automatically. |
| `subtitle` | `auto` | `auto` writes totals, stations mapped, exclusions, and how to read size/colour. |
| `mos` | `""` | MOS tag; also used for output file names. |
| `prepared_by` | `""` | Footer credit. |
| `date` | `auto` | Footer date; `auto` = today. |
| `value_label` | `slots` | What one unit is called (`slots`, `authorizations`, `billets`). |
| `exclude_zero` | `true` | Stations with 0 slots are listed in the footer instead of mapped. |
| `accept_fuzzy` | `false` | Accept every spelling-similarity gazetteer match without confirmation. |
| `fuzzy_auto_accept` | `0.92` | Matches at least this similar are accepted anyway and listed in the footer (e.g. `JBSA FT SAM HOUST` → JBSA-Fort Sam Houston). |
| `use_places` | `true` | Place `(CITY)` names, and otherwise-unknown town names, at the town centre from the GeoNames town list. Footnoted on the sheet. |
| `symbol_max_diameter_pt` | `36` | Diameter of the largest station circle. Everything else scales by area. |
| `world_max_diameter_pt` | `64` | Cap for theater circles on the world frame (the legend says when it applies). |
| `fonts` | `[Inter, Arial, Helvetica, DejaVu Sans]` | First installed family is used. |
| `outputs` | `[pdf, png]` | Any of `pdf`, `png`, `svg`. |
| `notes` | `[]` | Extra footer sentences. |

## `classes` — likelihood classes

| Key | Default |
|---|---|
| `breaks_pct` | `[2, 5, 10]` — share-of-slots boundaries (a share equal to a break goes to the upper class) |
| `labels` | `[Less likely, Possible, Likely, Most likely]` |
| `colors` | `["#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]` (ColorBrewer YlOrRd, colour-blind safe) |

`len(labels) == len(colors) == len(breaks_pct) + 1`.

## `page`

| Key | Default | Meaning |
|---|---|---|
| `width_in`, `height_in` | `24`, `18` | ARCH C landscape. 17×11 works with smaller type. |
| `margin_in` | `0.4` | |
| `panel_width_in` | `5.25` | Right-hand legend / key column. |
| `dpi` | `200` | PNG resolution. |

## `plan` — how frames are chosen

| Key | Default | Meaning |
|---|---|---|
| `max_frames` | `13` | Including the world frame. The layout holds at most 13. |
| `region_merge_km` | `900` | Overseas countries whose nearest stations are closer than this share a frame. |
| `cluster_gap_pt` | `1.0` | Symbols closer than this edge-to-edge gap at a frame's scale trigger a detail frame. Raise it for more detail frames. |
| `cluster_merge_pt` | `30` | Colliding clusters closer than this share one detail frame. |
| `min_halfwidth_km` | `15` | Smallest detail frame half-width; below this, symbols are displaced instead. |
| `region_min_halfwidth_km` | `30` | Smallest half-width for a multi-station region frame. |
| `single_halfwidth_km` | `60` | Half-width for a frame with one station. |
| `pad_frac` | `0.22` | Padding around the stations (fraction of their span), room for labels. |
| `max_depth` | `2` | Region → detail. |
| `displace_gap_pt` | `2.0` | Gap enforced between near-coincident symbols in their owning frame. |

## `frame_overrides`

Keyed by frame id (`fid` in `frame_plan.json`) or by current title:

```yaml
frame_overrides:
  germany-poland/clay: {title: "Rhine–Main & Kaiserslautern"}
  korea: {halfwidth_km: 180}
  conus/white_house: {title: "National Capital Region", center: [-77.05, 38.97], halfwidth_km: 42}
```

## `label_overrides`

Keyed by station `GID` (column in `duty_station_table.csv`) or raw station name:

```yaml
label_overrides:
  hood: {dir: S}                         # E W N S NE NW SE SW
  white_house: {dir: N, dx: 0, dy: 16}   # non-zero dx/dy (points) draws a leader line
  pentagon: {name: "The Pentagon"}       # change the printed name only
```

## Batch overrides (`overrides/`)

`slotmap batch` writes a `sheet.yaml` for every sheet. To tune sheets so the tuning survives the next
export, put partial configs in an `overrides/` folder (or pass `--overrides DIR`). They are merged on top
of the generated config, most general first:

| File | Applies to |
|---|---|
| `35.yaml` or `CMF35.yaml` | every sheet in CMF 35 |
| `35G.yaml` | every 35G sheet |
| `35G_SL3.yaml` or `35G3.yaml` | only the 35G skill level 3 sheet |
| `255A_CW2.yaml` | only the 255A CW2 sheet |

Any key from this page works, e.g.

```yaml
# overrides/35G.yaml
prepared_by: SSG McGowan
frame_overrides:
  conus/stewart: {title: "Fort Stewart & Hunter AAF"}
label_overrides:
  gordon: {dir: W}
```

`notes` lists are added to the generated notes rather than replacing them.
