"""Shared UI primitives built on the :mod:`design` tokens.

These are the Qt counterparts of the small components the SaaS composes its
screens from, so a page here is assembled the same way a page is there:
:class:`IconButton` for a bare icon action, :class:`ToggleSwitch` for a boolean
setting, :class:`SectionCard` for a titled block, :class:`Chip` for an
attachment pill, :class:`Divider` for a rule.

Two of them exist because Qt has no equivalent at all:

* :class:`ToggleSwitch` — Qt only offers a checkbox. A tick box reads as "tick
  this to agree", while the layer controls in Settings are live on/off
  switches, which is a different promise to the user. It is also impossible to
  put a check GLYPH inside a stylesheet-only checkbox without shipping an image
  file, so a styled ``QCheckBox`` ends up as an ambiguous filled square.
* :class:`IconButton` — a ``QPushButton`` cannot re-tint its own icon, so the
  active/inactive flip in the nav rail (grey icon on nothing, white icon on a
  brand pill) has to re-render the pixmap. That lives here rather than being
  repeated at each call site.

Every one of them reads its colours through ``design.tokens()`` at construction
and again in :meth:`refresh_theme`, so a widget rebuilt after the user switches
QGIS themes picks up the right palette.
"""

from qgis.PyQt.QtCore import QPropertyAnimation, QRectF, QSize, pyqtProperty, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPen
from qgis.PyQt.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from . import design, icons
from .compat import (
    QEasingCurve_OutCubic, QFrame_HLine, QFrame_NoFrame, QPainter_Antialiasing,
    QSizePolicy_Expanding, QSizePolicy_Fixed, QSizePolicy_Ignored,
    QSizePolicy_Maximum, QSizePolicy_Preferred, Qt_AlignVCenter, Qt_ElideRight,
    Qt_LeftButton, Qt_PointingHandCursor, Qt_ScrollBarAlwaysOff,
    Qt_WA_StyledBackground, Qt_transparent,
)


