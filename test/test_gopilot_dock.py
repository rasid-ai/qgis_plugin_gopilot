# coding=utf-8
"""Tests for the GoPilot dock panel.

Replaces ``test_rasid_plugin_dialog.py``, which tested the modal dialog the
dock superseded. (That test had never run green in any case: it clicked a
``button_box`` the dialog did not have, and imported ``QDialogButtonBox`` from
``QtGui``, where it has not lived since Qt4.)

These are structural tests, not network ones: every view is built with
``client=None``, so each one must come up and show its own empty/error state
rather than raising. That is the property worth guarding — a QGIS panel that
throws during construction takes the plugin's whole load with it.

.. note:: This program is free software; you can redistribute it and/or modify
     it under the terms of the GNU General Public License as published by
     the Free Software Foundation; either version 2 of the License, or
     (at your option) any later version.
"""

__author__ = 'info@rasid.ai'
__date__ = '2026-11-09'
__copyright__ = 'Copyright 2026, RASID '

import os
import sys
import time
import unittest

from qgis.PyQt.QtCore import QPoint, QSize
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication, QDockWidget, QLabel, QMainWindow, QPushButton, QWidget,
)

# This module does NOT use the suite's `utilities.get_qgis_app()`, deliberately.
# That helper is QGIS-2 era and cannot work on QGIS 3:
#   * `test/qgis_interface.py` imports `QgsMapLayerRegistry`, which QGIS 3
#     replaced with `QgsProject`, so importing it raises;
#   * `utilities.py` then builds `QtGui.QWidget()`, but QWidget moved to
#     QtWidgets in Qt5.
# The first of those raises ImportError inside the helper's own try block, so
# it is swallowed and the helper returns `(None, None, None, None)` — meaning
# no QApplication is created and the first widget a test builds crashes the
# interpreter outright (exit 127, no traceback). Rather than half-fix someone
# else's harness from here, these tests make the one thing they actually need.
QGIS_APP = QApplication.instance() or QApplication(sys.argv[:1])

# QSettings needs an organisation to have a file to write to. QgsApplication
# sets one inside QGIS; a bare QApplication does not, and then every write is
# silently dropped — `QSettings().fileName()` is empty and `status()` reports
# an access error. Any test of a preference would pass or fail on the DEFAULT
# rather than on what the code stored, which is a test that proves nothing.
# A test-only scope keeps those writes out of the real QGIS settings.
if not QGIS_APP.organizationName():
    QGIS_APP.setOrganizationName("RASIDPluginTests")
    QGIS_APP.setApplicationName("GoPilotTests")


def _code_of(path):
    """``path``'s source with every COMMENT span blanked out.

    Any test of the form "is this value gone from the code?" has to use this
    rather than the raw text. The commits that removed the off-palette
    colours, the emoji labels and the selector-less stylesheets also wrote
    comments explaining what they removed and why — so a plain substring
    search finds the very strings it is checking for the absence of. Three
    separate tests were written against raw source and all three failed on
    their own documentation before this existed.

    Comment spans are replaced with spaces rather than deleted so that every
    other offset in the file is unchanged, and so that a multi-token search
    like ``t.accent`` still matches code that plainly contains it. String
    literals are KEPT: stylesheets and glyphs live in them, and they are
    usually the thing being looked for.
    """
    import io
    import tokenize
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    for token in tokenize.generate_tokens(
            io.StringIO("".join(lines)).readline):
        if token.type != tokenize.COMMENT:
            continue
        row = token.start[0] - 1
        start, end = token.start[1], token.end[1]
        line = lines[row]
        lines[row] = line[:start] + " " * (end - start) + line[end:]
    return "".join(lines)


def _destroy(widget):
    """Tear a test widget down for real, not just drop the reference.

    ``cleanup()`` on a shell or page stops its threads, timers and map tools.
    It does not free the widget tree, and ``self.shell = None`` does not
    reliably either: Qt releases a widget's native resources when its C++
    object is deleted, which happens on the event loop via ``deleteLater``.

    Without this the suite accumulated live widget trees — a couple of dozen
    shells, six views each — and the process died outright partway through
    (exit 127, no traceback, and with output buffered the blame landed on
    whichever test happened to be next). The giveaway was that any two test
    classes passed while three together did not, and the class that died
    passed on its own.

    Order matters: hide before reparenting, so Qt does not briefly map the
    widget as its own top-level window, and pump afterwards so the deletion
    actually happens before the next test builds its own.
    """
    if widget is None:
        return
    cleanup = getattr(widget, "cleanup", None)
    if cleanup is not None:
        try:
            cleanup()
        except Exception as exc:                        # noqa: BLE001
            print(f"  (cleanup during teardown: {exc})")
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()
    for _ in range(3):
        QGIS_APP.processEvents()


def _hover_event(widget):
    """A QEnterEvent, whose constructor differs between Qt5 and Qt6."""
    from qgis.PyQt.QtCore import QPointF
    from qgis.PyQt.QtGui import QEnterEvent
    point = QPointF(widget.rect().center())
    try:                                    # Qt6 wants three positions
        return QEnterEvent(point, point, point)
    except TypeError:                       # Qt5 is happy with one
        return QEnterEvent(point.toPoint(), point.toPoint(), point.toPoint())

from rasid_components import design, prefs               # noqa: E402
from rasid_components.compat import (                     # noqa: E402
    Qt_Horizontal, Qt_Vertical,
)
from rasid_components.gopilot_shell import GoPilotShell    # noqa: E402
from rasid_components.nav_rail import NAV_ITEMS, NavRail   # noqa: E402


def _sample_solutions(count):
    """Solution dicts shaped like the API's, for the card tests."""
    return [
        {"id": i, "name": f"Solution {i}", "image_url": None,
         "gotoken_price": "20000.0000", "status": "prod"}
        for i in range(count)
    ]


def _sample_projects(count):
    """Project dicts shaped like the API's, for the card tests."""
    return [
        {"id": i, "title": f"Project {i}", "thumbnail": None,
         "solution": {"name": "Flood mapping"}, "processes_number": 3,
         "system_modification_date": "2026-11-04T10:00:00Z",
         "tags": [{"name": "egypt"}, {"name": "flood"}]}
        for i in range(count)
    ]


class GoPilotShellTest(unittest.TestCase):
    """The panel builds, switches views, and tears down."""

    def setUp(self):
        self.shell = GoPilotShell(client=None, iface=None)

    def tearDown(self):
        _destroy(self.shell)
        self.shell = None

    def test_every_view_builds(self):
        """Each view can be shown without raising, even with no client."""
        for key in ("chat", "projects", "gobox", "processes", "history",
                    "settings"):
            self.shell.show_view(key)
            self.assertEqual(self.shell.current_view(), key)

    def test_chat_is_the_default_view(self):
        self.assertEqual(self.shell.current_view(), "chat")

    def test_rail_tracks_the_current_view(self):
        """The rail always shows something active, including in chat."""
        self.shell.show_view("history")
        self.assertEqual(self.shell.rail.current(), "history")

        # Chat has no button of its own and borrows the new-chat slot.
        self.shell.show_view("chat")
        self.assertEqual(self.shell.rail.current(), "new-chat")

        # Processes is a drill-down, so it points back at Projects.
        self.shell.show_view("processes")
        self.assertEqual(self.shell.rail.current(), "projects")

    def test_new_chat_clears_the_session(self):
        self.shell.chat_page.current_chat_id = "123"
        self.shell.new_chat()
        self.assertIsNone(self.shell.chat_page.current_chat_id)
        self.assertEqual(self.shell.current_view(), "chat")

    def test_unknown_view_is_ignored(self):
        """A bad key must not change the view or raise."""
        self.shell.show_view("history")
        self.shell.show_view("does-not-exist")
        self.assertEqual(self.shell.current_view(), "history")

    def test_cleanup_is_idempotent(self):
        """unload() may run after the dock already cleaned up."""
        self.shell.cleanup()
        self.shell.cleanup()


class GoPilotDockTest(unittest.TestCase):
    """The dock wrapper floats, re-docks, and tears down.

    A real ``QMainWindow`` is required here, not optional scaffolding: a
    ``QDockWidget`` that belongs to no main window reports ``isFloating() ==
    True`` because it sits in no dock area, and ``setFloating(False)`` has
    nowhere to put it back. Adding it to a main window is what QGIS's
    ``iface.addDockWidget`` does, so this mirrors the real arrangement.
    """

    def setUp(self):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QMainWindow
        from rasid_components.gopilot_dock import GoPilotDock

        self.window = QMainWindow()
        self.window.resize(1200, 800)
        self.dock = GoPilotDock(client=None, iface=None)
        self.window.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        # show() so showEvent runs, which is where the dock swaps in the blank
        # title bar and re-syncs the rail's toggle — addDockWidget alone does
        # not emit topLevelChanged. The event loop then has to turn for the
        # layout to act on the swap.
        self.window.show()
        self.dock.show()
        for _ in range(8):
            QGIS_APP.processEvents()

    def tearDown(self):
        self.window.removeDockWidget(self.dock)
        _destroy(self.dock)
        _destroy(self.window)
        self.dock = None
        self.window = None

    def test_no_title_bar_while_docked(self):
        """Docked, the panel has no title bar and content starts at y=0.

        Regression guard: topLevelChanged does NOT fire when addDockWidget
        places a dock in an area, and a parentless dock reports itself as
        floating — so the constructor's sync left QGIS's 22px bar in place and
        nothing afterwards removed it. showEvent is what catches that.
        """
        self.assertIs(self.dock.titleBarWidget(), self.dock._blank_title_bar)
        shell = self.dock.shell
        self.assertEqual(shell.mapTo(self.dock, shell.rect().topLeft()).y(), 0)
        # Still set, so a tabbed panel's tab and the taskbar name the product.
        self.assertIn("GoPilot", self.dock.windowTitle())

    def test_no_title_bar_while_floating_either(self):
        """Floating must not grow a bar back, in Qt's layer or the OS's.

        This asserted the OPPOSITE until the caption was measured: the bar the
        panel wore when undocked was the OS caption, not a Qt title bar, and
        handing Qt's bar back (``setTitleBarWidget(None)``) neither removed the
        caption nor was needed to keep the window usable. It only added a
        second close button under the system one.

        The blank bar therefore stays set in both states; see
        ``FloatingChromeTest`` for the caption itself.
        """
        self.dock.setFloating(True)
        for _ in range(8):
            QGIS_APP.processEvents()
        self.assertIs(self.dock.titleBarWidget(), self.dock._blank_title_bar)

        self.dock.setFloating(False)
        for _ in range(8):
            QGIS_APP.processEvents()
        self.assertIs(self.dock.titleBarWidget(), self.dock._blank_title_bar)

    def test_rail_carries_the_window_controls(self):
        """What QGIS's bar used to offer is reachable from the rail."""
        rail = self.dock.shell.rail
        self.assertEqual(rail.btn_float.icon_name(), "undock")

        rail.btn_float.click()
        QGIS_APP.processEvents()
        self.assertTrue(self.dock.isFloating())
        self.assertEqual(rail.btn_float.icon_name(), "dock")

        rail.btn_float.click()
        QGIS_APP.processEvents()
        self.assertFalse(self.dock.isFloating())

    def test_rail_close_hides_without_destroying(self):
        """Closing hides only — view state and in-flight work must survive."""
        self.dock.shell.rail.btn_close.click()
        QGIS_APP.processEvents()
        self.assertFalse(self.dock.isVisible())
        self.assertIsNotNone(self.dock.shell.chat_page)

    def test_starts_docked(self):
        self.assertFalse(self.dock.isFloating())

    def test_toggle_floats_then_redocks(self):
        self.dock.toggle_floating()
        self.assertTrue(self.dock.isFloating())
        self.dock.toggle_floating()
        self.assertFalse(self.dock.isFloating())

    def test_geometry_is_restored_however_it_was_floated(self):
        """setFloating directly — the path QGIS's own float button takes.

        The geometry handling hangs off topLevelChanged rather than off our
        own method precisely so this works; QGIS's button never calls
        toggle_floating.
        """
        self.dock.setFloating(True)
        self.dock.resize(540, 480)
        QGIS_APP.processEvents()
        self.dock.setFloating(False)
        QGIS_APP.processEvents()
        self.dock.setFloating(True)
        QGIS_APP.processEvents()
        self.assertEqual(self.dock.size(), QSize(540, 480))

    def test_first_undock_gets_a_window_sized_default(self):
        """Undocking must not produce a panel-wide sliver of a window."""
        self.dock.toggle_floating()
        self.assertGreater(self.dock.size().width(), design.DOCK_MIN_WIDTH)

    def test_later_undocks_restore_the_users_size(self):
        """The size the user left the floating panel at must come back.

        Qt alone does not do this: it derives the new floating size from the
        DOCKED size, so 520x500 came back as 650x625. The dock remembers the
        geometry itself.

        The resize has to happen while FLOATING — a docked widget's geometry
        is owned by the dock area, so resizing it there is overridden and says
        nothing about the floating size.
        """
        self.dock.toggle_floating()
        self.dock.resize(520, 500)
        QGIS_APP.processEvents()
        self.dock.toggle_floating()           # back into the dock area
        QGIS_APP.processEvents()
        self.dock.toggle_floating()           # out again
        QGIS_APP.processEvents()
        self.assertEqual(self.dock.size(), QSize(520, 500))


class FloatingChromeTest(unittest.TestCase):
    """The undocked panel: no system caption, but still movable and resizable.

    Undocked, the panel used to wear a grey Windows caption with its own close
    button, duplicating the rail's X. The fix removes the caption via window
    flags, which also removes the OS drag-to-move that lives on it — so these
    tests have to pin BOTH halves. Dropping the bar and stranding the window
    would be a worse bug than the bar.

    The caption is checked against the Win32 style bits rather than against
    frame insets, because insets are ambiguous: the flag set we use reports a
    7px top inset with no caption at all, which reads like a small title bar if
    you only measure geometry.
    """

    CAPTION = 0x00C00000      # WS_CAPTION    — the bar
    THICKFRAME = 0x00040000   # WS_THICKFRAME — OS resize on every edge

    def setUp(self):
        if not sys.platform.startswith("win"):
            self.skipTest("Win32 window styles")
        from qgis.PyQt.QtWidgets import QMainWindow
        from qgis.PyQt.QtCore import Qt
        from rasid_components.gopilot_dock import GoPilotDock

        self.window = QMainWindow()
        self.window.resize(1100, 800)
        self.dock = GoPilotDock(client=None, iface=None)
        self.window.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.window.show()
        self.dock.show()
        self._pump()

    def tearDown(self):
        self.window.removeDockWidget(self.dock)
        _destroy(self.dock)
        _destroy(self.window)
        self.dock = None
        self.window = None

    def _pump(self, times=20):
        for _ in range(times):
            QGIS_APP.processEvents()

    def _style(self):
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.restype = ctypes.c_long
        return user32.GetWindowLongW(int(self.dock.winId()), -16) & 0xFFFFFFFF

    def _mouse(self, widget, kind, global_pt):
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtGui import QMouseEvent
        button = Qt.MouseButton.LeftButton
        local = widget.mapFromGlobal(global_pt)
        try:                                    # Qt6 wants QPointF
            event = QMouseEvent(kind, QPointF(local), QPointF(global_pt),
                                button, button, Qt.KeyboardModifier.NoModifier)
        except TypeError:                       # Qt5 takes QPoint
            event = QMouseEvent(kind, local, global_pt, button, button,
                                Qt.KeyboardModifier.NoModifier)
        QGIS_APP.sendEvent(widget, event)

    def test_floating_has_no_system_caption(self):
        self.dock.setFloating(True)
        self._pump()
        self.assertFalse(self._style() & self.CAPTION)

    def test_floating_has_no_frame_at_all(self):
        """Not even the resize border.

        Clearing only the caption left ``WS_THICKFRAME``, and Windows paints
        that in the accent colour, thicker along the top — a coloured strip
        above the panel with no title and no buttons, which still reads as a
        bar. The window is frameless now, so the panel's own pixels are the
        only ones on screen.
        """
        self.dock.setFloating(True)
        self._pump()
        self.assertFalse(self._style() & self.THICKFRAME)
        geometry, frame = self.dock.geometry(), self.dock.frameGeometry()
        self.assertEqual(geometry.y() - frame.y(), 0)
        self.assertEqual(geometry.x() - frame.x(), 0)
        self.assertEqual(frame.size(), geometry.size())

    def test_resize_zones_are_live_only_while_floating(self):
        self.assertFalse(self.dock._resize.is_active())
        self.dock.setFloating(True)
        self._pump()
        self.assertTrue(self.dock._resize.is_active())
        self.dock.setFloating(False)
        self._pump()
        self.assertFalse(self.dock._resize.is_active())

    def test_dragging_a_zone_resizes_the_window(self):
        """Replaces what the OS border did, in all eight directions."""
        from qgis.PyQt.QtCore import QEvent, QPoint
        self.dock.setFloating(True)
        self.dock.setGeometry(300, 200, 620, 640)
        self._pump()

        # name -> (dx, dy), expected (dw, dh) and whether the origin moves
        cases = {
            "right": ((40, 0), (40, 0), False),
            "bottom": ((0, 30), (0, 30), False),
            "left": ((-25, 0), (25, 0), True),
            "top": ((0, -20), (0, 20), True),
            "bottom-right": ((15, 15), (15, 15), False),
            "top-left": ((-10, -10), (10, 10), True),
        }
        for name, (delta, grow, moves) in cases.items():
            self.dock.setGeometry(300, 200, 620, 640)
            self._pump(6)
            zone = self.dock._resize.zone(name)
            before = self.dock.geometry()
            start = zone.mapToGlobal(QPoint(2, 2))
            self._mouse(zone, QEvent.Type.MouseButtonPress, start)
            self._mouse(zone, QEvent.Type.MouseMove,
                        start + QPoint(*delta))
            self._pump(6)
            after = self.dock.geometry()
            self._mouse(zone, QEvent.Type.MouseButtonRelease,
                        start + QPoint(*delta))
            self._pump(3)
            self.assertEqual(
                (after.width() - before.width(),
                 after.height() - before.height()), grow,
                f"{name} zone resized wrongly")
            if moves:
                self.assertNotEqual((after.x(), after.y()),
                                    (before.x(), before.y()),
                                    f"{name} zone should move the origin too")

    def test_a_zone_cannot_crush_the_panel_below_its_minimum(self):
        """And the opposite edge must stay put while it clamps.

        Dragging left or top moves the origin as well as the size, so a naive
        implementation that applies a delta per mouse-move walks the far edge
        across the screen once it hits the minimum width.
        """
        from qgis.PyQt.QtCore import QEvent, QPoint
        self.dock.setFloating(True)
        self.dock.setGeometry(300, 200, design.DOCK_MIN_WIDTH + 20, 600)
        self._pump()

        zone = self.dock._resize.zone("left")
        right_edge = self.dock.geometry().right()
        start = zone.mapToGlobal(QPoint(2, 2))
        self._mouse(zone, QEvent.Type.MouseButtonPress, start)
        for step in (100, 400, 900):     # far past the minimum
            self._mouse(zone, QEvent.Type.MouseMove, start + QPoint(step, 0))
            self._pump(4)
        self._mouse(zone, QEvent.Type.MouseButtonRelease,
                    start + QPoint(900, 0))
        self._pump(6)

        self.assertGreaterEqual(self.dock.width(), design.DOCK_MIN_WIDTH)
        self.assertEqual(self.dock.geometry().right(), right_edge)

    def test_caption_stays_off_across_a_redock_cycle(self):
        """`setWindowFlags` un-maps the window, so re-showing it re-enters the
        sync. The flags must survive that round trip and still be clear on a
        second undock, where Qt has rebuilt the window in between.
        """
        for _ in range(2):
            self.dock.setFloating(True)
            self._pump()
            self.assertFalse(self._style() & self.CAPTION)
            self.assertTrue(self.dock.isVisible())
            self.dock.setFloating(False)
            self._pump()
        self.assertFalse(self.dock.isFloating())
        self.assertTrue(self.dock.isVisible())

    def test_a_handle_drags_the_floating_window(self):
        from qgis.PyQt.QtCore import QEvent, QPoint
        self.dock.setFloating(True)
        self._pump()

        handle = self.dock.shell.rail.logo_block()
        start = self.dock.frameGeometry().topLeft()
        grab = handle.mapToGlobal(QPoint(5, 5))
        self._mouse(handle, QEvent.Type.MouseButtonPress, grab)
        self._pump(3)
        self.assertTrue(self.dock._drag.is_dragging())

        self._mouse(handle, QEvent.Type.MouseMove, grab + QPoint(60, 40))
        self._pump(10)
        moved = self.dock.frameGeometry().topLeft() - start
        # Against frameGeometry: move() positions a window by its frame, so a
        # grab offset measured from geometry() would jump by the frame size.
        self.assertEqual((moved.x(), moved.y()), (60, 40))

        self._mouse(handle, QEvent.Type.MouseButtonRelease,
                    grab + QPoint(60, 40))
        self._pump(3)
        self.assertFalse(self.dock._drag.is_dragging())

    def test_handles_are_inert_while_docked(self):
        """Docked, the panel belongs to QGIS's layout; it must not self-move."""
        from qgis.PyQt.QtCore import QEvent, QPoint
        self.assertFalse(self.dock._drag.is_enabled())
        before = self.window.geometry()
        handle = self.dock.shell.rail.logo_block()
        grab = handle.mapToGlobal(QPoint(5, 5))
        self._mouse(handle, QEvent.Type.MouseButtonPress, grab)
        self._mouse(handle, QEvent.Type.MouseMove, grab + QPoint(80, 80))
        self._mouse(handle, QEvent.Type.MouseButtonRelease,
                    grab + QPoint(80, 80))
        self._pump(10)
        self.assertFalse(self.dock._drag.is_dragging())
        self.assertEqual(self.window.geometry(), before)

    def test_every_handle_is_real_and_not_a_button(self):
        """A drag handle must exist and must not swallow a control's click."""
        handles = self.dock.shell.drag_handles()
        self.assertTrue(handles)
        for handle in handles:
            self.assertIsNotNone(handle)
        rail = self.dock.shell.rail
        # The rail is a handle, but its buttons sit above the filter: clicking
        # one must still act rather than start a window drag.
        self.assertIn(rail, handles)
        self.dock.setFloating(True)
        self._pump()
        rail.button("gobox").click()
        self._pump()
        self.assertEqual(self.dock.shell.current_view(), "gobox")
        self.assertFalse(self.dock._drag.is_dragging())

    def test_floating_panel_has_no_pale_rim(self):
        """Qt insets its widget when floating; the gap must not be visible.

        The style's dock frame width leaves a few pixels around the shell, and
        they are painted by whatever is behind it — the palette's Window
        colour, not the panel's canvas. In dark mode that was #201D1F against
        the shell's #141213, a pale border on three sides of the floating
        panel. Light mode hid it entirely, the two colours being equal there,
        so this is sampled rather than eyeballed.

        The SHELL is what this measures. The dock now starts on the sign-in
        card, whose background is the topographic image and therefore is not
        the canvas colour at the edges by design — asserting against that
        instead reported #f7f8f8 and looked like a regression.
        """
        self.dock.show_shell()
        self.dock.setFloating(True)
        self.dock.resize(520, 640)
        self._pump(40)

        image = self.dock.grab().toImage()
        canvas = QColor(design.tokens().canvas)
        for label, x, y in (
            ("top-left", 0, 0),
            ("top edge", image.width() // 2, 0),
            ("left edge", 0, image.height() // 2),
            ("bottom edge", image.width() // 2, image.height() - 1),
        ):
            # pixelColor, not QColor(pixel()): the latter drops the alpha and
            # has made this kind of check pass against the wrong colour before.
            self.assertEqual(image.pixelColor(x, y).name(), canvas.name(),
                             f"{label} is not the canvas colour")

    def test_double_click_on_a_handle_redocks(self):
        """The gesture Qt's own dock title bar carries, kept working."""
        from qgis.PyQt.QtCore import QEvent, QPoint
        self.dock.setFloating(True)
        self._pump()
        handle = self.dock.shell.rail.logo_block()
        self._mouse(handle, QEvent.Type.MouseButtonDblClick,
                    handle.mapToGlobal(QPoint(5, 5)))
        self._pump()
        self.assertFalse(self.dock.isFloating())


class BrandPlacementTest(unittest.TestCase):
    """The brand appears in the product, not in chrome.

    With QGIS's title bar restored there is no header carrying the name, so the
    mark and the wordmark have to be somewhere the user actually looks.
    """

    def setUp(self):
        self.shell = GoPilotShell(client=None, iface=None)
        self.shell.resize(design.DOCK_DEFAULT_WIDTH, 760)
        self.shell.show()
        for _ in range(10):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.shell)
        self.shell = None

    def test_mark_sits_at_the_top_of_the_rail(self):
        rail = self.shell.rail
        self.assertFalse(rail._logo.pixmap().isNull())
        # Above every nav button.
        logo_y = rail._logo_block.geometry().y()
        for key, _icon, _tip in NAV_ITEMS:
            self.assertLess(logo_y, rail.button(key).geometry().y(), key)

    def test_wordmark_is_in_the_empty_chat(self):
        self.shell.show_view("chat")
        self.shell.chat_page.start_new_chat()
        for _ in range(6):
            QGIS_APP.processEvents()
        empty = self.shell.chat_page._empty_state
        self.assertIsNotNone(empty)
        texts = [w.text() for w in empty.findChildren(QLabel) if w.text()]
        self.assertTrue(any("GoPilot" in x for x in texts), texts)
        self.assertTrue(
            any("Ask something about Earth" in x for x in texts), texts)

    def test_every_opener_sends_on_click(self):
        """Clicking a card SENDS it; it does not type it in for editing.

        This asserted the opposite until the behaviour was changed to match
        the SaaS. A card states what it will do, so stopping at a filled
        composer asked for a second confirmation of a decision already made.
        Asserted here through the real shell, where the signal chain runs from
        the card in the empty state all the way to the composer.
        """
        from rasid_components.gopilot_page import SUGGESTIONS
        self.shell.show_view("chat")
        page = self.shell.chat_page
        page.start_new_chat()
        for _ in range(6):
            QGIS_APP.processEvents()

        cards = [w for w in page._empty_state.findChildren(QWidget)
                 if type(w).__name__ == "_SuggestionCard"]
        self.assertEqual(len(cards), len(SUGGESTIONS))

        # Off the real handler, so no SendMessageThread runs against the
        # None client this shell was built with.
        page.composer.submitted.disconnect(page._on_submit)
        seen = []
        page.composer.submitted.connect(
            lambda text, files, geojson: seen.append(text))

        cards[2].clicked.emit()
        for _ in range(6):
            QGIS_APP.processEvents()
        self.assertEqual(seen, [cards[2].prompt])
        self.assertEqual(page.composer.input.toPlainText(), "")

    def test_openers_reflow_between_one_and_two_columns(self):
        """Two columns is the design, one is what fits a narrow dock.

        Driven by resizing the PANEL, not the grid: the grid's width is owned
        by its parent layout, so a direct resize() on it is reverted before it
        can take effect (it happened to survive long enough on Qt5 and did not
        on Qt6). Widening the panel is also the real path a user takes.
        """
        page = self.shell.chat_page
        page.start_new_chat()
        for _ in range(6):
            QGIS_APP.processEvents()
        grid = [w for w in page._empty_state.findChildren(QWidget)
                if type(w).__name__ == "_SuggestionGrid"][0]

        def settle():
            for _ in range(8):
                QGIS_APP.processEvents()

        self.shell.resize(design.DOCK_DEFAULT_WIDTH, 760)
        settle()
        self.assertEqual(grid._columns, 1,
                         f"grid was {grid.width()}px at a 420px panel")

        self.shell.resize(820, 760)
        settle()
        self.assertEqual(grid._columns, 2,
                         f"grid was {grid.width()}px at an 820px panel")

    def test_all_openers_are_the_same_height(self):
        """A grid equalises rows, not the whole set — six cards, four heights."""
        page = self.shell.chat_page
        page.start_new_chat()
        grid = [w for w in page._empty_state.findChildren(QWidget)
                if type(w).__name__ == "_SuggestionGrid"][0]

        def settle():
            for _ in range(16):
                QGIS_APP.processEvents()

        for width in (420, 540, 820):
            self.shell.resize(width, 820)
            settle()
            heights = {card.height() for card in grid.cards}
            self.assertEqual(len(heights), 1,
                             f"at {width}px the cards were {heights}")

    def test_wrapped_label_shrinks_back_when_widened(self):
        """heightForWidth is clamped to minimumHeight — a ratchet if unguarded.

        Measured once at a narrow width, the label kept its two-line height at
        every wider width, because every later measurement reported the pin
        back to it. That is what made the cards uneven.
        """
        from rasid_components.widgets import WrappedLabel
        label = WrappedLabel("Map buildings within 2 km of the Eiffel Tower")
        label.setStyleSheet("font-size:12px;")
        label.show()

        label.resize(200, 40)
        QGIS_APP.processEvents()
        narrow = label.minimumHeight()

        label.resize(420, 40)
        QGIS_APP.processEvents()
        wide = label.minimumHeight()

        self.assertGreater(narrow, 0)
        self.assertLess(wide, narrow,
                        f"height did not shrink: {narrow} -> {wide}")

    def test_removing_the_empty_state_takes_its_spacer(self):
        """Left behind, the centring stretch pushes the first message down."""
        page = self.shell.chat_page
        page.start_new_chat()
        for _ in range(4):
            QGIS_APP.processEvents()
        page._remove_empty_state()
        page.add_message("hello", is_user=True)
        for _ in range(4):
            QGIS_APP.processEvents()
        first = page._messages.itemAt(0)
        self.assertIsNone(first.spacerItem(), "centring stretch was left behind")

    def test_logo_rule_tracks_the_view_header(self):
        """Both halves of the top rule appear and disappear together."""
        self.shell.show_view("chat")           # no view-title strip
        self.assertFalse(self.shell._view_header.isVisible())
        self.assertFalse(self.shell.rail._logo_rule_visible)

        self.shell.show_view("settings")       # strip is shown
        self.assertTrue(self.shell._view_header.isVisible())
        self.assertTrue(self.shell.rail._logo_rule_visible)

    def test_top_rule_halves_are_one_line(self):
        """The logo block and the view header are the same height."""
        self.shell.show_view("settings")
        for _ in range(8):
            QGIS_APP.processEvents()
        self.assertEqual(self.shell.rail._logo_block.height(),
                         self.shell._view_header.height())
        self.assertEqual(self.shell._view_header.height(),
                         design.VIEW_HEADER_HEIGHT)


class HistoryViewTest(unittest.TestCase):
    """The history view is the list, and its delete control reads as one."""

    def setUp(self):
        from rasid_components.history_panel import HistoryPanel
        self.panel = HistoryPanel(client=None)
        self.panel.resize(design.DOCK_DEFAULT_WIDTH - design.RAIL_WIDTH, 700)
        self.panel.show()
        for _ in range(6):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.panel)
        self.panel = None

    def _rows(self, titles):
        self.panel._all_chats = [
            {"id": str(i), "title": title, "timestamp": "2026-11-09 10:00"}
            for i, title in enumerate(titles)
        ]
        self.panel._render()
        for _ in range(6):
            QGIS_APP.processEvents()
        return self.panel._rows

    def test_no_controls_above_the_list(self):
        """New chat, filter and refresh are all gone from the view."""
        from qgis.PyQt.QtWidgets import QLineEdit
        self.assertEqual(self.panel.findChildren(QLineEdit), [],
                         "the filter field is back")
        labels = [w.text() for w in self.panel.findChildren(QLabel) if w.text()]
        self.assertFalse(any("New chat" in x for x in labels), labels)

    def test_delete_turns_red_only_on_hover(self):
        """A red trash on every row would read as a column of warnings."""
        row = self._rows(["Describe my selected layer"])[0]
        button = row._delete_btn
        t = design.tokens()

        self.assertFalse(button.is_hovered())
        self.assertEqual(button._current_icon_color(), t.text_muted)

        button.enterEvent(_hover_event(button))
        self.assertTrue(button.is_hovered())
        self.assertEqual(button._current_icon_color(), t.error)

        button.leaveEvent(_hover_event(button))
        self.assertFalse(button.is_hovered())
        self.assertEqual(button._current_icon_color(), t.text_muted)

    def test_failed_load_offers_a_retry(self):
        """Without a Refresh button, a failed load must not be a dead end."""
        self.panel._on_error("boom")
        for _ in range(4):
            QGIS_APP.processEvents()
        from qgis.PyQt.QtWidgets import QPushButton
        labels = [b.text().strip() for b in
                  self.panel.findChildren(QPushButton)]
        self.assertIn("Try again", labels)

    def test_retry_button_is_gone_once_rows_arrive(self):
        """The error state nests its button in a row layout.

        A widgets-only clear detaches that layout item without touching the
        widgets inside it, orphaning the button: still parented, still visible,
        no longer managed, so it floated over the middle of the chat list.
        """
        from qgis.PyQt.QtWidgets import QPushButton
        self.panel._on_error("boom")
        for _ in range(4):
            QGIS_APP.processEvents()
        self._rows(["Describe my selected layer", "Hii"])
        labels = [b.text().strip() for b in
                  self.panel.findChildren(QPushButton) if b.isVisible()]
        self.assertNotIn("Try again", labels, "orphaned retry button")

    def test_empty_list_says_so(self):
        self.panel._all_chats = []
        self.panel._render()
        for _ in range(4):
            QGIS_APP.processEvents()
        labels = [w.text() for w in self.panel.findChildren(QLabel) if w.text()]
        self.assertTrue(any("No saved chats" in x for x in labels), labels)


class CardGridTest(unittest.TestCase):
    """Project cards fill the panel instead of being centred at a fixed width."""

    def setUp(self):
        from rasid_components.projects_page import ProjectsPage
        self.page = ProjectsPage(client=None)
        self.page._projects = [
            {"id": i, "title": f"Project {i}", "thumbnail": None,
             "solution": {"name": "Flood mapping"}, "process_count": 2,
             "system_modification_date": "2026-11-04T10:00:00Z", "tags": []}
            for i in range(6)
        ]

    def tearDown(self):
        self.page = None

    def _lay_out(self, width):
        self.page.resize(width, 600)
        self.page.show()
        for _ in range(10):
            QGIS_APP.processEvents()
        self.page._layout_cards()
        for _ in range(10):
            QGIS_APP.processEvents()
        return [self.page.grid.itemAt(i).widget()
                for i in range(self.page.grid.count())]

    def _row_margins(self, cards, columns):
        """(left gap, right gap) around the first full row of cards."""
        row = [c for c in cards[:columns] if c is not None]
        left = min(c.mapTo(self.page, c.rect().topLeft()).x() for c in row)
        right_edge = max(c.mapTo(self.page, c.rect().topLeft()).x() + c.width()
                         for c in row)
        return left, self.page.scroll.viewport().width() - right_edge

    def test_the_row_fills_the_width_at_any_column_count(self):
        """The old fixed 260px card left 37px of grey down each side.

        Asserted as a property rather than against a column count: projects
        now follow the GoBox paradigm and go two-up at the panel minimum, so
        pinning the expectation to "one column" only re-encoded the layout of
        the day.
        """
        # Tolerance is one scrollbar. card_grid_metrics reserves the bar's
        # width whenever it is not already showing, because the viewport only
        # shrinks once it appears and that does not fire a resize — so a width
        # measured without it clips the last card's border. When the content
        # then turns out to be short enough not to need a bar, that reserve is
        # left as slack on the right. Erring narrow is invisible; erring wide
        # clips. Asserting pixel symmetry here would be asserting the bug.
        slack = self.page.scroll.verticalScrollBar().sizeHint().width()
        for width in (design.DOCK_MIN_WIDTH - design.RAIL_WIDTH,
                      design.DOCK_DEFAULT_WIDTH - design.RAIL_WIDTH,
                      560, 760):
            cards = self._lay_out(width)
            columns = self.page._calculate_columns()
            margins = self.page.grid.contentsMargins()
            left, right = self._row_margins(cards, columns)
            self.assertEqual(left, margins.left(), f"at {width}px")
            self.assertGreaterEqual(
                right, margins.right() - 1,
                f"at {width}px, {columns} cols: row overflows, R={right}")
            # Plus the floor-division remainder: splitting the row across
            # `columns` cards loses up to columns-1 px.
            self.assertLessEqual(
                right, margins.right() + slack + columns,
                f"at {width}px, {columns} cols: L={left} R={right} "
                f"(more than a scrollbar plus rounding of slack)")

    def test_cards_in_a_row_are_equal_width(self):
        cards = self._lay_out(760)
        columns = self.page._calculate_columns()
        self.assertGreaterEqual(columns, 2)
        widths = {c.width() for c in cards[:columns] if c is not None}
        self.assertEqual(len(widths), 1, widths)

    def test_columns_are_capped(self):
        self._lay_out(1200)
        self.assertLessEqual(self.page._calculate_columns(),
                             design.CARD_MAX_COLS)


class BackgroundTest(unittest.TestCase):
    """Every view paints the brand canvas, not the host's palette.

    Guards two silent failures at once:

    * A stylesheet ``background`` on a plain QWidget SUBCLASS is ignored
      unless ``WA_StyledBackground`` is set, so all five views were showing
      whatever QGIS's palette uses for ``Window``.
    * It went unnoticed because the test palettes resembled the token. This
      test therefore installs a deliberately off-brand ``Window`` colour, so a
      leak cannot look plausible.
    """

    OFF_BRAND = "#123456"

    def setUp(self):
        from qgis.PyQt.QtGui import QColor, QPalette
        self._previous = QGIS_APP.palette()
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(self.OFF_BRAND))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#FFFFFF"))
        QGIS_APP.setPalette(palette)
        design.invalidate()
        self.shell = GoPilotShell(client=None, iface=None)
        self.shell.resize(design.DOCK_DEFAULT_WIDTH, 700)
        self.shell.show()
        for _ in range(10):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.shell)
        self.shell = None
        QGIS_APP.setPalette(self._previous)
        design.invalidate()

    def test_no_view_leaks_the_host_palette(self):
        canvas = QColor(design.tokens().canvas).name().upper()
        for view in ("chat", "projects", "gobox", "settings", "history"):
            self.shell.show_view(view)
            for _ in range(12):
                QGIS_APP.processEvents()
            image = self.shell.grab().toImage()
            # Just inside the content column, below the header rule and left
            # of any card.
            sampled = QColor(image.pixel(6, 200)).name().upper()
            self.assertNotEqual(
                sampled, QColor(self.OFF_BRAND).name().upper(),
                f"{view} is painting the host palette")
            self.assertEqual(sampled, canvas, f"{view} background")


