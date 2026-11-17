# -*- coding: utf-8 -*-
"""Apply a server-supplied SLD to a layer that has just been loaded.

WHAT AN SLD IS DOING HERE
=========================
Every result file the API returns may carry an ``sld`` field: a Styled Layer
Descriptor document, as XML, describing how the layer should be drawn. It is
what makes a result arrive looking the way it looks on the website rather
than in QGIS's default random colour.

WHY THIS IS SHORT
=================
QGIS reads SLD natively — ``QgsVectorLayer.loadSldStyle`` is the whole
mechanism. The website has to work much harder: Mapbox GL has no idea what
an SLD is, so ``sld_to_mapbox.js`` parses the XML and rebuilds each rule as
Mapbox paint properties. Nothing like that is needed here, and writing one
would be strictly worse than the reader QGIS already ships.

VECTOR ONLY, AND THAT IS NOT A SHORTCUT
=======================================
``loadSldStyle`` refuses raster layers outright — measured on QGIS 3.36.3
and 4.0.1, with a properly georeferenced raster and a real
``RasterSymbolizer``/``ColorMap``, it answers::

    Loading style file ... failed because:
    Layer type 1 not supported

So there is no raster path to write. There is also nothing to style: the
website never sends a raster through its SLD converter either (which handles
only Point, Line, Polygon and Text symbolizers), because raster results are
served to it as pre-rendered TMS tiles with the colouring already baked in.
SLD is a vector concern in this product, which is exactly where QGIS
supports it.
"""

import os
import tempfile

from .logger import debug_print


def is_sld(text):
    """Does ``text`` look like an SLD document?

    Checked before writing anything to disk, so a null, a stray empty string
    or a server that starts sending JSON here cannot leave temp files behind
    or produce a confusing "style failed" line in the log.
    """
    if not text or not isinstance(text, str):
        return False
    head = text.lstrip()[:400].lower()
    return "styledlayerdescriptor" in head or "<sld:" in head


def apply_sld(layer, sld_xml):
    """Style ``layer`` from an SLD document. Returns True if it took.

    Never raises and never fails the load that called it: a layer the user
    can see in the wrong colours is worth having, and an unstyled layer is a
    far better outcome than an error where their result should be.

    ``loadSldStyle`` wants a FILE, not a string, so the document goes to a
    temp file first — and the temp file is removed afterwards, because this
    runs once per downloaded layer and a QGIS session can be open for days.
    """
    if layer is None or not is_sld(sld_xml):
        return False

    # Rasters are refused by QGIS itself; say so once, quietly, rather than
    # letting it look like the style was malformed.
    if _is_raster(layer):
        debug_print("[GoPilot] Skipping SLD: QGIS does not apply SLD to "
                    "raster layers (the website styles rasters server-side)")
        return False

    handle, path = tempfile.mkstemp(prefix="gopilot_style_", suffix=".sld")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(sld_xml)
    except OSError as exc:
        debug_print(f"[GoPilot] Could not write the SLD: {exc}")
        try:
            os.close(handle)
        except OSError:
            pass
        return False

    # Kept so an SLD that would render nothing can be undone. Cloned before
    # loading, because loadSldStyle replaces the renderer in place.
    previous = None
    try:
        if layer.renderer() is not None:
            previous = layer.renderer().clone()
    except Exception:
        previous = None

    try:
        # NOTE THE ORDER: (errorMessage, ok). It reads backwards from every
        # other QGIS call nearby, and taking it as (ok, message) silently
        # inverts the result — a successful style returns an empty string,
        # which is falsy.
        message, ok = layer.loadSldStyle(path)
        if not ok:
            debug_print(f"[GoPilot] SLD rejected: {message}")
            return False

        # A STYLE THAT DRAWS NOTHING IS WORSE THAN NO STYLE.
        #
        # An SLD's rules can carry attribute filters, and QGIS turns those
        # into a rule-based renderer. A rule-based renderer with no matching
        # rule and no catch-all draws NO features at all — while still
        # listing its classes in the legend. The result is a layer that
        # loads, ticks on in the tree, shows a legend, and puts nothing on
        # the map, which is impossible to tell from a broken download.
        #
        # Measured: an SLD whose rules name a field the data does not have,
        # or values that do not occur, renders 0 of 3 features. That happens
        # whenever the style and the data disagree about the attribute — a
        # style keyed on a place name against a layer carrying `area_m2` and
        # `size_class`, say.
        #
        # So the style is only kept if it would actually draw something. The
        # user asked for this layer; seeing it in the wrong colours is a
        # cosmetic problem, not seeing it at all is not.
        if not _renders_any_feature(layer):
            debug_print(
                "[GoPilot] SLD discarded: none of its rules match this "
                "layer's attributes, so it would render nothing. Falling "
                "back to QGIS's own styling so the data stays visible.")
            if previous is not None:
                try:
                    layer.setRenderer(previous)
                except Exception:
                    pass
            return False

        try:
            layer.triggerRepaint()
        except Exception:
            pass        # the style is applied; a repaint is a nicety
        return True
    except Exception as exc:
        debug_print(f"[GoPilot] SLD failed: {type(exc).__name__}: {exc}")
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


#: How many features to test before giving the style the benefit of the
#: doubt. A result layer is usually small; this exists so a style check can
#: never walk a million-row layer on the UI thread.
MATCH_SAMPLE = 500


def _renders_any_feature(layer):
    """Would this layer's current renderer draw at least one feature?

    Only rule-based renderers can draw nothing — a single-symbol renderer
    always draws, and QGIS builds one of those from an SLD with no filters.
    So anything else is taken at its word.

    The rule FILTERS are evaluated rather than asking the renderer for a
    symbol: ``QgsRuleBasedRenderer.symbolForFeature`` is documented to
    always return null, and using it reported "nothing renders" even for a
    style that works perfectly.
    """
    try:
        from qgis.core import (QgsExpression, QgsExpressionContext,
                               QgsExpressionContextUtils, QgsFeatureRequest)
    except ImportError:             # pragma: no cover
        return True

    renderer = layer.renderer()
    root = getattr(renderer, "rootRule", None)
    if root is None:
        return True                 # not rule-based; it draws
    try:
        rules = root().children()
    except Exception:
        return True
    if not rules:
        return True

    # A rule with no filter catches everything, so the style is fine.
    filters = []
    for rule in rules:
        try:
            text = rule.filterExpression()
        except Exception:
            return True
        if not text:
            return True
        filters.append(text)

    context = QgsExpressionContext()
    try:
        context.appendScope(QgsExpressionContextUtils.layerScope(layer))
    except Exception:
        pass

    expressions = [QgsExpression(text) for text in filters]
    request = QgsFeatureRequest().setLimit(MATCH_SAMPLE)
    seen = 0
    try:
        for feature in layer.getFeatures(request):
            seen += 1
            context.setFeature(feature)
            for expression in expressions:
                if expression.evaluate(context):
                    return True
    except Exception:
        return True                 # cannot tell; do not discard the style

    # No feature at all is not a failing style, it is an empty layer.
    return seen == 0


def _is_raster(layer):
    """True for a raster layer, without importing QGIS at module scope."""
    try:
        from qgis.core import QgsRasterLayer
        return isinstance(layer, QgsRasterLayer)
    except ImportError:         # pragma: no cover - QGIS is always present
        return False
