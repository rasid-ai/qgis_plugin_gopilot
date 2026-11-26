# -*- coding: utf-8 -*-
"""Show a raster result the way the website shows it: as styled tiles.

WHY THIS EXISTS
===============
There is no style document for a raster result, and there never was. The
colour is applied SERVER-SIDE while the tile pyramid is built, and the
website simply displays that finished pyramid — so the browser never needs
a style, and none is sent. ``sld`` is null for rasters, and ``symbology``
and ``style_config`` are declared in the web's schema but read by no line
of it.

The plugin, by contrast, downloads the raw GeoTIFF and hands it to QGIS,
which has no idea how it is meant to look and falls back to a grey stretch.
That is the whole of the "my NDVI is grey" problem: not a styling bug, a
different source. This module closes it by loading the same pyramid the
website does — QGIS reads tiles natively, so the result is the server's own
colouring, pixel for pixel, with nothing translated.

THREE THINGS THAT EACH PRODUCE AN INVISIBLE LAYER
=================================================
A tile layer that cannot fetch anything still loads, still reports valid,
and still appears in the tree. It just draws nothing, which looks exactly
like "the plugin ignored my raster". All three of these were measured
against the live pyramid rather than reasoned about:

1. **The URL must not be wholly percent-encoded.** ``quote(url, safe="")``
   produces ``url=https%3A%2F%2F…``, and the provider hands that back as a
   literal — every request then goes nowhere. :class:`QgsDataSourceUri`
   encodes only what needs it (the ``{}`` braces) and leaves ``://`` alone,
   so the URI is built with it rather than by hand.

2. **The rows are TMS.** gdal2tiles writes TMS unless asked for ``--xyz``,
   and this backend does not ask. Requesting XYZ rows returns 404 at every
   zoom level the pyramid has; requesting inverted rows returns a real PNG
   at every one. QGIS spells "inverted" as the ``{-y}`` placeholder — there
   is no ``type=tms``, which is not a valid provider URI at all.

   For a pyramid covering a small AoI the mismatch is not the vertical
   mirror the convention is famous for: the flipped row simply falls outside
   the data, so EVERY tile 404s and nothing draws.

3. **The zoom range comes from the pyramid.** ``tilemapresource.xml`` beside
   the tiles lists the levels that exist and the extent they cover. Claiming
   a deeper range than exists is the damaging direction — QGIS asks for a
   level that was never built and draws blank, whereas an understated range
   just means it scales the deepest real tiles and goes soft. The file also
   supplies the extent, which matters because ``bbox`` on the file record is
   frequently null.

PICTURE OR DATA
===============
A tile layer is an IMAGE. You cannot query a pixel value, run an analysis or
re-symbolise it. The GeoTIFF is the data and stays on offer; this is the
view. Both are worth having and they answer different questions, which is
why the result controls offer both rather than one replacing the other.

A NOTE ON THE TOKEN
===================
The pyramid is not public: ``/media/`` answers an anonymous request with
403, and accepts ``Authorization: Bearer <key>``. The key therefore has to
travel with every tile request, and only ever to our own hosts (the same
allowlist the downloads use). It is worth knowing that saving the QGIS
project persists the layer's URI, token included, into the ``.qgs`` file —
the website has no equivalent exposure because a browser uses a session
cookie. Nothing here can prevent that; it is recorded so it is a known
property rather than a surprise.
"""

import re
from urllib.parse import urlsplit

from .config import API_HOST, is_trusted_api_url
from .logger import debug_print

#: Zoom range to fall back on when ``tilemapresource.xml`` cannot be read.
#: Deliberately conservative: too LOW only costs sharpness (QGIS scales the
#: deepest tile it has), while too HIGH draws nothing at all once the user
#: zooms past the top of the pyramid.
ZOOM_MIN = 0
ZOOM_MAX_FALLBACK = 16

#: `tiles_situation` values that mean the pyramid is not usable yet. From
#: the web's own `isReadyForMap`: 'g' is gdal2tiles still running and 'gf'
#: that it failed.
NOT_READY = ("g", "gf")

#: How long to wait for the pyramid's metadata. A 1 KB file from the host
#: that just answered the chat, so this is already generous — and it is
#: fetched on the UI thread, so it is also the longest the window can sit
#: still. Losing it costs the zoom range and the framing, never the layer.
METADATA_TIMEOUT = 4.0


def tiles_ready(entry):
    """Is this file's pyramid built and usable?

    The same test the website applies before putting a raster on its map,
    so the plugin offers the styled view exactly when the website would
    show one — rather than offering a button that fetches 404s.
    """
    if not isinstance(entry, dict):
        return False
    if entry.get("visualization") is False:
        return False            # the model's own "download only" verdict
    if entry.get("processing_status") not in (None, "completed"):
        return False
    if (entry.get("tiles_situation") or "") in NOT_READY:
        return False
    return bool(entry.get("tiles_path"))


