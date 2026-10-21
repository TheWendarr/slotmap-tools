"""slotmap_arcpy - the slotmap-tools pipeline for machines that only have the Python standard library and
ArcPy (ArcGIS Pro 2.5+; Python 3.7+). Run it with ArcGIS Pro's Python:

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\scripts\\propy.bat" -m slotmap_arcpy batch data\\35G.xlsx

Everything up to the GIS outputs (reading tables, matching names, splitting, planning frames) is pure
standard library. ArcPy is imported only to write the FileGDB/GeoPackage and to build and export the layout.
"""
__version__ = "0.2.0-arcpy"