class IconButton(QPushButton):
    """A square, flat, icon-only button.

    ``tone`` picks the resting icon colour:
    ``"secondary"`` (default), ``"primary"``, ``"muted"``, ``"danger"``,
    ``"accent"``, or ``"on-accent"``.

    ``hover_tone`` re-colours the glyph AND its wash while the pointer is on
    it — ``hover_tone="danger"`` is how a delete control announces itself only
    at the moment it matters, instead of sitting there red all the time.

    Set ``active=True`` (or use it as the rail's current tab) to get the filled
    brand pill with a white glyph. ``filled=True`` makes it a permanent brand
    action button — that is the composer's Send.

    ``overlay=True`` is for a control sitting ON an image: a translucent dark
    disc with a white glyph, reddening on hover. A card thumbnail can be any
    photograph, so neither a light nor a dark glyph is legible on its own —
    the disc supplies the contrast the image cannot be relied on for.
    """

    def __init__(self, name, size=design.RAIL_BUTTON, icon_size=design.ICON,
                 tooltip="", tone="secondary", hover_tone=None, filled=False,
                 overlay=False, parent=None):
        super().__init__(parent)
        self._icon_name = name
        self._icon_px = icon_size
        self._tone = tone
        self._hover_tone = hover_tone
        self._hovered = False
        self._filled = filled
        self._overlay = overlay
        self._active = False
        self._size = size

        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCursor(Qt_PointingHandCursor)
        # Without this, Enter anywhere in the panel fires whichever icon button
        # Qt last decided was the default — in the composer that meant a stray
        # Return deleted a chat or opened the file picker.
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFlat(True)
        if tooltip:
            self.setToolTip(tooltip)
        self.refresh_theme()

    # ── appearance ────────────────────────────────────────────────────────────
    _TONES = ("primary", "secondary", "muted", "danger", "accent", "on-accent")

    def _tone_color(self, tone):
        t = design.tokens()
        return {
            "primary":   t.text,
            "secondary": t.text_secondary,
            "muted":     t.text_muted,
            "danger":    t.error,
            "accent":    t.accent,
            "on-accent": t.text_on_accent,
        }.get(tone, t.text_secondary)

    def _resting_colors(self):
        """(icon colour, background, hover background) for the current state."""
        t = design.tokens()
        if self._overlay:
            return ("#FFFFFF", design.alpha("#000000", 0.45),
                    design.alpha(t.error, 0.92))
        if self._filled or self._active:
            return t.text_on_accent, t.accent, t.accent_hover
        hover_bg = t.hover
        if self._hover_tone == "danger":
            hover_bg = t.error_soft
        elif self._hover_tone == "accent":
            hover_bg = t.accent_soft
        return self._tone_color(self._tone), "transparent", hover_bg

    def _current_icon_color(self):
        """Glyph colour right now, hover included."""
        t = design.tokens()
        if self._overlay:
            return "#FFFFFF"
        if self._filled or self._active:
            return t.text_on_accent
        if self._hovered and self._hover_tone:
            return self._tone_color(self._hover_tone)
        return self._tone_color(self._tone)

    def _sync_icon(self):
        self.setIcon(icons.icon(
            self._icon_name, self._icon_px, self._current_icon_color(),
            disabled_color=design.tokens().text_disabled,
        ))

    def refresh_theme(self):
        """Re-render the glyph and stylesheet against the live theme."""
        t = design.tokens()
        _fg, bg, hover = self._resting_colors()
        # A filled button is a CIRCLE, and the radius has to come from its own
        # size. The previous constant (999, the CSS `rounded-full` idiom) does
        # not work here: Qt never clamps border-radius, so once it exceeds the
        # box the corners come back — at 999px on a 30px button the Send
        # control rendered as a green square. See design.pill_radius.
        radius = (design.pill_radius(self._size)
                  if (self._filled or self._overlay) else design.RADIUS)
        self._sync_icon()
        if self._overlay:
            pressed = design.alpha(t.error_hover, 0.95)
        elif self._filled or self._active:
            pressed = t.accent_pressed
        else:
            pressed = t.pressed
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; border: none; border-radius: {radius}px;
                padding: 0;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {pressed}; }}
            QPushButton:disabled {{ background: {bg}; }}
        """)

    # The WASH is handled by the `:hover` rule above — Qt does that itself. The
    # GLYPH is not: an icon is a pixmap, and a stylesheet cannot recolour one.
    # QIcon's Active mode is meant to cover this, but whether a style uses it
    # on hover is up to the style, so the colour is swapped explicitly here
    # instead. Only the icon is touched, not the stylesheet, so there is no
    # re-parse or flicker on every pointer entry.
    def enterEvent(self, event):
        super().enterEvent(event)
        if self._hover_tone:
            self._hovered = True
            self._sync_icon()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._hover_tone:
            self._hovered = False
            self._sync_icon()

    def is_hovered(self):
        return self._hovered

    def set_active(self, active):
        """Toggle the filled brand pill (the nav rail's current tab)."""
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        self.refresh_theme()

    def is_active(self):
        return self._active

    def set_icon_name(self, name):
        """Swap the glyph — used by the dock/undock toggle."""
        if name == self._icon_name:
            return
        self._icon_name = name
        self.refresh_theme()

    def icon_name(self):
        return self._icon_name


class ToggleSwitch(QAbstractButton):
    """A painted on/off switch. Checkable, animated, keyboard-focusable.

    Sized to match a line of 13px body text so it sits on the baseline of the
    label beside it in a settings row.
    """

    TRACK_W = 36
    TRACK_H = 20
    KNOB_INSET = 2

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt_PointingHandCursor)
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self.setSizePolicy(QSizePolicy_Fixed, QSizePolicy_Fixed)

        # Knob position as a 0..1 fraction, animated so the state change is
        # legible as a transition rather than a jump.
        self._fraction = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"fraction", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve_OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._fraction)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_fraction(self):
        return self._fraction

    def set_fraction(self, value):
        self._fraction = float(value)
        self.update()

    # Declared as a Qt property so QPropertyAnimation can drive it.
    fraction = pyqtProperty(float, fget=get_fraction, fset=set_fraction)

    def sizeHint(self):
        return QSize(self.TRACK_W, self.TRACK_H)

    def paintEvent(self, _event):
        t = design.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter_Antialiasing, True)
        p.setPen(QPen(Qt_transparent))

        # Track: brand when on, neutral when off, blended while animating so
        # the colour travels with the knob instead of snapping at the end.
        off_track = QColor(t.border_strong)
        on_track = QColor(t.accent)
        track = QColor(
            round(off_track.red() + (on_track.red() - off_track.red()) * self._fraction),
            round(off_track.green() + (on_track.green() - off_track.green()) * self._fraction),
            round(off_track.blue() + (on_track.blue() - off_track.blue()) * self._fraction),
        )
        if not self.isEnabled():
            track = QColor(t.border)

        p.setBrush(track)
        p.setPen(QPen(Qt_transparent))
        radius = self.TRACK_H / 2.0
        p.drawRoundedRect(QRectF(0, 0, self.TRACK_W, self.TRACK_H),
                          radius, radius)

        knob_d = self.TRACK_H - self.KNOB_INSET * 2
        travel = self.TRACK_W - knob_d - self.KNOB_INSET * 2
        x = self.KNOB_INSET + travel * self._fraction
        p.setBrush(QColor("#FFFFFF" if not t.dark or self.isChecked()
                          else t.text_secondary))
        p.drawEllipse(QRectF(x, self.KNOB_INSET, knob_d, knob_d))

        if self.hasFocus():
            p.setBrush(QColor(Qt_transparent))
            pen = QPen(QColor(t.focus_ring))
            pen.setWidthF(1.6)
            p.setPen(pen)
            p.drawRoundedRect(QRectF(0.8, 0.8, self.TRACK_W - 1.6,
                                     self.TRACK_H - 1.6), radius, radius)
        p.end()


