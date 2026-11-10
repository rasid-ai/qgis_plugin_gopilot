"""The dock that hosts the GoPilot panel.

DOCKED, NOT MODAL
=================
GoPilot used to open as a 1087x500 modal dialog run under ``exec()``. That is
the wrong shape for this product in two ways:

* **Modal blocked QGIS.** While the panel was open the main window was inert —
  you could not pan to the area you were asking about, toggle a layer, or check
  a result. The panel even had to MINIMISE ITSELF to let the user draw an area,
  then restore afterwards.
* **A floating window covers the map.** GoPilot's whole value is that it talks
  about what is on your canvas, so it needs to sit BESIDE the canvas, in the
  same place QGIS keeps Layers, Browser and Processing.

So the panel is a dock in the right-hand area. Undocking is still one click
away, from the nav rail, for users on a second monitor who want it large.

NO TITLE BAR WHILE DOCKED
=========================
Docked, the panel has no title bar — not QGIS's, and not a branded one of our
own (an earlier revision tried that and merely duplicated the wordmark). The
content starts at y=0 and the nav rail carries what the bar used to: the
dock/undock toggle and close. The brand appears where the user is actually
looking instead: the RASID mark at the head of the rail, the "GoPilot by
RASID" wordmark across the chat's empty state.

NOR WHILE FLOATING
==================
Floating, the bar was the OS CAPTION, not anything Qt drew: a floating
``QDockWidget`` on Windows gets native decorations (measured: a 31px top frame
and ``WS_CAPTION`` set). ``setTitleBarWidget`` cannot remove that — it only
governs Qt's own in-window bar — so the panel used to come out of the dock
wearing a grey system bar with a second close button on it, duplicating the
rail's.

:meth:`_apply_floating_chrome` clears the caption through window flags
instead, and keeps the OS resize border; :mod:`.window_drag` puts back the one
thing the caption also provided, drag-to-move.

WHAT THIS COSTS
===============
Dragging the panel into a DIFFERENT dock area, or onto another panel to tab
with it, is gone: that gesture belongs to Qt's own title bar, and there is no
longer one to grab in either state. The rail's dock/undock button covers the
common case, double-clicking a drag handle re-docks, and QGIS's Panels menu
still shows and hides the panel.

Moving the floating WINDOW is not lost, only reimplemented — see
:mod:`.window_drag` — and resizing it was never lost, because the OS resize
border survives the flags that drop the caption.

Also gone: the system close button and Alt+Space's window menu. The rail's X is
the close affordance now, and it was already there, which is what made the
system one a duplicate rather than a second option.

Qt's floating geometry also does not survive a re-dock on its own — it
recomputes from the docked size — so :meth:`_on_top_level_changed` keeps it.
That hangs off the signal rather than off a button so it applies no matter how
the panel was floated.

WHY QgsDockWidget
=================
QGIS's own subclass, when it is importable. It adds ``setUserVisible()`` and
the ``closed`` signal, and it is what every built-in panel uses, so GoPilot
tabs correctly when a user drags it onto another dock. It falls back to plain
``QDockWidget`` so the module stays importable outside a QGIS session (the
offscreen smoke test in ``scripts/smoke_ui.py``).

CLOSING IS NOT DESTROYING
=========================
The X on the title bar HIDES the dock; the shell, its views and any in-flight
request stay alive, so reopening is instant and an answer that lands while the
panel is hidden is still there when it comes back. Teardown happens only in
``RasidPlugin.unload()``, which is also the only place QGIS guarantees it is
safe to delete the widget.
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import QStackedWidget, QWidget

from . import design
from .compat import (
    QDockWidget_Closable, QDockWidget_Floatable, QDockWidget_Movable,
    Qt_AllDockWidgetAreas, Qt_FramelessWindowHint, Qt_Tool,
)
from .gopilot_shell import GoPilotShell
from .login_dialog import LoginView
from .logger import debug_print
from .widgets import paint_background
from .window_drag import WindowDragFilter
from .window_resize import ResizeFrame

try:
    from qgis.gui import QgsDockWidget as _DockBase
    _HAVE_QGS_DOCK = True
except ImportError:  # pragma: no cover - non-QGIS contexts only
    from qgis.PyQt.QtWidgets import QDockWidget as _DockBase
    _HAVE_QGS_DOCK = False

DOCK_TITLE = "GoPilot by RASID"

# Window flags for the floating panel. Measured on Windows 11 against both
# QGIS 3.36 (Qt 5.15) and 4.0.1 (Qt 6.8), reading GWL_STYLE directly:
#
#   Qt's own floating flags  WS_CAPTION set, WS_THICKFRAME set
#   + CustomizeWindowHint    WS_CAPTION cleared, WS_THICKFRAME set
#   + FramelessWindowHint    both cleared, frame insets 0 on every side
#
# The middle one removes the title bar and keeps the OS resize border, and was
# the first fix. It was not enough: Windows PAINTS that border, in the user's
# accent colour and thicker along the top, so the panel still had a coloured
# strip above it. No title and no buttons, but indistinguishable from a bar at
# a glance — which is what it got reported as.
#
# So: frameless. Nothing is drawn around the panel at all, and the two things
# the frame was still providing are replaced in kind — window_drag for moving
# (the caption's job) and window_resize for resizing (the border's).
#
# Qt_Tool carries the window/dialog/popup type bits already (it is 0xb, not a
# standalone flag), so it does not need Qt_Window beside it; keeping the panel
# a Tool window is also what holds it above the QGIS window and out of the
# taskbar, which is how every other QGIS panel behaves when floated.
FLOATING_FLAGS = Qt_Tool | Qt_FramelessWindowHint
# Deliberately stable: QGIS persists dock geometry and tab position against the
# object name, so changing it would orphan every existing user's layout.
DOCK_OBJECT_NAME = "RasidGoPilotDock"


class GoPilotDock(_DockBase):
    """The right-hand GoPilot panel."""

    closed = pyqtSignal()
    # An API key was typed into the docked sign-in card. The plugin owns
    # validation: the dock has no opinion on what a good key looks like.
    sign_in_requested = pyqtSignal(str)

    def __init__(self, client=None, iface=None, parent=None):
        super().__init__(DOCK_TITLE, parent)
        self.setObjectName(DOCK_OBJECT_NAME)
        self.iface = iface
        # Geometry bookkeeping for floating; see _on_top_level_changed. Set
        # before any widget exists, because resizeEvent can fire during
        # construction and would otherwise hit a missing attribute.
        self._floating_geometry = None   # live, while floating
        self._restore_geometry = None    # what to restore on the next undock

        self.setAllowedAreas(Qt_AllDockWidgetAreas)
        self.setFeatures(
            QDockWidget_Movable | QDockWidget_Floatable | QDockWidget_Closable
        )

        # Two pages: the sign-in card and the panel proper.
        #
        # The panel DOCKS whether or not anyone is signed in. It used to open a
        # modal login window first and create the dock only once a key
        # validated, so an unauthenticated user got a floating dialog in front
        # of QGIS instead of the panel they had just asked for.
        #
        # The shell is built either way — with ``client=None`` if there is no
        # credential yet — because `dock.shell` is what the rail, the drag
        # handles and the geometry memory are all wired to. It receives the
        # client later through ``GoPilotShell.set_client``.
        self.shell = GoPilotShell(client=client, iface=iface)
        self.login_view = LoginView()
        self.login_view.sign_in_requested.connect(
            self.sign_in_requested.emit)

        self._pages = QStackedWidget()
        self._pages.addWidget(self.login_view)
        self._pages.addWidget(self.shell)
        self.setWidget(self._pages)
        self.setMinimumWidth(design.DOCK_MIN_WIDTH)
        self._pages.setCurrentWidget(
            self.shell if client is not None else self.login_view)

        # Paint the dock itself the same canvas colour as the shell inside it.
        # Floating, Qt insets its widget by the style's dock frame width — 3px
        # left, 2px top here — and whatever is behind shows through as a rim
        # around the panel. That rim is the palette's Window colour, so in dark
        # mode it came out #201D1F against the shell's #141213: a visible pale
        # border on three sides of the floating panel. (Light mode hid it, the
        # two colours being identical there, which is why it had to be sampled
        # rather than looked at.) Docked the inset is zero and this is inert.
        paint_background(self, design.tokens().canvas, DOCK_OBJECT_NAME)

        # No title bar at all, in either state — see _sync_chrome. The controls
        # it used to carry now live in the nav rail.
        self._blank_title_bar = QWidget()
        self._blank_title_bar.setFixedHeight(0)

        self.shell.rail.float_toggle_requested.connect(self.toggle_floating)
        self.shell.rail.close_requested.connect(self.close)

        # Drag-to-move, standing in for the caption removed below. The
        # predicate is `isFloating`, so the handles are inert while docked.
        self._drag = WindowDragFilter(enabled=self.isFloating, parent=self)
        self._drag.double_clicked.connect(self.toggle_floating)
        for handle in self.shell.drag_handles():
            self._drag.attach(handle)

        # Resize zones, standing in for the OS border the frameless window no
        # longer has. Parented to the dock rather than the shell so they are
        # above the shell's content and get the mouse first.
        self._resize = ResizeFrame(self, host=self, parent=self)

        self.topLevelChanged.connect(self._on_top_level_changed)
        self._sync_chrome()
        self.shell.rail.set_floating(self.isFloating())

    # ── dock / undock ─────────────────────────────────────────────────────────
    def toggle_floating(self):
        """Float the dock, or put it back in the dock area.

        Kept as a public method even though QGIS's title bar now provides the
        control, because the geometry handling hangs off ``topLevelChanged``
        and so applies however the state was changed — this method, QGIS's
        float button, a double-click, or a drag.
        """
        self.setFloating(not self.isFloating())

    def _sync_chrome(self):
        """No title bar in either state — Qt's while docked, the OS's while not.

        The blank zero-height widget deals with Qt's own in-window bar, which
        is the only one that exists while docked. It stays set while floating
        too: Qt would otherwise put its bar back inside the window, under the
        caption we are about to remove, and the panel would have two.

        The caption itself is not Qt's to give or take, so it needs
        :meth:`_apply_floating_chrome`.
        """
        if self.titleBarWidget() is not self._blank_title_bar:
            self.setTitleBarWidget(self._blank_title_bar)
        if self.isFloating():
            self._apply_floating_chrome()
        # Only while floating: docked, QGIS owns the panel's size and a grab
        # strip down the left edge would fight the dock-area splitter beside
        # it. Guarded with getattr because showEvent can reach this during
        # construction, before the frame exists.
        resize = getattr(self, "_resize", None)
        if resize is not None:
            resize.set_active(self.isFloating())

    def _apply_floating_chrome(self):
        """Clear the OS caption on the floating window, keeping its resize border.

        See ``FLOATING_FLAGS`` for the measurements. Three details matter:

        * **The guard is not an optimisation.** ``setWindowFlags`` un-maps a
          visible window on Windows, so the ``show()`` below is mandatory — and
          ``show()`` delivers a ``showEvent``, which calls straight back into
          here. Returning early when the flags already match is what stops that
          from recursing.
        * **Geometry is restored around the call**, because re-applying flags
          re-asks the window manager to position the window and it does not
          reliably come back where it was.
        * **It only ever runs while floating.** Docked, the dock is a child
          widget inside QGIS's main window and has no window flags worth
          setting; Qt clears them itself on the way back in.
        """
        if self.windowFlags() == FLOATING_FLAGS:
            return
        geometry = self.geometry()
        self.setWindowFlags(FLOATING_FLAGS)
        self.setGeometry(geometry)
        self.show()

    def _on_top_level_changed(self, floating):
        """Restore the size the panel last floated at.

        Qt does NOT preserve a floating dock's geometry across a re-dock: on
        the way back out it derives a new size from the DOCKED size, so a panel
        the user sized to 520x500 came back 650x625. Remembering it here —
        rather than in :meth:`toggle_floating` — is what makes it work for
        QGIS's own float button too, which calls ``setFloating`` directly and
        never passes through our code.
        """
        self._sync_chrome()
        self.shell.rail.set_floating(floating)

        if not floating:
            # Becoming docked: whatever it was last floating at is what to
            # restore next time. Safe to read now because
            # `_remember_floating_geometry` stops updating the moment
            # isFloating() goes false, so this still holds the floating value.
            self._restore_geometry = self._floating_geometry
            return

        if self._restore_geometry is not None:
            self.setGeometry(self._restore_geometry)
            return

        # Never floated before, so there is nothing to restore. A dock pops out
        # at its docked width, which is too narrow to be worth undocking for —
        # give it a window-sized default this once.
        self.resize(design.DOCK_DEFAULT_WIDTH + 180, 760)
        self._center_on_parent()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._remember_floating_geometry()
        # The zones are positioned by hand, not by a layout — the dock's own
        # layout manages the title bar and the shell and nothing else — so
        # every size change has to move them.
        resize = getattr(self, "_resize", None)
        if resize is not None:
            resize.relayout()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._remember_floating_geometry()

    def _remember_floating_geometry(self):
        """Track the geometry continuously while the panel is floating.

        On every move/resize rather than at the moment of docking, because by
        the time ``topLevelChanged(False)`` arrives Qt has already resized the
        widget to its docked size. The ``isFloating()`` guard is what keeps
        that docked size out of the remembered value.
        """
        if self.isFloating():
            self._floating_geometry = self.geometry()

    def _center_on_parent(self):
        """Put a freshly floated dock over the QGIS window, not off-screen."""
        try:
            main_window = (self.iface.mainWindow() if self.iface
                           else self.parent())
            if main_window is None:
                return
            geo = main_window.geometry()
            own = self.frameGeometry()
            self.move(
                geo.center().x() - own.width() // 2,
                max(geo.top() + 40, geo.center().y() - own.height() // 2),
            )
        except Exception as exc:
            # Positioning is cosmetic; never let it stop the undock.
            debug_print(f"[GoPilot] Could not position floating dock: {exc}")

    # ── visibility ────────────────────────────────────────────────────────────
    def reveal(self):
        """Show and raise the dock, whichever state it is in.

        ``setUserVisible`` is the QGIS-aware version: if the dock is tabbed
        behind another panel it brings its tab to the front, which plain
        ``show()`` does not.
        """
        if _HAVE_QGS_DOCK:
            try:
                self.setUserVisible(True)
            except Exception as exc:
                debug_print(f"[GoPilot] setUserVisible failed: {exc}")
                self.show()
        else:
            self.show()
        self.raise_()
        # Focus whatever the user actually needs to type into.
        if self.is_signed_in():
            focus = getattr(self.shell, "chat_page", None)
            if focus is not None and hasattr(focus, "composer"):
                focus.composer.focus_input()
        else:
            self.login_view.focus_input()

    def showEvent(self, event):
        super().showEvent(event)
        # Re-sync, because `topLevelChanged` does NOT fire when a dock widget
        # is placed into a dock area by `addDockWidget()`. The constructor runs
        # before that call, and a dock widget with no main window reports
        # itself as FLOATING — so the constructor's sync left the NATIVE bar in
        # place, and nothing afterwards took it away: the panel opened with
        # QGIS's 22px title bar still on it.
        self._sync_chrome()
        self.shell.rail.set_floating(self.isFloating())

    def closeEvent(self, event):
        # Hide only — see the module docstring. Threads and view state survive.
        self.closed.emit()
        super().closeEvent(event)

    def is_signed_in(self):
        """Is the panel showing the product, or the sign-in card?"""
        return self._pages.currentWidget() is self.shell

    def set_client(self, client):
        """Hand a validated client to the shell and show it."""
        self.shell.set_client(client)
        self._pages.setCurrentWidget(self.shell)
        self.login_view.set_busy(False)
        self.login_view.clear_error()

    def show_shell(self):
        """Show the panel proper, without changing who is signed in.

        The counterpart to :meth:`show_signin`. Needed on its own because the
        two pages have DIFFERENT backgrounds — the sign-in card paints the
        topographic image to the panel's edges, the shell paints the canvas —
        so anything reasoning about the panel's own pixels has to be able to
        say which page it means.
        """
        self._pages.setCurrentWidget(self.shell)

    def show_signin(self, message=""):
        """Go back to the sign-in card — on logout, or a refused key."""
        self._pages.setCurrentWidget(self.login_view)
        self.login_view.set_busy(False)
        if message:
            self.login_view.show_error(message)
        else:
            self.login_view.clear_error()
        self.login_view.focus_input()

    def cleanup(self):
        """Tear the shell down. Called from the plugin's ``unload()`` only."""
        # Before the shell, and outside its try block: the drag filter is a
        # Python callback sitting in Qt's event path for three widgets that are
        # about to be destroyed, and an event delivered to one of them after
        # the interpreter starts finalising kills the process outright. See
        # WindowDragFilter.detach_all.
        drag = getattr(self, "_drag", None)
        if drag is not None:
            try:
                drag.detach_all()
            except Exception as exc:
                debug_print(f"[GoPilot] Drag filter detach failed: {exc}")

        try:
            self.shell.cleanup()
        except Exception as exc:
            debug_print(f"[GoPilot] Shell cleanup failed: {exc}")
