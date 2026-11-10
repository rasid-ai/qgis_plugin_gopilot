"""The GoPilot panel: a view-title strip, the stacked views, and the nav rail.

    ┌──────────────────────────────────────┬────┐
    │  Projects                            │ ◎  │  mark + view-title strip,
    ├──────────────────────────────────────┤────│  one rule across both
    │                                      │ ⊞  │  new chat
    │   the current view                   │ 🗀  │  projects
    │   (chat / projects / GoBox /         │ ▣  │  GoBox
    │    processes / history / settings)   │ ↺  │  history
    │                                      │ ⚙  │  settings
    │                                      │────│
    │                                      │ ⇱  │  undock
    │                                      │    │
    ├──────────────────────────────────────┤────│  the composer's top border
    │  [+]  Ask something about Earth  [→] │ ✕  │  and the footer rule are
    └──────────────────────────────────────┴────┘  one line; + → ✕ one row

WHY A SHELL AT ALL
==================
Each view used to be a page in a modal dialog with a 190px labelled sidebar of
its own, and the chat page carried a second sidebar inside that. Docked at
~420px, two levels of chrome ate more than half the width. One shell owning the
header and the rail means a view is now just its content.

THE RAIL IS ON THE RIGHT
========================
Against the panel's outer edge, so the icons sit next to the QGIS window frame
rather than between the map and the conversation. (The sketch drew it on the
left; right is what was asked for, and it keeps the reading order
content-then-controls.)

PROCESSES HAS NO RAIL BUTTON
============================
It is reached by opening a project or creating one from a GoBox solution — a
drill-down, not a destination. While it is showing, the rail highlights
Projects, which is where Back goes.
"""

from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget,
)

from . import design
from .widgets import paint_background, paint_transparent
from .compat import exec_dialog
from .feedback_dialog import FeedbackDialog
from .gopilot_page import GoPilotPage
from .history_panel import HistoryPanel
from .logger import debug_print
from .nav_rail import NavRail
from .processes_page import ProcessesPage
from .projects_page import ProjectsPage
from .settings_page import SettingsPage
from .solutions_page import SolutionsPage

# Title shown in the view-header strip. None means the view names itself and
# the strip stays hidden — see GoPilotShell._build_view_header.
VIEW_TITLES = {
    "chat": None,
    "projects": "Projects",
    "gobox": "GoBox",
    "processes": None,
    "history": "Chat history",
    "settings": "Settings",
}


class FetchProfileThread(QThread):
    """Load the signed-in user's profile and their GoToken wallet.

    Two requests, because they are two endpoints — the balance is not part of
    the profile response (see ``RasidClient.get_gotokens``).

    The wallet is fetched in its own try block so a billing hiccup cannot blank
    out the name and email as well; the account card then simply shows no
    balance rather than a wrong one.
    """

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            data = self.client.get_profile()
        except Exception as exc:
            self.error.emit(str(exc))
            return

        if not isinstance(data, dict):
            data = {}
        try:
            data["gotokens"] = self.client.get_gotokens()
        except Exception as exc:
            # Recorded, not swallowed. Three outcomes have to stay telling
            # apart downstream: a real balance, a user with no wallet, and a
            # request that did not come back. Only the last one is an error,
            # and the card says so rather than showing a figure it never got.
            debug_print(f"[RASID] GoToken wallet fetch failed: {exc}")
            data["gotokens_error"] = str(exc)
        self.finished.emit(data)


