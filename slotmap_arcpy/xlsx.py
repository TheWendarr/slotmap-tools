"""Read .xlsx workbooks with the standard library (zipfile + xml.etree). Values come back as strings.

Handles shared strings, inline strings, numbers, booleans and blank cells. Formulas give their cached value.
Dates are returned as the stored serial number (slot tables have none).
"""
from __future__ import annotations

import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_COL = re.compile(r"^([A-Z]+)(\d+)$")


def _col_index(ref: str) -> int:
    m = _COL.match(ref.upper())
    letters = m.group(1) if m else "A"
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text(el) -> str:
    """Concatenate every <t> under a string item (rich text runs included)."""
    return "".join(t.text or "" for t in el.iter("{%s}t" % NS["m"]))


def _num(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    if f.is_integer() and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def sheet_names(path: Path) -> List[str]:
    with zipfile.ZipFile(str(path)) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        return [s.get("name") for s in wb.find("m:sheets", NS)]


def read_xlsx(path: Path, sheet: Optional[str] = None) -> "OrderedDict[str, List[List[str]]]":
    """Return {sheet name: rows}, each row a list of cell strings ('' for blanks). sheet=None reads all."""
    out = OrderedDict()  # type: OrderedDict
    with zipfile.ZipFile(str(path)) as z:
        names = set(z.namelist())
        shared = []  # type: List[str]
        if "xl/sharedStrings.xml" in names:
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in sst.findall("m:si", NS)]
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.findall("pr:Relationship", NS)}
        for s in wb.find("m:sheets", NS):
            name = s.get("name")
            if sheet is not None and name != sheet:
                continue
            rid = s.get("{%s}id" % NS["r"])
            target = targets.get(rid, "")
            part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            root = ET.fromstring(z.read(part))
            rows = []  # type: List[List[str]]
            data = root.find("m:sheetData", NS)
            next_r = 1
            for row in (data if data is not None else []):
                rnum = int(row.get("r") or next_r)
                while next_r < rnum:          # keep blank rows so row numbers stay aligned
                    rows.append([])
                    next_r += 1
                cells = {}  # type: Dict[int, str]
                for i, c in enumerate(row.findall("m:c", NS)):
                    ref = c.get("r")
                    ci = _col_index(ref) if ref else i
                    t = c.get("t", "n")
                    v = c.find("m:v", NS)
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        is_ = c.find("m:is", NS)
                        val = _text(is_) if is_ is not None else ""
                    elif t == "b" and v is not None:
                        val = "TRUE" if v.text == "1" else "FALSE"
                    elif v is not None and v.text is not None:
                        val = v.text if t in ("str", "e") else _num(v.text)
                    else:
                        val = ""
                    cells[ci] = val
                width = max(cells) + 1 if cells else 0
                rows.append([cells.get(i, "") for i in range(width)])
                next_r = rnum + 1
            out[name] = rows
    if sheet is not None and sheet not in out:
        raise ValueError("No sheet named {!r} in {}".format(sheet, path))
    return out


def rows_to_records(rows: List[List[str]]) -> List["OrderedDict[str, str]"]:
    """First non-empty row is the header. Fully blank rows are dropped."""
    rows = [r for r in rows]
    while rows and not any(str(v).strip() for v in rows[0]):
        rows.pop(0)
    if not rows:
        return []
    header = [str(h).strip() for h in rows[0]]
    seen = {}  # type: Dict[str, int]
    for i, h in enumerate(header):        # de-duplicate header names
        if h in seen:
            seen[h] += 1
            header[i] = "{}_{}".format(h, seen[h])
        else:
            seen[h] = 0
    out = []
    for r in rows[1:]:
        if not any(str(v).strip() for v in r):
            continue
        rec = OrderedDict()
        for i, h in enumerate(header):
            rec[h or "COL{}".format(i + 1)] = r[i] if i < len(r) else ""
        out.append(rec)
    return out
