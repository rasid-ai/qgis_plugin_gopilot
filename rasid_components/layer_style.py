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

RASTERS ARE TRANSLATED BY HAND
==============================
``loadSldStyle`` refuses raster layers outright — measured on QGIS 3.36.3
and 4.0.1, with a properly georeferenced raster and a real
``RasterSymbolizer``/``ColorMap``, it answers::

    Loading style file ... failed because:
    Layer type 1 not supported

That is a flat refusal by layer type, not a complaint about the document, so
there is nothing to fix at the SLD end. The colour map therefore has to be
read out of the XML here and rebuilt as a QGIS renderer —
``QgsSingleBandPseudoColorRenderer`` over a ``QgsColorRampShader``. Only the
part of SLD that a raster actually uses is translated; see
:func:`_raster_renderer` for exactly what, and what is ignored.

The website does not do this, which is why it was not obvious that it was
needed: there, raster results are pre-rendered TMS tiles with the colouring
already baked into the images, so the browser never sees a raster SLD. The
plugin downloads the raw GeoTIFF instead, so without this it shows QGIS's
default grey stretch where the website shows a colour ramp.
"""

import os
import tempfile
import xml.etree.ElementTree as ElementTree

from .logger import debug_print

#: SLD namespaces vary ("sld:" prefixed, default-namespaced, or bare), so
#: elements are matched on LOCAL name throughout, the same way the website's
#: own converter does it.
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _find_all(node, name):
    """Every descendant whose local name is ``name``, document order."""
    return [child for child in node.iter() if _local(child.tag) == name]


def _first(node, name):
    found = _find_all(node, name)
    return found[0] if found else None


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
    if layer is None:
        return False

    # Said out loud, because "no style arrived" and "the style was rejected"
    # look identical on the map — an unstyled layer — and the difference
    # decides whether to look at this plugin or at the payload.
    if not sld_xml:
        debug_print(f"[GoPilot] No SLD was sent for {layer.name()!r}; "
                    f"QGIS will render it with its own defaults")
        return False
    if not is_sld(sld_xml):
        debug_print(f"[GoPilot] The style sent for {layer.name()!r} is not "
                    f"an SLD document: {str(sld_xml)[:120]!r}")
        return False

    # QGIS will not take an SLD on a raster, so the colour map is read here
    # and rebuilt as a renderer instead.
    if _is_raster(layer):
        return _apply_raster_sld(layer, sld_xml)

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


def _apply_raster_sld(layer, sld_xml):
    """Rebuild a ``RasterSymbolizer``'s colour map as a QGIS renderer."""
    try:
        root = ElementTree.fromstring(sld_xml)
    except ElementTree.ParseError as exc:
        debug_print(f"[GoPilot] Raster SLD will not parse: {exc}")
        return False

    symbolizer = _first(root, "RasterSymbolizer")
    if symbolizer is None:
        # A vector style attached to a raster result. Nothing to do, and
        # nothing wrong either.
        debug_print("[GoPilot] Raster SLD has no RasterSymbolizer; "
                    "leaving QGIS's own rendering alone")
        return False

    renderer = _raster_renderer(layer, symbolizer)
    if renderer is None:
        return False

    opacity = _opacity(symbolizer)
    if opacity is not None:
        try:
            renderer.setOpacity(opacity)
        except Exception:
            pass

    try:
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception as exc:
        debug_print(f"[GoPilot] Could not set the raster renderer: {exc}")
        return False
    return True