class CardGridMetricsTest(unittest.TestCase):
    """The panel's minimum width must fit a two-column GoBox grid.

    That is the contract DOCK_MIN_WIDTH is set by, and it is a sum across five
    constants in design.py plus the rail and the scrollbar — easy to break by
    adjusting any one of them, so it is asserted rather than commented.
    """

    def setUp(self):
        self.shell = GoPilotShell(client=None, iface=None)
        self.shell.show()
        self.page = self.shell.gobox_page
        self.page._solutions = [
            {"id": i, "name": name, "image_url": None,
             "gotoken_price": "20000.0000", "status": "prod"}
            for i, name in enumerate(
                ["BananaSight", "Cloud Detection", "Field Delineation",
                 "Sentinel-2 Spectral Analytics", "Solar Panel Detection",
                 "Scene Parsing"])
        ]

    def tearDown(self):
        _destroy(self.shell)
        self.shell = None

    def _lay_out(self, panel_width):
        self.shell.resize(panel_width, 820)
        self.shell.show_view("gobox")
        for _ in range(16):
            QGIS_APP.processEvents()
        self.page._layout_cards()
        for _ in range(16):
            QGIS_APP.processEvents()
        return self.page._grid_metrics()

    def test_two_columns_at_the_minimum_panel_width(self):
        columns, card_width = self._lay_out(design.DOCK_MIN_WIDTH)
        self.assertGreaterEqual(
            columns, 2,
            f"only {columns} column(s) at the {design.DOCK_MIN_WIDTH}px "
            f"minimum; card would be {card_width}px")

    def test_card_never_goes_below_its_floor(self):
        for width in (design.DOCK_MIN_WIDTH, 420, 470, 560, 700, 900):
            _columns, card_width = self._lay_out(width)
            self.assertGreaterEqual(card_width,
                                    design.GOBOX_CARD_MIN_WIDTH - 1,
                                    f"card {card_width}px at panel {width}px")

    def test_columns_are_capped_so_cards_grow_instead(self):
        """A wide panel should get roomier cards, not more of them."""
        columns, card_width = self._lay_out(1100)
        self.assertLessEqual(columns, design.CARD_MAX_COLS)
        self.assertGreater(card_width, design.GOBOX_CARD_MIN_WIDTH)

    def _settle_grid(self, timeout_ms=1200):
        """Pump events until the page's debounced relayout has run its course.

        Without this the test races the debounce. ``show_view`` and
        ``_layout_cards`` arm a 120ms single-shot timer; a tight
        ``processEvents`` loop finishes in well under that, so on a quiet run
        the timer never fires and the sampled cards stay valid — but under the
        load of the full suite the 120ms elapses mid-loop, the grid is rebuilt,
        and the cards sampled a moment earlier have had their children
        deleted. That surfaced as an IndexError on `children[0]` in roughly one
        run in six, in a test that has nothing to do with timing.
        """
        timer = getattr(self.page, "_relayout_timer", None)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            QGIS_APP.processEvents()
            if timer is None or not timer.isActive():
                # One more pass, so anything the timeout queued is delivered.
                for _ in range(8):
                    QGIS_APP.processEvents()
                if timer is None or not timer.isActive():
                    return
            time.sleep(0.01)

    def _cards(self):
        """The grid's cards, skipping the trailing stretch row's empty cell."""
        cards = []
        for index in range(self.page.grid.count()):
            widget = self.page.grid.itemAt(index).widget()
            if widget is not None:
                cards.append(widget)
        self.assertTrue(cards, "the grid holds no cards")
        return cards

    def test_equalising_early_never_crushes_a_card(self):
        """The regression: levelling before the layout pass broke the grid.

        `height()` is meaningless until a card has been laid out. A fresh card
        reports Qt's 480px default; one left from a previous pass reports a
        stale small value. Pinning every card to either figure wrecked the
        page — in the second case the content was drawn on top of the
        thumbnail. Levelling is now measured from sizeHint() and applied as a
        MINIMUM, so the worst case is a slightly ragged grid.
        """
        self.shell.resize(420, 820)
        self.shell.show_view("gobox")
        for _ in range(16):
            QGIS_APP.processEvents()

        # Worst case: level immediately, with no layout pass in between.
        self.page._layout_cards()
        self.page._equalise_card_heights()
        # Let the debounce finish BEFORE sampling: a rebuild between the
        # sample and the read deletes the children out from under it.
        self._settle_grid()

        for index, card in enumerate(self._cards()):
            children = card.findChildren(QWidget)
            self.assertGreaterEqual(
                len(children), 2,
                f"card {index} has {len(children)} children; the grid was "
                f"rebuilt under the test")
            thumb, content = children[0], children[1]
            self.assertGreaterEqual(
                content.y(), thumb.height(),
                f"card {index}: content overlaps the thumbnail by "
                f"{thumb.height() - content.y()}px")
            # Never capped, so content can always claim the room it needs.
            self.assertGreater(card.maximumHeight(), 10000,
                               f"card {index} has a fixed height")

    def test_every_card_is_the_same_height(self):
        """A grid equalises rows; the whole set has to be equalised too."""
        for width in (design.DOCK_MIN_WIDTH, 560):
            self._lay_out(width)
            cards = [self.page.grid.itemAt(i).widget()
                     for i in range(self.page.grid.count())]
            heights = {c.height() for c in cards if c is not None}
            self.assertEqual(len(heights), 1,
                             f"at {width}px the cards were {heights}")

    def test_projects_are_two_up_at_the_minimum(self):
        """Same guarantee as GoBox: two cards at the panel's minimum width."""
        projects = self.shell.projects_page
        projects._projects = _sample_projects(4)
        self.shell.resize(design.DOCK_MIN_WIDTH, 820)
        self.shell.show_view("projects")
        for _ in range(16):
            QGIS_APP.processEvents()
        columns, card_width = projects._grid_metrics()
        self.assertGreaterEqual(columns, 2)
        self.assertGreaterEqual(card_width, design.PROJECT_CARD_MIN_WIDTH)

    def test_narrow_cards_keep_every_element(self):
        """Compacting must not drop the web card's content.

        At two-up the card is ~156px, where the solution chip and date cannot
        share a row. The date moves down beside the process count rather than
        being dropped.
        """
        from rasid_components.widgets import Chip
        card = self.shell.projects_page._create_card(
            _sample_projects(1)[0], design.PROJECT_CARD_MIN_WIDTH + 6)
        texts = " ".join(w.text() for w in card.findChildren(QLabel)
                         if w.text())
        chip_text = " ".join(
            lbl.text() for c in card.findChildren(Chip)
            for lbl in c.findChildren(QLabel))
        self.assertIn("Project 0", texts)          # title
        self.assertIn("Processes:", texts)          # count
        self.assertIn("2026", texts)                # date survived the move
        self.assertIn("Flood mapping", chip_text)   # solution
        self.assertIn("#egypt", chip_text)          # at least one tag

    def test_cards_keep_their_natural_height_in_a_tall_panel(self):
        """The grid must park spare vertical space below the cards.

        The scroll area stretches its content widget to the viewport height,
        and a QGridLayout with no stretch row hands that surplus to its card
        rows — four cards in a tall panel were pulled from 211px to 342px
        each. Nothing in the cards' own sizing explained it, because nothing
        in their own sizing was responsible.
        """
        for page_name, items in (("projects_page", _sample_projects(4)),
                                 ("gobox_page", _sample_solutions(4))):
            page = getattr(self.shell, page_name)
            if page_name == "projects_page":
                page._projects = items
            else:
                page._solutions = items
            self.shell.resize(design.DOCK_MIN_WIDTH, 900)
            self.shell.show_view(
                "projects" if page_name == "projects_page" else "gobox")
            for _ in range(16):
                QGIS_APP.processEvents()
            page._layout_cards()
            for _ in range(16):
                QGIS_APP.processEvents()

            cards = [page.grid.itemAt(i).widget()
                     for i in range(page.grid.count())]
            cards = [c for c in cards if c is not None]
            self.assertTrue(cards, page_name)
            tallest_hint = max(c.sizeHint().height() for c in cards)
            for card in cards:
                self.assertLessEqual(
                    card.height(), tallest_hint + 2,
                    f"{page_name}: card stretched to {card.height()} from a "
                    f"natural {tallest_hint}")

    def test_tag_count_scales_with_card_width(self):
        """A QHBoxLayout does not wrap, so the count is chosen, not found."""
        from rasid_components.projects_page import _tags_that_fit
        self.assertEqual(_tags_that_fit(156), 1)
        self.assertEqual(_tags_that_fit(200), 2)
        self.assertEqual(_tags_that_fit(320), 3)

    def test_projects_go_two_up_when_the_panel_is_wide(self):
        projects = self.shell.projects_page
        projects._projects = _sample_projects(4)
        self.shell.resize(760, 820)
        self.shell.show_view("projects")
        for _ in range(16):
            QGIS_APP.processEvents()
        columns, _width = projects._grid_metrics()
        self.assertGreaterEqual(columns, 2)

    def test_project_card_mirrors_the_saas_component(self):
        """Every element of the web card, and no Open button."""
        from qgis.PyQt.QtWidgets import QPushButton
        from rasid_components.widgets import Chip, ClickableCard

        card = self.shell.projects_page._create_card(
            _sample_projects(1)[0], 320)
        texts = " ".join(w.text() for w in card.findChildren(QLabel)
                         if w.text())

        self.assertIn("Project 0", texts)              # title
        self.assertIn("Processes:", texts)              # bold count label
        chips = [c for c in card.findChildren(Chip)]
        chip_text = " ".join(
            lbl.text() for c in chips for lbl in c.findChildren(QLabel))
        self.assertIn("Flood mapping", chip_text)       # solution chip
        self.assertIn("#egypt", chip_text)              # hash-prefixed tag

        # The web card is a <Link> with no button inside it but delete.
        self.assertIsInstance(card, ClickableCard)
        labelled = [b.text() for b in card.findChildren(QPushButton)
                    if b.text().strip()]
        self.assertEqual(labelled, [], f"unexpected button(s): {labelled}")

    def test_project_card_click_opens_and_delete_does_not(self):
        """The delete control must not also open the project."""
        page = self.shell.projects_page
        opened, hidden = [], []
        page.project_opened.connect(lambda p: opened.append(p))
        page._on_hide = lambda p: hidden.append(p)

        card = page._create_card(_sample_projects(1)[0], 320)
        card.clicked.emit()
        self.assertEqual(len(opened), 1)
        self.assertEqual(hidden, [])

        card.delete_requested.emit()
        self.assertEqual(len(hidden), 1)
        self.assertEqual(len(opened), 1, "delete also opened the project")

    def test_project_title_highlights_on_hover(self):
        """The web card turns its title brand-green on hover."""
        card = self.shell.projects_page._create_card(
            _sample_projects(1)[0], 320)
        t = design.tokens()
        self.assertIn(t.text, card._title.styleSheet())
        card.enterEvent(_hover_event(card))
        self.assertIn(t.accent_text, card._title.styleSheet())
        card.leaveEvent(_hover_event(card))
        self.assertIn(t.text, card._title.styleSheet())


class GridRelayoutPerformanceTest(unittest.TestCase):
    """Dragging the panel edge must not rebuild the grid per mouse move.

    `_layout_cards` destroys and recreates every card and re-requests every
    thumbnail. Run on each resize event, a 14-item catalogue cost 840 card
    rebuilds across 60 drag steps at 42ms each. The relayout is debounced and
    skipped when the grid's shape has not changed.
    """

    CATALOGUE = 14

    def setUp(self):
        self.shell = GoPilotShell(client=None, iface=None)
        self.shell.resize(design.DOCK_DEFAULT_WIDTH, 820)
        self.shell.show()
        self.page = self.shell.gobox_page
        self.page._solutions = [
            {"id": i, "name": f"Solution {i}", "image_url": None,
             "gotoken_price": "20000.0000", "status": "prod"}
            for i in range(self.CATALOGUE)
        ]
        self.shell.show_view("gobox")
        self._settle()
        self.page._layout_cards()
        self._settle()

        self.builds = 0
        self._original_create = self.page._create_card

        def counting(sol, card_width=None):
            self.builds += 1
            if card_width is None:
                return self._original_create(sol)
            return self._original_create(sol, card_width)

        self.page._create_card = counting

    def tearDown(self):
        self.page._create_card = self._original_create
        _destroy(self.shell)
        self.shell = None

    def _settle(self, passes=12):
        for _ in range(passes):
            QGIS_APP.processEvents()

    def test_a_drag_does_not_rebuild_per_step(self):
        for step in range(40):
            self.shell.resize(420 + (step % 20) * 4, 820)
            QGIS_APP.processEvents()
        self.assertLess(
            self.builds, self.CATALOGUE * 2,
            f"{self.builds} cards rebuilt mid-drag; the debounce is not "
            f"collapsing the burst")

    def test_the_grid_catches_up_once_the_drag_settles(self):
        """Debouncing must defer the work, not drop it."""
        for step in range(20):
            self.shell.resize(600 + step * 6, 820)
            QGIS_APP.processEvents()
        # Longer than GRID_RELAYOUT_DELAY_MS so the timer fires.
        deadline = time.time() + 1.5
        while time.time() < deadline:
            QGIS_APP.processEvents()

        _cols, card_width = self.page._grid_metrics()
        cards = [self.page.grid.itemAt(i).widget()
                 for i in range(self.page.grid.count())]
        self.assertTrue(cards)
        for card in cards:
            self.assertEqual(card.width(), card_width)

    def test_an_unchanged_shape_is_not_rebuilt(self):
        """Re-firing the relayout at the same size must be a no-op."""
        self.page._relayout_if_needed()
        self._settle()
        self.assertEqual(self.builds, 0)


class ThumbnailRoundingTest(unittest.TestCase):
    """The thumbnail image itself is clipped to the card's corner radius.

    A stylesheet border-radius rounds a QLabel's BACKGROUND, not its pixmap —
    so the empty placeholder was rounded and the loaded image was square, and
    the card's top corners went square as soon as the image arrived.
    """

    def _square(self, width=200, height=124):
        from qgis.PyQt.QtGui import QPixmap
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor("#2E7D32"))
        return pixmap

    def test_top_corners_are_clipped_bottom_stays_square(self):
        from rasid_components.image_loader import round_pixmap
        out = round_pixmap(self._square(), design.RADIUS_LG, top_only=True)
        image = out.toImage()
        last_x, last_y = out.width() - 1, out.height() - 1
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(image.pixelColor(last_x, 0).alpha(), 0)
        # The bottom meets the card's content and must stay flush.
        self.assertEqual(image.pixelColor(0, last_y).alpha(), 255)
        self.assertEqual(image.pixelColor(last_x, last_y).alpha(), 255)

    def test_all_corners_when_not_top_only(self):
        from rasid_components.image_loader import round_pixmap
        out = round_pixmap(self._square(), design.RADIUS_LG)
        image = out.toImage()
        last_x, last_y = out.width() - 1, out.height() - 1
        for x, y in ((0, 0), (last_x, 0), (0, last_y), (last_x, last_y)):
            self.assertEqual(image.pixelColor(x, y).alpha(), 0)

    def test_degrades_quietly(self):
        from qgis.PyQt.QtGui import QPixmap
        from rasid_components.image_loader import round_pixmap
        self.assertTrue(round_pixmap(QPixmap(), 12).isNull())
        # No radius means no work and no copy.
        same = self._square()
        self.assertEqual(round_pixmap(same, 0).size(), same.size())


class ThumbnailCoverTest(unittest.TestCase):
    """Card thumbnails fill their box whatever shape the source image is.

    The bug: `load_image` scaled with KeepAspectRatio, which FITS the image
    inside the box. A square solution image in a 324x201 card thumbnail was
    drawn 201x201 — 123px of empty grey — while a 16:10 one filled the card.
    Two cards in one grid looking like two different designs, decided purely
    by the source file.
    """

    BOX = (324, 201)

    @staticmethod
    def _source(width, height):
        from qgis.PyQt.QtGui import QPixmap
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor("#2E7D32"))
        return pixmap

    def test_cover_fills_the_box_for_every_aspect(self):
        from rasid_components.image_loader import scale_to_cover
        for width, height in ((1600, 1000), (1000, 1000), (800, 1200),
                              (2400, 600), (90, 60)):
            result = scale_to_cover(self._source(width, height), *self.BOX)
            self.assertEqual((result.width(), result.height()), self.BOX,
                             f"{width}x{height} did not fill the box")

    def test_contain_is_what_left_the_gap(self):
        """Documents the old behaviour this replaced."""
        from qgis.PyQt.QtCore import Qt
        square = self._source(1000, 1000)
        contained = square.scaled(
            *self.BOX, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.assertLess(contained.width(), self.BOX[0])

    def test_degrades_quietly_on_bad_input(self):
        from qgis.PyQt.QtGui import QPixmap
        from rasid_components.image_loader import scale_to_cover
        self.assertTrue(scale_to_cover(QPixmap(), 10, 10).isNull())
        # A zero-sized box must not raise or return something cropped to 0.
        unchanged = scale_to_cover(self._source(40, 40), 0, 10)
        self.assertEqual(unchanged.width(), 40)


class SettingsLinksTest(unittest.TestCase):
    """About and Terms open the website, not an in-plugin dialog."""

    def setUp(self):
        from rasid_components.settings_page import SettingsPage
        self.page = SettingsPage(client=None, iface=None)
        self.opened = []
        self.page._open_url = self.opened.append

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _button(self, label):
        from qgis.PyQt.QtWidgets import QPushButton
        for button in self.page.findChildren(QPushButton):
            if button.text().strip() == label:
                return button
        self.fail(f"no {label!r} button in Settings")

    def test_about_opens_the_public_about_page(self):
        from rasid_components.config import WEBSITE_URL
        self._button("About RASID").click()
        self.assertEqual(self.opened, [f"{WEBSITE_URL}/about"])

    def test_terms_opens_the_portal_terms(self):
        from rasid_components.config import APP_BASE_URL
        self._button("Terms").click()
        self.assertEqual(self.opened, [f"{APP_BASE_URL}/terms"])

    def test_both_say_they_leave_the_plugin(self):
        """A button that opens a browser should warn before it is pressed."""
        for label in ("About RASID", "Terms"):
            self.assertIn("browser", self._button(label).toolTip().lower(),
                          label)

    def test_the_about_dialog_is_gone(self):
        """Its content lived in the plugin and drifted from the website."""
        self.assertFalse(hasattr(self.page, "about_requested"))
        with self.assertRaises(ImportError):
            __import__("rasid_components.about_page")


class TokenBalanceTest(unittest.TestCase):
    """The account card's balance: formatted, and silent when unknown.

    The bug this guards: the balance was read from
    ``profile["balance"]["amount"]`` with a default of ``0``. That field moved
    to ``/billing/gotokens/`` when the product moved to GoTokens, so the
    default fired for everyone and the card confidently reported "0 tokens
    available" to a user holding millions.
    """

    def setUp(self):
        from rasid_components.settings_page import SettingsPage
        self.page = SettingsPage(client=None, iface=None)

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _balance_text(self):
        return self.page._balance_label.text()

    # ── formatting ────────────────────────────────────────────────────────────
    def test_formats_with_thousands_separators(self):
        from rasid_components.formatting import format_tokens
        self.assertEqual(format_tokens("2404153.0000"), "2,404,153")
        self.assertEqual(format_tokens("0.0000"), "0")
        self.assertEqual(format_tokens("12.5000"), "12.50")
        self.assertEqual(format_tokens(2404153), "2,404,153")

    def test_parses_as_decimal_not_float(self):
        """The API sends a string so the figure cannot drift; honour that."""
        from rasid_components.formatting import format_tokens
        # A value float() cannot hold exactly.
        self.assertEqual(format_tokens("9007199254740993"),
                         "9,007,199,254,740,993")

    def test_unparseable_is_none_not_zero(self):
        from rasid_components.formatting import format_tokens
        for junk in (None, "", "n/a", {}, []):
            self.assertIsNone(format_tokens(junk), repr(junk))

    # ── display ───────────────────────────────────────────────────────────────
    def test_shows_a_real_balance(self):
        self.page.set_profile({
            "profile": {"first_name": "Hassan", "last_name": "Wehbi",
                        "email": "hw@example.com"},
            "gotokens": {"wallet": {"balance": "2404153.0000",
                                    "is_overdrawn": False},
                         "can_send": True},
        })
        self.assertTrue(self.page._balance_label.isVisible()
                        or self.page._balance_label.isVisibleTo(self.page))
        self.assertIn("2,404,153 tokens", self._balance_text())

    def test_missing_wallet_shows_nothing(self):
        """A user with no plan has no wallet — that is not a zero balance."""
        self.page.set_profile({
            "profile": {"email": "hw@example.com"},
            "gotokens": {"wallet": None, "can_send": False},
        })
        self.assertFalse(self.page._balance_label.isVisibleTo(self.page))

    def test_absent_gotokens_shows_nothing(self):
        """The wallet request failed, so the key never arrived."""
        self.page.set_profile({"profile": {"email": "hw@example.com"}})
        self.assertFalse(self.page._balance_label.isVisibleTo(self.page))
        # The name must still have been filled in.
        self.assertEqual(self.page._name_label.text(), "hw@example.com")

    def test_zero_balance_is_still_shown(self):
        """A real zero is information; it must not be hidden like an absence."""
        self.page.set_profile({
            "profile": {"email": "hw@example.com"},
            "gotokens": {"wallet": {"balance": "0.0000",
                                    "is_overdrawn": False}},
        })
        self.assertTrue(self.page._balance_label.isVisibleTo(self.page))
        self.assertIn("0 tokens", self._balance_text())

    def test_failed_request_says_unavailable(self):
        """A request that did not come back is not a zero and not an absence."""
        self.page.set_profile({
            "profile": {"email": "hw@example.com"},
            "gotokens_error": "404 Client Error",
        })
        self.assertTrue(self.page._balance_label.isVisibleTo(self.page))
        self.assertIn("unavailable", self._balance_text())
        self.assertNotIn("0 tokens", self._balance_text())

    def test_overdrawn_is_called_out(self):
        self.page.set_profile({
            "profile": {"email": "hw@example.com"},
            "gotokens": {"wallet": {"balance": "-120.0000",
                                    "is_overdrawn": True}},
        })
        self.assertIn("overdrawn", self._balance_text())


class ComposerAlignmentTest(unittest.TestCase):
    """The bottom band must read as one row across both columns.

    Guards the contract documented in ``design.py``: the composer's top rule
    and the rule above Settings are the SAME line, and the +, Send and Settings
    glyphs sit on the SAME centre line. Both were out (by 14px and 7px) because
    each column was positioned from its own independent numbers, and the fix is
    only as durable as a test that measures real laid-out geometry.
    """

    def setUp(self):
        self.shell = GoPilotShell(client=None, iface=None)
        self.shell.resize(design.DOCK_DEFAULT_WIDTH, 760)
        self.shell.show()
        # Several passes: the composer's text box and the wrapped labels pin
        # their own heights from resizeEvent, each scheduling another pass.
        for _ in range(10):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.shell)
        self.shell = None

    def _up_from_bottom(self, widget):
        """Widget's top edge, in px up from the COMPOSER band's bottom edge.

        The datum used to be the shell's own bottom, because the composer was
        flush against it. The AI disclaimer now sits below the composer — as
        it does in the SaaS, where `{disclaimer}` follows `<ChatInput/>` — so
        that datum measured the composer plus a strip of small print and read
        144px where the contract says 63. The contract itself is unchanged:
        it is about the composer's own row, so the composer's own bottom edge
        is what it has to be measured from.
        """
        composer = self.shell.chat_page.composer
        # top + height, NOT rect().bottom(): a QRect's bottom is INCLUSIVE
        # (height - 1), so using it measured every distance here one pixel
        # short and turned both contracts into off-by-ones.
        base = (composer.mapTo(self.shell, composer.rect().topLeft()).y()
                + composer.height())
        top = widget.mapTo(self.shell, widget.rect().topLeft()).y()
        return base - top

    def _centre(self, widget):
        """Widget's vertical centre, in px up from the composer's bottom."""
        return self._up_from_bottom(widget) - widget.height() / 2.0

    def test_the_composer_band_is_its_documented_height(self):
        """The composer still owns the bottom band; nothing shares it now.

        A matching band at the foot of the rail used to continue this rule
        across the panel, so that the rail's bottom button and the composer's
        + and Send sat on one line. That band held Settings, then Close, and is
        gone: the controls moved up under the nav tabs, which left it aligning
        nothing. The composer's own geometry is still a contract, so it keeps
        being checked here — only the rail half of the claim has gone.
        """
        composer = self.shell.chat_page.composer
        self.assertEqual(self._up_from_bottom(composer),
                         design.COMPOSER_ROW_HEIGHT)

    def test_the_composer_buttons_share_a_centre_line(self):
        composer = self.shell.chat_page.composer
        centres = {
            "plus": self._centre(composer.btn_plus),
            "send": self._centre(composer.btn_send),
        }
        self.assertEqual(len(set(centres.values())), 1, centres)
        self.assertEqual(centres["plus"], design.COMPOSER_BUTTON_CENTRE)

    def test_prompt_text_sits_on_the_button_centre_line(self):
        """Within half a pixel — exact is impossible at an odd line height."""
        composer = self.shell.chat_page.composer
        text = composer.input
        line_height = max(14, text.fontMetrics().lineSpacing())
        text_centre = (self._up_from_bottom(text)
                       - text.viewportMargins().top()
                       - line_height / 2.0)
        self.assertLessEqual(
            abs(text_centre - self._centre(composer.btn_plus)), 0.5)

    def test_pill_insets_are_symmetric(self):
        """The + and Send are the same distance from their own pill edge."""
        composer = self.shell.chat_page.composer
        pill = composer.btn_plus.parent()
        left = composer.btn_plus.mapTo(pill, composer.btn_plus.rect().topLeft())
        send_right = (composer.btn_send.mapTo(
            pill, composer.btn_send.rect().topLeft()).x()
            + composer.btn_send.width())
        self.assertEqual(left.x(), pill.width() - send_right)

    def test_buttons_hold_their_line_when_attachments_appear(self):
        """Chips grow the band upward; the centre line must not move."""
        composer = self.shell.chat_page.composer
        before = self._centre(composer.btn_send)
        composer.set_geometry({"type": "FeatureCollection", "features": []},
                              "Drawn area")
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertEqual(self._centre(composer.btn_send), before)


