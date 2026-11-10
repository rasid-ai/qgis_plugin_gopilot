from qgis.PyQt.QtWidgets import (
    QLabel, QHBoxLayout,
    QVBoxLayout, QWidget, QScrollArea, QFrame, QSplitter,
    QMessageBox, QStackedWidget,
)
from qgis.PyQt.QtCore import QThread, pyqtSignal, QTimer
from .compat import (
    Qt_Horizontal, Qt_Vertical, Qt_AlignTop, Qt_AlignCenter,
    Qt_PointingHandCursor, Qt_RichText, Qt_TextBrowserInteraction,
    exec_dialog,
    QMessageBox_Question, QMessageBox_Yes, QMessageBox_No
)
from .image_loader import load_image
from .create_process_wizard import CreateProcessWizard
from . import design
from . import icons
from .widgets import (
    Chip, ElidedLabel, IconButton, SectionCard, WrappedLabel,
    apply_scroll_style, clear_layout, paint_background, primary_button,
    secondary_button,
)
from .config import APP_BASE_URL

LIST_THUMB = 40
DETAIL_THUMB = 280

# Raw API `situation` codes -> the label shown for them.
#
# These used to be (label, colour) pairs, and the colours — #00856F, #e74c3c,
# #3B82F6, #6366F1 — were a palette of their own: a second green, a second red
# and two colours used nowhere else in the plugin. Every reader already
# discarded them, because a status is drawn as a Chip and a Chip takes a TONE,
# which resolves against the theme. So the colours are gone and the tone
# mapping below is the one colour decision left.
_COMPLETED = "Completed"
_FAILED = "Failed"
_INFERENCE = "Inference"
_PREPARATION = "Preparation"

SITUATION_LABELS = {
    # Completed
    "is": _COMPLETED,
    "done": _COMPLETED,

    # Failed
    "if": _FAILED,
    "failed": _FAILED,
    "pdmf": _FAILED,

    # Running
    "i": _INFERENCE,
    "inference": _INFERENCE,

    # Queued / preparing
    "c": _PREPARATION,
    "creating": _PREPARATION,
    "idle": _PREPARATION,
}

_CHIP_TONES = {
    _COMPLETED: "success",
    _FAILED: "error",
    _INFERENCE: "accent",
    _PREPARATION: "neutral",
}


def situation_label(situation):
    """The display label for an API situation code.

    Unknown codes read as Preparation rather than raising: the API adds states
    faster than the plugin ships, and a process in a state this build has not
    heard of is far more likely to be on its way somewhere than finished.
    """
    return SITUATION_LABELS.get(situation, _PREPARATION)


def _chip_tone(situation):
    """The :class:`~.widgets.Chip` tone for an API situation code."""
    return _CHIP_TONES.get(situation_label(situation), "neutral")


class FetchProcessesThread(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, client, project_slug):
        super().__init__()
        self.client = client
        self.project_slug = project_slug

    def run(self):
        try:
            processes = self.client.get_processes(self.project_slug)
            self.finished.emit(processes)
        except Exception as e:
            self.error.emit(str(e))


class FetchProcessDetailThread(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, client, process_id):
        super().__init__()
        self.client = client
        self.process_id = process_id

    def run(self):
        try:
            detail = self.client.get_process_detail(self.process_id)
            self.finished.emit(detail)
        except Exception as e:
            self.error.emit(str(e))


class HideProcessThread(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, client, project_slug, process_id):
        super().__init__()
        self.client = client
        self.project_slug = project_slug
        self.process_id = process_id

    def run(self):
        try:
            self.client.hide_process(self.project_slug, self.process_id)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class DownloadThread(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, client, url):
        super().__init__()
        self.client = client
        self.url = url

    def run(self):
        try:
            path = self.client.download_file(self.url)
            self.finished.emit(path)
        except Exception as e:
            self.error.emit(str(e))


def _layer_exists_in_qgis(layer_name):
    """Check if a layer with the given name already exists in QGIS project."""
    from qgis.core import QgsProject
    for layer in QgsProject.instance().mapLayers().values():
        if layer.name() == layer_name:
            return True
    return False


def _add_layer_to_qgis(filepath, layer_name):
    from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
    root = QgsProject.instance().layerTreeRoot()
    target_group = root.findGroup("GoBox")
    if target_group is None:
        target_group = root.insertGroup(0, "GoBox")

    ext = filepath.rsplit(".", 1)[-1].lower()
    if ext in ("tif", "tiff"):
        layer = QgsRasterLayer(filepath, layer_name)
    else:
        layer = QgsVectorLayer(filepath, layer_name, "ogr")

    if not layer.isValid():
        raise Exception(f"Could not load layer from {filepath}")

    QgsProject.instance().addMapLayer(layer, False)
    target_group.addLayer(layer)


