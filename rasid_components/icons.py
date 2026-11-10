"""Vector icon set, drawn with QPainter.

WHY NOT SHIP SVG/PNG FILES
==========================
The SaaS draws its icons with `lucide-react`: one 24x24 grid, 2px round-capped
strokes, `currentColor`. Three things make that hard to reproduce with assets in
a QGIS plugin, and all three are solved by drawing:

* **Colour.** Every icon has to work on a light surface AND a dark one, and the
  active ones have to flip to white on the brand green. A PNG per colour per
  icon is ~90 files to keep in sync; an SVG would need its `stroke` rewritten at
  load time anyway.
* **Sharpness.** Qt renders icons at `size * devicePixelRatio`. A 16px PNG on a
  200% display is upscaled and visibly soft, and QGIS users run high-DPI
  laptops constantly. Painting happens at the physical pixel size, so it is
  always crisp.
* **Weight.** No new binary files in the repo, nothing to add to
  `resources.qrc`, nothing for `build_plugin.py` to copy.

HOW IT WORKS
============
Each icon is a function that appends primitives to a painter already set up with
a transform mapping a 24x24 design grid onto the requested pixel size, and with
the pen/brush configured. So icon bodies read like the lucide source they are
ported from — coordinates on a 24-unit grid — and know nothing about size,
colour or DPI.

    btn.setIcon(icons.icon("send", color=t.accent))

Rounded caps and joins are set once in :func:`_painter`, which is what gives the
set its family resemblance to lucide.

ADDING AN ICON
==============
Add a ``def _draw_<name>(p)`` using the ``_line``/``_poly``/``_circle``/
``_round_rect``/``_arc`` helpers on the 24x24 grid, then register it in
``_ICONS``. Keep strokes on the grid's half-pixel centres (e.g. 4.5, not 4) for
odd-width shapes, or they land between device pixels and look blurry.
"""

import math

from qgis.PyQt.QtCore import QRectF, QPointF, QSize
from qgis.PyQt.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .compat import (
    QIcon_Active, QIcon_Disabled, QIcon_Normal, QIcon_Off, QIcon_Selected,
    QPainter_Antialiasing, Qt_NoBrush, Qt_RoundCap, Qt_RoundJoin,
    Qt_transparent,
)

GRID = 24.0  # the design grid every icon below is drawn on


# ── primitives, all in 24x24 design units ────────────────────────────────────
def _line(p, x1, y1, x2, y2):
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _poly(p, points, closed=False):
    path = QPainterPath(QPointF(*points[0]))
    for pt in points[1:]:
        path.lineTo(QPointF(*pt))
    if closed:
        path.closeSubpath()
    p.drawPath(path)


def _fill_poly(p, points):
    """A filled shape. Used only where lucide itself fills (the send glyph)."""
    path = QPainterPath(QPointF(*points[0]))
    for pt in points[1:]:
        path.lineTo(QPointF(*pt))
    path.closeSubpath()
    pen = p.pen()
    p.setBrush(QBrush(pen.color()))
    p.drawPath(path)
    p.setBrush(QBrush(Qt_NoBrush))


def _circle(p, cx, cy, r):
    p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))


def _fill_circle(p, cx, cy, r):
    pen = p.pen()
    p.setBrush(QBrush(pen.color()))
    p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    p.setBrush(QBrush(Qt_NoBrush))


def _round_rect(p, x, y, w, h, r):
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


def _arc(p, cx, cy, r, start_deg, span_deg):
    """Stroke an arc. Angles are degrees, counter-clockwise, 0 = 3 o'clock."""
    path = QPainterPath()
    rect = QRectF(cx - r, cy - r, r * 2, r * 2)
    path.arcMoveTo(rect, start_deg)
    path.arcTo(rect, start_deg, span_deg)
    p.drawPath(path)


# ══════════════════════════ the icons ════════════════════════════════════════
# Ported from lucide-react, the set the SaaS uses. Each name below matches the
# lucide icon it came from so the two stay comparable.

def _draw_plus(p):                                      # lucide: plus
    _line(p, 12, 5, 12, 19)
    _line(p, 5, 12, 19, 12)


def _draw_send(p):                                      # lucide: send
    # The classic paper plane: spine from the nose to the fold, then the body.
    _poly(p, [(21.5, 2.5), (10.5, 13.5)])
    _poly(p, [(21.5, 2.5), (14.5, 21.5), (10.5, 13.5), (2.5, 9.5)], True)


