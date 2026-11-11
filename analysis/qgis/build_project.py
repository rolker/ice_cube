"""Build a QGIS project for a season GeoPackage made by import_sessions.py.

Run it inside QGIS (3.34 LTR or later): Plugins > Python Console, then the "Show Editor"
button, open this file, and press Run Script. It asks for the season .gpkg (or reads the
ICECUBE_GPKG environment variable), replaces the current project (it asks first if one
is open), and saves `<season>.qgz` next to the gpkg.

What it sets up:
- Project CRS: the local transverse Mercator at the base, read from the gpkg's
  `icecube_meta.local_proj` (written by the import from your private site config), so
  the map is in metres east/north of the base; measurements are planar metres. Without
  a local frame the project stays in EPSG:4326. No basemap.
- Layers (group "Survey", top to bottom): photos, captures, refs, tracks, track_points
  (off), then a "Map layers" group with every layer_* table, and the sessions table.
- Styles: captures by class (surface cyan dots, object magenta squares, ref dark slate
  rings, photo brown camera, control orange triangles), labelled S12 / O13 / R14 / P15 /
  C3 plus your label; refs as rings, red when the pole check failed, labelled with the
  height difference to the last visit; monuments by status (measured green, predicted
  orange hollow) with uncertainty in the label; other map layers in their GeoJSON
  stroke/fill colours; tracks thin grey by fix quality.
- Forms: on captures, photos, refs and sessions only `note` (and `label` on captures,
  with completion from the labels already used) are editable; the photo field shows
  the picture. Map tips (View > Show Map Tips) show the capture and its photo.

Nothing site-specific is in this file: the CRS and paths come from the gpkg at run time.
"""
import os
import sqlite3

from qgis.core import (QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsEditorWidgetSetup, QgsFillSymbol,
                       QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsProject,
                       QgsProperty, QgsRectangle, QgsRendererCategory, QgsSimpleMarkerSymbolLayer,
                       QgsSingleSymbolRenderer, QgsSymbolLayer, QgsTextBufferSettings,
                       QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling)

try:
    from qgis.utils import iface
except ImportError:
    iface = None

CLASS_STYLE = {   # class: (label, marker shape, fill, stroke, size mm)
    "surface": ("Surface", "circle", "0,200,230,255", "0,0,0,0", 1.6),
    "object": ("Object", "square", "220,0,220,255", "90,0,90,255", 2.6),
    "ref": ("Ref", "circle", "0,0,0,0", "47,79,79,255", 3.6),
    "control": ("Control (0.3)", "triangle", "255,140,0,255", "120,60,0,255", 2.6),
}
FIX = {4: ("RTK fixed", "110,110,110,255", "solid"), 5: ("RTK float", "170,170,170,255", "dash"),
       2: ("DGPS", "210,150,150,255", "dot"), 1: ("Single", "220,90,90,255", "dot"),
       0: ("No fix", "230,230,230,255", "dot")}
STATUS = {"measured": ("Measured", "circle", "0,150,0,255", "0,80,0,255", 3.0),
          "predicted": ("Predicted", "circle", "0,0,0,0", "230,120,0,255", 3.0)}


# ----------------------------------------------------------------------------- helpers

def _prop(name):
    """QgsSymbolLayer property enum across QGIS versions (3.36 scoped the enum)."""
    scoped = getattr(QgsSymbolLayer, "Property", None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(QgsSymbolLayer, "Property" + name)


def marker(shape, fill, stroke, size, stroke_width=0.3):
    return QgsMarkerSymbol.createSimple({
        "name": shape, "color": fill, "outline_color": stroke, "size": str(size),
        "outline_width": str(stroke_width), "outline_style": "solid" if not stroke.endswith(",0") else "no"})


def camera_marker():
    """A brown camera drawn from simple markers (body and lens)."""
    sym = marker("square", "140,90,40,255", "70,40,10,255", 3.0, 0.2)
    sym.appendSymbolLayer(QgsSimpleMarkerSymbolLayer.create({
        "name": "circle", "size": "1.5", "color": "255,255,255,255",
        "outline_color": "70,40,10,255", "outline_width": "0.2"}))
    return sym


def labels(layer, expression, size=8.0):
    s = QgsPalLayerSettings()
    s.fieldName = expression
    s.isExpression = True
    fmt = QgsTextFormat()
    fmt.setSize(size)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    fmt.setBuffer(buf)
    s.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)


