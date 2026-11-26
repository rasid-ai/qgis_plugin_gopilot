import re
from qgis.PyQt.QtWidgets import (
    QLabel, QGridLayout, QVBoxLayout, QHBoxLayout, QWidget,
    QScrollArea, QFrame, QDialog, QLineEdit, QTextBrowser,
    QMessageBox,
)
from qgis.PyQt.QtCore import QThread, QTimer, pyqtSignal
from .compat import (
    Qt_AlignTop, Qt_AlignCenter,
    QFrame_StyledPanel,
    exec_dialog, QDialog_Accepted
)
from .image_loader import load_image
from . import design
from .formatting import format_tokens
from .widgets import (
    paint_transparent,
    Chip, WrappedLabel, apply_scroll_style, card_grid_metrics,
    clear_layout, equalise_heights, paint_background, primary_button,
    secondary_button,
)

def strip_html(text):
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()



def _price_label(sol):
    """What a run of this solution costs.

    Prefers ``gotoken_price`` — tokens per run, a fixed price whatever the
    area, which is how the product prices GoBox now. Falls back to the older
    ``euro_per_km2`` so a solution served by a backend that predates the token
    pricing still shows something rather than nothing.
    """
    tokens = format_tokens(sol.get("gotoken_price"))
    if tokens is not None:
        return f"{tokens} tokens / run"
    euros = sol.get("euro_per_km2")
    if euros is not None:
        return f"\u20ac{euros} / km\u00b2"
    return None