class SettingRow(QWidget):
    """One settings line: title, control, and an optional description under it.

        ┌────────────────────────────────────────┬────────┐
        │ Group results in one layer group       │  (on)  │
        ├────────────────────────────────────────┴────────┤
        │ Keeps everything GoPilot adds together instead  │
        │ of scattering it through your layer panel.      │
        └─────────────────────────────────────────────────┘

    The description sits on its OWN full-width row rather than in a column
    beside the control. That is not only better typography in a ~420px dock —
    it is the only arrangement that wraps reliably. ``heightForWidth`` resolved
    through a horizontal layout uses the row's full width rather than the
    width the label actually receives after the control takes its share, so a
    two-line description was measured as fitting on one line and clipped. A
    wrapped label that is a direct child of a vertical layout has no such
    ambiguity.

    Emits :attr:`toggled` when built with a switch, so a caller can wire
    persistence without reaching into the child widget.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, title, description="", control=None, checked=None,
                 parent=None):
        super().__init__(parent)
        enable_height_for_width(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 7, 0, 7)
        outer.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(design.SPACE_3)
        # The title stays single-line, so this horizontal row never needs a
        # height-for-width answer from anything.
        label = QLabel(title)
        label.setWordWrap(False)
        label.setStyleSheet(
            design.body_qss(design.TEXT_MD)
            + f" font-weight:{design.WEIGHT_MEDIUM};"
        )
        top.addWidget(label, stretch=1)

        if control is None and checked is not None:
            control = ToggleSwitch(checked)
            control.toggled.connect(self.toggled.emit)
        self.control = control
        if control is not None:
            top.addWidget(control, alignment=Qt_AlignVCenter)
        outer.addLayout(top)

        if description:
            outer.addWidget(wrapped_label(
                description, design.TEXT_SM, secondary=True))

    def is_checked(self):
        return bool(self.control and self.control.isChecked())


class SectionCard(QFrame):
    """A titled card: `.card` from the SaaS, with an optional icon and action.

    Content goes in via :meth:`add` / :meth:`add_layout`; the card owns its own
    padding so pages never set per-child margins.
    """

    _SEQ = 0

    def __init__(self, title="", icon_name=None, parent=None):
        super().__init__(parent)
        SectionCard._SEQ += 1
        self._name = f"sectionCard{SectionCard._SEQ}"
        self.setObjectName(self._name)
        t = design.tokens()
        self.setStyleSheet(design.card_qss(self._name))
        # Forward the height-for-width question up to whatever lays the card
        # out. Every link in the chain from the scroll area's content widget
        # down to the wrapped label has to advertise it, or the first one that
        # does not falls back to a single-line sizeHint and clips the rest.
        enable_height_for_width(self)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(design.SPACE_4, design.SPACE_3,
                                     design.SPACE_4, design.SPACE_4)
        self._lay.setSpacing(design.SPACE_2)

        self.header = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(design.SPACE_2)
            if icon_name:
                badge = QLabel()
                badge.setPixmap(icons.pixmap(icon_name, design.ICON_SM,
                                             t.accent, dpr=2.0))
                paint_transparent(badge, border=True)
                self.header.addWidget(badge)
            heading = QLabel(title)
            heading.setStyleSheet(design.heading_qss(design.TEXT_MD))
            self.header.addWidget(heading)
            self.header.addStretch()
            self._lay.addLayout(self.header)

    def add_header_widget(self, widget):
        """Put a control (e.g. Refresh) at the right end of the card title."""
        if self.header is None:
            self._lay.addWidget(widget)
        else:
            self.header.addWidget(widget)

    def add(self, widget):
        self._lay.addWidget(widget)
        return widget

    def add_layout(self, layout):
        self._lay.addLayout(layout)
        return layout

    def add_divider(self):
        div = Divider()
        self._lay.addWidget(div)
        return div

    def add_spacing(self, px):
        self._lay.addSpacing(px)

    def body(self):
        """The card's own layout, for callers that need insertWidget etc."""
        return self._lay


