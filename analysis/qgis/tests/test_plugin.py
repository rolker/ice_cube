"""The QGIS plugin without QGIS: settings and path resolution (plugin core), the packaged
zip (vendored pipeline, metadata, runs from the zip alone), build_project's .qgz.bak and
unsaved-changes rule, and the import task, with qgis.* replaced by stand-ins.
Run: python3 -m unittest discover -s analysis/qgis/tests"""
import contextlib
import importlib
import io
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
QGIS_DIR = HERE.parent
PLUGIN_DIR = QGIS_DIR / "plugin" / "icecube_qgis"
sys.path.insert(0, str(QGIS_DIR))
sys.path.insert(0, str(HERE))

import package_plugin  # noqa: E402
import synth  # noqa: E402

core = importlib.import_module("core")   # the plugin's core (package_plugin put it on the path)
assert Path(core.__file__).resolve().parent == PLUGIN_DIR


class FakeStore:
    """QgsSettings stand-in; a one-item list comes back as a plain string, as QSettings
    does with some backends."""

    def __init__(self, data=None):
        self.data = dict(data or {})

    def value(self, key, default=None):
        v = self.data.get(key, default)
        return v[0] if isinstance(v, list) and len(v) == 1 else v

    def setValue(self, key, value):  # noqa: N802
        self.data[key] = value


@contextlib.contextmanager
def fake_qgis(**core_attrs):
    """qgis, qgis.core, qgis.utils, qgis.PyQt.* as mocks for the duration."""
    names = ["qgis", "qgis.core", "qgis.utils", "qgis.PyQt", "qgis.PyQt.QtWidgets",
             "qgis.PyQt.QtCore", "qgis.PyQt.QtGui"]
    saved = {n: sys.modules.get(n) for n in names}
    mods = {n: mock.MagicMock(name=n) for n in names}
    for k, v in core_attrs.items():
        setattr(mods["qgis.core"], k, v)
    mods["qgis.utils"].iface = None
    sys.modules.update(mods)
    try:
        yield mods
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


