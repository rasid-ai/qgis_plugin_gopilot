"""Resize handles for the frameless floating panel.

WHY NOT THE OS, AND WHY NOT QSizeGrip
=====================================
The floating panel is frameless so that no bar or coloured border appears
around it. Removing the frame removes ``WS_THICKFRAME``, and with it the OS
resize border on every edge — so the panel has to grow its own.

``QSizeGrip`` is the obvious candidate and does not work here. It hands off to
the platform's modal move/size loop, which means: it only resizes from one
corner, it cannot be driven by synthetic events (so nothing about it can be
regression-tested), and the first attempt to exercise it blocked the test
process waiting for real mouse input. The arithmetic below is a dozen lines,
covers all eight directions, and is testable.

HOW IT WORKS
============
Eight thin widgets are parented to the panel window and positioned around its
inside edge — four edges and four corners. They are invisible (no paint, no
background) but they are real widgets, so they sit above the content and get
the mouse events the content would otherwise swallow. Each one knows which
edges it moves, and a drag converts cursor movement into a new window
geometry.

Anchoring is the part worth getting right: dragging a LEFT or TOP edge changes
the window's position as well as its size, and doing that by applying a delta
to the current geometry on every mouse-move accumulates rounding and fights
the minimum size. So the press records the geometry once, and every move
computes the new rectangle from that original — a drag is therefore idempotent
and clamping at the minimum size cannot drift the opposite edge.
"""

from qgis.PyQt.QtCore import QObject, QRect
from qgis.PyQt.QtWidgets import QWidget

from .compat import (
    Qt_LeftButton, Qt_SizeBDiagCursor, Qt_SizeFDiagCursor, Qt_SizeHorCursor,
    Qt_SizeVerCursor, mouse_global_pos,
)

# Thickness of the grab zones, in px. Wide enough to hit without aiming, since
# they are invisible; narrow enough not to steal clicks from the content. The
# rail's buttons are inset further than this, so nothing of ours is covered.
ZONE = 5

# (name, left, top, right, bottom, cursor) — the flags say which edges a drag
# on that zone moves.
ZONES = (
    ("left",         True,  False, False, False, Qt_SizeHorCursor),
    ("right",        False, False, True,  False, Qt_SizeHorCursor),
    ("top",          False, True,  False, False, Qt_SizeVerCursor),
    ("bottom",       False, False, False, True,  Qt_SizeVerCursor),
    ("top-left",     True,  True,  False, False, Qt_SizeFDiagCursor),
    ("top-right",    False, True,  True,  False, Qt_SizeBDiagCursor),
    ("bottom-left",  True,  False, False, True,  Qt_SizeBDiagCursor),
    ("bottom-right", False, False, True,  True,  Qt_SizeFDiagCursor),
)


class _ResizeZone(QWidget):
    """One invisible grab strip along an edge or corner of the window."""

    def __init__(self, window, edges, cursor, parent):
        super().__init__(parent)
        self._window = window
        self._left, self._top, self._right, self._bottom = edges
        self.setCursor(cursor)
        self._origin = None      # cursor position at press, in screen coords
        self._geometry = None    # window geometry at press

    def mousePressEvent(self, event):
        if event.button() != Qt_LeftButton:
            return
        self._origin = mouse_global_pos(event)
        self._geometry = QRect(self._window.geometry())

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        delta = mouse_global_pos(event) - self._origin
        rect = QRect(self._geometry)

        # Minimums come from the window itself, so the panel's own
        # DOCK_MIN_WIDTH is honoured without this module knowing about it.
        min_w = max(self._window.minimumWidth(),
                    self._window.minimumSizeHint().width(), 1)
        min_h = max(self._window.minimumHeight(),
                    self._window.minimumSizeHint().height(), 1)

        if self._left:
            # Clamped against the RIGHT edge, which must not move: let the left
            # edge travel at most far enough to leave the minimum width.
            rect.setLeft(min(rect.left() + delta.x(), rect.right() - min_w))
        if self._right:
            rect.setRight(max(rect.right() + delta.x(), rect.left() + min_w))
        if self._top:
            rect.setTop(min(rect.top() + delta.y(), rect.bottom() - min_h))
        if self._bottom:
            rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + min_h))

        self._window.setGeometry(rect)

    def mouseReleaseEvent(self, event):
        self._origin = None
        self._geometry = None

    def is_dragging(self):
        return self._origin is not None


class ResizeFrame(QObject):
    """The eight zones, kept positioned around ``window``.

    Created once and switched on only while the window is frameless and
    floating: docked, the panel is inside QGIS's layout and QGIS owns its size,
    so a grab strip there would fight the splitter it sits next to.
    """

    def __init__(self, window, host=None, parent=None):
        super().__init__(parent)
        self._window = window
        self._host = host if host is not None else window
        self._zones = {}
        self._active = False

        for name, left, top, right, bottom, cursor in ZONES:
            zone = _ResizeZone(window, (left, top, right, bottom), cursor,
                               self._host)
            zone.hide()
            self._zones[name] = zone

    def zone(self, name):
        return self._zones.get(name)

    def is_active(self):
        return self._active

    def is_dragging(self):
        return any(z.is_dragging() for z in self._zones.values())

    def set_active(self, active):
        self._active = bool(active)
        for zone in self._zones.values():
            zone.setVisible(self._active)
        if self._active:
            self.relayout()

    def relayout(self):
        """Reposition the zones. Call from the host's ``resizeEvent``."""
        if not self._active:
            return
        width = self._host.width()
        height = self._host.height()
        z = ZONE
        geometry = {
            "left":         (0, z, z, max(0, height - 2 * z)),
            "right":        (width - z, z, z, max(0, height - 2 * z)),
            "top":          (z, 0, max(0, width - 2 * z), z),
            "bottom":       (z, height - z, max(0, width - 2 * z), z),
            "top-left":     (0, 0, z, z),
            "top-right":    (width - z, 0, z, z),
            "bottom-left":  (0, height - z, z, z),
            "bottom-right": (width - z, height - z, z, z),
        }
        for name, (x, y, w, h) in geometry.items():
            zone = self._zones[name]
            zone.setGeometry(x, y, w, h)
            # Above the content, or the shell underneath takes the mouse.
            zone.raise_()
