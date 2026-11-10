"""RASID design system — the single source of colour, type and spacing.

WHY THIS FILE EXISTS
====================
The plugin and the RASID SaaS web app are one product, so they have to look
like one product. The SaaS defines its palette in ``tailwind.config.js`` and its
component recipes in ``src/index.css``; this module is the Qt port of those two
files. Every raw value below is copied from the SaaS, not re-invented, so a
brand change there is a one-line change here.

WHAT IS PORTED, AND FROM WHERE
------------------------------
* ``BRAND``  — tailwind ``colors.brand`` (RASID logo green, 50-900 scale).
* ``SLATE``  — tailwind ``colors.slate``, the WARM TAUPE neutral ramp derived
  from the brand gray-brown ``#5F575A``. These are deliberately NOT Tailwind's
  stock cool slates; using cool greys here is what makes a RASID screen look
  off-brand.
* ``STATE``  — tailwind ``colors.state`` (success / warning / error / info).
* ``SAND`` / ``GOLD`` — warm support surface and accolade gold.
* Radii, type scale and spacing — the values the SaaS components actually use
  (``rounded-2xl`` composer, ``rounded-xl`` cards, ``text-sm`` body, …).

LIGHT AND DARK
--------------
The SaaS ships light only. QGIS does not: a user on the Night Mapping theme
gets a dark palette and any hardcoded white surface in a plugin turns into a
glowing hole. So this module resolves SEMANTIC tokens (``surface``, ``text``,
``border``, ``accent``, …) per theme through :func:`tokens`, and widgets are
written against those names rather than against raw hex.

Two things change in dark mode on purpose:

* Neutrals are taken from the DARK end of the same warm taupe ramp, so dark
  mode stays warm and on-brand instead of going blue-grey.
* The accent moves from ``brand.500`` (#008B6A) to ``brand.highlight``
  (#009D84) and hovers to ``brand.400``. #008B6A on a #201D1F surface is a
  3.0:1 contrast, which fails AA for the small text and icons we put on it;
  the brighter greens clear it while reading as the same colour family.

USAGE
=====
    from .design import tokens, RADIUS, TEXT_SM

    t = tokens()
    frame.setStyleSheet(
        f"background:{t.surface}; border:1px solid {t.border};"
        f"border-radius:{RADIUS}px; color:{t.text};"
    )

:func:`tokens` is resolved on every call (it is a cheap dict lookup after the
first palette probe) so a widget built after the user switches QGIS themes gets
the right set without a plugin reload.

RULES
=====
1. No hex literals in widget code. If a colour is missing, add a semantic token
   here and use that.
2. Never put a raw ``BRAND``/``SLATE`` value in a stylesheet directly — go
   through :func:`tokens`, or the widget will be unreadable in one of the two
   themes.
3. Keep the ported scales in sync with the SaaS. The comments name the exact
   Tailwind key each value came from.
"""

from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication

# ── 1. BRAND IDENTITY — RASID green (exact logo values) ──────────────────────
# Ported from tailwind.config.js `colors.brand`.
BRAND = {
    50:  "#E6F5F0",   # Mint — subtle surfaces, selected cards
    100: "#C7EBE0",
    200: "#97D9C8",
    300: "#5FC2AC",
    400: "#29A98C",   # dark-mode accent hover
    500: "#008B6A",   # Primary Green (logo mark) — THE brand colour
    600: "#007A5E",   # pressed / hover-dark
    700: "#006655",   # deep logo green
    800: "#0A5044",
    900: "#0C3F37",
    "highlight": "#009D84",  # Bright logo green — dark-mode accent, progress
    "soft": "#F2FBF8",       # very subtle brand-tinted surface
}

# Secondary accent — RASID teal. Links, data-viz series, secondary actions.
ACCENT = {
    "DEFAULT": "#009D84",
    "light":   "#E0F4EF",
    "dark":    "#007E6A",
}

# ── 2. NEUTRALS — warm taupe, derived from the brand gray-brown #5F575A ──────
# Ported from tailwind.config.js `colors.slate`. Warm on purpose: see module
# docstring. 650/750/850 are interpolated rungs the SaaS does not need (it has
# no dark mode) but Qt does, to keep dark surfaces from banding together.
SLATE = {
    50:  "#F8F7F7",   # card bg / alternating rows
    100: "#F1EEEF",   # app background
    200: "#E6E2E3",   # hover state on light backgrounds
    300: "#D4CED0",   # borders / dividers
    400: "#A99FA2",   # muted text / icons / placeholders
    500: "#7C7477",   # secondary text
    600: "#5F575A",   # brand gray-brown — strong labels
    650: "#544E50",   # (interpolated) dark-mode strong border
    700: "#4A4446",   # body / subheadings
    750: "#3E393B",   # (interpolated) dark-mode raised surface
    800: "#322E30",   # primary text / card headers
    850: "#2A2628",   # (interpolated) dark-mode surface
    900: "#201D1F",   # headings / darkest surfaces
    950: "#141213",
}

# Warm support surface (letterhead / wallpaper family), alongside Mint.
SAND = {"DEFAULT": "#F1ECE4", 50: "#FAF8F4", 100: "#F1ECE4", 200: "#E7E0D4"}

