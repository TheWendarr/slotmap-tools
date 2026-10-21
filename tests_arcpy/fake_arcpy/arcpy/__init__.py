"""A stand-in for ArcPy (ArcGIS Pro 2.9 API surface used by slotmap_arcpy) for tests on machines without
ArcGIS. It records calls, checks argument shapes, and validates the .pagx JSON the renderer writes. It does
not draw anything. Only the classes, methods and properties documented for Pro 2.9 exist here, so a call to
something 2.x does not have fails the tests.
"""
import copy
import json
import os
from types import SimpleNamespace

CALLS = []


def _log(*a):
    CALLS.append(a)


class _Env(object):
    overwriteOutput = False


env = _Env()
_FS = {}       # path -> {"type": ..., "fields": [...], "rows": [...]}


def Exists(p):
    return str(p) in _FS or os.path.exists(str(p))


class Point(object):
    def __init__(self, X=0.0, Y=0.0):
        self.X, self.Y = float(X), float(Y)


class Array(list):
    pass


class Polygon(object):
    def __init__(self, arr, sr=None):
        assert all(isinstance(p, Point) for p in arr)
        self.points, self.sr = list(arr), sr


class SpatialReference(object):
    def __init__(self, item=None):
        self.item, self.wkt = item, None

    def loadFromString(self, s):
        assert s.startswith("PROJCS[") or s.startswith("GEOGCS["), s[:30]
        assert s.count("[") == s.count("]"), "unbalanced WKT"
        self.wkt = s


class Extent(object):
    def __init__(self, XMin, YMin, XMax, YMax):
        assert XMax > XMin and YMax > YMin
        self.XMin, self.YMin, self.XMax, self.YMax = XMin, YMin, XMax, YMax


# ------------------------------------------------------------------ geoprocessing
class _Mgmt(object):
    def CreateFileGDB(self, folder, name):
        p = os.path.join(str(folder), name)
        os.makedirs(p, exist_ok=True)
        _FS[p] = {"type": "gdb"}
        _log("CreateFileGDB", p)

    def CreateFeatureclass(self, ws, name, geom, spatial_reference=None):
        assert geom in ("POINT", "POLYGON", "POLYLINE")
        assert str(ws) in _FS, "workspace must exist"
        _FS[os.path.join(str(ws), name)] = {"type": geom, "fields": [], "rows": []}

    def CreateTable(self, ws, name):
        _FS[os.path.join(str(ws), name)] = {"type": "TABLE", "fields": [], "rows": []}

    def AddField(self, tbl, name, ftype, field_length=None):
        assert ftype in ("TEXT", "LONG", "DOUBLE", "SHORT", "FLOAT", "DATE")
        assert len(name) <= 64 and name.upper() not in ("WHERE", "SELECT", "FROM", "ORDER")
        _FS[tbl]["fields"].append(name)

    def Delete(self, p):
        _FS.pop(str(p), None)

    def CreateSQLiteDatabase(self, path, spatial_type):
        assert spatial_type.startswith("GEOPACKAGE")
        open(str(path), "w").close()
        _FS[str(path)] = {"type": "gpkg"}


class _Conv(object):
    def FeatureClassToFeatureClass(self, src, out_path, name):
        assert src in _FS, src
        _FS[os.path.join(out_path, name)] = copy.deepcopy(_FS[src])

    def TableToTable(self, src, out_path, name):
        assert src in _FS, src
        _FS[os.path.join(out_path, name)] = copy.deepcopy(_FS[src])

    def ExcelToTable(self, *a):
        raise NotImplementedError


management, conversion = _Mgmt(), _Conv()


class _Cursor(object):
    def __init__(self, tbl, fields):
        assert tbl in _FS, tbl
        for f in fields:
            assert f in ("SHAPE@XY", "SHAPE@") or f in _FS[tbl]["fields"], (tbl, f)
        self.tbl, self.fields = tbl, fields

    def insertRow(self, row):
        assert len(row) == len(self.fields)
        _FS[self.tbl]["rows"].append(list(row))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


da = SimpleNamespace(InsertCursor=_Cursor)