class Divider(QFrame):
    """A 1px hairline rule in the divider token colour."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame_HLine)
        self.setFixedHeight(1)
        self.setStyleSheet(
            f"background:{design.tokens().divider}; border:none;"
        )


class ClickableCard(QFrame):
    """A card that behaves like a button.

    The SaaS wraps its project card in a ``<Link>`` — the whole card is the
    affordance and there is no "Open" button inside it. Qt has no equivalent,
    so the card supplies the behaviour: pointer cursor, a hover state, and a
    :attr:`clicked` signal.

    Subclasses override :meth:`on_hover_changed` to restyle their own contents
    (the SaaS turns the project title brand-green on hover).

    Child buttons still work normally: a QPushButton accepts its own click, so
    it never reaches this widget — which is what keeps the delete control from
    also opening the project.
    """

    clicked = pyqtSignal()

    def __init__(self, object_name="clickableCard", parent=None):
        super().__init__(parent)
        self._card_name = object_name
        self._hovered = False
        self.setObjectName(object_name)
        self.setCursor(Qt_PointingHandCursor)
        self.apply_card_style()

    def apply_card_style(self):
        t = design.tokens()
        # Border-only hover, matching the web card, which keeps its white
        # surface and lifts on a shadow. A full mint wash across a card this
        # large reads as a selection rather than a hover.
        border = t.accent if self._hovered else t.border
        self.setStyleSheet(
            f"QFrame#{self._card_name} {{ background:{t.surface}; "
            f"border:{design.BORDER}px solid {border}; "
            f"border-radius:{design.RADIUS_LG}px; }}"
        )
        self.on_hover_changed(self._hovered)

    def on_hover_changed(self, hovered):
        """Hook for subclasses. Default does nothing."""

    def is_hovered(self):
        return self._hovered

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        self.apply_card_style()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hovered = False
        self.apply_card_style()

    def mouseReleaseEvent(self, event):
        # Release, not press: a click is only a click if the pointer is still
        # on the card, so dragging off it cancels, as a button would.
        if (event.button() == Qt_LeftButton
                and self.rect().contains(event.pos())):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Chip(QFrame):
    """A removable pill — an attached layer, drawn AOI or queued upload.

    Mirrors the SaaS ``FileChips``: icon, truncated label, and an X that emits
    :attr:`removed`.
    """

    removed = pyqtSignal()

    def __init__(self, text, icon_name=None, tone="accent", removable=True,
                 max_chars=26, radius=None, parent=None):
        super().__init__(parent)
        t = design.tokens()
        # Pinned height, because the pill radius is derived from it and Qt will
        # not clamp an oversized one (see design.pill_radius). Letting the
        # content decide the height would leave the corners slightly wrong
        # whenever the font metrics shifted.
        self.setFixedHeight(design.CHIP_HEIGHT)
        self.setStyleSheet(
            design.chip_qss(tone, design.CHIP_HEIGHT, radius=radius))
        self.setSizePolicy(QSizePolicy_Maximum, QSizePolicy_Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 5 if removable else 9, 0)
        lay.setSpacing(5)

        tint = {"accent": t.accent_text, "neutral": t.text_secondary,
                "success": t.success, "warning": t.warning,
                "error": t.error}.get(tone, t.accent_text)

        if icon_name:
            glyph = QLabel()
            glyph.setPixmap(icons.pixmap(icon_name, 12, tint, dpr=2.0))
            paint_transparent(glyph, border=True)
            lay.addWidget(glyph)

        shown = text if len(text) <= max_chars else text[:max_chars - 1] + "…"
        self._label = QLabel(shown)
        if shown != text:
            self._label.setToolTip(text)
        lay.addWidget(self._label)

        if removable:
            close = QPushButton()
            close.setFixedSize(16, 16)
            close.setCursor(Qt_PointingHandCursor)
            close.setAutoDefault(False)
            close.setIcon(icons.icon("close", 9, tint))
            close.setIconSize(QSize(9, 9))
            close.setToolTip("Remove")
            close.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none;
                    border-radius: 8px;
                }}
                QPushButton:hover {{ background: {design.alpha(t.error, 0.18)}; }}
            """)
            close.clicked.connect(self.removed.emit)
            lay.addWidget(close)


