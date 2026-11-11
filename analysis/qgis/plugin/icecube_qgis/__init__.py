"""Ice Cube QGIS plugin: import Ice Cube Logger sessions into the season GeoPackage,
build the styled project, export labels. See docs/howto/qgis.md."""


def classFactory(iface):  # noqa: N802 (QGIS entry point)
    from .plugin import IceCubePlugin
    return IceCubePlugin(iface)