def fresh(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class SettingsTest(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        s = core.load_settings(FakeStore())
        self.assertEqual(s, {"gpkg": "", "input_dir": "", "site_config": "", "layers": []})
        store = FakeStore()
        core.save_settings(store, {"gpkg": " C:/x/s.gpkg ", "input_dir": "D:/in", "site_config": "",
                                   "layers": ["a.geojson"]})
        back = core.load_settings(store)   # the single layer comes back as a string
        self.assertEqual(back["gpkg"], "C:/x/s.gpkg")
        self.assertEqual(back["layers"], ["a.geojson"])
        self.assertEqual(core.as_list(None), [])
        self.assertEqual(core.as_list(["a", " ", "b"]), ["a", "b"])


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def site(self, text):
        p = self.d / "site.toml"
        p.write_text(text)
        return str(p)

    def test_site_config_paths_are_relative_to_it(self):
        cfg = self.site('[season]\nname = "2030"\ngpkg = "out/2030.gpkg"\ninput_dir = "in"\n'
                        'layers = ["layers/a.geojson", "layers/b.geojson"]\n')
        r = core.resolve({"site_config": cfg})
        self.assertEqual(Path(r["gpkg"]), self.d / "out" / "2030.gpkg")
        self.assertEqual(Path(r["input_dir"]), self.d / "in")
        self.assertEqual([Path(p) for p in r["layers"]], [self.d / "layers/a.geojson", self.d / "layers/b.geojson"])
        self.assertEqual(Path(r["qgz"]), self.d / "out" / "2030.qgz")
        self.assertEqual(Path(r["photos_dir"]), self.d / "out" / "photos")
        self.assertEqual(Path(r["labels_csv"]), self.d / "out" / "2030-captures.csv")

    def test_settings_override_config(self):
        cfg = self.site('[season]\ngpkg = "a.gpkg"\ninput_dir = "in"\nlayers = ["x.geojson"]\n')
        r = core.resolve({"site_config": cfg, "gpkg": str(self.d / "b.gpkg"),
                          "input_dir": str(self.d / "drive"), "layers": [str(self.d / "y.geojson")]})
        self.assertEqual(Path(r["gpkg"]), self.d / "b.gpkg")
        self.assertEqual(Path(r["input_dir"]), self.d / "drive")
        self.assertEqual(r["layers"], [str(self.d / "y.geojson")])
        self.assertEqual(r["season"], "b")

    def test_season_name_from_gpkg_meta(self):
        g = self.d / "x.gpkg"
        con = sqlite3.connect(g)
        con.execute("CREATE TABLE icecube_meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO icecube_meta VALUES ('season', 'winter')")
        con.commit()
        con.close()
        self.assertEqual(Path(core.resolve({"gpkg": str(g)})["qgz"]), self.d / "winter.qgz")

    def test_errors(self):
        with self.assertRaises(core.SetupError):
            core.resolve({"site_config": str(self.d / "missing.toml")})
        with self.assertRaises(core.SetupError):
            core.resolve({"site_config": self.site("not = [toml")})
        with self.assertRaisesRegex(core.SetupError, "input folder"):
            core.import_argv(core.resolve({"gpkg": str(self.d / "s.gpkg")}))
        with self.assertRaisesRegex(core.SetupError, "GeoPackage"):
            core.import_argv(core.resolve({"input_dir": str(self.d)}))
        with self.assertRaisesRegex(core.SetupError, "not found"):
            core.import_argv(core.resolve({"gpkg": "s.gpkg", "input_dir": str(self.d / "nope")}))
        with self.assertRaisesRegex(core.SetupError, "layer"):
            core.import_argv(core.resolve({"gpkg": "s.gpkg", "input_dir": str(self.d),
                                           "layers": [str(self.d / "gone.geojson")]}))

    def test_import_argv(self):
        cfg = self.site('[season]\ngpkg = "s.gpkg"\n')
        lay = self.d / "a.geojson"
        lay.write_text("{}")
        argv = core.import_argv(core.resolve({"site_config": cfg, "input_dir": str(self.d),
                                              "layers": [str(lay)]}), force=True)
        self.assertEqual(argv, [str(self.d), "--gpkg", str(self.d / "s.gpkg"), "--config", cfg,
                                "--layers", str(lay), "--force"])

    def test_sources_and_same_file(self):
        self.assertEqual(core.source_path("C:/x/s.gpkg|layername=captures"), "C:/x/s.gpkg")
        self.assertEqual(core.source_path("file:///C:/x/s.gpkg|layername=a"), "C:/x/s.gpkg")
        self.assertEqual(core.source_path("/home/x/s.gpkg"), "/home/x/s.gpkg")
        self.assertTrue(core.same_file(str(self.d / "a" / ".." / "s.gpkg"), str(self.d / "s.gpkg")))
        self.assertFalse(core.same_file("", "x"))

    def test_tables_and_write_lock(self):
        g = self.d / "s.gpkg"
        self.assertEqual(core.gpkg_tables(g), set())
        self.assertIsNone(core.write_lock_error(g))   # no file yet: nothing to wait for
        con = sqlite3.connect(g, isolation_level=None)
        con.execute("CREATE TABLE gpkg_contents (table_name TEXT)")
        con.execute("INSERT INTO gpkg_contents VALUES ('captures')")
        self.assertEqual(core.gpkg_tables(g), {"captures"})
        self.assertIsNone(core.write_lock_error(g))
        con.execute("BEGIN IMMEDIATE")   # someone else is writing
        try:
            self.assertRegex(core.write_lock_error(g, timeout=0.2), "in use")
        finally:
            con.execute("ROLLBACK")
            con.close()

    def test_pipeline_dir_in_repo(self):
        self.assertEqual(core.pipeline_dir(PLUGIN_DIR), QGIS_DIR)
        with self.assertRaises(core.SetupError):
            core.pipeline_dir(self.d / "a" / "b")

    def test_summary(self):
        text = core.summary_text({
            "added": [{"session": "a", "captures": 3, "photos": 1, "replaced": False},
                      {"session": "b", "captures": 2, "photos": 0, "replaced": True}],
            "unchanged": ["c", "d"], "totals": {"sessions": 4, "captures": 20, "photos": 5, "refs": 2},
            "recomputed": True, "origin": "G"})
        self.assertIn("1 new session(s), 1 re-read, 2 unchanged; 5 captures and 1 photos read", text)
        self.assertIn("Season: 4 sessions, 20 captures", text)
        self.assertIn("recomputed about G", text)


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            package_plugin.main([str(self.d / "dist")])
        self.zip = self.d / "dist" / "icecube_qgis.zip"

    def tearDown(self):
        self.tmp.cleanup()

    def test_contents_and_metadata(self):
        with zipfile.ZipFile(self.zip) as z:
            names = set(z.namelist())
            meta = z.read("icecube_qgis/metadata.txt").decode()
        for f in ("__init__.py", "metadata.txt", "plugin.py", "core.py", "settings_dialog.py", "icon.svg"):
            self.assertIn(f"icecube_qgis/{f}", names)
        for m in core.PIPELINE:
            self.assertIn(f"icecube_qgis/pipeline/{m}.py", names)
        self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])
        self.assertTrue(all(n.startswith("icecube_qgis/") for n in names))
        keys = dict(line.split("=", 1) for line in meta.splitlines() if "=" in line)
        self.assertEqual(keys["qgisMinimumVersion"], "3.34")
        self.assertTrue(keys["qgisMaximumVersion"].startswith("4."))
        for k in ("name", "description", "version", "author", "email", "about", "icon"):
            self.assertTrue(keys.get(k), k)
        self.assertNotIn("supportsQt6", meta)   # dropped by QGIS 4: no effect

    def test_vendored_pipeline_runs_from_the_zip_alone(self):
        dest = self.d / "plugins"
        with zipfile.ZipFile(self.zip) as z:
            z.extractall(dest)
        pdir = core.pipeline_dir(dest / "icecube_qgis")
        self.assertEqual(pdir, dest / "icecube_qgis" / "pipeline")
        sess = synth.write_zip(self.d / "in" / "IceCubeLogger_2026-12-01_10-00-00.zip", synth.session_v05())
        gpkg = self.d / "out" / "s.gpkg"
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import import_sessions as m; "
                "r = m.run(sys.argv[2:], log=lambda s: None); print(json.dumps(r['totals']))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(pdir), str(sess.parent), "--gpkg", str(gpkg),
                              "--base", *map(str, synth.BASE)], capture_output=True, text=True,
                             cwd=self.d, check=True).stdout
        self.assertIn('"captures": 4', out)