# Award / accolade gold. Its own token: `STATE["warning"]` means "caution" and
# must not be borrowed for a trophy.
GOLD = {"DEFAULT": "#C9A227", "light": "#FAF3DF", "dark": "#7A5F0F"}

# ── 3. SEMANTIC STATES (the feedback colours) ────────────────────────────────
# Ported from tailwind.config.js `colors.state`.
STATE = {
    "success":     "#10B981",
    "successDark": "#059669",
    "warning":     "#F59E0B",
    "warningDark": "#D97706",
    "error":       "#EF4444",
    "errorDark":   "#DC2626",
    "info":        "#3B82F6",
    "infoDark":    "#2563EB",
}

# ── 4. SHAPE ─────────────────────────────────────────────────────────────────
# The radii the SaaS components use, named by their Tailwind class.
RADIUS_XS = 4     # rounded
RADIUS_SM = 6     # rounded-md — small buttons, badges
RADIUS = 8        # rounded-lg — inputs, icon buttons, nav items
RADIUS_MD = 10
RADIUS_LG = 12    # rounded-xl — cards (.card)
RADIUS_XL = 16    # rounded-2xl — the chat composer pill


def pill_radius(height):
    """Radius that makes a ``height``-tall box a true pill / circle.

    There is deliberately no ``RADIUS_PILL`` constant to go with the scale
    above. CSS clamps an oversized ``border-radius`` to half the box, so the
    web idiom ``rounded-full`` is written as a huge number and left alone —
    but **Qt does not clamp**. Past the box size Qt's stylesheet engine
    degrades back toward a square: on a 30px button, radius 15 is a perfect
    circle, 40 is a rounded square again, and 999 is very nearly a plain
    square. A constant is therefore the wrong shape for this value; it has to
    be derived from the widget it is applied to.
    """
    return max(0, int(height) // 2)

# ── 5. TYPE ──────────────────────────────────────────────────────────────────
# Inter for UI, Space Grotesk for display headings — the SaaS `fontFamily`.
# Both are webfonts there; here they are used only if the user happens to have
# them installed, which is why each stack ends in the platform UI font.
FONT_STACK = ('"Inter", "Segoe UI", system-ui, -apple-system, '
              '"Helvetica Neue", Arial, sans-serif')
DISPLAY_STACK = ('"Space Grotesk", "Inter", "Segoe UI", system-ui, '
                 'sans-serif')
MONO_STACK = '"JetBrains Mono", Menlo, Consolas, "Courier New", monospace'

# The same stacks, quoted for an HTML ``style="..."`` ATTRIBUTE.
#
# A Qt stylesheet takes the double-quoted form above without complaint. An
# HTML attribute does not: the first inner ``"`` closes the attribute, so
#
#     style="font-family:"JetBrains Mono", …; font-size:12px; color:#322E30;"
#
# ends at ``font-family:`` and everything after it is parsed as stray
# attributes. Measured through QTextDocument: fenced code blocks resolved to
# NO font family — they were drawn in the body font, at the body size, in the
# body colour — and the only reason inline code looked monospace anyway is
# Qt's own built-in rule for ``<code>``.
#
# Use these two anywhere the result goes into HTML rather than into
# setStyleSheet.
FONT_STACK_HTML = FONT_STACK.replace('"', "'")
DISPLAY_STACK_HTML = DISPLAY_STACK.replace('"', "'")
MONO_STACK_HTML = MONO_STACK.replace('"', "'")

# Point-ish pixel sizes. Smaller than the web scale by one step across the
# board: a QGIS dock is ~380px wide where the SaaS panel is ~720px, and Qt
# renders text at the OS scale factor already.
TEXT_XS = 10      # timestamps, meta
TEXT_SM = 11      # captions, chips, helper text
TEXT_BASE = 12    # secondary body
TEXT_MD = 13      # body / message text  (the workhorse)
TEXT_LG = 15      # section headings
TEXT_XL = 17      # page title
TEXT_DISPLAY = 19 # the "GoPilot by RASID" wordmark
TEXT_HERO = 23    # the same wordmark as the empty state's hero

WEIGHT_NORMAL = 400
WEIGHT_MEDIUM = 500
WEIGHT_SEMIBOLD = 600
WEIGHT_BOLD = 700

# ── 6. SPACING — the 4px grid the SaaS lays out on ───────────────────────────
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24

# Fixed metrics for the dock shell, so the rail and the composer agree.
RAIL_WIDTH = 46          # vertical tab bar
RAIL_BUTTON = 34         # square icon button inside the rail
CHIP_HEIGHT = 22         # attachment pill; pinned so its radius is exact

# The panel has NO title bar of its own: QGIS's native dock title bar carries
# the window title, the float control and the close button, and a second bar
# under it only duplicated the wordmark. The brand lives inside the product
# instead — the mark at the top of the nav rail, the wordmark in the chat's
# empty state.
RAIL_LOGO = 24           # the RASID mark at the top of the rail

# Height of the strip that names the current view, and of the rail's logo block
# beside it. Pinned rather than left to the contents so the two agree and their
# bottom rules form one line, the same contract the composer and the rail
# footer have at the other end of the panel.
VIEW_HEADER_HEIGHT = 44

# The reasoning and tool panels above an assistant answer. FIXED heights, with
# the content scrolling inside.
#
# Reasoning routinely runs to hundreds of words and the tool list to a dozen
# rows. Letting either size itself pushed the answer — the thing actually being
# read — off the bottom of a 420px panel, and made the bubble jump on every
# poll as the reasoning grew. A fixed box costs a scroll gesture to read it all
# and keeps the answer where the eye already is.
#
# Side by side when both are present — reasoning left, tools right — which is
# the SaaS's arrangement too. ONE height for both, not one each: two boxes of
# different heights in the same row leave a ragged bottom edge, and the only
# thing a reader gains from the tools box being shorter is a gap.
#
# A single panel, when the turn produced only one of the two, spans the full
# width instead; see MessageBubble._size_panels.
REASONING_PANEL_HEIGHT = 88

# ── THE COMPOSER / RAIL-FOOTER ALIGNMENT CONTRACT ────────────────────────────
# The bottom of the panel reads as one band across both columns: the composer's
# top border and the rule above Settings are a SINGLE line, and the +, Send and
# Settings glyphs sit on a SINGLE centre line. Neither is true unless both
# columns are measured from the same numbers, which is what these are for.
#
#   ┌───────────────────────────────────────────┬──────┐
#   │ ·········· one continuous rule ·········· │ ···· │  <- COMPOSER_ROW_HEIGHT
#   │  ╭─────────────────────────────────────╮  │      │     from the bottom
#   │  │ (+)   Ask GoPilot…           (send) │  │ (⚙)  │  <- one centre line
#   │  ╰─────────────────────────────────────╯  │      │
#   └───────────────────────────────────────────┴──────┘
#
# The vertical padding is SYMMETRIC on purpose. With uneven padding the band's
# height and its centre line stop being derivable from each other, so the rail
# footer could match one or the other but never both at once.
COMPOSER_BUTTON = 30     # the + and Send — same size as each other, by design
COMPOSER_PILL_PAD = 5    # inside the rounded pill, around the buttons
COMPOSER_PAD = 10        # outside the pill, above and below
BORDER = 1               # every hairline in this UI

# BORDERS COUNT. A Qt stylesheet border is reserved IN ADDITION to the layout's
# own contents margins, not inside them — so the pill is 2px taller than its
# padding plus its buttons, and the composer 1px taller again for its top rule.
# Leaving those 3px out is what left the two columns 3px and 1px apart when
# these numbers were first derived.
COMPOSER_ROW_HEIGHT = (
    BORDER                                  # the composer's own top rule
    + COMPOSER_PAD
    + BORDER + COMPOSER_PILL_PAD            # the pill's border, then its pad
    + COMPOSER_BUTTON
    + COMPOSER_PILL_PAD + BORDER
    + COMPOSER_PAD
)

# Where the +/Send glyphs sit, measured up from the panel's bottom edge. The
# rail's Settings button is placed on this same line. It is NOT simply
# COMPOSER_ROW_HEIGHT / 2: the band carries a rule along its top edge and none
# along its bottom, so its centre and the buttons' centre differ by half a
# pixel-row either way.
COMPOSER_BUTTON_CENTRE = (COMPOSER_PAD + BORDER + COMPOSER_PILL_PAD
                          + COMPOSER_BUTTON / 2)
ICON_SM = 14
ICON = 17
ICON_LG = 20
# ── PANEL WIDTH, AND THE CARD GRID THAT HAS TO FIT IN IT ────────────────────
# The minimum is set by the narrowest thing the panel must still display
# properly, and that is a TWO-COLUMN card grid — one column of solutions in a
# panel this size wastes the width and makes browsing a long scroll.
#
# The arithmetic, which is why these five numbers are here together rather than
# scattered across the two grid pages:
#
#     usable   = DOCK_MIN_WIDTH - RAIL_WIDTH - scrollbar
#              = 400 - 46 - 10                        = 344
#     available= usable - CARD_GRID_MARGIN * 2        = 320
#     per card = (available - CARD_GRID_SPACING) / 2  = 156
#
# So 156px is what a GoBox card actually gets at the minimum, and
# GOBOX_CARD_MIN_WIDTH is set below it so two always fit. Changing any of them
# means re-checking that sum (there is a test that does).
DOCK_MIN_WIDTH = 400
DOCK_DEFAULT_WIDTH = 420

CARD_GRID_MARGIN = 12    # around the grid, inside the scroll area
CARD_GRID_SPACING = 8    # between cards

# The smallest a card may be before the grid drops a column. Both grids use
# the same floor: a card is a thumbnail, a title, a line or two of meta and
# one action, which survives being small. Project cards carry a little more
# (solution, process count, tags) so the extras that do not fit are dropped
# at narrow widths rather than the card being made wider — see
# ProjectsPage._create_card.
# One floor for both grids, so both go two-up at the panel's minimum width.
#
# The SaaS project card puts its solution chip and date on ONE row, which
# needs ~250px. Rather than give projects a wider floor (which cost two-up and
# made the cards feel huge), the card keeps every element the web card has and
# redistributes them down the card — see _ProjectCard. Same content, GoBox
# density.
GOBOX_CARD_MIN_WIDTH = 150
PROJECT_CARD_MIN_WIDTH = 150

# Columns are capped so that widening the panel grows the CARDS rather than
# multiplying them — four 156px cards in a floating window is not a better use
# of the space than three roomy ones.
CARD_MAX_COLS = 3

# How long the card grids wait after a resize before rebuilding. Dragging the
# panel edge emits a resize per mouse move, and a rebuild destroys and
# recreates every card (and re-requests every thumbnail), so relaying out on
# each one measured 42ms per step with a 14-item catalogue — visibly laggy.
# Coalescing the burst into one rebuild costs an imperceptible pause after the
# drag stops.
GRID_RELAYOUT_DELAY_MS = 120

# Card thumbnails are 16:9 — the SaaS cards use Tailwind's `aspect-video`.
# Full-bleed and cropped to cover (see image_loader.scale_to_cover), so this
# is the real drawn height.
CARD_THUMB_ASPECT = 0.5625


# ── Helpers ──────────────────────────────────────────────────────────────────
def alpha(hex_color, opacity):
    """Return ``hex_color`` as an ``rgba()`` string at ``opacity`` (0.0-1.0).

    Qt stylesheets understand ``rgba()`` but not ``#RRGGBBAA``, and they do not
    understand CSS ``color-mix``/variables at all — so translucent tints (hover
    washes, overlay scrims, the streaming caret) have to be built here.
    """
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {opacity:.3f})"


