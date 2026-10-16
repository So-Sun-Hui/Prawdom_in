# SPDX-License-Identifier: GPL-2.0-or-later
"""Prawdom_in: local QGIS importer."""
def classFactory(iface):
    from .plugin import PrawdomPlugin
    return PrawdomPlugin(iface)