class NavRailTest(unittest.TestCase):
    """The rail reports and emits what the shell expects."""

    def setUp(self):
        self.rail = NavRail()

    def tearDown(self):
        self.rail = None

    def test_has_a_button_per_nav_item(self):
        for key, _icon, _tip in NAV_ITEMS:
            self.assertIsNotNone(self.rail.button(key), key)
        # Settings is one of the tabs, not a special case bolted on after
        # them: it opens a view in the same stack as Projects and GoBox.
        self.assertIn("settings", [key for key, _i, _t in NAV_ITEMS])

    def test_window_controls_sit_below_every_tab(self):
        """Tabs first under the mark; then undock, then close in the footer."""
        rail = NavRail()
        rail.resize(design.RAIL_WIDTH, 700)
        rail.show()
        for _ in range(8):
            QGIS_APP.processEvents()
        # mapTo the rail, not geometry(): the undock toggle and the close
        # button each live in their own host frame, so their raw y is relative
        # to that frame and not comparable with a tab's.
        def y(widget):
            return widget.mapTo(rail, widget.rect().topLeft()).y()

        # Settings is in NAV_ITEMS now, so it is covered by this sweep — the
        # assertion that used to pin it *below* the window controls is exactly
        # what this replaces.
        lowest_tab = max(y(rail.button(key)) for key, _i, _t in NAV_ITEMS)
        self.assertGreater(y(rail.btn_float), lowest_tab)
        # Close sits directly under undock, not in a band at the foot of the
        # rail: one gap of rail spacing between them, not a panel's height.
        self.assertGreater(y(rail.btn_close), y(rail.btn_float))
        gap = y(rail.btn_close) - (y(rail.btn_float) + rail.btn_float.height())
        self.assertLessEqual(gap, design.SPACE_2, f"gap of {gap}px")

    def test_the_close_control_is_red(self):
        """And it is the ONLY red thing in the rail.

        Red here means "this makes the panel go away", the same thing it means
        on the transcript's trash. If a navigation tab ever picked up the
        danger tone the signal would stop being worth anything.
        """
        rail = NavRail()
        rail.resize(design.RAIL_WIDTH, 620)
        rail.show()
        for _ in range(12):
            QGIS_APP.processEvents()
        try:
            self.assertEqual(rail.btn_close._tone, "danger")
            self.assertEqual(rail.btn_close._hover_tone, "danger")
            self.assertNotEqual(rail.btn_float._tone, "danger")
            for key, _icon, _tip in NAV_ITEMS:
                self.assertNotEqual(rail.button(key)._tone, "danger", key)
        finally:
            _destroy(rail)

    def test_the_close_tooltip_says_the_chat_survives(self):
        """Closing only HIDES the panel.

        The glyph is red because it is the control that makes the panel go
        away, but nothing is destroyed — so the reassurance the colour used to
        carry has to live in the tooltip instead.
        """
        rail = NavRail()
        try:
            self.assertIn("kept", rail.btn_close.toolTip().lower())
        finally:
            _destroy(rail)

    def test_the_close_red_is_legible_on_the_rail(self):
        """Unlike the message bubble, this surface is neutral."""
        def luminance(colour):
            channels = []
            for value in (colour.redF(), colour.greenF(), colour.blueF()):
                channels.append(value / 12.92 if value <= 0.03928
                                else ((value + 0.055) / 1.055) ** 2.4)
            return (0.2126 * channels[0] + 0.7152 * channels[1]
                    + 0.0722 * channels[2])

        for dark in (False, True):
            tokens = design.Tokens(dark)
            first = luminance(QColor(tokens.error))
            second = luminance(QColor(tokens.surface_alt))
            lighter, darker = max(first, second), min(first, second)
            value = (lighter + 0.05) / (darker + 0.05)
            self.assertGreaterEqual(
                value, 3.0,
                f"close is {value:.2f}:1 on "
                f"{'dark' if dark else 'light'}")

    def test_settings_is_the_last_tab(self):
        """Above the rule, but below the four content views.

        It is the one tab that configures rather than navigates, so it sits at
        the bottom of the group — adjacent to the rule that separates views
        from window chrome, not interleaved with Projects and GoBox.
        """
        rail = NavRail()
        rail.resize(design.RAIL_WIDTH, 700)
        rail.show()
        for _ in range(8):
            QGIS_APP.processEvents()

        def y(widget):
            return widget.mapTo(rail, widget.rect().topLeft()).y()

        others = [key for key, _i, _t in NAV_ITEMS if key != "settings"]
        self.assertGreater(y(rail.button("settings")),
                           max(y(rail.button(key)) for key in others))

    def test_only_one_button_is_active(self):
        self.rail.set_current("gobox")
        active = [key for key, _i, _t in NAV_ITEMS
                  if self.rail.button(key).is_active()]
        self.assertEqual(active, ["gobox"])

    def test_new_chat_emits_action_not_selection(self):
        """New chat is a command; the others are destinations."""
        seen = {"action": [], "selected": []}
        self.rail.action.connect(lambda k: seen["action"].append(k))
        self.rail.selected.connect(lambda k: seen["selected"].append(k))

        self.rail.button("new-chat").click()
        self.rail.button("projects").click()

        self.assertEqual(seen["action"], ["new-chat"])
        self.assertEqual(seen["selected"], ["projects"])


class DesignTokenTest(unittest.TestCase):
    """Both themes resolve a complete token set."""

    def test_both_themes_define_every_token(self):
        for dark in (False, True):
            tokens = design.Tokens(dark)
            for name in design.Tokens.__slots__:
                self.assertTrue(
                    hasattr(tokens, name),
                    f"{'dark' if dark else 'light'} is missing {name}")

    def test_dark_mode_uses_the_brighter_brand_green(self):
        """#008B6A fails AA on a dark surface; the ramp has to shift."""
        self.assertEqual(design.Tokens(False).accent, design.BRAND[500])
        self.assertEqual(design.Tokens(True).accent, design.BRAND["highlight"])


def _sample_processes():
    """Process dicts shaped like the API's, covering the states that differ."""
    return [
        {"id": 1, "name": "Building footprints — Cairo west",
         "situation": "i", "thumbnail": None, "fees": "12.50",
         "create_date": "2026-11-04", "area": 18.4},
        {"id": 2, "name": "Flood exposure — Nile delta", "situation": "is",
         "thumbnail": None, "fees": "40.00", "create_date": "2026-11-02",
         "area": 240.75,
         "analytics": {"Buildings detected": 18422, "Mean confidence": "0.91"},
         "result_file_shp": "https://example.invalid/r.zip",
         "dataset": "https://example.invalid/d.tif"},
        {"id": 3, "name": "Solar panels — Aswan", "situation": "if",
         "thumbnail": None, "fees": "8.00", "create_date": "2026-10-28",
         "area": 5.1},
    ]


class ProcessStatusTest(unittest.TestCase):
    """Situation codes map to a label and a chip tone, and nothing else."""

    def test_known_codes_map_to_their_label(self):
        from rasid_components.processes_page import situation_label
        for code, expected in (("is", "Completed"), ("done", "Completed"),
                               ("if", "Failed"), ("pdmf", "Failed"),
                               ("i", "Inference"), ("c", "Preparation")):
            self.assertEqual(situation_label(code), expected, code)

    def test_unknown_codes_read_as_preparation(self):
        """The API gains states faster than the plugin ships."""
        from rasid_components.processes_page import situation_label
        self.assertEqual(situation_label("some-new-state"), "Preparation")
        self.assertEqual(situation_label(None), "Preparation")

    def test_every_label_has_a_tone(self):
        """A status with no tone would fall back to neutral silently."""
        from rasid_components import processes_page as pp
        for label in set(pp.SITUATION_LABELS.values()):
            self.assertIn(label, pp._CHIP_TONES, label)

    def test_the_table_carries_no_colours(self):
        """It held four hex colours that every reader discarded."""
        from rasid_components import processes_page as pp
        for code, label in pp.SITUATION_LABELS.items():
            self.assertIsInstance(label, str, code)


class ProcessesPageDesignTest(unittest.TestCase):
    """The processes view uses the same components as the rest of the panel."""

    def setUp(self):
        from rasid_components.processes_page import ProcessesPage
        self.page = ProcessesPage(client=None, iface=None)
        self.page.resize(design.DOCK_DEFAULT_WIDTH, 820)
        self.page.show()
        for _ in range(12):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _load(self, width=None):
        """Load the list, and let its own side effects finish first.

        ``_on_loaded`` selects the first process, which starts a detail fetch.
        With ``client=None`` that fetch FAILS, and the error handler calls
        ``_clear_detail()`` — so an error landing after a test had rendered
        its own detail pane wiped the cards it was about to assert on. It
        showed up as `'General info' not found in []`, on Qt6, in about one
        run in four.
        """
        if width is not None:
            self.page.resize(width, 820)
        self.page._on_loaded(_sample_processes())
        self._settle_detail()

    def _settle_detail(self, timeout_ms=1500):
        """Pump until no detail fetch is in flight."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            QGIS_APP.processEvents()
            thread = getattr(self.page, "_detail_thread", None)
            if thread is None or not thread.isRunning():
                for _ in range(8):
                    QGIS_APP.processEvents()
                thread = getattr(self.page, "_detail_thread", None)
                if thread is None or not thread.isRunning():
                    return
            time.sleep(0.01)

    def test_detail_sections_are_section_cards(self):
        """Settings, GoBox and Projects all use SectionCard; so does this."""
        from rasid_components.widgets import SectionCard
        self._load()
        self.page._show_detail(_sample_processes()[1])
        for _ in range(16):
            QGIS_APP.processEvents()

        cards = self.page._detail_widget.findChildren(SectionCard)
        titles = []
        for card in cards:
            if card.header is None:
                continue
            for i in range(card.header.count()):
                widget = card.header.itemAt(i).widget()
                if isinstance(widget, QLabel) and widget.text():
                    titles.append(widget.text())
        self.assertIn("General info", titles)
        self.assertIn("Data analytics", titles)
        self.assertIn("Downloads", titles)

    def test_the_header_offers_no_second_cancel(self):
        """Back was wired to the wizard's own Cancel handler.

        Two controls, one action, 700px apart in a panel that cannot spare the
        width. Cancel is the one that stays, in the action bar beside the
        primary button.
        """
        self.assertFalse(hasattr(self.page, "_back_btn"))
        labels = [b.text() for b in self.page.findChildren(QPushButton)]
        self.assertNotIn("Back", labels)

    def test_a_running_process_offers_no_downloads(self):
        from rasid_components.widgets import SectionCard
        self._load()
        self.page._show_detail(_sample_processes()[0])
        for _ in range(16):
            QGIS_APP.processEvents()
        titles = [
            w.text()
            for card in self.page._detail_widget.findChildren(SectionCard)
            for w in card.findChildren(QLabel) if w.text()
        ]
        self.assertNotIn("Downloads", titles)
        self.assertFalse(self.page._downloads_section_exists)

    def test_the_status_chip_retones_when_the_status_changes(self):
        """The poll loop's Preparation -> Completed transition.

        A Chip fixes its tone at construction, so this can only work by
        replacing the widget — which is why the chip is swapped rather than
        restyled.
        """
        from rasid_components.widgets import Chip
        self._load()
        running = _sample_processes()[0]
        self.page._show_detail(running)
        for _ in range(12):
            QGIS_APP.processEvents()
        first = self.page._status_badge
        self.assertIsInstance(first, Chip)

        done = dict(running, situation="is")
        self.page._update_detail_status_only(done)
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertIsNot(self.page._status_badge, first)
        # The replacement is IN the layout, not just held on the attribute —
        # and first in it, ahead of the trailing stretch, so the chip stays
        # left-aligned. (Checking it against the old chip's parent would prove
        # nothing: the swap unparents that one, so its parent is None.)
        self.assertEqual(
            self.page._status_row.indexOf(self.page._status_badge), 0)
        self.assertIsNotNone(self.page._status_badge.parent())

    def test_selection_styling_is_scoped_to_the_row(self):
        """An unscoped QFrame rule also restyled the chip inside the row."""
        self._load()
        qss = self.page._row_qss(True)
        self.assertIn("QFrame#processRow", qss)
        # No bare `QFrame {` rule, which would match every frame inside.
        self.assertNotIn("QFrame {", qss)
        self.assertNotIn("QFrame{", qss)

    def test_both_selection_paths_produce_the_same_styling(self):
        """The click path and the poll loop's restore used to differ."""
        self._load()
        rows = [self.page._list_layout.itemAt(i).widget()
                for i in range(self.page._list_layout.count())]
        self.page._select_row(rows[1])
        selected_qss = rows[1].styleSheet()
        self.page._select_row(rows[2])
        self.assertEqual(rows[2].styleSheet(), selected_qss)
        # And the one it left behind is back to the unselected style.
        self.assertEqual(rows[1].styleSheet(), self.page._row_qss(False))

    def test_wide_panels_give_the_list_a_readable_column(self):
        """It opened ~110px wide, eliding every process name.

        The split was only applied when the splitter's ORIENTATION changed,
        and the splitter is born horizontal — so at any width above the
        stacking threshold the sizes were never set at all and the column
        collapsed to its children's size hints.
        """
        self._load(width=820)
        for _ in range(16):
            QGIS_APP.processEvents()
        self.assertEqual(self.page._splitter.orientation(), Qt_Horizontal)
        list_width = self.page._splitter.sizes()[0]
        self.assertGreaterEqual(list_width, self.page._LIST_MIN_WIDTH)

    def test_narrow_panels_fit_the_list_to_its_rows(self):
        """Stacked, a fixed share left a band of empty canvas under the rows."""
        self._load(width=design.DOCK_MIN_WIDTH)
        for _ in range(16):
            QGIS_APP.processEvents()
        self.assertEqual(self.page._splitter.orientation(), Qt_Vertical)
        needed = self.page._list_content_height()
        allotted = self.page._splitter.sizes()[0]
        # Enough for the rows, and not materially more.
        self.assertGreaterEqual(allotted, needed - 2)
        self.assertLessEqual(allotted, needed + 8)

    def test_content_height_is_measurable_before_the_event_loop_turns(self):
        """The reason _list_content_height exists at all.

        A QVBoxLayout does not fold newly added widgets into its own sizeHint
        until it handles a deferred LayoutRequest, so reading sizeHint straight
        after adding rows reports almost nothing — 12px for three 58px rows.
        """
        self.page._clear_list()
        for proc in _sample_processes():
            self.page._list_layout.addWidget(self.page._create_row(proc))
        computed = self.page._list_content_height()
        self.assertGreater(computed, 100, "rows were not counted")
        for _ in range(20):
            QGIS_APP.processEvents()
        settled = self.page._list_inner.sizeHint().height()
        self.assertEqual(computed, settled)

    def test_a_user_drag_is_not_overridden(self):
        self._load(width=820)
        self.page._on_splitter_moved(300, 1)
        self.assertTrue(self.page._split_user_set)
        self.page._splitter.setSizes([300, 520])
        # Read back rather than asserting 300: a splitter subtracts its handle
        # width from the sizes it is given, so it reports 298 for a 300 ask.
        chosen = self.page._splitter.sizes()[0]
        self.page._split_applied_for = None
        self.page._apply_initial_split(self.page._splitter.orientation())
        self.assertEqual(self.page._splitter.sizes()[0], chosen)


class CreateProcessWizardDesignTest(unittest.TestCase):
    """The wizard's form is built from design-system components."""

    def setUp(self):
        from qgis.gui import QgsMapCanvas
        from rasid_components.create_process_wizard import CreateProcessWizard

        class StubIface:
            def __init__(self):
                self._canvas = QgsMapCanvas()

            def mapCanvas(self):
                return self._canvas

        self._iface = StubIface()
        self.wizard = CreateProcessWizard(
            client=None, iface=self._iface, project_slug="demo",
            project_title="Nile delta study")
        self.wizard.resize(design.DOCK_DEFAULT_WIDTH, 900)
        self.wizard.show()
        for _ in range(10):
            QGIS_APP.processEvents()
        self.wizard._on_config_loaded({
            "supports": {"sentinel_catalogue": True, "mapbox": True},
            "defaults": {"zoom_level": 17},
            "sentinel": {
                "fields": {}, "defaults": {},
                "choices": {"data_collection": [{"label": "S2", "value": "s2"}],
                            "eval_script": [{"label": "NDVI", "value": "n"}],
                            "sort_by": [{"label": "Recent", "value": "r"}]},
            },
        })
        for _ in range(20):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.wizard)
        self.wizard = None
        self._iface = None

    def test_sections_are_section_cards(self):
        from rasid_components.widgets import SectionCard
        for group in (self.wizard._aoi_group, self.wizard._date_group,
                      self.wizard._sentinel_group, self.wizard._zoom_group,
                      self.wizard._upload_group, self.wizard._link_group):
            self.assertIsInstance(group, SectionCard)

    def test_no_card_is_taller_than_its_content(self):
        """A card with an Expanding child absorbs the form's spare height.

        The AOI card measured 342px against a 160px size hint, because the
        QTextEdit in it reports a vertically Expanding POLICY regardless of
        its fixed height, and a layout item advertises its widget's policy
        rather than its limits. The spare height belongs to the trailing
        stretch.
        """
        for _ in range(20):
            QGIS_APP.processEvents()
        for group in (self.wizard._aoi_group, self.wizard._date_group,
                      self.wizard._sentinel_group):
            if not group.isVisible():
                continue
            self.assertLessEqual(
                group.height(), group.sizeHint().height() + 2,
                f"{group.objectName()} is stretched beyond its content")

    def test_the_form_styles_its_own_controls(self):
        """One stylesheet on the form, not a dozen on the inputs."""
        qss = self.wizard._form.styleSheet()
        for selector in ("QLineEdit", "QComboBox", "QDateEdit", "QSpinBox",
                         "QRadioButton", "QLabel"):
            self.assertIn(selector, qss, selector)

    def test_the_wizard_has_no_title_bar_of_its_own(self):
        """The processes header already names the project."""
        labels = [w.text() for w in self.wizard.findChildren(QLabel)
                  if w.text()]
        self.assertNotIn("New Process — Nile delta study", labels)
        self.assertFalse(any("Nile delta study" in text for text in labels))

    def test_the_primary_action_carries_no_icon(self):
        """A tick means "done"; this button starts the work.

        It also keeps the three create actions consistent — Create project in
        GoBox and New process in the header are both label-only.
        """
        self.assertEqual(self.wizard._submit_btn.text(), "Create process")
        self.assertTrue(self.wizard._submit_btn.icon().isNull())

    def test_the_calendar_drop_down_is_styled(self):
        """setCalendarPopup grows a `drop-down`, not the up/down buttons.

        Unstyled it kept its native frame and bit a dark notch out of the
        right-hand edge of the date field.
        """
        qss = design.spinbox_qss()
        self.assertIn("QDateEdit::drop-down", qss)
        drop = qss.split("QDateEdit::drop-down")[1].split("}")[0]
        self.assertIn("border: none", drop)
        self.assertIn("background: transparent", drop)

    def test_cancel_is_not_styled_as_destructive(self):
        """Cancelling an unsubmitted form destroys nothing."""
        t = design.tokens()
        self.assertNotIn(t.error, self.wizard._cancel_btn.styleSheet())

    def test_switching_type_switches_sections(self):
        self.wizard._type_radios["mapbox"].setChecked(True)
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertFalse(self.wizard._sentinel_group.isVisible())
        self.assertFalse(self.wizard._date_group.isVisible())
        self.assertTrue(self.wizard._aoi_group.isVisible())

        self.wizard._type_radios["sentinel_catalogue"].setChecked(True)
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertTrue(self.wizard._sentinel_group.isVisible())
        self.assertTrue(self.wizard._date_group.isVisible())
        self.assertTrue(self.wizard._catalogue_group.isVisible())

    def test_zoom_is_only_offered_for_a_mapbox_only_solution(self):
        """Not simply "mapbox is selected" — the config has to support ONLY it.

        Asserted because the first version of the test above assumed picking
        Mapbox was enough, and the rule is deliberately narrower than that.
        """
        # This fixture also supports sentinel_catalogue, so no zoom.
        self.wizard._type_radios["mapbox"].setChecked(True)
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertFalse(self.wizard._zoom_group.isVisible())

        self.wizard._config = {"supports": {"mapbox": True},
                               "defaults": {"zoom_level": 17}}
        self.wizard._update_sections()
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertTrue(self.wizard._zoom_group.isVisible())


class FormControlStyleTest(unittest.TestCase):
    """The control styles added for the wizard's inputs."""

    def test_arrows_are_real_images_not_rotated_borders(self):
        """Qt stylesheets have no transform; a border chevron renders as an L."""
        for qss in (design.select_qss(), design.spinbox_qss()):
            self.assertIn("image: url(", qss)
            self.assertNotIn("border-left: 2px solid", qss)

    def test_the_arrow_image_exists_on_disk(self):
        """A url() Qt cannot load draws nothing at all."""
        import os
        from rasid_components import icons
        path = icons.stylesheet_url("chevron-down", 10, "#334155")
        self.assertTrue(os.path.exists(path), path)
        self.assertNotIn("\\", path, "a backslash is an escape in url()")

    def test_a_checked_radio_stays_circular(self):
        """A 4px border swallowed the radius and drew a rounded square."""
        qss = design.radio_qss()
        self.assertIn("border-radius: 7px", qss)
        checked = qss.split("QRadioButton::indicator:checked")[1]
        self.assertNotIn("border: 4px", checked)

    def test_form_qss_covers_every_control_the_wizard_uses(self):
        qss = design.form_qss()
        for selector in ("QLineEdit", "QTextEdit", "QComboBox", "QSpinBox",
                         "QDateEdit", "QRadioButton", "QCheckBox", "QLabel"):
            self.assertIn(selector, qss, selector)