# ------------------------------------------------------------------ CIM
class _CIM(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _CreateCIM(name, version):
    assert version == "V2"
    return _CIM(type=name)


cim = SimpleNamespace(CreateCIMObjectFromClassName=_CreateCIM)


# ------------------------------------------------------------------ mapping
class Symbol(object):
    _props = ("angle", "color", "outlineColor", "outlineWidth", "size", "useRealWorldUnits")

    def __init__(self):
        object.__setattr__(self, "_v", {})

    def __setattr__(self, k, v):
        assert k in self._props, "Symbol has no property {}".format(k)
        if k in ("color", "outlineColor"):
            assert isinstance(v, dict) and list(v) == ["RGB"] and len(v["RGB"]) == 4, v
        self._v[k] = v

    def __getattr__(self, k):
        return self._v.get(k)


class _Item(object):
    def __init__(self, value):
        self.values, self.symbol, self.label, self.description = [[value]], Symbol(), str(value), ""


class SimpleRenderer(object):
    type = "SimpleRenderer"

    def __init__(self):
        self.symbol = Symbol()


class UniqueValueRenderer(object):
    type = "UniqueValueRenderer"

    def __init__(self, layer):
        self._layer, self._fields, self.groups = layer, [], []

    @property
    def fields(self):
        return self._fields

    @fields.setter
    def fields(self, v):
        self._fields = list(v)
        vals = sorted({str(r[self._layer._fidx(v[0])]) for r in self._layer._rows()})
        self.groups = [SimpleNamespace(heading=v[0], items=[_Item(x) for x in vals])]


class Symbology(object):
    def __init__(self, layer):
        self._layer = layer
        self.renderer = SimpleRenderer()

    def updateRenderer(self, t):
        assert t in ("SimpleRenderer", "UniqueValueRenderer", "GraduatedColorsRenderer", "GraduatedSymbolsRenderer")
        self.renderer = SimpleRenderer() if t == "SimpleRenderer" else UniqueValueRenderer(self._layer)


class Layer(object):
    def __init__(self, path):
        self.dataSource, self.name, self.definitionQuery, self.showLabels = path, os.path.basename(path), "", False
        self._sym = Symbology(self)
        tc = _CIM(type="CIMTextSymbol", height=8.0, fontStyleName="Regular")
        self._cim = _CIM(labelClasses=[_CIM(expression="$feature.NAME", expressionEngine="Arcade", whereClause="",
                                            visibility=False, textSymbol=_CIM(symbol=tc),
                                            maplexLabelPlacementProperties=_CIM(neverRemoveLabel=False))],
                         labelVisibility=False)

    def _fidx(self, f):
        t = _FS.get(self.dataSource)
        return t["fields"].index(f) + 1        # rows start with the shape

    def _rows(self):
        t = _FS.get(self.dataSource) or {"rows": []}
        return t["rows"]

    @property
    def symbology(self):
        return self._sym

    @symbology.setter
    def symbology(self, v):
        assert isinstance(v, Symbology)
        self._sym = v

    def getDefinition(self, v):
        assert v == "V2"
        return self._cim

    def setDefinition(self, c):
        self._cim = c

    def listLabelClasses(self):
        return [SimpleNamespace(expression="", SQLQuery="", visible=True, name="Class 1")]


class Map(object):
    def __init__(self, d):
        self.name, self.URI, self._def = d["name"], d["uRI"], d
        self.layers = []
        self._sr = None

    @property
    def spatialReference(self):
        return self._sr

    @spatialReference.setter
    def spatialReference(self, v):
        assert isinstance(v, SpatialReference) and v.wkt
        self._sr = v

    def addDataFromPath(self, p):
        assert os.path.exists(p) or p in _FS, "missing data: " + p
        lyr = Layer(p)
        self.layers.insert(0, lyr)
        return lyr

    def listLayers(self, wildcard=None):
        return list(self.layers)


class _Camera(object):
    def __init__(self):
        self.ext = None

    def setExtent(self, e):
        assert isinstance(e, Extent)
        self.ext = e


class MapFrame(object):
    type = "MAPFRAME_ELEMENT"

    def __init__(self, name, m):
        self.name, self.map, self.camera = name, m, _Camera()

    def exportToPNG(self, out_png, width=640, height=480, resolution=96, **kw):
        open(out_png, "wb").close()


class Layout(object):
    def __init__(self, doc, aprx):
        self._doc, self._aprx = doc, aprx
        self.name = doc["layoutDefinition"]["name"]
        self.pageWidth = doc["layoutDefinition"]["page"]["width"]
        self.pageHeight = doc["layoutDefinition"]["page"]["height"]
        maps = {m["uRI"]: m for m in doc.get("mapDefinitions", [])}
        self._frames = []
        for e in doc["layoutDefinition"]["elements"]:
            if e["type"] == "CIMMapFrame":
                uri = e["view"]["viewableObjectPath"]
                m = aprx._maps.setdefault(uri, Map(maps[uri]))
                self._frames.append(MapFrame(e["name"], m))

    def exportToPAGX(self, out):
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(self._doc, fh)

    def listElements(self, element_type=None, wildcard=None):
        assert element_type in (None, "MAPFRAME_ELEMENT", "TEXT_ELEMENT", "GRAPHIC_ELEMENT", "LEGEND_ELEMENT")
        return list(self._frames) if element_type in (None, "MAPFRAME_ELEMENT") else []

    def exportToPDF(self, out_pdf, resolution=96, **kw):
        open(out_pdf, "wb").close()
        _log("exportToPDF", out_pdf)

    def exportToPNG(self, out_png, resolution=96, **kw):
        open(out_png, "wb").close()

    def getDefinition(self, v):
        return self._doc


def validate_pagx(doc):
    """Structural checks on a layout document - what Pro would choke on first."""
    lay = doc["layoutDefinition"]
    assert lay["page"]["width"] > 0 and lay["page"]["units"]["uwkid"] == 109008
    uris = {m["uRI"] for m in doc["mapDefinitions"]}
    assert len(uris) == len(doc["mapDefinitions"]), "duplicate map URIs"
    names = [e["name"] for e in lay["elements"]]
    assert len(names) == len(set(names)), "duplicate element names"
    for e in lay["elements"]:
        if e["type"] == "CIMMapFrame":
            assert e["view"]["viewableObjectPath"] in uris
        for ring in (e.get("frame") or {}).get("rings", []):
            assert ring[0] == ring[-1], "open ring in " + e["name"]
        g = e.get("graphic") or {}
        if g.get("type") == "CIMTextGraphic":
            assert set(g["shape"]) >= {"x", "y"} and isinstance(g["text"], str)
            ts = g["symbol"]["symbol"]
            assert ts["type"] == "CIMTextSymbol" and ts["height"] > 0
            assert ts["horizontalAlignment"] in ("Left", "Center", "Right")
        if g.get("type") == "CIMPolygonGraphic":
            for ring in g["polygon"]["rings"]:
                assert ring[0] == ring[-1] and len(ring) >= 4
    for m in doc["mapDefinitions"]:
        assert m["layers"] == [] and "wkt" in m["spatialReference"]
    return True


class ArcGISProject(object):
    def __init__(self, path):
        assert str(path).lower().endswith(".aprx") and os.path.exists(str(path)), path
        self.filePath = str(path)
        self._maps = {}
        from .template_layout import TEMPLATE
        self._layouts = [Layout(copy.deepcopy(TEMPLATE), self)]

    def listLayouts(self, wildcard=None):
        return [l for l in self._layouts if wildcard is None or l.name == wildcard]

    def listMaps(self, wildcard=None):
        return [m for m in self._maps.values() if wildcard is None or m.name == wildcard]

    def importDocument(self, document_path, include_layout=True, reuse_existing_maps=False):
        with open(document_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        validate_pagx(doc)
        self._layouts.append(Layout(doc, self))
        _log("importDocument", document_path)

    def saveACopy(self, file_name):
        open(file_name, "w").close()


mp = SimpleNamespace(ArcGISProject=ArcGISProject)