def _draw_message_plus(p):                               # message-square-plus
    _poly(p, [(21, 14.5), (21, 5), (3, 5), (3, 19.5), (7.5, 16), (21, 16)])
    _line(p, 12, 8, 12, 13)
    _line(p, 9.5, 10.5, 14.5, 10.5)


def _draw_message(p):                                    # message-square
    _poly(p, [(21, 16), (21, 5), (3, 5), (3, 19.5), (7.5, 16), (21, 16)], True)


def _draw_folder(p):                                     # folder
    _poly(p, [(3, 19.5), (3, 5), (9, 5), (11.2, 8), (21, 8), (21, 19.5)], True)


def _draw_box(p):                                        # box / GoBox
    _poly(p, [(12, 2.5), (21, 7.2), (21, 16.8), (12, 21.5),
              (3, 16.8), (3, 7.2)], True)
    _line(p, 3, 7.2, 12, 12)
    _line(p, 21, 7.2, 12, 12)
    _line(p, 12, 12, 12, 21.5)


def _draw_history(p):                                    # history
    # Clock face with the counter-clockwise "rewind" arrow lucide uses, so it
    # is not confused with a plain clock (which we never show).
    _arc(p, 12, 12, 8.5, 110, -320)
    _poly(p, [(3.5, 5.5), (3.5, 10.2), (8.2, 10.2)])
    _poly(p, [(12, 7.5), (12, 12), (15.8, 14)])


def _draw_settings(p):                                   # settings (gear)
    # A real toothed outline. An earlier version drew the teeth as eight radial
    # stubs around a small circle, which is cheaper but renders as a SUN, not a
    # gear — the stubs never close into a rim, so the eye reads rays.
    _circle(p, 12, 12, 3.4)
    r_tip, r_root, half = 10.1, 7.4, 22.5
    pts = []
    for i in range(8):
        a = i * 45.0
        for angle, radius in ((a - half, r_root), (a - 12.0, r_tip),
                              (a + 12.0, r_tip), (a + half, r_root)):
            rad = math.radians(angle)
            pts.append((12 + math.cos(rad) * radius,
                        12 + math.sin(rad) * radius))
    _poly(p, pts, True)


def _draw_sliders(p):                                    # sliders-horizontal
    _line(p, 3.5, 7, 20.5, 7)
    _line(p, 3.5, 12, 20.5, 12)
    _line(p, 3.5, 17, 20.5, 17)
    _fill_circle(p, 9, 7, 2.2)
    _fill_circle(p, 15.5, 12, 2.2)
    _fill_circle(p, 7, 17, 2.2)


def _draw_panel_right(p):                                # panel-right → "dock"
    _round_rect(p, 3, 4.5, 18, 15, 2.5)
    _line(p, 15, 4.5, 15, 19.5)


def _draw_undock(p):                              # arrow-up-right-from-square
    _poly(p, [(10.5, 4.5), (4.5, 4.5), (4.5, 19.5), (19.5, 19.5), (19.5, 13.5)])
    _poly(p, [(14, 4.5), (19.5, 4.5), (19.5, 10)])
    _line(p, 19.5, 4.5, 11.5, 12.5)


def _draw_x(p):                                          # x
    _line(p, 6, 6, 18, 18)
    _line(p, 18, 6, 6, 18)


def _draw_check(p):                                      # check
    _poly(p, [(4.5, 12.5), (9.5, 17.5), (19.5, 6.5)])


def _draw_trash(p):                                      # trash-2
    _line(p, 3.5, 6.5, 20.5, 6.5)
    _poly(p, [(5.5, 6.5), (6.4, 20), (17.6, 20), (18.5, 6.5)])
    _poly(p, [(9, 6.5), (9, 3.8), (15, 3.8), (15, 6.5)])
    _line(p, 10.2, 10.5, 10.2, 16.5)
    _line(p, 13.8, 10.5, 13.8, 16.5)


def _draw_upload(p):                                     # upload
    _line(p, 12, 16.5, 12, 4.5)
    _poly(p, [(6.5, 10), (12, 4.5), (17.5, 10)])
    _poly(p, [(3.5, 15), (3.5, 19.5), (20.5, 19.5), (20.5, 15)])