class ElidedLabel(QLabel):
    """A single-line label that truncates with "…" instead of being clipped.

    ``QLabel`` has no elide mode, and this matters more than it sounds in a
    420px dock. Two failure modes it fixes:

    * A long chat title either wraps to three lines or is cut mid-glyph.
    * **An unbreakable string pins the whole panel open.** Word wrap can only
      break at spaces, so a filesystem path like
      ``C:\\Users\\…\\qgis_gopilot_venv\\venv_py3.12`` has a minimum width of
      the entire path. Inside a ``QScrollArea`` with ``widgetResizable``, that
      minimum wins over the viewport width — so every sibling card was laid
      out ~700px wide and clipped at the dock edge, taking the settings
      toggles off-screen with it.

    Pass ``elide=Qt_ElideMiddle`` for paths, where both ends carry the
    information. The full string is always available as the tooltip.
    """

    def __init__(self, text="", elide=Qt_ElideRight, parent=None):
        super().__init__(parent)
        self._full_text = text
        self._elide = elide
        self.setWordWrap(False)
        # Ignored, so the label can shrink below its own text width. Without
        # it the parent layout honours the full sizeHint and nothing narrows.
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy_Ignored, QSizePolicy_Preferred)
        self._apply()

    def setText(self, text):
        self._full_text = text
        self._apply()

    def full_text(self):
        return self._full_text

    def _apply(self):
        metrics = self.fontMetrics()
        available = max(0, self.width())
        if available <= 0:
            # Not laid out yet. Showing the full string here would be measured
            # as the sizeHint, which is the very thing being avoided, so show
            # nothing until there is a real width to elide against.
            super().setText("")
            self.setToolTip(self._full_text)
            return
        super().setText(
            metrics.elidedText(self._full_text, self._elide, available))
        self.setToolTip(
            self._full_text
            if metrics.horizontalAdvance(self._full_text) > available else ""
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()


def equalise_heights(widgets):
    """Give every widget in ``widgets`` the height of the tallest.

    A ``QGridLayout`` equalises each ROW, not the whole grid, so a card whose
    title wraps to two lines makes its row taller and the grid steps against
    itself row by row. Levelling them makes the set read as a grid rather than
    a ragged list.

    MEASURED FROM ``sizeHint()``, NOT ``height()``
    ==============================================
    ``height()`` is only meaningful after a layout pass, and whether one has
    happened depends on when the caller's deferred timer fires. An earlier
    version read it and broke in two different directions:

    * Fresh cards, never laid out, report Qt's DEFAULT widget size — 480px.
      Every card was then pinned to 480.
    * Cards left over from a previous pass report a stale small height, and
      every card got pinned to that. The content then overflowed its card and
      was drawn on top of the thumbnail.

    ``sizeHint()`` has neither problem: measured on the same cards it returned
    181 and 211 both before and after layout, matching their laid-out heights
    exactly. These cards have a FIXED width, so the layout resolves
    height-for-width against a known number rather than guessing one.

    SETS A MINIMUM, NOT A FIXED HEIGHT
    ==================================
    So that a measurement which comes out too small can never crush content
    again. If the estimate is low the card simply keeps its natural height and
    the grid stays slightly ragged — a cosmetic miss instead of a broken page.
    """
    live = [w for w in widgets if w is not None]
    if not live:
        return 0
    tallest = max(w.sizeHint().height() for w in live)
    if tallest <= 0:
        return 0
    for widget in live:
        if widget.minimumHeight() != tallest:
            widget.setMinimumHeight(tallest)
    return tallest


def card_grid_metrics(scroll_area, grid, min_card_width,
                      max_columns=design.CARD_MAX_COLS):
    """How many card columns fit, and how wide each card should be.

    Returns ``(columns, card_width)``. Shared by the Projects and GoBox grids,
    which had a copy of this sum each and had already drifted apart.

    Two things it gets right that the per-page versions did not:

    * **It reserves the vertical scrollbar.** The viewport only shrinks once
      the bar appears, and that does not fire a resize on the page — so a
      width measured before it appeared overshoots by the bar's width and the
      last card's border is clipped. Erring narrow is invisible.
    * **Cards GROW, columns are capped.** Dividing the width by a fixed card
      size means a wide floating panel gets five cramped columns. Capping the
      count and handing the remainder back to the cards keeps them readable at
      any width.
    """
    bar = scroll_area.verticalScrollBar()
    reserve = 0 if bar.isVisible() else bar.sizeHint().width()
    margins = grid.contentsMargins()
    spacing = grid.spacing()
    available = (scroll_area.viewport().width() - reserve
                 - margins.left() - margins.right())
    if available <= 0:
        return 1, min_card_width

    fits = (available + spacing) // (min_card_width + spacing)
    columns = max(1, min(max_columns, int(fits)))
    card_width = (available - spacing * (columns - 1)) // columns
    return columns, max(1, int(card_width))


def paint_background(widget, color, name=None):
    """Make ``widget`` actually paint ``color`` as its background.

    TWO THINGS GO WRONG WITHOUT THIS, AND BOTH ARE SILENT
    =====================================================
    1. **A plain ``QWidget`` subclass ignores a stylesheet background.** Qt
       only honours it for QFrame-derived widgets unless
       ``WA_StyledBackground`` is set. Every page here called
       ``setStyleSheet("background: <canvas>")`` and none of them painted it —
       the panel had been showing whatever QGIS's palette uses for ``Window``
       all along. It went unnoticed because the test palettes were close
       enough to the token to look right; against a deliberately off-brand
       palette (``#123456``) all five views leaked it immediately.

    2. **An unscoped ``background:`` leaks onto children.** A bare property in
       a stylesheet set on a parent also matches its descendants, which would
       flood the cards, bubbles and composer with the page colour. So the rule
       is scoped to this widget's object name.

    Callers that need a transparent child can leave it alone: a widget that
    paints nothing shows its nearest ancestor that does.
    """
    object_name = name or widget.objectName() or f"bg{id(widget):x}"
    widget.setObjectName(object_name)
    widget.setAttribute(Qt_WA_StyledBackground, True)
    widget.setStyleSheet(
        f"QWidget#{object_name} {{ background: {color}; }}"
    )
    return widget


def paint_transparent(widget, name=None, border=False):
    """Make ``widget`` paint nothing, without leaking onto its descendants.

    The scoped counterpart to :func:`paint_background`, and it exists because
    the obvious ``setStyleSheet("background: transparent")`` has a consequence
    well beyond the widget it is set on.

    WHAT THE BARE VERSION DID
    =========================
    A stylesheet set on a widget applies to that widget AND every descendant,
    and Qt counts a child WINDOW as a descendant. So a bare ``background:
    transparent`` on a layout container also reached any ``QMessageBox``
    parented to a page inside it — and a transparent top-level window has
    nothing behind it to composite, so Windows painted it black. "Project
    created" came up as a black rectangle with an invisible message in it;
    measured, the dialog's own pixels were ``alpha=0`` while its stylesheet was
    empty, which is inheritance and nothing else.

    Scoping by object name keeps the transparency on the one widget that asked
    for it. Descendants do not need to inherit it: a plain ``QWidget`` or
    ``QLabel`` paints no background of its own anyway, which is why this is
    safe as well as narrower.

    ``border=True`` also clears the border, for the callers that were setting
    ``"background:transparent; border:none;"`` together.
    """
    object_name = name or widget.objectName() or f"clear{id(widget):x}"
    widget.setObjectName(object_name)
    extra = " border: none;" if border else ""
    widget.setStyleSheet(
        f"#{object_name} {{ background: transparent;{extra} }}"
    )
    return widget


def clear_layout(layout):
    """Empty ``layout``, deleting its widgets — including nested layouts'.

    The obvious version of this loop only handles direct child WIDGETS::

        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    For a nested layout, ``item.widget()`` is ``None``, so the branch is
    skipped — but ``takeAt`` has already detached the item. The grandchild
    widgets are then orphaned: still parented to the host, still visible, and
    now unmanaged by any layout, so they keep whatever geometry they last had
    and float on top of whatever is drawn next. That is exactly what happened
    to the history view's "Try again" button, which hung over the middle of
    the chat list after a failed load was replaced by real rows.
    """
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            break
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)
            child.deleteLater()