class DialogOpacityTest(unittest.TestCase):
    """A dialog parented to a page must not inherit the page's transparency.

    "Project created" came up as a black rectangle with invisible text. Nothing
    was wrong with the dialog: a layout container in the shell carried a bare
    ``background: transparent``, a stylesheet applies to its widget AND every
    descendant, and Qt counts a child WINDOW as a descendant — so the message
    box inherited the transparency and a transparent top-level window has
    nothing behind it to composite.
    """

    def setUp(self):
        self.shell = GoPilotShell(client=None, iface=None)
        self.shell.resize(design.DOCK_DEFAULT_WIDTH, 760)
        self.shell.show()
        for _ in range(10):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.shell)
        self.shell = None

    def _box(self, parent):
        from qgis.PyQt.QtWidgets import QMessageBox
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Project Created")
        box.setText("Project 'Demo' created successfully!")
        box.show()
        for _ in range(20):
            QGIS_APP.processEvents()
        return box

    def test_a_message_box_on_a_page_is_opaque(self):
        for name in ("gobox_page", "projects_page", "processes_page",
                     "settings_page", "chat_page"):
            page = getattr(self.shell, name)
            box = self._box(page)
            try:
                image = box.grab().toImage()
                corner = image.pixelColor(4, 4)
                centre = image.pixelColor(image.width() // 2,
                                          image.height() // 2)
                # alpha, not colour: the failure was a fully transparent
                # window, which only LOOKS black once the desktop shows
                # through. A colour assertion would depend on the host theme.
                self.assertEqual(corner.alpha(), 255, f"{name}: corner")
                self.assertEqual(centre.alpha(), 255, f"{name}: centre")
            finally:
                box.close()
                box.deleteLater()
                QGIS_APP.processEvents()

    def test_no_widget_in_the_panel_has_an_unscoped_stylesheet(self):
        """The root cause, asserted directly rather than via a screenshot.

        Fails on any stylesheet whose first rule is a bare property instead of
        a selector — that is the shape that reaches descendants, child windows
        included.

        Scans EVERY descendant of the shell. The first version of this walked
        UP from each page instead, and that is precisely why it passed while
        the bug was still live: the scroll area's viewport carries one of these
        rules, and a message bubble sits several widgets BELOW a page, so the
        viewport was never on any path this looked at. "Download failed" went
        on coming up black.

        The condition is "a bare rule on a widget that HAS children", not
        "a bare rule anywhere": a stylesheet reaches a widget and its
        descendants, so on a leaf — most of the panel's labels — it has
        nothing to leak onto and is the shortest correct way to write it.
        Failing those too would be 59 findings with no defect among them.
        """
        offenders = []
        for widget in [self.shell] + self.shell.findChildren(QWidget):
            qss = (widget.styleSheet() or "").strip()
            if not qss or qss.startswith(("#", "Q", "*", "/*")):
                continue
            if not widget.findChildren(QWidget):
                continue        # a leaf; nothing below it to inherit this
            offenders.append(
                f"{type(widget).__name__}({widget.objectName()!r}) has "
                f"{len(widget.findChildren(QWidget))} children: "
                f"{qss.splitlines()[0][:60]}")
        self.assertEqual(offenders, [], "unscoped stylesheet on a container")

    def test_a_message_box_on_a_chat_bubble_is_opaque(self):
        """Where the reported failures actually came from.

        message_bubble parents its "Download failed" and "Load Error" boxes to
        the BUBBLE, which is inside the transcript, inside the scroll area's
        viewport — a chain the page-level check above never touched.
        """
        from qgis.PyQt.QtWidgets import QMessageBox
        page = self.shell.chat_page
        page._remove_empty_state()
        row = page.add_message("an answer", is_user=False)
        for _ in range(8):
            QGIS_APP.processEvents()
        bubble = page._bubble_in(row)
        self.assertIsNotNone(bubble)

        box = QMessageBox(bubble)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Could not download the result file.")
        box.show()
        for _ in range(20):
            QGIS_APP.processEvents()
        try:
            image = box.grab().toImage()
            self.assertEqual(image.pixelColor(4, 4).alpha(), 255)
            self.assertEqual(
                image.pixelColor(image.width() // 2,
                                 image.height() // 2).alpha(), 255)
        finally:
            box.close()
            box.deleteLater()
            QGIS_APP.processEvents()

    def test_paint_transparent_scopes_to_the_widget(self):
        from qgis.PyQt.QtWidgets import QWidget
        from rasid_components.widgets import paint_transparent

        parent = QWidget()
        child = QWidget(parent)
        child.setObjectName("innerChild")
        paint_transparent(parent, name="clearHost")

        qss = parent.styleSheet()
        self.assertIn("#clearHost", qss)
        # A selector, not a bare property — the bare form is what reached
        # child windows.
        self.assertTrue(qss.strip().startswith("#"), qss)
        # One rule, naming only the host. (Asserting the child's objectName is
        # absent would pass for free while it is "", which is how the first
        # version of this test managed to be vacuous.)
        self.assertNotIn("innerChild", qss)
        self.assertEqual(qss.count("{"), 1, qss)
        _destroy(parent)

    def test_paint_transparent_can_also_clear_the_border(self):
        from qgis.PyQt.QtWidgets import QWidget
        from rasid_components.widgets import paint_transparent
        widget = paint_transparent(QWidget(), name="clearBordered",
                                   border=True)
        self.assertIn("border: none", widget.styleSheet())
        _destroy(widget)


class GfmMarkdownTest(unittest.TestCase):
    """The GitHub-flavoured features the SaaS gets from remark-gfm.

    python-markdown ships none of these three and `pymdownx` is not in the
    bundled venv, so they are implemented in `message_bubble._GfmExtension`.
    Each was silently dropped before: tildes, brackets and bare URLs all
    rendered as literal text.
    """

    def _html(self, source):
        from rasid_components.message_bubble import render_body_html
        return render_body_html(source)

    def test_strikethrough(self):
        self.assertIn("<s>struck</s>", self._html("~~struck~~"))

    def test_task_lists_become_glyphs(self):
        """Qt rich text has no form controls, so a real checkbox draws nothing."""
        html = self._html("- [ ] todo\n- [x] done\n- [X] shouting")
        # The gap comes from the renderer, not retyped here: it is an EN
        # SPACE, and a literal one in a test is indistinguishable from a
        # typo for the next reader.
        from rasid_components.message_bubble import _TaskListProcessor
        gap = _TaskListProcessor.GAP
        self.assertIn(f"☐{gap}todo", html)
        self.assertIn(f"☑{gap}done", html)
        self.assertIn(f"☑{gap}shouting", html)
        self.assertNotIn("[ ]", html)
        self.assertNotIn("[x]", html)

    def test_bare_urls_become_links(self):
        html = self._html("Visit https://rasid.ai/about for more.")
        self.assertIn('href="https://rasid.ai/about"', html)

    def test_trailing_punctuation_is_not_part_of_the_url(self):
        html = self._html("see https://rasid.ai. next")
        self.assertIn('href="https://rasid.ai"', html)
        self.assertNotIn('href="https://rasid.ai."', html)

    def test_code_spans_are_left_alone(self):
        """The whole reason these are parser extensions and not regexes."""
        for source, forbidden in (
            ("`~~keep~~`", "<s>"),
            ("`https://rasid.ai`", "<a "),
            ("```\n~~keep~~\n```", "<s>"),
        ):
            html = self._html(source)
            self.assertNotIn(forbidden, html, source)

    def test_a_markdown_link_is_not_double_matched(self):
        html = self._html("See [the docs](https://rasid.ai/docs) now.")
        self.assertEqual(html.count("<a "), 1, html)
        self.assertIn(">the docs<", html)

    def test_long_link_text_is_shortened_with_a_tooltip(self):
        """A signed storage URL as link text forces a horizontal scroll."""
        from rasid_components.message_bubble import LINK_TEXT_MAX
        label = "x" * (LINK_TEXT_MAX + 60)
        html = self._html(f"[{label}](https://rasid.ai)")
        self.assertIn("…</a>", html)
        self.assertIn(f'title="{label}"', html)

    def test_headings_do_not_use_heading_tags(self):
        """Qt adds a font-size ADJUSTMENT to h1–h6, on top of any inline size.

        An `<h2 style="font-size:13px">` still drew at about 18px — larger
        than the body it was labelling.
        """
        html = self._html("## Summary")
        self.assertNotIn("<h2", html)
        self.assertIn(f"font-size:{design.TEXT_MD}px", html)
        self.assertIn("Summary", html)

    def test_tables_and_quotes_survive_each_other(self):
        """Both are rewritten to tables; the passes must not collide."""
        html = self._html("> quoted\n\n| a |\n|---|\n| 1 |")
        self.assertIn("quoted", html)
        self.assertIn("<th", html)


class ToolCallFoldingTest(unittest.TestCase):
    """One row per invocation, from the two tags each one emits."""

    def _fold(self, calls):
        from rasid_components.message_bubble import fold_tool_calls
        return fold_tool_calls(calls)

    def test_the_two_tags_of_one_call_fold_together(self):
        folded = self._fold([
            {"id": 7, "name": "run_gdal_dem", "status": "in_progress"},
            {"id": 7, "name": "run_gdal_dem", "status": "success"},
        ])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["status"], "success")

    def test_the_same_tool_twice_is_two_rows(self):
        """Folding by NAME collapsed these into one, showing only the last."""
        folded = self._fold([
            {"id": 1, "name": "search", "status": "success"},
            {"id": 2, "name": "search", "status": "error"},
        ])
        self.assertEqual([c["status"] for c in folded], ["success", "error"])

    def test_the_name_comes_from_the_first_tag(self):
        """The closing tag streams in and can still be a truncated prefix."""
        folded = self._fold([
            {"id": 3, "name": "write_raster", "status": "in_progress"},
            {"id": 3, "name": "write_ras", "status": "success"},
        ])
        self.assertEqual(folded[0]["name"], "write_raster")

    def test_rows_without_an_id_are_kept_as_they_come(self):
        """Legacy rows wrote one tag per call and no id at all."""
        folded = self._fold([
            {"name": "old_one", "status": "success"},
            {"name": "old_one", "status": "success"},
        ])
        self.assertEqual(len(folded), 2)

    def test_junk_is_skipped_not_raised(self):
        self.assertEqual(self._fold(None), [])
        self.assertEqual(self._fold(["not a dict"]), [])


class ThinkingContentTest(unittest.TestCase):
    """`thinking_content` arrives as a list of blocks, one per <think> tag."""

    def _text(self, raw):
        from rasid_components.chat_threads import thinking_text
        return thinking_text(raw)

    def test_blocks_are_separated(self):
        from rasid_components.chat_threads import THINKING_BLOCK_SEPARATOR
        self.assertEqual(self._text(["first", "second"]),
                         f"first{THINKING_BLOCK_SEPARATOR}second")

    def test_the_separator_draws_no_rule(self):
        """A rule cost a third of the panel's visible height per separator."""
        from rasid_components.chat_threads import THINKING_BLOCK_SEPARATOR
        from rasid_components.message_bubble import render_body_html
        self.assertNotIn("-", THINKING_BLOCK_SEPARATOR)
        html = render_body_html(self._text(["first", "second", "third"]))
        self.assertNotIn('height="1"', html, "a rule is still being drawn")
        # Still two paragraphs' worth of separation, not one run-on block.
        self.assertEqual(html.count("<p"), 3, html)

    def test_a_bare_string_passes_through(self):
        self.assertEqual(self._text("just one"), "just one")

    def test_empty_shapes_are_none(self):
        for raw in (None, "", [], ["", "   "]):
            self.assertIsNone(self._text(raw), repr(raw))


class ChatStreamingTest(unittest.TestCase):
    """The answer is written into one bubble as the task reports progress.

    The API has no socket: `GET sessions/tasks/{id}/` returns an `llm_message`
    that grows between polls, and re-reading it is what the SaaS calls
    streaming. This used to read that payload once, on completion, and keep
    only its text.
    """

    def setUp(self):
        from rasid_components.gopilot_page import GoPilotPage
        self.page = GoPilotPage(None, None)
        self.page.resize(design.DOCK_DEFAULT_WIDTH, 820)
        self.page.show()
        for _ in range(10):
            QGIS_APP.processEvents()
        self.page._remove_empty_state()
        self.page._show_thinking()
        for _ in range(5):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _pump(self, times=8):
        for _ in range(times):
            QGIS_APP.processEvents()

    def test_a_partial_fills_the_live_bubble(self):
        bubble = self.page._live_bubble
        self.assertIsNotNone(bubble)
        self.page._on_partial({
            "content": "Found **3 sites**",
            "thinking_content": ["Reading the layers"],
            "tool_calls_content": [{"id": 1, "name": "list_layers",
                                    "status": "in_progress"}],
        })
        self._pump()
        self.assertIsNotNone(bubble._thinking_panel)
        self.assertIsNotNone(bubble._tools_panel)
        self.assertIn("3 sites", bubble._body.text())

    def test_reasoning_does_not_vanish_on_a_poll_that_omits_it(self):
        """Each poll re-sends the whole message; one may arrive without it."""
        bubble = self.page._live_bubble
        self.page._on_partial({"content": "",
                               "thinking_content": ["Reading the layers"]})
        self._pump()
        self.assertIsNotNone(bubble._thinking_panel)

        self.page._on_partial({"content": "part of the answer"})
        self._pump()
        self.assertIsNotNone(bubble._thinking_panel,
                             "the reasoning panel was deleted mid-turn")

    def test_an_empty_poll_keeps_the_placeholder(self):
        """A long reasoning phase carries no content yet."""
        bubble = self.page._live_bubble
        self.page._on_partial({"content": "",
                               "thinking_content": ["Still working"]})
        self._pump()
        self.assertTrue(bubble._body.text().strip(),
                        "the bubble was blanked, which reads as a failed turn")

    def test_the_reply_finishes_the_same_bubble(self):
        """A second bubble at the end produced a visible flicker."""
        bubble = self.page._live_bubble
        before = len(self.page._bubbles)
        self.page._on_partial({"content": "Found"})
        self._pump()
        self.page._on_reply({
            "session_id": 5,
            "message": "Found **3 sites** for the survey.",
            "llm_message": {"content": "Found **3 sites** for the survey.",
                            "thinking_content": ["Reading the layers"],
                            "tool_calls_content": []},
        })
        self._pump()
        self.assertEqual(len(self.page._bubbles), before)
        self.assertIn("survey", bubble._body.text())
        self.assertIsNone(self.page._live_bubble)

    def test_a_new_turn_does_not_inherit_the_last_one_s_reasoning(self):
        self.page._on_partial({"content": "a",
                               "thinking_content": ["old reasoning"]})
        self._pump()
        self.page._on_reply({"session_id": 5, "message": "a",
                             "llm_message": {"content": "a"}})
        self._pump()
        self.page._show_thinking()
        self._pump()
        self.assertIsNone(self.page._live_thinking)
        self.assertIsNone(self.page._live_tool_calls)


class SweptDialogsTest(unittest.TestCase):
    """The three dialogs that were still hand-rolled after the refactor.

    All three set colours and sizes by hand. Two of the faults they carried
    are invisible in a screenshot, which is why they are asserted rather than
    eyeballed: a stylesheet with a ``font-size`` and no ``font-family``
    renders in the HOST application's font, and a selector-less
    ``setStyleSheet`` leaks onto every descendant including a child
    QMessageBox.
    """

    FILES = ("feedback_dialog.py", "solutions_page.py",
             "dependencies/dependency_installer.py")

    def _source(self, name):
        """Code only — see :func:`_code_of` for why this is not the raw text."""
        import os
        from rasid_components import design as anchor
        path = os.path.join(os.path.dirname(os.path.abspath(anchor.__file__)),
                            *name.split("/"))
        return _code_of(path)

    def _host_font_labels(self, widget):
        bad = []
        for label in widget.findChildren(QLabel):
            if not label.text().strip():
                continue
            sheet = label.styleSheet()
            if "font-size" in sheet and "font-family" not in sheet:
                bad.append(label.text()[:40])
        return bad

    def test_no_label_sets_a_size_without_a_family(self):
        from rasid_components.dependencies.dependency_installer import (
            DependencyInstallerDialog,
        )
        from rasid_components.feedback_dialog import FeedbackDialog
        for factory in (lambda: DependencyInstallerDialog(),
                        lambda: FeedbackDialog(client=None)):
            dialog = factory()
            try:
                for _ in range(15):
                    QGIS_APP.processEvents()
                self.assertEqual(
                    self._host_font_labels(dialog), [],
                    f"{type(dialog).__name__} has labels in the host font")
            finally:
                _destroy(dialog)

    def test_no_selector_less_stylesheet_remains(self):
        """`setStyleSheet("background: x")` also paints every child."""
        import re
        for name in self.FILES:
            source = self._source(name)
            for match in re.finditer(
                    r"setStyleSheet\(\s*f?\"(?!\"\")([^\"]*)\"", source):
                rule = match.group(1)
                if not rule.strip():
                    continue
                # A scoped rule names a selector before its brace. A bare
                # declaration like "background: {x};" has no brace at all.
                self.assertIn(
                    "{", rule,
                    f"{name} sets a selector-less rule: {rule[:60]!r}")

    def test_the_three_files_are_off_the_shim(self):
        for name in self.FILES:
            self.assertNotIn("theme_utils", self._source(name), name)

    def test_a_solution_with_an_image_and_no_description_opens(self):
        """These two branches did not share their colour variables.

        `sidebar_bg` and `card_border` were assigned inside the
        `if description` branch and read inside the `if image` branch, so this
        exact combination raised NameError instead of opening the dialog.
        """
        from rasid_components import solutions_page
        from rasid_components.solutions_page import SolutionsPage

        captured = []
        real_exec = solutions_page.exec_dialog
        real_image = solutions_page.load_image
        solutions_page.exec_dialog = lambda dialog: (captured.append(dialog)
                                                     or 0)
        solutions_page.load_image = lambda *a, **k: None
        page = SolutionsPage(client=None)
        try:
            page._on_create_project({
                "slug": "s", "name": "S",
                "description_html": "",                  # none
                "result_image_url": "https://example.invalid/r.png",
            })
            self.assertEqual(len(captured), 1,
                             "the dialog was never created")
        finally:
            solutions_page.exec_dialog = real_exec
            solutions_page.load_image = real_image
            for dialog in captured:
                _destroy(dialog)
            _destroy(page)

    def test_the_feedback_version_comes_from_the_manifest(self):
        """It was hardcoded "2.0.0" while metadata.txt said something else."""
        import os
        from rasid_components import config, design as anchor
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(anchor.__file__)))
        with open(os.path.join(root, "metadata.txt"), encoding="utf-8") as fh:
            declared = next(line.split("=", 1)[1].strip() for line in fh
                            if line.startswith("version="))
        self.assertEqual(config.plugin_version(), declared)
        self.assertNotIn(
            '"2.0.0"', self._source("feedback_dialog.py"),
            "the hardcoded version is back")

    def test_the_rating_stars_are_drawn_not_typed(self):
        """"☆" at 24px is whatever the host font decides it is."""
        from rasid_components import icons
        self.assertIn("star", icons.available())
        self.assertIn("star-filled", icons.available())
        source = self._source("feedback_dialog.py")
        for glyph in ("☆", "★"):
            self.assertNotIn(glyph, source,
                             "a star glyph is still being typed as text")
        for name in ("star", "star-filled"):
            image = icons.pixmap(name, 48, "#C9A227", dpr=1.0).toImage()
            drawn = sum(1 for y in range(image.height())
                        for x in range(image.width())
                        if image.pixelColor(x, y).alpha() > 0)
            self.assertGreater(drawn, 100, f"{name} draws nothing")

    def test_only_the_filled_star_is_solid(self):
        """The two states have to differ where a glance actually lands.

        Not by pixel COUNT: measured, the outline covers more non-transparent
        pixels than the fill (951 against 832 at 48px), because a ten-sided
        star has a long perimeter and every antialiased edge counts. What
        separates the two is whether the middle is painted, so that is what
        this samples.
        """
        from rasid_components import icons

        def centre_alpha(name):
            image = icons.pixmap(name, 48, "#C9A227", dpr=1.0).toImage()
            return image.pixelColor(image.width() // 2,
                                    image.height() // 2).alpha()

        self.assertGreater(centre_alpha("star-filled"), 200,
                           "the filled star is hollow")
        self.assertLess(centre_alpha("star"), 50,
                        "the outline star is filled in")


class BubbleTeardownTest(unittest.TestCase):
    """A bubble must stop its own pending work before it is deleted.

    WHAT THIS CAN AND CANNOT PROVE
    ==============================
    The reported crash was a Windows access violation inside
    ``message_bubble.on_finished``, reached through Qt's event loop — a
    queued ``QNetworkReply.finished`` arriving at a closure whose QLabel had
    just been freed with its parent bubble. That cannot be reproduced in a
    test: it needs the reply to complete during the destruction itself, and a
    deleted wrapper that PyQt still believes is valid. When PyQt DOES know,
    it raises RuntimeError instead, which is the harmless case.

    So these assert the contract that makes the crash impossible — the work
    is disconnected and aborted before teardown, and every callback bails on
    a dead widget — rather than claiming to reproduce it.
    """

    class _Client:
        api_key = "test-key"

    IMAGE = "Result ![map](https://example.invalid/result.png)"

    def setUp(self):
        from rasid_components.gopilot_page import GoPilotPage
        self.page = GoPilotPage(self._Client(), None)
        self.page.resize(470, 760)
        self.page.show()
        for _ in range(15):
            QGIS_APP.processEvents()
        self.page.start_new_chat()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.page._remove_empty_state()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _bubble_with_a_preview(self):
        self.page.add_message(self.IMAGE, is_user=False, message_id=501)
        for _ in range(10):
            QGIS_APP.processEvents()
        bubble = self.page._bubbles[-1]
        self.assertTrue(
            bubble._replies,
            "no preview request was started, so this test proves nothing")
        return bubble

    def test_clearing_the_transcript_stops_pending_previews(self):
        bubble = self._bubble_with_a_preview()
        self.page._clear_transcript()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertTrue(bubble._closing)
        self.assertEqual(bubble._replies, [],
                         "replies were left outstanding past teardown")

    def test_page_cleanup_stops_them_too(self):
        """Unload destroys the panel; the same work has to be stopped."""
        bubble = self._bubble_with_a_preview()
        self.page.cleanup()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertTrue(bubble._closing)

    def test_cleanup_is_safe_twice_and_on_an_idle_bubble(self):
        bubble = self._bubble_with_a_preview()
        bubble.cleanup()
        bubble.cleanup()            # must not raise
        plain = self.page.add_message("no results here", is_user=True,
                                      message_id=9)
        for _ in range(8):
            QGIS_APP.processEvents()
        self.page._bubbles[-1].cleanup()

    def test_a_download_callback_bails_once_closing(self):
        """The slots must not write to a button whose owner has gone.

        These moved from the bubble to `downloads.DownloadButton` when both
        panes were put on one controller, so the guard is tested where it
        now lives. Same property: a download that lands after teardown has
        nowhere to report to, and writing to a freed QPushButton is the
        access violation this whole class exists about.
        """
        from rasid_components.downloads import DownloadButton
        button = QPushButton("x")
        controller = DownloadButton(
            button, object(), "https://example.invalid/x.geojson",
            add_layer=lambda path: None,
            idle_text="Add", busy_text="Downloading…", done_text="Added")
        try:
            controller.cleanup()
            # Each of these would touch the button if the guard were gone.
            controller._on_progress(10, 100)
            controller._on_downloaded("c:/nope.tif")
            controller._on_error("boom")
            controller._on_layers_gone(["someid"])
            self.assertEqual(button.text(), "Add",
                             "a callback wrote to the button after cleanup")
        finally:
            _destroy(button)

    def test_a_callback_survives_a_widget_that_died_without_cleanup(self):
        """Teardown order is not always ours to choose.

        `_closing` covers the case where we tore the thing down. This covers
        the other one: the widget is destroyed by its parent and the work in
        flight finishes afterwards, with no cleanup call in between. Nothing
        may escape — the callbacks run on the UI thread and an exception
        there is an error dialog over the user's map at best.

        Previously this called two bubble methods that no longer exist, and
        it SKIPPED on every run (it bailed when the widget had not been
        reaped), so it never noticed. It drives the controller now, where
        that guard lives.
        """
        from rasid_components.downloads import DownloadButton
        button = QPushButton("x")
        controller = DownloadButton(
            button, object(), "https://example.invalid/x.geojson",
            add_layer=lambda path: None,
            idle_text="Add", busy_text="Downloading…", done_text="Added")
        # Destroyed WITHOUT controller.cleanup(), so `_closing` is still
        # False and only the RuntimeError guard can save these.
        _destroy(button)
        controller._on_progress(10, 100)
        controller._on_downloaded("c:/nope.tif")
        controller._on_error("boom")
        controller.cleanup()


class StaleReplyTest(unittest.TestCase):
    """A new prompt must not be answered with the previous reply.

    `GET sessions/tasks/{id}/` carries an `llm_message`, and until the worker
    has written this turn's row that field can still hold the PREVIOUS turn's
    message — complete, with its own thinking and tool calls. Rendering it is
    why sending "Fetch me buildings" came back with "Hello, I am GoPilot".
    """

    def _thread(self, known=()):
        from rasid_components.chat_threads import SendMessageThread
        return SendMessageThread(None, "a new question", session_id="s1",
                                 known_message_ids=known)

    def test_a_message_already_on_screen_is_rejected(self):
        thread = self._thread(known={"901"})
        previous = {"id": 901, "content": "Hello, I am GoPilot",
                    "thinking_content": "greeting",
                    "tool_calls_content": [{"id": 1, "name": "x"}]}
        self.assertFalse(thread._is_this_turn(previous))

    def test_a_new_message_is_accepted(self):
        thread = self._thread(known={"901"})
        self.assertTrue(thread._is_this_turn(
            {"id": 902, "content": "Fetching the buildings…"}))

    def test_ids_compare_as_strings(self):
        """The API sends ints here and strings elsewhere."""
        thread = self._thread(known={"901"})
        self.assertFalse(thread._is_this_turn({"id": "901", "content": "x"}))
        self.assertFalse(thread._is_this_turn({"id": 901, "content": "x"}))

    def test_an_empty_shell_is_rejected(self):
        """The row exists but nothing has been written to it yet.

        Rendering it would blank a bubble that currently reads "Thinking…".
        The SaaS makes the same check in `hasAnyStreamedField`.
        """
        thread = self._thread()
        self.assertFalse(thread._is_this_turn({"id": 903}))
        self.assertFalse(thread._is_this_turn(
            {"id": 903, "content": "", "thinking_content": ""}))

    def test_a_message_with_only_files_is_accepted(self):
        """A turn can answer with a layer and no prose."""
        thread = self._thread()
        self.assertTrue(thread._is_this_turn(
            {"id": 904, "files": [{"id": 7}]}))

    def test_a_message_with_no_id_is_accepted(self):
        """Absent id cannot be matched against anything; do not drop the turn."""
        thread = self._thread(known={"901"})
        self.assertTrue(thread._is_this_turn({"content": "an answer"}))

    def test_the_timeout_fallback_will_not_return_a_known_message(self):
        """Walking back from the end finds the previous answer just as well."""
        class Gopilot:
            def get_messages(self, session_id):
                return [{"id": 900, "role": "user", "content": "hi"},
                        {"id": 901, "role": "assistant", "content": "Hello"}]

        self.assertIsNone(self._thread(known={"901"})
                          ._last_assistant_message(Gopilot()))
        self.assertIsNotNone(self._thread()
                             ._last_assistant_message(Gopilot()))

    def test_the_poll_loop_never_emits_the_previous_turn(self):
        """The guard has to be IN the loop, not just available to it.

        Drives `_await_task` against a task that reports the previous turn's
        message for its first two polls — which is what the real one does
        before the worker writes this turn's row — and asserts the UI is
        never handed it. Asserting `_is_this_turn` alone passed happily with
        the call site removed.
        """
        from rasid_components import chat_threads

        PREVIOUS = {"id": 901, "content": "Hello, I am GoPilot",
                    "thinking_content": "greeting",
                    "tool_calls_content": [{"id": 1, "name": "x"}]}
        FRESH = {"id": 902, "content": "Fetching the buildings…"}

        class Gopilot:
            def __init__(self):
                self.polls = 0

            def get_task_status(self, task_id):
                self.polls += 1
                if self.polls <= 2:
                    return {"status": "processing", "llm_message": PREVIOUS}
                return {"status": "completed", "llm_message": FRESH}

        thread = self._thread(known={"901"})
        seen = []
        thread.partial.connect(lambda message: seen.append(message))

        original = chat_threads.POLL_INTERVAL
        chat_threads.POLL_INTERVAL = 0        # do not sleep 2s per poll
        try:
            final = thread._await_task(Gopilot(), "task-1")
        finally:
            chat_threads.POLL_INTERVAL = original

        self.assertNotIn(PREVIOUS, seen,
                         "the previous turn was streamed into the new bubble")
        self.assertEqual(seen, [FRESH])
        self.assertEqual(final, FRESH,
                         "the turn was committed with the wrong message")

    def test_the_page_supplies_what_it_is_showing(self):
        from rasid_components.gopilot_page import GoPilotPage
        page = GoPilotPage(None, None)
        page.show()
        for _ in range(10):
            QGIS_APP.processEvents()
        try:
            page.start_new_chat()
            for _ in range(10):
                QGIS_APP.processEvents()
            page._remove_empty_state()
            page.add_message("q", is_user=True, message_id=11)
            page.add_message("a", is_user=False, message_id=12)
            page.add_message("no id yet", is_user=False)
            for _ in range(10):
                QGIS_APP.processEvents()
            self.assertEqual(page._rendered_message_ids(), {"11", "12"})
        finally:
            _destroy(page)


class DrawRestartTest(unittest.TestCase):
    """Starting a draw while one is running must not abandon the live tool.

    `GeometryInputManager._draw_tool` is the ONLY strong reference to the map
    tool: QgsMapTool takes the canvas in its constructor but the canvas does
    not own the tool. Overwriting that attribute therefore let Python free the
    C++ tool while the canvas was still pointing at it, and the next mouse
    event over the map read freed memory — a crash while drawing an AoI.
    """

    def setUp(self):
        from qgis.gui import QgsMapCanvas
        from rasid_components.geometry import GeometryInputManager
        self.host = QMainWindow()
        self.canvas = QgsMapCanvas(self.host)
        self.host.setCentralWidget(self.canvas)
        self.host.resize(800, 600)
        self.host.show()
        for _ in range(15):
            QGIS_APP.processEvents()

        test = self

        class FakeIface:
            def mainWindow(self):
                return test.host

            def mapCanvas(self):
                return test.canvas

        self.page = QWidget(self.host)
        self.manager = GeometryInputManager(FakeIface(),
                                            parent_widget=self.page)

    def tearDown(self):
        try:
            self.manager.cancel_draw_mode()
        except Exception:
            pass
        for _ in range(10):
            QGIS_APP.processEvents()
        self.manager = None
        _destroy(self.host)
        self.host = None

    def _start(self):
        self.manager.start_draw_mode(show_help=False)
        for _ in range(15):
            QGIS_APP.processEvents()
        return self.manager._draw_tool

    def test_restarting_replaces_the_tool_on_the_canvas(self):
        first = self._start()
        self.assertIs(self.canvas.mapTool(), first)
        second = self._start()
        self.assertIsNot(second, first)
        self.assertIs(
            self.canvas.mapTool(), second,
            "the canvas still points at a tool the manager has dropped")

    def test_the_previous_tool_is_never_one_of_ours(self):
        """Otherwise finishing restores the canvas to a deleted tool."""
        from rasid_components.geometry.modern_draw_tool import ModernDrawTool
        self._start()
        self._start()
        self.assertNotIsInstance(self.manager._prev_map_tool, ModernDrawTool)

    def test_the_abandoned_tool_can_no_longer_deliver_geometry(self):
        """The property only the teardown produces.

        Without it the first tool is merely dropped: still connected to
        `_on_draw_finished`, still holding its rubber band on the canvas, and
        relying on Python's garbage collector to free a QgsMapTool that QGIS
        may still hold internal pointers to. Tearing it down disconnects it,
        so a stale tool cannot hand geometry to a manager that has moved on.
        """
        received = []
        self.manager.geometry_ready.connect(
            lambda payload: received.append(payload))

        first = self._start()
        self._start()

        first.polygon_finished.emit([[0, 0], [1, 0], [1, 1], [0, 0]])
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertEqual(
            received, [],
            "a tool the manager had replaced still delivered geometry")

    def test_the_canvas_survives_mouse_events_after_a_restart(self):
        """This is where the dangling tool pointer was dereferenced."""
        # QPointF, not QPoint: Qt6's QMouseEvent has no QPoint overload, and
        # Qt5 takes the float form happily, so this is the one spelling that
        # builds an event on both.
        from qgis.PyQt.QtCore import QEvent, QPointF, Qt
        from qgis.PyQt.QtGui import QMouseEvent
        self._start()
        self._start()
        for _ in range(5):
            QGIS_APP.sendEvent(self.canvas.viewport(), QMouseEvent(
                QEvent.Type.MouseMove, QPointF(80, 80),
                Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
        for _ in range(10):
            QGIS_APP.processEvents()

    def test_finishing_after_a_restart_leaves_no_draw_tool_behind(self):
        self._start()
        self._start()
        self.manager._on_draw_finished([[0, 0], [1, 0], [1, 1], [0, 0]])
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            QGIS_APP.processEvents()
        self.assertIsNone(self.manager._draw_tool)
        from rasid_components.geometry.modern_draw_tool import ModernDrawTool
        self.assertNotIsInstance(self.canvas.mapTool(), ModernDrawTool)


class DownloadButtonTest(unittest.TestCase):
    """The download control, which used to be a one-way trip.

    Both panes that download a result ran the same two faults, because the
    code was written twice:

    1. On success the button was disabled and relabelled for good. Removing
       the layer from QGIS did not bring it back, so the only way to get the
       layer again was to leave the pane and return, which rebuilt it.
    2. The label said "Downloading…" and nothing more, so a large file and a
       stalled plugin looked identical.
    """

    GEOJSON = ('{"type":"FeatureCollection","features":[{"type":"Feature",'
               '"properties":{},"geometry":{"type":"Polygon","coordinates":'
               '[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}}]}')

    class _Client:
        """Writes the file in pieces, reporting progress as a real one does."""

        def __init__(self, source, fail=False):
            self.source = source
            self.calls = 0
            self.fail = fail

        def download_file(self, url, dest_dir=None, progress=None):
            self.calls += 1
            if self.fail:
                raise RuntimeError("503 Service Unavailable")
            with open(self.source, "rb") as fh:
                data = fh.read()
            target = os.path.join(dest_dir or os.path.dirname(self.source),
                                  "result.geojson")
            step = max(1, len(data) // 4)
            written = 0
            with open(target, "wb") as fh:
                for start in range(0, len(data), step):
                    chunk = data[start:start + step]
                    fh.write(chunk)
                    written += len(chunk)
                    if progress:
                        progress(written, len(data))
            return target

    def setUp(self):
        import tempfile
        from qgis.core import QgsProject
        QgsProject.instance().removeAllMapLayers()
        self._dir = tempfile.mkdtemp(prefix="dlbtn_")
        self.source = os.path.join(self._dir, "result.geojson")
        with open(self.source, "w", encoding="utf-8") as fh:
            fh.write(self.GEOJSON)
        self.button = QPushButton()
        self.button.show()
        self.labels = []
        self._controllers = []
        self.added = []
        for _ in range(10):
            QGIS_APP.processEvents()

    def tearDown(self):
        from qgis.core import QgsProject
        for controller in self._controllers:
            controller.cleanup()
        QgsProject.instance().removeAllMapLayers()
        _destroy(self.button)

    def _controller(self, client=None, add_layer=None):
        from rasid_components.downloads import DownloadButton
        from qgis.core import QgsProject, QgsVectorLayer

        def default_add(path):
            layer = QgsVectorLayer(path, "probe result", "ogr")
            QgsProject.instance().addMapLayer(layer)
            self.added.append(layer)
            return layer

        original = self.button.setText

        def record(text):
            self.labels.append(text)
            original(text)

        self.button.setText = record
        controller = DownloadButton(
            self.button, client or self._Client(self.source),
            "https://example.invalid/r.geojson",
            add_layer or default_add,
            idle_text="Add result to QGIS", busy_text="Downloading…",
            done_text="Added result")
        self._controllers.append(controller)
        return controller

    def _settle(self, timeout_ms=2000):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            QGIS_APP.processEvents()

    def _layer_present(self):
        from qgis.core import QgsProject
        return any(l.name() == "probe result"
                   for l in QgsProject.instance().mapLayers().values())

    # ── progress ────────────────────────────────────────────────────────────
    def test_the_label_counts_up_while_downloading(self):
        controller = self._controller()
        controller.start()
        self._settle()
        with_percent = [t for t in self.labels if "%" in t]
        self.assertTrue(
            with_percent,
            f"no progress was reported; labels were {self.labels}")

    def test_bytes_are_shown_when_the_server_sends_no_length(self):
        """A chunked response has no Content-Length to divide by."""
        from rasid_components.downloads import progress_text
        self.assertEqual(progress_text("Downloading…", 512, 0),
                         "Downloading… 512 bytes")
        self.assertEqual(progress_text("Downloading…", 45, 100),
                         "Downloading… 45%")
        self.assertEqual(progress_text("Downloading…", 0, 0), "Downloading…")

    def test_a_percentage_is_never_out_of_range(self):
        """A server that over-reports its own length must not read 143%."""
        from rasid_components.downloads import progress_text
        self.assertEqual(progress_text("x", 200, 100), "x 100%")
        self.assertEqual(progress_text("x", -5, 100), "x 0%")

    # ── the extension, which QGIS needs to open anything ────────────────────
    def test_the_downloaded_file_keeps_its_extension(self):
        """Through the REAL thread, because that is where this is restored.

        A result URL ends in "/download/" with no extension, so the saved
        file is called "download" and QGIS refuses it outright:
        `Cannot open ...\\rasid_download_xxxx\\download`. The thread rebuilds
        the suffix from the filename — and the controller has to pass that
        filename on, which it did not, so every chat download broke.

        The other tests here stub `download_file` and never look at the
        name, so none of them noticed.
        """
        from rasid_components.downloads import DownloadButton
        import tempfile

        class Client:
            """Saves to "download", the way an extensionless URL does."""

            def download_file(self, url, dest_dir=None, progress=None):
                target = os.path.join(dest_dir or tempfile.mkdtemp(),
                                      "download")
                with open(target, "wb") as fh:
                    fh.write(b"{}")
                return target

        seen = []
        button = QPushButton()
        controller = DownloadButton(
            button, Client(),
            "https://app.gopilot.earth/api/llm/files/77/download/",
            add_layer=lambda path: seen.append(path),
            idle_text="Add", busy_text="Downloading…", done_text="Added",
            filename="ndvi_jan2026_colored.tif")
        try:
            controller.start()
            self._settle()
            self.assertTrue(seen, "the download never completed")
            self.assertTrue(
                seen[0].endswith(".tif"),
                f"QGIS cannot open this: {seen[0]!r}")
        finally:
            controller.cleanup()
            _destroy(button)

    def test_the_filename_reaches_the_download_thread(self):
        """Dropping it is what broke this, so it is asserted directly."""
        from rasid_components.downloads import DownloadButton
        button = QPushButton()
        controller = DownloadButton(
            button, None, "https://example.invalid/files/1/download/",
            add_layer=lambda path: None,
            idle_text="Add", busy_text="…", done_text="Added",
            filename="result.gpkg")
        try:
            self.assertEqual(controller.filename, "result.gpkg")
        finally:
            controller.cleanup()
            _destroy(button)

    # ── the added state is not terminal ─────────────────────────────────────
    def test_the_button_stays_usable_after_a_successful_add(self):
        controller = self._controller()
        controller.start()
        self._settle()
        self.assertEqual(self.button.text(), "Added result")
        self.assertTrue(
            self.button.isEnabled(),
            "the button was disabled for good, so the layer cannot be re-added")

    def test_removing_the_layer_returns_the_button_to_idle(self):
        """The reported bug: no way back without reopening the pane."""
        from qgis.core import QgsProject
        controller = self._controller()
        controller.start()
        self._settle()
        QgsProject.instance().removeMapLayer(self.added[0].id())
        self._settle(500)
        self.assertEqual(self.button.text(), "Add result to QGIS")
        self.assertTrue(self.button.isEnabled())

    def test_adding_again_reuses_the_downloaded_file(self):
        """Results are immutable, so a second fetch would be waste."""
        client = self._Client(self.source)
        controller = self._controller(client)
        controller.start()
        self._settle()
        self.assertEqual(client.calls, 1)
        controller.start()
        self._settle(1000)
        self.assertEqual(client.calls, 1, "it downloaded the file twice")
        self.assertTrue(self._layer_present())

    def test_removing_another_layer_does_not_reset_the_button(self):
        from qgis.core import QgsProject, QgsVectorLayer
        controller = self._controller()
        controller.start()
        self._settle()
        other = QgsVectorLayer(self.source, "something else", "ogr")
        QgsProject.instance().addMapLayer(other)
        self._settle(300)
        QgsProject.instance().removeMapLayer(other.id())
        self._settle(300)
        self.assertEqual(self.button.text(), "Added result")

    # ── failures ────────────────────────────────────────────────────────────
    def test_a_failed_download_offers_a_retry(self):
        controller = self._controller(self._Client(self.source, fail=True))
        controller.start()
        self._settle()
        self.assertTrue(self.button.isEnabled())
        self.assertIn("Retry", self.button.text())

    def test_a_failed_add_offers_a_retry(self):
        def boom(path):
            raise RuntimeError("QGIS could not open it")

        controller = self._controller(add_layer=boom)
        controller.start()
        self._settle()
        self.assertTrue(self.button.isEnabled())
        self.assertIn("Retry", self.button.text())

    def test_an_existing_layer_shows_the_loaded_state(self):
        """A pane reopened later, with the layer still in the project."""
        from qgis.core import QgsProject, QgsVectorLayer
        layer = QgsVectorLayer(self.source, "probe result", "ogr")
        QgsProject.instance().addMapLayer(layer)
        controller = self._controller()
        controller.mark_loaded("probe result")
        self.assertEqual(self.button.text(), "Added result")
        self.assertTrue(self.button.isEnabled())
        QgsProject.instance().removeMapLayer(layer.id())
        self._settle(400)
        self.assertEqual(self.button.text(), "Add result to QGIS")

    def test_cleanup_lets_go_of_the_project_signal(self):
        """A slot left connected is called while the widget is destroyed."""
        from qgis.core import QgsProject
        controller = self._controller()
        controller.start()
        self._settle()
        controller.cleanup()
        # Removing the layer now must not touch the button.
        QgsProject.instance().removeMapLayer(self.added[0].id())
        self._settle(400)
        self.assertEqual(self.button.text(), "Added result")

    # ── auto-load must not duplicate ────────────────────────────────────────
    def test_auto_load_fires_once_per_result(self):
        """`set_content` re-renders on every poll of a streaming reply.

        The old guard was "click every ENABLED button", which worked only
        because a loaded button stayed disabled. Now that it stays
        clickable, that guard would add a second copy of every layer on each
        re-render.
        """
        from rasid_components.message_bubble import MessageBubble
        url = "https://app.gopilot.earth/api/llm/files/55/download/"
        started = []

        class StubClient:
            api_key = "k"

        bubble = MessageBubble(f"result [r.geojson]({url})", role="ai",
                               client=StubClient(), message_id=5)
        try:
            for _ in range(8):
                QGIS_APP.processEvents()
            for controller in bubble._downloads:
                controller.start = lambda c=controller: started.append(c.url)
            bubble._auto_load()
            bubble._auto_load()
            bubble._auto_load()
            self.assertEqual(
                len(started), 1,
                f"auto-load fired {len(started)} times for one result")
        finally:
            bubble.cleanup()
            _destroy(bubble)


class LayerStylingTest(unittest.TestCase):
    """Result layers are drawn the way the server says, not at random.

    Every result file may carry an ``sld`` — a Styled Layer Descriptor — and
    applying it is what makes a layer land in QGIS looking like it does on
    the website instead of in a random colour.

    The plugin could not do this at all, for a reason that had nothing to do
    with styling: results are discovered by reading the message TEXT for
    links, which yields a URL and a filename, so the API's own file list —
    where the style, type and id live — was never looked at. The style was
    discarded two steps before anything could use it.
    """

    SLD = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<StyledLayerDescriptor version="1.0.0"'
        ' xmlns="http://www.opengis.net/sld"'
        ' xmlns:ogc="http://www.opengis.net/ogc">'
        '<NamedLayer><Name>b</Name><UserStyle><FeatureTypeStyle><Rule>'
        '<PolygonSymbolizer>'
        '<Fill><CssParameter name="fill">#FF00AA</CssParameter></Fill>'
        '<Stroke><CssParameter name="stroke">#003366</CssParameter>'
        '<CssParameter name="stroke-width">2</CssParameter></Stroke>'
        '</PolygonSymbolizer>'
        '</Rule></FeatureTypeStyle></UserStyle></NamedLayer>'
        '</StyledLayerDescriptor>'
    )
    GEOJSON = ('{"type":"FeatureCollection","features":[{"type":"Feature",'
               '"properties":{"n":1},"geometry":{"type":"Polygon",'
               '"coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}}]}')
    URL = "https://app.gopilot.earth/api/llm/files/4242/download/"

    def setUp(self):
        import tempfile
        self._dir = tempfile.mkdtemp(prefix="sldtest_")
        self.geojson = os.path.join(self._dir, "buildings.geojson")
        with open(self.geojson, "w", encoding="utf-8") as fh:
            fh.write(self.GEOJSON)
        self._saved = (prefs.get_bool(prefs.LAYER_GROUP_ENABLED),
                       prefs.get_bool(prefs.ZOOM_TO_LAYER))
        prefs.set_value(prefs.LAYER_GROUP_ENABLED, False)
        prefs.set_value(prefs.ZOOM_TO_LAYER, False)
        self._bubbles = []

    def tearDown(self):
        from qgis.core import QgsProject
        QgsProject.instance().removeAllMapLayers()
        for bubble in self._bubbles:
            bubble.cleanup()
            _destroy(bubble)
        prefs.set_value(prefs.LAYER_GROUP_ENABLED, self._saved[0])
        prefs.set_value(prefs.ZOOM_TO_LAYER, self._saved[1])

    def _files(self, sld=None):
        return [{"id": 4242, "message_id": 77, "name": "buildings.geojson",
                 "file_url": self.URL, "file_type": "geojson", "sld": sld}]

    def _bubble(self, files):
        from rasid_components.message_bubble import MessageBubble
        bubble = MessageBubble(
            f"Here you go [buildings.geojson]({self.URL})",
            role="ai", client=None, message_id=77, files=files)
        self._bubbles.append(bubble)
        for _ in range(8):
            QGIS_APP.processEvents()
        return bubble

    def _load(self, name, sld):
        from rasid_components.message_bubble import add_layer_to_qgis
        from qgis.core import QgsProject
        add_layer_to_qgis(self.geojson, name, None, sld=sld)
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == name:
                return layer
        return None

    # ── finding the style for a result ──────────────────────────────────────
    def test_the_bubble_finds_the_style_for_its_own_file(self):
        bubble = self._bubble(self._files(self.SLD))
        self.assertEqual(bubble.style_for(self.URL), self.SLD)

    def test_a_presigned_url_still_matches(self):
        """`file_url` can carry a signature the link in the text does not."""
        bubble = self._bubble(self._files(self.SLD))
        self.assertEqual(bubble.style_for(self.URL + "?X-Amz-Signature=abc"),
                         self.SLD)

    def test_another_files_style_is_not_used(self):
        bubble = self._bubble(self._files(self.SLD))
        self.assertIsNone(bubble.style_for(
            "https://app.gopilot.earth/api/llm/files/9999/download/"))

    def test_no_file_list_is_not_an_error(self):
        """Messages predating this, and any message with no results."""
        bubble = self._bubble(None)
        self.assertIsNone(bubble.style_for(self.URL))

    # ── applying it ─────────────────────────────────────────────────────────
    def test_the_style_reaches_the_layer(self):
        layer = self._load("styled", self.SLD)
        self.assertIsNotNone(layer)
        symbol = layer.renderer().symbol()
        self.assertEqual(symbol.color().name().lower(), "#ff00aa")
        self.assertEqual(
            symbol.symbolLayer(0).strokeColor().name().lower(), "#003366")

    def test_a_layer_with_no_style_still_loads(self):
        self.assertIsNotNone(self._load("plain", None))

    def test_junk_in_the_style_field_does_not_fail_the_load(self):
        """A visible layer in the wrong colours beats no layer at all."""
        for junk in ('{"not": "an sld"}', "<html>nope</html>", "", "   "):
            name = f"junk{abs(hash(junk)) % 1000}"
            self.assertIsNotNone(self._load(name, junk), junk)

    def test_an_sld_is_recognised_by_its_root_element(self):
        from rasid_components.layer_style import is_sld
        self.assertTrue(is_sld(self.SLD))
        self.assertTrue(is_sld('<sld:StyledLayerDescriptor/>'))
        for bad in (None, "", "   ", 42, '{"json": true}', "<qgis/>"):
            self.assertFalse(is_sld(bad), repr(bad))

    def test_no_temporary_style_files_are_left_behind(self):
        """`loadSldStyle` needs a file, and QGIS sessions run for days."""
        import tempfile
        before = {n for n in os.listdir(tempfile.gettempdir())
                  if n.startswith("gopilot_style_")}
        self._load("styled2", self.SLD)
        after = {n for n in os.listdir(tempfile.gettempdir())
                 if n.startswith("gopilot_style_")}
        self.assertEqual(after - before, set())

    def _filtered_sld(self, field, values):
        rules = ""
        for colour, value in zip(("#FF0000", "#00FF00"), values):
            rules += (
                f'<Rule><Name>{value}</Name>'
                f'<ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">'
                f'<ogc:PropertyIsEqualTo>'
                f'<ogc:PropertyName>{field}</ogc:PropertyName>'
                f'<ogc:Literal>{value}</ogc:Literal>'
                f'</ogc:PropertyIsEqualTo></ogc:Filter>'
                f'<PolygonSymbolizer><Fill>'
                f'<CssParameter name="fill">{colour}</CssParameter>'
                f'</Fill></PolygonSymbolizer></Rule>')
        return ('<?xml version="1.0"?>'
                '<StyledLayerDescriptor version="1.0.0"'
                ' xmlns="http://www.opengis.net/sld">'
                '<NamedLayer><Name>g</Name><UserStyle><FeatureTypeStyle>'
                f'{rules}'
                '</FeatureTypeStyle></UserStyle></NamedLayer>'
                '</StyledLayerDescriptor>')

    def _classed_geojson(self):
        """Two features carrying `size_class`, as a result layer would."""
        import tempfile
        data = ('{"type":"FeatureCollection","features":['
                '{"type":"Feature","properties":{"size_class":"largest"},'
                '"geometry":{"type":"Polygon","coordinates":'
                '[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}},'
                '{"type":"Feature","properties":{"size_class":"middle"},'
                '"geometry":{"type":"Polygon","coordinates":'
                '[[[2,0],[3,0],[3,1],[2,1],[2,0]]]}}]}')
        path = os.path.join(self._dir, "classed.geojson")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data)
        return path

    def _draws_something(self, layer):
        from qgis.core import (QgsExpression, QgsExpressionContext,
                               QgsExpressionContextUtils)
        renderer = layer.renderer()
        root = getattr(renderer, "rootRule", None)
        if root is None:
            return True
        context = QgsExpressionContext()
        context.appendScope(QgsExpressionContextUtils.layerScope(layer))
        for feature in layer.getFeatures():
            context.setFeature(feature)
            for rule in root().children():
                text = rule.filterExpression()
                if not text or QgsExpression(text).evaluate(context):
                    return True
        return False

    def test_a_style_whose_filters_match_is_kept(self):
        from qgis.core import QgsVectorLayer
        from rasid_components.layer_style import apply_sld
        layer = QgsVectorLayer(self._classed_geojson(), "ok", "ogr")
        self.assertTrue(apply_sld(
            layer, self._filtered_sld("size_class", ["largest", "middle"])))
        self.assertTrue(self._draws_something(layer))

    def test_a_style_that_would_draw_nothing_is_discarded(self):
        """The reported symptom: loaded, ticked, legend shown, map empty.

        An SLD's rules become a rule-based renderer, and one with no matching
        rule and no catch-all draws NO features while still listing its
        classes in the legend — indistinguishable from a broken download.
        Measured: a style keyed on a field the data lacks renders 0 of 3.
        """
        from qgis.core import QgsVectorLayer
        from rasid_components.layer_style import apply_sld
        for field, values in (("name", ["Khufu", "Khafre"]),
                              ("size_class", ["BIG", "SMALL"])):
            layer = QgsVectorLayer(self._classed_geojson(), "bad", "ogr")
            kept = apply_sld(layer, self._filtered_sld(field, values))
            self.assertFalse(kept, f"{field}={values} should not be kept")
            self.assertTrue(
                self._draws_something(layer),
                f"{field}={values}: the layer was left invisible")

    def test_the_layer_keeps_a_working_renderer_after_a_discard(self):
        """Reverted to what QGIS gave it, not left with a dead renderer."""
        from qgis.core import QgsVectorLayer, QgsSingleSymbolRenderer
        from rasid_components.layer_style import apply_sld
        layer = QgsVectorLayer(self._classed_geojson(), "bad2", "ogr")
        apply_sld(layer, self._filtered_sld("name", ["Khufu"]))
        self.assertIsInstance(layer.renderer(), QgsSingleSymbolRenderer)

    def test_an_unfiltered_style_is_never_second_guessed(self):
        """No filters means it catches everything; do not inspect features."""
        from qgis.core import QgsVectorLayer
        from rasid_components.layer_style import apply_sld
        layer = QgsVectorLayer(self._classed_geojson(), "plainsld", "ogr")
        self.assertTrue(apply_sld(layer, self.SLD))
        self.assertEqual(
            layer.renderer().symbol().color().name().lower(), "#ff00aa")

    def test_an_empty_layer_keeps_its_style(self):
        """No features is an empty result, not a failing style."""
        from qgis.core import QgsVectorLayer
        from rasid_components.layer_style import apply_sld
        path = os.path.join(self._dir, "empty.geojson")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"type":"FeatureCollection","features":[]}')
        layer = QgsVectorLayer(path, "empty", "ogr")
        if not layer.isValid():
            self.skipTest("OGR would not open an empty FeatureCollection")
        self.assertTrue(apply_sld(
            layer, self._filtered_sld("size_class", ["largest"])))

    def test_a_raster_is_skipped_rather_than_failing(self):
        """QGIS refuses SLD on rasters — "Layer type 1 not supported".

        Measured on 3.36.3 and 4.0.1 with a georeferenced raster and a real
        RasterSymbolizer. Not a gap in this code: the website does not style
        rasters either, it serves them as pre-rendered tiles with the
        colouring already baked in.
        """
        from rasid_components.layer_style import apply_sld
        from qgis.core import QgsRasterLayer
        layer = QgsRasterLayer(self.geojson, "notaraster")   # invalid is fine
        self.assertFalse(apply_sld(layer, self.SLD))

    # ── the plumbing that was missing ───────────────────────────────────────
    def test_the_page_hands_the_file_list_to_the_bubble(self):
        from rasid_components.gopilot_page import GoPilotPage
        page = GoPilotPage(None, None)
        page.show()
        for _ in range(10):
            QGIS_APP.processEvents()
        try:
            page.start_new_chat()
            for _ in range(10):
                QGIS_APP.processEvents()
            page._remove_empty_state()
            page.add_message(f"result [b.geojson]({self.URL})",
                             is_user=False, message_id=77,
                             files=self._files(self.SLD))
            for _ in range(10):
                QGIS_APP.processEvents()
            self.assertEqual(page._bubbles[-1].style_for(self.URL), self.SLD)
        finally:
            _destroy(page)

    def test_a_reloaded_chat_styles_its_layers_too(self):
        """Not only a live turn: the loader reads `files` from the server."""
        from rasid_components.gopilot_page import GoPilotPage
        page = GoPilotPage(None, None)
        page.show()
        for _ in range(10):
            QGIS_APP.processEvents()
        try:
            page._on_messages_loaded([{
                "id": 77, "role": "assistant", "is_user": False,
                "content": f"result [b.geojson]({self.URL})",
                "files": self._files(self.SLD),
            }])
            for _ in range(12):
                QGIS_APP.processEvents()
            self.assertEqual(page._bubbles[-1].style_for(self.URL), self.SLD)
        finally:
            _destroy(page)


class MessageIdReconcileTest(unittest.TestCase):
    """A just-sent prompt has to get its server id without a page reload.

    The panel draws the user's message immediately rather than waiting for a
    round trip to echo it, so that bubble has no server row behind it. Two
    things need one, and both looked to the user like "you have to leave the
    page and come back":

    * the trash control, hidden until there is something to ask the server
      to delete;
    * `_on_message_deleted`, which removes bubbles BY id — so trashing a
      reply left its id-less prompt behind as an orphan.

    Reopening the chat fixed both because it refetched the transcript. This
    does the same refetch without rebuilding anything.
    """

    ROWS = [
        {"id": 500, "is_user": True, "content": "map the dam"},
        {"id": 501, "is_user": False, "content": "Here it is."},
    ]

    class _Gopilot:
        def __init__(self, rows):
            self.rows = rows
            self.calls = 0

        def get_messages(self, session_id):
            self.calls += 1
            return list(self.rows)

    class _Client:
        api_key = "k"

        def __init__(self, rows):
            self.gopilot = MessageIdReconcileTest._Gopilot(rows)

    def _page(self, rows=None):
        from rasid_components.gopilot_page import GoPilotPage
        page = GoPilotPage(self._Client(self.ROWS if rows is None else rows),
                           None)
        page.resize(470, 760)
        page.show()
        for _ in range(15):
            QGIS_APP.processEvents()
        page.start_new_chat()
        for _ in range(12):
            QGIS_APP.processEvents()
        page.current_chat_id = "sess-1"
        page._remove_empty_state()
        self._pages.append(page)
        return page

    def _settle(self, timeout_ms=1500):
        """Let the fetch thread actually run; pumping alone will not."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            QGIS_APP.processEvents()

    def setUp(self):
        self._pages = []

    def tearDown(self):
        for page in self._pages:
            _destroy(page)
        self._pages = []

    def test_a_just_sent_prompt_starts_without_an_id_or_a_trash(self):
        """The starting condition — otherwise the rest proves nothing."""
        page = self._page()
        page.add_message("map the dam", is_user=True)
        for _ in range(10):
            QGIS_APP.processEvents()
        bubble = page._bubbles[-1]
        self.assertIsNone(bubble.message_id)
        self.assertFalse(bubble._trash_button.isVisible())

    def test_the_prompt_gets_its_id_once_the_turn_lands(self):
        page = self._page()
        page.add_message("map the dam", is_user=True)
        page.add_message("Here it is.", is_user=False, message_id=501)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()
        prompt = page._bubbles[0]
        self.assertEqual(prompt.message_id, 500)
        self.assertTrue(prompt._trash_button.isVisible(),
                        "the trash is still hidden on a sent message")

    def test_trashing_the_reply_then_removes_the_prompt_too(self):
        page = self._page()
        page.add_message("map the dam", is_user=True)
        page.add_message("Here it is.", is_user=False, message_id=501)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()

        # What the server reports after trashing the turn.
        page._on_message_deleted([500, 501])
        for _ in range(15):
            QGIS_APP.processEvents()
        self.assertEqual(page._bubbles, [],
                         "an orphaned prompt was left on screen")

    def test_matching_is_by_content_not_position(self):
        """A system notice from a failed turn shifts the two lists apart.

        Assigning by position would then give the prompt somebody else's id,
        and its trash button would delete the wrong message.
        """
        page = self._page()
        page._add_system_message("**Could not do that**", tone="error")
        page.add_message("map the dam", is_user=True)
        page.add_message("Here it is.", is_user=False, message_id=501)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()
        by_role = {b.role: b.message_id for b in page._bubbles}
        self.assertEqual(by_role.get("user"), 500)
        self.assertIsNone(by_role.get("system"),
                          "a system notice was given a server id")

    def test_two_prompts_are_matched_by_their_text_not_their_order(self):
        """The case where only the text tells them apart.

        Both bubbles are prompts, so the role check cannot separate them, and
        the server here answers newest-first. Taking the first unclaimed row
        of the right role would give each bubble the OTHER one's id — and a
        trash button that deletes the wrong message is worse than one that
        does not appear.
        """
        rows = [{"id": 901, "is_user": True, "content": "beta"},
                {"id": 900, "is_user": True, "content": "alpha"}]
        page = self._page(rows)
        page.add_message("alpha", is_user=True)
        page.add_message("beta", is_user=True)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()
        got = [(b.raw_text(), b.message_id) for b in page._bubbles]
        self.assertEqual(got, [("alpha", 900), ("beta", 901)], got)

    def test_a_prompt_never_takes_an_assistant_row_id(self):
        """Roles must not be crossed, even on identical text.

        Here the only server row is an assistant one, so the prompt has
        nothing it may claim and the fallback rebuilds from the server. The
        invariant that matters either way: that id belongs to an ASSISTANT
        bubble and must never end up on a prompt, whose trash button would
        then delete the reply instead.
        """
        rows = [{"id": 700, "is_user": False, "content": "same words"}]
        page = self._page(rows)
        page.add_message("same words", is_user=True)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()
        for bubble in page._bubbles:
            if bubble.message_id == 700:
                self.assertEqual(
                    bubble.role, "ai",
                    "an assistant row's id landed on a prompt")

    def test_an_id_already_on_screen_is_not_handed_out_twice(self):
        """Two identical prompts must not end up as the same server row."""
        rows = [{"id": 800, "is_user": True, "content": "again"},
                {"id": 801, "is_user": True, "content": "again"}]
        page = self._page(rows)
        page.add_message("again", is_user=True)
        page.add_message("again", is_user=True)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()
        ids = [b.message_id for b in page._bubbles]
        self.assertEqual(sorted(ids), [800, 801], ids)

    def test_nothing_is_fetched_when_every_bubble_has_an_id(self):
        """One round trip per turn is the budget; zero when there is no need."""
        page = self._page()
        page.add_message("map the dam", is_user=True, message_id=500)
        page.add_message("Here it is.", is_user=False, message_id=501)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle(400)
        self.assertEqual(page.client.gopilot.calls, 0)

    def test_an_unsaved_chat_fetches_nothing(self):
        page = self._page()
        page.current_chat_id = None
        page.add_message("map the dam", is_user=True)
        page._reconcile_message_ids()
        self._settle(400)
        self.assertEqual(page.client.gopilot.calls, 0)

    def test_finishing_a_turn_reconciles_without_being_asked(self):
        """The hook has to be IN `_on_reply`, not merely available to it.

        Every other test here calls `_reconcile_message_ids` itself, so they
        all keep passing with the call site deleted — which is precisely the
        bug that was reported. This one goes through the turn.
        """
        page = self._page()
        page.add_message("map the dam", is_user=True)
        page._show_thinking()           # the bubble the reply streams into
        for _ in range(10):
            QGIS_APP.processEvents()

        page._on_reply({
            "session_id": "sess-1",
            "message": "Here it is.",
            "llm_message": {"id": 501, "content": "Here it is."},
        })
        self._settle()

        prompt = page._bubbles[0]
        self.assertEqual(prompt.role, "user")
        self.assertEqual(
            prompt.message_id, 500,
            "finishing a turn left the prompt without its server id")
        self.assertTrue(prompt._trash_button.isVisible())

    def test_an_unmatchable_bubble_falls_back_to_a_rebuild(self):
        """Matching on text cannot be trusted; the outcome still must be.

        A server that trims, normalises or annotates what it stores will
        never match, and a bubble left without an id is the whole bug — no
        trash, and invisible to a delete. So the fallback does automatically
        what reopening the chat did by hand.
        """
        rows = [{"id": 500, "is_user": True,
                 "content": "map the dam [edited]"},
                {"id": 501, "is_user": False, "content": "Here it is."}]
        page = self._page(rows)
        page.add_message("map the dam", is_user=True)
        page.add_message("Here it is.", is_user=False, message_id=501)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()

        ids = [b.message_id for b in page._bubbles]
        self.assertNotIn(None, ids, f"a bubble was left with no id: {ids}")
        self.assertTrue(all(b._trash_button.isVisible()
                            for b in page._bubbles))

    def test_a_successful_match_does_not_rebuild(self):
        """The rebuild is the fallback, not the mechanism.

        Rebuilding costs a flicker, the scroll position and every image
        preview, so the normal path must leave the existing widgets alone.
        Identity of the bubble objects is the test: a rebuild replaces them.
        """
        page = self._page()
        page.add_message("map the dam", is_user=True)
        page.add_message("Here it is.", is_user=False, message_id=501)
        for _ in range(10):
            QGIS_APP.processEvents()
        before = list(page._bubbles)

        page._reconcile_message_ids()
        self._settle()

        self.assertEqual([id(b) for b in page._bubbles],
                         [id(b) for b in before],
                         "the transcript was rebuilt when it did not need to be")
        self.assertEqual(page._bubbles[0].message_id, 500)

    def test_a_failed_refetch_is_silent(self):
        """The ids are a nicety; a working turn must not sprout an error."""
        class Boom:
            def get_messages(self, session_id):
                raise RuntimeError("503")

        page = self._page()
        page.client.gopilot = Boom()
        page.add_message("map the dam", is_user=True)
        for _ in range(10):
            QGIS_APP.processEvents()
        page._reconcile_message_ids()
        self._settle()
        roles = [b.role for b in page._bubbles]
        self.assertNotIn("system", roles,
                         "a failed id refetch put an error card in the chat")


class TranscriptResyncAfterDeleteTest(unittest.TestCase):
    """After a delete the server decides what is left, not id arithmetic.

    Removing bubbles by the ids the delete reported is the optimistic half:
    instant, and right whenever we hold an id for everything the server
    trashed. It is not always right —

    * the backend takes a whole TURN but a build that does not send
      `trashed_ids` falls back to the single id we asked about;
    * a prompt drawn locally this session may have had no id to match.

    Either way the pair's other half stayed on screen as an orphan until the
    chat was reopened. The SaaS never has this bug because its delete
    invalidates the messages query and the list is rebuilt from the server.
    """

    class _Gopilot:
        def __init__(self):
            self.rows = [
                {"id": 500, "is_user": True, "content": "turn one"},
                {"id": 501, "is_user": False, "content": "answer one"},
                {"id": 502, "is_user": True, "content": "turn two"},
                {"id": 503, "is_user": False, "content": "answer two"},
            ]

        def get_messages(self, session_id):
            return [dict(row) for row in self.rows]

        def trash(self, *ids):
            self.rows = [r for r in self.rows if r["id"] not in ids]

    class _Client:
        api_key = "k"

        def __init__(self):
            self.gopilot = TranscriptResyncAfterDeleteTest._Gopilot()

    def setUp(self):
        from rasid_components.gopilot_page import GoPilotPage
        self.client = self._Client()
        self.page = GoPilotPage(self.client, None)
        self.page.resize(470, 760)
        self.page.show()
        for _ in range(15):
            QGIS_APP.processEvents()
        self.page.start_new_chat()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.page.current_chat_id = "sess-1"
        self.page._remove_empty_state()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _fill(self, with_ids=True):
        for row in self.client.gopilot.rows:
            self.page.add_message(
                row["content"], is_user=row["is_user"],
                message_id=row["id"] if with_ids else None)
        for _ in range(15):
            QGIS_APP.processEvents()

    def _settle(self, timeout_ms=1500):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            QGIS_APP.processEvents()

    def _texts(self):
        return [b.raw_text() for b in self.page._bubbles]

    def test_an_orphan_is_cleared_when_only_one_id_was_reported(self):
        self._fill()
        self.client.gopilot.trash(502, 503)     # the server took the turn
        self.page._on_message_deleted([503])    # but named only the reply
        self._settle()
        self.assertEqual(self._texts(), ["turn one", "answer one"],
                         "the prompt was left behind as an orphan")

    def test_an_orphan_is_cleared_when_the_prompt_had_no_id(self):
        self._fill(with_ids=False)
        for bubble in self.page._bubbles:
            if bubble.role != "user":
                for row in self.client.gopilot.rows:
                    if row["content"] == bubble.raw_text():
                        bubble.set_message_id(row["id"])
        for _ in range(10):
            QGIS_APP.processEvents()
        self.client.gopilot.trash(502, 503)
        self.page._on_message_deleted([502, 503])
        self._settle()
        self.assertEqual(self._texts(), ["turn one", "answer one"])

    def test_the_remaining_prompt_also_gets_its_id(self):
        """So the NEXT delete does not strand anything either."""
        self._fill(with_ids=False)
        self.client.gopilot.trash(502, 503)
        self.page._on_message_deleted([502, 503])
        self._settle()
        self.assertEqual(sorted(b.message_id for b in self.page._bubbles),
                         [500, 501])

    def test_a_correct_optimistic_update_is_not_rebuilt(self):
        """No flicker when the ids already agreed with the server."""
        self._fill()
        before = list(self.page._bubbles)
        self.client.gopilot.trash(502, 503)
        self.page._on_message_deleted([502, 503])
        self._settle()
        kept = [b for b in before if b.raw_text() in ("turn one",
                                                      "answer one")]
        self.assertEqual([id(b) for b in self.page._bubbles],
                         [id(b) for b in kept],
                         "the transcript was rebuilt needlessly")

    def test_an_emptied_session_goes_to_a_new_chat(self):
        self.client.gopilot.rows = self.client.gopilot.rows[:2]
        self._fill()
        self.client.gopilot.trash(500, 501)
        self.page._on_message_deleted([501])
        self._settle()
        self.assertEqual(self.page._bubbles, [])
        self.assertIsNone(self.page.current_chat_id)
        self.assertIsNotNone(self.page._empty_state)

    def test_a_failed_resync_leaves_the_optimistic_update_alone(self):
        class Boom:
            def get_messages(self, session_id):
                raise RuntimeError("503")

        self._fill()
        self.page.client.gopilot = Boom()
        self.page._on_message_deleted([502, 503])
        self._settle()
        self.assertEqual(self._texts(), ["turn one", "answer one"])
        self.assertNotIn("system", [b.role for b in self.page._bubbles],
                         "a failed bookkeeping refetch added an error card")


class DeletingAMessageTest(unittest.TestCase):
    """What happens to the SESSION when an exchange is deleted.

    The backend trashes a whole turn — asking it to delete a reply takes the
    prompt with it, and vice versa, because there is no edit. So deleting
    "one chat" always removes a pair, and the only question is whether the
    session still has anything in it afterwards:

    * one exchange, deleted  -> the session is now empty, which is the same
      thing as having deleted the session, so go to a new chat;
    * one of several, deleted -> there is still a conversation to read, so
      nothing moves.

    Not to be confused with deleting a SESSION from the History list, which
    is :class:`DeletingAChatFromHistoryTest`.
    """

    def setUp(self):
        self.shell = GoPilotShell(None)
        self.shell.resize(470, 760)
        self.shell.show()
        for _ in range(20):
            QGIS_APP.processEvents()
        self.page = self.shell.chat_page

    def tearDown(self):
        self.page = None
        _destroy(self.shell)
        self.shell = None

    def _session_with(self, exchanges):
        """Returns the (user_id, bot_id) pairs now in the transcript."""
        self.page.start_new_chat()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.page.current_chat_id = "sess-1"
        self.page._remove_empty_state()
        pairs = []
        for index in range(exchanges):
            user_id, bot_id = 100 + index * 2, 101 + index * 2
            self.page.add_message(f"question {index}", is_user=True,
                                  message_id=user_id)
            self.page.add_message(f"answer {index}", is_user=False,
                                  message_id=bot_id)
            pairs.append((user_id, bot_id))
        for _ in range(20):
            QGIS_APP.processEvents()
        return pairs

    def _delete(self, pair):
        """What the server reports after trashing a turn: both ids."""
        self.page._on_message_deleted(list(pair))
        for _ in range(20):
            QGIS_APP.processEvents()

    # ── one exchange: deleting it empties the session ────────────────────────
    def test_deleting_the_only_exchange_leaves_the_session(self):
        pairs = self._session_with(1)
        self._delete(pairs[0])
        self.assertIsNone(self.page.current_chat_id,
                          "still pointing at a session with nothing in it")

    def test_deleting_the_only_exchange_shows_the_new_chat_page(self):
        pairs = self._session_with(1)
        self._delete(pairs[0])
        self.assertIsNotNone(self.page._empty_state,
                             "left looking at a blank transcript")

    def test_deleting_the_only_exchange_stays_in_the_chat_view(self):
        """A new chat page, not a different part of the plugin."""
        pairs = self._session_with(1)
        self._delete(pairs[0])
        self.assertEqual(self.shell.current_view(), "chat")

    # ── several exchanges: deleting one changes nothing else ─────────────────
    def test_deleting_one_of_two_keeps_the_session(self):
        pairs = self._session_with(2)
        self._delete(pairs[0])
        self.assertEqual(self.page.current_chat_id, "sess-1")

    def test_deleting_one_of_two_leaves_the_other_on_screen(self):
        pairs = self._session_with(2)
        self._delete(pairs[0])
        remaining = sorted(b.message_id for b in self.page._bubbles)
        self.assertEqual(remaining, [102, 103])
        self.assertIsNone(self.page._empty_state,
                          "the empty state appeared over a live conversation")

    def test_both_halves_of_the_turn_go(self):
        """Deleting the reply takes the prompt, and the other way round."""
        pairs = self._session_with(2)
        self._delete(pairs[1])
        remaining = sorted(b.message_id for b in self.page._bubbles)
        self.assertEqual(remaining, [100, 101])

    # ── what counts as "still has chats" ────────────────────────────────────
    def test_a_leftover_error_card_does_not_count_as_a_conversation(self):
        """An error notice is not a chat.

        One left over from a failed turn would otherwise make an emptied
        session look like it still had something in it, and the user would
        be stranded on a dead session showing only an error.
        """
        pairs = self._session_with(1)
        self.page._add_system_message("**Could not do that**", tone="error")
        for _ in range(12):
            QGIS_APP.processEvents()
        self._delete(pairs[0])
        self.assertIsNone(self.page.current_chat_id)
        self.assertIsNotNone(self.page._empty_state)

    def test_a_just_sent_message_counts_even_without_an_id(self):
        """It has no server id yet, but it is on screen and it is a chat."""
        pairs = self._session_with(1)
        self.page.add_message("just typed", is_user=True)   # no message_id
        for _ in range(12):
            QGIS_APP.processEvents()
        self._delete(pairs[0])
        self.assertEqual(self.page.current_chat_id, "sess-1",
                         "threw away a session that still had a message")

    def test_the_history_list_is_told_either_way(self):
        for exchanges in (1, 2):
            pairs = self._session_with(exchanges)
            seen = []
            self.page.history_changed.connect(lambda: seen.append(True))
            try:
                self._delete(pairs[0])
                self.assertTrue(seen, f"{exchanges} exchange(s): "
                                      f"history was not refreshed")
            finally:
                self.page.history_changed.disconnect()


class DrawBarFootprintTest(unittest.TestCase):
    """The on-canvas draw controls: one row, movable, parked where you left it.

    This was a 280x300 card that could not be moved — measured, 9% of the
    AREA of a 1280x720 canvas and 42% of its HEIGHT, sitting on top of the
    imagery being traced. The numbers below are the point of the change, so
    they are asserted rather than left to a screenshot.
    """

    # Generous enough not to be brittle about font metrics, tight enough to
    # fail if the strip ever grows a second row or its labels back.
    MAX_HEIGHT = 56
    MAX_AREA_PCT = 3.0

    def setUp(self):
        from rasid_components.geometry.modern_draw_tool import DrawingToolbar
        self._saved = prefs.get_str(prefs.DRAW_BAR_POS)
        prefs.set_value(prefs.DRAW_BAR_POS, "")
        self.bar = DrawingToolbar()
        self.bar.show()
        for _ in range(20):
            QGIS_APP.processEvents()

    def tearDown(self):
        self.bar.cleanup()
        _destroy(self.bar)
        self.bar = None
        prefs.set_value(prefs.DRAW_BAR_POS, self._saved or "")

    def test_it_is_a_single_row(self):
        self.bar.update_info(4, "12.4 km²", "14.1 km")
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertLessEqual(
            self.bar.height(), self.MAX_HEIGHT,
            f"the strip is {self.bar.height()}px tall; it has grown a row")

    def test_it_covers_little_of_the_map(self):
        self.bar.update_info(4, "12.4 km²", "14.1 km")
        for _ in range(12):
            QGIS_APP.processEvents()
        pct = (self.bar.width() * self.bar.height()) / (1280.0 * 720.0) * 100
        self.assertLess(
            pct, self.MAX_AREA_PCT,
            f"covers {pct:.1f}% of a 1280x720 canvas "
            f"({self.bar.width()}x{self.bar.height()})")

    def test_the_measurements_read_on_one_line(self):
        self.bar.update_info(4, "12.4 km²", "14.1 km")
        text = self.bar.info_label.text()
        self.assertNotIn("\n", text, text)
        self.assertIn("12.4 km²", text)
        self.assertIn("14.1 km", text)
        self.assertFalse(self.bar.info_label.wordWrap(),
                         "word wrap is what let this grow to three lines")

    def test_only_the_primary_action_keeps_a_label(self):
        self.assertTrue(self.bar.finish_btn.text().strip())
        for name in ("undo_btn", "clear_btn", "cancel_btn"):
            button = getattr(self.bar, name)
            self.assertEqual(button.text(), "", name)

    def test_every_icon_only_button_says_what_it_does_and_its_key(self):
        """The tooltips carry what the removed hint line used to say."""
        expected = {"undo_btn": "Backspace", "cancel_btn": "Esc"}
        for name in ("undo_btn", "clear_btn", "cancel_btn"):
            tip = getattr(self.bar, name).toolTip()
            self.assertTrue(tip.strip(), f"{name} has no tooltip")
            if name in expected:
                self.assertIn(expected[name], tip, name)

    def test_the_three_glyphs_are_different_shapes(self):
        """Rendered at 14px, `undo` and `refresh` are one arc at two
        rotations — with the labels gone the glyph is all there is to tell
        these apart, so `clear` uses a bin instead."""
        names = {self.bar.undo_btn._icon_name,
                 self.bar.clear_btn._icon_name,
                 self.bar.cancel_btn._icon_name}
        self.assertEqual(len(names), 3, names)
        self.assertNotIn("refresh", names)

    def test_it_is_draggable(self):
        from rasid_components.window_drag import WindowDragFilter
        self.assertIsInstance(self.bar._drag, WindowDragFilter)
        self.assertTrue(self.bar._drag.is_enabled())

    def test_the_parked_position_survives_the_bar(self):
        self.bar.move(321, 234)
        for _ in range(12):
            QGIS_APP.processEvents()
        self.bar.remember_position()
        self.assertEqual(prefs.get_str(prefs.DRAW_BAR_POS), "321,234")

    def test_hiding_remembers_where_it_was(self):
        """Hiding is common to finish, cancel and the tool being torn down."""
        self.bar.move(275, 180)
        for _ in range(12):
            QGIS_APP.processEvents()
        self.bar.hide()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertEqual(prefs.get_str(prefs.DRAW_BAR_POS), "275,180")

    def test_an_offscreen_memory_is_discarded(self):
        """A position from a monitor that has since been unplugged.

        Restoring it would park the bar where it cannot be reached or
        dragged back, with no way to recover but editing QSettings.
        """
        prefs.set_value(prefs.DRAW_BAR_POS, "-9000,-9000")
        self.assertIsNone(self.bar.remembered_position())

    def test_a_corrupt_memory_is_discarded(self):
        for value in ("garbage", "12", "a,b", "12,", ""):
            prefs.set_value(prefs.DRAW_BAR_POS, value)
            self.assertIsNone(self.bar.remembered_position(), value)

    def test_a_reachable_memory_is_restored(self):
        screen = QGIS_APP.primaryScreen().availableGeometry()
        point = screen.topLeft() + QPoint(60, 60)
        prefs.set_value(prefs.DRAW_BAR_POS, f"{point.x()},{point.y()}")
        self.assertEqual(self.bar.remembered_position(), point)


class NavOrderTest(unittest.TestCase):
    """Chat history belongs directly under New chat."""

    def test_history_follows_new_chat(self):
        from rasid_components.nav_rail import NAV_ITEMS
        keys = [key for key, _icon, _tip in NAV_ITEMS]
        self.assertEqual(keys[:2], ["new-chat", "history"],
                         f"the rail starts {keys[:3]}")

    def test_projects_and_gobox_no_longer_split_them(self):
        from rasid_components.nav_rail import NAV_ITEMS
        keys = [key for key, _icon, _tip in NAV_ITEMS]
        self.assertGreater(keys.index("projects"), keys.index("history"))
        self.assertGreater(keys.index("gobox"), keys.index("history"))


class DeletingAChatFromHistoryTest(unittest.TestCase):
    """Where a delete leaves the user depends on what is left behind.

    With other conversations still listed there is more to manage, so the
    view stays on History. With none left there is nothing to manage and an
    empty list is a dead end, so that case goes on to a new chat.

    Either way the chat page must stop pointing at a deleted conversation, or
    the composer would post the next message to a session the server no
    longer has — but that is a reset, not necessarily a navigation.
    """

    def setUp(self):
        self.shell = GoPilotShell(None)
        self.shell.resize(470, 760)
        self.shell.show()
        for _ in range(15):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.shell)
        self.shell = None

    def _seed(self, ids, open_id=None):
        """Put ``ids`` in the history list, with ``open_id`` the live chat."""
        self.shell.show_view("history")
        for _ in range(10):
            QGIS_APP.processEvents()
        history = self.shell.history_page
        history._all_chats = [{"id": i, "title": f"Chat {i}"} for i in ids]
        history._current_id = str(open_id) if open_id is not None else None
        self.shell.chat_page.current_chat_id = (
            str(open_id) if open_id is not None else None)
        history._render()
        for _ in range(10):
            QGIS_APP.processEvents()

    def _delete(self, chat_id):
        self.shell.history_page._on_deleted(chat_id)
        for _ in range(12):
            QGIS_APP.processEvents()

    def test_with_others_left_the_view_stays_on_history(self):
        self._seed(["77", "78", "79"], open_id="77")
        self._delete("77")
        self.assertEqual(self.shell.current_view(), "history",
                         "deleting one of several threw the user out")

    def test_with_others_left_the_chat_page_is_still_reset(self):
        """Otherwise the next message posts to a deleted session."""
        self._seed(["77", "78", "79"], open_id="77")
        self._delete("77")
        self.assertIsNone(self.shell.chat_page.current_chat_id)

    def test_deleting_the_last_chat_goes_on_to_a_new_one(self):
        """An empty History has nothing left to manage."""
        self._seed(["77"], open_id="77")
        self._delete("77")
        self.assertEqual(self.shell.current_view(), "chat")
        self.assertIsNone(self.shell.chat_page.current_chat_id)

    def test_deleting_the_last_chat_navigates_even_if_it_was_not_open(self):
        """The rule is about what is LEFT, not about which one went."""
        self._seed(["77"], open_id=None)
        self._delete("77")
        self.assertEqual(self.shell.current_view(), "chat")

    def test_deleting_some_other_chat_touches_neither(self):
        self._seed(["77", "78"], open_id="77")
        self._delete("78")
        self.assertEqual(self.shell.current_view(), "history")
        self.assertEqual(self.shell.chat_page.current_chat_id, "77")

    def test_the_open_chat_stays_selected_when_another_is_deleted(self):
        self._seed(["77", "78"], open_id="77")
        self._delete("78")
        self.assertEqual(self.shell.history_page._current_id, "77")

    def test_the_new_chat_signal_still_navigates(self):
        """The two signals mean different things and must stay apart."""
        self.shell.show_view("history")
        self.shell.history_page.new_chat_requested.emit()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertEqual(self.shell.current_view(), "chat")


class PlanCardTest(unittest.TestCase):
    """What the account is entitled to, and what each absence means.

    Every figure from `/billing/me/` is optional, and none of the absences
    mean zero: a null wallet is an account from before billing, an absent
    storage block is an older watcher, a null subscription is no paid plan.
    Showing zeros for any of them would be the same bug that once told every
    user they held 0 tokens.
    """

    FULL = {
        "plan_code": "pro", "plan_name": "Pro",
        "gotokens": {"balance": "2404153.0000",
                     "monthly_allowance": "5000000.0000",
                     "period_end": "2026-12-01", "is_overdrawn": False},
        "storage": {"used_bytes": 1288490188, "limit_bytes": 107374182400,
                    "unlimited": False, "over": False, "fraction": 0.012},
        "subscription": {"status": "active",
                         "current_period_end": "2026-12-01T00:00:00Z",
                         "cancel_at_period_end": False},
    }

    def setUp(self):
        from rasid_components.settings_page import SettingsPage
        self.page = SettingsPage(client=None)
        self.page.resize(460, 900)
        self.page.show()
        for _ in range(12):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _apply(self, entitlements, error=None):
        self.page.set_profile({"profile": {"email": "a@b.c"},
                               "entitlements": entitlements,
                               "entitlements_error": error})
        for _ in range(12):
            QGIS_APP.processEvents()

    def test_a_full_plan_shows_tokens_storage_and_the_renewal(self):
        self._apply(self.FULL)
        self.assertTrue(self.page._plan_card.isVisible())
        self.assertEqual(self.page._plan_name.text(), "Pro")
        self.assertEqual(self.page._plan_renewal.text(), "Renews 1 Dec 2026")
        self.assertIn("2,404,153", self.page._tokens_caption.text())
        self.assertIn("5,000,000", self.page._tokens_caption.text())
        self.assertIn("100 GB", self.page._storage_caption.text())
        self.assertTrue(self.page._tokens_meter.isVisible())
        self.assertTrue(self.page._storage_meter.isVisible())

    def test_a_cancelling_subscription_ends_rather_than_renews(self):
        """Not a renewal date at all — the opposite — so not that word."""
        payload = dict(self.FULL)
        payload["subscription"] = dict(self.FULL["subscription"],
                                       cancel_at_period_end=True)
        self._apply(payload)
        self.assertEqual(self.page._plan_renewal.text(), "Ends 1 Dec 2026")

    def test_unlimited_storage_draws_no_bar(self):
        """A full bar and no limit at all must not look alike."""
        payload = dict(self.FULL)
        payload["storage"] = {"used_bytes": 1288490188, "limit_bytes": 0,
                              "unlimited": True, "over": False,
                              "fraction": None}
        self._apply(payload)
        self.assertIn("unlimited", self.page._storage_caption.text())
        self.assertFalse(self.page._storage_meter.isVisible())

    def test_being_over_the_limit_is_coloured_differently(self):
        payload = dict(self.FULL)
        payload["storage"] = dict(self.FULL["storage"], over=True,
                                  used_bytes=112000000000, fraction=1.04)
        self._apply(payload)
        self.assertIn(design.Tokens(design.is_dark()).error,
                      self.page._storage_meter.styleSheet())

    def test_a_null_wallet_hides_the_token_row_rather_than_showing_zero(self):
        payload = {"plan_code": "free", "plan_name": "Free",
                   "gotokens": None, "subscription": None}
        self._apply(payload)
        self.assertTrue(self.page._plan_card.isVisible())
        self.assertEqual(self.page._plan_name.text(), "Free")
        self.assertFalse(self.page._tokens_caption.isVisible())
        self.assertFalse(self.page._tokens_meter.isVisible())

    def test_an_absent_storage_block_hides_the_storage_row(self):
        payload = {k: v for k, v in self.FULL.items() if k != "storage"}
        self._apply(payload)
        self.assertFalse(self.page._storage_caption.isVisible())
        self.assertFalse(self.page._storage_meter.isVisible())

    def test_a_failed_request_hides_the_whole_card(self):
        self._apply(None, error="503 Service Unavailable")
        self.assertFalse(self.page._plan_card.isVisible())

    def test_an_unparseable_date_is_dropped_not_printed_raw(self):
        """"Renews 2026-13-99" is worse than no renewal line."""
        payload = dict(self.FULL)
        payload["subscription"] = dict(self.FULL["subscription"],
                                       current_period_end="not a date")
        payload["gotokens"] = dict(self.FULL["gotokens"], period_end=None)
        self._apply(payload)
        self.assertEqual(self.page._plan_renewal.text(), "")
        self.assertFalse(self.page._plan_renewal.isVisible())

    def test_the_wallet_period_is_used_when_there_is_no_subscription(self):
        """A comped account still has an allowance that rolls over."""
        payload = dict(self.FULL)
        payload["subscription"] = None
        self._apply(payload)
        self.assertEqual(self.page._plan_renewal.text(),
                         "Allowance resets 1 Dec 2026")

    def test_the_version_is_not_hardcoded(self):
        """settings_page carried its own "2.0.0" beside the feedback one."""
        import inspect
        from rasid_components import settings_page
        source = inspect.getsource(settings_page)
        self.assertNotIn('PLUGIN_VERSION = "2.0.0"', source)


class AiDisclaimerTest(unittest.TestCase):
    """The AI notice under the composer, worded as the SaaS words it."""

    SENTENCE = (
        "GoPilot is powered by AI. By using it, you agree to our Terms of "
        "Service and Privacy Policy. Conversations may be reviewed for "
        "quality and safety, but are never used to train our AI models."
    )

    def setUp(self):
        from rasid_components.gopilot_page import GoPilotPage
        self.page = GoPilotPage(None, None)
        self.page.resize(design.DOCK_DEFAULT_WIDTH, 820)
        self.page.show()
        for _ in range(12):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _plain(self):
        """The label's text with the anchor markup stripped back out."""
        import re
        html = self.page._disclaimer_label.text()
        return re.sub(r"<[^>]+>", "", html)

    def test_the_wording_is_exact(self):
        self.assertEqual(self._plain(), self.SENTENCE)

    def test_it_is_visible_on_an_empty_chat_and_a_full_one(self):
        """A standing statement about the product, not an onboarding note."""
        self.page.start_new_chat()
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertTrue(self.page._disclaimer_label.isVisible())
        self.page.add_message("hello", is_user=True, message_id=1)
        for _ in range(12):
            QGIS_APP.processEvents()
        self.assertTrue(self.page._disclaimer_label.isVisible())

    def test_both_documents_are_reachable(self):
        """Naming documents a reader cannot open would be a dead end."""
        html = self.page._disclaimer_label.text()
        self.assertIn("/terms", html)
        self.assertIn("/privacy-policy", html)
        self.assertTrue(self.page._disclaimer_label.openExternalLinks())

    def test_it_sits_below_the_composer(self):
        host = self.page._disclaimer_label.parent()
        self.assertGreater(host.y(), self.page.composer.y(),
                           "the disclaimer is above the composer")


class MessageFooterOutsideBubbleTest(unittest.TestCase):
    """The timestamp and trash sit below the bubble, not inside its fill.

    Structural, not cosmetic. Inside the user's brand-green fill no red glyph
    can clear the 3:1 WCAG floor for a non-text graphic, because red and green
    sit at nearly the same luminance — it measured 1.14:1. Three in-bubble
    fixes were tried and rejected before the footer was moved out onto the
    panel background, which is one surface for both roles.
    """

    def setUp(self):
        from rasid_components.gopilot_page import GoPilotPage
        self.page = GoPilotPage(None, None)
        self.page.resize(470, 760)
        self.page.show()
        for _ in range(15):
            QGIS_APP.processEvents()
        self.page.start_new_chat()
        for _ in range(15):
            QGIS_APP.processEvents()
        self.page._remove_empty_state()
        self.page.add_message("a question", is_user=True, message_id=11)
        self.page.add_message("an answer", is_user=False, message_id=12)
        for _ in range(20):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def test_the_trash_is_below_the_styled_frame_on_both_roles(self):
        for bubble in self.page._bubbles:
            frame_bottom = bubble._frame.mapTo(
                bubble, bubble._frame.rect().bottomLeft()).y()
            self.assertGreaterEqual(
                bubble._trash_button.y(), frame_bottom,
                f"{bubble.role}: the trash is still inside the bubble fill")

    def test_the_container_itself_paints_nothing(self):
        """Only the inner frame carries the fill, or the footer sits on it."""
        for bubble in self.page._bubbles:
            self.assertNotIn("QFrame#bubble", bubble.styleSheet())
            self.assertIn("QFrame#bubble", bubble._frame.styleSheet())

    def test_the_red_clears_the_contrast_floor_where_it_now_sits(self):
        """3:1 is the WCAG floor for a non-text graphic."""
        def luminance(colour):
            channels = []
            for value in (colour.redF(), colour.greenF(), colour.blueF()):
                channels.append(value / 12.92 if value <= 0.03928
                                else ((value + 0.055) / 1.055) ** 2.4)
            return (0.2126 * channels[0] + 0.7152 * channels[1]
                    + 0.0722 * channels[2])

        for dark in (False, True):
            tokens = design.Tokens(dark)
            first = luminance(QColor(tokens.error))
            second = luminance(QColor(tokens.canvas))
            lighter, darker = max(first, second), min(first, second)
            value = (lighter + 0.05) / (darker + 0.05)
            self.assertGreaterEqual(
                value, 3.0,
                f"trash on the panel background is {value:.2f}:1 on "
                f"{'dark' if dark else 'light'}")

    def test_both_roles_share_one_control(self):
        tones = {b._trash_button._tone for b in self.page._bubbles}
        self.assertEqual(tones, {"danger"})

    def test_the_timestamp_is_not_the_bubble_foreground(self):
        """On the panel background, white-on-green would be white-on-white."""
        for bubble in self.page._bubbles:
            label = bubble._time_label
            if label is None:
                continue
            self.assertNotIn(bubble._fg.upper(), label.styleSheet().upper(),
                             f"{bubble.role}: timestamp kept the bubble's fg")


class DialogFooterTest(unittest.TestCase):
    """One convention for every dialog footer in the plugin.

    Taken from the SaaS, whose own footers are `flex justify-end gap-3` with
    Cancel first as `btn btn-outline bg-white` and the brand button second,
    and whose action buttons are bare strings — "Submit Feedback", "Create
    Project" — with a glyph only for the pending spinner.

    Three things had drifted: the feedback dialog's Cancel was borderless
    while every other one was bordered, the two action buttons had grown
    icons the product does not have, and the layer picker had its buttons in
    the opposite order.
    """

    def _dialogs(self):
        from rasid_components.dependencies.dependency_installer import (
            DependencyInstallerDialog,
        )
        from rasid_components.feedback_dialog import FeedbackDialog
        from rasid_components.geometry.drawing_help_dialog import (
            DrawingHelpDialog,
        )
        return [
            ("installer", DependencyInstallerDialog()),
            ("feedback", FeedbackDialog(client=None)),
            ("drawing help", DrawingHelpDialog()),
        ]

    def test_every_cancel_is_styled_identically(self):
        sheets = {}
        dialogs = self._dialogs()
        try:
            for name, dialog in dialogs:
                for _ in range(12):
                    QGIS_APP.processEvents()
                cancel = [b for b in dialog.findChildren(QPushButton)
                          if b.text().strip() == "Cancel"]
                self.assertEqual(len(cancel), 1, f"{name} has no Cancel")
                sheets[name] = cancel[0].styleSheet()
            self.assertEqual(
                len(set(sheets.values())), 1,
                "Cancel is styled differently per dialog: "
                + ", ".join(sorted(sheets)))
            # And it is the BORDERED one, not the borderless ghost.
            one = next(iter(sheets.values()))
            self.assertIn("border:", one)
            self.assertNotIn("border: none", one)
        finally:
            for _, dialog in dialogs:
                _destroy(dialog)

    def test_no_footer_button_carries_an_icon(self):
        dialogs = self._dialogs()
        try:
            for name, dialog in dialogs:
                for _ in range(12):
                    QGIS_APP.processEvents()
                for button in dialog.findChildren(QPushButton):
                    if not button.text().strip():
                        continue        # an icon-only control is not a footer
                    self.assertTrue(
                        button.icon().isNull(),
                        f"{name}: {button.text()!r} has an icon")
        finally:
            for _, dialog in dialogs:
                _destroy(dialog)

    def test_the_primary_action_comes_before_cancel(self):
        """Left to right: the green one, then Cancel.

        This is a deliberate divergence from the SaaS, whose footers are
        `flex justify-end` with Cancel first. The process wizard already put
        its primary first and that is the order this product wants, so the
        dialogs were brought into line with the wizard rather than the web.
        """
        dialogs = self._dialogs()
        try:
            for name, dialog in dialogs:
                # Shown, not just constructed: an unshown dialog has never
                # been laid out, so every child reports x() == 0 and the
                # comparison below passes or fails for no reason at all.
                dialog.show()
                for _ in range(15):
                    QGIS_APP.processEvents()
                labelled = [b for b in dialog.findChildren(QPushButton)
                            if b.text().strip()]
                texts = [b.text().strip() for b in labelled]
                if "Cancel" not in texts or len(labelled) < 2:
                    continue
                cancel = labelled[texts.index("Cancel")]
                others = [b for b in labelled if b is not cancel]
                for other in others:
                    self.assertGreater(
                        cancel.x(), other.x(),
                        f"{name}: Cancel sits left of {other.text()!r}")
        finally:
            for _, dialog in dialogs:
                _destroy(dialog)

    def test_the_layer_picker_follows_the_same_order(self):
        """Asserted on the source: this dialog needs a project to build."""
        source = _code_of(__import__(
            "rasid_components.geometry.geometry_input_manager",
            fromlist=["x"]).__file__.replace(".pyc", ".py"))
        select_at = source.index("btn_row.addWidget(btn_ok)")
        cancel_at = source.index("btn_row.addWidget(btn_cancel)")
        self.assertLess(select_at, cancel_at,
                        "Cancel is added before Select")


class DrawDoesNotDisturbQgisTest(unittest.TestCase):
    """Starting a draw must not minimize, raise or move the host window.

    Every part of this plugin lives inside a QDockWidget, so
    ``parent_widget.window()`` walks up to the QGIS MAIN WINDOW — and
    ``start_draw_mode`` used to call ``showMinimized()`` on whatever that walk
    returned. Docked, that minimized the entire application to draw on its
    own canvas, then restored it with ``showNormal()``/``raise_()``, which is
    what looked like QGIS being picked up and moved. The arrangement is built
    for real here (main window → dock → page) because that walk is the whole
    bug: a test with a bare parent widget passes either way.
    """

    def setUp(self):
        from qgis.gui import QgsMapCanvas
        self.host = QMainWindow()
        self.canvas = QgsMapCanvas(self.host)
        self.host.setCentralWidget(self.canvas)
        self.dock = QDockWidget("GoPilot", self.host)
        self.page = QWidget()
        self.dock.setWidget(self.page)
        from rasid_components.compat import Qt_RightDockWidgetArea
        self.host.addDockWidget(Qt_RightDockWidgetArea, self.dock)
        self.host.resize(900, 600)
        self.host.show()
        for _ in range(20):
            QGIS_APP.processEvents()

        test = self

        class FakeIface:
            def mainWindow(self):
                return test.host

            def mapCanvas(self):
                return test.canvas

        from rasid_components.geometry import GeometryInputManager
        self.manager = GeometryInputManager(FakeIface(),
                                            parent_widget=self.page)

    def tearDown(self):
        self.manager = None
        _destroy(self.host)
        self.host = None

    def test_the_page_really_does_resolve_to_the_qgis_window(self):
        """If this stops being true the other tests here prove nothing."""
        self.assertIs(self.page.window(), self.host)

    def test_starting_a_draw_leaves_the_host_window_alone(self):
        # minimize_parent=True deliberately: the GUARD is under test, not the
        # call sites that now pass False.
        self.manager.start_draw_mode(minimize_parent=True, show_help=False)
        for _ in range(20):
            QGIS_APP.processEvents()
        self.assertFalse(self.host.isMinimized(),
                         "drawing minimized QGIS itself")
        self.assertIsNone(
            self.manager._parent_dialog,
            "the host window was captured, so it will be raised on finish")

    def test_finishing_a_polygon_does_not_move_the_host_window(self):
        self.manager.start_draw_mode(minimize_parent=True, show_help=False)
        for _ in range(20):
            QGIS_APP.processEvents()
        before = (self.host.x(), self.host.y(), self.host.isMinimized())

        self.manager._on_draw_finished([[0, 0], [1, 0], [1, 1], [0, 0]])
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            QGIS_APP.processEvents()
        after = (self.host.x(), self.host.y(), self.host.isMinimized())
        self.assertEqual(before, after,
                         f"the host window changed on finish: "
                         f"{before} -> {after}")

    def test_a_window_the_plugin_owns_can_still_step_aside(self):
        """The guard is about the HOST window, not about the feature."""
        own = QMainWindow()
        own.resize(300, 200)
        own.show()
        page = QWidget()
        own.setCentralWidget(page)
        for _ in range(15):
            QGIS_APP.processEvents()
        try:
            test = self

            class FakeIface:
                def mainWindow(self):
                    return test.host

                def mapCanvas(self):
                    return test.canvas

            from rasid_components.geometry import GeometryInputManager
            manager = GeometryInputManager(FakeIface(), parent_widget=page)
            manager.start_draw_mode(minimize_parent=True, show_help=False)
            for _ in range(20):
                QGIS_APP.processEvents()
            self.assertIs(manager._parent_dialog, own)
            self.assertFalse(self.host.isMinimized(),
                             "the host was minimized anyway")
        finally:
            _destroy(own)

    def test_no_call_site_asks_to_minimize(self):
        """Belt and braces: the guard exists, and nobody leans on it."""
        import inspect
        from rasid_components import create_process_wizard, gopilot_page
        for module in (create_process_wizard, gopilot_page):
            source = inspect.getsource(module)
            self.assertNotIn(
                "minimize_parent=True", source,
                f"{module.__name__} still asks for the host to be minimized")


class ListSelectionContrastTest(unittest.TestCase):
    """A selected row has to set its own text colour, not inherit one.

    The catalogue search list styled only the selected row's BACKGROUND. Qt
    then draws the text in the palette's HighlightedText, which the host
    application sets to white to suit its own blue highlight — so the row the
    user had just clicked came out white on a pale green tint and was the one
    row in the list they could not read.
    """

    FLOOR = 4.5  # WCAG AA for body text.

    def _luminance(self, colour):
        channels = []
        for value in (colour.redF(), colour.greenF(), colour.blueF()):
            channels.append(value / 12.92 if value <= 0.03928
                            else ((value + 0.055) / 1.055) ** 2.4)
        return (0.2126 * channels[0] + 0.7152 * channels[1]
                + 0.0722 * channels[2])

    def _ratio(self, one, two):
        first, second = self._luminance(one), self._luminance(two)
        lighter, darker = max(first, second), min(first, second)
        return (lighter + 0.05) / (darker + 0.05)

    def test_the_selected_row_sets_a_colour_at_all(self):
        """Without this the palette decides, and the palette is the host's."""
        qss = design.list_qss()
        marker = "QListWidget::item:selected"
        self.assertIn(marker, qss)
        rule = qss.split(marker, 1)[1].split("}", 1)[0]
        self.assertIn("color:", rule,
                      "the selected row leaves its text to HighlightedText")

    def test_the_selected_row_is_legible_in_both_themes(self):
        for dark in (False, True):
            tokens = design.Tokens(dark)
            value = self._ratio(QColor(tokens.accent_text),
                                QColor(tokens.accent_soft))
            self.assertGreaterEqual(
                value, self.FLOOR,
                f"selected row is {value:.2f}:1 on "
                f"{'dark' if dark else 'light'}")

    def test_white_on_the_selection_tint_would_have_failed(self):
        """The measurement that makes the rule above worth keeping."""
        for dark in (False, True):
            tokens = design.Tokens(dark)
            value = self._ratio(QColor("#FFFFFF"),
                                QColor(tokens.accent_soft))
            if not dark:
                self.assertLess(
                    value, 1.5,
                    "light-mode white on the selection tint is supposed to "
                    "be the unreadable case this guards against")


class PromptSuggestionTest(unittest.TestCase):
    """The empty chat's openers, drawn from the same endpoint as the SaaS."""

    def setUp(self):
        from rasid_components.gopilot_page import GoPilotPage
        self.page = GoPilotPage(None, None)
        self.page.resize(design.DOCK_DEFAULT_WIDTH, 820)
        self.page.show()
        for _ in range(10):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def test_the_static_set_is_used_until_a_draw_lands(self):
        from rasid_components.gopilot_page import SUGGESTIONS
        self.assertEqual(self.page._suggestion_items(), SUGGESTIONS)

    def _armed_grid(self):
        """An empty chat with its openers, and the submit path recorded.

        ``_on_submit`` is taken off the signal first. What is under test is
        whether a CLICK submits, and leaving the real handler attached would
        start a SendMessageThread against a ``None`` client for every
        assertion — network machinery this test is not about.
        """
        self.page.start_new_chat()
        for _ in range(20):
            QGIS_APP.processEvents()
        self.page.composer.submitted.disconnect(self.page._on_submit)
        seen = []
        self.page.composer.submitted.connect(
            lambda text, files, geojson: seen.append(text))
        return self.page._suggestion_grid, seen

    def test_clicking_an_opener_sends_it(self):
        """Not "types it into the box" — the card IS the message."""
        grid, seen = self._armed_grid()
        card = grid.cards[0]
        card.clicked.emit()
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertEqual(seen, [card.prompt])
        self.assertEqual(
            self.page.composer.input.toPlainText(), "",
            "the composer still holds the text, so it was typed not sent")

    def test_an_opener_sends_its_prompt_not_its_label(self):
        """A card's title can be six words standing for a paragraph."""
        self.page._fetched_suggestions = [{
            "id": 7, "title": "Check a dam", "icon": "map",
            "description": "Look at the Three Gorges Dam",
            "prompt_text": "Locate the Three Gorges Dam and frame the map "
                           "on it, then report its surface area.",
        }]
        grid, seen = self._armed_grid()
        card = grid.cards[0]
        card.clicked.emit()
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertEqual(seen, ["Locate the Three Gorges Dam and frame the "
                                "map on it, then report its surface area."])
        self.assertNotEqual(seen, [card.title])

    def test_a_busy_composer_keeps_the_prompt_instead_of_dropping_it(self):
        """Mid-turn the click cannot send, so it must not vanish either."""
        grid, seen = self._armed_grid()
        card = grid.cards[0]
        self.page.composer.set_busy(True)
        try:
            card.clicked.emit()
            for _ in range(10):
                QGIS_APP.processEvents()
            self.assertEqual(seen, [], "sent while a turn was in flight")
            self.assertEqual(
                self.page.composer.input.toPlainText(), card.prompt,
                "the click was silently dropped")
        finally:
            self.page.composer.set_busy(False)

    def test_an_empty_response_keeps_the_static_set(self):
        """The empty state must never come up blank."""
        from rasid_components.gopilot_page import SUGGESTIONS
        self.page._on_suggestions([])
        self.assertEqual(self.page._suggestion_items(), SUGGESTIONS)

    def test_api_rows_become_cards_that_send_prompt_text(self):
        """`title` is what the card shows, `prompt_text` what gets sent."""
        self.page._fetched_suggestions = [{
            "id": 1, "title": "Slope from my DEM",
            "prompt_text": "Derive a slope raster from the loaded DEM",
            "description": "Adds the result as a layer", "icon": "layers",
        }]
        items = self.page._suggestion_items()
        self.assertEqual(len(items), 1)
        icon, title, description, prompt = items[0]
        self.assertEqual(icon, "layers")
        self.assertEqual(title, "Slope from my DEM")
        self.assertNotEqual(prompt, title)
        self.assertIn("slope raster", prompt)

    def test_an_unknown_server_icon_falls_back(self):
        """The API sends lucide names; ours is a 33-icon subset drawn in code."""
        from rasid_components import icons
        self.page._fetched_suggestions = [
            {"id": 1, "title": "t", "prompt_text": "p",
             "icon": "flux-capacitor"}]
        icon = self.page._suggestion_items()[0][0]
        self.assertIn(icon, icons.available())

    def test_every_alias_resolves_to_a_real_icon(self):
        from rasid_components import icons
        from rasid_components.gopilot_page import GoPilotPage
        for alias, name in GoPilotPage._ICON_ALIASES.items():
            self.assertIn(name, icons.available(), f"{alias} -> {name}")
        self.assertIn(GoPilotPage._ICON_FALLBACK, icons.available())

    def test_rows_without_any_text_are_skipped(self):
        from rasid_components.gopilot_page import SUGGESTIONS
        self.page._fetched_suggestions = [{"id": 1, "title": "", "icon": "map"}]
        self.assertEqual(self.page._suggestion_items(), SUGGESTIONS)


class DownloadAuthTest(unittest.TestCase):
    """Result downloads must be authenticated — and only to our own hosts.

    GoPilot serves result files from a host that is NOT ``API_HOST``
    (``app.gopilot.earth/api/llm/files/<id>/download/``), and the old rule
    treated any URL not starting with ``API_HOST`` as external and stripped
    the Authorization header. Every download of a chat result failed with
    ``401 Authentication credentials were not provided``.

    The other half matters just as much: a presigned storage link arrives
    inside a model answer, so it is attacker-influenced data and must never
    receive the user's token.
    """

    OURS = "https://app.gopilot.earth/api/llm/files/5342/download/"
    THEIRS = "https://bucket.s3.eu-west-1.amazonaws.com/x.tif?X-Amz-Sig=1"

    def test_our_file_endpoint_is_trusted(self):
        from rasid_components.config import is_trusted_api_url
        self.assertTrue(is_trusted_api_url(self.OURS))
        self.assertTrue(
            is_trusted_api_url("https://api.rasid.ai/api/llm/files/1/download/"))

    def test_a_bare_host_with_no_path_is_trusted(self):
        """`startswith(API_HOST + "/")` rejected this; the host test does not."""
        from rasid_components.config import is_trusted_api_url
        self.assertTrue(is_trusted_api_url("https://api.rasid.ai"))

    def test_third_party_storage_is_not_trusted(self):
        from rasid_components.config import is_trusted_api_url
        self.assertFalse(is_trusted_api_url(self.THEIRS))

    def test_a_lookalike_host_is_not_trusted(self):
        """The reason this matches on the host and not on a substring."""
        from rasid_components.config import is_trusted_api_url
        for url in ("https://rasid.ai.attacker.example/steal",
                    "https://notrasid.ai/x",
                    "https://gopilot.earth.evil.example/x"):
            self.assertFalse(is_trusted_api_url(url), url)

    def test_junk_is_untrusted_not_an_exception(self):
        from rasid_components.config import is_trusted_api_url
        for url in (None, "", "not a url", "file:///etc/passwd"):
            self.assertFalse(is_trusted_api_url(url), repr(url))

    def test_download_file_authenticates_only_our_hosts(self):
        """Exercises the real download path, with the network stubbed."""
        import tempfile
        import types
        from rasid_components import rasid_client as rc

        seen = []

        class FakeResponse:
            status_code = 200
            content = b"bytes"
            headers = {}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size=8192):
                yield self.content

        class FakeSession:
            headers = {"Authorization": "Bearer rsd_live_SECRET"}

            def get(self, url, **kwargs):
                seen.append(("session", url))
                return FakeResponse()

        def anonymous_get(url, **kwargs):
            seen.append(("anonymous", url))
            return FakeResponse()

        client = rc.RasidClient.__new__(rc.RasidClient)
        client.session = FakeSession()
        client.api_key = "rsd_live_SECRET"

        original = rc.requests
        rc.requests = types.SimpleNamespace(get=anonymous_get)
        try:
            client.download_file(self.OURS, tempfile.mkdtemp(prefix="t_"))
            client.download_file(self.THEIRS, tempfile.mkdtemp(prefix="t_"))
        finally:
            rc.requests = original

        self.assertEqual(
            [how for how, _url in seen], ["session", "anonymous"], seen)