def mix(hex_a, hex_b, ratio=0.5):
    """Blend two colours. ``ratio`` is how much of ``hex_b`` to use.

    Used to interpolate ramp rungs (e.g. a border that sits between two
    surfaces) without hardcoding another hex.
    """
    a, b = QColor(hex_a), QColor(hex_b)
    r = round(a.red() * (1 - ratio) + b.red() * ratio)
    g = round(a.green() * (1 - ratio) + b.green() * ratio)
    bl = round(a.blue() * (1 - ratio) + b.blue() * ratio)
    return QColor(r, g, bl).name()


def is_dark():
    """True when QGIS is running a dark theme.

    Decided from the luminance of the application palette's Window colour
    rather than from the theme's NAME: users rename themes, ship their own, and
    QGIS's own "Night Mapping" is not the only dark one in the wild.
    """
    palette = QApplication.palette()
    # Scoped enum works on PyQt5 5.15+ and PyQt6 alike (see compat.py).
    bg = palette.color(QPalette.ColorRole.Window)
    luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return luminance < 128


class Tokens:
    """Resolved semantic colours for one theme.

    Attribute names describe the ROLE, never the colour, so the same widget
    code reads correctly in both themes. Grouped below as:
    surfaces -> borders -> text -> accent -> states -> chat -> scrollbar.
    """

    __slots__ = (
        "dark",
        # surfaces
        "canvas", "surface", "surface_alt", "surface_raised", "surface_sunken",
        "overlay", "hover", "pressed", "selected",
        # borders
        "border", "border_strong", "border_subtle", "divider", "focus_ring",
        # text
        "text", "text_secondary", "text_muted", "text_disabled",
        "text_inverse", "text_on_accent",
        # accent
        "accent", "accent_hover", "accent_pressed", "accent_soft",
        "accent_soft_border", "accent_text",
        # states
        "success", "success_soft", "warning", "warning_soft",
        "error", "error_hover", "error_soft",
        "info", "info_soft",
        # chat
        "bubble_user", "bubble_user_text", "bubble_ai", "bubble_ai_border",
        "bubble_ai_text", "bubble_system", "bubble_system_text",
        "code_bg", "composer_bg",
        # scrollbar
        "scroll_thumb", "scroll_thumb_hover",
    )

    def __init__(self, dark):
        self.dark = dark
        if dark:
            self._init_dark()
        else:
            self._init_light()

    def _init_light(self):
        # Surfaces. `canvas` is the SaaS app background (slate-100) and
        # `surface` the white card that sits on it — that pairing is what makes
        # cards read as grounded rather than as boxes floating on white.
        self.canvas = SLATE[100]
        self.surface = "#FFFFFF"
        self.surface_alt = SLATE[50]
        self.surface_raised = "#FFFFFF"
        self.surface_sunken = SLATE[100]
        self.overlay = alpha(SLATE[900], 0.45)
        self.hover = SLATE[200]
        self.pressed = SLATE[300]
        self.selected = BRAND[50]

        self.border = SLATE[200]
        self.border_strong = SLATE[300]
        self.border_subtle = SLATE[100]
        self.divider = SLATE[200]
        self.focus_ring = BRAND[500]

        self.text = SLATE[800]
        self.text_secondary = SLATE[600]
        self.text_muted = SLATE[400]
        self.text_disabled = SLATE[300]
        self.text_inverse = "#FFFFFF"
        self.text_on_accent = "#FFFFFF"

        self.accent = BRAND[500]
        self.accent_hover = BRAND[600]
        self.accent_pressed = BRAND[700]
        self.accent_soft = BRAND[50]
        self.accent_soft_border = BRAND[200]
        self.accent_text = BRAND[600]

        self.success = STATE["success"]
        self.success_soft = "#E7F8F1"
        self.warning = STATE["warning"]
        self.warning_soft = "#FEF5E7"
        self.error = STATE["error"]
        self.error_hover = STATE["errorDark"]
        self.error_soft = "#FDECEC"

        self.info = STATE["info"]
        self.info_soft = "#EBF2FE"

        self.bubble_user = BRAND[500]
        self.bubble_user_text = "#FFFFFF"
        self.bubble_ai = "#FFFFFF"
        self.bubble_ai_border = SLATE[200]
        self.bubble_ai_text = SLATE[800]
        self.bubble_system = SLATE[100]
        self.bubble_system_text = SLATE[600]
        self.code_bg = SLATE[100]
        self.composer_bg = SLATE[50]

        self.scroll_thumb = alpha(SLATE[600], 0.40)
        self.scroll_thumb_hover = alpha(SLATE[600], 0.62)

    def _init_dark(self):
        # The dark ramp walks the SAME warm taupe scale from the other end, so
        # dark mode stays warm. Three distinct surface levels (sunken / base /
        # raised) keep the composer, the transcript and the bubbles separable
        # without drawing a border around everything.
        self.canvas = SLATE[950]
        self.surface = SLATE[900]
        self.surface_alt = SLATE[850]
        self.surface_raised = SLATE[750]
        self.surface_sunken = "#0E0D0D"
        self.overlay = alpha("#000000", 0.60)
        self.hover = SLATE[750]
        self.pressed = SLATE[700]
        self.selected = "#10342C"          # brand-900 pulled toward the surface

        self.border = SLATE[750]
        self.border_strong = SLATE[650]
        self.border_subtle = SLATE[850]
        self.divider = SLATE[800]
        self.focus_ring = BRAND["highlight"]

        self.text = SLATE[100]
        self.text_secondary = SLATE[400]
        self.text_muted = SLATE[500]
        self.text_disabled = SLATE[650]
        self.text_inverse = SLATE[900]
        self.text_on_accent = "#FFFFFF"

        # Brighter end of the brand ramp — #008B6A fails AA on these surfaces.
        self.accent = BRAND["highlight"]
        self.accent_hover = BRAND[400]
        self.accent_pressed = BRAND[500]
        self.accent_soft = "#13332C"
        self.accent_soft_border = BRAND[800]
        self.accent_text = BRAND[300]

        self.success = STATE["success"]
        self.success_soft = "#10332A"
        self.warning = STATE["warning"]
        self.warning_soft = "#3A2C10"
        self.error = STATE["error"]
        self.error_hover = STATE["errorDark"]
        self.error_soft = "#3D1F1F"

        self.info = STATE["info"]
        self.info_soft = "#16253D"

        # The user bubble keeps the primary green even in dark mode: it carries
        # white text, which is 4.9:1 on #008B6A, and it is the one place the
        # exact logo colour should still appear.
        self.bubble_user = BRAND[600]
        self.bubble_user_text = "#FFFFFF"
        self.bubble_ai = SLATE[850]
        self.bubble_ai_border = SLATE[750]
        self.bubble_ai_text = SLATE[100]
        self.bubble_system = SLATE[850]
        self.bubble_system_text = SLATE[400]
        self.code_bg = "#121011"
        self.composer_bg = SLATE[850]

        self.scroll_thumb = alpha("#FFFFFF", 0.28)
        self.scroll_thumb_hover = alpha("#FFFFFF", 0.46)


