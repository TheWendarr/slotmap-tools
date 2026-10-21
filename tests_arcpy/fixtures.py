"""Test fixtures written at run time with the standard library (so only .py files need to travel).

write_xlsx() produces a minimal valid .xlsx (inline strings, numbers) - enough for slotmap_arcpy.xlsx.
"""
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HEADER = ["LOCATION", "STATE", "STATENAME", "CNTRY", "POSCO", "POSCOTITLE", "TOTREQ", "TOTAUTH"]
CMF35 = [
    ["FORT HOOD", "TX", "Texas", "USA", "35G1O", "GEOINT", 5, 5],
    ["FORT HOOD", "TX", "Texas", "USA", "35G1S", "GEOINT", 2, 2],
    ["CAMP HUMPHREYS", None, "No Title Code Found", "KOREA, REPUBLIC OF", "35G1F", "GEOINT", 3, 3],
    ["FORT GORDON", "GA", "Georgia", "USA", "35G2O", "GEOINT", 4, 4],
    ["FORT CARSON", "CO", "Colorado", "USA", "35F1O", "INTEL ANALYST", 6, 6],
    ["FORT BRAGG", "NC", "North Carolina", "USA", "35F3O", "INTEL ANALYST", 2, 2],
    ["FORT EUSTIS", "VA", "Virginia", "USA", "35F3O", "INTEL ANALYST", 1, 0],
]
CMF25 = [
    ["FORT GORDON", "GA", "Georgia", "USA", "25D3O", "CYBER NET DEF", 7, 7],
    ["FORT MEADE", "MD", "Maryland", "USA", "25D3O", "CYBER NET DEF", 3, 3],
    ["SCHOFIELD BARRACKS", "HI", "Hawaii", "USA", "25D4O", "CYBER NET DEF", 2, 2],
]


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, rows, sheet="XPERDET"):
    cells = []
    for ri, row in enumerate(rows, 1):
        cs = []
        for ci, v in enumerate(row):
            ref = "{}{}".format(_col(ci), ri)
            if v is None:
                continue
            if isinstance(v, (int, float)):
                cs.append('<c r="{}"><v>{}</v></c>'.format(ref, v))
            else:
                cs.append('<c r="{}" t="inlineStr"><is><t>{}</t></is></c>'.format(ref, escape(str(v))))
        cells.append('<row r="{}">{}</row>'.format(ri, "".join(cs)))
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{}" xmlns:r="{}"><sheets><sheet name="{}" sheetId="1" r:id="rId1"/></sheets></workbook>'.format(ns, rel, escape(sheet)),
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{}"><sheetData>{}</sheetData></worksheet>'.format(ns, "".join(cells)),
    }
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


def write_fixtures(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_xlsx(folder / "CMF35.xlsx", [HEADER] + CMF35)
    write_xlsx(folder / "CMF25.xlsx", [HEADER] + CMF25)
    return folder