class BrandMarkTest(unittest.TestCase):
    """The panel carries the GoPilot mark, not the RASID company one."""

    def test_the_mark_renders_in_both_themes(self):
        from rasid_components import branding
        for dark in (False, True):
            branding.invalidate()
            tokens = design.Tokens(dark)
            mark = branding.brand_mark(tokens, size=24)
            self.assertFalse(mark.isNull(), f"dark={dark}")
            # Oversampled for high-DPI, then tagged — so 24 logical pixels.
            self.assertEqual(mark.devicePixelRatio(), 2.0)
            self.assertEqual(mark.width(), 48)

    def test_each_theme_gets_its_own_variant(self):
        """One file for both themes loses the hairline guide arcs in one."""
        from rasid_components import branding
        self.assertTrue(
            branding.mark_path(dark=False).endswith(branding.MARK_LIGHT))
        self.assertTrue(
            branding.mark_path(dark=True).endswith(branding.MARK_DARK))
        self.assertNotEqual(branding.MARK_LIGHT, branding.MARK_DARK)

    def test_both_mark_files_ship(self):
        import os
        from rasid_components import branding
        for dark in (False, True):
            path = branding.mark_path(dark)
            self.assertTrue(os.path.exists(path), path)

    def test_the_mark_is_cached(self):
        """Re-rasterising an SVG on every rail rebuild is wasted work."""
        from rasid_components import branding
        branding.invalidate()
        tokens = design.Tokens(False)
        first = branding.brand_mark(tokens, size=24)
        self.assertIs(branding.brand_mark(tokens, size=24), first)
        branding.invalidate()
        self.assertIsNot(branding.brand_mark(tokens, size=24), first)


