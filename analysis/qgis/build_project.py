"""Build a QGIS project for a season GeoPackage made by import_sessions.py.

The Ice Cube QGIS plugin (analysis/qgis/plugin, "Build / refresh project") runs this; it
also works on its own inside QGIS (3.34 LTR or later, QGIS 4): Plugins > Python Console,
then the "Show Editor" button, open this file, and press Run Script. It asks for the
season .gpkg (or reads the ICECUBE_GPKG environment variable), replaces the current
project and saves `<season>.qgz` next to the gpkg. An existing `<season>.qgz` is kept as
`<season>.qgz.bak` (the previous .bak is replaced) and overwritten: nothing to delete by
hand. It asks first only when the open project has unsaved changes (or a layer has
unsaved edits), since replacing the project would lose them.

What it sets up:
- Project CRS: "Ice Cube local ENU (origin G)" (or whatever the origin is named), an
  orthographic projection at the origin, so the map is in metres east (x) and north (y)
  of the origin; measurements are planar metres (ellipsoid NONE). It is read from the
  gpkg's `icecube_meta.local_wkt` (WKT2, written by the import from your private site
  config) with QgsCoordinateReferenceSystem.fromWkt, falling back to the proj strings.
  When the registry allows it the CRS is saved as a user CRS under that name (so the
  status bar shows the name, not "Unknown CRS"). If no form gives a valid CRS the script
  STOPS with an error: it never falls back to longitude/latitude. No basemap.
- Layers (group "Survey", top to bottom): photos, captures, refs, tracks, track_points
  (off), then a "Map layers" group with every layer_* table, and the sessions table.
- "Imagery" group at the bottom: the GeoTIFFs in the `imagery/` folder next to the gpkg
  (local copies made by fetch_imagery.py, already in the local CRS and datum corrected),
  newest year first; only the newest photo is checked, older years and the shaded relief
  are present but unchecked. Below them the wider copies in `imagery/development/`
  (named "... (development)"), whose newest photo is also checked so it fills in around
  the lot one. RGB copies use bands 1, 2, 3 with band 4 as alpha. Nothing is fetched from
  the web.
- Styles: captures by class (surface cyan dots, object magenta squares, ref dark slate
  rings, photo brown camera, control orange triangles), labelled S12 / O13 / R14 / P15 /
  C3 plus your label; refs as rings, red when the pole check failed, labelled with the
  height difference to the last visit; monuments by status (measured green, predicted
  orange hollow) with uncertainty in the label; tax parcels (kind=parcel polygons, from
  fetch_parcels.py) thin grey with PID labels; other map layers in their GeoJSON
  stroke/fill colours; tracks thin grey by fix quality.
- Forms: on captures, photos, refs and sessions only `note` (and `label` on captures,
  with completion from the labels already used) are editable; the photo field shows
  the picture. Map tips (View > Show Map Tips) show the capture and its photo.

Nothing site-specific is in this file: the CRS and paths come from the gpkg at run time.
"""
import json
import os
import re
import shutil
import sqlite3

from qgis.core import (QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsEditorWidgetSetup, QgsFillSymbol,
                       QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsProject,
                       QgsProperty, QgsRasterLayer, QgsRectangle, QgsRendererCategory, QgsSimpleMarkerSymbolLayer,
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


LOG = print   # report lines; the plugin points this at its log


def _log(msg):
    LOG(msg)


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
        '[% CASE WHEN "x_m" IS NOT NULL THEN \'<br>x \' || format_number("x_m", 3) || \' y \' || '
        'format_number("y_m", 3) || \' z \' || format_number("z_m", 3) || \' m\' ELSE \'\' END %]'
        '<br>[% coalesce("note", \'\') %]' + PHOTO_TIP)
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


def style_parcels(lyr):
    """Tax parcels (kind=parcel polygons, fetch_parcels.py): thin grey outlines, no fill,
    small grey PID labels."""
    sym = QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "128,128,128,255",
                                      "outline_width": "0.15", "style": "no"})
    lyr.setRenderer(QgsSingleSymbolRenderer(sym))
    labels(lyr, '"pid"' if has(lyr, "pid") else '"label"', 6.0)
    lyr.setMapTipTemplate('<b>[% "pid" %]</b> [% coalesce("area_ac", \'\') %] ac<br>'
                          '<i>[% coalesce("note", \'\') %]</i><br>[% coalesce("source", \'\') %]')
    form(lyr, editable=())


