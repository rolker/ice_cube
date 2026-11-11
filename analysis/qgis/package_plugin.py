#!/usr/bin/env python3
"""Build the Ice Cube QGIS plugin zip, self-contained.

    python3 analysis/qgis/package_plugin.py OUT_DIR

Writes OUT_DIR/icecube_qgis.zip: the plugin folder analysis/qgis/plugin/icecube_qgis/
(metadata.txt, the Python files, icon.svg) with the pipeline modules of analysis/qgis
(gpkg_writer, local_frame, logger_session, import_sessions, export_labels,
build_project) copied into icecube_qgis/pipeline/, so QGIS needs nothing from the repo.
Install it in QGIS with Plugins > Manage and Install Plugins > Install from ZIP; to
update, build and install the zip again. The zip holds code only, no site data.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE / "plugin" / "icecube_qgis"
sys.path.insert(0, str(PLUGIN))
from core import PIPELINE  # noqa: E402

SKIP_DIRS = {"__pycache__", "pipeline"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def plugin_version():
    for line in (PLUGIN / "metadata.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    return "?"


def build_zip(out_dir):
    """Write out_dir/icecube_qgis.zip; returns its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "icecube_qgis.zip"
    tmp = out.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(PLUGIN.rglob("*")):
            rel = f.relative_to(PLUGIN)
            if f.is_dir() or SKIP_DIRS & set(rel.parts) or f.suffix in SKIP_SUFFIXES:
                continue
            z.write(f, f"icecube_qgis/{rel.as_posix()}")
        for m in PIPELINE:
            z.write(HERE / f"{m}.py", f"icecube_qgis/pipeline/{m}.py")
        z.writestr("icecube_qgis/pipeline/README.txt",
                   "Copied from analysis/qgis by package_plugin.py; edit the originals there.\n")
    tmp.replace(out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", help="folder to write icecube_qgis.zip into")
    args = ap.parse_args(argv)
    out = build_zip(args.out_dir)
    print(f"{out} (Ice Cube plugin {plugin_version()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