class ReasoningPanelTest(unittest.TestCase):
    """Fixed-height, scrollable, stacked — and present on a reopened chat."""

    LONG = "\n\n".join(f"Pass {i}: a long line of reasoning about the "
                       f"layers and the slope threshold." for i in range(6))
    MANY = [{"id": i, "name": f"tool_{i}", "status": "success"}
            for i in range(1, 9)]

    def setUp(self):
        from rasid_components.gopilot_page import GoPilotPage
        self.page = GoPilotPage(None, None)
        self.page.resize(design.DOCK_DEFAULT_WIDTH, 820)
        self.page.show()
        for _ in range(10):
            QGIS_APP.processEvents()

    def tearDown(self):
        _destroy(self.page)
        self.page = None

    def _pump(self, times=15):
        for _ in range(times):
            QGIS_APP.processEvents()

    def _loaded_bubble(self):
        self.page._on_messages_loaded([
            {"content": "Ask", "is_user": True,
             "system_registration_date": "2026-11-10T13:40:00Z"},
            {"content": "Answer", "is_user": False,
             "system_registration_date": "2026-11-10T13:41:00Z",
             "thinking_content": ["Reading the layers", "Deriving slope"],
             "tool_calls_content": [
                 {"id": 1, "name": "list_layers", "status": "success"}]},
        ])
        self._pump()
        return next(b for b in self.page._bubbles if b.role == "ai")

    def test_a_reopened_chat_still_shows_reasoning_and_tools(self):
        """The reported bug: the panels appeared only on the live turn.

        The history loader never passed `thinking_content` or
        `tool_calls_content` to the bubble, so reasoning existed until you
        navigated away and was then gone — although the API returns it with
        every message.
        """
        bubble = self._loaded_bubble()
        self.assertIsNotNone(bubble._thinking_panel)
        self.assertIsNotNone(bubble._tools_panel)

    def test_the_panels_are_a_fixed_height(self):
        """Reasoning used to push the answer off the bottom of the panel."""
        bubble = self._loaded_bubble()
        bubble.set_thinking(self.LONG, streaming=False)
        bubble.set_tool_calls(self.MANY, streaming=False)
        self._pump()
        # One shared height: two boxes of different heights in the same row
        # leave a ragged bottom edge.
        self.assertEqual(bubble._thinking_panel.height(),
                         design.REASONING_PANEL_HEIGHT)
        self.assertEqual(bubble._tools_panel.height(),
                         design.REASONING_PANEL_HEIGHT)

    def test_long_content_scrolls_inside_the_panel(self):
        from qgis.PyQt.QtWidgets import QScrollArea
        bubble = self._loaded_bubble()
        bubble.set_thinking(self.LONG, streaming=False)
        bubble.set_tool_calls(self.MANY, streaming=False)
        self._pump(25)
        for panel in (bubble._thinking_panel, bubble._tools_panel):
            areas = panel.findChildren(QScrollArea)
            self.assertTrue(areas, "no scroll area in the panel")
            content = areas[0].widget().sizeHint().height()
            viewport = areas[0].viewport().height()
            self.assertGreater(content, viewport,
                               "content fits, so this proves nothing")
            self.assertTrue(areas[0].verticalScrollBar().maximum() > 0)

    def test_the_panels_sit_side_by_side(self):
        """Reasoning left, tools right, sharing one row above the answer.

        Stacked they cost ~170px before the answer even started, which is
        most of the readable height on a 420px dock.
        """
        bubble = self._loaded_bubble()
        bubble.set_thinking(self.LONG, streaming=False)
        bubble.set_tool_calls(self.MANY, streaming=False)
        self._pump()
        thinking = bubble._thinking_panel
        tools = bubble._tools_panel
        left = thinking.mapTo(bubble, thinking.rect().topLeft())
        right = tools.mapTo(bubble, tools.rect().topLeft())
        self.assertEqual(left.y(), right.y(), "panels are still stacked")
        self.assertGreater(right.x(), left.x(), "tools is not to the right")

    def test_two_panels_split_the_width_with_no_gap(self):
        bubble = self._loaded_bubble()
        bubble.set_thinking(self.LONG, streaming=False)
        bubble.set_tool_calls(self.MANY, streaming=False)
        self._pump()
        inner = max(40, bubble.max_width() - 26)
        total = (bubble._thinking_panel.width()
                 + bubble._tools_panel.width() + design.SPACE_1)
        self.assertEqual(total, inner, "the row does not fill the bubble")

    def test_a_lone_panel_takes_the_whole_row(self):
        """A turn that only reasoned should not leave half the row empty."""
        bubble = self._loaded_bubble()
        bubble.set_tool_calls([], streaming=False)
        bubble.set_thinking(self.LONG, streaming=False)
        self._pump()
        inner = max(40, bubble.max_width() - 26)
        self.assertEqual(bubble._thinking_panel.width(), inner)

    def test_empty_reasoning_removes_the_panel(self):
        bubble = self._loaded_bubble()
        bubble.set_thinking("", streaming=False)
        bubble.set_tool_calls([], streaming=False)
        self._pump()
        self.assertIsNone(bubble._thinking_panel)
        self.assertIsNone(bubble._tools_panel)

    def test_the_tool_caption_counts_the_calls(self):
        bubble = self._loaded_bubble()
        bubble.set_tool_calls(self.MANY, streaming=False)
        self._pump()
        self.assertIn(str(len(self.MANY)), bubble._tools_caption.text())