def is_parcel_layer(lyr):
    if not has(lyr, "kind"):
        return False
    kinds = {str(k).lower() for k in lyr.uniqueValues(lyr.fields().indexOf("kind"))}
    return kinds == {"parcel"}


def style_map_layer(lyr):
    """Points: monuments by status, base as a red triangle, others in their stroke colour.
    Lines and polygons: GeoJSON stroke and fill colours. Labels: label or name. Tax
    parcels (kind=parcel): thin grey with PID labels."""
    name = '"label"' if has(lyr, "label") else '"name"' if has(lyr, "name") else "''"
    if has(lyr, "label") and has(lyr, "name"):
        name = 'coalesce("label", "name")'
    unc = (' || CASE WHEN "uncertainty_m" IS NOT NULL THEN \' ±\' || format_number("uncertainty_m", 2) '
           '|| \' m\' ELSE \'\' END') if has(lyr, "uncertainty_m") else ""
    geom = lyr.geometryType()
    gtype = int(geom.value) if hasattr(geom, "value") else int(geom)   # 0 point, 1 line, 2 polygon
    if gtype == 2 and is_parcel_layer(lyr):
        style_parcels(lyr)
        return
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


# ----------------------------------------------------------------------------- imagery

def _sidecar(tif):
    try:
        with open(os.path.splitext(tif)[0] + ".json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


DEVELOPMENT_DIR = "development"   # imagery/<this>/: the same datasets over a wider extent


def imagery_entries(folder, suffix=""):
    """The GeoTIFFs of `folder` as dicts (path, name, year, relief, visible), in the
    Imagery group's order: photos newest first, then shaded relief / hillshade. The year is
    the latest acquisition year in the fetch_imagery.py sidecar (<name>.json), else the
    latest 4-digit year in the file name. Only the newest photo is visible. `suffix` is
    appended to the layer names."""
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith((".tif", ".tiff")) or ".tmp." in fn:
            continue
        path = os.path.join(folder, fn)
        stem = os.path.splitext(fn)[0]
        side = _sidecar(path)
        years = side.get("acquisition_years") or [
            int(y) for y in re.findall(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", stem)]
        relief = bool(re.search(r"shaded|relief|hillshade|_hs(_|$)", stem, re.I)
                      or "shaded" in str(side.get("rendering_rule") or "").lower())
        y0, y1 = (min(years), max(years)) if years else (None, None)
        label = (f"{y0}" if y0 == y1 else f"{y0}-{y1}") if years else ""
        out.append({"path": path, "dataset": side.get("dataset") or stem,
                    "name": f"{label} {stem}".strip() + suffix, "year": y1 or 0, "relief": relief,
                    "visible": False})
    out.sort(key=lambda e: (e["relief"], -e["year"], e["dataset"]))
    for e in out:
        if not e["relief"]:
            e["visible"] = True
            break
    return out


def add_imagery(project, root, folder):
    """Add the Imagery group (at the bottom of `root`); returns the layers added. The
    lot-sized copies in `folder` come first (drawn on top), then the wider copies in
    `folder`/development, whose newest photo is also ticked so it fills in around the lot
    one; the rest are unticked."""
    entries = imagery_entries(folder)
    if folder:
        entries += imagery_entries(os.path.join(folder, DEVELOPMENT_DIR), " (development)")
    if not entries:
        return []
    group = root.addGroup("Imagery")
    added = []
    for e in entries:
        lyr = QgsRasterLayer(e["path"], e["name"], "gdal")
        if not lyr.isValid():
            _log(f"imagery: could not open {e['path']}")
            continue
        try:
            if lyr.bandCount() >= 3:
                from qgis.core import QgsMultiBandColorRenderer
                r = QgsMultiBandColorRenderer(lyr.dataProvider(), 1, 2, 3)
                if lyr.bandCount() >= 4:
                    r.setAlphaBand(4)
                lyr.setRenderer(r)
        except Exception as err:   # noqa: BLE001 (the default renderer still shows it)
            _log(f"imagery {e['name']}: band order not set ({err})")
        add(project, group, lyr, e["visible"])
        added.append(lyr)
    group.setExpanded(False)
    _log(f"imagery: {len(added)} layer(s) from {folder}")
    return added


# ----------------------------------------------------------------------------- main

class CrsError(RuntimeError):
    pass


def local_crs(meta):
    """The project CRS from icecube_meta: WKT2 (fromWkt), then the orthographic and
    transverse Mercator proj strings (fromProj). Registered as a user CRS when possible.
    Raises CrsError (never falls back to EPSG:4326)."""
    name = meta.get("local_crs_name") or "Ice Cube local ENU"
    tried = []
    crs = None
    for kind, key in (("WKT2", "local_wkt"), ("proj", "local_proj"), ("proj tmerc", "local_proj_tmerc")):
        text = meta.get(key)
        if not text:
            continue
        maker = QgsCoordinateReferenceSystem.fromWkt if kind == "WKT2" else QgsCoordinateReferenceSystem.fromProj
        try:
            c = maker(text)
        except Exception as e:   # noqa: BLE001 (any binding error: try the next form)
            tried.append(f"{key}: {e}")
            continue
        if c.isValid():
            crs = c
            _log(f"local CRS from {key} ({kind})")
            break
        tried.append(f"{key}: not valid")
    if crs is None:
        raise CrsError(
            "Ice Cube: no valid local CRS in the GeoPackage (" + ("; ".join(tried) or
            "icecube_meta has no local_wkt/local_proj: was an origin given at import?") +
            "). Re-run import_sessions.py with [site.origin] in the site config; see "
            "docs/howto/qgis.md, Troubleshooting. Stopped: the project was not built.")
    return register_user_crs(crs, name)


def register_user_crs(crs, name):
    """Save the CRS as a user CRS called `name` (QGIS 3.18+) and return the registered one,
    so the status bar shows the name; otherwise return `crs` as is."""
    try:
        from qgis.core import Qgis, QgsApplication
        reg = QgsApplication.coordinateReferenceSystemRegistry()
        fmt = getattr(getattr(Qgis, "CrsDefinitionFormat", None), "Wkt", None)
        if fmt is None:
            from qgis.core import QgsCoordinateReferenceSystem as C
            fmt = C.FormatWkt
        existing = [d for d in reg.userCrsList() if d.name == name]
        if existing:
            sid = existing[0].id
            reg.updateUserCrs(sid, crs, name, fmt)
        else:
            sid = reg.addUserCrs(crs, name, fmt)
        if sid is None or sid < 0:
            raise RuntimeError("addUserCrs returned no id")
        user = QgsCoordinateReferenceSystem(f"USER:{sid}")
        if user.isValid():
            _log(f"user CRS {user.authid()} \"{name}\"")
            return user
        _log("user CRS registered but not readable back; using the CRS as built")
    except Exception as e:   # noqa: BLE001 (older QGIS: no registry; the CRS still works)
        _log(f"user CRS not registered ({e}); using the CRS as built")
    return crs


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


def backup_existing(qgz):
    """Copy an existing `qgz` to `<qgz>.bak` (replacing an older .bak); returns the .bak
    path, or None when there was nothing to keep."""
    if not os.path.isfile(qgz):
        return None
    bak = qgz + ".bak"
    shutil.copy2(qgz, bak)
    return bak


def unsaved_changes(project):
    """What replacing `project` would lose: a list of short descriptions (empty when the
    project is saved and no layer has unsaved edits)."""
    out = []
    if project.isDirty():
        out.append("unsaved project changes" + (f" in {os.path.basename(project.fileName())}"
                                                 if project.fileName() else ""))
    for lyr in project.mapLayers().values():
        if getattr(lyr, "isEditable", None) and lyr.isEditable() and lyr.isModified():
            out.append(f"unsaved edits in layer {lyr.name()}")
    return out


def confirm_replace(project, qgz, ask=True):
    """True when the build may replace the open project: no unsaved changes, or the user
    said so. Without iface (or ask=False) it never asks."""
    lost = unsaved_changes(project)
    if not lost or not ask or iface is None:
        return True
    from qgis.PyQt.QtWidgets import QMessageBox
    same = project.fileName() and os.path.normcase(os.path.abspath(project.fileName())) == \
        os.path.normcase(os.path.abspath(qgz))
    what = "This project" if same else "The open project"
    text = (f"{what} has " + "; ".join(lost) + ".\n\nBuild anyway and discard them? "
            f"(The current {os.path.basename(qgz)} is kept as {os.path.basename(qgz)}.bak.)")
    yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
    return QMessageBox.question(None, "Ice Cube", text, yes | no, no) == yes


def build(gpkg, ask=True, extent=None):
    """Build and save the project; returns the .qgz path (None when the user said no).
    `extent` (a QgsRectangle in the project CRS) keeps a view instead of zooming to the
    data."""
    gpkg = os.path.abspath(gpkg)
    tables, meta = read_gpkg(gpkg)
    season = meta.get("season") or os.path.splitext(os.path.basename(gpkg))[0]
    qgz = os.path.join(os.path.dirname(gpkg), season + ".qgz")
    crs = local_crs(meta)   # before touching the open project: a CRS error stops here
    project = QgsProject.instance()
    if not confirm_replace(project, qgz, ask):
        _log("not built: the open project has unsaved changes")
        return None
    bak = backup_existing(qgz)
    if bak:
        _log(f"previous project kept as {bak}")
    for lyr in project.mapLayers().values():   # discard edit buffers the user agreed to lose
        if getattr(lyr, "isEditable", None) and lyr.isEditable():
            lyr.rollBack()
    project.clear()
    project.setFileName(qgz)
    project.setTitle(f"Ice Cube {season}")
    project.writeEntryBool("Paths", "/Absolute", False)

    project.setCrs(crs)
    project.setEllipsoid("NONE")   # planar measurements in the local metres

    def layer(table, name=None):
        lyr = QgsVectorLayer(f"{gpkg}|layername={table}", name or table, "ogr")
        if not lyr.isValid():
            _log(f"could not open {table}")
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
            _log(f"{table}: style not fully applied ({e})")
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
            _log(f"{table}: style not fully applied ({e})")
        add(project, maps, lyr)
        extent_layers.append(lyr)
    if "sessions" in tables:
        lyr = layer("sessions")
        if lyr is not None:
            form(lyr, editable=("note",))
            add(project, root.addGroup("Tables"), lyr)
    try:
        add_imagery(project, root, os.path.join(os.path.dirname(gpkg), "imagery"))
    except Exception as e:   # imagery is a backdrop: never stop the build for it
        _log(f"imagery not added ({e})")

    # QGIS gives a new project the CRS of the first layer added (WGS84 here), overriding
    # the setCrs above, so set it again now that all layers are in, and check it took.
    project.setCrs(crs)
    if iface is not None:
        iface.mapCanvas().setDestinationCrs(crs)
    if project.crs() != crs:
        raise RuntimeError("Ice Cube: the project CRS is "
                           f"{project.crs().authid() or project.crs().description()}, not the local "
                           "CRS; set it by hand in Project > Properties > CRS")

    ext = QgsRectangle()
    if hasattr(ext, "setNull"):   # QGIS 3.34+ (setMinimal is deprecated, gone in 5.0)
        ext.setNull()
    else:
        ext.setMinimal()
    for lyr in extent_layers:
        if lyr.featureCount() and not lyr.extent().isEmpty():
            tr = QgsCoordinateTransform(lyr.crs(), crs, project)
            ext.combineExtentWith(tr.transformBoundingBox(lyr.extent()))
    if iface is not None and extent is not None and not extent.isEmpty():
        iface.mapCanvas().setExtent(extent)
        iface.mapCanvas().refresh()
    elif iface is not None and not ext.isEmpty():
        iface.mapCanvas().setExtent(ext.buffered(max(ext.width(), ext.height()) * 0.05))
        iface.mapCanvas().refresh()
    ok = project.write(qgz)
    if not ok:
        raise OSError(f"could not save {qgz}: {project.error()}")
    pc = project.crs()
    _log(f"{'saved' if ok else 'COULD NOT SAVE'} {qgz}; project CRS {pc.description() or pc.authid()} "
          f"(origin {meta.get('origin_name', '?')}, x east, y north, metres)")
    return qgz


def main():
    gpkg_path = choose_gpkg()
    if not gpkg_path:
        print("no GeoPackage chosen")
        return
    try:
        build(gpkg_path)
    except CrsError as err:
        print("ERROR: " + str(err))
        if iface is not None:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Ice Cube: CRS error", str(err))


# Run when executed as a script (python console "Run Script" uses __main__ or __console__),
# not when imported as a module by the plugin or the tests.
if __name__.rsplit(".", 1)[-1] != "build_project":
    main()
