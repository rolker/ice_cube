"""Ice Cube > Settings: the season GeoPackage, the season input folder, the site config
and the map-layer GeoJSON files, kept in QgsSettings (see core.SETTINGS)."""
from qgis.PyQt.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                                 QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                                 QVBoxLayout, QWidget)

from . import core


class SettingsDialog(QDialog):
    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ice Cube settings")
        self.setMinimumWidth(640)
        form = QFormLayout()

        self.gpkg = self._path_row(form, "Season GeoPackage", values["gpkg"], self._pick_gpkg,
                                   "The season .gpkg the import writes (photos/ and the .qgz go next "
                                   "to it). Empty: [season].gpkg of the site config; leave it empty "
                                   "to keep one site config per survey.")
        self.input_dir = self._path_row(form, "Season input folder", values["input_dir"], self._pick_dir,
                                        "Folder with the IceCubeLogger_<session>.zip shares (searched "
                                        "recursively), e.g. the Drive sessions folder. Empty: "
                                        "[season].input_dir of the site config; leave it empty to keep "
                                        "one site config per survey.")
        self.site_config = self._path_row(form, "Site config (TOML)", values["site_config"],
                                          self._pick_toml, "Private config with [season] and [site] "
                                          "(base, origin); relative paths in it are relative to it. One "
                                          "site config per survey: with the GeoPackage, input folder "
                                          "and map layers empty, all come from here, so switching "
                                          "surveys means changing only this path.")

        self.layers = QListWidget()
        self.layers.addItems(values["layers"])
        self.layers.setToolTip("Map-layer GeoJSON files imported as layer_* tables. "
                               "Empty: [season].layers of the site config.")
        add, remove = QPushButton("Add…"), QPushButton("Remove")
        add.clicked.connect(self._add_layers)
        remove.clicked.connect(lambda: [self.layers.takeItem(self.layers.row(i))
                                        for i in self.layers.selectedItems()])
        buttons = QVBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        row = QHBoxLayout()
        row.addWidget(self.layers, 1)
        row.addLayout(buttons)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Map layer GeoJSON files", holder)

        self.resolved = QLabel()
        self.resolved.setWordWrap(True)
        for w in (self.gpkg, self.input_dir, self.site_config):
            w.textChanged.connect(self._show_resolved)
        self.layers.model().rowsInserted.connect(self._show_resolved)
        self.layers.model().rowsRemoved.connect(self._show_resolved)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.resolved)
        layout.addWidget(box)
        self._show_resolved()

    def _path_row(self, form, label, value, picker, tip):
        edit = QLineEdit(value)
        edit.setToolTip(tip)
        btn = QPushButton("…")
        btn.setMaximumWidth(32)
        btn.clicked.connect(lambda: picker(edit))
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(btn)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(label, holder)
        return edit

    def _pick_gpkg(self, edit):
        p, _ = QFileDialog.getSaveFileName(self, "Season GeoPackage", edit.text(), "GeoPackage (*.gpkg)",
                                           options=QFileDialog.Option.DontConfirmOverwrite)
        if p:
            edit.setText(p)

    def _pick_dir(self, edit):
        p = QFileDialog.getExistingDirectory(self, "Season input folder", edit.text())
        if p:
            edit.setText(p)

    def _pick_toml(self, edit):
        p, _ = QFileDialog.getOpenFileName(self, "Site config", edit.text(), "TOML (*.toml);;All files (*)")
        if p:
            edit.setText(p)

    def _add_layers(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Map layer GeoJSON files", "",
                                             "GeoJSON (*.geojson *.json);;All files (*)")
        self.layers.addItems(ps)

    def values(self):
        return {"gpkg": self.gpkg.text().strip(), "input_dir": self.input_dir.text().strip(),
                "site_config": self.site_config.text().strip(),
                "layers": [self.layers.item(i).text() for i in range(self.layers.count())]}

    def _show_resolved(self, *_):
        try:
            r = core.resolve(self.values())
            lines = [f"GeoPackage: {r['gpkg'] or '(not set)'}",
                     f"Input folder: {r['input_dir'] or '(not set)'}",
                     f"Map layers: {len(r['layers'])} file(s)"]
            if r["qgz"]:
                lines.append(f"Project: {r['qgz']}")
            self.resolved.setText("In use:\n" + "\n".join(lines))
        except core.SetupError as e:
            self.resolved.setText(str(e))