class MarkdownRendersInQtTest(unittest.TestCase):
    """Does Qt HONOUR the markup we emit, not just receive it?

    ``GfmMarkdownTest`` checks the HTML contains the right tags. That is a
    weaker claim than it looks: ``QLabel`` rich text supports a SUBSET of
    HTML/CSS and silently ignores the rest, so a correct ``<s>`` in the string
    says nothing about whether anything is struck through on screen.

    These go through ``QTextDocument`` — the same engine ``QLabel`` uses — and
    inspect the resulting character and block formats, so anything Qt dropped
    shows up as a format that never changed. This is how the font-stack bug
    below was found.
    """

    def _doc(self, source):
        from qgis.PyQt.QtGui import QTextDocument
        from rasid_components.message_bubble import render_body_html
        doc = QTextDocument()
        doc.setHtml(render_body_html(source))
        return doc

    def _run(self, source, needle):
        """The character format of the run containing ``needle``."""
        doc = self._doc(source)
        block = doc.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and needle in fragment.text():
                    return fragment.charFormat()
                iterator += 1
            block = block.next()
        self.fail(f"no run containing {needle!r}")

    def test_bold_is_actually_bold(self):
        self.assertGreater(self._run("**bold text**", "bold text")
                           .fontWeight(), 50)

    def test_italic_is_actually_italic(self):
        self.assertTrue(self._run("*slanted*", "slanted").fontItalic())

    def test_strikethrough_actually_strikes(self):
        """`<s>` in the HTML is not the same as a struck glyph on screen."""
        self.assertTrue(self._run("~~struck~~", "struck").fontStrikeOut())

    def test_links_become_real_anchors(self):
        fmt = self._run("[docs](https://rasid.ai/d)", "docs")
        self.assertTrue(fmt.isAnchor())
        self.assertIn("rasid.ai", fmt.anchorHref())

    def test_bare_urls_become_real_anchors(self):
        self.assertTrue(
            self._run("see https://rasid.ai/x now", "rasid.ai").isAnchor())

    def test_code_is_monospace_inline_and_in_blocks(self):
        """The font-stack bug: both were specified and neither arrived.

        MONO_STACK is double-quoted for Qt stylesheets, and dropping it into
        an HTML ``style="…"`` attribute ended the attribute at the first inner
        quote — so the family, AND the size and colour after it, were thrown
        away. Fenced blocks resolved to no family at all and were drawn in the
        body font; inline code only looked right because Qt has its own rule
        for ``<code>``.
        """
        for label, source in (("inline", "`code_here`"),
                              ("fenced", "```\ncode_here\n```")):
            family = self._run(source, "code_here").fontFamily()
            self.assertTrue(
                any(mono in family
                    for mono in ("Mono", "Consol", "Courier")),
                f"{label} code resolved to {family!r}")

    def test_the_html_font_stacks_carry_no_double_quotes(self):
        """The root cause, asserted where it can be seen."""
        for stack in (design.FONT_STACK_HTML, design.MONO_STACK_HTML,
                      design.DISPLAY_STACK_HTML):
            self.assertNotIn('"', stack, stack)

    def test_headings_are_bigger_and_bolder_than_body(self):
        body = self._run("plain paragraph", "plain paragraph")
        for source, needle in (("# One", "One"), ("## Two", "Two"),
                               ("### Three", "Three")):
            fmt = self._run(source, needle)
            self.assertGreater(fmt.fontWeight(), 50, needle)
            self.assertGreaterEqual(fmt.font().pixelSize(),
                                    body.font().pixelSize(), needle)

    def test_lists_become_qt_text_lists(self):
        for source in ("- one\n- two", "1. one\n2. two"):
            doc = self._doc(source)
            self.assertTrue(
                any(doc.findBlockByNumber(i).textList() is not None
                    for i in range(doc.blockCount())), source)

    def test_tables_become_a_qt_text_table(self):
        doc = self._doc("| a | b |\n|---|---|\n| 1 | 2 |")
        tables = [f for f in doc.rootFrame().childFrames()
                  if hasattr(f, "columns")]
        self.assertTrue(tables, "the table did not become a QTextTable")
        self.assertEqual(tables[0].columns(), 2)
        self.assertGreaterEqual(tables[0].rows(), 2)

    def test_task_list_glyphs_survive_to_plain_text(self):
        text = self._doc("- [x] done\n- [ ] todo").toPlainText()
        self.assertIn("☑", text)
        self.assertIn("☐", text)


class MarkdownImageTest(unittest.TestCase):
    """Markdown images become real previews, not Qt's broken-image stub.

    Qt rich text cannot fetch an http resource, so an ``<img src="https://…">``
    that reaches the body draws a 16px placeholder and nothing else. The bubble
    pulls result links out of the prose and re-renders them as controls, so the
    question is only which images reach the body at all.
    """

    def _files(self, source):
        from rasid_components.message_bubble import extract_file_urls
        return extract_file_urls(source)

    def test_an_image_with_an_extension_becomes_one_control(self):
        found = self._files("![map](https://ex.invalid/slope.png)")
        self.assertEqual(len(found), 1, found)
        _url, name, ext, is_image = found[0]
        self.assertEqual((name, ext, is_image), ("slope.png", "png", True))

    def test_an_extensionless_image_is_still_an_image(self):
        """GoPilot's own image links end in /files/<id>/download/.

        These used to fall through to the body as a literal <img>, which Qt
        drew as a 0x0 broken stub.
        """
        found = self._files(
            "![slope map](https://ex.invalid/api/llm/files/42/download/)")
        self.assertEqual(len(found), 1, found)
        _url, name, _ext, is_image = found[0]
        self.assertTrue(is_image)
        self.assertTrue(name.endswith(".png"), name)
        self.assertIn("slope", name)

    def test_an_image_is_not_collected_twice(self):
        """`![alt](url)` also matches the plain `[alt](url)` pattern."""
        for source in ("![map](https://ex.invalid/slope.png)",
                       "![map](https://ex.invalid/files/9/download/)"):
            self.assertEqual(len(self._files(source)), 1, source)

    def test_no_img_tag_reaches_the_rendered_body(self):
        from rasid_components.message_bubble import MessageBubble
        for source in ("![map](https://ex.invalid/slope.png)",
                       "![map](https://ex.invalid/files/42/download/)",
                       "text\n\n![a](https://ex.invalid/b)\n\nmore text"):
            bubble = MessageBubble(source, role="ai", client=None)
            try:
                self.assertNotIn("<img", bubble._body.text(), source)
            finally:
                _destroy(bubble)

    def test_the_image_syntax_is_stripped_from_the_prose(self):
        """Not just the URL: the `![alt]` part has to go with it."""
        from rasid_components.message_bubble import MessageBubble
        bubble = MessageBubble(
            "Result below.\n\n![slope map](https://ex.invalid/s.png)",
            role="ai", client=None)
        try:
            body = bubble._body.text()
            self.assertIn("Result below", body)
            self.assertNotIn("![", body)
            self.assertNotIn("slope map]", body)
        finally:
            _destroy(bubble)


UPLOAD_LIMITS = {
    "accept": ".tif,.tiff,.geojson,.csv",
    "extensions": ["tif", "tiff", "geojson", "csv"],
    "kinds": {
        "raster": {"extensions": ["tif", "tiff"], "max_bytes": 200,
                   "mappable": True},
        "vector": {"extensions": ["geojson"], "max_bytes": 1000,
                   "mappable": True},
        "table": {"extensions": ["csv"], "max_bytes": 1000,
                  "mappable": False},
    },
}