def enable_height_for_width(widget):
    """Let a layout ask ``widget`` how tall it needs to be at a given width.

    ``setWordWrap(True)`` makes a ``QLabel`` ABLE to answer that question but
    does not make its size policy advertise it, and a ``QBoxLayout`` only asks
    widgets whose policy says ``hasHeightForWidth()``. Nested two layouts deep
    inside a scroll area, an un-advertised wrapped label gets one line's worth
    of height — so a two-line description either draws over the title above it
    or is silently clipped to its first line.

    Must be applied to the CONTAINER as well as the label (see
    :class:`SettingRow`), or the chain breaks at the first widget that does not
    advertise it.
    """
    policy = widget.sizePolicy()
    policy.setHeightForWidth(True)
    widget.setSizePolicy(policy)
    return widget


class WrappedLabel(QLabel):
    """A word-wrapped label that claims the height its text actually needs.

    WHY THIS IS NOT JUST ``setWordWrap(True)``
    ==========================================
    ``heightForWidth`` is advisory: a layout asks only if the widget's size
    policy advertises it, AND every link in the chain must forward the
    question. Inside a ``QScrollArea`` with ``widgetResizable`` and three
    levels of nested boxes — content widget, card, row, label — enabling it on
    all four still measured these descriptions as one line, so the second line
    was silently clipped. (Qt resolves the chain against sizeHint widths that
    do not match what the label is finally allocated.)

    So the label stops asking and starts telling. ``resizeEvent`` is the first
    moment it knows its REAL width, and from that width ``heightForWidth``
    gives an exact answer; pinning that as the minimum height forces the
    layout to honour it.

    The re-layout this triggers calls ``resizeEvent`` again, which is why the
    guard matters: the second pass computes the same height, finds the minimum
    already set, and changes nothing — so it settles in two passes instead of
    looping.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._syncing = False
        self.setWordWrap(True)
        # Minimum width 0 so the dock can still narrow; a wrapped label's
        # natural minimum is its longest word, which is fine for prose.
        self.setMinimumWidth(0)
        enable_height_for_width(self)

    def setText(self, text):
        super().setText(text)
        self._sync_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self):
        """Pin the exact height this text needs at the width it now has.

        The ``setMinimumHeight(0)`` is not tidying — it is load-bearing.
        ``QLabel.heightForWidth()`` is CLAMPED to ``minimumHeight()``, so
        measuring while our own pin is still in place just returns the pin.
        That made this a one-way ratchet: a label measured once at a narrow
        width kept its two-line height at every wider width forever, because
        every later measurement reported the two-line value back. Widening the
        panel left the text on one line inside a two-line box, which is where
        the suggestion cards' uneven heights and their mid-card gaps came from.

        Verified directly: a fresh label reports 13 at 397px; after
        ``setMinimumHeight(30)`` the same call reports 30; clearing it restores
        13.
        """
        if self._syncing:
            return
        width = self.width()
        if width <= 0:
            return
        self._syncing = True
        try:
            if self.minimumHeight():
                self.setMinimumHeight(0)
            needed = self.heightForWidth(width)
            if needed > 0 and needed != self.minimumHeight():
                self.setMinimumHeight(needed)
        finally:
            self._syncing = False


def wrapped_label(text, size=design.TEXT_MD, secondary=False, weight=None):
    """A pre-styled :class:`WrappedLabel`."""
    label = WrappedLabel(text)
    style = design.body_qss(size, secondary=secondary)
    if weight is not None:
        style += f" font-weight:{weight};"
    label.setStyleSheet(style)
    return label


def apply_scroll_style(area, transparent=True, horizontal=False):
    """Give an EXISTING ``QScrollArea`` the brand scrollbars and no frame.

    The counterpart to :func:`styled_scroll_area`, for the pages that build
    their own scroll area and cannot simply be handed one — Projects, GoBox,
    Processes (two of them), About and the process wizard. Without this they
    kept the stock OS scrollbar: a chunky grey trough with arrow buttons,
    against the slim inset thumb everywhere else in the panel. Same widget,
    two different scrollbars, depending on which view you were in.
    """
    area.setFrameShape(QFrame_NoFrame)
    if not horizontal:
        area.setHorizontalScrollBarPolicy(Qt_ScrollBarAlwaysOff)
    bg = "transparent" if transparent else design.tokens().canvas
    area.setStyleSheet(
        f"QScrollArea {{ background:{bg}; border:none; }}"
        + design.scrollbar_qss()
    )
    viewport = area.viewport()
    if viewport is not None:
        # Scoped by object name, like everything else here. A bare
        # `background:{bg};` on the VIEWPORT was the last leak of its kind,
        # and the worst placed: the viewport is an ancestor of every message
        # bubble, so a QMessageBox parented to a bubble — "Download failed",
        # "Load Error" — inherited a transparent background and came up as a
        # black rectangle with invisible text.
        #
        # It survived the first sweep because that one rewrote literal
        # `setStyleSheet("background: transparent;")` calls and this is an
        # f-string, and it survived the first regression test because that
        # walked UP from the pages, while a bubble sits several widgets below
        # one.
        #
        # Qt's own name for it is reused rather than replaced: Qt's built-in
        # stylesheet rules reference `qt_scrollarea_viewport`, so renaming it
        # would change how the stock style draws the area.
        name = viewport.objectName() or "scrollViewport"
        viewport.setObjectName(name)
        viewport.setStyleSheet(f"#{name} {{ background:{bg}; }}")
    return area


def styled_scroll_area(transparent=True, horizontal=False):
    """A new frameless :class:`QScrollArea` with the brand scrollbars."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    return apply_scroll_style(area, transparent=transparent,
                              horizontal=horizontal)