class BuildProjectTest(unittest.TestCase):
    """build_project.py with qgis.* mocked: the .bak and the unsaved-changes rule."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.ctx = fake_qgis()
        self.mods = self.ctx.__enter__()
        self.bp = fresh("build_project", QGIS_DIR / "build_project.py")
        self.logged = []
        self.bp.LOG = self.logged.append
        self.gpkg = self.d / "s.gpkg"
        self.gpkg.write_bytes(b"")
        self.qgz = self.d / "s.qgz"
        self.bp.read_gpkg = lambda g: ([], {"season": "s"})
        self.bp.local_crs = lambda meta: mock.MagicMock()
        self.project = mock.MagicMock()
        self.project.isDirty.return_value = False
        self.project.mapLayers.return_value = {}
        self.project.fileName.return_value = str(self.qgz)
        self.project.write.side_effect = lambda p: Path(p).write_text("new") or True
        # project.crs() returns whatever setCrs() was last given, as in QGIS
        self.project.setCrs.side_effect = lambda c: setattr(self.project.crs, "return_value", c)
        self.bp.QgsProject.instance.return_value = self.project

    def test_crs_set_after_layers_and_checked(self):
        """A first layer that changes the project CRS must not survive the build."""
        crs = mock.MagicMock(name="local")
        self.bp.local_crs = lambda meta: crs
        self.bp.build(str(self.gpkg), ask=False)
        self.assertIs(self.project.crs(), crs)
        self.assertEqual(self.project.setCrs.call_args_list[-1], mock.call(crs))

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_not_run_on_import(self):
        self.assertFalse(self.mods["qgis.PyQt.QtWidgets"].QFileDialog.getOpenFileName.called)

    def test_backup_existing(self):
        self.assertIsNone(self.bp.backup_existing(str(self.qgz)))
        self.qgz.write_text("one")
        self.assertEqual(self.bp.backup_existing(str(self.qgz)), str(self.qgz) + ".bak")
        self.qgz.write_text("two")
        self.bp.backup_existing(str(self.qgz))
        self.assertEqual(Path(str(self.qgz) + ".bak").read_text(), "two")

    def test_build_keeps_bak_and_overwrites_without_asking(self):
        self.qgz.write_text("old")
        self.bp.iface = mock.MagicMock()
        self.assertEqual(self.bp.build(str(self.gpkg)), str(self.qgz))
        self.assertEqual(self.qgz.read_text(), "new")
        self.assertEqual(Path(str(self.qgz) + ".bak").read_text(), "old")
        self.assertFalse(self.mods["qgis.PyQt.QtWidgets"].QMessageBox.question.called)

    def test_first_build_has_no_bak(self):
        self.bp.build(str(self.gpkg))
        self.assertTrue(self.qgz.is_file())
        self.assertFalse(Path(str(self.qgz) + ".bak").exists())

    def test_asks_only_with_unsaved_changes(self):
        self.qgz.write_text("old")
        self.bp.iface = mock.MagicMock()
        self.project.isDirty.return_value = True
        qmb = self.mods["qgis.PyQt.QtWidgets"].QMessageBox
        qmb.question.return_value = qmb.StandardButton.No
        self.assertIsNone(self.bp.build(str(self.gpkg)))
        self.assertTrue(qmb.question.called)
        self.assertEqual(self.qgz.read_text(), "old")             # untouched
        self.assertFalse(Path(str(self.qgz) + ".bak").exists())
        qmb.question.return_value = qmb.StandardButton.Yes
        self.assertEqual(self.bp.build(str(self.gpkg)), str(self.qgz))
        self.assertEqual(Path(str(self.qgz) + ".bak").read_text(), "old")

    def test_unsaved_edits_count(self):
        lyr = mock.MagicMock()
        lyr.isEditable.return_value, lyr.isModified.return_value = True, True
        lyr.name.return_value = "captures"
        self.project.mapLayers.return_value = {"a": lyr}
        self.assertEqual(self.bp.unsaved_changes(self.project), ["unsaved edits in layer captures"])
        self.assertTrue(self.bp.confirm_replace(self.project, str(self.qgz), ask=False))


class ImportTaskTest(unittest.TestCase):
    """plugin.ImportTask.run with a stand-in QgsTask, on a synthetic session."""

    def test_run_and_cancel(self):
        class Task:
            def __init__(self, desc):
                self.canceled, self.progress = False, []

            def isCanceled(self):  # noqa: N802
                return self.canceled

            def setProgress(self, p):  # noqa: N802
                self.progress.append(p)

        logged = []
        msglog = mock.MagicMock()
        msglog.logMessage.side_effect = lambda m, tag, level: logged.append(m)
        with tempfile.TemporaryDirectory() as tmp, fake_qgis(QgsTask=Task, QgsMessageLog=msglog):
            d = Path(tmp)
            sys.path.insert(0, str(PLUGIN_DIR.parent))
            try:
                plugin = importlib.import_module("icecube_qgis.plugin")
                sess = synth.write_zip(d / "in" / "IceCubeLogger_2026-12-01_10-00-00.zip", synth.session_v05())
                argv = [str(sess.parent), "--gpkg", str(d / "s.gpkg"), "--base", *map(str, synth.BASE)]
                core.add_pipeline_path(PLUGIN_DIR)
                done = []
                t = plugin.ImportTask(argv, lambda *a: done.append(a))
                self.assertTrue(t.run())
                self.assertEqual(t.result["totals"]["captures"], 4)
                self.assertEqual([a["session"] for a in t.result["added"]], ["2026-12-01_10-00-00"])
                self.assertEqual(t.progress[-1], 100.0)
                self.assertTrue(any("1 session(s) found" in m for m in logged))
                t.finished(True)
                self.assertEqual(done[0][0], True)
                t2 = plugin.ImportTask(argv + ["--force"], lambda *a: None)
                t2.canceled = True
                self.assertFalse(t2.run())
                self.assertIn("cancelled", t2.error)
                t3 = plugin.ImportTask(["--bogus"], lambda *a: None)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertFalse(t3.run())
                self.assertIn("Settings", t3.error)
            finally:
                sys.path.remove(str(PLUGIN_DIR.parent))
                for n in [n for n in sys.modules if n.startswith("icecube_qgis")]:
                    del sys.modules[n]


if __name__ == "__main__":
    unittest.main()