_CACHE = {}


def tokens():
    """Semantic colours for the theme QGIS is running right now.

    Cached per mode, so calling this once per widget in a constructor is free.
    """
    dark = is_dark()
    if dark not in _CACHE:
        _CACHE[dark] = Tokens(dark)
    return _CACHE[dark]


def invalidate():
    """Drop the cache. Call if the host palette changes mid-session."""
    _CACHE.clear()


# ── Reusable stylesheet recipes ───────────────────────────────────────────────
# The Qt equivalents of the SaaS component classes in src/index.css. Each
# returns a stylesheet string rather than being applied globally, because a
# QGIS plugin must never restyle widgets it does not own.

def scrollbar_qss(selector=""):
    """Slim, inset, auto-hiding-looking scrollbars matching the SaaS.

    ``selector`` scopes the rules to one widget subtree (e.g. ``"QScrollArea"``)
    so the plugin never repaints scrollbars elsewhere in QGIS.
    """
    t = tokens()
    s = f"{selector} " if selector else ""
    return f"""
        {s}QScrollBar:vertical {{
            background: transparent; width: 10px; margin: 0;
        }}
        {s}QScrollBar::handle:vertical {{
            background: {t.scroll_thumb}; border-radius: 5px; min-height: 28px;
        }}
        {s}QScrollBar::handle:vertical:hover {{
            background: {t.scroll_thumb_hover};
        }}
        {s}QScrollBar:horizontal {{
            background: transparent; height: 10px; margin: 0;
        }}
        {s}QScrollBar::handle:horizontal {{
            background: {t.scroll_thumb}; border-radius: 5px; min-width: 28px;
        }}
        {s}QScrollBar::handle:horizontal:hover {{
            background: {t.scroll_thumb_hover};
        }}
        {s}QScrollBar::add-line, {s}QScrollBar::sub-line {{
            width: 0; height: 0; background: none; border: none;
        }}
        {s}QScrollBar::add-page, {s}QScrollBar::sub-page {{
            background: none;
        }}
    """