def origin_of(client):
    """The host this session is actually talking to.

    Taken from the LIVE client rather than from ``config.API_HOST``, which
    is a build-time default: a deployment can serve from somewhere else, and
    a tile URL built against the wrong origin simply 404s, so the layer
    loads, draws nothing, and leaves the user looking at their basemap.
    """
    for holder in (getattr(client, "gopilot", None), client):
        base = getattr(holder, "base_url", None)
        if not base:
            continue
        parts = urlsplit(str(base))
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return API_HOST


def tiles_base(entry, origin=None):
    """The directory the pyramid lives in, with a trailing slash, or None.

    Built the same way the website builds it: the origin followed by the
    file's ``tiles_path``. An absolute ``tiles_path`` is used as it stands —
    the field is relative on the deployments seen so far, but a URL is
    unambiguous and prepending an origin to one would corrupt it.
    """
    if not tiles_ready(entry):
        return None
    base = str(entry.get("tiles_path") or "")
    if not base.endswith("/"):
        base += "/"
    if urlsplit(base).scheme in ("http", "https"):
        return base
    if not base.startswith("/"):
        base = "/" + base
    return f"{(origin or API_HOST).rstrip('/')}{base}"


def tiles_template(entry, origin=None, tms=True):
    """The tile-URL template for this file's pyramid, or None.

    ``tms=True`` (the default, and what this backend produces) asks for rows
    counted from the bottom, which QGIS spells ``{-y}``. See the module
    docstring: with XYZ rows every tile of a small pyramid 404s.
    """
    base = tiles_base(entry, origin)
    if not base:
        return None
    return base + ("{z}/{x}/{-y}.png" if tms else "{z}/{x}/{y}.png")


def metadata_url(entry, origin=None):
    """Where ``tilemapresource.xml`` sits for this pyramid, or None."""
    base = tiles_base(entry, origin)
    return (base + "tilemapresource.xml") if base else None


def parse_metadata(text):
    """Pull the zoom range and extent out of ``tilemapresource.xml``.

    Returns ``{"zmin", "zmax", "bbox", "profile"}`` with whatever could be
    read; keys are absent rather than guessed.

    Read with a regex, not an XML parser. The document is a handful of lines
    in a fixed shape written by gdal2tiles, and ``xml.etree`` is explicitly
    documented as vulnerable to entity-expansion attacks — not worth
    inviting for four numbers.

    gdal2tiles declares ``<SRS>EPSG:3857</SRS>`` and then writes the bounding
    box in DEGREES. The numbers, not the declaration, are what this follows.
    """
    out = {}
    if not text:
        return out

    orders = [int(value) for value in re.findall(r'order="(\d+)"', text)]
    if orders:
        out["zmin"] = min(orders)
        out["zmax"] = max(orders)

    box = re.search(r'<BoundingBox\s+minx="([-\d.eE+]+)"\s+'
                    r'miny="([-\d.eE+]+)"\s+maxx="([-\d.eE+]+)"\s+'
                    r'maxy="([-\d.eE+]+)"', text)
    if box:
        try:
            out["bbox"] = [float(value) for value in box.groups()]
        except (TypeError, ValueError):
            pass

    profile = re.search(r'profile="(\w+)"', text)
    if profile:
        out["profile"] = profile.group(1)
    return out


def read_metadata(url, api_key=None, client=None):
    """Fetch and parse the pyramid's metadata. Returns {} on any failure.

    Never raises: the styled view is worth having with a fallback zoom range
    and no framing, so a metadata hiccup must not cost the user the layer.
    """
    if not url:
        return {}
    try:
        session = getattr(client, "session", None)
        headers = {}
        if api_key and is_trusted_api_url(url):
            headers["Authorization"] = f"Bearer {api_key}"
        if session is not None:
            response = session.get(url, headers=headers,
                                   timeout=METADATA_TIMEOUT)
        else:
            import requests
            response = requests.get(url, headers=headers,
                                    timeout=METADATA_TIMEOUT)
        if response.status_code != 200:
            debug_print(f"[GoPilot] The pyramid's metadata at {url} answered "
                        f"{response.status_code}; falling back to defaults")
            return {}
        return parse_metadata(response.text)
    except Exception as exc:
        debug_print(f"[GoPilot] Could not read the pyramid's metadata: {exc}")
        return {}