def _draw_download(p):                                   # download
    _line(p, 12, 4.5, 12, 16.5)
    _poly(p, [(6.5, 11), (12, 16.5), (17.5, 11)])
    _poly(p, [(3.5, 15), (3.5, 19.5), (20.5, 19.5), (20.5, 15)])


def _draw_layers(p):                                     # layers
    _poly(p, [(12, 2.5), (21.5, 8), (12, 13.5), (2.5, 8)], True)
    _poly(p, [(3.5, 12.4), (12, 17.3), (20.5, 12.4)])
    _poly(p, [(3.5, 16.4), (12, 21.3), (20.5, 16.4)])


def _draw_pencil(p):                                     # pencil
    _poly(p, [(2.8, 21.2), (4.6, 15.6), (16.4, 3.8), (20.2, 7.6),
              (8.4, 19.4)], True)
    _line(p, 14.2, 6, 18, 9.8)


def _draw_map_pin(p):                                    # map-pin
    _arc(p, 12, 9.6, 6.4, 200, -220)
    _poly(p, [(6.1, 12.4), (12, 21.5), (17.9, 12.4)])
    _circle(p, 12, 9.6, 2.5)


def _draw_paperclip(p):                                  # paperclip
    # One continuous line: down the outer left, round the bottom, up the right,
    # over the top, then back down inside — drawn in a frame rotated 45 degrees
    # so the clip leans the way lucide's does. Composing it from two stacked
    # arcs in page space (the previous attempt) produced a circled slash,
    # because the arcs met at the wrong tangents and the stem crossed them.
    p.save()
    p.translate(12, 12)
    p.rotate(-45)
    path = QPainterPath(QPointF(-4.8, -6.5))
    path.lineTo(-4.8, 4.0)
    # 180 degree turns as single cubics; control offset 4/3 * r is the standard
    # one-segment circle approximation and is well inside a 1px error here.
    path.cubicTo(QPointF(-4.8, 4.0 + 6.4), QPointF(4.8, 4.0 + 6.4),
                 QPointF(4.8, 4.0))
    path.lineTo(4.8, -7.0)
    path.cubicTo(QPointF(4.8, -7.0 - 2.4), QPointF(1.6, -7.0 - 2.4),
                 QPointF(1.6, -7.0))
    path.lineTo(1.6, 2.2)
    p.drawPath(path)
    p.restore()


def _draw_info(p):                                       # info
    _circle(p, 12, 12, 9)
    _line(p, 12, 11, 12, 16.5)
    _fill_circle(p, 12, 7.8, 0.95)


def _draw_library(p):                                    # library-big
    _round_rect(p, 3, 4, 5, 16, 1.5)
    _round_rect(p, 10.5, 4, 5, 16, 1.5)
    _poly(p, [(18.2, 4.6), (21.3, 5.4), (18.6, 19.8), (15.5, 19)], True)


def _draw_user(p):                                       # user
    _circle(p, 12, 8, 3.8)
    _arc(p, 12, 20.6, 7.2, 20, 140)


def _draw_log_out(p):                                    # log-out
    _poly(p, [(10, 4.5), (4.5, 4.5), (4.5, 19.5), (10, 19.5)])
    _poly(p, [(15.5, 8), (19.5, 12), (15.5, 16)])
    _line(p, 19.5, 12, 9.5, 12)


def _draw_chevron_left(p):                               # chevron-left
    _poly(p, [(15, 5), (8, 12), (15, 19)])


def _draw_chevron_right(p):                              # chevron-right
    _poly(p, [(9, 5), (16, 12), (9, 19)])


def _draw_chevron_down(p):                               # chevron-down
    _poly(p, [(5, 9), (12, 16), (19, 9)])


def _draw_chevron_up(p):                                 # chevron-up
    # The mirror of chevron-down about y=12.5, so a spin box's two arrows are
    # the same glyph and read as a pair.
    _poly(p, [(5, 16), (12, 9), (19, 16)])


def _draw_search(p):                                     # search
    _circle(p, 10.5, 10.5, 6.5)
    _line(p, 15.3, 15.3, 20.5, 20.5)


def _draw_undo(p):                                       # undo-2
    # An arrow curving back on itself, anticlockwise. "refresh" (rotate-cw)
    # was the nearest existing glyph and means "do it again", not "take the
    # last one back"; chevron-left, which this replaced at the call site,
    # reads as "go back a page".
    _arc(p, 12, 13, 7, 200, 160)
    _poly(p, [(11, 3), (5, 6.5), (11, 10)])


