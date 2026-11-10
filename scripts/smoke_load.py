"""Exercise the plugin's real load path, without starting QGIS.

``scripts/smoke_ui.py`` builds the panel directly and ``test/`` unit-tests the
shell, but neither walks the path QGIS actually takes:

    classFactory() -> initGui() -> run()/_show_panel() -> unload()

That path is where plugin-level mistakes live — a lazy import that is no longer
lazy enough, a dock added to the wrong area, an ``unload`` that leaves the dock
parented to the main window. This script drives it against a stub ``iface``
carrying only the methods the plugin calls.

``run()`` itself is skipped because it needs a valid API key; ``_show_panel()``
is called instead, which is exactly what ``run()`` does once auth succeeds.

Usage, from the plugin root::

    "C:/Program Files/QGIS 3.36.3/bin/python-qgis.bat" scripts/smoke_load.py
    "C:/Program Files/QGIS 4.0.1/bin/python-qgis.bat"  scripts/smoke_load.py
"""

import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_ROOT = os.path.dirname(PLUGIN_DIR)
PACKAGE = os.path.basename(PLUGIN_DIR)
sys.path.insert(0, PLUGINS_ROOT)

if not os.environ.get("QT_QPA_PLATFORM") and not sys.platform.startswith("win"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qgis.PyQt.QtCore import QSettings, Qt          # noqa: E402
from qgis.PyQt.QtWidgets import (                   # noqa: E402
    QApplication, QMainWindow, QToolBar,
)


class StubIface:
    """The handful of ``iface`` methods the plugin actually uses."""

    def __init__(self, window):
        self._window = window
        self._toolbar = QToolBar(window)
        self.docks = []
        self.toolbar_actions = []
        self.toolbars = []
        self.menu_entries = []

    def mainWindow(self):
        return self._window

    def addToolBar(self, title):
        """A real QToolBar, as QGIS returns — the plugin names it and fills it."""
        bar = QToolBar(title, self._window)
        self._window.addToolBar(bar)
        self.toolbars.append(bar)
        return bar

    def addToolBarIcon(self, action):
        # Only reached by the fallback path; the plugin asks for its own
        # toolbar first.
        self._toolbar.addAction(action)
        self.toolbar_actions.append(action)

    def removeToolBarIcon(self, action):
        self._toolbar.removeAction(action)
        if action in self.toolbar_actions:
            self.toolbar_actions.remove(action)

    def addPluginToMenu(self, title, action):
        self.menu_entries.append((title, action))

    def removePluginMenu(self, title, action):
        if (title, action) in self.menu_entries:
            self.menu_entries.remove((title, action))

    def addDockWidget(self, area, dock):
        self._window.addDockWidget(area, dock)
        self.docks.append((area, dock))

    def removeDockWidget(self, dock):
        self._window.removeDockWidget(dock)

    def mapCanvas(self):
        return None


def main():
    app = QApplication(sys.argv[:1])
    # QSettings keys are scoped by org/app name. QGIS sets these; without them
    # a value written here lands in a different store than the plugin reads.
    app.setOrganizationName("QGIS")
    app.setApplicationName("QGIS3")

    # RasidPlugin.__init__ does `QSettings().value('locale/userLocale')[0:2]`
    # with no None guard, so a profile that has never had a locale written
    # raises TypeError during classFactory. Every real QGIS session sets it.
    if QSettings().value("locale/userLocale") is None:
        QSettings().setValue("locale/userLocale", "en_US")

    window = QMainWindow()
    window.resize(1400, 900)
    window.show()
    iface = StubIface(window)

    package = __import__(PACKAGE)
    plugin = package.classFactory(iface)
    print(f"classFactory   -> {type(plugin).__name__}")

    plugin.initGui()
    print(f"initGui        -> actions {[a.text() for a in plugin.actions]}")
    # The button has to land on the PLUGINS toolbar, not the Web one, and the
    # Plugins menu entry has to exist: both are where a user looks for it.
    # Its OWN toolbar, named so QGIS can persist where the user puts it,
    # plus the Plugins menu entry.
    assert iface.toolbars, "the plugin did not create its own toolbar"
    bar = iface.toolbars[0]
    assert bar.objectName() == "GoPilotToolbar", bar.objectName()
    assert bar.actions(), "the toolbar has no button on it"
    assert not iface.toolbar_actions,         "fell back to the shared Plugins toolbar"
    assert iface.menu_entries, "no entry in the Plugins menu"
    assert all(title == "GoPilot" for title, _a in iface.menu_entries),         iface.menu_entries
    print(f"               -> toolbar={bar.objectName()!r} "
          f"buttons={[a.text() for a in bar.actions()]} "
          f"menu={[t for t, _a in iface.menu_entries]}")

    # What run() does once the API key validates.
    plugin._show_panel()
    app.processEvents()
    dock = plugin.dock
    assert dock is not None, "panel was not created"
    area = iface.docks[0][0]
    assert area == Qt.DockWidgetArea.RightDockWidgetArea, f"docked to {area}"
    assert dock.titleBarWidget() is dock._blank_title_bar,         "docked panel should have no title bar"
    assert dock.shell.mapTo(dock, dock.shell.rect().topLeft()).y() == 0,         "content should start at the very top when docked"
    print(f"_show_panel    -> docked right, floating={dock.isFloating()}")
    assert not dock.isFloating()

    dock.shell.rail.btn_float.click()
    app.processEvents()
    # Asserted the opposite until the bar was measured: undocked, the panel
    # wore the OS CAPTION, which setTitleBarWidget never controlled. The blank
    # bar stays set in both states and the caption is cleared through window
    # flags instead; see gopilot_dock._apply_floating_chrome.
    assert dock.titleBarWidget() is dock._blank_title_bar, \
        "floating panel should still have no Qt title bar"
    if sys.platform.startswith("win"):
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.restype = ctypes.c_long
        style = user32.GetWindowLongW(int(dock.winId()), -16) & 0xFFFFFFFF
        assert not style & 0x00C00000, "WS_CAPTION: the title bar is back"
        # No border either: Windows paints WS_THICKFRAME in the accent colour,
        # which looked like a bar even with no title or buttons on it.
        assert not style & 0x00040000, "WS_THICKFRAME: accent border is back"
        assert dock._resize.is_active(), "no resize zones on a frameless panel"
    print(f"undock (rail)  -> floating={dock.isFloating()}, "
          f"{dock.width()}x{dock.height()}, no caption")
    assert dock.isFloating()

    dock.shell.rail.btn_float.click()
    app.processEvents()
    print(f"re-dock (rail) -> floating={dock.isFloating()}")
    assert not dock.isFloating()

    # Reopening an existing panel must reuse it, not build a second one.
    plugin.run()
    app.processEvents()
    assert plugin.dock is dock, "run() rebuilt the panel instead of revealing it"
    assert len(iface.docks) == 1, f"panel added {len(iface.docks)} times"
    print("run() again    -> reused the existing panel")

    plugin.unload()
    app.processEvents()
    assert plugin.dock is None, "unload left the dock reference set"
    # And unload has to take both back off, or a reload leaves dead buttons.
    assert not iface.menu_entries, "unload left a menu entry behind"
    assert not bar.actions(), "unload left a button on the toolbar"
    assert plugin.toolbar is None, "unload left the toolbar reference set"
    print("unload         -> panel released")

    print("\nLOAD PATH OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