def primary_button_qss(radius=RADIUS, padding="8px 14px", font_size=TEXT_MD):
    """`.btn .btn-primary` — the filled brand action."""
    t = tokens()
    return f"""
        QPushButton {{
            background: {t.accent}; color: {t.text_on_accent};
            border: none; border-radius: {radius}px; padding: {padding};
            font-family: {FONT_STACK}; font-size: {font_size}px;
            font-weight: {WEIGHT_SEMIBOLD};
        }}
        QPushButton:hover {{ background: {t.accent_hover}; }}
        QPushButton:pressed {{ background: {t.accent_pressed}; }}
        QPushButton:disabled {{
            background: {t.hover}; color: {t.text_disabled};
        }}
    """


def secondary_button_qss(radius=RADIUS, padding="8px 14px",
                         font_size=TEXT_MD):
    """`.btn .btn-white` — neutral surface button with a hairline border."""
    t = tokens()
    return f"""
        QPushButton {{
            background: {t.surface}; color: {t.text};
            border: 1px solid {t.border_strong}; border-radius: {radius}px;
            padding: {padding}; font-family: {FONT_STACK};
            font-size: {font_size}px; font-weight: {WEIGHT_MEDIUM};
        }}
        QPushButton:hover {{
            background: {t.surface_alt}; border-color: {t.accent};
            color: {t.accent_text};
        }}
        QPushButton:pressed {{ background: {t.hover}; }}
        QPushButton:disabled {{
            color: {t.text_disabled}; border-color: {t.border};
        }}
    """