class FetchSolutionsThread(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            solutions = self.client.get_solutions()
            self.finished.emit(solutions)
        except Exception as e:
            self.error.emit(str(e))


class CreateProjectThread(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, client, solution_slug, title, tags=None):
        super().__init__()
        self.client = client
        self.solution_slug = solution_slug
        self.title = title
        self.tags = tags

    def run(self):
        try:
            result = self.client.create_project(self.solution_slug, self.title, self.tags)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class SolutionsPage(QWidget):
    project_created = pyqtSignal(dict)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self._threads = []
        self._solutions = []
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
        paint_background(self, design.tokens().canvas, "solutionsPage")

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
        if self._solutions:
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

    MIN_CARD_WIDTH = design.GOBOX_CARD_MIN_WIDTH

    def _grid_metrics(self):
        """(columns, card width) from the shared helper. See widgets."""
        return card_grid_metrics(self.scroll, self.grid, self.MIN_CARD_WIDTH)

    def _calculate_columns(self):
        return self._grid_metrics()[0]

    def load_solutions(self):
        self._clear_grid()
        loading = QLabel("Loading solutions...")
        loading.setAlignment(Qt_AlignCenter)
        self.grid.addWidget(loading, 0, 0)

        self._fetch_thread = FetchSolutionsThread(self.client)
        self._fetch_thread.finished.connect(self._on_solutions_loaded)
        self._fetch_thread.error.connect(self._on_solutions_error)
        self._fetch_thread.start()

    def _on_solutions_loaded(self, solutions):
        self._clear_grid()

        if not solutions:
            lbl = QLabel("No solutions found.")
            lbl.setAlignment(Qt_AlignCenter)
            self.grid.addWidget(lbl, 0, 0)
            return

        # Sort solutions: "prod" status first, "coming soon" last
        self._solutions = sorted(
            solutions,
            key=lambda s: (s.get("status", "") != "prod", s.get("name", ""))
        )

        self._layout_cards()

    def _layout_cards(self):
        """Layout cards in grid based on current width."""
        self._clear_grid()
        if not self._solutions:
            return

        cols, card_width = self._grid_metrics()
        self._last_metrics = (cols, card_width)
        # Image threads accumulate one per card per rebuild; drop the ones
        # that have finished so a long session does not hoard them.
        self._threads = [t for t in self._threads if t.isRunning()]
        row, col = 0, 0

        for sol in self._solutions:
            card = self._create_card(sol, card_width)
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

    def _on_solutions_error(self, msg):
        self._clear_grid()
        lbl = QLabel(f"Failed to load solutions: {msg}")
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

    def _create_card(self, sol, card_width=design.GOBOX_CARD_MIN_WIDTH):
        """A GoBox solution card, sized so two fit at the panel's minimum.

        Everything is STACKED rather than put on rows. The price chip and the
        button shared a row in the previous version, which needs ~235px of
        content width; a two-up card at the minimum panel width gets about
        130px, so the button was clipped. Stacked, the card works at every
        width the grid can produce.
        """
        t = design.tokens()
        card = QFrame()
        card.setFixedWidth(card_width)
        card.setObjectName("goboxCard")
        card.setStyleSheet(
            f"QFrame#goboxCard {{ background:{t.surface}; "
            f"border:{design.BORDER}px solid {t.border}; "
            f"border-radius:{design.RADIUS_LG}px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Full-bleed thumbnail, cropped to cover so it fills the card whatever
        # shape the source image is. Only the TOP corners are rounded, to sit
        # inside the card's own radius without a seam.
        thumb = QLabel()
        thumb_height = int(card_width * design.CARD_THUMB_ASPECT)
        thumb.setFixedSize(card_width, thumb_height)
        thumb.setAlignment(Qt_AlignCenter)
        thumb.setStyleSheet(
            f"background:{t.surface_alt}; border:none; "
            f"border-top-left-radius:{design.RADIUS_LG}px; "
            f"border-top-right-radius:{design.RADIUS_LG}px;"
        )
        load_image(self.client, sol.get("image_url"), thumb, self._threads,
                   size=(card_width, thumb_height), fit="cover",
                   radius=design.RADIUS_LG, round_top_only=True)
        layout.addWidget(thumb)

        content = QWidget()
        paint_transparent(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(design.SPACE_2, design.SPACE_2,
                                          design.SPACE_2, design.SPACE_2)
        content_layout.setSpacing(design.SPACE_1)

        name = WrappedLabel(sol.get("name", "Unnamed"))
        name.setStyleSheet(
            design.body_qss(design.TEXT_BASE)
            + f" font-weight:{design.WEIGHT_SEMIBOLD};"
        )
        content_layout.addWidget(name)

        price = _price_label(sol)
        if price:
            price_label = QLabel(price)
            price_label.setStyleSheet(
                design.body_qss(design.TEXT_SM, secondary=True))
            content_layout.addWidget(price_label)

        content_layout.addSpacing(design.SPACE_1)

        # Only "prod" solutions can actually be launched.
        if sol.get("status", "") == "prod":
            button = primary_button("Create project", compact=True)
            button.clicked.connect(
                lambda _=False, x=sol: self._on_create_project(x))
            content_layout.addWidget(button)
        else:
            badge_row = QHBoxLayout()
            badge_row.setContentsMargins(0, 0, 0, 0)
            badge_row.addWidget(Chip("Coming soon", tone="warning",
                                     removable=False, max_chars=20))
            badge_row.addStretch()
            content_layout.addLayout(badge_row)

        layout.addWidget(content)
        return card

    def _on_create_project(self, solution):
        slug = solution.get("slug", "")
        sol_name = solution.get("name", "Solution")
        sol_desc_raw = solution.get("description_html", "")
        result_image_url = solution.get("result_image_url", "")

        # Modern dialog with side-by-side layout
        t = design.tokens()

        dlg = QDialog(self)
        dlg.setWindowTitle("Create project")
        dlg.setFixedWidth(850)
        dlg.setFixedHeight(750)
        # Scoped by object name. This was a selector-less
        # ``setStyleSheet("background: ...")``, which Qt applies to the widget
        # AND every descendant — including a child QMessageBox, which is a
        # QDialog. That is the same leak that produced the unreadable black
        # message boxes elsewhere in the plugin, and the two QMessageBoxes
        # raised out of this very method were inheriting it.
        dlg.setObjectName("createProjectDialog")
        dlg.setStyleSheet(
            f"QDialog#createProjectDialog {{ background: {t.surface}; }}")

        main_layout = QVBoxLayout(dlg)
        main_layout.setContentsMargins(design.SPACE_5, design.SPACE_4,
                                       design.SPACE_5, design.SPACE_5)
        main_layout.setSpacing(design.SPACE_3)

        # Solution label
        sol_label = QLabel(f"Solution: {sol_name}")
        sol_label.setStyleSheet(design.heading_qss(design.TEXT_LG))
        main_layout.addWidget(sol_label)

        # Horizontal layout for side-by-side content
        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        # LEFT SIDE: Description (scrollable)
        if sol_desc_raw:
            import markdown
            html_desc = markdown.markdown(
                sol_desc_raw,
                extensions=['tables', 'fenced_code', 'nl2br']
            )

            desc_browser = QTextBrowser()
            desc_browser.setObjectName("solutionDescription")
            desc_browser.setOpenExternalLinks(True)
            desc_browser.setFixedHeight(660)  # Match the right side total height

            desc_browser.setStyleSheet(
                f"""
                QTextBrowser#solutionDescription {{
                    background: {t.surface_sunken};
                    border: {design.BORDER}px solid {t.border};
                    border-radius: {design.RADIUS}px;
                    padding: {design.SPACE_3}px;
                    color: {t.text_secondary};
                    font-family: {design.FONT_STACK};
                    font-size: {design.TEXT_BASE}px;
                }}
                """
                + design.scrollbar_qss("QTextBrowser#solutionDescription")
            )

            # The _HTML font stacks, not the plain ones: these are
            # single-quoted, and Qt's rich-text parser drops a CSS declaration
            # whose value contains the same double quote that closes the
            # attribute it sits in.
            desc_browser.setHtml(f"""
                <style>
                    body {{
                        color: {t.text_secondary};
                        font-family: {design.FONT_STACK_HTML};
                        font-size: {design.TEXT_BASE}px;
                        line-height: 1.5;
                    }}
                    table {{
                        border-collapse: collapse;
                        width: 100%;
                        margin: {design.SPACE_2}px 0;
                    }}
                    td, th {{
                        border: {design.BORDER}px solid {t.border};
                        padding: 6px 8px;
                        text-align: left;
                    }}
                    th {{
                        background-color: {t.surface_alt};
                        font-weight: {design.WEIGHT_SEMIBOLD};
                    }}
                    code {{
                        background: {t.surface_alt};
                        padding: 2px 4px;
                        border-radius: {design.RADIUS_XS}px;
                        font-family: {design.MONO_STACK_HTML};
                    }}
                    strong {{
                        color: {t.text};
                    }}
                </style>
                {html_desc}
            """)

            content_layout.addWidget(desc_browser, stretch=1)

        # RIGHT SIDE: Form inputs (fixed container to match left side height)
        right_container = QWidget()
        right_container.setFixedHeight(660)

        form_layout = QVBoxLayout(right_container)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)

        # Form section label
        form_label = QLabel("Create a project")
        form_label.setStyleSheet(design.heading_qss(design.TEXT_MD))
        form_layout.addWidget(form_label)

        # Project title. The two field labels were bare QLabels with no
        # styling at all, so they rendered in the host application's font
        # while their own inputs were in Inter.
        title_label = QLabel("Project title")
        title_label.setStyleSheet(design.field_label_qss())
        form_layout.addWidget(title_label)

        title_input = QLineEdit()
        title_input.setPlaceholderText("Enter project title...")
        title_input.setStyleSheet(design.input_qss())
        form_layout.addWidget(title_input)

        # Tags
        tags_label = QLabel("Tags (separated by spaces)")
        tags_label.setStyleSheet(design.field_label_qss())
        form_layout.addWidget(tags_label)

        tags_input = QLineEdit()
        tags_input.setPlaceholderText("e.g. cloud detection urban")
        tags_input.setStyleSheet(design.input_qss())
        form_layout.addWidget(tags_input)

        # Add stretch to push buttons to bottom
        form_layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        # Primary FIRST, Cancel second — see drawing_help_dialog for why this
        # deliberately differs from the SaaS footer.
        #
        # No icon — the SaaS renders this as the bare string "Create Project".
        create_btn = primary_button("Create project")
        create_btn.setDefault(True)
        create_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(create_btn)

        cancel_btn = secondary_button("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addWidget(cancel_btn)

        form_layout.addLayout(btn_layout)

        # Result preview image
        if result_image_url:
            # Label
            preview_label = QLabel("Result preview")
            preview_label.setStyleSheet(
                design.field_label_qss() + " margin-top: 12px;")
            form_layout.addWidget(preview_label)

            # Image container. The fill and border read from tokens directly:
            # they used to reuse `sidebar_bg` and `card_border`, which are
            # assigned inside the `if sol_desc_raw:` branch above — so a
            # solution carrying a result image but NO description raised
            # NameError here instead of opening the dialog.
            image_container = QFrame()
            image_container.setObjectName("resultPreview")
            image_container.setFrameShape(QFrame_StyledPanel)
            image_container.setMinimumHeight(380)
            image_container.setMaximumHeight(380)
            image_container.setStyleSheet(f"""
                QFrame#resultPreview {{
                    background: {t.surface_sunken};
                    border: {design.BORDER}px solid {t.border};
                    border-radius: {design.RADIUS}px;
                }}
            """)

            container_layout = QVBoxLayout(image_container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(0)

            # Image label that fills the container
            image_label = QLabel()
            image_label.setAlignment(Qt_AlignCenter)
            image_label.setScaledContents(True)  # Scale to fill
            image_label.setMinimumSize(1, 1)  # Allow it to expand
            paint_transparent(image_label, "resultPreviewImage")

            container_layout.addWidget(image_label)

            # Load the image at high resolution (1024x1024), will be scaled to fit container
            load_image(self.client, result_image_url, image_label, self._threads,size=(1024, 1024))
            form_layout.addWidget(image_container)

        # Add right container to content layout
        content_layout.addWidget(right_container, stretch=1)

        # Add content layout to main
        main_layout.addLayout(content_layout)

        if exec_dialog(dlg) != QDialog_Accepted:
            return

        title = title_input.text().strip()
        if not title:
            QMessageBox.warning(self, "Missing Title", "Please enter a project title.")
            return

        tags = [t.strip() for t in tags_input.text().split() if t.strip()] or None

        self._create_thread = CreateProjectThread(self.client, slug, title, tags)
        self._create_thread.finished.connect(self._on_project_created)
        self._create_thread.error.connect(self._on_create_error)
        self._create_thread.start()

    def _on_project_created(self, result):
        QMessageBox.information(
            self, "Project Created",
            f"Project '{result.get('title', '')}' created successfully!"
        )
        self.project_created.emit(result)

    def _on_create_error(self, msg):
        QMessageBox.critical(self, "Error", f"Failed to create project:\n{msg}")