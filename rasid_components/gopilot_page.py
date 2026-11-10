"""The GoPilot chat view: transcript, status strip and composer.

WHAT CHANGED FROM THE WINDOWED VERSION
======================================
This used to be a ``QSplitter`` with a permanent 200px history pane on the left
and the conversation on the right, inside a 1087px modal dialog. Docked to the
side of the QGIS window there is no room for that, and no need: history moved
to its own view behind the rail (:mod:`history_panel`), the composer moved to
:mod:`composer`, and the bubble moved to :mod:`message_bubble`. What is left
here is the transcript and the turn-taking logic.

BUBBLE WIDTH IS COMPUTED, NOT CONSTANT
======================================
Bubbles used to be capped at a hardcoded 560px — wider than the entire dock, so
every one of them was clipped at the panel edge. :meth:`_apply_bubble_widths`
re-caps every bubble from the viewport width on each resize. Assistant messages
get nearly the full width (they carry tables and code); the user's own get 86%,
so the conversation still reads as two columns rather than one wall.

MODALITY
========
Nothing here is modal. The old dialog ran under ``exec()``, which blocked the
whole QGIS main window — so you could not pan the map to look at what you were
asking about. Docked and non-modal, drawing an area no longer needs the panel to
minimise itself first.
"""

import os

from qgis.PyQt.QtCore import QDateTime, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from . import branding, design, icons
from .chat_threads import (
    FetchUploadLimitsThread,
    HideMessageThread,
    FetchMessagesThread, FetchPromptSuggestionsThread,
    SendMessageThread, stop_threads, thinking_text,
)
from .compat import (
    QMessageBox_No,
    QMessageBox_Yes,
    Qt_AlignCenter, Qt_AlignVCenter, Qt_LeftButton, Qt_PointingHandCursor,
)
from .composer import Composer
from .geometry import GeometryInputManager
from .logger import debug_print
from .message_bubble import MessageBubble
from .widgets import (
    paint_transparent,
    paint_background,
    WrappedLabel, body_label, enable_height_for_width, heading,
    styled_scroll_area,
)

# Shown on an empty chat. Static rather than server-driven (the SaaS fetches
# these): the plugin's useful openers are about the user's OWN loaded layers,
# which only the plugin knows about.
# (icon, title, description). The title is what gets typed into the composer;
# the description says what will happen, so the card is a promise rather than
# just a phrase. Concrete, named places on purpose — "Analyse a layer" taught
# nobody what GoPilot can actually do, while "Count the trees in Kensington
# Gardens" does.
#
# Static rather than server-driven (the SaaS fetches its own set): the plugin's
# useful openers are the ones that touch the user's QGIS project, which only
# the plugin knows about. The last two are exactly those.
SUGGESTIONS = (
    ("map-pin", "Find the Three Gorges Dam",
     "Locate it and frame the map there"),
    ("layers", "Map buildings within 2 km of the Eiffel Tower",
     "Building footprints inside a 2 km radius"),
    ("sparkle", "How severely did the 2023 Maui fires burn?",
     "Burn severity across the wildfire zone"),
    ("search", "Count the trees in Kensington Gardens",
     "Detection and a total, London"),
    ("alert", "Flood risk layers for the Nile Delta",
     "Find the exposure layers, then add them to my project"),
    ("draw", "Classify land cover for an area I draw",
     "Draw an AOI and get a classified raster back"),
)


