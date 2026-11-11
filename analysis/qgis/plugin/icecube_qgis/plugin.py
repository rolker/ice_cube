"""Ice Cube plugin: toolbar and Plugins > Ice Cube menu.

- Import new sessions: import_sessions.run() in a QgsTask on the configured input folder.
  First QGIS lets go of the GeoPackage: layers with unsaved edits are saved or discarded
  (asked), then the project is closed (saved project) or the gpkg layers are removed
  (unsaved project), so no OGR handle stays open on the file (Windows locks). Afterwards
  the project file is opened again, or rebuilt when the gpkg's table set changed, and the
  map extent is restored.
- Build / refresh project: build_project.build() on the configured gpkg.
- Export labels: export_labels.export() to a CSV chosen in a dialog.
- Open photos folder, Settings.
Errors go to the message bar and the "Ice Cube" tab of the log panel.
"""
import importlib
import os
import traceback
from pathlib import Path

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProject, QgsSettings, QgsTask
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

try:   # Qt6: QAction lives in QtGui; Qt5: in QtWidgets
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction

from . import core

PLUGIN_DIR = Path(__file__).resolve().parent
TAG = "Ice Cube"
MENU = "&Ice Cube"


def _level(name):
    """Qgis.MessageLevel member on QGIS 3 and 4 (scoped enum on Qt6)."""
    scoped = getattr(Qgis, "MessageLevel", None)
    return getattr(scoped, name, None) if scoped is not None and hasattr(scoped, name) else getattr(Qgis, name)


def log(msg, level="Info"):
    QgsMessageLog.logMessage(str(msg), TAG, _level(level))


def pipeline():
    """The pipeline modules (vendored in pipeline/, or the repo's analysis/qgis)."""
    core.add_pipeline_path(PLUGIN_DIR)
    mods = {}
    for m in ("import_sessions", "export_labels", "build_project"):
        mods[m] = importlib.import_module(m)
    mods["build_project"].LOG = log
    return mods


class Cancelled(Exception):
    pass


class ImportTask(QgsTask):
    """Runs import_sessions.run(argv) off the main thread; `done(ok, result, error)` is
    called on the main thread when it ends."""

    def __init__(self, argv, done):
        super().__init__("Ice Cube: import sessions")
        self.argv, self.done = argv, done
        self.result, self.error = None, None

    def run(self):
        try:
            imp = importlib.import_module("import_sessions")
            log("import " + " ".join(f'"{a}"' for a in self.argv))
            self.result = imp.run(self.argv, log=log, progress=self._progress)
            return True
        except Cancelled:
            self.error = "cancelled (sessions finished before the cancel are kept)"
        except SystemExit as e:   # argparse
            self.error = f"bad import arguments ({e}); check Ice Cube > Settings"
        except Exception as e:   # noqa: BLE001 (reported in the log and the message bar)
            self.error = f"{type(e).__name__}: {e}"
            log(traceback.format_exc(), "Critical")
        return False

    def _progress(self, done, total, name):
        if self.isCanceled():
            raise Cancelled()
        self.setProgress(100.0 * done / max(total, 1))

    def finished(self, ok):
        self.done(ok, self.result, self.error)


class IceCubePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.toolbar = None
        self.task = None

    # ------------------------------------------------------------------ GUI setup
    def initGui(self):  # noqa: N802
        icon = QIcon(str(PLUGIN_DIR / "icon.svg"))
        theme = QgsApplication.getThemeIcon
        self.toolbar = self.iface.addToolBar("Ice Cube")
        self.toolbar.setObjectName("IceCubeToolbar")
        for text, ico, slot, tip in (
                ("Import new sessions", icon, self.import_sessions,
                 "Import new session zips from the season input folder into the GeoPackage"),
                ("Build / refresh project", theme("/mActionRefresh.svg"), self.build_project,
                 "Rebuild the season project (.qgz) with fresh styles; keeps the old one as .qgz.bak"),
                ("Export labels", theme("/mActionFileSaveAs.svg"), self.export_labels,
                 "Write the captures with labels and notes to CSV"),
                ("Open photos folder", theme("/mActionFileOpen.svg"), self.open_photos,
                 "Open the photos folder next to the GeoPackage"),
                ("Settings", theme("/mActionOptions.svg"), self.settings, "Ice Cube settings")):
            a = QAction(ico, text, self.iface.mainWindow())
            a.setToolTip(tip)
            a.triggered.connect(slot)
            self.toolbar.addAction(a)
            self.iface.addPluginToMenu(MENU, a)
            self.actions.append(a)

    def unload(self):
        for a in self.actions:
            self.iface.removePluginMenu(MENU, a)
        self.actions = []
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None

    # ------------------------------------------------------------------ helpers
    def bar(self, text, level="Info", duration=8):
        self.iface.messageBar().pushMessage(TAG, text, level=_level(level), duration=duration)

    def fail(self, text):
        log(text, "Critical")
        self.bar(text + "  (details: View > Panels > Log Messages, Ice Cube tab)", "Critical", 0)

    def resolved(self):
        return core.resolve(core.load_settings(QgsSettings()))

    def gpkg_layers(self, gpkg):
        return [lyr for lyr in QgsProject.instance().mapLayers().values()
                if core.same_file(core.source_path(lyr.source()), gpkg)]

    def release_edits(self, layers, why):
        """Leave edit mode on `layers`, asking whether to save or discard unsaved edits.
        False when the user cancels or a save fails."""
        modified = [lyr for lyr in layers if lyr.isEditable() and lyr.isModified()]
        if modified:
            B = QMessageBox.StandardButton
            ans = QMessageBox.question(
                self.iface.mainWindow(), TAG,
                f"Unsaved edits in {', '.join(lyr.name() for lyr in modified)}.\n\n"
                f"Save them before {why}?", B.Save | B.Discard | B.Cancel, B.Save)
            if ans == B.Cancel:
                return False
            for lyr in modified:
                if ans == B.Save and not lyr.commitChanges():
                    self.fail(f"could not save the edits in {lyr.name()}: "
                              + "; ".join(lyr.commitErrors()))
                    return False
                if ans == B.Discard:
                    lyr.rollBack()
        for lyr in layers:
            if lyr.isEditable():
                lyr.rollBack()   # nothing left to lose: just leave edit mode
        return True

    def guarded(self, what, fn):
        try:
            return fn()
        except core.SetupError as e:
            self.fail(str(e))
        except Exception as e:   # noqa: BLE001 (never fail silently)
            log(traceback.format_exc(), "Critical")
            self.fail(f"{what} failed: {type(e).__name__}: {e}")
        return None

    # ------------------------------------------------------------------ import
    def import_sessions(self):
        self.guarded("import", self._import_start)

    def _import_start(self):
        if self.task is not None:
            self.bar("an import is already running", "Warning")
            return
        r = self.resolved()
        argv = core.import_argv(r)
        mods = pipeline()   # noqa: F841 (puts the pipeline on sys.path for the task)
        gpkg = r["gpkg"]
        project = QgsProject.instance()
        layers = self.gpkg_layers(gpkg)
        state = {"gpkg": gpkg, "qgz": r["qgz"], "reopen": None, "rebuild": False, "extent": None,
                 "empty": not project.mapLayers(), "tables": core.gpkg_tables(gpkg)}
        if layers:
            if not self.release_edits(layers, "importing"):
                return
            state["extent"] = self.iface.mapCanvas().extent()
            if project.fileName():
                if project.isDirty():
                    B = QMessageBox.StandardButton
                    ans = QMessageBox.question(
                        self.iface.mainWindow(), TAG,
                        f"Save the changes to {os.path.basename(project.fileName())} before "
                        "importing? The project is closed during the import and opened again "
                        "after it.", B.Save | B.Discard | B.Cancel, B.Save)
                    if ans == B.Cancel:
                        return
                    if ans == B.Save and not project.write():
                        self.fail(f"could not save {project.fileName()}: {project.error()}")
                        return
                state["reopen"] = project.fileName()
                project.clear()   # closes every layer, so nothing holds the gpkg
            else:
                project.removeMapLayers([lyr.id() for lyr in layers])
                state["rebuild"] = True
        err = core.write_lock_error(gpkg)
        if err:
            self._restore(state)
            raise core.SetupError(err)
        self.bar("importing sessions…", "Info", 4)
        self.task = ImportTask(argv, lambda ok, res, e: self.guarded(
            "import", lambda: self._import_done(ok, res, e, state)))
        QgsApplication.taskManager().addTask(self.task)

    def _restore(self, state):
        if state["reopen"]:
            QgsProject.instance().read(state["reopen"])
            if state["extent"] is not None:
                self.iface.mapCanvas().setExtent(state["extent"])
                self.iface.mapCanvas().refresh()

    def _import_done(self, ok, result, error, state):
        self.task = None
        if not ok:
            self._restore(state)
            self.fail(f"import: {error}")
            return
        msg = core.summary_text(result)
        log(msg, "Success")
        changed = core.gpkg_tables(state["gpkg"]) != state["tables"]
        reopen, qgz = state["reopen"], state["qgz"]
        have_qgz = bool(qgz) and os.path.isfile(qgz)
        if reopen and not (core.same_file(reopen, qgz) and changed):
            self._restore(state)   # the same project, its layers read the new rows
            if changed:
                msg += " New tables in the GeoPackage: use Build / refresh project to show them."
        elif state["empty"] and have_qgz and not changed:
            QgsProject.instance().read(qgz)
        elif reopen or state["rebuild"] or state["empty"]:
            # the season project with a new table set, gpkg layers of an unsaved project,
            # or nothing open: (re)build the season project
            if self._build(extent=state["extent"]):
                msg += " Project rebuilt" + (" (new tables)." if changed else ".")
            else:
                self._restore(state)
        self.bar(msg, "Success", 15)

    # ------------------------------------------------------------------ build
    def build_project(self):
        self.guarded("build", lambda: self._build(ask=True))

    def _build(self, extent=None, ask=True):
        """build_project.build(); it asks only when the open project has unsaved changes."""
        r = core.require(self.resolved(), "gpkg")
        if not os.path.isfile(r["gpkg"]):
            raise core.SetupError(f"{r['gpkg']} does not exist yet: run Import new sessions first")
        bp = pipeline()["build_project"]
        try:
            qgz = bp.build(r["gpkg"], ask=ask, extent=extent)
        except bp.CrsError as e:
            self.fail(str(e))
            return None
        if qgz:
            self.bar(f"project saved: {qgz}", "Success", 8)
        return qgz

    # ------------------------------------------------------------------ export
    def export_labels(self):
        self.guarded("export", self._export)

    def _export(self):
        r = core.require(self.resolved(), "gpkg")
        caps = [lyr for lyr in self.gpkg_layers(r["gpkg"]) if lyr.isEditable() and lyr.isModified()]
        if caps and not self.release_edits(caps, "exporting"):
            return
        out, _ = QFileDialog.getSaveFileName(self.iface.mainWindow(), "Export labels",
                                             r["labels_csv"], "CSV (*.csv)")
        if not out:
            return
        n = pipeline()["export_labels"].export(r["gpkg"], out)
        log(f"{out}: {n} captures", "Success")
        self.bar(f"{n} captures written to {out}", "Success", 10)

    # ------------------------------------------------------------------ photos, settings
    def open_photos(self):
        self.guarded("open photos", self._open_photos)

    def _open_photos(self):
        r = core.require(self.resolved(), "gpkg")
        d = r["photos_dir"]
        if not os.path.isdir(d):
            raise core.SetupError(f"no photos folder yet ({d}): import sessions first")
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def settings(self):
        self.guarded("settings", self._settings)

    def _settings(self):
        from .settings_dialog import SettingsDialog
        store = QgsSettings()
        dlg = SettingsDialog(core.load_settings(store), self.iface.mainWindow())
        if dlg.exec():
            core.save_settings(store, dlg.values())
            self.bar("settings saved", "Info", 3)