def ghost_button_qss(radius=RADIUS, padding="6px 10px", font_size=TEXT_BASE):
    """`.btn-transparent` — no chrome until hovered."""
    t = tokens()
    return f"""
        QPushButton {{
            background: transparent; color: {t.text_secondary};
            border: none; border-radius: {radius}px; padding: {padding};
            font-family: {FONT_STACK}; font-size: {font_size}px;
            font-weight: {WEIGHT_MEDIUM}; text-align: left;
        }}
        QPushButton:hover {{ background: {t.hover}; color: {t.text}; }}
        QPushButton:pressed {{ background: {t.pressed}; }}
        QPushButton:disabled {{ color: {t.text_disabled}; }}
    """


def danger_button_qss(radius=RADIUS, padding="7px 12px", font_size=TEXT_BASE):
    """`.btn-danger`, outline flavour — destructive but not shouting."""
    t = tokens()
    return f"""
        QPushButton {{
            background: transparent; color: {t.error};
            border: 1px solid {t.error}; border-radius: {radius}px;
            padding: {padding}; font-family: {FONT_STACK};
            font-size: {font_size}px; font-weight: {WEIGHT_SEMIBOLD};
        }}
        QPushButton:hover {{ background: {t.error_soft}; }}
        QPushButton:pressed {{ background: {t.error}; color: #FFFFFF; }}
    """


def card_qss(object_name, radius=RADIUS_LG):
    """`.card` — white surface, hairline border, soft radius.

    Scoped by ``objectName`` because an unscoped ``QFrame`` rule in a plugin
    leaks onto every frame in the widget tree, including QGIS's own.
    """
    t = tokens()
    return f"""
        QFrame#{object_name} {{
            background: {t.surface}; border: 1px solid {t.border};
            border-radius: {radius}px;
        }}
    """


def input_qss(radius=RADIUS, padding="8px 12px", font_size=TEXT_MD):
    """`.form-control` — includes the brand focus border."""
    t = tokens()
    return f"""
        QLineEdit, QPlainTextEdit, QTextEdit {{
            background: {t.surface}; color: {t.text};
            border: 1px solid {t.border_strong}; border-radius: {radius}px;
            padding: {padding}; font-family: {FONT_STACK};
            font-size: {font_size}px;
            selection-background-color: {t.accent};
            selection-color: {t.text_on_accent};
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
            border: 1px solid {t.focus_ring};
        }}
        QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
            background: {t.surface_alt}; color: {t.text_disabled};
        }}
    """


def select_qss(radius=RADIUS, padding="7px 10px", font_size=TEXT_MD):
    """`QComboBox`, matching :func:`input_qss`'s box so a form row lines up.

    The popup is styled separately from the closed control: Qt renders it as a
    child ``QAbstractItemView``, which inherits none of the combo's own rules.
    Left unstyled it came up as a native list — white on white in dark mode,
    with the host theme's selection colour.

    The arrow is the same ``chevron-down`` the rest of the panel uses, written
    to a PNG by :func:`icons.stylesheet_url`. The first attempt built it from
    two rotated borders, which is the standard CSS trick and does not work
    here: Qt stylesheets have no ``transform``, so what rendered was an
    L-shaped corner rather than a chevron.
    """
    from . import icons
    t = tokens()
    arrow = icons.stylesheet_url("chevron-down", 10, t.text_secondary)
    arrow_disabled = icons.stylesheet_url("chevron-down", 10, t.text_disabled)
    return f"""
        QComboBox {{
            background: {t.surface}; color: {t.text};
            border: 1px solid {t.border_strong}; border-radius: {radius}px;
            padding: {padding}; font-family: {FONT_STACK};
            font-size: {font_size}px;
        }}
        QComboBox:hover {{ border-color: {t.accent_soft_border}; }}
        QComboBox:focus {{ border-color: {t.focus_ring}; }}
        QComboBox:disabled {{
            background: {t.surface_alt}; color: {t.text_disabled};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding; subcontrol-position: center right;
            width: 20px; border: none; background: transparent;
        }}
        QComboBox::down-arrow {{
            image: url({arrow}); width: 10px; height: 10px;
            margin-right: 6px;
        }}
        QComboBox::down-arrow:disabled {{
            image: url({arrow_disabled});
        }}
        QComboBox QAbstractItemView {{
            background: {t.surface}; color: {t.text};
            border: 1px solid {t.border}; border-radius: {RADIUS_SM}px;
            selection-background-color: {t.accent_soft};
            selection-color: {t.accent_text};
            outline: none; padding: 2px;
        }}
    """