class _SuggestionCard(QFrame):
    """One opener: an icon badge, a title, and what it will do.

    A ``QFrame`` rather than a ``QPushButton`` because a button draws ONE
    string in ONE style. This needs a title and a description at different
    sizes and weights, both word-wrapped, next to a tinted badge — so the card
    is a frame of real labels and supplies the button behaviour itself
    (pointer cursor, hover wash, click signal).
    """

    clicked = pyqtSignal()

    BADGE = 26
    TEXT_SPACING = 2

    def __init__(self, icon_name, title, description, prompt=None,
                 parent=None):
        super().__init__(parent)
        self.title = title
        # What gets SENT, which the API keeps separate from what the card
        # SHOWS: `prompt_text` can be a paragraph of instruction standing
        # behind a six-word label. Falls back to the title for the static set,
        # whose titles were written to be sendable as they are.
        self.prompt = prompt or title
        self.setObjectName("suggestionCard")
        self.setCursor(Qt_PointingHandCursor)
        self.setToolTip(description)
        enable_height_for_width(self)

        t = design.tokens()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 11, 9)
        lay.setSpacing(design.SPACE_2)

        badge = QLabel()
        badge.setFixedSize(self.BADGE, self.BADGE)
        badge.setAlignment(Qt_AlignCenter)
        badge.setPixmap(icons.pixmap(icon_name, 14, t.accent, dpr=2.0))
        badge.setStyleSheet(
            f"background:{t.accent_soft}; border:none; "
            f"border-radius:{design.RADIUS_SM}px;"
        )
        # Centred, not top-aligned: every card is padded to the height of the
        # tallest one (see _SuggestionGrid._equalise_heights), so a top-aligned
        # badge on a one-line card would float away from the text beside it.
        lay.addWidget(badge, alignment=Qt_AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(self.TEXT_SPACING)
        # Stretches so the title+description block sits in the vertical centre
        # of whatever height the card has been padded to.
        text_col.addStretch()

        self._title = WrappedLabel(title)
        self._title.setAlignment(Qt_AlignCenter)
        self._title.setStyleSheet(
            f"background:transparent; border:none; color:{t.text}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_BASE}px; "
            f"font-weight:{design.WEIGHT_SEMIBOLD};"
        )
        text_col.addWidget(self._title)

        self._description = WrappedLabel(description)
        self._description.setAlignment(Qt_AlignCenter)
        self._description.setStyleSheet(
            f"background:transparent; border:none; color:{t.text_muted}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_SM}px;"
        )
        text_col.addWidget(self._description)
        text_col.addStretch()
        lay.addLayout(text_col, stretch=1)

        self._apply_style(hover=False)

    def required_height(self):
        """Height this card needs for its own text at its current width.

        Read from the labels' pinned minimum heights rather than from
        ``sizeHint()``: a wrapped label's sizeHint is computed at a width Qt
        chooses heuristically, not the width the label actually has, so two
        cards with the same real wrapping reported different hints (78 vs 63).
        :class:`~.widgets.WrappedLabel` maintains an exact minimum height for
        the width it is really at, which is the honest number.
        """
        margins = self.layout().contentsMargins()
        text = (self._title.minimumHeight() + self.TEXT_SPACING
                + self._description.minimumHeight())
        content = margins.top() + margins.bottom() + max(self.BADGE, text)
        # Plus the BORDER. `_apply_style` draws a 1px border, and a styled
        # border is part of the frame, so Qt reserves it outside the layout's
        # content rect: the layout wanted 70px here while the card the layout
        # produced was 72. Omitting it made this figure 2px short of the real
        # height, so the uniform minimum handed to every card was 2px under
        # what the tallest of them actually took — the tall cards overshot it
        # and only the one short card sat at it. That is the "six cards, two
        # heights" this is supposed to prevent.
        return content + 2 * self.frameWidth()

    def _apply_style(self, hover):
        t = design.tokens()
        self.setStyleSheet(f"""
            QFrame#suggestionCard {{
                background: {t.accent_soft if hover else t.surface};
                border: 1px solid {t.accent if hover else t.border};
                border-radius: {design.RADIUS}px;
            }}
        """)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._apply_style(hover=True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._apply_style(hover=False)

    def mouseReleaseEvent(self, event):
        # Release, not press: a click is only a click if the pointer is still
        # on the card, so dragging off it cancels, as a button would.
        if event.button() == Qt_LeftButton and self.rect().contains(
                event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _SuggestionGrid(QWidget):
    """The openers, in one or two columns depending on the width available.

    Two columns is the shape of the design, but it is not affordable at every
    size: the panel's content column is ~374px at the default dock width, which
    would leave each card about 140px for its text and wrap
    "Map buildings within 2 km of the Eiffel Tower" over five lines. So the
    grid reflows — one column when docked narrow, two once there is room for
    both to stay readable (undocked, or a widened dock).

    Mirrors the SaaS, whose own suggestion grid is ``grid-cols-1
    sm:grid-cols-2``.
    """

    # Two cards plus the gap, at the ~230px each needs to read comfortably.
    TWO_COLUMN_MIN = 470

    def __init__(self, items, parent=None):
        super().__init__(parent)
        paint_transparent(self)
        enable_height_for_width(self)

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(design.SPACE_2)
        self._grid.setVerticalSpacing(design.SPACE_2)

        # Items are (icon, title, description) or
        # (icon, title, description, prompt_text).
        self.cards = [_SuggestionCard(*item) for item in items]
        self._columns = 0
        self._card_height = 0
        self._reflow(1)

    def _reflow(self, columns):
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards:
            self._grid.removeWidget(card)
        for index, card in enumerate(self.cards):
            self._grid.addWidget(card, index // columns, index % columns)
        # Equal columns, and clear any stretch left from the previous shape.
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def _equalise_heights(self):
        """Give every card the height of the tallest one.

        A grid equalises each ROW, not the whole set, so with one column every
        card kept its own height and with two the rows stepped against each
        other — six cards at four different heights. Uniform height makes the
        set read as one block of options rather than a ragged list, and it is
        what the design calls for.

        Re-run whenever the width changes, because wrapping — and therefore
        the tallest card — changes with it.
        """
        if not self.cards:
            return
        tallest = max(card.required_height() for card in self.cards)
        if tallest <= 0 or tallest == self._card_height:
            return
        self._card_height = tallest
        for card in self.cards:
            # A MINIMUM, not a fixed height. required_height() reads the
            # labels' pinned minimums, which are only correct once they have
            # been laid out — so if this runs early the figure can come out
            # short. A minimum degrades to "not quite level"; a fixed height
            # would crush the text on top of the badge.
            card.setMinimumHeight(tallest)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(2 if self.width() >= self.TWO_COLUMN_MIN else 1)
        # Deferred by one turn of the event loop: the cards' labels pin their
        # own heights from THEIR resizeEvent, which has not run yet at this
        # point, so measuring now would read the previous width's wrapping.
        QTimer.singleShot(0, self._equalise_heights)


class GoPilotPage(QWidget):
    """The chat view. Owns one conversation at a time."""

    session_changed = pyqtSignal(str)       # a new/opened session id
    history_changed = pyqtSignal()          # the session list needs reloading
    busy_changed = pyqtSignal(bool)

    def __init__(self, client, iface=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.iface = iface
        self.current_chat_id = None
        self._threads = []
        self._bubbles = []
        self._empty_state = None
        self._thinking_row = None
        self._live_bubble = None
        self._live_thinking = None
        self._live_tool_calls = None
        self._fetched_suggestions = None
        self._suggestions_requested = False
        self._limits_requested = False
        self._suggestion_grid = None
        self._streaming_bubble = None

        self.geo_manager = GeometryInputManager(iface, parent_widget=self)
        self.geo_manager.geometry_ready.connect(self._on_geometry_ready)
        self.geo_manager.draw_cancelled.connect(
            lambda: self._set_status("", busy=False))

        t = design.tokens()
        paint_background(self, t.canvas, "gopilotChatPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = styled_scroll_area()
        self._transcript = QWidget()
        paint_transparent(self._transcript)
        self._messages = QVBoxLayout(self._transcript)
        self._messages.setContentsMargins(design.SPACE_3, design.SPACE_3,
                                          design.SPACE_3, design.SPACE_3)
        self._messages.setSpacing(design.SPACE_2)
        self._messages.addStretch()
        self._scroll.setWidget(self._transcript)
        root.addWidget(self._scroll, stretch=1)

        root.addWidget(self._build_status_strip())

        self.composer = Composer()
        self.composer.submitted.connect(self._on_submit)
        self.composer.layer_requested.connect(self.pick_layer)
        self.composer.draw_requested.connect(self.start_draw)
        root.addWidget(self.composer)

        self.start_new_chat()

    # ── chrome ────────────────────────────────────────────────────────────────
    def _build_status_strip(self):
        """A thin strip that appears only while something is happening."""
        t = design.tokens()
        self._status_host = QFrame()
        self._status_host.setObjectName("statusStrip")
        self._status_host.setStyleSheet(
            f"QFrame#statusStrip {{ background:{t.accent_soft}; "
            f"border:none; border-top:1px solid {t.accent_soft_border}; }}"
        )
        lay = QHBoxLayout(self._status_host)
        lay.setContentsMargins(design.SPACE_3, 5, design.SPACE_3, 5)
        lay.setSpacing(design.SPACE_2)

        self._status_icon = QLabel()
        self._status_icon.setPixmap(
            icons.pixmap("sparkle", 13, t.accent_text, dpr=2.0))
        paint_transparent(self._status_icon, border=True)
        lay.addWidget(self._status_icon)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            f"background:transparent; border:none; color:{t.accent_text}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_SM}px; "
            f"font-weight:{design.WEIGHT_MEDIUM};"
        )
        lay.addWidget(self._status_label, stretch=1)
        self._status_host.setVisible(False)
        return self._status_host

    def _set_status(self, text, busy=None):
        """Show (or hide, when ``text`` is empty) the status strip."""
        self._status_label.setText(text or "")
        self._status_host.setVisible(bool(text))
        if busy is not None:
            self.composer.set_busy(busy)
            self.busy_changed.emit(busy)

    # ── transcript plumbing ───────────────────────────────────────────────────
    def _clear_transcript(self):
        self._bubbles = []
        self._empty_state = None
        self._thinking_row = None
        self._streaming_bubble = None
        while self._messages.count():
            item = self._messages.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._messages.addStretch()

    def _bubble_budget(self, role):
        """Max bubble width for ``role`` at the current viewport width."""
        viewport = self._scroll.viewport()
        width = viewport.width() if viewport is not None else self.width()
        # Minus the transcript's own horizontal padding.
        usable = max(MessageBubble.MIN_WIDTH, width - design.SPACE_3 * 2 - 2)
        return usable if role != "user" else int(usable * 0.86)

    def _add_bubble(self, bubble, side):
        """Insert ``bubble`` in an alignment row, before the trailing stretch.

        Wrapping it in an HBox with a stretch on the opposite side is the one
        reliable way to pin alignment AND let the bubble hug its content width:
        a layout alignment flag alone makes it claim the full row.
        """
        row = QWidget()
        paint_transparent(row)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if side == "right":
            lay.addStretch()
            lay.addWidget(bubble)
        elif side == "center":
            lay.addStretch()
            lay.addWidget(bubble)
            lay.addStretch()
        else:
            lay.addWidget(bubble)
            lay.addStretch()

        bubble.set_max_width(self._bubble_budget(bubble.role))
        self._bubbles.append(bubble)
        self._messages.insertWidget(max(0, self._messages.count() - 1), row)
        # Deferred: the bubble has not been laid out yet, so its height (and
        # therefore the scroll maximum) is not known until the event loop turns.
        QTimer.singleShot(0, self._scroll_to_bottom)
        return row

    def add_message(self, text, is_user=True, timestamp=None,
                    attachments=None, thinking=None, tool_calls=None,
                    message_id=None):
        """Add one message to the transcript.

        ``thinking`` and ``tool_calls`` are what the model did on the way to
        ``text``. They are parameters rather than something only the live turn
        can set, because a reopened chat has to render the same thing: the
        history loader had no way to pass them, so reasoning and tool activity
        existed only until you navigated away and then were gone for good,
        even though the API returns them with every message.
        """
        bubble = MessageBubble(
            text, role="user" if is_user else "ai", timestamp=timestamp,
            client=self.client, iface=self.iface, attachments=attachments,
            message_id=message_id,
        )
        bubble.delete_requested.connect(self._on_delete_message)
        if not is_user:
            if thinking:
                bubble.set_thinking(thinking, streaming=False)
            if tool_calls:
                bubble.set_tool_calls(tool_calls, streaming=False)
        return self._add_bubble(bubble, "right" if is_user else "left")

    def _add_system_message(self, text, tone="neutral"):
        bubble = MessageBubble(text, role="system", with_time=False,
                               client=self.client, iface=self.iface, tone=tone)
        return self._add_bubble(bubble, "center")

    def _scroll_to_bottom(self):
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_bubble_widths()

    def _apply_bubble_widths(self):
        """Re-cap every bubble after a resize (including a dock drag)."""
        for bubble in list(self._bubbles):
            try:
                bubble.set_max_width(self._bubble_budget(bubble.role))
            except RuntimeError:
                # The bubble was deleted by a transcript reload mid-resize.
                self._bubbles.remove(bubble)

    # ── empty state ───────────────────────────────────────────────────────────
    def _show_empty_state(self):
        """Brand, headline, and the openers — the screen every session starts on."""
        host = QWidget()
        paint_transparent(host)
        enable_height_for_width(host)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(design.SPACE_2, design.SPACE_5,
                                 design.SPACE_2, design.SPACE_4)
        outer.setSpacing(design.SPACE_2)

        # The wordmark lives HERE rather than in a header bar. The panel has no
        # title bar of its own (see gopilot_dock), so this is where the product
        # names itself — on the one screen the user always starts from, at the
        # size a brand deserves, instead of as 14px of chrome they stop seeing
        # after a day.
        t = design.tokens()
        wordmark = QLabel(branding.wordmark_html(t))
        wordmark.setAlignment(Qt_AlignCenter)
        wordmark.setStyleSheet(branding.wordmark_qss(design.TEXT_HERO))
        outer.addWidget(wordmark)
        outer.addSpacing(design.SPACE_1)

        title = heading("Ask something about Earth", design.TEXT_LG)
        title.setAlignment(Qt_AlignCenter)
        outer.addWidget(title)

        subtitle = body_label(
            "Fetch satellite imagery, detect features, analyse terrain or run "
            "spatial queries. GoPilot works the map for you.",
            design.TEXT_BASE, secondary=True,
        )
        subtitle.setAlignment(Qt_AlignCenter)
        outer.addWidget(subtitle)

        outer.addSpacing(design.SPACE_3)
        grid = _SuggestionGrid(self._suggestion_items())
        for card in grid.cards:
            card.clicked.connect(
                lambda prompt=card.prompt: self._use_prompt(prompt))
        outer.addWidget(grid)
        self._suggestion_grid = grid

        # Centred by a stretch on EITHER side rather than by expanding the host.
        # Six cards can be taller than the viewport; an expanding host would be
        # handed the viewport height and squeeze them, while a natural-height
        # host with stretches around it centres when there is room and lets the
        # scroll area scroll when there is not.
        self._messages.insertStretch(0)
        self._messages.insertWidget(1, host)
        self._empty_state = host
        self._fetch_suggestions()
        self._fetch_upload_limits()

    # Server icon names → the plugin's registry. The API sends a lucide name
    # (the SaaS renders lucide directly); ours is a 33-icon subset drawn in
    # code, so anything outside it falls back rather than rendering blank.
    _ICON_ALIASES = {
        "map": "map-pin", "map-pin": "map-pin", "pin": "map-pin",
        "layers": "layers", "stack": "layers",
        "search": "search", "zoom-in": "search", "magnifier": "search",
        "sparkles": "sparkle", "sparkle": "sparkle", "wand": "sparkle",
        "alert-triangle": "alert", "alert": "alert", "warning": "alert",
        "pencil": "draw", "draw": "draw", "edit": "draw",
        "folder": "projects", "box": "gobox", "upload": "upload",
        "download": "download", "info": "info", "message": "message",
        "history": "history", "settings": "settings", "refresh": "refresh",
    }
    _ICON_FALLBACK = "sparkle"

    def _suggestion_items(self):
        """What the empty state's cards should show.

        The API's set if one has been drawn, the static set otherwise. Drawn
        ONCE per page and cached in ``_fetched_suggestions``: the endpoint
        picks at random on every call, so refetching would reshuffle the cards
        under the user's cursor — the same reason the SaaS caches its draw
        against a key rather than the request.
        """
        rows = self._fetched_suggestions
        if not rows:
            return SUGGESTIONS
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = (row.get("title") or "").strip()
            prompt = (row.get("prompt_text") or "").strip() or title
            if not title and not prompt:
                continue
            icon = self._ICON_ALIASES.get(
                (row.get("icon") or "").strip().lower(), self._ICON_FALLBACK)
            items.append((icon, title or prompt,
                          (row.get("description") or "").strip(), prompt))
        return items or SUGGESTIONS

    def _fetch_upload_limits(self):
        """Ask the server what may be uploaded, once per page."""
        if self.client is None or self._limits_requested:
            return
        self._limits_requested = True
        thread = FetchUploadLimitsThread(self.client)
        thread.finished.connect(self._on_upload_limits)
        self._threads.append(thread)
        thread.start()

    def _on_upload_limits(self, limits):
        if not limits:
            return      # keep the built-in filter; the server will judge
        try:
            self.composer.set_upload_limits(limits)
        except RuntimeError:
            pass        # the page went away while the request was in flight

    def _fetch_suggestions(self):
        """Ask for a draw, once, and rebuild the empty state if it lands."""
        if self.client is None or self._suggestions_requested:
            return
        self._suggestions_requested = True
        thread = FetchPromptSuggestionsThread(self.client, limit=6)
        thread.finished.connect(self._on_suggestions)
        self._threads.append(thread)
        thread.start()

    def _on_suggestions(self, rows):
        if not rows:
            return          # keep the static set; nothing to redraw
        self._fetched_suggestions = rows
        # Only rebuild if the empty state is still what the user is looking
        # at. A draw that lands after they have started typing must not wipe
        # the transcript they are now in.
        if self._empty_state is not None:
            self._remove_empty_state()
            self._show_empty_state()

    def _use_prompt(self, prompt):
        """Clicking an opener SENDS it, as it does in the SaaS.

        The card is a promise — its description says what will happen — so
        typing the text into the composer and stopping there asks the user to
        confirm a choice they already made, and the one extra click is on a
        send button they have to go find.

        ``Composer.submit`` is a no-op while a turn is in flight, so the text
        is placed first and deliberately left behind in that case: the click
        is not silently dropped, it becomes a filled composer the user can
        send when the current turn finishes.
        """
        self.composer.input.setPlainText(prompt)
        self.composer.submit()
        self.composer.focus_input()

    def _remove_empty_state(self):
        if self._empty_state is None:
            return
        # Take the leading stretch that centred it, too. Left behind, it would
        # keep pushing the first real message down the transcript.
        leading = self._messages.itemAt(0)
        if leading is not None and leading.spacerItem() is not None:
            self._messages.takeAt(0)
        self._empty_state.setParent(None)
        self._empty_state.deleteLater()
        self._empty_state = None

    # ── the in-flight reply ───────────────────────────────────────────────────
    def _show_thinking(self):
        """Open the bubble the answer will be written into.

        This used to be a placeholder bubble reading "GoPilot is thinking…",
        thrown away and replaced by a second bubble when the turn finished. It
        is now the REAL assistant bubble, created empty and filled in by
        :meth:`_on_partial` as the task reports progress — which is what makes
        the answer appear as it is written rather than in one jump at the end.
        """
        self._live_thinking = None
        self._live_tool_calls = None
        self._thinking_row = self.add_message("", is_user=False)
        self._live_bubble = self._bubble_in(self._thinking_row)
        if self._live_bubble is not None:
            self._live_bubble.set_content("_Thinking…_")
        return self._thinking_row

    def _bubble_in(self, row):
        """The :class:`MessageBubble` inside a transcript row, if any."""
        if row is None:
            return None
        layout = row.layout()
        if layout is None:
            return None
        for index in range(layout.count()):
            candidate = layout.itemAt(index).widget()
            if isinstance(candidate, MessageBubble):
                return candidate
        return None

    def _on_partial(self, message):
        """Re-render the in-flight answer from a poll's ``llm_message``."""
        bubble = self._live_bubble
        if bubble is None:
            return

        # Sticky: keep the last reasoning and tool list the turn reported.
        # Each poll re-sends the whole message, and a poll that happens to
        # omit `thinking_content` would otherwise delete a panel the user is
        # mid-sentence through reading — the reasoning visibly vanished and
        # came back. Only a NEW non-empty value replaces the old one.
        thinking = thinking_text(message.get("thinking_content"))
        if thinking:
            self._live_thinking = thinking
        calls = message.get("tool_calls_content")
        if calls:
            self._live_tool_calls = calls

        try:
            bubble.set_thinking(self._live_thinking, streaming=True)
            bubble.set_tool_calls(self._live_tool_calls, streaming=True)
            content = message.get("content") or ""
            # Keep the placeholder until there is something to replace it
            # with: a poll during a long reasoning phase carries no content
            # yet, and blanking the bubble there reads as a failed turn.
            bubble.set_content(content or "_Thinking…_")
            bubble.set_max_width(self._bubble_budget(bubble.role))
        except RuntimeError:
            # The transcript was reloaded or cleared under us mid-turn.
            self._live_bubble = None
            return
        self._scroll_to_bottom()

    def _hide_thinking(self):
        """Drop the in-flight bubble. Only for turns that produced nothing."""
        if self._thinking_row is not None:
            bubble = self._bubble_in(self._thinking_row)
            if bubble in self._bubbles:
                self._bubbles.remove(bubble)
            self._thinking_row.setParent(None)
            self._thinking_row.deleteLater()
            self._thinking_row = None
        self._live_bubble = None

    # ── sending ───────────────────────────────────────────────────────────────
    def _on_submit(self, text, files, geojson):
        if not text and not files and not geojson:
            return
        self._remove_empty_state()

        labels = []
        if geojson:
            labels.append("Area attached")
        labels.extend(os.path.basename(path) for path in files)
        self.add_message(text or "(attachment only)", is_user=True,
                         attachments=labels or None)

        self._show_thinking()
        self._set_status("Sending…", busy=True)

        input_metadata = None
        if geojson:
            input_metadata = {"type": "vector_upload", "format": "geojson"}

        thread = SendMessageThread(
            self.client, text, self.current_chat_id,
            input_metadata=input_metadata, geojson_data=geojson, files=files,
        )
        thread.finished.connect(self._on_reply)
        thread.error.connect(self._on_send_error)
        thread.partial.connect(self._on_partial)
        thread.progress.connect(lambda msg: self._set_status(msg))
        self._threads.append(thread)
        thread.start()

    def _on_reply(self, response):
        session_id = response.get("session_id")
        is_new = not self.current_chat_id and session_id
        if session_id:
            self.current_chat_id = str(session_id)

        message = response.get("message", "")
        llm_message = response.get("llm_message") or {}
        bubble = self._live_bubble

        if bubble is not None and message:
            # Finish the bubble the answer has been streaming into, rather
            # than discarding it and adding a second one — which is what
            # produced a visible flicker at the end of every turn.
            try:
                # The final payload wins where it has something to say,
                # otherwise what streamed stands — a completed task sometimes
                # reports only the finished text.
                bubble.set_thinking(
                    thinking_text(llm_message.get("thinking_content"))
                    or self._live_thinking,
                    streaming=False)
                bubble.set_tool_calls(
                    llm_message.get("tool_calls_content")
                    or self._live_tool_calls, streaming=False)
                bubble.set_content(message)
                bubble.set_max_width(self._bubble_budget(bubble.role))
                # The server only has a row for this reply now, so this is the
                # earliest its trash control can do anything.
                bubble.set_message_id(llm_message.get("id"))
            except RuntimeError:
                self._hide_thinking()
                self.add_message(message, is_user=False)
            self._thinking_row = None
            self._live_bubble = None
            self._scroll_to_bottom()
        else:
            self._hide_thinking()
            if message:
                self.add_message(message, is_user=False)
        self._set_status("", busy=False)
        if is_new:
            # The session only exists server-side once the first turn lands, so
            # this is the earliest the history list can show it.
            self.session_changed.emit(self.current_chat_id)
            self.history_changed.emit()

    def _on_delete_message(self, message_id):
        """Trash one exchange, after asking.

        The backend moves the whole TURN — a prompt and its reply travel
        together — so the confirmation says so rather than letting the user
        discover that their question went with the answer.
        """
        confirmed = QMessageBox.question(
            self, "Delete exchange",
            "Delete this question and its answer?\n\n"
            "They move to the trash and can be restored from the web app.",
            QMessageBox_Yes | QMessageBox_No, QMessageBox_No)
        if confirmed != QMessageBox_Yes:
            return

        thread = HideMessageThread(self.client, message_id)
        thread.finished.connect(self._on_message_deleted)
        thread.error.connect(
            lambda msg: self._add_system_message(
                f"**Could not delete that exchange**\n\n{msg}", tone="error"))
        self._threads.append(thread)
        thread.start()

    def _on_message_deleted(self, message_ids):
        """Remove the bubbles the server actually trashed.

        Driven by the returned ids rather than by the one that was clicked:
        the backend decides what a turn is, and taking its word keeps the
        transcript matching the server instead of guessing which sibling went
        with it.
        """
        removed = {int(i) for i in message_ids}
        for bubble in list(self._bubbles):
            try:
                if bubble.message_id is None:
                    continue
                if int(bubble.message_id) not in removed:
                    continue
            except (RuntimeError, TypeError, ValueError):
                continue
            row = bubble.parentWidget()
            self._bubbles.remove(bubble)
            if row is not None:
                row.setParent(None)
                row.deleteLater()
        self.history_changed.emit()

    def _on_send_error(self, message):
        self._hide_thinking()
        self._add_system_message(
            f"**Something went wrong**\n\n{message}", tone="error")
        self._set_status("", busy=False)

    # ── conversations ─────────────────────────────────────────────────────────
    def start_new_chat(self):
        """Clear the transcript and start an unsaved conversation."""
        self.current_chat_id = None
        self._clear_transcript()
        self._show_empty_state()
        self._set_status("", busy=False)
        self.composer.clear_attachments()
        self.composer.focus_input()
        self.session_changed.emit("")

    def open_chat(self, chat_id, title=""):
        """Load an existing conversation into the transcript."""
        self.current_chat_id = str(chat_id)
        self._clear_transcript()
        self._set_status(f"Loading {title or 'chat'}…")

        thread = FetchMessagesThread(self.client, chat_id)
        thread.finished.connect(self._on_messages_loaded)
        thread.error.connect(self._on_messages_error)
        self._threads.append(thread)
        thread.start()
        self.session_changed.emit(self.current_chat_id)

    def _on_messages_loaded(self, messages):
        self._clear_transcript()
        if not messages:
            self._show_empty_state()
        for message in messages:
            role = message.get("role", "user")
            is_user_flag = message.get("is_user", None)
            if is_user_flag is not None:
                is_user = bool(is_user_flag)
            else:
                is_user = role != "assistant"
            self.add_message(
                message.get("content", ""), is_user=is_user,
                timestamp=self._format_timestamp(
                    message.get("system_registration_date", "")),
                thinking=thinking_text(message.get("thinking_content")),
                tool_calls=message.get("tool_calls_content"),
                message_id=message.get("id"),
            )
        self._set_status("", busy=False)

    @staticmethod
    def _format_timestamp(raw):
        """"2026-06-27T21:33:58.818Z" -> "Jun 27 21:33", or None."""
        if not raw:
            return None
        try:
            parsed = QDateTime.fromString(raw[:19], "yyyy-MM-ddTHH:mm:ss")
            return parsed.toString("MMM d HH:mm") if parsed.isValid() else None
        except Exception:
            return None

    def _on_messages_error(self, message):
        self._add_system_message(
            f"**Could not load this chat**\n\n{message}", tone="error")
        self._set_status("", busy=False)

    # ── geometry input ────────────────────────────────────────────────────────
    def start_draw(self):
        """Let the user draw an area on the QGIS canvas."""
        if not self.iface:
            self._add_system_message(
                "Drawing needs the QGIS map canvas, which is not available "
                "here.")
            return
        self._set_status("Draw on the map — right-click to finish")
        # minimize_parent=False: the whole point of docking is that the canvas
        # is already visible beside the panel, so hiding the panel to reach the
        # map (what the modal dialog had to do) is now counterproductive.
        self.geo_manager.start_draw_mode(
            minimize_parent=False, show_help=True, help_context="gopilot")

    def pick_layer(self):
        """Attach a loaded QGIS vector layer, converted to GeoJSON."""
        if not self.iface:
            self._add_system_message(
                "Selecting a layer needs a QGIS project, which is not "
                "available here.")
            return
        self.geo_manager.show_layer_picker(
            title="Select a layer",
            message="Pick a vector layer to send with your message:")

    def _on_geometry_ready(self, geometry_data):
        name = geometry_data.get("name") or "Area"
        source = geometry_data.get("source")
        self.composer.set_geometry(geometry_data.get("geojson"), name)
        self._set_status("", busy=False)
        self.composer.set_placeholder(
            f"Ask about {name}…" if source == "layer"
            else "Ask about the area you drew…")
        self.composer.focus_input()

    # ── streaming hooks ───────────────────────────────────────────────────────
    # Wired and ready: the transport currently replaces the thinking bubble in
    # one step, but a chunked one only needs to call these three.
    def begin_ai_stream(self):
        self._hide_thinking()
        bubble = MessageBubble("", role="ai", client=self.client,
                               iface=self.iface)
        self._add_bubble(bubble, "left")
        self._streaming_bubble = bubble
        return bubble

    def update_ai_stream(self, full_text):
        if self._streaming_bubble is not None:
            self._streaming_bubble.set_content(full_text)
            self._scroll_to_bottom()

    def finish_ai_stream(self, full_text=None):
        if self._streaming_bubble is not None and full_text is not None:
            self._streaming_bubble.set_content(full_text)
        self._streaming_bubble = None
        self._set_status("", busy=False)
        self._scroll_to_bottom()

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    def cleanup(self):
        """Stop threads and release the map tool. Safe to call twice."""
        stop_threads(self._threads)
        if getattr(self, "geo_manager", None) is not None:
            try:
                self.geo_manager.cleanup()
            except Exception as exc:
                debug_print(f"[GoPilot] Geometry manager cleanup: {exc}")