def _labelled_button(text, qss, icon_name, icon_color, icon_px, tooltip):
    btn = QPushButton(text)
    btn.setCursor(Qt_PointingHandCursor)
    # Icon buttons in a chat panel must never become the dialog's default, or
    # Return in the composer fires them instead of sending the message.
    btn.setAutoDefault(False)
    btn.setDefault(False)
    btn.setStyleSheet(qss)
    if icon_name:
        btn.setIcon(icons.icon(icon_name, icon_px, icon_color))
        btn.setIconSize(QSize(icon_px, icon_px))
    if tooltip:
        btn.setToolTip(tooltip)
    return btn


def primary_button(text, icon_name=None, tooltip="", icon_px=design.ICON_SM,
                   compact=False):
    """`.btn-primary` — the filled brand action, with an optional glyph.

    ``compact=True`` tightens the padding and drops a type step, for the
    action inside a two-up card where the full-size button would be wider
    than the card it sits in.
    """
    t = design.tokens()
    qss = (design.primary_button_qss(padding="6px 10px",
                                     font_size=design.TEXT_BASE)
           if compact else design.primary_button_qss())
    return _labelled_button(text, qss, icon_name, t.text_on_accent,
                            12 if compact else icon_px, tooltip)


def secondary_button(text, icon_name=None, tooltip="", icon_px=design.ICON_SM):
    """`.btn-white` — neutral surface action with a hairline border."""
    t = design.tokens()
    return _labelled_button(text, design.secondary_button_qss(), icon_name,
                            t.text_secondary, icon_px, tooltip)


