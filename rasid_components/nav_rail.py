"""The vertical tab bar down the edge of the GoPilot panel.

Top to bottom: the RASID mark, the five view tabs (new chat, projects, GoBox,
history, Settings), a hairline rule, then the two window controls — undock and
close — stacked under it. Everything is top-aligned; the rail's lower half is
empty space.

THE RULE SPLITS VIEWS FROM WINDOW CONTROLS
==========================================
Everything above it opens something in the stacked view; everything below it
changes what happens to the window. Settings belongs above, with the tabs: it
IS a view in that stack. It used to sit alone in a band at the foot of the
rail, which filed it with the window chrome and implied it was a different
KIND of thing from Projects or GoBox when it is the same kind.

That bottom band is gone. It existed to align Settings with the composer's +
and Send — its top border continued the composer's rule, and the glyph sat on
``COMPOSER_BUTTON_CENTRE`` — so once Settings moved up to the tabs the band had
nothing left to align. Keeping it only to hold Close put the close button a
full panel-height from the undock button, which is the one control a user
reaching for it is most likely to want next.

ICONS ONLY, WHY
===============
A GoPilot dock is ~420px wide and the transcript needs every one of them, so
the rail is 46px of pure icon. Each button therefore carries a tooltip, and
``new-chat`` is additionally the one labelled affordance users look for first,
so it is given the filled brand pill treatment rather than being left flat.

NEW CHAT IS AN ACTION, NOT A TAB
================================
Clicking it both switches to the chat view AND clears the current conversation.
It emits :attr:`action` rather than :attr:`selected`, and the shell decides what
"new chat" means — that way the rail never has to know about sessions. The chat
view's own highlight is driven by :meth:`set_current` afterwards.
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import QFrame, QLabel, QVBoxLayout

from . import branding, design
from .compat import Qt_AlignCenter, Qt_AlignHCenter
from .widgets import Divider, IconButton, paint_transparent

# (key, icon name, tooltip). Order is top-to-bottom.
NAV_ITEMS = (
    ("new-chat", "new-chat", "New chat"),
    ("projects", "projects", "Projects"),
    ("gobox", "gobox", "GoBox — ready-made solutions"),
    ("history", "history", "Chat history"),
    # Settings sits with the others because it IS one of them: it opens a view
    # in the same stack. It used to live alone in the bottom footer, which
    # grouped it with the window controls it has nothing to do with.
    ("settings", "settings", "Settings"),
)

# Keys that switch the stacked view rather than firing a one-shot action.
VIEW_KEYS = ("projects", "gobox", "history", "settings")


class NavRail(QFrame):
    """Fixed-width icon rail. Emits :attr:`selected` or :attr:`action`."""

    selected = pyqtSignal(str)   # a view key: projects | gobox | history | settings
    action = pyqtSignal(str)     # a one-shot action key: new-chat
    # The window controls QGIS's title bar used to provide.
    float_toggle_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(design.RAIL_WIDTH)
        self._buttons = {}

        t = design.tokens()
        # A left border, not a right one: the rail sits against the panel's
        # right edge, so the rule has to fall between it and the content.
        self.setStyleSheet(
            f"QFrame#navRail {{ background:{t.surface_alt}; "
            f"border-left:1px solid {t.border}; }}"
        )

        lay = QVBoxLayout(self)
        # No margins top or bottom: the logo block and the Settings footer own
        # that space, because their heights are what align this column with
        # the view header above and the composer below.
        #
        # The LEFT margin is this frame's own border width. The logo block and
        # the footer are full-width children with their own background, so at
        # a zero margin they painted straight over the rail's border-left —
        # the vertical rule separating rail from content vanished for exactly
        # the height of those two blocks and resumed in between.
        lay.setContentsMargins(design.BORDER, 0, 0, 0)
        lay.setSpacing(design.SPACE_1)

        lay.addWidget(self._build_logo_block())
        lay.addSpacing(design.SPACE_1)

        # Navigation first, so the tabs sit directly under the mark where the
        # eye lands; the window controls come after them.
        for key, icon_name, tip in NAV_ITEMS:
            btn = IconButton(icon_name, tooltip=tip)
            btn.clicked.connect(lambda _=False, k=key: self._on_click(k))
            self._buttons[key] = btn
            lay.addWidget(btn, alignment=Qt_AlignHCenter)

        lay.addSpacing(design.SPACE_1)
        lay.addWidget(self._build_window_controls())
        lay.addStretch()

    def _build_logo_block(self):
        """The RASID mark, at the head of the rail.

        Sized to ``VIEW_HEADER_HEIGHT`` so it occupies exactly the band the
        view-title strip occupies beside it, and carries the same bottom rule
        — the mirror of what the Settings footer does against the composer at
        the other end of the panel.

        The rule is hidden in the chat view, where there is no view-title strip
        for it to line up with; see :meth:`set_top_rule_visible`. The mark is
        not a button: there is nothing for "go to GoPilot" to mean when the
        panel IS GoPilot, and a clickable logo that does nothing is worse than
        a logo.
        """
        t = design.tokens()
        block = QFrame()
        block.setObjectName("navRailLogo")
        block.setFixedHeight(design.VIEW_HEADER_HEIGHT)
        lay = QVBoxLayout(block)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._logo = QLabel()
        paint_transparent(self._logo, border=True)
        self._logo.setPixmap(branding.brand_mark(t))
        self._logo.setToolTip(
            f"{branding.WORDMARK} {branding.WORDMARK_SUFFIX}")
        lay.addWidget(self._logo, alignment=Qt_AlignCenter)

        self._logo_block = block
        self._apply_logo_style(rule=False)
        return block

    def _apply_logo_style(self, rule):
        t = design.tokens()
        border = (f"border-bottom:{design.BORDER}px solid {t.border};"
                  if rule else "border:none;")
        self._logo_block.setStyleSheet(
            f"QFrame#navRailLogo {{ background:{t.surface_alt}; "
            f"border:none; {border} }}"
        )
        self._logo_rule_visible = rule

    def set_top_rule_visible(self, visible):
        """Show the logo block's bottom rule, to meet the view header's.

        Driven by the shell: the rule exists to continue the line under the
        view-title strip, so with no strip showing (the chat view) it would be
        a 46px stub ending in mid-air.
        """
        if bool(visible) != getattr(self, "_logo_rule_visible", False):
            self._apply_logo_style(bool(visible))

    def _build_window_controls(self):
        """Undock/dock and close — the controls a title bar used to carry.

        The panel has no title bar in either state, so these have to live
        somewhere; the rail is the only chrome left.

        Both sit directly under the navigation tabs, one above the other,
        behind a single rule. The rule separates "which view am I in" from
        "what happens to this window"; the pairing is because they are the same
        kind of answer to the second question, and a user looking for one is
        looking for the other.

        Close used to be pinned to a band at the very bottom of the rail
        instead, aligned with the composer. That band existed to line Settings
        up with the composer's + and Send, and when Settings moved up to the
        tabs it left Close sitting alone, a full panel-height away from the
        undock button it belongs with.
        """
        host = QFrame()
        paint_transparent(host, border=True)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(design.SPACE_1)

        rule = Divider()
        rule.setFixedWidth(design.RAIL_BUTTON - 8)
        lay.addWidget(rule, alignment=Qt_AlignHCenter)
        lay.addSpacing(design.SPACE_1)

        self.btn_float = IconButton(
            "undock", tooltip="Undock into a floating window")
        self.btn_float.clicked.connect(self.float_toggle_requested.emit)
        lay.addWidget(self.btn_float, alignment=Qt_AlignHCenter)

        # Red, matching the close control every window frame has — and the
        # trash in the transcript, so "red" means the same thing throughout
        # the panel.
        #
        # This was deliberately neutral, on the argument that closing only
        # HIDES the panel: the views, the transcript and any in-flight request
        # all survive, so a red X promises a destruction that does not happen.
        # That reasoning still holds and was overruled on purpose — the rail
        # is the only chrome the panel has, and the one control that makes it
        # go away should be the one that stands out. The tooltip carries the
        # reassurance instead.
        #
        # Legible on the rail: 3.52:1 in light and 3.97:1 in dark against
        # surface_alt, both over the 3:1 floor for a non-text graphic. Unlike
        # the user's message bubble, this surface is neutral, so plain `error`
        # works without a backing shape.
        self.btn_close = IconButton(
            "close", tooltip="Close panel (your chat is kept)",
            tone="danger", hover_tone="danger")
        self.btn_close.clicked.connect(self.close_requested.emit)
        lay.addWidget(self.btn_close, alignment=Qt_AlignHCenter)
        return host

    def set_floating(self, floating):
        """Offer the action that is actually available."""
        if floating:
            self.btn_float.set_icon_name("dock")
            self.btn_float.setToolTip("Dock back into the QGIS window")
        else:
            self.btn_float.set_icon_name("undock")
            self.btn_float.setToolTip("Undock into a floating window")

    def _on_click(self, key):
        if key in VIEW_KEYS:
            self.selected.emit(key)
        else:
            self.action.emit(key)

    def set_current(self, key):
        """Light the button for ``key``; clear every other one.

        An unknown key (e.g. the ``processes`` view, which the rail has no
        button for) clears the whole rail rather than raising — a drill-down is
        legitimately "no tab", and the shell maps it back to ``projects``.
        """
        for btn_key, btn in self._buttons.items():
            btn.set_active(btn_key == key)

    def current(self):
        for key, btn in self._buttons.items():
            if btn.is_active():
                return key
        return None

    def logo_block(self):
        """The mark's container, which doubles as a window drag handle.

        The block rather than the ``QLabel`` inside it: the label is centred in
        a 46x44 band and is only 24px square, so filtering the block gives the
        whole band instead of just the glyph.
        """
        return getattr(self, "_logo_block", None)

    def button(self, key):
        """The underlying :class:`IconButton`, for tooltips/badges later."""
        return self._buttons.get(key)

    def refresh_theme(self):
        t = design.tokens()
        self.setStyleSheet(
            f"QFrame#navRail {{ background:{t.surface_alt}; "
            f"border-left:1px solid {t.border}; }}"
        )
        if getattr(self, "_logo_block", None) is not None:
            self._logo.setPixmap(branding.brand_mark(t))
            self._apply_logo_style(getattr(self, "_logo_rule_visible", False))
        for btn in self._buttons.values():
            btn.refresh_theme()
        for btn in (getattr(self, "btn_float", None),
                    getattr(self, "btn_close", None)):
            if btn is not None:
                btn.refresh_theme()