class ProcessesPage(QWidget):

    def __init__(self, client, iface=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.iface = iface
        self._threads = []
        self._current_project = None
        self._selected_row = None
        self._current_processes = []
        self._selected_process_id = None  # Track selected process for silent refresh

        # Track detail panel sections for selective updates during auto-refresh
        self._detail_thumbnail = None
        self._status_badge = None
        self._general_info_layout = None
        self._analytics_layout = None
        self._not_completed_message = None  # Track "not yet completed" message
        # Track if download buttons have been added
        self._downloads_section_exists = False
        self._last_known_situation = None  # Track status to detect changes
        paint_background(self, design.tokens().canvas,
                         "processesPage")

        # Auto-refresh timer for polling process status (completely invisible)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_auto_refresh)
        self._refresh_timer.setInterval(10000)  # Poll every 10 seconds

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header bar ──
        # Unfilled, matching the view-title strip the other views get from the
        # shell: title plus a rule, no slab of near-white across the top.
        #
        # Scoped by object name as well. The old unscoped `background:` /
        # `border-bottom:` applied to this frame AND propagated to its child
        # labels and buttons, which is why anything added here had to re-state
        # its own background to avoid inheriting a stray bottom border.
        header = QFrame()
        header.setObjectName("processesHeader")
        header.setFixedHeight(design.VIEW_HEADER_HEIGHT)
        header.setStyleSheet(
            f"QFrame#processesHeader {{ background: transparent; border: none;"
            f" border-bottom: {design.BORDER}px solid "
            f"{design.tokens().border}; }}")
        header_layout = QHBoxLayout(header)
        # Matches the shell's view-title strip exactly — same height, same
        # left inset, same rule colour — so switching from Projects into a
        # project does not nudge the title sideways or shift the rule.
        header_layout.setContentsMargins(design.SPACE_4, 0, design.SPACE_3, 0)
        header_layout.setSpacing(design.SPACE_2)

        self._title_label = ElidedLabel("")
        self._title_label.setStyleSheet(design.heading_qss(design.TEXT_LG))
        # stretch=1, and NO trailing addStretch(). An ElidedLabel declares an
        # Ignored horizontal policy so it can shrink below its text width —
        # which also means a layout will happily give it ZERO width unless it
        # is told to take the slack. Added plain, with a stretch after it, the
        # project title collapsed to nothing and the header looked empty.
        header_layout.addWidget(self._title_label, stretch=1)

        # No Back button here. It appeared only while the wizard was open and
        # was wired to the same handler as the wizard's own Cancel, so the two
        # were one action offered twice, 700px apart, in a panel with no room
        # to spare. Cancel is the one that survives: it sits in the action bar
        # with the primary button, which is where a form's exit belongs.
        # Leaving the drill-down entirely is still the rail's Projects button.

        # No plus glyph, matching the Create project button in GoBox: the
        # label already says what it does, and the icon made the panel's two
        # create actions look like different components.
        self._new_process_btn = primary_button("New process")
        self._new_process_btn.clicked.connect(self._on_new_process)
        header_layout.addWidget(self._new_process_btn)

        outer.addWidget(header)

        # ── Body: stacked widget switching between list view and create wizard ──
        self._body_stack = QStackedWidget()
        outer.addWidget(self._body_stack, stretch=1)

        # Page 0: process list + detail splitter
        self._list_view = QWidget()
        self._build_list_view()
        self._body_stack.addWidget(self._list_view)

        # Page 1: create process wizard (added dynamically)
        self._wizard = None

    def _build_list_view(self):
        layout = QVBoxLayout(self._list_view)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt_Horizontal)
        self._splitter = splitter
        # Once the user drags the handle, their split wins over ours.
        self._split_user_set = False
        self._split_applied_for = None
        self._applying_split = False
        splitter.splitterMoved.connect(self._on_splitter_moved)
        layout.addWidget(splitter)

        # Left: scrollable process list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        apply_scroll_style(self._list_scroll)
        # No minimum width any more. 220px was fine beside a 1087px dialog, but
        # in a ~420px dock it claimed most of the panel and squeezed the detail
        # pane to a column too narrow to read. Narrow layouts stack the two
        # panes vertically instead — see _sync_splitter_orientation.
        left_layout.addWidget(self._list_scroll)

        self._list_inner = QWidget()
        self._list_layout = QVBoxLayout(self._list_inner)
        self._list_layout.setSpacing(4)
        self._list_layout.setContentsMargins(6, 6, 6, 6)
        self._list_layout.setAlignment(Qt_AlignTop)
        self._list_scroll.setWidget(self._list_inner)

        splitter.addWidget(left)

        # Right: detail panel
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        apply_scroll_style(self._detail_scroll)
        splitter.addWidget(self._detail_scroll)

        self._detail_widget = QWidget()
        self._detail_layout = QVBoxLayout(self._detail_widget)
        self._detail_layout.setContentsMargins(12, 12, 12, 12)
        self._detail_layout.setSpacing(8)
        self._detail_layout.setAlignment(Qt_AlignTop)
        self._detail_scroll.setWidget(self._detail_widget)

        self._show_detail_placeholder()

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        self._sync_splitter_orientation()

    # Below this width the list and the detail pane cannot both be readable
    # side by side, so they stack. Chosen as the dock's default width plus the
    # nav rail, i.e. "wider than the panel normally is".
    _STACK_BELOW_WIDTH = 520
    # Side by side, the list still has to show a process NAME. Below about
    # this it shows a thumbnail and an ellipsis.
    _LIST_MIN_WIDTH = 190
    # Stacked, the list takes what its rows need up to this share of the
    # height, so three processes do not reserve a third of the panel.
    _LIST_MAX_HEIGHT_SHARE = 0.45

    def _sync_splitter_orientation(self):
        """Side-by-side when there is room, stacked when there is not.

        The processes view is the most window-shaped screen in the plugin: a
        master list plus a detail pane. Docked at panel width that reads as two
        unusable columns, so it becomes list-above-detail, which is the same
        information in the shape the space allows.
        """
        splitter = getattr(self, "_splitter", None)
        if splitter is None:
            return
        wanted = (Qt_Vertical if self.width() < self._STACK_BELOW_WIDTH
                  else Qt_Horizontal)
        if splitter.orientation() != wanted:
            self._applying_split = True
            try:
                splitter.setOrientation(wanted)
            finally:
                self._applying_split = False
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 2)
            # A new axis means the old split is meaningless, so the user's
            # drag along the previous one does not carry over.
            self._split_user_set = False
            self._split_applied_for = None
        self._apply_initial_split(wanted)

    def _list_content_height(self):
        """Height the process rows need, computed rather than asked for.

        The obvious version of this — ``self._list_inner.sizeHint()`` — does
        not work here, and measurably so. A ``QVBoxLayout`` folds newly added
        widgets into its own size hint only when it next handles a deferred
        LayoutRequest, which means after the event loop turns. Called straight
        after the rows are added, as it is from ``_on_loaded``, three 58px rows
        reported a hint of 12px. Neither ``activate()`` nor
        ``updateGeometry()`` changes that; only ``processEvents()`` does, and
        pumping the event loop mid-load to measure something is not a trade
        worth making.

        The rows' OWN hints are valid immediately, so this is the sum the
        layout itself would arrive at: every widget's hint, the spacing between
        them, and the layout's margins. Summing each row rather than
        multiplying one by the count, because a row with a long enough name to
        wrap is taller than its neighbours.
        """
        layout = self._list_layout
        margins = layout.contentsMargins()
        total = margins.top() + margins.bottom()
        widgets = 0
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is None:
                continue
            total += widget.sizeHint().height()
            widgets += 1
        if widgets > 1:
            total += layout.spacing() * (widgets - 1)
        return total

    def _apply_initial_split(self, orientation):
        """Give the two panes a usable starting split.

        Returning early when the orientation already matched is what left the
        side-by-side layout broken: the splitter is BORN horizontal, so at
        panel widths above the stacking threshold the sizes below were never
        reached at all, and the split fell back to the children's size hints.
        A list of rows that can each elide hints at almost nothing, so the list
        column opened about 110px wide and every process name in it was
        truncated to "Buildin…".

        Applied once per orientation, and never again once the user has
        dragged the handle — their split is the right one after that, and
        re-deriving it on every resize would snap the handle back mid-drag.
        """
        splitter = getattr(self, "_splitter", None)
        if splitter is None or self._split_user_set:
            return
        if self._split_applied_for == orientation:
            return
        # The SPLITTER's extent, not the page's: the splitter sits below the
        # header inside the stacked body, so the page is some 50px taller than
        # it. Sizes that do not add up to the real extent get rescaled
        # proportionally by Qt, which quietly shrank a measured 194px list to
        # 182 — and compounded with the bug above, to 138.
        extent = (splitter.height() if orientation == Qt_Vertical
                  else splitter.width())
        if extent <= 0:
            extent = (self.height() if orientation == Qt_Vertical
                      else self.width())
        if extent <= 0:
            return      # not laid out yet; resizeEvent will come back here

        if orientation == Qt_Vertical:
            # Fit the rows, rather than taking a fixed share: with three
            # processes a 38% slice left a band of empty canvas between the
            # last row and the detail pane.
            needed = self._list_content_height() + 2 * design.BORDER
            lower = int(extent * 0.18)
            upper = int(extent * self._LIST_MAX_HEIGHT_SHARE)
            first = max(lower, min(needed, upper))
        else:
            first = max(self._LIST_MIN_WIDTH, int(extent * 0.34))
            first = min(first, int(extent * 0.5))

        self._applying_split = True
        try:
            splitter.setSizes([first, max(1, extent - first)])
        finally:
            self._applying_split = False
        self._split_applied_for = orientation

    def _on_splitter_moved(self, _pos, _index):
        """Record that the split is now the user's choice.

        Guarded against our own ``setSizes``. Qt 5.15 and 6.8 were both
        measured NOT to emit this for a programmatic resize, so the guard is
        not fixing an observed bug — it is there because the flag it sets is
        permanent: anything that sets it by mistake silently freezes the split
        for the rest of the session, which is a failure that would be
        invisible until someone wondered why the pane stopped fitting.
        """
        if self._applying_split:
            return
        self._split_user_set = True

    def resizeEvent(self, event):
        """Re-pick the splitter axis as the dock is dragged wider or narrower."""
        super().resizeEvent(event)
        self._sync_splitter_orientation()

    def closeEvent(self, event):
        """Stop auto-refresh timer when widget is closed."""
        self._stop_auto_refresh()
        super().closeEvent(event)

    def hideEvent(self, event):
        """Stop auto-refresh timer when widget is hidden."""
        self._stop_auto_refresh()
        super().hideEvent(event)

    # ── Public ──

    def load_processes(self, project):
        self._current_project = project
        self._title_label.setText(project.get("title", "Project"))
        self._body_stack.setCurrentWidget(self._list_view)
        self._new_process_btn.show()
        self._clear_list()
        self._show_detail_placeholder()

        loading = QLabel("Loading processes...")
        loading.setAlignment(Qt_AlignCenter)
        self._list_layout.addWidget(loading)

        slug = project.get("slug", "")
        self._fetch_thread = FetchProcessesThread(self.client, slug)
        self._fetch_thread.finished.connect(self._on_loaded)
        self._fetch_thread.error.connect(self._on_error)
        self._fetch_thread.start()

    # ── List callbacks ──

    def _on_loaded(self, processes, preserve_selection=False):
        """Load processes into list. If preserve_selection=True, restore previously selected process."""
        self._current_processes = processes

        # Save scroll positions (both list and detail)
        list_scroll_pos = self._list_scroll.verticalScrollBar().value()
        detail_scroll_pos = self._detail_scroll.verticalScrollBar().value()

        self._clear_list()

        if not processes:
            lbl = WrappedLabel(
                "No processes yet. Use New process to create one.")
            lbl.setAlignment(Qt_AlignCenter)
            lbl.setStyleSheet(design.body_qss(design.TEXT_BASE,
                                              secondary=True))
            self._list_layout.addWidget(lbl)
            self._stop_auto_refresh()
            return

        selected_index = None
        selected_proc = None
        for i, proc in enumerate(processes):
            row = self._create_row(proc)
            self._list_layout.addWidget(row)

            # Track which row should be selected
            if preserve_selection and proc.get("id") == self._selected_process_id:
                selected_index = i
                selected_proc = proc

        # Restore selection or select first process
        if processes:
            if preserve_selection and selected_index is not None:
                # Silently restore previously selected process (don't trigger full reload)
                selected_row = self._list_layout.itemAt(
                    selected_index).widget()

                # Restore the highlight without triggering a detail fetch.
                self._select_row(selected_row)

                # Silently update detail view - only update status badge, don't rebuild everything
                if selected_proc and selected_proc.get("id"):
                    self._detail_thread = FetchProcessDetailThread(
                        self.client, selected_proc.get("id"))
                    # Pass the captured scroll position through. It was
                    # read into a local and never used, so every poll rebuilt
                    # the info rows and left the pane jumped back to the top
                    # while it was being read.
                    self._detail_thread.finished.connect(
                        lambda proc, pos=detail_scroll_pos:
                        self._update_detail_status_only(
                            proc, restore_scroll=pos))
                    self._detail_thread.error.connect(self._on_detail_error)
                    self._detail_thread.start()

            elif not preserve_selection:
                # First load - select first process
                first_proc = processes[0]
                first_row = self._list_layout.itemAt(0).widget()
                self._on_row_clicked(first_proc, first_row)

        # Restore scroll position
        self._list_scroll.verticalScrollBar().setValue(list_scroll_pos)

        # The stacked split is derived from the rows' height, which was not
        # known when the pane was first laid out — so re-derive it now that
        # they exist. No-ops if the user has dragged the handle.
        self._split_applied_for = None
        self._apply_initial_split(self._splitter.orientation())

        # Check if we have any "Preparing" processes (silently)
        self._manage_auto_refresh()

    def _on_error(self, msg):
        self._clear_list()
        lbl = WrappedLabel(f"Could not load processes: {msg}")
        lbl.setAlignment(Qt_AlignCenter)
        lbl.setStyleSheet(design.body_qss(design.TEXT_BASE, secondary=True))
        self._list_layout.addWidget(lbl)
        self._stop_auto_refresh()

    # ── Auto-refresh logic (completely invisible to user) ──

    def _has_preparing_processes(self):
        """Check if any processes are still in progress (not completed or failed)."""
        for proc in self._current_processes:
            situation = proc.get("situation", "")
            # Keep refreshing if process is in Preparation ('c') or Inference ('i')
            # Stop refreshing only when all processes are Completed ('is') or Failed ('if', 'failed', 'pdmf')
            if situation in ('c', 'i', 'creating', 'inference', 'idle'):
                return True
            # Also refresh if situation is unknown/not mapped
            if situation not in SITUATION_LABELS:
                return True
        return False

    def _manage_auto_refresh(self):
        """Silently start or stop auto-refresh based on process states."""
        if self._has_preparing_processes():
            if not self._refresh_timer.isActive():
                self._refresh_timer.start()
        else:
            self._stop_auto_refresh()

    def _on_auto_refresh(self):
        """Called by timer to silently refresh process list in background."""
        if self._current_project:
            slug = self._current_project.get("slug", "")
            self._fetch_thread = FetchProcessesThread(self.client, slug)
            # Use lambda to pass preserve_selection=True for silent refresh
            self._fetch_thread.finished.connect(
                lambda procs: self._on_loaded(procs, preserve_selection=True))
            self._fetch_thread.error.connect(self._on_error)
            self._fetch_thread.start()

    def _stop_auto_refresh(self):
        """Silently stop the auto-refresh timer."""
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()

    # ── List helpers ──

    def _clear_list(self):
        self._selected_row = None
        clear_layout(self._list_layout)

    def _create_row(self, proc):
        t = design.tokens()
        row = QFrame()
        row.setObjectName("processRow")
        row.setCursor(Qt_PointingHandCursor)
        row.setStyleSheet(self._row_qss(False))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(design.SPACE_2, design.SPACE_2,
                                  design.SPACE_2, design.SPACE_2)
        layout.setSpacing(design.SPACE_2)

        thumb = QLabel()
        thumb.setFixedSize(LIST_THUMB, LIST_THUMB)
        thumb.setAlignment(Qt_AlignCenter)
        thumb.setStyleSheet(
            f"background:{t.surface_alt}; border:none; "
            f"border-radius:{design.RADIUS_SM}px;")
        load_image(self.client, proc.get("thumbnail"), thumb, self._threads,
                   size=(LIST_THUMB, LIST_THUMB), fit="cover")
        layout.addWidget(thumb)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = ElidedLabel(proc.get("name", "Unnamed"))
        name.setStyleSheet(
            design.body_qss(design.TEXT_BASE)
            + f" font-weight:{design.WEIGHT_SEMIBOLD};")
        info.addWidget(name)

        situation = proc.get("situation", "idle") or "idle"
        label_text = situation_label(situation)
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.addWidget(Chip(label_text, tone=_chip_tone(situation),
                                 removable=False, max_chars=18))
        badge_row.addStretch()
        info.addLayout(badge_row)
        layout.addLayout(info, stretch=1)

        hide_btn = IconButton("trash", size=28, icon_size=design.ICON_SM,
                              tooltip="Hide process", tone="muted",
                              hover_tone="danger")
        hide_btn.clicked.connect(lambda _, p=proc: self._on_hide_process(p))
        layout.addWidget(hide_btn, alignment=Qt_AlignTop)

        row.mousePressEvent = lambda e, p=proc, r=row: self._on_row_clicked(
            p, r)
        return row

    def _select_row(self, row):
        """Move the selection highlight to ``row``.

        Both the click path and the poll loop's silent restore come through
        here, which is what keeps them agreeing: they each used to inline their
        own stylesheet, so a row selected by a refresh and one selected by a
        click were styled by two separate copies of the same four colours.

        The rules stay scoped to ``QFrame#processRow``. The old unscoped
        ``QFrame {...}`` matched the row AND every frame inside it — the status
        chip in particular, which lost its tone and its pill shape as soon as
        its row was selected.

        The fill is the ``selected`` token rather than a hardcoded ``#d1fae5``
        light / ``#1a4d3f`` dark pair, the last two off-palette colours here.
        """
        if self._selected_row is row:
            return
        if self._selected_row is not None:
            try:
                self._selected_row.setStyleSheet(self._row_qss(False))
            except RuntimeError:
                pass        # the previous row was rebuilt out from under us
        row.setStyleSheet(self._row_qss(True))
        self._selected_row = row

    @staticmethod
    def _row_qss(selected):
        """The one definition of a process row's fill, border and hover."""
        t = design.tokens()
        if selected:
            return (
                f"QFrame#processRow {{ background:{t.selected}; "
                f"border:{design.BORDER}px solid {t.accent}; "
                f"border-radius:{design.RADIUS}px; }}"
            )
        return (
            f"QFrame#processRow {{ background:{t.surface}; "
            f"border:{design.BORDER}px solid {t.border}; "
            f"border-radius:{design.RADIUS}px; }}"
            f"QFrame#processRow:hover {{ background:{t.accent_soft}; "
            f"border-color:{t.accent}; }}"
        )

    def _on_row_clicked(self, proc, row):
        self._select_row(row)

        # Save selected process ID for invisible refresh
        process_id = proc.get("id")
        self._selected_process_id = process_id
        if not process_id:
            self._show_detail(proc)
            return

        self._clear_detail()
        loading = QLabel("Loading details…")
        loading.setAlignment(Qt_AlignCenter)
        loading.setStyleSheet(design.body_qss(design.TEXT_BASE,
                                              secondary=True))
        self._detail_layout.addWidget(loading)

        self._detail_thread = FetchProcessDetailThread(self.client, process_id)
        self._detail_thread.finished.connect(self._show_detail)
        self._detail_thread.error.connect(self._on_detail_error)
        self._detail_thread.start()

    def _on_detail_error(self, msg):
        self._clear_detail()
        lbl = WrappedLabel(f"Could not load this process: {msg}")
        lbl.setAlignment(Qt_AlignCenter)
        lbl.setStyleSheet(design.body_qss(design.TEXT_BASE)
                          + f" color:{design.tokens().error};")
        self._detail_layout.addWidget(lbl)

    # ── Detail panel ──

    def _show_detail_placeholder(self):
        self._clear_detail()
        lbl = QLabel("Select a process to view details")
        lbl.setAlignment(Qt_AlignCenter)
        lbl.setStyleSheet(design.body_qss(design.TEXT_BASE, secondary=True))
        self._detail_layout.addWidget(lbl)

    def _clear_detail(self):
        """Empty the detail pane and forget every handle into it."""
        self._detail_thumbnail = None
        self._status_badge = None
        self._status_row = None
        self._general_info_layout = None
        self._analytics_layout = None
        self._analytics_card = None
        self._downloads_card = None
        self._not_completed_message = None
        self._downloads_section_exists = False
        self._last_known_situation = None
        # widgets.clear_layout, not a local copy: it unparents before
        # deleteLater, which is what stops a cleared card from staying on
        # screen until the event loop catches up, and it recurses into nested
        # layouts, which the info rows are.
        clear_layout(self._detail_layout)

    def _insert_before_stretch(self, widget):
        """Add ``widget`` to the detail pane, above its trailing stretch.

        _show_detail finishes with addStretch() to park spare height at the
        bottom. A plain addWidget after that lands BELOW the spacer, which
        pins the new card to the floor of the pane with a gap above it — how
        the Downloads card arrived when a process finished while being watched.
        """
        layout = self._detail_layout
        index = layout.count()
        for i in range(layout.count()):
            if layout.itemAt(i).spacerItem() is not None:
                index = i
                break
        layout.insertWidget(index, widget)

    def _add_info_row(self, label, value):
        """Add info row to the main detail layout."""
        self._add_info_row_to_layout(self._detail_layout, label, value)

    def _add_info_row_to_layout(self, parent_layout, label, value):
        """Add info row to a specific layout."""
        row = QHBoxLayout()
        key = QLabel(str(label))
        key.setStyleSheet(design.body_qss(design.TEXT_SM, secondary=True))
        key.setFixedWidth(100)
        val = WrappedLabel(str(value))
        val.setStyleSheet(design.body_qss(design.TEXT_BASE))
        row.addWidget(key)
        row.addWidget(val, stretch=1)
        parent_layout.addLayout(row)

    def _update_detail_status_only(self, proc, restore_scroll=None):
        """Refresh the live parts of the detail pane, leaving Downloads alone.

        The poll loop calls this instead of :meth:`_show_detail` so an
        in-progress download is not torn down under the user every ten
        seconds. ``restore_scroll`` puts the scrollbar back afterwards: the
        info rows ARE rebuilt here, which can change the pane's height.
        """
        if not self._status_badge:
            # Detail panel not yet created, skip silent update
            return

        try:
            # 1. Update thumbnail — same cover fit and radius as the first
            # render, or a refresh would quietly swap in a letterboxed image.
            if self._detail_thumbnail:
                width = max(160, self._detail_pane_width())
                load_image(self.client, proc.get("thumbnail"),
                           self._detail_thumbnail, self._threads,
                           size=(width,
                                 int(width * design.CARD_THUMB_ASPECT)),
                           fit="cover", radius=design.RADIUS)

            # 2. Update status chip
            situation = proc.get("situation", "idle") or "idle"
            label_text = situation_label(situation)
            self._set_status_chip(situation, label_text)

            # 3. Update general info section
            if self._general_info_layout:
                # Clear existing general info
                while self._general_info_layout.count():
                    item = self._general_info_layout.takeAt(0)
                    if item.widget():
                        item.widget().deleteLater()
                    elif item.layout():
                        clear_layout(item.layout())

                # Rebuild general info
                fees = proc.get("fees")
                self._add_info_row_to_layout(self._general_info_layout, "Fees",
                                             f"€{fees}" if fees is not None else "—")
                create_date = proc.get("create_date", "—") or "—"
                self._add_info_row_to_layout(
                    self._general_info_layout, "Date", create_date)
                area = proc.get("area")
                self._add_info_row_to_layout(self._general_info_layout, "Area",
                                             f"{area} km²" if area is not None else "—")

            # 4. Update/Add analytics section
            analytics = proc.get("analytics", {})
            if analytics:
                if not self._analytics_layout:
                    # Analytics just became available. It goes in a card of its
                    # own, the same one _show_detail builds — appending a bare
                    # layout here instead put the rows straight onto the page
                    # background, so a pane that gained analytics while being
                    # watched looked different from the same pane reopened.
                    analytics_card = SectionCard("Data analytics",
                                                 icon_name="sliders")
                    analytics_container = QVBoxLayout()
                    analytics_container.setSpacing(design.SPACE_1)
                    analytics_card.add_layout(analytics_container)
                    self._insert_before_stretch(analytics_card)
                    self._analytics_layout = analytics_container
                    self._analytics_card = analytics_card

                clear_layout(self._analytics_layout)

                for key, value in analytics.items():
                    self._add_info_row_to_layout(
                        self._analytics_layout, key, value)

            # 5. Add download buttons if status just changed to "Completed"
            is_completed = situation == "is"
            status_changed_to_completed = (is_completed and
                                           self._last_known_situation != "is" and
                                           not self._downloads_section_exists)

            if status_changed_to_completed:
                # Delete "not yet completed" message if it exists
                if self._not_completed_message:
                    try:
                        # setParent(None) first: deleteLater alone leaves the
                        # widget visible in its layout until the event loop
                        # gets round to it, so the note sat under the new
                        # Downloads card for a frame or more.
                        self._not_completed_message.setParent(None)
                        self._not_completed_message.deleteLater()
                        self._not_completed_message = None
                    except RuntimeError:
                        pass  # Widget already deleted

                result_vector = proc.get(
                    "result_file_shp") or proc.get("result_file")
                dataset = proc.get("dataset")
                process_name = proc.get("name", "process")

                if result_vector or dataset:
                    self._add_download_buttons(
                        result_vector, dataset, process_name)
                    self._downloads_section_exists = True

            # Track current situation for next refresh
            self._last_known_situation = situation

            if restore_scroll is not None:
                self._detail_scroll.verticalScrollBar().setValue(
                    restore_scroll)

        except RuntimeError:
            # Widgets were deleted, skip update
            pass

    def _show_detail(self, proc, restore_scroll=None):
        """Render one process's detail pane.

        Three cards — identity, General info, Data analytics — plus Downloads
        when there is something to download, matching the ``SectionCard``
        paradigm Settings, GoBox and Projects use. It was previously a flat run
        of labels under underlined headings, on the page background, with the
        thumbnail on a hardcoded ``#f0f0f0`` that stayed pale grey in dark mode.

        ``restore_scroll`` puts the scrollbar back after a silent refresh, so
        polling does not yank the pane to the top while it is being read.
        """
        self._clear_detail()
        t = design.tokens()

        situation = proc.get("situation", "idle") or "idle"
        label_text = situation_label(situation)

        # ── Identity card: thumbnail, name, status ──
        identity = SectionCard()
        # 16:9, the same ratio the GoBox and project cards use, and full width
        # rather than a fixed 280px square — a square that size overflowed the
        # pane at panel width and left a letterboxed image in it at any other.
        thumb = QLabel()
        thumb.setMinimumHeight(120)
        thumb.setAlignment(Qt_AlignCenter)
        # surface_alt, the same placeholder fill the GoBox and project cards
        # use, so a process with no thumbnail yet looks like a card with none.
        thumb.setStyleSheet(
            f"background:{t.surface_alt}; border:none; "
            f"border-radius:{design.RADIUS}px;")
        thumb_width = max(160, self._detail_pane_width())
        load_image(self.client, proc.get("thumbnail"), thumb, self._threads,
                   size=(thumb_width,
                         int(thumb_width * design.CARD_THUMB_ASPECT)),
                   fit="cover", radius=design.RADIUS)
        identity.add(thumb)
        self._detail_thumbnail = thumb

        name = WrappedLabel(proc.get("name", "Unnamed process"))
        name.setStyleSheet(design.heading_qss(design.TEXT_LG))
        identity.add(name)

        # A Chip, like every other status badge in the panel, so it re-tones
        # with the theme instead of being white-on-fixed-colour.
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.addStretch()
        identity.add_layout(badge_row)
        self._status_row = badge_row
        self._status_badge = None
        self._set_status_chip(situation, label_text)
        self._detail_layout.addWidget(identity)

        # ── General info ──
        general_card = SectionCard("General info", icon_name="info")
        general_info_container = QVBoxLayout()
        general_info_container.setSpacing(design.SPACE_1)

        fees = proc.get("fees")
        self._add_info_row_to_layout(
            general_info_container, "Fees",
            f"€{fees}" if fees is not None else "—")
        self._add_info_row_to_layout(
            general_info_container, "Date",
            proc.get("create_date", "—") or "—")
        area = proc.get("area")
        self._add_info_row_to_layout(
            general_info_container, "Area",
            f"{area} km²" if area is not None else "—")

        general_card.add_layout(general_info_container)
        self._detail_layout.addWidget(general_card)
        self._general_info_layout = general_info_container

        # ── Data analytics (solution-specific) ──
        analytics = proc.get("analytics", {})
        if analytics:
            analytics_card = SectionCard("Data analytics", icon_name="sliders")
            analytics_container = QVBoxLayout()
            analytics_container.setSpacing(design.SPACE_1)
            for key, value in analytics.items():
                self._add_info_row_to_layout(analytics_container, key, value)
            analytics_card.add_layout(analytics_container)
            self._detail_layout.addWidget(analytics_card)
            self._analytics_layout = analytics_container
            self._analytics_card = analytics_card
        else:
            self._analytics_layout = None
            self._analytics_card = None

        # ── Downloads, only once there is a result ──
        is_completed = situation == "is"
        result_vector = proc.get("result_file_shp") or proc.get("result_file")
        dataset = proc.get("dataset")
        process_name = proc.get("name", "process")

        if is_completed and (result_vector or dataset):
            self._add_download_buttons(result_vector, dataset, process_name)
            self._downloads_section_exists = True
        elif not is_completed:
            # Secondary body copy, not grey italics. The old #888 was the same
            # colour in both themes, and italic 12px on a dark canvas was the
            # least readable text in the panel.
            status_msg = WrappedLabel(
                f"This process is still running ({label_text.lower()}). "
                f"Results appear here when it finishes.")
            status_msg.setStyleSheet(
                design.body_qss(design.TEXT_BASE, secondary=True))
            self._detail_layout.addWidget(status_msg)
            self._downloads_section_exists = False
            self._not_completed_message = status_msg

        self._last_known_situation = situation
        self._detail_layout.addStretch()

        if restore_scroll is not None:
            self._detail_scroll.verticalScrollBar().setValue(restore_scroll)

    def _set_status_chip(self, situation, label_text):
        """Put the current status chip in the identity card.

        Replaces the chip rather than restyling it: a :class:`~.widgets.Chip`
        fixes its tone at construction — the frame's fill and border, the label
        colour and any icon tint all come from it — so there is no correct way
        to retone one in place. Swapping the widget is what makes the poll
        loop's Preparation -> Inference -> Completed transition land in the
        right colour instead of keeping the first one it was built with.
        """
        row = getattr(self, "_status_row", None)
        if row is None:
            return
        old = getattr(self, "_status_badge", None)
        if old is not None:
            try:
                old.setParent(None)
                old.deleteLater()
            except RuntimeError:
                pass        # already gone; the rebuild below still stands
        self._status_badge = Chip(label_text, tone=_chip_tone(situation),
                                  removable=False, max_chars=24)
        # Before the trailing stretch, so the chip stays left-aligned.
        row.insertWidget(0, self._status_badge)

    def _detail_pane_width(self):
        """Usable width inside the detail pane, for sizing the thumbnail.

        The viewport minus the layout margins and the card's own padding, so a
        cover-scaled image is requested at the size it will be drawn at rather
        than at a fixed 280px that then has to be scaled again.
        """
        viewport = self._detail_scroll.viewport().width()
        if viewport <= 0:
            viewport = max(self.width(), design.DOCK_MIN_WIDTH)
        return max(0, viewport - 2 * design.SPACE_3 - 2 * design.SPACE_4)

    def _add_download_buttons(self, result_vector, dataset, process_name):
        """The Downloads card. Built once, when the process completes.

        The result is the primary action and the dataset the secondary one —
        previously both were custom-painted buttons on hardcoded fills
        (``#00856F``, ``#1E293B``, and ``#10b981`` for the loaded state), which
        is three greens and a slate that appear nowhere else in the palette.
        """
        card = SectionCard("Downloads", icon_name="download")
        self._downloads_card = card

        for url, suffix, label, primary in (
            (result_vector, "result", "Download result (Shapefile)", True),
            (dataset, "dataset", "Download dataset (GeoTIFF)", False),
        ):
            if not url:
                continue
            layer_name = f"{process_name} ({suffix})"
            if _layer_exists_in_qgis(layer_name):
                # Already in the project: a disabled secondary button with a
                # tick, rather than a green one. Green reads as "do this",
                # and this is the one state where there is nothing to do.
                button = secondary_button("Already loaded", icon_name="check")
                button.setEnabled(False)
            elif primary:
                button = primary_button(label, icon_name="download")
                # `b=button` binds this iteration's button. Closing over the
                # loop variable instead would give both buttons the LAST one,
                # so clicking Download result would disable the dataset button.
                button.clicked.connect(
                    lambda _=False, u=url, n=layer_name, b=button:
                    self._download_and_load(u, n, b))
            else:
                button = secondary_button(label, icon_name="download")
                # `b=button` binds this iteration's button. Closing over the
                # loop variable instead would give both buttons the LAST one,
                # so clicking Download result would disable the dataset button.
                button.clicked.connect(
                    lambda _=False, u=url, n=layer_name, b=button:
                    self._download_and_load(u, n, b))
            card.add(button)

        self._insert_before_stretch(card)

    # ── Download & load into QGIS ──

    def _download_and_load(self, url, layer_name, button):
        """Download a result and add it to the QGIS project.

        The button carries its own progress: it is the thing the user clicked
        and the only place the outcome belongs. Three states, all through the
        secondary style rather than a repainted green — the finished state used
        to set a hardcoded ``#10b981`` fill, the only button in the panel that
        went green on success, and once set it stayed green through theme
        changes because nothing re-applied it.
        """
        button.setEnabled(False)
        button.setText("Downloading…")

        thread = DownloadThread(self.client, url)

        def on_done(filepath):
            try:
                _add_layer_to_qgis(filepath, layer_name)
                # Matches the "Already loaded" state a reopened pane shows, so
                # the pane looks the same whether the layer was added just now
                # or in a previous session.
                button.setText("Already loaded")
                button.setIcon(icons.icon("check", design.ICON_SM,
                                          design.tokens().text_secondary))
            except Exception as e:
                QMessageBox.warning(self, "Load Error",
                                    f"Downloaded but failed to load:\n{e}")
                button.setEnabled(True)
                button.setText("Retry download")

        def on_err(msg):
            QMessageBox.warning(self, "Download Error",
                                f"Failed to download:\n{msg}")
            button.setEnabled(True)
            button.setText("Retry download")

        thread.finished.connect(on_done)
        thread.error.connect(on_err)
        self._threads.append(thread)
        thread.start()

    # ── Hide process ──

    def _on_hide_process(self, proc):
        process_name = proc.get("name", "this process")

        # Create message box with clickable link
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Hide Process")
        msg_box.setIcon(QMessageBox_Question)
        msg_box.setText(
            f'Are you sure you want to hide "{process_name}"?<br><br>'
            "This will hide the process. To fully delete it, visit:<br>"
            f'<a href="{APP_BASE_URL}/hidden-items">{APP_BASE_URL}/hidden-items</a>'
        )
        msg_box.setTextFormat(Qt_RichText)
        msg_box.setTextInteractionFlags(Qt_TextBrowserInteraction)
        msg_box.setStandardButtons(QMessageBox_Yes | QMessageBox_No)
        msg_box.setDefaultButton(QMessageBox_No)

        reply = exec_dialog(msg_box)
        if reply != QMessageBox_Yes:
            return

        slug = self._current_project.get("slug", "")
        process_id = proc.get("id")
        thread = HideProcessThread(self.client, slug, process_id)
        thread.finished.connect(
            lambda: self.load_processes(self._current_project))
        thread.error.connect(
            lambda msg: QMessageBox.warning(
                self, "Error", f"Failed to hide process:\n{msg}")
        )
        self._threads.append(thread)
        thread.start()

    # ── New process: embedded wizard ──

    def _on_new_process(self):
        if not self._current_project or not self.iface:
            return

        # Stop auto-refresh while in wizard
        self._stop_auto_refresh()

        # Remove previous wizard if any
        if self._wizard:
            self._body_stack.removeWidget(self._wizard)
            self._wizard.deleteLater()
            self._wizard = None

        slug = self._current_project.get("slug", "")
        title = self._current_project.get("title", "Project")

        self._wizard = CreateProcessWizard(
            self.client, self.iface, slug, title
        )
        self._wizard.process_created.connect(self._on_wizard_done)
        self._wizard.cancelled.connect(self._on_wizard_cancel)
        self._body_stack.addWidget(self._wizard)
        self._body_stack.setCurrentWidget(self._wizard)
        self._new_process_btn.hide()

    def _on_wizard_done(self, result):
        self._body_stack.setCurrentWidget(self._list_view)
        self._new_process_btn.show()
        if self._wizard:
            self._body_stack.removeWidget(self._wizard)
            self._wizard.deleteLater()
            self._wizard = None
        self.load_processes(self._current_project)

    def _on_wizard_cancel(self):
        self._body_stack.setCurrentWidget(self._list_view)
        self._new_process_btn.show()
        if self._wizard:
            self._body_stack.removeWidget(self._wizard)
            self._wizard.deleteLater()
            self._wizard = None
