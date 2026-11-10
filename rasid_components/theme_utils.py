"""Theme-aware colour utilities — compatibility facade over :mod:`design`.

WHAT THIS IS NOW
================
This module used to own the plugin's colours as a short list of hardcoded
greys (``#2c3e50``, ``#f7f8fa``, ``#333333``, …). Those greys were COOL, while
the RASID brand neutral is a warm taupe (``#5F575A``), so every panel read
slightly blue next to the SaaS. The palette now lives in :mod:`design`, ported
directly from the SaaS ``tailwind.config.js``.

Rather than rewrite nine pages at once, every function below is kept with its
original name and signature and simply forwards to the matching semantic token.
So the existing pages (processes, projects, solutions, wizard, login, feedback,
drawing help) re-tone to the brand palette without being touched, and new code
can use the richer token set in :mod:`design` directly.

WHICH ONE SHOULD I USE?
-----------------------
* **New widgets** — use :mod:`design`. It has surfaces, borders, chat, state
  and scrollbar tokens plus the component stylesheet recipes, and it is the
  file that stays in sync with the SaaS.
* **Existing widgets** — these helpers are fine. They are a stable facade, not
  deprecated-and-rotting, but they only expose a fraction of the tokens.

Mapping, for reference when porting a page over:

====================================  =====================
``theme_utils``                       ``design.tokens()``
====================================  =====================
``get_text_color()``                  ``.text``
``get_secondary_text_color()``        ``.text_secondary``
``get_card_bg()``                     ``.surface``
``get_card_border()``                 ``.border``
``get_sidebar_bg()``                  ``.surface_alt``
``get_sidebar_border()``              ``.border``
``get_hover_bg()``                    ``.hover``
``get_separator_color()``             ``.divider``
``BRAND_PRIMARY``                     ``.accent``
``BRAND_HOVER``                       ``.accent_hover``
``BRAND_DANGER``                      ``.error``
====================================  =====================

THE BRAND CONSTANTS ARE DYNAMIC
===============================
``BRAND_PRIMARY``, ``BRAND_HOVER`` and friends look like module constants but
are resolved per-theme through :pep:`562` module ``__getattr__``. They have to
be: ``#008B6A`` on a dark QGIS surface is a 3.0:1 contrast ratio, which fails
WCAG AA for the small text and icons the plugin paints on it, so dark mode uses
the brighter end of the same brand ramp.

This only works through ATTRIBUTE access::

    from . import theme_utils          # correct — resolves per call
    theme_utils.BRAND_PRIMARY

    from .theme_utils import BRAND_PRIMARY   # WRONG — freezes one theme's value

Every caller in the plugin uses the first form; keep it that way.
"""

from . import design

# Names that resolve through __getattr__ below instead of being bound here.
_DYNAMIC = {
    "BRAND_PRIMARY":     lambda t: t.accent,
    "BRAND_HOVER":       lambda t: t.accent_hover,
    "BRAND_PRESSED":     lambda t: t.accent_pressed,
    "BRAND_LIGHT":       lambda t: t.accent_soft,
    "BRAND_DANGER":      lambda t: t.error,
    "BRAND_DANGER_HOVER":      lambda t: t.error_soft,
    "BRAND_DANGER_HOVER_DARK": lambda t: t.error_soft,
    "BRAND_SUCCESS":     lambda t: t.success,
    "BRAND_WARNING":     lambda t: t.warning,
}


def __getattr__(name):
    """Resolve the brand constants against the live theme (see module docs)."""
    resolver = _DYNAMIC.get(name)
    if resolver is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return resolver(design.tokens())


def __dir__():
    return sorted(list(globals()) + list(_DYNAMIC))


def is_dark_theme():
    """True when QGIS is running a dark theme.

    Decided from the luminance of the palette's Window colour, not the theme
    name — users rename and ship their own themes.
    """
    return design.is_dark()


def get_text_color():
    """Primary text colour."""
    return design.tokens().text


def get_secondary_text_color():
    """Muted text — labels, descriptions, helper copy."""
    return design.tokens().text_secondary


def get_sidebar_bg():
    """Background for sidebars, headers and other recessed surfaces."""
    return design.tokens().surface_alt


def get_sidebar_border():
    """Border between a sidebar and the content beside it."""
    return design.tokens().border


def get_hover_bg():
    """Hover wash for interactive rows and icon buttons."""
    return design.tokens().hover


def get_separator_color():
    """Divider / horizontal-rule colour."""
    return design.tokens().divider


def get_card_bg():
    """Card, dialog and input background."""
    return design.tokens().surface


def get_card_border():
    """Card and input border."""
    return design.tokens().border


def get_danger_hover_bg():
    """Hover wash behind a destructive action."""
    return design.tokens().error_soft