def tiles_uri(url, api_key=None, zmin=None, zmax=None):
    """A QGIS raster-provider URI for a tile set.

    Built with :class:`QgsDataSourceUri` rather than by string formatting.
    Hand-rolling it meant percent-encoding the whole template, which the
    provider then treats as a literal URL — the layer loads and every
    request goes nowhere. QGIS encodes only the braces and round-trips its
    own output; see the module docstring.

    The API key is attached as an ``Authorization`` header, and ONLY for our
    own hosts — the same allowlist the downloads use. A pyramid served from
    third-party storage must not receive the user's credentials.
    """
    from qgis.core import QgsDataSourceUri

    uri = QgsDataSourceUri()
    uri.setParam("type", "xyz")
    uri.setParam("zmin", str(ZOOM_MIN if zmin is None else zmin))
    uri.setParam("zmax", str(ZOOM_MAX_FALLBACK if zmax is None else zmax))
    if api_key and is_trusted_api_url(url):
        uri.setParam("http-header:Authorization", f"Bearer {api_key}")
    uri.setParam("url", url)
    return uri.encodedUri().data().decode()


def add_tile_layer(entry, layer_name, iface=None, api_key=None,
                   tms=True, client=None):
    """Add this file's styled pyramid to the project. Returns the layer.

    Raises RuntimeError if the pyramid is not ready or QGIS will not build
    the layer, so the caller can report it on the control that was clicked.
    """
    from qgis.core import QgsProject, QgsRasterLayer

    origin = origin_of(client) if client is not None else None
    url = tiles_template(entry, origin=origin, tms=tms)
    if not url:
        raise RuntimeError("This result has no styled tiles yet.")

    # The pyramid's own account of itself: which levels exist, and where.
    meta = read_metadata(metadata_url(entry, origin=origin),
                         api_key=api_key, client=client)
    if meta.get("profile") not in (None, "mercator"):
        debug_print(f"[GoPilot] The pyramid for {layer_name!r} is in the "
                    f"{meta['profile']!r} profile, not mercator; QGIS reads "
                    f"tiles as mercator, so it may be misplaced")

    uri = tiles_uri(url, api_key=api_key,
                    zmin=meta.get("zmin"), zmax=meta.get("zmax"))
    layer = QgsRasterLayer(uri, layer_name, "wms")
    if not layer.isValid():
        # Nearly always the provider: a tile layer needs QGIS's `wms`
        # provider library, which is present in the application but not in
        # a bare interpreter.
        raise RuntimeError(
            f"QGIS could not open the styled tiles: "
            f"{layer.error().message() or 'unknown error'}")

    from . import prefs
    project = QgsProject.instance()
    if prefs.get_bool(prefs.LAYER_GROUP_ENABLED):
        group_name = prefs.get_str(prefs.LAYER_GROUP_NAME)
        root = project.layerTreeRoot()
        group = root.findGroup(group_name) or root.insertGroup(0, group_name)
        project.addMapLayer(layer, False)
        # Newest on top, as everywhere else the plugin adds a layer.
        group.insertLayer(0, layer)
    else:
        project.addMapLayer(layer)

    _zoom(entry, meta, iface)
    debug_print(f"[GoPilot] Added the styled tiles for {layer_name!r}: {url} "
                f"(zoom {meta.get('zmin', ZOOM_MIN)}"
                f"–{meta.get('zmax', ZOOM_MAX_FALLBACK)})")
    return layer


def _zoom(entry, meta, iface):
    """Frame the result over the pyramid's own extent.

    A tile layer's extent is the whole world until something narrows it —
    the tile conventions carry no extent — so zooming to the layer itself
    would zoom to nothing useful.

    ``tilemapresource.xml`` is the first choice because it describes the
    thing being drawn; the file record's ``bbox`` is the fallback, and is
    often null. Both are in WGS84 degrees.
    """
    from . import prefs
    if not prefs.get_bool(prefs.ZOOM_TO_LAYER) or iface is None:
        return

    bbox = (meta or {}).get("bbox")
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        bbox = (entry or {}).get("bbox")
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        debug_print("[GoPilot] The styled tiles came with no extent, so the "
                    "canvas was left where it was")
        return

    try:
        from qgis.core import (QgsCoordinateReferenceSystem,
                               QgsCoordinateTransform, QgsProject,
                               QgsRectangle)
        canvas = iface.mapCanvas()
        rect = QgsRectangle(float(bbox[0]), float(bbox[1]),
                            float(bbox[2]), float(bbox[3]))
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"),
            canvas.mapSettings().destinationCrs(),
            QgsProject.instance())
        canvas.setExtent(transform.transformBoundingBox(rect))
        canvas.refresh()
    except Exception as exc:
        debug_print(f"[GoPilot] Could not frame the styled tiles: {exc}")
