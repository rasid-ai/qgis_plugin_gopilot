from qgis.PyQt.QtWidgets import (
    QLabel, QHBoxLayout, QGridLayout,
    QVBoxLayout, QWidget, QScrollArea, QMessageBox,
)
from qgis.PyQt.QtCore import QThread, QTimer, pyqtSignal
from .compat import (
    Qt_AlignTop, Qt_AlignCenter, Qt_AlignVCenter,
    Qt_RichText, Qt_TextBrowserInteraction,
    exec_dialog,
    QMessageBox_Question, QMessageBox_Yes, QMessageBox_No
)
from .image_loader import load_image
from . import design
from .widgets import (
    paint_transparent,
    Chip, ClickableCard, Divider, IconButton, WrappedLabel,
    apply_scroll_style, card_grid_metrics, clear_layout, equalise_heights,
    paint_background,
)
from .config import APP_BASE_URL



def _tags_that_fit(card_width):
    """How many tag chips a card this wide can show without clipping.

    A ``QHBoxLayout`` does not wrap, so chips that overflow are cut off rather
    than reflowed onto a second line. The count therefore has to be chosen
    from the width rather than discovered from the layout. Three is what the
    web card shows.
    """
    if card_width < 185:
        return 1
    if card_width < 265:
        return 2
    return 3


def _solution_name(proj):
    """The solution's name, or "Custom" as the SaaS card does."""
    solution = proj.get("solution", {})
    if isinstance(solution, dict):
        return solution.get("name") or "Custom"
    return str(solution) if solution else "Custom"


def _process_count(proj):
    """"3 processes", or None when the API did not say."""
    count = proj.get("process_count")
    if count is None:
        return None
    return "1 process" if count == 1 else f"{count} processes"