def _raster_renderer(layer, symbolizer):
    """A pseudocolour renderer from the symbolizer's ``ColorMap``, or None.

    WHAT IS TRANSLATED
        ``ColorMap`` and its ``ColorMapEntry`` list — the colour, the
        quantity, and per-entry opacity — plus the map's ``type``, which
        chooses how QGIS interpolates:

        ============  ===========================
        SLD type      QGIS ramp
        ============  ===========================
        ``ramp``      Interpolated (the default)
        ``intervals`` Discrete
        ``values``    Exact
        ============  ===========================

        A band from ``ChannelSelection/GrayChannel/SourceChannelName`` is
        honoured if present.

    WHAT IS NOT
        ``ContrastEnhancement``, ``ShadedRelief``, ``OverlapBehavior`` and
        RGB ``ChannelSelection``. A raster that needs those is one QGIS
        already renders sensibly by itself, and a half-done translation
        would be worse than none.
    """
    try:
        from qgis.core import (QgsColorRampShader, QgsRasterShader,
                               QgsSingleBandPseudoColorRenderer)
        from qgis.PyQt.QtGui import QColor
    except ImportError:             # pragma: no cover
        return None

    colour_map = _first(symbolizer, "ColorMap")
    if colour_map is None:
        debug_print("[GoPilot] Raster SLD has no ColorMap to translate")
        return None

    stops = []
    for entry in _find_all(colour_map, "ColorMapEntry"):
        colour = QColor(entry.get("color") or "")
        if not colour.isValid():
            continue
        try:
            quantity = float(entry.get("quantity"))
        except (TypeError, ValueError):
            continue
        # Per-entry opacity is part of the entry's colour in QGIS, which has
        # no separate alpha on a ramp stop.
        try:
            alpha = entry.get("opacity")
            if alpha is not None:
                colour.setAlphaF(max(0.0, min(1.0, float(alpha))))
        except (TypeError, ValueError):
            pass
        stops.append((quantity, colour, entry.get("label") or
                      _tidy_number(quantity)))

    if not stops:
        debug_print("[GoPilot] Raster SLD ColorMap has no usable entries")
        return None

    band = _band(layer, symbolizer)
    if band is None:
        return None

    stops.sort(key=lambda stop: stop[0])
    shader_function = QgsColorRampShader()
    shader_function.setColorRampType(_ramp_type(colour_map))
    item = QgsColorRampShader.ColorRampItem
    shader_function.setColorRampItemList(
        [item(value, colour, label) for value, colour, label in stops])

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_function)
    return QgsSingleBandPseudoColorRenderer(
        layer.dataProvider(), band, shader)


def _band(layer, symbolizer):
    """Which band the colour map applies to, or None to leave the layer be.

    A ColorMap colours ONE band. Applying one to band 1 of a three-band
    raster would replace a correct RGB rendering with a single-channel ramp
    — so unless the SLD names a band, a multi-band raster is left alone.
    That case is real: a result can arrive as an already-classified RGB
    GeoTIFF that QGIS renders correctly on its own.
    """
    named = _first(symbolizer, "SourceChannelName")
    if named is not None and (named.text or "").strip():
        try:
            band = int(float(named.text.strip()))
        except (TypeError, ValueError):
            band = None
        if band and 1 <= band <= layer.bandCount():
            return band
        debug_print(f"[GoPilot] Raster SLD names band {named.text!r}, which "
                    f"this raster does not have")
        return None

    if layer.bandCount() == 1:
        return 1

    debug_print(
        f"[GoPilot] Raster SLD has a ColorMap but no band, and this raster "
        f"has {layer.bandCount()} bands — leaving QGIS's own rendering "
        f"alone rather than flattening it to one channel")
    return None


def _ramp_type(colour_map):
    """SLD's ColorMap ``type`` as a QGIS colour-ramp type.

    Looked up by NAME rather than by the integer: on QGIS 4 these are
    scoped ``ShaderInterpolationMethod`` members and on 3.36 they are plain
    ints, and only the attribute name is the same on both.
    """
    from qgis.core import QgsColorRampShader
    wanted = {"ramp": "Interpolated",
              "intervals": "Discrete",
              "values": "Exact"}.get(
                  (colour_map.get("type") or "ramp").strip().lower(),
                  "Interpolated")
    kind = getattr(QgsColorRampShader, wanted, None)
    if kind is None:
        kind = getattr(QgsColorRampShader.Type, wanted)
    return kind


def _opacity(symbolizer):
    node = _first(symbolizer, "Opacity")
    if node is None or not (node.text or "").strip():
        return None
    try:
        return max(0.0, min(1.0, float(node.text.strip())))
    except (TypeError, ValueError):
        return None


def _tidy_number(value):
    """``0.35`` not ``0.35000000000000003``, for the legend label."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


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