class GoPilotShell(QWidget):
    """Everything inside the dock. Hosts the views, owns their wiring."""

    logout_requested = pyqtSignal()

    def __init__(self, client=None, iface=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.iface = iface
        self._profile_thread = None
        self._view_keys = {}

        t = design.tokens()
        paint_background(self, t.canvas, "gopilotShell")
        self.setMinimumWidth(design.DOCK_MIN_WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # No brand header here: it IS the dock's title bar now (see
        # dock_title_bar.DockTitleBar). Having both put the wordmark on screen
        # twice and cost 67px of chrome before any content appeared.
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # The view header goes in the CONTENT column, not above the whole body,
        # so the rail runs the full height of the panel beside it. Stacked
        # above everything it would push the rail's first icon a strip-height
        # further down, away from the brand header it belongs with.
        content = QWidget()
        paint_transparent(content)
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        content_lay.addWidget(self._build_view_header())

        self._stack = QStackedWidget()
        self._build_views()
        content_lay.addWidget(self._stack, stretch=1)
        body.addWidget(content, stretch=1)

        self.rail = NavRail()
        self.rail.selected.connect(self.show_view)
        self.rail.action.connect(self._on_rail_action)
        body.addWidget(self.rail)

        root.addLayout(body, stretch=1)

        self.show_view("chat")
        if self.client is not None:
            self._fetch_profile()

    # ── header ────────────────────────────────────────────────────────────────
    def _build_view_header(self):
        """A strip naming the current view.

        The rail is icon-only, so without this the Projects and GoBox views
        arrive with no title anywhere on screen — in the old window their name
        was carried by the labelled sidebar button, which no longer exists.

        Hidden for the two views that already name themselves: the chat (the
        brand header above IS its title) and processes (its own header carries
        the project name plus Back and New process).
        """
        t = design.tokens()
        self._view_header = QFrame()
        self._view_header.setObjectName("viewHeader")
        # Pinned so it and the rail's logo block are exactly the same height
        # and their bottom rules form one line across the panel.
        self._view_header.setFixedHeight(design.VIEW_HEADER_HEIGHT)
        # No fill — the title sits on the canvas with just a rule beneath it.
        # A `surface_alt` band here read as a near-white slab across the top of
        # every view, which competed with the content below it for attention
        # and made the panel look like it had two toolbars. The rule alone is
        # enough to separate a title from what it titles.
        self._view_header.setStyleSheet(
            f"QFrame#viewHeader {{ background:transparent; border:none; "
            f"border-bottom:{design.BORDER}px solid {t.border}; }}"
        )
        lay = QHBoxLayout(self._view_header)
        lay.setContentsMargins(design.SPACE_4, 0, design.SPACE_3, 0)
        lay.setSpacing(design.SPACE_2)
        self._view_title = QLabel("")
        self._view_title.setStyleSheet(design.heading_qss(design.TEXT_LG))
        lay.addWidget(self._view_title)
        lay.addStretch()
        self._view_header.setVisible(False)
        return self._view_header

    def _build_views(self):
        self.chat_page = GoPilotPage(self.client, self.iface)
        self.projects_page = ProjectsPage(self.client)
        self.gobox_page = SolutionsPage(self.client)
        self.processes_page = ProcessesPage(self.client, self.iface)
        self.history_page = HistoryPanel(self.client)
        self.settings_page = SettingsPage(self.client, self.iface)

        for key, page in (
            ("chat", self.chat_page),
            ("projects", self.projects_page),
            ("gobox", self.gobox_page),
            ("processes", self.processes_page),
            ("history", self.history_page),
            ("settings", self.settings_page),
        ):
            self._stack.addWidget(page)
            self._view_keys[key] = page

        # History <-> chat.
        self.history_page.chat_selected.connect(self._open_chat)
        self.history_page.new_chat_requested.connect(self.new_chat)
        self.chat_page.history_changed.connect(self.history_page.load)
        self.chat_page.session_changed.connect(
            lambda chat_id: self.history_page.set_current(chat_id or None))

        # Projects / GoBox drill into processes.
        self.projects_page.project_opened.connect(self._open_project)
        self.gobox_page.project_created.connect(self._open_project)

        # Settings asks; the shell (and above it, the plugin) acts.
        self.settings_page.logout_requested.connect(self.logout_requested.emit)
        self.settings_page.feedback_requested.connect(self.open_feedback)

    def show_view(self, key):
        """Switch the stack to ``key`` and load that view's data if needed."""
        page = self._view_keys.get(key)
        if page is None:
            debug_print(f"[GoPilot] Unknown view {key!r}")
            return
        self._stack.setCurrentWidget(page)

        title = VIEW_TITLES.get(key)
        self._view_title.setText(title or "")
        self._view_header.setVisible(bool(title))
        # The rail's logo block carries the other half of that rule, so it
        # appears and disappears with the strip beside it.
        self.rail.set_top_rule_visible(bool(title))

        # Lazy loading: a view fetches only when it is actually looked at, so
        # opening the panel costs one request (the profile), not five.
        if key == "projects":
            self.projects_page.load_projects()
        elif key == "gobox":
            self.gobox_page.load_solutions()
        elif key == "history":
            self.history_page.load()
        elif key == "settings":
            self.settings_page.refresh_libraries()

        # The rail has no "chat" button — "new chat" sits in that slot — so the
        # chat view lights that one. Without it the rail shows NOTHING active
        # in the panel's primary view, which reads as "nothing is selected".
        # Processes is a drill-down with no button of its own, so it keeps
        # pointing at the list the user came from.
        self.rail.set_current({
            "chat": "new-chat",
            "processes": "projects",
        }.get(key, key))

    def set_client(self, client):
        """Adopt a client after construction, and load what needs loading.

        The shell is built before anyone has signed in — the panel docks on
        first use either way — so every page starts with ``client=None`` and
        shows its own empty state. Handing the client over here rather than
        rebuilding the shell keeps the rail, the drag handles and the dock's
        geometry memory all pointing at the same widgets they always did.

        Safe because a page only READS ``self.client`` when it starts a
        request; nothing captures it at construction.
        """
        self.client = client
        for page in (self.chat_page, self.projects_page, self.gobox_page,
                     self.processes_page, self.history_page,
                     self.settings_page):
            if page is not None:
                page.client = client

        if client is None:
            return
        # The profile drives the account card and the token balance, and the
        # current view needs its data now that there is someone to ask.
        self._fetch_profile()
        self.show_view(self.current_view() or "chat")

    def drag_handles(self):
        """Widgets that should behave like a title bar when floating.

        The floating panel has no caption to grab (see
        ``gopilot_dock._apply_floating_chrome``), so these stand in for one.
        All three are chrome the user already reads as "the frame", and none of
        them has a click action of its own to compete with a drag:

        * the view-title strip — a title row, which is what a caption is;
        * the rail's RASID mark, the panel's top corner;
        * the rail itself, whose empty lower half is the largest uninterrupted
          surface on the panel. Its buttons sit above this filter and keep
          their own clicks and cursors.

        Deliberately NOT the chat transcript or the composer: dragging a window
        by the text you are trying to select would be worse than having no
        handle at all.
        """
        return (self._view_header, self.rail.logo_block(), self.rail)

    def current_view(self):
        current = self._stack.currentWidget()
        for key, page in self._view_keys.items():
            if page is current:
                return key
        return None

    def _on_rail_action(self, key):
        if key == "new-chat":
            self.new_chat()

    # ── actions ───────────────────────────────────────────────────────────────
    def new_chat(self):
        self.chat_page.start_new_chat()
        self.show_view("chat")

    def _open_chat(self, chat_id, title):
        self.chat_page.open_chat(chat_id, title)
        self.history_page.set_current(chat_id)
        self.show_view("chat")

    def _open_project(self, project):
        self.processes_page.load_processes(project)
        self.show_view("processes")

    def open_feedback(self):
        """Open the feedback dialog, tagged with the view it was sent from."""
        dialog = FeedbackDialog(
            self.client, current_page=self.current_view() or "unknown",
            parent=self)
        exec_dialog(dialog)

    # ── profile ───────────────────────────────────────────────────────────────
    def _fetch_profile(self):
        self._profile_thread = FetchProfileThread(self.client)
        self._profile_thread.finished.connect(self.settings_page.set_profile)
        self._profile_thread.error.connect(
            lambda msg: debug_print(f"[RASID] Profile fetch failed: {msg}"))
        self._profile_thread.start()

    def refresh_profile(self):
        """Re-read the profile — e.g. after the user buys tokens."""
        thread = self._profile_thread
        if thread is not None and thread.isRunning():
            return
        self._fetch_profile()

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def cleanup(self):
        """Release every view's threads, timers and map tools.

        Called from the dock's ``closeEvent`` and from the plugin's ``unload``.
        Must be safe to call more than once, and must not raise: a partially
        constructed shell (a failed page import) still has to tear down the
        pages that did build.
        """
        for page in (getattr(self, "chat_page", None),
                     getattr(self, "history_page", None),
                     getattr(self, "settings_page", None)):
            if page is not None and hasattr(page, "cleanup"):
                try:
                    page.cleanup()
                except Exception as exc:
                    debug_print(f"[GoPilot] Page cleanup failed: {exc}")

        # ProcessesPage polls process status on a 10s timer; left running after
        # the widget is destroyed it fires into freed memory.
        processes = getattr(self, "processes_page", None)
        timer = getattr(processes, "_refresh_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError as exc:
                debug_print(f"[GoPilot] Could not stop refresh timer: {exc}")

        thread = self._profile_thread
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(1000)
            except RuntimeError as exc:
                debug_print(f"[RASID] Profile thread teardown: {exc}")
        self._profile_thread = None