def spinbox_qss(radius=RADIUS, padding="7px 10px", font_size=TEXT_MD):
    """`QSpinBox` and `QDateEdit` — same box, with usable step buttons.

    Qt's default up/down buttons are tiny native arrows that sit outside this
    palette entirely. These are flat, tokenised, and tall enough to hit: half
    the control each, flush to its right edge. The arrows themselves are drawn
    chevrons, for the reason given in :func:`select_qss`.
    """
    from . import icons
    t = tokens()
    down = icons.stylesheet_url("chevron-down", 9, t.text_secondary)
    up = icons.stylesheet_url("chevron-up", 9, t.text_secondary)
    return f"""
        QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {{
            background: {t.surface}; color: {t.text};
            border: 1px solid {t.border_strong}; border-radius: {radius}px;
            padding: {padding}; font-family: {FONT_STACK};
            font-size: {font_size}px;
            selection-background-color: {t.accent};
            selection-color: {t.text_on_accent};
        }}
        QSpinBox:focus, QDoubleSpinBox:focus,
        QDateEdit:focus, QDateTimeEdit:focus {{
            border-color: {t.focus_ring};
        }}
        QSpinBox:disabled, QDoubleSpinBox:disabled,
        QDateEdit:disabled, QDateTimeEdit:disabled {{
            background: {t.surface_alt}; color: {t.text_disabled};
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button,
        QDateEdit::up-button, QDateTimeEdit::up-button,
        QSpinBox::down-button, QDoubleSpinBox::down-button,
        QDateEdit::down-button, QDateTimeEdit::down-button {{
            subcontrol-origin: border; width: 18px;
            background: transparent; border: none;
        }}
        /* A QDateEdit with setCalendarPopup(True) does NOT use the up/down
           buttons above — it grows a `drop-down` subcontrol instead, and left
           unstyled that one keeps its native frame: a dark notch bitten out of
           the right edge of the field, which is what it looked like. */
        QDateEdit::drop-down, QDateTimeEdit::drop-down {{
            subcontrol-origin: padding; subcontrol-position: center right;
            width: 20px; background: transparent; border: none;
        }}
        QSpinBox::up-button, QDoubleSpinBox::up-button,
        QDateEdit::up-button, QDateTimeEdit::up-button {{
            subcontrol-position: top right; margin: 2px 2px 0 0;
        }}
        QSpinBox::down-button, QDoubleSpinBox::down-button,
        QDateEdit::down-button, QDateTimeEdit::down-button {{
            subcontrol-position: bottom right; margin: 0 2px 2px 0;
        }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
        QDateEdit::up-arrow, QDateTimeEdit::up-arrow {{
            image: url({up}); width: 9px; height: 9px;
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
        QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{
            image: url({down}); width: 9px; height: 9px;
        }}
        QCalendarWidget QAbstractItemView {{
            background: {t.surface}; color: {t.text};
            selection-background-color: {t.accent};
            selection-color: {t.text_on_accent};
            outline: none;
        }}
        QCalendarWidget QWidget {{ background: {t.surface}; }}
    """


def radio_qss(font_size=TEXT_MD):
    """`QRadioButton` / `QCheckBox` with a brand-filled indicator."""
    t = tokens()
    return f"""
        QRadioButton, QCheckBox {{
            background: transparent; color: {t.text};
            font-family: {FONT_STACK}; font-size: {font_size}px;
            spacing: 8px; padding: 2px 0;
        }}
        QRadioButton::indicator, QCheckBox::indicator {{
            width: 14px; height: 14px;
            border: 1px solid {t.border_strong}; background: {t.surface};
        }}
        /* 7px for a 14px box: exactly half, so it is a circle. Qt does NOT
           clamp an over-large radius, so 8px here drew a rounded square. */
        QRadioButton::indicator {{ border-radius: 7px; }}
        QCheckBox::indicator {{ border-radius: {RADIUS_SM}px; }}
        QRadioButton::indicator:hover, QCheckBox::indicator:hover {{
            border-color: {t.accent};
        }}
        /* A filled disc, keeping the 1px border. Setting a 4px border to fake
           an inner dot made the border swallow the radius and the checked
           indicator rendered as a green rounded square next to round
           unchecked ones. */
        QRadioButton::indicator:checked {{
            background: {t.accent}; border: 1px solid {t.accent};
        }}
        QCheckBox::indicator:checked {{
            background: {t.accent}; border: 1px solid {t.accent};
        }}
        QRadioButton:disabled, QCheckBox:disabled {{
            color: {t.text_disabled};
        }}
    """


