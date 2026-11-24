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
    Qt_TextBrowserInteraction,
)
from .composer import Composer
from .config import APP_BASE_URL
from .geometry import GeometryInputManager
from .logger import debug_print
from .message_bubble import MessageBubble, extract_file_urls
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

        # STAYING AT THE BOTTOM WHILE THE TRANSCRIPT IS STILL GROWING.
        #
        # One deferred scroll is not enough to land at the end of a loaded
        # chat. Every bubble's wrapped labels pin their own heights from
        # their resizeEvent, each scheduling another layout pass, so the
        # scroll maximum keeps rising for several turns of the event loop
        # after the widgets are inserted. A scroll issued before that
        # settles jumps to a maximum that is about to be wrong: measured on
        # a 30-turn chat, it landed 3512px down a range that finished at
        # 9542px — 37% of the way in, which is what "it opens at the start"
        # looked like.
        #
        # So instead of guessing when the layout has finished, this follows
        # the range: while `_stick_to_bottom` is set, every change to the
        # scroll range re-pins the view to the end.
        self._stick_to_bottom = False
        self._stick_timer = QTimer(self)
        self._stick_timer.setSingleShot(True)
        self._stick_timer.timeout.connect(self._release_bottom)
        bar = self._scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._on_scroll_range_changed)
        # Any deliberate scroll by the user wins immediately — the pin must
        # never fight someone reading back through the conversation.
        bar.sliderPressed.connect(self._release_bottom)
        bar.actionTriggered.connect(self._release_bottom)

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

        root.addWidget(self._build_disclaimer())

        self.start_new_chat()

    # ── chrome ────────────────────────────────────────────────────────────────
    def _build_disclaimer(self):
        """The AI notice under the composer, as the SaaS carries it.

        Permanent rather than dismissible, and not part of the empty state:
        it is a standing statement about the product, so it has to be there
        on the hundredth message as well as the first. The SaaS puts it in
        exactly this spot — a hairline-topped strip below the composer.

        The wording is the SaaS's, verbatim. "Terms of Service" and "Privacy
        Policy" are links here though they are plain text on the web: in a
        QGIS panel there is nowhere to navigate to, so naming documents the
        reader cannot open would be a dead end.
        """
        t = design.tokens()
        host = QFrame()
        host.setObjectName("aiDisclaimer")
        host.setStyleSheet(
            f"QFrame#aiDisclaimer {{ background:{t.surface}; border:none; "
            f"border-top:{design.BORDER}px solid {t.divider}; }}"
        )
        lay = QVBoxLayout(host)
        lay.setContentsMargins(design.SPACE_3, design.SPACE_1,
                               design.SPACE_3, design.SPACE_1)
        lay.setSpacing(0)

        link = (f"color:{t.text_secondary}; text-decoration:underline;")
        label = QLabel(
            f"GoPilot is powered by AI. By using it, you agree to our "
            f'<a href="{APP_BASE_URL}/terms" style="{link}">Terms of '
            f'Service</a> and <a href="{APP_BASE_URL}/privacy-policy" '
            f'style="{link}">Privacy Policy</a>. Conversations may be '
            f"reviewed for quality and safety, but are never used to train "
            f"our AI models."
        )
        label.setWordWrap(True)
        label.setAlignment(Qt_AlignCenter)
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(Qt_TextBrowserInteraction)
        label.setStyleSheet(
            f"background:transparent; border:none; color:{t.text_muted}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_XS}px;"
        )
        lay.addWidget(label)
        self._disclaimer_label = label
        return host

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
        # Stop each bubble's pending work BEFORE dropping it. A bubble can
        # have an image preview downloading or a layer download running, and
        # both call back into its widgets — so deleting the bubble without
        # stopping them left a reply to complete into freed memory. That is
        # the access violation that took QGIS down when a chat containing an
        # image result was opened from History, since opening one clears the
        # transcript (twice: here and again when the messages land).
        for bubble in self._bubbles:
            try:
                bubble.cleanup()
            except RuntimeError:
                pass            # already gone; nothing left to stop
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
                    message_id=None, files=None):
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
            message_id=message_id, files=files,
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

    def jump_to_end(self):
        """Go to the end of the transcript, and STAY there while it settles.

        Used when a whole conversation appears at once — opening one from
        History, or a reload after a delete. A single scroll cannot do this
        reliably; see the note where `_stick_to_bottom` is declared.

        The pin releases itself: on a timer, so it can never be permanent,
        and on the first scroll the user makes.
        """
        self._stick_to_bottom = True
        # Long enough for the layout passes a long chat needs, short enough
        # that it cannot interfere with anything the user does next.
        self._stick_timer.start(1500)
        self._scroll_to_bottom()

    def _release_bottom(self, *_args):
        self._stick_to_bottom = False
        try:
            self._stick_timer.stop()
        except RuntimeError:
            pass

    def _on_scroll_range_changed(self, _minimum, maximum):
        if not self._stick_to_bottom:
            return
        try:
            self._scroll.verticalScrollBar().setValue(maximum)
        except RuntimeError:
            self._stick_to_bottom = False

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

    def _rendered_message_ids(self):
        """Ids of every message already on screen.

        Read off the bubbles rather than kept as a separate set, so it cannot
        drift from what the user is actually looking at.
        """
        ids = set()
        for bubble in self._bubbles:
            try:
                identifier = bubble.message_id
            except RuntimeError:
                continue
            if identifier is not None:
                ids.add(str(identifier))
        return ids

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
            # Files arrive mid-turn, and "Load results automatically" clicks
            # the result buttons as soon as they appear — so the style has to
            # be in place by then, not only at the end of the turn.
            bubble.set_files(message.get("files"))
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
            # Everything the transcript already shows. The task row can keep
            # reporting the PREVIOUS turn's `llm_message` until the worker
            # writes this one, and rendering that is how a new prompt came
            # back answered with the last reply.
            known_message_ids=self._rendered_message_ids(),
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
                # BEFORE set_content, which builds the result buttons and,
                # with "load results automatically" on, clicks them. A click
                # looks the style up from this list, so setting it afterwards
                # meant auto-loaded layers were never styled.
                bubble.set_files(llm_message.get("files"))
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
        self._reconcile_message_ids()

    def _reconcile_message_ids(self):
        """Fill in the server ids for bubbles added locally this session.

        A message the user has just sent is drawn immediately — the panel
        does not wait for a round trip to echo it — so its bubble has no
        server row behind it. Two things need one:

        * the trash control, which stays hidden until there is something to
          ask the server to delete;
        * :meth:`_on_message_deleted`, which removes bubbles BY id, so an
          id-less prompt survived its own reply being trashed.

        Both of those looked like "you have to leave the page and come back",
        and that is exactly what was happening: reopening the chat refetched
        the transcript and every bubble got its id. This does the refetch
        without rebuilding anything, so nothing flickers and the scroll
        position and image previews are left alone.

        The reply's own id arrives with the finished task, so this is only
        ever filling in the prompt — but it is written generally, because the
        reply's id is missing too on the timeout-recovery path.
        """
        if self.client is None or not self.current_chat_id:
            return
        # An id OR a file list is reason enough to ask. A reply bubble gets
        # its id when the turn finishes but never its files, so testing only
        # for a missing id skipped the fetch on exactly the turn whose
        # results needed styling.
        if not any(self._needs_an_id(b) or self._needs_files(b)
                   for b in self._bubbles):
            return
        thread = FetchMessagesThread(self.client, self.current_chat_id)
        thread.finished.connect(self._on_ids_reconciled)
        # Deliberately silent on failure: the ids are a nicety, and a failed
        # refetch must not put an error card in a conversation that worked.
        thread.error.connect(
            lambda msg: debug_print(f"[GoPilot] Id reconcile failed: {msg}"))
        self._threads.append(thread)
        thread.start()

    @staticmethod
    def _needs_an_id(bubble):
        try:
            return bubble.role != "system" and bubble.message_id is None
        except RuntimeError:
            return False

    @staticmethod
    def _needs_files(bubble):
        """An assistant bubble that offers results but knows nothing of them.

        Results are found by reading the message TEXT for links, so a bubble
        can have download buttons and still have no file list — and the file
        list is where each result's style lives.
        """
        try:
            if bubble.role != "ai" or bubble._files:
                return False
            return bool(extract_file_urls(bubble.raw_text()))
        except (RuntimeError, AttributeError):
            return False

    def _on_ids_reconciled(self, messages):
        """Match id-less bubbles to server rows by role and text.

        By CONTENT rather than by position: the two lists usually line up,
        but a turn that failed and left a system notice, or a message trashed
        from the web app in another tab, is enough to shift them — and
        assigning the wrong id to a bubble means its trash button deletes
        somebody else's message.

        This also fills in each message's FILE LIST, which is why it matters
        for more than the trash control. The task's own `llm_message` does
        not carry `files`, so a bubble built from a live turn has none — and
        the file list is where each result's SLD lives. The style was
        therefore missing on every freshly-answered turn and only appeared
        once the chat was reopened, which is the endpoint this already
        reads. Measured on a raster result: "No SLD was sent for
        'ndvi_20261120'", while the same file is styled on the website.
        """
        claimed = set()
        for bubble in self._bubbles:
            try:
                if bubble.message_id is not None:
                    claimed.add(str(bubble.message_id))
            except RuntimeError:
                continue

        # Files first, keyed on the ids we already hold. A bubble that was
        # given its id when the turn finished still has no files.
        by_id = {}
        for message in messages or ():
            identifier = message.get("id")
            if identifier is not None:
                by_id[str(identifier)] = message
        for bubble in self._bubbles:
            try:
                if bubble.message_id is None:
                    continue
                message = by_id.get(str(bubble.message_id))
                if message is not None:
                    bubble.set_files(message.get("files"))
            except RuntimeError:
                continue

        for bubble in self._bubbles:
            if not self._needs_an_id(bubble):
                continue
            try:
                wanted = bubble.raw_text().strip()
                is_user_bubble = bubble.role == "user"
            except RuntimeError:
                continue
            for message in messages or ():
                identifier = message.get("id")
                if identifier is None or str(identifier) in claimed:
                    continue
                is_user = message.get("is_user")
                if is_user is None:
                    is_user = message.get("role") == "user"
                if bool(is_user) != is_user_bubble:
                    continue
                if (message.get("content") or "").strip() != wanted:
                    continue
                try:
                    bubble.set_message_id(identifier)
                    # And its files, for the same reason as above.
                    bubble.set_files(message.get("files"))
                except RuntimeError:
                    break
                claimed.add(str(identifier))
                break

        # Anything still without an id could not be matched, and a bubble
        # with no id is the bug being fixed: no trash control, and invisible
        # to a delete. So fall back to the thing that always works — rebuild
        # the transcript from the server, which is precisely what reopening
        # the chat did by hand.
        #
        # Deliberately a fallback rather than the first move: a rebuild costs
        # a flicker, the scroll position and every image preview, and the
        # match above normally succeeds. But a heuristic on message text
        # cannot be trusted against a server that trims, normalises or
        # annotates what it stores, and being right matters more here than
        # being smooth.
        stranded = [b for b in self._bubbles if self._needs_an_id(b)]
        if stranded:
            debug_print(
                f"[GoPilot] {len(stranded)} bubble(s) could not be matched "
                f"to a server row; rebuilding the transcript")
            self._on_messages_loaded(messages)

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
            # Stop the bubble's own pending work before dropping its row, for
            # the same reason _clear_transcript does.
            try:
                bubble.cleanup()
            except RuntimeError:
                pass
            if row is not None:
                row.setParent(None)
                row.deleteLater()
        self.history_changed.emit()

        # Deleting the LAST exchange empties the session, and an empty
        # session is the same thing as a deleted one — so go to a new chat
        # rather than leaving the user looking at a blank transcript that
        # still points at a session with nothing in it. Deleting one of
        # several leaves the rest to read, so nothing moves.
        if self._conversation_bubbles() == 0:
            self.start_new_chat()
            return

        # Then ask the server what the transcript actually is now.
        #
        # Removing bubbles by id above is the OPTIMISTIC half: it is instant,
        # and it is right whenever we hold an id for everything the server
        # trashed. It is not always right. The backend takes a whole turn and
        # reports the rows it moved in `trashed_ids`, but a build that does
        # not send that list falls back to the single id we asked about — and
        # a prompt drawn locally this session may have had no id to match in
        # the first place. Either way the pair's other half stayed on screen
        # as an orphan until the chat was reopened, which is exactly the
        # "leave the page and come back" that was reported.
        #
        # The SaaS has never had this bug because it does not do arithmetic
        # on ids at all: its delete invalidates the messages query and the
        # list is rebuilt from the server. This is that, minus the rebuild in
        # the case where the optimistic removal already agreed with it.
        self._resync_transcript()

    def _resync_transcript(self):
        """Refetch the session and rebuild only if it disagrees with us.

        Checking before rebuilding is what keeps this invisible: in the
        common case the optimistic update was already correct, and a rebuild
        would cost a flicker, the scroll position and every image preview for
        no change at all.
        """
        if self.client is None or not self.current_chat_id:
            return
        thread = FetchMessagesThread(self.client, self.current_chat_id)
        thread.finished.connect(self._on_resync)
        # Silent: the optimistic update is already on screen, and an error
        # card about a bookkeeping refetch would be noise about a delete that
        # worked.
        thread.error.connect(
            lambda msg: debug_print(f"[GoPilot] Resync failed: {msg}"))
        self._threads.append(thread)
        thread.start()

    def _on_resync(self, messages):
        if not messages:
            # The server has nothing left in this session.
            self.start_new_chat()
            return

        server_ids = {str(m.get("id")) for m in messages
                      if m.get("id") is not None}
        shown_ids = set()
        for bubble in self._bubbles:
            try:
                if bubble.role != "system" and bubble.message_id is not None:
                    shown_ids.add(str(bubble.message_id))
            except RuntimeError:
                continue

        # An id-less bubble is not a disagreement on its own — a message sent
        # moments ago has no id yet, and `_reconcile_message_ids` is what
        # gives it one. A MISMATCH of real ids is a disagreement.
        if shown_ids == server_ids and not any(
                self._needs_an_id(b) for b in self._bubbles):
            return

        debug_print(f"[GoPilot] Transcript out of step with the server "
                    f"(showing {sorted(shown_ids)}, server has "
                    f"{sorted(server_ids)}); rebuilding")
        self._on_messages_loaded(messages)

    def _conversation_bubbles(self):
        """How many bubbles the user would call part of the conversation.

        System notices do not count — an error card is not a chat, and one
        left over from a failed turn would otherwise make an emptied session
        look like it still had something in it. Counted by ROLE rather than
        by having a server id, because a message that has only just been sent
        does not have an id yet and is still very much part of the chat.
        """
        total = 0
        for bubble in self._bubbles:
            try:
                if bubble.role != "system":
                    total += 1
            except RuntimeError:
                continue        # C++ side already gone; it is not on screen
        return total

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
                # The API's file list for this message, which carries each
                # file's SLD. Read from the server on every reload, so a
                # reopened chat styles its layers exactly like a live one.
                files=message.get("files"),
            )
        self._set_status("", busy=False)
        # A conversation opens at its END. The newest exchange is the one the
        # user came back for; starting at the top of a long chat meant
        # scrolling all the way down every time.
        if messages:
            self.jump_to_end()

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
        # The bubbles own pending work of their own (previews, downloads) that
        # `stop_threads` above knows nothing about. On unload the whole panel
        # is destroyed, so anything still running would complete into freed
        # widgets.
        for bubble in self._bubbles:
            try:
                bubble.cleanup()
            except RuntimeError:
                pass
        if getattr(self, "geo_manager", None) is not None:
            try:
                self.geo_manager.cleanup()
            except Exception as exc:
                debug_print(f"[GoPilot] Geometry manager cleanup: {exc}")