def has(layer, field):
    return layer.fields().indexOf(field) >= 0


def form(layer, editable=("note",), photo_field=None, suggest=()):
    """Every field read-only except `editable`; `photo_field` shows the image; fields in
    `suggest` offer the values already used (still free text)."""
    cfg = layer.editFormConfig()
    for i, f in enumerate(layer.fields()):
        cfg.setReadOnly(i, f.name() not in editable)
        if f.name() in suggest:
            layer.setEditorWidgetSetup(i, QgsEditorWidgetSetup("UniqueValues", {"Editable": True}))
        elif f.name() == "note":
            layer.setEditorWidgetSetup(i, QgsEditorWidgetSetup("TextEdit", {"IsMultiline": True}))
        elif f.name() == photo_field:
            layer.setEditorWidgetSetup(i, QgsEditorWidgetSetup("ExternalResource", {
                "DocumentViewer": 1, "DocumentViewerHeight": 320, "DocumentViewerWidth": 0,
                "FileWidget": True, "FileWidgetButton": False, "RelativeStorage": 1,
                "StorageMode": 0, "UseLink": True, "FullUrl": False}))
    layer.setEditFormConfig(cfg)


PHOTO_TIP = ('[% CASE WHEN "photo" IS NOT NULL THEN \'<br><img src="file:///\' || '
             '@project_folder || \'/\' || "photo" || \'" width="320">\' ELSE \'\' END %]')


def add(project, group, layer, visible=True):
    project.addMapLayer(layer, False)
    node = group.addLayer(layer)
    node.setItemVisibilityChecked(visible)
    return layer


# ----------------------------------------------------------------------------- layers

def style_captures(lyr):
    cats = [QgsRendererCategory(k, marker(shape, fill, stroke, size), label)
            for k, (label, shape, fill, stroke, size) in CLASS_STYLE.items()]
    cats.append(QgsRendererCategory("photo", camera_marker(), "Photo"))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("class", cats))
    code = 'upper(left("class", 1)) || to_string("n")'
    labels(lyr, f'{code} || CASE WHEN coalesce("label", \'\') != \'\' THEN \' \' || "label" ELSE \'\' END')
    lyr.setDisplayExpression(f'{code} || \' \' || "session"')
    lyr.setMapTipTemplate(
        '<b>[% upper(left("class", 1)) || to_string("n") %]</b> [% "class" %] '
        '<i>[% coalesce("label", \'\') %]</i><br>[% "session" %] [% "utc" %]<br>'
        'fix [% "fix" %], [% "n_epochs" %] epochs, sd up [% format_number("sd_u_m" * 100, 1) %] cm'
        '[% CASE WHEN "u_m" IS NOT NULL THEN \', ground \' || format_number("u_m", 3) || '
        '\' m vs base\' ELSE \'\' END %]<br>[% coalesce("note", \'\') %]' + PHOTO_TIP)
    form(lyr, editable=("note", "label"), photo_field="photo", suggest=("label",))


def style_photos(lyr):
    lyr.setRenderer(QgsSingleSymbolRenderer(camera_marker()))
    lyr.setMapTipTemplate('<b>[% "name" %]</b><br>[% "session" %] n=[% "n" %] [% "utc" %]' + PHOTO_TIP)
    lyr.setDisplayExpression('"name"')
    form(lyr, editable=("note",), photo_field="photo")


def style_refs(lyr):
    sym = marker("circle", "0,0,0,0", "47,79,79,255", 4.4, 0.6)
    sym.symbolLayer(0).setDataDefinedProperty(_prop("StrokeColor"), QgsProperty.fromExpression(
        'CASE WHEN "pole_ok" = 0 THEN \'220,0,0,255\' ELSE \'47,79,79,255\' END'))
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))
    labels(lyr, '\'R\' || to_string("n") || CASE WHEN "diff_last_m" IS NOT NULL THEN \' \' || '
                'format_number("diff_last_m" * 100, 1) || \' cm\' ELSE \'\' END', 7.5)
    lyr.setMapTipTemplate('<b>R[% "n" %]</b> site [% "site" %] [% "session" %]<br>[% "utc" %], '
                          '[% "n_epochs" %] epochs, pole [% "pole_m" %] m<br>[% coalesce("pole_check", \'\') %]')
    form(lyr, editable=("note",))


