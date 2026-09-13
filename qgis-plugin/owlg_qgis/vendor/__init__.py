"""The OWLG decoder package, vendored inside the plugin.

It is deliberately a SUBPACKAGE (owlg_qgis.vendor.owlg) rather than a top-level
package named 'owlg'. Two reasons:

  1. When the plugin is reinstalled, QGIS purges sys.modules of everything under
     the 'owlg_qgis.' prefix -- this decoder included. A top-level 'owlg' would
     survive in the module cache, and the new installation would appear to have
     had no effect until QGIS was restarted.
  2. It can never collide with an 'owlg' package the user installed via pip.
"""