def ghost_button(text, icon_name=None, tooltip="", icon_px=design.ICON_SM):
    """`.btn-transparent` — no chrome until hovered. Left-aligned text."""
    t = design.tokens()
    return _labelled_button(text, design.ghost_button_qss(), icon_name,
                            t.text_secondary, icon_px, tooltip)


def danger_button(text, icon_name=None, tooltip="", icon_px=design.ICON_SM):
    """`.btn-danger`, outline flavour — destructive but not shouting."""
    t = design.tokens()
    return _labelled_button(text, design.danger_button_qss(), icon_name,
                            t.error, icon_px, tooltip)


def heading(text, size=design.TEXT_LG, display=False, muted=False):
    """A heading label, pre-styled."""
    label = QLabel(text)
    label.setStyleSheet(design.heading_qss(size, display=display, muted=muted))
    return label


def body_label(text, size=design.TEXT_MD, secondary=False, wrap=True):
    """A body-copy label, pre-styled.

    Wrapped text goes through :func:`wrapped_label` so it reports an honest
    height for its width — see that function for what goes wrong otherwise.
    """
    if wrap:
        return wrapped_label(text, size, secondary=secondary)
    label = QLabel(text)
    label.setWordWrap(False)
    label.setStyleSheet(design.body_qss(size, secondary=secondary))
    return label


def vertical_spacer():
    """An expanding spacer widget, for pages that center their content."""
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy_Expanding, QSizePolicy_Expanding)
    paint_transparent(spacer)
    return spacer