def style_tracks(lyr, points=False):
    cats = []
    for k, (label, color, style) in FIX.items():
        if points:
            sym = marker("circle", color, "0,0,0,0", 0.6)
        else:
            sym = QgsLineSymbol.createSimple({"line_color": color, "line_width": "0.15",
                                              "line_style": style})
        cats.append(QgsRendererCategory(k, sym, label))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("fix", cats))


def style_map_layer(lyr):
    """Points: monuments by status, base as a red triangle, others in their stroke colour.
    Lines and polygons: GeoJSON stroke and fill colours. Labels: label or name."""
    name = '"label"' if has(lyr, "label") else '"name"' if has(lyr, "name") else "''"
    if has(lyr, "label") and has(lyr, "name"):
        name = 'coalesce("label", "name")'
    unc = (' || CASE WHEN "uncertainty_m" IS NOT NULL THEN \' ±\' || format_number("uncertainty_m", 2) '
           '|| \' m\' ELSE \'\' END') if has(lyr, "uncertainty_m") else ""
    geom = lyr.geometryType()
    gtype = int(geom.value) if hasattr(geom, "value") else int(geom)   # 0 point, 1 line, 2 polygon
    if gtype == 0:
        kinds = set(lyr.uniqueValues(lyr.fields().indexOf("kind"))) if has(lyr, "kind") else set()
        if "base" in {str(k).lower() for k in kinds}:
            lyr.setRenderer(QgsSingleSymbolRenderer(marker("triangle", "230,30,30,255", "90,0,0,255", 3.2)))
            labels(lyr, name)
        elif has(lyr, "status"):
            cats = [QgsRendererCategory(k, marker(s, f, st, z, 0.5), lab)
                    for k, (lab, s, f, st, z) in STATUS.items()]
            cats.append(QgsRendererCategory("", marker("circle", "0,0,0,0", "130,130,130,255", 2.4, 0.4),
                                            "Other"))
            lyr.setRenderer(QgsCategorizedSymbolRenderer("status", cats))
            labels(lyr, name + unc, 7.5)
        else:
            sym = marker("circle", "90,90,200,255", "0,0,0,0", 1.8)
            if has(lyr, "stroke_rgba"):
                sym.symbolLayer(0).setDataDefinedProperty(_prop("FillColor"), QgsProperty.fromExpression(
                    'coalesce("stroke_rgba", \'90,90,200,255\')'))
            lyr.setRenderer(QgsSingleSymbolRenderer(sym))
            labels(lyr, name, 7.0)
    elif gtype == 1:
        sym = QgsLineSymbol.createSimple({"line_color": "80,80,80,255", "line_width": "0.4"})
        sym.symbolLayer(0).setDataDefinedProperty(_prop("StrokeColor"), QgsProperty.fromExpression(
            'coalesce("stroke_rgba", \'80,80,80,255\')'))
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        labels(lyr, name, 7.0)
    else:
        sym = QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "0,120,0,255",
                                          "outline_width": "0.4"})
        sl = sym.symbolLayer(0)
        sl.setDataDefinedProperty(_prop("FillColor"), QgsProperty.fromExpression(
            'coalesce("fill_rgba", \'0,0,0,0\')'))
        sl.setDataDefinedProperty(_prop("StrokeColor"), QgsProperty.fromExpression(
            'coalesce("stroke_rgba", \'0,120,0,255\')'))
        lyr.setRenderer(QgsSingleSymbolRenderer(sym))
        labels(lyr, name + unc, 7.0)
    form(lyr, editable=())


# ----------------------------------------------------------------------------- main

def read_gpkg(gpkg):
    con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT table_name FROM gpkg_contents ORDER BY table_name")]
        meta = dict(con.execute("SELECT key, value FROM icecube_meta")) if "icecube_meta" in tables else {}
    finally:
        con.close()
    return tables, meta