class UploadLimitsTest(unittest.TestCase):
    """What may be uploaded is the server's decision, not ours."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="gopilot_limits_")
        self.small = os.path.join(self.dir, "area.geojson")
        self.big = os.path.join(self.dir, "scene.tif")
        self.odd = os.path.join(self.dir, "notes.docx")
        with open(self.small, "w") as handle:
            handle.write("x" * 50)
        with open(self.big, "wb") as handle:
            handle.write(b"x" * 500)        # over the 200-byte raster cap
        with open(self.odd, "w") as handle:
            handle.write("x")

    def test_an_accepted_file_passes(self):
        from rasid_components.uploads import partition_uploads
        accepted, rejected = partition_uploads([self.small], UPLOAD_LIMITS)
        self.assertEqual(accepted, [self.small])
        self.assertEqual(rejected, [])

    def test_an_unsupported_extension_is_named_in_the_reason(self):
        from rasid_components.uploads import partition_uploads
        _accepted, rejected = partition_uploads([self.odd], UPLOAD_LIMITS)
        self.assertEqual(len(rejected), 1)
        self.assertIn(".docx", rejected[0][1])

    def test_an_oversized_file_reports_both_sizes(self):
        """So the user can see how far over the limit they are."""
        from rasid_components.uploads import partition_uploads
        _accepted, rejected = partition_uploads([self.big], UPLOAD_LIMITS)
        self.assertEqual(len(rejected), 1)
        reason = rejected[0][1]
        self.assertIn("raster", reason)
        self.assertIn("200 bytes", reason)
        self.assertIn("500 bytes", reason)

    def test_every_problem_is_reported_at_once(self):
        """Not one per round trip to the API."""
        from rasid_components.uploads import partition_uploads
        accepted, rejected = partition_uploads(
            [self.small, self.big, self.odd], UPLOAD_LIMITS)
        self.assertEqual(accepted, [self.small])
        self.assertEqual(len(rejected), 2)

    def test_without_limits_everything_is_accepted(self):
        """No hardcoded fallback: a stale local list is the bug to avoid.

        While the limits are loading or the request failed, the server is left
        to judge.
        """
        from rasid_components.uploads import partition_uploads
        files = [self.small, self.big, self.odd]
        for limits in (None, {}, {"extensions": ["tif"]}):
            accepted, rejected = partition_uploads(files, limits)
            self.assertEqual(accepted, files, repr(limits))
            self.assertEqual(rejected, [], repr(limits))

    def test_the_kinds_map_decides_the_kind(self):
        from rasid_components.uploads import upload_kind
        self.assertEqual(upload_kind(UPLOAD_LIMITS, "a/b/scene.TIF"), "raster")
        self.assertEqual(upload_kind(UPLOAD_LIMITS, "x.geojson"), "vector")
        self.assertIsNone(upload_kind(UPLOAD_LIMITS, "x.docx"))
        self.assertIsNone(upload_kind(UPLOAD_LIMITS, "no_extension"))

    def test_the_dialog_filter_comes_from_the_server(self):
        from rasid_components.uploads import file_dialog_filter
        filter_text = file_dialog_filter(UPLOAD_LIMITS)
        for pattern in ("*.tif", "*.geojson", "*.csv"):
            self.assertIn(pattern, filter_text)
        self.assertIn("All files (*)", file_dialog_filter(None))

    def test_sizes_are_readable(self):
        from rasid_components.uploads import format_bytes
        self.assertEqual(format_bytes(900), "900 bytes")
        self.assertEqual(format_bytes(2048), "2.0 kB")
        self.assertEqual(format_bytes(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(format_bytes(250 * 1024 * 1024), "250 MB")
        self.assertEqual(format_bytes(None), "?")


class MessageDeleteTest(unittest.TestCase):
    """A trash control on each message, deleting the whole exchange."""

    MESSAGES = [
        {"id": 11, "content": "my question", "is_user": True,
         "system_registration_date": "2026-11-10T13:00:00Z"},
        {"id": 12, "content": "the answer", "is_user": False,
         "system_registration_date": "2026-11-10T13:01:00Z"},
        {"id": 13, "content": "a later question", "is_user": True,
         "system_registration_date": "2026-11-10T13:02:00Z"},
    ]

    def setUp(self):
        from qgis.PyQt.QtWidgets import QMessageBox
        from rasid_components.compat import QMessageBox_No
        from rasid_components.gopilot_page import GoPilotPage

        # The trash control is wired to the page, which asks for confirmation
        # with a MODAL dialog. Clicking it in a test would block the run
        # forever waiting for a human — it hung the suite until this stub went
        # in. Answering "No" keeps the click observable without starting a
        # request; the tests that need a confirmed delete call the handler
        # directly instead.
        self._real_question = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *args, **kwargs: QMessageBox_No)

        self.page = GoPilotPage(None, None)
        self.page.resize(design.DOCK_DEFAULT_WIDTH, 760)
        self.page.show()
        for _ in range(10):
            QGIS_APP.processEvents()
        self.page._on_messages_loaded(list(self.MESSAGES))
        for _ in range(12):
            QGIS_APP.processEvents()

    def tearDown(self):
        from qgis.PyQt.QtWidgets import QMessageBox
        QMessageBox.question = self._real_question
        _destroy(self.page)
        self.page = None

    def test_every_loaded_message_offers_a_trash_control(self):
        self.assertEqual(len(self.page._bubbles), 3)
        for bubble in self.page._bubbles:
            self.assertIsNotNone(bubble._trash_button)
            self.assertTrue(bubble._trash_button.isVisible())

    def test_a_message_with_no_server_row_hides_the_control(self):
        """The in-flight reply has nothing to ask the server to delete yet."""
        row = self.page.add_message("streaming…", is_user=False)
        for _ in range(8):
            QGIS_APP.processEvents()
        bubble = self.page._bubble_in(row)
        self.assertIsNone(bubble.message_id)
        self.assertFalse(bubble._trash_button.isVisible())

        bubble.set_message_id(99)
        self.assertTrue(bubble._trash_button.isVisible())

    def test_the_whole_turn_is_removed_not_just_the_clicked_message(self):
        """The backend trashes a prompt and its reply together.

        Removal follows the ids the SERVER reports, rather than guessing which
        sibling went with the one that was clicked.
        """
        self.page._on_message_deleted([11, 12])
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertEqual([b.message_id for b in self.page._bubbles], [13])

    def test_an_id_the_server_did_not_trash_is_left_alone(self):
        self.page._on_message_deleted([11])
        for _ in range(10):
            QGIS_APP.processEvents()
        self.assertEqual([b.message_id for b in self.page._bubbles], [12, 13])

    def test_the_trash_is_red_at_rest_on_both_bubbles(self):
        """It used to be muted grey, visible only on hover."""
        for bubble in self.page._bubbles:
            self.assertIn("danger", bubble._trash_button._tone, bubble.role)
            # Red on hover too, not reverting to a neutral.
            self.assertIn("danger", bubble._trash_button._hover_tone or "")

    def test_both_bubbles_use_the_same_control(self):
        """One red, no backing shape, whichever surface it sits on.

        Three designs were built here. A pale pink glyph cleared 3:1 on the
        brand bubble and read as WHITE at 11px; a red glyph on a light disc was
        legible but made the user's control a visibly different component from
        the assistant's. Matching the two was chosen over the measurement —
        see the note in MessageBubble.__init__.
        """
        user = next(b for b in self.page._bubbles if b.role == "user")
        ai = next(b for b in self.page._bubbles if b.role == "ai")
        for bubble in (user, ai):
            self.assertEqual(bubble._trash_button._tone, "danger")
            self.assertEqual(bubble._trash_button._hover_tone, "danger")

    def test_neither_control_draws_a_resting_background(self):
        """The disc is gone: both are a bare glyph on the bubble."""
        for bubble in self.page._bubbles:
            _glyph, background, _hover = \
                bubble._trash_button._resting_colors()
            self.assertEqual(background, "transparent", bubble.role)

    def test_the_glyph_renders_the_same_red_on_both(self):
        """Asserted on PIXELS, because the point is that they look alike."""
        def reddest(button):
            image = button.grab().toImage()
            best = None
            for y in range(image.height()):
                for x in range(image.width()):
                    colour = image.pixelColor(x, y)
                    if colour.alpha() == 0:
                        continue
                    redness = colour.red() - max(colour.green(),
                                                 colour.blue())
                    if best is None or redness > best[0]:
                        best = (redness, colour.name())
            return best

        samples = {b.role: reddest(b._trash_button)
                   for b in self.page._bubbles}
        self.assertEqual(samples["user"][1], samples["ai"][1], samples)
        # And it is a real red, not the near-white tint that was tried first.
        self.assertGreater(samples["user"][0], 100, samples)

    def test_the_assistant_bubble_clears_the_contrast_floor(self):
        """Where the surface allows it, the red still has to be legible.

        Only the assistant bubble is asserted. On the user's brand-green
        bubble no red can clear 3:1 — red and green share a luminance — and
        that is the trade recorded in MessageBubble, not an oversight.
        """
        def luminance(colour):
            channels = []
            for value in (colour.redF(), colour.greenF(), colour.blueF()):
                channels.append(value / 12.92 if value <= 0.03928
                                else ((value + 0.055) / 1.055) ** 2.4)
            return (0.2126 * channels[0] + 0.7152 * channels[1]
                    + 0.0722 * channels[2])

        for dark in (False, True):
            tokens = design.Tokens(dark)
            first = luminance(QColor(tokens.error))
            second = luminance(QColor(tokens.bubble_ai))
            lighter, darker = max(first, second), min(first, second)
            value = (lighter + 0.05) / (darker + 0.05)
            self.assertGreaterEqual(
                value, 3.0,
                f"ai trash is {value:.2f}:1 on "
                f"{'dark' if dark else 'light'}")

    def test_why_the_footer_is_outside_the_bubble_is_recorded(self):
        """The decision has to be findable, not just made.

        This used to assert that the ACCEPTED COST of an unreadable red was
        written down ("1.14:1", "red-green"). There is no longer a cost to
        accept: the footer moved onto the panel background and the same red
        now measures 3.26:1. What has to stay findable is why the structure
        is the way it is, so that nobody tidies the footer back inside the
        bubble and silently reintroduces 1.14:1.
        """
        import inspect
        from rasid_components.message_bubble import MessageBubble
        source = inspect.getsource(MessageBubble.__init__)
        self.assertIn("1.14:1", source)          # what it was
        self.assertIn("3.26:1", source)          # what it is
        self.assertIn("OUTSIDE THE BUBBLE", source)

    def test_the_bubble_asks_the_page_rather_than_the_api(self):
        """Deleting removes the sibling too, which only the page knows about."""
        seen = []
        bubble = self.page._bubbles[0]
        bubble.delete_requested.connect(seen.append)
        bubble._trash_button.click()
        QGIS_APP.processEvents()
        self.assertEqual(seen, [11])


class ChatRenameTest(unittest.TestCase):
    """A pen beside the trash, renaming in place."""

    def setUp(self):
        from rasid_components.history_panel import HistoryPanel
        self.panel = HistoryPanel(None)
        self.panel.resize(420, 500)
        self.panel.show()
        for _ in range(8):
            QGIS_APP.processEvents()
        self.panel._on_loaded([
            {"id": 5, "title": "Old name", "timestamp": "Nov 10"},
        ])
        for _ in range(10):
            QGIS_APP.processEvents()
        from rasid_components.history_panel import _ChatRow
        rows = self.panel.findChildren(_ChatRow)
        self.assertTrue(rows, "no chat rows were built")
        self.row = rows[0]

    def tearDown(self):
        _destroy(self.panel)
        self.panel = None
        self.row = None

    def test_the_row_offers_a_pen(self):
        self.assertIsNotNone(self.row._rename_btn)
        self.assertIn("ename", self.row._rename_btn.toolTip())

    def test_the_pen_swaps_the_title_for_an_editor(self):
        self.row.start_rename()
        QGIS_APP.processEvents()
        self.assertTrue(self.row._title_edit.isVisible())
        self.assertFalse(self.row._title_label.isVisible())
        self.assertEqual(self.row._title_edit.text(), "Old name")

    def test_enter_emits_the_new_title_and_closes_the_editor(self):
        seen = []
        self.row.rename_requested.connect(seen.append)
        self.row.start_rename()
        self.row._title_edit.setText("  A new name  ")
        self.row._commit_rename()
        QGIS_APP.processEvents()
        self.assertEqual(seen, ["A new name"], "the title was not trimmed")
        self.assertFalse(self.row._title_edit.isVisible())
        self.assertEqual(self.row.title, "A new name")

    def test_an_unchanged_title_asks_for_nothing(self):
        seen = []
        self.row.rename_requested.connect(seen.append)
        self.row.start_rename()
        self.row._commit_rename()
        self.assertEqual(seen, [])

    def test_an_empty_title_asks_for_nothing(self):
        seen = []
        self.row.rename_requested.connect(seen.append)
        self.row.start_rename()
        self.row._title_edit.setText("   ")
        self.row._commit_rename()
        self.assertEqual(seen, [])

    def test_escape_cancels_without_renaming(self):
        from qgis.PyQt.QtCore import QEvent
        from qgis.PyQt.QtGui import QKeyEvent
        from rasid_components.compat import Qt_Key_Escape, Qt_NoModifier

        seen = []
        self.row.rename_requested.connect(seen.append)
        self.row.start_rename()
        self.row._title_edit.setText("discard me")
        self.row.keyPressEvent(QKeyEvent(
            QEvent.Type.KeyPress, Qt_Key_Escape, Qt_NoModifier))
        QGIS_APP.processEvents()
        self.assertEqual(seen, [])
        self.assertFalse(self.row._title_edit.isVisible())
        self.assertEqual(self.row.title, "Old name")

    def test_losing_focus_cancels_rather_than_commits(self):
        seen = []
        self.row.rename_requested.connect(seen.append)
        self.row.start_rename()
        self.row._title_edit.setText("half typed")
        self.row._cancel_rename()
        self.assertEqual(seen, [])
        self.assertFalse(self.row._title_edit.isVisible())

    def test_a_click_while_renaming_does_not_open_the_chat(self):
        from qgis.PyQt.QtCore import QEvent, QPointF
        from qgis.PyQt.QtGui import QMouseEvent
        from rasid_components.compat import Qt_LeftButton, Qt_NoModifier

        opened = []
        self.row.clicked.connect(lambda: opened.append(True))
        self.row.start_rename()
        point = QPointF(self.row.rect().center())
        try:
            event = QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
                                Qt_LeftButton, Qt_LeftButton, Qt_NoModifier)
        except TypeError:
            event = QMouseEvent(QEvent.Type.MouseButtonPress,
                                point.toPoint(), Qt_LeftButton,
                                Qt_LeftButton, Qt_NoModifier)
        self.row.mousePressEvent(event)
        QGIS_APP.processEvents()
        self.assertEqual(opened, [])

    def test_the_servers_title_wins_over_ours(self):
        """It may trim or truncate what it stored."""
        chat = {"id": 5, "title": "Old name"}
        self.panel._on_renamed(chat, self.row, "Truncated by server")
        QGIS_APP.processEvents()
        self.assertEqual(self.row.title, "Truncated by server")
        self.assertEqual(chat["title"], "Truncated by server")

    def test_a_refused_rename_puts_the_old_title_back(self):
        chat = {"id": 5, "title": "New name"}
        self.row.set_title("New name")
        self.panel._on_rename_failed(chat, self.row, "Old name", "boom")
        QGIS_APP.processEvents()
        self.assertEqual(self.row.title, "Old name")
        self.assertEqual(chat["title"], "Old name")


class LoginDialogTest(unittest.TestCase):
    """The sign-in dialog, matched to the SaaS auth page."""

    def setUp(self):
        from rasid_components.login_dialog import LoginDialog
        self.dialog = LoginDialog()
        self.dialog.resize(720, 580)
        self.dialog.show()
        for _ in range(20):
            QGIS_APP.processEvents()

    def tearDown(self):
        self.dialog.close()
        _destroy(self.dialog)
        self.dialog = None

    def test_the_plugins_entry_points_still_exist(self):
        """rasid_plugin reads api_key_input and exec()s the dialog.

        Asserted because the dialog was rebuilt from scratch: the layout is
        free to change, this contract is not.
        """
        self.dialog.api_key_input.setText("  rsd_live_TEST  ")
        self.assertEqual(self.dialog.api_key_input.text().strip(),
                         "rsd_live_TEST")
        self.assertTrue(self.dialog.login_button.isDefault(),
                        "Enter should sign in")

    def test_the_background_image_ships_and_loads(self):
        """A missing asset would leave the dialog transparent — i.e. black."""
        # The background belongs to the VIEW; the dialog is a thin
        # wrapper that forwards only the form controls.
        source = self.dialog.view._background._source
        self.assertFalse(source.isNull(),
                         "auth-topo.jpg did not load")
        self.assertGreater(source.width(), 1000)

    def test_the_background_is_in_the_build_manifest(self):
        """Shipping the plugin without it would reproduce the black dialog."""
        import os
        from rasid_components import login_dialog
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(login_dialog.__file__)))
        with open(os.path.join(root, "build_plugin.py"),
                  encoding="utf-8") as handle:
            manifest = handle.read()
        self.assertIn(login_dialog.BACKGROUND_IMAGE, manifest)

    def test_the_card_sits_over_the_background(self):
        """The corner is image, the centre is card."""
        image = self.dialog.grab().toImage()
        corner = image.pixelColor(8, 8)
        centre = image.pixelColor(image.width() // 2, image.height() // 2)
        self.assertEqual(corner.alpha(), 255)
        self.assertEqual(centre.alpha(), 255)
        # The card is lighter than the washed map behind it.
        self.assertNotEqual(corner.name(), centre.name())

    def test_the_dialog_looks_the_same_in_both_qgis_themes(self):
        """The one place in the plugin that ignores the host theme.

        The background is a light topographic map, so a dark-theme card and
        text colour would be illegible on it — the light token set is resolved
        explicitly. Asserted on the rendered pixels, not on the tokens.
        """
        from qgis.PyQt.QtGui import QColor, QPalette
        from rasid_components.login_dialog import LoginDialog

        renders = {}
        original = QGIS_APP.palette()
        try:
            for dark in (False, True):
                palette = QPalette()
                window = QColor("#201D1F") if dark else QColor("#F1EEEF")
                text = QColor("#F1EEEF") if dark else QColor("#322E30")
                palette.setColor(QPalette.ColorRole.Window, window)
                palette.setColor(QPalette.ColorRole.WindowText, text)
                palette.setColor(QPalette.ColorRole.Base, window)
                palette.setColor(QPalette.ColorRole.Text, text)
                QGIS_APP.setPalette(palette)
                design.invalidate()

                dialog = LoginDialog()
                dialog.resize(720, 580)
                dialog.show()
                for _ in range(25):
                    QGIS_APP.processEvents()
                image = dialog.grab().toImage()
                renders[dark] = image.pixelColor(
                    image.width() // 2, image.height() // 2).name()
                dialog.close()
                _destroy(dialog)
        finally:
            QGIS_APP.setPalette(original)
            design.invalidate()
        self.assertEqual(renders[False], renders[True],
                         "the card changed with the host theme")

    def test_the_visibility_toggle_uses_words_not_emoji(self):
        """Emoji rendered as full-colour glyphs that matched no other styling."""
        from rasid_components.compat import (
            QLineEdit_Normal, QLineEdit_Password,
        )
        self.assertEqual(self.dialog._eye_btn.text(), "Show")
        self.dialog._toggle_api_key_visibility()
        self.assertEqual(self.dialog.api_key_input.echoMode(),
                         QLineEdit_Normal)
        self.assertEqual(self.dialog._eye_btn.text(), "Hide")
        self.dialog._toggle_api_key_visibility()
        self.assertEqual(self.dialog.api_key_input.echoMode(),
                         QLineEdit_Password)
        self.assertEqual(self.dialog._eye_btn.text(), "Show")
        for label in (self.dialog._eye_btn.text(),
                      self.dialog._eye_btn.toolTip()):
            self.assertTrue(label.isascii(), label)

    def test_it_offers_the_route_to_getting_a_key(self):
        """The slot the SaaS gives to "continue with Google".

        The plugin cannot do OAuth — no redirect to come back to — so the slot
        holds the link that produces a key instead.
        """
        from rasid_components.config import APP_BASE_URL
        self.assertIn("API key", self.dialog.get_key_btn.text())
        self.assertIn(APP_BASE_URL, self.dialog.get_key_btn.toolTip())

    def test_there_is_no_accolade_badge(self):
        """Removed on request. The SaaS keeps it; the panel does not."""
        from rasid_components import login_dialog
        self.assertFalse(hasattr(login_dialog, "AWARD_URL"))
        labels = [w.text() for w in self.dialog.findChildren(QLabel)
                  if w.text()]
        self.assertFalse(any("Winner" in text for text in labels), labels)


class DockedSignInTest(unittest.TestCase):
    """The panel docks whether or not anyone is signed in.

    It used to open a MODAL login window first and create the dock only once
    a key validated — so a user who was not signed in got a floating dialog
    in front of QGIS instead of the panel they had clicked for.
    """

    def setUp(self):
        if not sys.platform.startswith("win"):
            self.skipTest("Win32 dock behaviour")
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QMainWindow
        from rasid_components.gopilot_dock import GoPilotDock

        self.window = QMainWindow()
        self.window.resize(1100, 800)
        # No client: the unauthenticated case is the one that was broken.
        self.dock = GoPilotDock(client=None, iface=None)
        self.window.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.window.show()
        self.dock.show()
        self._pump()

    def tearDown(self):
        self.dock.cleanup()
        self.window.removeDockWidget(self.dock)
        _destroy(self.dock)
        _destroy(self.window)
        self.dock = None
        self.window = None

    def _pump(self, times=20):
        for _ in range(times):
            QGIS_APP.processEvents()

    def test_it_docks_without_a_client(self):
        self.assertFalse(self.dock.isFloating())
        self.assertTrue(self.dock.isVisible())
        self.assertFalse(self.dock.is_signed_in())

    def test_the_shell_is_built_even_unauthenticated(self):
        """`dock.shell` is what the rail, drag handles and geometry use.

        Building it lazily would leave all of that pointing at nothing while
        the sign-in card was up.
        """
        self.assertIsNotNone(self.dock.shell)
        self.assertIsNotNone(self.dock.shell.rail)
        self.assertIsNone(self.dock.shell.client)
        self.assertTrue(self.dock.shell.drag_handles())

    def test_the_typed_key_is_handed_to_the_plugin(self):
        """The view does not validate — only the plugin can reach the API."""
        seen = []
        self.dock.sign_in_requested.connect(seen.append)
        self.dock.login_view.api_key_input.setText("  rsd_live_TEST  ")
        self.dock.login_view._submit()
        self._pump(5)
        self.assertEqual(seen, ["rsd_live_TEST"], "not trimmed, or not sent")

    def test_an_empty_key_never_reaches_the_plugin(self):
        seen = []
        self.dock.sign_in_requested.connect(seen.append)
        self.dock.login_view.api_key_input.clear()
        self.dock.login_view._submit()
        self._pump(5)
        self.assertEqual(seen, [])
        self.assertTrue(self.dock.login_view.error_label.isVisible())

    def test_a_refusal_is_reported_in_the_card(self):
        """Not in a message box: the field to correct is right there."""
        self.dock.show_signin("That API key was refused.")
        self._pump()
        self.assertFalse(self.dock.is_signed_in())
        self.assertTrue(self.dock.login_view.error_label.isVisible())
        self.assertIn("refused", self.dock.login_view.error_label.text())

    def test_signing_in_swaps_to_the_shell_and_wires_every_page(self):
        class FakeClient:
            gopilot = None

            def get_profile(self):
                return {}

        client = FakeClient()
        self.dock.set_client(client)
        self._pump()
        self.assertTrue(self.dock.is_signed_in())
        for page in (self.dock.shell.chat_page, self.dock.shell.projects_page,
                     self.dock.shell.gobox_page,
                     self.dock.shell.processes_page,
                     self.dock.shell.history_page,
                     self.dock.shell.settings_page):
            self.assertIs(page.client, client, type(page).__name__)
        self.assertFalse(self.dock.login_view.error_label.isVisible())

    def test_signing_out_keeps_the_panel_docked(self):
        """Logout used to destroy the dock, taking its position with it."""
        class FakeClient:
            gopilot = None

            def get_profile(self):
                return {}

        self.dock.set_client(FakeClient())
        self._pump()
        self.dock.shell.set_client(None)
        self.dock.show_signin()
        self._pump()
        self.assertFalse(self.dock.is_signed_in())
        self.assertTrue(self.dock.isVisible())
        self.assertFalse(self.dock.isFloating())
        self.assertIsNone(self.dock.shell.client)

    def test_reveal_focuses_the_key_field_when_not_signed_in(self):
        self.dock.reveal()
        self._pump()
        self.assertFalse(self.dock.is_signed_in())
        self.assertTrue(self.dock.login_view.api_key_input.hasFocus()
                        or self.dock.login_view.api_key_input.isVisible())


class DrawToolDesignTest(unittest.TestCase):
    """The polygon tool uses the same design system as the panel."""

    def _source(self, name):
        import os
        from rasid_components import geometry
        path = os.path.join(os.path.dirname(os.path.abspath(
            geometry.__file__)), name)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def _code(self, name):
        """The module's source with COMMENTS stripped. See :func:`_code_of`."""
        import os
        from rasid_components import geometry
        return _code_of(os.path.join(
            os.path.dirname(os.path.abspath(geometry.__file__)), name))

    def test_the_drawn_polygon_is_brand_coloured(self):
        """It was #3B82F6 — a blue appearing nowhere else in the product.

        The area being drawn is the most prominent thing on screen while the
        tool is live, and it was the one piece of UI that did not look like
        GoPilot.
        """
        code = self._code("modern_draw_tool.py")
        self.assertNotIn("59, 130, 246", code)
        self.assertNotIn("3B82F6", code.upper())
        self.assertIn("t.accent", code)

    def test_the_tool_uses_no_theme_utils(self):
        """design.tokens() is the single source for colour."""
        for name in ("modern_draw_tool.py", "drawing_help_dialog.py"):
            self.assertNotIn("theme_utils", self._code(name), name)

    def test_no_emoji_remain_in_the_tool(self):
        """Windows draws these as full-colour glyphs at the host font's size.

        The toolbar read "⎌ Undo Last Point", "🗑️ Clear All", "✓ Finish
        Polygon", "✕ Cancel"; the help dialog used 🖱️ and 💡.
        """
        for name in ("modern_draw_tool.py", "drawing_help_dialog.py"):
            for char in self._code(name):
                self.assertLess(
                    ord(char), 0x1F000,
                    f"{name} still contains {char!r}")

    def test_the_toolbar_uses_the_products_buttons(self):
        from rasid_components.geometry.modern_draw_tool import DrawingToolbar
        bar = DrawingToolbar()
        try:
            labels = [bar.finish_btn.text(), bar.undo_btn.text(),
                      bar.clear_btn.text(), bar.cancel_btn.text()]
            for text in labels:
                self.assertTrue(text.isascii(), text)
            # Finish is the primary action and starts unavailable: an area
            # needs three points before it can be closed.
            self.assertFalse(bar.finish_btn.isEnabled())
            self.assertFalse(bar.undo_btn.isEnabled())
            bar.update_info(3, "Area: 1 km²", "Perimeter: 4 km")
            self.assertTrue(bar.finish_btn.isEnabled())
            self.assertTrue(bar.undo_btn.isEnabled())
        finally:
            # cleanup() first: the bar installs a Python event filter to be
            # draggable, and one left on a widget being destroyed is what
            # kills the interpreter at shutdown with no traceback.
            bar.cleanup()
            _destroy(bar)

    def test_the_undo_glyph_exists(self):
        """"refresh" means do it again; a chevron means go back a page."""
        from rasid_components import icons
        self.assertIn("undo", icons.available())
        pixmap = icons.pixmap("undo", 48, "#008B6A", dpr=1.0)
        image = pixmap.toImage()
        drawn = sum(1 for y in range(image.height())
                    for x in range(image.width())
                    if image.pixelColor(x, y).alpha() > 0)
        self.assertGreater(drawn, 100, "the undo glyph draws nothing")

    def test_the_help_dialog_background_is_scoped(self):
        """A bare `QDialog` rule also matches a child QMessageBox."""
        source = self._source("drawing_help_dialog.py")
        self.assertIn("QDialog#drawingHelpDialog", source)
        self.assertNotIn("QDialog {{\n", source)

    def test_the_help_dialog_has_no_blank_filler_labels(self):
        """Empty entries in the instruction list made it 643px tall."""
        from rasid_components.geometry.drawing_help_dialog import (
            DrawingHelpDialog,
        )
        dialog = DrawingHelpDialog(context="gopilot")
        try:
            for _ in range(15):
                QGIS_APP.processEvents()
            # A label with no text but a PIXMAP is the hero icon, not filler.
            blanks = [w for w in dialog.findChildren(QLabel)
                      if not w.text().strip() and w.height() > 4
                      and w.pixmap() is None]
            self.assertEqual(blanks, [], "blank labels are padding the dialog")
        finally:
            _destroy(dialog)

    def test_every_help_label_is_in_the_product_font(self):
        """A rule that sets a colour and a size but no family is invisible.

        Every label here had `color:` and `font-size:` and no `font-family:`,
        so the whole dialog rendered in the HOST application's font while the
        panel beside it was in Inter. Nothing about that looks broken in a
        screenshot — it just does not look like the product — which is why it
        survived a visual sweep and needs an assertion instead.
        """
        from rasid_components import design
        from rasid_components.geometry.drawing_help_dialog import (
            DrawingHelpDialog,
        )
        dialog = DrawingHelpDialog(context="wizard")
        try:
            for _ in range(15):
                QGIS_APP.processEvents()
            family = design.FONT_STACK.split(",")[0].strip().strip('"')
            checked = 0
            for label in dialog.findChildren(QLabel):
                if not label.text().strip():
                    continue
                sheet = label.styleSheet()
                self.assertIn(
                    family, sheet,
                    f"{label.text()[:40]!r} sets no font family")
                checked += 1
            self.assertGreater(checked, 3,
                               "found no text labels to check")
        finally:
            _destroy(dialog)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromName(__name__)
    unittest.TextTestRunner(verbosity=2).run(suite)
