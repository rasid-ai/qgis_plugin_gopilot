"""The GoPilot mark and wordmark, theme-aware.

Small on purpose: these two assets are used in several unrelated places — the
top of the nav rail, the chat's empty state, the login panel, the QGIS toolbar
action — and each of them had started to grow its own copy of the "which file,
at what size, in which theme" logic.

THE MARK IS THE PRODUCT'S, NOT THE COMPANY'S
============================================
This used to load ``icon.png`` / ``icon_nobg.png``, which are the RASID
company marks. The panel is GoPilot, so it now carries the GoPilot mark —
``gopilot-mark.svg``, copied from the SaaS's own ``public/img`` so the two
clients cannot drift apart.

IT SHIPS TWICE
==============
``gopilot-mark.svg`` has dark guide arcs for light surfaces;
``gopilot-mark-reversed.svg`` has white ones for dark. Picking one for both
themes loses the guide arcs in the other — they are hairlines, so on the wrong
background they are either invisible or the only thing you see.
:func:`brand_mark` chooses by theme.

WHY SVG AND NOT A PAINTED GLYPH
===============================
Every other icon here is drawn in code (see :mod:`.icons`), deliberately. A
LOGO is the exception: it has to be exactly itself, and re-deriving a gradient
arc and a four-point arrow from the original by hand is a copy that can drift.
``QSvgRenderer`` was measured present on both QGIS 3.36/Qt5 and 4.0.1/Qt6
before relying on it, and there is still a painted fallback for any build that
lacks it.
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPainter, QPixmap

from . import design, icons
from .compat import Qt_KeepAspectRatio, Qt_SmoothTransformation

try:
    from qgis.PyQt.QtSvg import QSvgRenderer
except ImportError:         # pragma: no cover - no QtSvg in this build
    QSvgRenderer = None

WORDMARK = "GoPilot"
WORDMARK_SUFFIX = "by RASID"

MARK_LIGHT = "gopilot-mark.svg"
MARK_DARK = "gopilot-mark-reversed.svg"

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Rendered marks, keyed by (filename, size, ratio). Re-rasterising an SVG on
# every theme refresh and every rail rebuild is wasted work for an image that
# cannot change between calls.
_CACHE = {}


def mark_path(dark=False):
    """Absolute path to the mark for this theme, whether or not it exists."""
    return os.path.join(_PLUGIN_ROOT, MARK_DARK if dark else MARK_LIGHT)


def brand_mark(tokens, size=design.RAIL_LOGO, ratio=2.0):
    """The GoPilot mark as a :class:`QPixmap`, suited to ``tokens``' theme.

    ``ratio`` oversamples for high-DPI screens the same way
    :func:`icons.pixmap` does: the pixmap is rendered at ``size * ratio`` and
    then tagged, so Qt draws it at ``size`` logical pixels without softening
    it.
    """
    dark = bool(getattr(tokens, "dark", False))
    key = (dark, int(size), float(ratio))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = _render_svg(mark_path(dark), size, ratio)
    if pixmap is None:
        # No QtSvg, or an unreadable file: fall back to the painted compass
        # rather than showing nothing where the brand should be.
        pixmap = icons.pixmap("gopilot", size - 2, tokens.accent, dpr=ratio)
    _CACHE[key] = pixmap
    return pixmap


def _render_svg(path, size, ratio):
    """Rasterise ``path`` to a ``size``-logical-pixel square, or None."""
    if QSvgRenderer is None or not os.path.exists(path):
        return None
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        return None

    extent = max(1, int(round(size * ratio)))
    pixmap = QPixmap(extent, extent)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    # AFTER end(), never before: a painter on a pixmap that already carries a
    # device pixel ratio scales its own coordinates by it and the drawing is
    # clipped to a corner. Same trap as icons.pixmap.
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def mark_pixmap_for_file(size=256):
    """The mark at an arbitrary size, light variant, for files and dialogs.

    Used where a plain image is wanted rather than a theme-aware one — the
    login panel's hero, for instance, which has its own fixed dark background
    and so always wants the reversed mark.
    """
    pixmap = _render_svg(mark_path(dark=True), size, 1.0)
    if pixmap is not None:
        return pixmap
    legacy = os.path.join(_PLUGIN_ROOT, "icon_nobg.png")
    if os.path.exists(legacy):
        candidate = QPixmap(legacy)
        if not candidate.isNull():
            return candidate.scaled(size, size, Qt_KeepAspectRatio,
                                    Qt_SmoothTransformation)
    return QPixmap()


def invalidate():
    """Drop the rendered-mark cache. Called when the theme changes."""
    _CACHE.clear()


def wordmark_html(tokens, muted_suffix=True):
    """"GoPilot by RASID" as rich text, with the suffix de-emphasised.

    Two weights and two colours in one line, which a single ``QLabel``
    stylesheet cannot express — hence rich text rather than plain.
    """
    suffix_color = tokens.text_muted if muted_suffix else tokens.text_secondary
    return (
        f'<span style="color:{tokens.text}; font-weight:700;">'
        f'{WORDMARK}</span>'
        f'<span style="color:{suffix_color}; font-weight:500;">'
        f' {WORDMARK_SUFFIX}</span>'
    )


def wordmark_qss(size=design.TEXT_DISPLAY):
    """Stylesheet for a label holding :func:`wordmark_html`."""
    return (
        f"background:transparent; border:none; "
        f"font-family:{design.DISPLAY_STACK}; font-size:{size}px; "
        f"letter-spacing:-0.3px;"
    )
