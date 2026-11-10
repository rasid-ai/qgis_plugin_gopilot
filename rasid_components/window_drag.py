"""Lets chosen widgets drag the window they live in, the way a caption does.

WHY THIS EXISTS
===============
The floating panel has no title bar. That is not a styling choice that could be
undone with a stylesheet — the bar was the OS caption, and removing it means
clearing ``WS_CAPTION`` on the window (see
``gopilot_dock._apply_floating_chrome``). Windows draws the caption AND
implements click-and-drag-to-move on it, so clearing the bit takes the move
behaviour with it.

Resizing survives, because that is a separate style bit (``WS_THICKFRAME``,
which the flags we use keep), and the OS still handles every edge and corner.
Moving is the only thing this file has to replace.

WHAT A CAPTION DOES THAT THIS COPIES
====================================
* Press and drag moves the window, at a constant grab offset, so the point
  under the cursor stays under the cursor.
* Double-click toggles the window's docked state — the same gesture Qt's own
  dock title bar uses, which is why :attr:`double_clicked` exists.
* The cursor changes over the draggable area, which is the only hint a user
  gets that an unlabelled strip is grabbable at all.

ATTACHED, NOT SUBCLASSED
========================
An event filter rather than a ``DragBar`` widget, because the areas that should
behave like a caption already exist and are already laid out: the nav rail's
empty space, the RASID mark at its head, the view-title strip. Making each of
them a subclass would mean three subclasses and three places to keep the
arithmetic right; filtering lets one object serve all of them, and adding a
fourth handle later is one ``attach`` call.

ONLY WHILE FLOATING
===================
Docked, the panel must not move itself — it belongs to QGIS's dock area and
dragging it around would fight the layout. The ``enabled`` predicate is checked
on every event rather than connected to a signal, so there is no state to go
stale when the panel is floated by a route this module never sees (QGIS's own
Panels menu, a programmatic ``setFloating``).
"""

from qgis.PyQt.QtCore import QObject, pyqtSignal

from .compat import (
    QEvent_Enter, QEvent_Leave, QEvent_MouseButtonDblClick,
    QEvent_MouseButtonPress, QEvent_MouseButtonRelease, QEvent_MouseMove,
    Qt_ArrowCursor, Qt_LeftButton, Qt_SizeAllCursor, mouse_global_pos,
)


class WindowDragFilter(QObject):
    """Makes every attached widget act as a drag handle for its window."""

    double_clicked = pyqtSignal()

    def __init__(self, enabled=None, parent=None):
        """``enabled`` is a predicate; dragging is ignored when it is false."""
        super().__init__(parent)
        self._enabled = enabled
        self._offset = None
        self._handles = []

    def attach(self, widget):
        """Make ``widget`` draggable. Safe to call with ``None``."""
        if widget is None or widget in self._handles:
            return
        widget.installEventFilter(self)
        self._handles.append(widget)

    def detach_all(self):
        """Uninstall from every handle. Must run before teardown.

        Leaving a Python event filter installed is not harmless at shutdown.
        Qt keeps delivering events to widgets while they are being destroyed,
        and each one is routed through :meth:`eventFilter` — a Python call. If
        the interpreter has begun finalising by then there is nothing left to
        call it, and the process dies without a traceback (exit 127, which is
        exactly how this surfaced: the test that installed the filter appeared
        to crash, while the real crash came later and took the buffered "ok"
        with it).

        Tolerates already-destroyed widgets: teardown order is not ours to
        choose, so a handle whose C++ side has gone is normal here, not a bug.
        """
        for widget in self._handles:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                pass          # C++ side already gone; nothing to uninstall
        self._handles = []
        self._offset = None

    def is_enabled(self):
        return True if self._enabled is None else bool(self._enabled())

    def is_dragging(self):
        return self._offset is not None

    def eventFilter(self, obj, event):
        kind = event.type()

        if kind == QEvent_Enter:
            # The hint that this strip is grabbable. Buttons sitting inside a
            # handle set their own cursor, so they keep the pointing hand.
            obj.setCursor(Qt_SizeAllCursor if self.is_enabled()
                          else Qt_ArrowCursor)
            return False

        if kind == QEvent_Leave:
            obj.setCursor(Qt_ArrowCursor)
            return False

        if not self.is_enabled():
            # Docked: leave the events alone entirely, so nothing below this
            # filter behaves differently depending on where the panel is.
            return False

        if kind == QEvent_MouseButtonPress and event.button() == Qt_LeftButton:
            window = obj.window()
            if window is None:
                return False
            # Against frameGeometry, not geometry: QWidget.move() positions a
            # top-level window by its FRAME, so measuring the grab offset from
            # the frame is what keeps the window from jumping by the frame
            # thickness on the first mouse move.
            self._offset = (mouse_global_pos(event)
                            - window.frameGeometry().topLeft())
            return True

        if kind == QEvent_MouseMove and self._offset is not None:
            window = obj.window()
            if window is not None:
                window.move(mouse_global_pos(event) - self._offset)
            return True

        if kind == QEvent_MouseButtonRelease:
            was_dragging = self._offset is not None
            self._offset = None
            return was_dragging

        if (kind == QEvent_MouseButtonDblClick
                and event.button() == Qt_LeftButton):
            # Matches the gesture Qt's own dock title bar carries, so the
            # muscle memory from every other QGIS panel still works.
            self._offset = None
            self.double_clicked.emit()
            return True

        return False