def _modified_date(proj):
    """The modification date as "04 Nov 2026", or None."""
    raw = proj.get("system_modification_date", "")
    if not raw:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(
            raw.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return raw.split("T")[0] if "T" in raw else raw


class FetchProjectsThread(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            projects = self.client.get_user_projects()
            self.finished.emit(projects)
        except Exception as e:
            self.error.emit(str(e))


class HideProjectThread(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, client, project_slug):
        super().__init__()
        self.client = client
        self.project_slug = project_slug

    def run(self):
        try:
            self.client.hide_project(self.project_slug)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class _ProjectCard(ClickableCard):
    """A project card: the SaaS component's content at GoBox density.

    The web card's rows, and where each one went::

        web                                   here
        ----------------------------------    --------------------------------
        aspect-video cover thumbnail          same
        text-lg bold title (2 lines)          same, one type step smaller
        [solution chip]        <date>         chip on its own row
        <strong>Processes:</strong> N         count and date share a row
        [#tags]                  [trash]      same, tag count scales
        border-b under the meta row           same

    Only ONE thing is rearranged: the solution chip and the date shared a row
    on the web, which needs about 250px. Keeping that row would have forced a
    230px card floor — one column in a 400px panel, and cards that felt huge.
    Moving the date down beside the process count fits the whole card in 156px
    and keeps every element.

    The tag count scales with width (1 / 2 / 3) because a QHBoxLayout does not
    wrap: chips that do not fit are clipped rather than reflowed, so the
    number shown has to be chosen rather than discovered.
    """

    delete_requested = pyqtSignal()

    def __init__(self, proj, card_width, client, threads, parent=None):
        super().__init__("projectCard", parent)
        self._proj = proj
        self.setFixedWidth(card_width)

        t = design.tokens()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── thumbnail: aspect-video, object-cover, rounded to the card ──
        thumb_height = int(card_width * design.CARD_THUMB_ASPECT)
        thumb = QLabel()
        thumb.setFixedSize(card_width, thumb_height)
        thumb.setAlignment(Qt_AlignCenter)
        thumb.setStyleSheet(
            f"background:{t.surface_alt}; border:none; "
            f"border-top-left-radius:{design.RADIUS_LG}px; "
            f"border-top-right-radius:{design.RADIUS_LG}px;"
        )
        load_image(client, proj.get("thumbnail"), thumb, threads,
                   size=(card_width, thumb_height), fit="cover",
                   radius=design.RADIUS_LG, round_top_only=True)
        layout.addWidget(thumb)

        content = QWidget()
        paint_transparent(content)
        body = QVBoxLayout(content)
        body.setContentsMargins(design.SPACE_2, design.SPACE_2,
                                design.SPACE_2, design.SPACE_2)
        body.setSpacing(design.SPACE_1)

        self._title = WrappedLabel(proj.get("title", "Untitled"))
        self._paint_title(hovered=False)
        body.addWidget(self._title)

        body.addWidget(Divider())

        solution_row = QHBoxLayout()
        solution_row.setContentsMargins(0, 0, 0, 0)
        solution_row.addWidget(Chip(_solution_name(proj), tone="neutral",
                                    removable=False, max_chars=20,
                                    radius=design.RADIUS_XS))
        solution_row.addStretch()
        body.addLayout(solution_row)

        # Process count and date share this row; on the web the date sat up
        # beside the solution chip, which does not fit at this width.
        count = proj.get("processes_number", proj.get("process_count"))
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(design.SPACE_1)
        processes = QLabel(
            f'<span style="color:{t.text}; font-weight:'
            f'{design.WEIGHT_SEMIBOLD};">Processes:</span> '
            f'<span style="color:{t.text_secondary};">'
            f'{0 if count is None else count}</span>'
        )
        processes.setStyleSheet(
            f"background:transparent; border:none; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_SM}px;"
        )
        meta_row.addWidget(processes)
        meta_row.addStretch()
        date = _modified_date(proj)
        if date:
            date_label = QLabel(date)
            date_label.setStyleSheet(
                design.body_qss(design.TEXT_XS, secondary=True))
            meta_row.addWidget(date_label, alignment=Qt_AlignVCenter)
        body.addLayout(meta_row)

        body.addStretch()

        # ── footer: #tags on the left, delete on the right ──
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(design.SPACE_1)
        tags = [tag.get("name", str(tag)) if isinstance(tag, dict) else str(tag)
                for tag in (proj.get("tags") or [])]
        shown = _tags_that_fit(card_width)
        for name in tags[:shown]:
            footer.addWidget(Chip(f"#{name}", tone="accent", removable=False,
                                  max_chars=10, radius=design.RADIUS_XS))
        if len(tags) > shown:
            more = QLabel(f"+{len(tags) - shown}")
            more.setStyleSheet(design.body_qss(design.TEXT_XS, secondary=True))
            footer.addWidget(more, alignment=Qt_AlignVCenter)
        footer.addStretch()

        # Barely there until you want it — slate-300 on the web, reddening on
        # hover. A trash at full strength on every card in a grid reads as a
        # row of warnings.
        delete_btn = IconButton("trash", size=24, icon_size=13,
                                tooltip="Move to trash", tone="muted",
                                hover_tone="danger")
        delete_btn.clicked.connect(self.delete_requested.emit)
        footer.addWidget(delete_btn, alignment=Qt_AlignVCenter)
        body.addLayout(footer)

        layout.addWidget(content)

    def _paint_title(self, hovered):
        t = design.tokens()
        self._title.setStyleSheet(
            f"background:transparent; border:none; "
            f"color:{t.accent_text if hovered else t.text}; "
            f"font-family:{design.FONT_STACK}; "
            f"font-size:{design.TEXT_BASE}px; "
            f"font-weight:{design.WEIGHT_SEMIBOLD};"
        )

    def on_hover_changed(self, hovered):
        """The web card turns its title brand-green on hover."""
        if getattr(self, "_title", None) is not None:
            self._paint_title(hovered)


class ProjectsPage(QWidget):
    project_opened = pyqtSignal(dict)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self._threads = []
        self._projects = []
        self._last_metrics = None
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(design.GRID_RELAYOUT_DELAY_MS)
        self._relayout_timer.timeout.connect(self._relayout_if_needed)
        # Paint the brand canvas, scoped by object name.
        #
        # Without this the page showed whatever QGIS's palette uses for
        # Window, while chat / history / settings all painted `canvas` — so
        # the background shifted as you moved between views, and in a dark
        # QGIS theme it shifted from a warm dark to a cool one.
        #
        # The selector matters: a bare `background:` in a stylesheet set on a
        # parent also matches every descendant, which would flood the cards
        # with the canvas colour too.
        paint_background(self, design.tokens().canvas, "projectsPage")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        apply_scroll_style(self.scroll)
        outer.addWidget(self.scroll)

        self._inner = QWidget()
        paint_transparent(self._inner)
        self.grid = QGridLayout(self._inner)
        self.grid.setSpacing(design.CARD_GRID_SPACING)
        self.grid.setContentsMargins(
            design.CARD_GRID_MARGIN, design.CARD_GRID_MARGIN,
            design.CARD_GRID_MARGIN, design.CARD_GRID_MARGIN)
        # AlignTop only, no AlignHCenter: centring made the grid claim just
        # its cards' fixed width and left the rest as dead space — 37px of
        # grey down each side of a single fixed-width card in a docked panel.
        self.grid.setAlignment(Qt_AlignTop)
        self.scroll.setWidget(self._inner)

    def resizeEvent(self, event):
        """Schedule a relayout; do not perform one per resize event.

        Dragging the panel edge emits a resize for every mouse move, and
        `_layout_cards` destroys and rebuilds every card and re-requests every
        thumbnail. Measured on a 14-item catalogue that was 840 card rebuilds
        across 60 steps, 42ms each — the lag you feel in the drag. The timer
        restarts on each event, so the burst collapses into one rebuild once
        the drag settles.
        """
        super().resizeEvent(event)
        if self._projects:
            self._relayout_timer.start()

    def _relayout_if_needed(self):
        """Rebuild only if the grid's shape actually changed.

        Most resizes do not change the column count or the card width by
        enough to matter, and rebuilding identical cards is pure waste — it
        also restarts every thumbnail download.
        """
        metrics = self._grid_metrics()
        if metrics == self._last_metrics:
            return
        self._layout_cards()

    MIN_CARD_WIDTH = design.PROJECT_CARD_MIN_WIDTH

    def _grid_metrics(self):
        """(columns, card width) from the shared helper. See widgets."""
        return card_grid_metrics(self.scroll, self.grid, self.MIN_CARD_WIDTH)

    def _calculate_columns(self):
        return self._grid_metrics()[0]

    def load_projects(self):
        self._clear_grid()
        loading = QLabel("Loading projects...")
        loading.setAlignment(Qt_AlignCenter)
        self.grid.addWidget(loading, 0, 0)

        self._fetch_thread = FetchProjectsThread(self.client)
        self._fetch_thread.finished.connect(self._on_projects_loaded)
        self._fetch_thread.error.connect(self._on_projects_error)
        self._fetch_thread.start()

    def _on_projects_loaded(self, projects):
        self._clear_grid()
        self._projects = projects

        if not projects:
            lbl = QLabel("No projects found.")
            lbl.setAlignment(Qt_AlignCenter)
            # body_qss, so this carries a font-family. The hand-rolled rule
            # set a colour and a size but no family, which left the one
            # message on an empty Projects page rendering in the host
            # application's font.
            lbl.setStyleSheet(design.body_qss(design.TEXT_MD, secondary=True))
            self.grid.addWidget(lbl, 0, 0)
            return

        self._layout_cards()

    def _layout_cards(self):
        """Layout cards in grid based on current width."""
        self._clear_grid()
        if not self._projects:
            return

        cols, card_width = self._grid_metrics()
        self._last_metrics = (cols, card_width)
        # Image threads accumulate one per card per rebuild; drop the ones
        # that have finished so a long session does not hoard them.
        self._threads = [t for t in self._threads if t.isRunning()]
        row, col = 0, 0

        for proj in self._projects:
            card = self._create_card(proj, card_width)
            self.grid.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

        # Park the leftover vertical space in an empty row BELOW the cards.
        #
        # The scroll area stretches its content widget to the viewport height
        # (widgetResizable), and a QGridLayout with no stretch row hands that
        # surplus to its card rows — so four cards in a tall panel were pulled
        # from their natural 211px to 342px each. The cards looked enormous
        # and nothing in their own sizing explained it, because nothing in
        # their own sizing was responsible.
        #
        # Stretches persist on a QGridLayout across rebuilds, so the old ones
        # have to be cleared or a previous shape's row keeps the space.
        for stale in range(self.grid.rowCount() + 1):
            self.grid.setRowStretch(stale, 0)
        self.grid.setRowStretch(row + (1 if col else 0), 1)

        # Deferred by one turn of the event loop: the cards' wrapped titles
        # pin their own heights from THEIR resizeEvent, which has not run yet,
        # so measuring now would read the previous layout's wrapping.
        QTimer.singleShot(0, self._equalise_card_heights)

    def _equalise_card_heights(self):
        """One height for every card, so the grid does not step row by row."""
        equalise_heights([self.grid.itemAt(i).widget()
                          for i in range(self.grid.count())])

    def _on_projects_error(self, msg):
        self._clear_grid()
        lbl = QLabel(f"Failed to load projects: {msg}")
        lbl.setAlignment(Qt_AlignCenter)
        lbl.setWordWrap(True)
        self.grid.addWidget(lbl, 0, 0)

    def _clear_grid(self):
        """Empty the grid, unparenting as well as deleting.

        ``deleteLater()`` alone only SCHEDULES deletion. Until the event loop
        gets round to it the widget is still a child of the grid host and still
        visible — but no longer managed by any layout, so it keeps whatever
        geometry it last had and paints on top of the cards that replace it.
        That is where the stray clipped label floating over the top of the
        GoBox grid came from. `clear_layout` unparents first, so it cannot
        paint again.
        """
        clear_layout(self.grid)

    def _create_card(self, proj, card_width=design.PROJECT_CARD_MIN_WIDTH):
        """One project card. See :class:`_ProjectCard`."""
        card = _ProjectCard(proj, card_width, self.client, self._threads)
        card.clicked.connect(lambda x=proj: self.project_opened.emit(x))
        card.delete_requested.connect(lambda x=proj: self._on_hide(x))
        return card

    def _on_hide(self, proj):
        title = proj.get("title", "this project")

        # Create message box with clickable link
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Hide Project")
        msg_box.setIcon(QMessageBox_Question)
        msg_box.setText(
            f"Are you sure you want to hide '{title}'?<br><br>"
            "This will move the project to your hidden items. "
            "To permanently delete it, visit:<br>"
            f'<a href="{APP_BASE_URL}/hidden-items">{APP_BASE_URL}/hidden-items</a>'
        )
        msg_box.setTextFormat(Qt_RichText)
        msg_box.setTextInteractionFlags(Qt_TextBrowserInteraction)
        msg_box.setStandardButtons(QMessageBox_Yes | QMessageBox_No)
        msg_box.setDefaultButton(QMessageBox_No)

        reply = exec_dialog(msg_box)
        if reply != QMessageBox_Yes:
            return

        slug = proj.get("slug", "")
        t = HideProjectThread(self.client, slug)
        t.finished.connect(self.load_projects)
        t.error.connect(lambda msg: QMessageBox.critical(
            self, "Hide Failed", f"Could not hide project:\n{msg}"
        ))
        self._threads.append(t)
        t.start()