def form_qss():
    """Every control a form uses, for setting once on the form container.

    One stylesheet on the parent rather than one per control: a form has a
    dozen inputs and styling them individually is how three of them get missed.
    All of these selectors are type-scoped, so unlike a bare ``background:``
    they cannot leak onto unrelated children — and a stylesheet reaches only
    the widget it is set on and that widget's descendants, so this stops at the
    form.

    The ``QLabel`` rule is what makes the field labels ("Data collection:",
    "Sort by:") part of the palette. Without it they fell through to QGIS's own
    text colour, which is a different grey from ``text_secondary`` in light
    themes and a much brighter one in dark. Labels that set their own
    stylesheet — a card's title, for instance — override this, because a
    widget's own rules beat an ancestor's.
    """
    t = tokens()
    labels = f"""
        QLabel {{
            background: transparent; border: none; color: {t.text};
            font-family: {FONT_STACK}; font-size: {TEXT_BASE}px;
        }}
    """
    return (input_qss() + select_qss() + spinbox_qss() + radio_qss()
            + labels)


def chip_qss(tone="accent", height=CHIP_HEIGHT, radius=None):
    """A small badge. A pill by default; pass ``radius`` for a softer square.

    ``tone`` is one of ``accent``, ``neutral``, ``success``, ``warning``,
    ``error``. ``height`` must match the chip's actual height, since the pill
    radius is derived from it — see :func:`pill_radius`.

    ``radius`` overrides that: the SaaS's project-card chips are Tailwind
    ``rounded`` (4px), not ``rounded-full``, and a pill reads as a different
    component next to them.
    """
    t = tokens()
    fills = {
        "accent":  (t.accent_soft, t.accent_text, t.accent_soft_border),
        "neutral": (t.surface_alt, t.text_secondary, t.border),
        "success": (t.success_soft, t.success, t.success),
        "warning": (t.warning_soft, t.warning, t.warning),
        "error":   (t.error_soft, t.error, t.error),
    }
    bg, fg, border = fills.get(tone, fills["accent"])
    return f"""
        QFrame {{
            background: {bg}; border: 1px solid {border};
            border-radius: {pill_radius(height) if radius is None else radius}px;
        }}
        QLabel {{
            background: transparent; border: none; color: {fg};
            font-family: {FONT_STACK}; font-size: {TEXT_SM}px;
            font-weight: {WEIGHT_MEDIUM};
        }}
    """


def heading_qss(size=TEXT_LG, display=False, muted=False):
    """A heading label. ``display=True`` opts into Space Grotesk."""
    t = tokens()
    return (
        f"background: transparent; border: none; "
        f"color: {t.text_secondary if muted else t.text}; "
        f"font-family: {DISPLAY_STACK if display else FONT_STACK}; "
        f"font-size: {size}px; font-weight: {WEIGHT_BOLD}; "
        f"letter-spacing: {'-0.3px' if display else '0px'};"
    )


def body_qss(size=TEXT_MD, secondary=False):
    """A body-copy label."""
    t = tokens()
    return (
        f"background: transparent; border: none; "
        f"color: {t.text_secondary if secondary else t.text}; "
        f"font-family: {FONT_STACK}; font-size: {size}px;"
    )


def field_label_qss(size=TEXT_MD):
    """The label naming a form control.

    Body copy at the body size but semibold, so the label and its control
    read as one unit. Separate from :func:`heading_qss` because a field label
    is not a heading: at bold it competed with the dialog's own title.
    """
    return body_qss(size) + f" font-weight: {WEIGHT_SEMIBOLD};"


def list_qss(radius=RADIUS):
    """`QListWidget` styled like the SaaS history well.

    The selected row sets a ``color`` as well as a ``background``, and has to.
    A stylesheet that gives ``::item:selected`` only a background leaves the
    TEXT to the palette's ``HighlightedText``, which the host application sets
    to white for its own blue highlight — so a selected row in the catalogue
    search drew white text on this pale green tint at 1.12:1, which is to say
    the row you had just clicked was the one you could not read. With
    ``accent_text`` it measures 4.74:1 in light and 6.37:1 in dark, both past
    the 4.5:1 floor, and it matches what :func:`menu_qss` and
    :func:`select_qss` already do for their own selected rows.
    """
    t = tokens()
    return f"""
        QListWidget {{
            background: transparent; border: none; border-radius: {radius}px;
            color: {t.text}; font-family: {FONT_STACK};
            font-size: {TEXT_BASE}px; outline: none; padding: 2px;
        }}
        QListWidget::item {{
            border-radius: {RADIUS_SM}px; margin: 2px 0; padding: 4px 7px;
        }}
        QListWidget::item:hover {{ background: {t.hover}; }}
        QListWidget::item:selected {{
            background: {t.accent_soft}; color: {t.accent_text};
        }}
    """ + scrollbar_qss("QListWidget")


def menu_qss():
    """`.dropdown-menu` + `.dropdown-item` — the composer's attach menu."""
    t = tokens()
    return f"""
        QMenu {{
            background: {t.surface}; border: 1px solid {t.border};
            border-radius: {RADIUS}px; padding: 5px;
            font-family: {FONT_STACK}; font-size: {TEXT_BASE}px;
            color: {t.text};
        }}
        QMenu::item {{
            padding: 7px 14px 7px 10px; border-radius: {RADIUS_SM}px;
            margin: 1px 2px;
        }}
        QMenu::item:selected {{
            background: {t.accent_soft}; color: {t.accent_text};
        }}
        QMenu::item:disabled {{ color: {t.text_disabled}; }}
        QMenu::separator {{
            height: 1px; background: {t.divider}; margin: 5px 8px;
        }}
        QMenu::icon {{ padding-left: 8px; }}
    """