def choose_gpkg():
    p = os.environ.get("ICECUBE_GPKG")
    if p:
        return p
    from qgis.PyQt.QtWidgets import QFileDialog
    p, _ = QFileDialog.getOpenFileName(None, "Season GeoPackage", "", "GeoPackage (*.gpkg)")
    return p or None


def build(gpkg, ask=True):
    """Build and save the project; returns the .qgz path."""
    gpkg = os.path.abspath(gpkg)
    tables, meta = read_gpkg(gpkg)
    season = meta.get("season") or os.path.splitext(os.path.basename(gpkg))[0]
    qgz = os.path.join(os.path.dirname(gpkg), season + ".qgz")
    project = QgsProject.instance()
    if ask and iface is not None and project.mapLayers():
        from qgis.PyQt.QtWidgets import QMessageBox
        yes = getattr(QMessageBox, "Yes", None) or QMessageBox.StandardButton.Yes   # Qt5 / Qt6
        if QMessageBox.question(None, "Ice Cube", f"Replace the current project with {season}.qgz?",
                                yes | (getattr(QMessageBox, "No", None) or QMessageBox.StandardButton.No)
                                ) != yes:
            return None
    project.clear()
    project.setFileName(qgz)
    project.setTitle(f"Ice Cube {season}")
    project.writeEntryBool("Paths", "/Absolute", False)

    crs = None
    if meta.get("local_proj"):
        crs = QgsCoordinateReferenceSystem.fromProj(meta["local_proj"])
        if not crs.isValid():
            print("local CRS not understood; keeping EPSG:4326")
            crs = None
    if crs is None:
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
    project.setCrs(crs)
    project.setEllipsoid("NONE")   # planar measurements in the local metres

    def layer(table, name=None):
        lyr = QgsVectorLayer(f"{gpkg}|layername={table}", name or table, "ogr")
        if not lyr.isValid():
            print(f"could not open {table}")
            return None
        return lyr

    root = project.layerTreeRoot()
    survey = root.addGroup("Survey")
    order = [("photos", style_photos, True), ("captures", style_captures, True),
             ("refs", style_refs, True), ("tracks", lambda l: style_tracks(l), True),
             ("track_points", lambda l: style_tracks(l, points=True), False)]
    extent_layers = []
    for table, styler, visible in order:
        if table not in tables:
            continue
        lyr = layer(table)
        if lyr is None:
            continue
        try:
            styler(lyr)
        except Exception as e:   # a style detail should not stop the project
            print(f"{table}: style not fully applied ({e})")
        add(project, survey, lyr, visible)
        if table in ("captures", "tracks"):
            extent_layers.append(lyr)
    maps = root.addGroup("Map layers")
    for table in sorted(t for t in tables if t.startswith("layer_")):
        lyr = layer(table, table[len("layer_"):])
        if lyr is None:
            continue
        try:
            style_map_layer(lyr)
        except Exception as e:
            print(f"{table}: style not fully applied ({e})")
        add(project, maps, lyr)
        extent_layers.append(lyr)
    if "sessions" in tables:
        lyr = layer("sessions")
        if lyr is not None:
            form(lyr, editable=("note",))
            add(project, root.addGroup("Tables"), lyr)

    ext = QgsRectangle()
    ext.setMinimal()
    for lyr in extent_layers:
        if lyr.featureCount() and not lyr.extent().isEmpty():
            tr = QgsCoordinateTransform(lyr.crs(), crs, project)
            ext.combineExtentWith(tr.transformBoundingBox(lyr.extent()))
    if iface is not None and not ext.isEmpty():
        iface.mapCanvas().setExtent(ext.buffered(max(ext.width(), ext.height()) * 0.05))
        iface.mapCanvas().refresh()
    ok = project.write(qgz)
    print(f"{'saved' if ok else 'COULD NOT SAVE'} {qgz}; CRS {crs.description() or crs.authid()}")
    return qgz


gpkg_path = choose_gpkg()
if gpkg_path:
    build(gpkg_path)
else:
    print("no GeoPackage chosen")