def _draw_refresh(p):                                    # refresh-cw
    _arc(p, 12, 12, 8, 60, 250)
    _poly(p, [(16.2, 2.6), (19.9, 6.3), (16.2, 9.0)])


def _draw_compass(p):                                    # compass → GoPilot
    _circle(p, 12, 12, 9)
    _fill_poly(p, [(16.2, 7.8), (13.6, 13.6), (7.8, 16.2), (10.4, 10.4)])


def _draw_sparkle(p):                                    # sparkles
    _poly(p, [(10, 3.5), (11.9, 8.1), (16.5, 10), (11.9, 11.9),
              (10, 16.5), (8.1, 11.9), (3.5, 10), (8.1, 8.1)], True)
    _line(p, 18, 4, 18, 9)
    _line(p, 15.5, 6.5, 20.5, 6.5)


def _star_points(outer=9.6, inner=4.2):
    """The ten points of a five-pointed star on the 24-unit grid.

    Computed rather than written out as twenty literals, so the two variants
    below cannot drift apart and the proportions stay adjustable.
    """
    points = []
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = math.radians(-90 + index * 36)
        points.append((12 + radius * math.cos(angle),
                       12 + radius * math.sin(angle)))
    return points


def _draw_star(p):                                       # star
    _poly(p, _star_points(), True)


def _draw_star_filled(p):                                # star, filled
    _fill_poly(p, _star_points())


def _draw_folder_open(p):                                # folder-open
    _poly(p, [(3, 18.5), (3, 5), (9, 5), (11.2, 8), (19, 8), (19, 10.5)])
    _poly(p, [(3, 18.5), (6.2, 10.5), (22, 10.5), (18.8, 18.5)], True)


def _draw_alert(p):                                      # alert-triangle
    _poly(p, [(12, 3.4), (22, 20.4), (2, 20.4)], True)
    _line(p, 12, 9.5, 12, 14.5)
    _fill_circle(p, 12, 17.4, 0.95)


_ICONS = {
    "plus": _draw_plus,
    "send": _draw_send,
    "new-chat": _draw_message_plus,
    "message": _draw_message,
    "projects": _draw_folder,
    "folder-open": _draw_folder_open,
    "gobox": _draw_box,
    "history": _draw_history,
    "settings": _draw_settings,
    "sliders": _draw_sliders,
    "dock": _draw_panel_right,
    "undock": _draw_undock,
    "close": _draw_x,
    "check": _draw_check,
    "trash": _draw_trash,
    "upload": _draw_upload,
    "download": _draw_download,
    "layers": _draw_layers,
    "draw": _draw_pencil,
    "map-pin": _draw_map_pin,
    "attach": _draw_paperclip,
    "info": _draw_info,
    "library": _draw_library,
    "user": _draw_user,
    "logout": _draw_log_out,
    "chevron-left": _draw_chevron_left,
    "chevron-right": _draw_chevron_right,
    "chevron-down": _draw_chevron_down,
    "chevron-up": _draw_chevron_up,
    "search": _draw_search,
    "refresh": _draw_refresh,
    "undo": _draw_undo,
    "gopilot": _draw_compass,
    "sparkle": _draw_sparkle,
    "alert": _draw_alert,
    "star": _draw_star,
    "star-filled": _draw_star_filled,
}


def available():
    """Every registered icon name. Handy when checking a name at a call site."""
    return sorted(_ICONS)


