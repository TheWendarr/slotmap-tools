# The template project (one time, about 5 minutes)

ArcPy in ArcGIS Pro 2.x can't create layouts, map frames or text from code. So the tool starts from a
small template layout you make once. It copies that layout's map frame, text and rectangle for every
frame, label and box on the sheet. **Their size, position, font and colour don't matter** — the tool
sets all of those.

## Make it

1. Open ArcGIS Pro and create a new project from the **Map** template.
   - Name: `slotmap_template`
   - Location: this `pro_template` folder
   - Uncheck **Create a new folder for this project**, so the result is `pro_template\slotmap_template.aprx`.
2. **Insert ▸ New Layout** — any page size.
3. Rename the layout to exactly `slotmap_template`. In the **Catalog** pane, open **Layouts**, then
   right-click the layout and choose **Rename**.
4. With the layout open, choose **Insert ▸ Map Frame** and pick the project's map. Drag a box anywhere on the page.
5. **Insert ▸ Text** — use the plain **Text** tool (point text, the "A"). Click the page and type anything.
6. **Insert ▸ Rectangle** — drag a box anywhere.
7. **Save** the project, then close ArcGIS Pro so the file isn't locked.

## Check it

From the repo folder, in the ArcGIS Pro **Python Command Prompt**:

```
python -m slotmap_arcpy check-template
```

You should see one `[OK]` line each for the layout, map frame, text and rectangle. If one is missing,
the error names it. Add it in Pro and save again.

## Notes

- The template map's own layers (the default basemap) are not used. Every sheet gets fresh maps.
- To keep the template somewhere else, pass `--template path\to\file.aprx`.
- Each built sheet is also saved as its own project (`<sheet>.aprx` next to the PDF). You can open it in Pro
  to adjust anything by hand. The template itself is never changed.
