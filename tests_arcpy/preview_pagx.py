"""Developer tool (needs matplotlib; not used on the ArcGIS machine): draw a .pagx written by slotmap_arcpy
so the page composition can be checked without ArcGIS Pro. Map frames are drawn as plain boxes.

    python tests_arcpy/preview_pagx.py out/35g3_layout.pagx preview.png
"""
import json
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402

TAG = re.compile(r"</?[A-Z]+[^>]*>")


def col(c):
    v = c["values"]
    return (v[0] / 255, v[1] / 255, v[2] / 255, v[3] / 100 if len(v) > 3 else 1)


def main(src, dst):
    doc = json.load(open(src, encoding="utf-8"))
    lay = doc["layoutDefinition"]
    W, H = lay["page"]["width"], lay["page"]["height"]
    fig = plt.figure(figsize=(W, H), dpi=60)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    maps = {m["uRI"]: m for m in doc["mapDefinitions"]}
    for z, e in enumerate(reversed(lay["elements"])):
        if e["type"] == "CIMMapFrame":
            ring = e["frame"]["rings"][0]
            m = maps[e["view"]["viewableObjectPath"]]
            ax.add_patch(Polygon([p[:2] for p in ring], closed=True, fc=col(m["backgroundColor"]), ec="#1f2328",
                                 lw=1, zorder=z))
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            ax.text(sum(xs) / len(xs), sum(ys) / len(ys), m["name"], ha="center", va="center", fontsize=14,
                    color="#8d8f94", zorder=z)
            continue
        g = e["graphic"]
        if g["type"] == "CIMPolygonGraphic":
            ring = g["polygon"]["rings"][0]
            sl = g["symbol"]["symbol"]["symbolLayers"]
            stroke = next(l for l in sl if l["type"] == "CIMSolidStroke")
            fill = next(l for l in sl if l["type"] == "CIMSolidFill")
            ax.add_patch(Polygon([p[:2] for p in ring], closed=True,
                                 fc=col(fill["color"]) if fill["enable"] else "none",
                                 ec=col(stroke["color"]) if stroke["enable"] else "none",
                                 lw=stroke["width"], zorder=z))
        elif g["type"] == "CIMTextGraphic":
            ts = g["symbol"]["symbol"]
            fill = ts["symbol"]["symbolLayers"][0]
            text = TAG.sub("", g["text"])
            ax.text(g["shape"]["x"], g["shape"]["y"], text, fontsize=ts["height"],
                    ha=ts["horizontalAlignment"].lower(),
                    va={"Center": "center", "Top": "top", "Bottom": "bottom", "Baseline": "baseline"}[ts["verticalAlignment"]],
                    weight="bold" if "Bold" in ts["fontStyleName"] else "normal",
                    style="italic" if "Italic" in ts["fontStyleName"] else "normal",
                    color=col(fill["color"]), family="DejaVu Sans", linespacing=1.2, zorder=z)
    fig.savefig(dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