def pixmap(name, size=18, color="#000000", stroke=1.9, dpr=1.0):
    """Render ``name`` to a :class:`QPixmap`.

    ``stroke`` is in design-grid units (lucide's own default is 2), so the
    visual weight stays constant as ``size`` changes instead of thinning out.
    ``dpr`` is the device pixel ratio; the returned pixmap carries it so Qt
    draws it at ``size`` logical pixels while using every physical one.
    """
    draw = _ICONS.get(name)
    if draw is None:
        raise KeyError(
            f"Unknown icon {name!r}. Available: {', '.join(available())}"
        )

    physical = max(1, int(round(size * dpr)))
    pm = QPixmap(physical, physical)
    pm.fill(Qt_transparent)

    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter_Antialiasing, True)
        # Map the 24-unit design grid onto the pixmap's PHYSICAL pixels.
        #
        # The device pixel ratio is deliberately not set yet: a QPainter opened
        # on a pixmap that already carries a ratio pre-scales itself by it, so
        # this transform would be applied on top of that one and the glyph
        # would be drawn at `dpr` times the intended size — i.e. only its
        # top-left corner would land inside the pixmap. Tagging the ratio after
        # `end()` keeps the paint pass in physical pixels, which is the space
        # `physical / GRID` is expressed in.
        scale = physical / GRID
        p.scale(scale, scale)

        pen = QPen(QColor(color))
        pen.setWidthF(stroke)
        pen.setCapStyle(Qt_RoundCap)
        pen.setJoinStyle(Qt_RoundJoin)
        # Cosmetic would pin the stroke to 1 device pixel and ignore `stroke`.
        pen.setCosmetic(False)
        p.setPen(pen)
        p.setBrush(QBrush(Qt_NoBrush))

        draw(p)
    finally:
        p.end()
    # Now that nothing is painting into it, tag the ratio so Qt lays the pixmap
    # out at `size` logical pixels while using all `physical` ones.
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name, size=18, color="#000000", stroke=1.9, disabled_color=None,
         active_color=None, dpr=2.0):
    """Build a :class:`QIcon` for ``name``.

    ``dpr`` defaults to 2.0 rather than 1.0 so the glyph is sharp on high-DPI
    displays without the caller having to ask the widget for its ratio — Qt
    downsamples cleanly for 1x, while the reverse (upscaling a 1x pixmap) is
    what makes plugin icons look fuzzy on a retina screen.

    Pass ``disabled_color`` / ``active_color`` to register the matching QIcon
    modes, so a toolbutton dims and highlights without the caller swapping
    icons by hand.
    """
    qic = QIcon()
    qic.addPixmap(pixmap(name, size, color, stroke, dpr),
                  QIcon_Normal, QIcon_Off)
    if disabled_color:
        qic.addPixmap(pixmap(name, size, disabled_color, stroke, dpr),
                      QIcon_Disabled, QIcon_Off)
    if active_color:
        for mode in (QIcon_Active, QIcon_Selected):
            qic.addPixmap(pixmap(name, size, active_color, stroke, dpr),
                          mode, QIcon_Off)
    return qic


def icon_size(size):
    """``QSize(size, size)`` — saves importing QSize at every call site."""
    return QSize(size, size)


_URL_CACHE = {}


def stylesheet_url(name, size=10, color="#000000", stroke=1.9, dpr=2.0):
    """A ``file://``-style path for ``image: url(...)`` in a stylesheet.

    WHY A FILE AT ALL
    =================
    Qt stylesheets can reference an image but cannot draw one, and they have no
    ``transform``. So a chevron cannot be built out of CSS: the usual trick of
    rotating two borders 45 degrees has nothing to rotate it with, and what it
    actually renders is a right-angled "L" sitting in the corner of the
    control — which is exactly what the combo boxes showed.

    Writing the icon we already draw in code to a PNG and pointing the
    stylesheet at it keeps one definition of the glyph. The alternative,
    shipping arrow assets, would put a second copy of the chevron in the repo
    that nothing keeps in step with :func:`_draw_chevron_down`.

    Cached by (name, size, colour, stroke, dpr), so a theme switch gets its own
    file rather than overwriting the one the old stylesheet still references.
    The files live in the system temp directory and are rewritten on demand, so
    a cleaner removing them costs one repaint, not a crash.
    """
    import hashlib
    import os
    import tempfile

    key = (name, int(size), str(color), float(stroke), float(dpr))
    cached = _URL_CACHE.get(key)
    if cached is not None and os.path.exists(cached):
        return cached

    directory = os.path.join(tempfile.gettempdir(), "gopilot-icons")
    os.makedirs(directory, exist_ok=True)
    digest = hashlib.md5(repr(key).encode("utf-8")).hexdigest()[:12]
    path = os.path.join(directory, f"{name}-{digest}.png")
    if not os.path.exists(path):
        pixmap(name, size, color, stroke=stroke, dpr=dpr).save(path, "PNG")

    # Forward slashes: a Qt stylesheet url() treats a backslash as an escape,
    # so a Windows path silently fails to load and the control draws no arrow
    # at all.
    url = path.replace("\\", "/")
    _URL_CACHE[key] = url
    return url
