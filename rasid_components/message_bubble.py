"""One chat message, and everything needed to render it.

WHY A QLabel AND NOT A WEB VIEW
===============================
A message body is a word-wrapped ``QLabel`` showing Qt rich text, because:

* ``QWebEngineView`` would give real CSS, but it is frequently ABSENT from QGIS
  installs (notably Windows OSGeo4W), so importing it crashes those users
  outright. ``QLabel`` rich text renders everywhere QGIS runs.
* ``QLabel`` sizes itself to its content through ``heightForWidth()``. A
  ``QTextBrowser`` does not, which is why the earlier version of this file
  carried a whole apparatus of ``documentSizeChanged`` handlers, margin
  fudging, scrollbar suppression and ``wheelEvent`` overrides to chase the
  right height. None of that is needed here.

The cost is that Qt rich text applies no external stylesheet to inner HTML
tags, so code blocks and tables have to be styled with INLINE attributes —
hence :func:`_style_code_blocks` and :func:`_style_tables` rewriting the
markdown library's output before it reaches the label.

RESULT FILES
============
GoPilot answers with links. A link to a raster or vector is not useful as blue
text in a chat panel, so links are stripped out of the prose and re-rendered at
the bottom of the bubble as controls: images become an inline preview with a
Save button, everything else becomes one "Add to QGIS" button that downloads
the file and inserts it as a layer.
"""

import os
import re
import shutil
from functools import partial

from qgis.PyQt.QtCore import QDateTime, QSize, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from xml.etree import ElementTree

import markdown

from . import design, icons, prefs
from .config import is_trusted_api_url
from .widgets import (
    IconButton,
    clear_layout, paint_transparent, styled_scroll_area,
)
from .chat_threads import DownloadLayerThread
from .compat import (
    QFrame_StyledPanel, QMessageBox_Ok, QMessageBox_Warning, Qt_AlignCenter, Qt_PointingHandCursor, Qt_RichText,
    Qt_SmoothTransformation, exec_dialog,
    Qt_AlignTop,
    Qt_ScrollBarAlwaysOff,
)
from .logger import debug_print

# Extensions GoPilot can hand back and QGIS can open.
_LAYER_EXTENSIONS = "tif|tiff|png|jpg|jpeg|shp|geojson|gpkg|zip"
_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg")
_RASTER_EXTENSIONS = ("tif", "tiff", "png", "jpg", "jpeg")

# HttpStatusCodeAttribute moved under QNetworkRequest.Attribute in PyQt6.
_HTTP_STATUS_ATTR = getattr(
    QNetworkRequest, "HttpStatusCodeAttribute",
    getattr(getattr(QNetworkRequest, "Attribute", None),
            "HttpStatusCodeAttribute", None),
)

# Qt rich text understands a small subset of text interaction flags; resolve
# them defensively because they are nested differently across bindings.
try:
    from qgis.PyQt.QtCore import Qt as _Qt
    _SELECTABLE = _Qt.TextInteractionFlag.TextSelectableByMouse
    _LINKS = _Qt.TextInteractionFlag.LinksAccessibleByMouse
except AttributeError:  # pragma: no cover - very old bindings
    _SELECTABLE = _LINKS = None


# ── layer loading ────────────────────────────────────────────────────────────
def add_layer_to_qgis(filepath, layer_name, iface=None):
    """Add ``filepath`` to the current project, honouring the user's settings.

    Grouping and zoom are preferences rather than hardcoded behaviour because
    this writes into the user's own QGIS document: some people want every
    result corralled under one group node, others want layers to land where
    they land. See :mod:`prefs`.
    """
    from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

    extension = filepath.rsplit(".", 1)[-1].lower()
    if extension in _RASTER_EXTENSIONS:
        layer = QgsRasterLayer(filepath, layer_name)
    else:
        layer = QgsVectorLayer(filepath, layer_name, "ogr")

    if not layer.isValid():
        raise RuntimeError(f"QGIS could not open {os.path.basename(filepath)}")

    project = QgsProject.instance()

    if prefs.get_bool(prefs.LAYER_GROUP_ENABLED):
        group_name = prefs.get_str(prefs.LAYER_GROUP_NAME)
        root = project.layerTreeRoot()
        group = root.findGroup(group_name) or root.insertGroup(0, group_name)
        # addMapLayer(layer, False) keeps it out of the tree root so the group
        # can own it; adding it twice would show the layer in both places.
        project.addMapLayer(layer, False)
        group.addLayer(layer)
    else:
        project.addMapLayer(layer)

    if prefs.get_bool(prefs.ZOOM_TO_LAYER) and iface is not None:
        try:
            canvas = iface.mapCanvas()
            extent = layer.extent()
            if not extent.isEmpty():
                # The layer's extent is in ITS crs; the canvas may be in
                # another, so go through the canvas's own layer-aware zoom.
                canvas.setExtent(
                    canvas.mapSettings().layerExtentToOutputExtent(
                        layer, extent)
                )
                canvas.refresh()
        except Exception as exc:
            # Zoom is a convenience; never let it fail the load itself.
            debug_print(f"[GoPilot] Could not zoom to layer: {exc}")

    return layer


def extract_file_urls(text):
    """Find downloadable files in a message.

    Returns ``[(url, filename, extension, is_image), …]``.

    Handles the two shapes the backend emits: a markdown link whose URL has no
    extension at all (presigned S3 links often do not) but whose LABEL names
    the file, and a bare URL that ends in a recognised extension.
    """
    results = []

    # Markdown IMAGE syntax first: `![alt](url)`. The `!` says it is an image,
    # so the extension does not have to — and for GoPilot's own images it
    # usually cannot, because the backend rewrites image links to
    # `/api/llm/files/<id>/download/`, which ends in no extension at all.
    #
    # Without this those fell through to the body as a literal `<img>`, and Qt
    # rich text cannot fetch an http resource: measured, the image run came
    # back 0x0 and drew Qt's 16px broken-image stub. Routed here instead, the
    # URL goes through the same preview-and-Save control as every other result
    # image, which downloads it with the authenticated session.
    image_pattern = r'!\[([^\]]*)\]\(<?((?:https?://|/)[^\s\)>]+)>?\)'
    for alt, url in re.findall(image_pattern, text, re.IGNORECASE):
        url_filename = url.split('/')[-1].split('?')[0]
        extension = (url_filename.rsplit('.', 1)[-1].lower()
                     if '.' in url_filename else '')
        if extension in _IMAGE_EXTENSIONS:
            filename = url_filename
        else:
            # Name it from the alt text so the Save dialog offers something
            # meaningful; "download" is not a filename anyone wants.
            stem = re.sub(r'[^\w\-. ]+', '', alt).strip() or 'image'
            filename = f"{stem}.png"
            extension = 'png'
        results.append((url, filename, extension, True))

    markdown_pattern = r'\[([^\]]+)\]\(<?((?:https?://|/)[^\s\)>]+)>?\)'
    for label, url in re.findall(markdown_pattern, text, re.IGNORECASE):
        # `![alt](url)` contains `[alt](url)`, so an image already collected
        # above matches here as well and was being added a second time — two
        # identical previews under one answer.
        if any(existing == url for existing, _, _, _ in results):
            continue
        url_filename = url.split('/')[-1].split('?')[0]
        if re.search(rf'\.(?:{_LAYER_EXTENSIONS})$', url_filename,
                     re.IGNORECASE):
            filename = url_filename
        else:
            label_match = re.search(
                rf'([^/\\]+\.(?:{_LAYER_EXTENSIONS}))$', label.strip(),
                re.IGNORECASE)
            filename = label_match.group(1) if label_match else None
        if not filename:
            continue
        extension = filename.rsplit('.', 1)[-1].lower()
        results.append((url, filename, extension,
                        extension in _IMAGE_EXTENSIONS))

    plain_pattern = (rf'(?<!\]\()https?://[^\s<>"]+'
                     rf'\.(?:{_LAYER_EXTENSIONS})(?:\?[^\s<>"]*)?')
    for url in re.findall(plain_pattern, text, re.IGNORECASE):
        if any(existing == url for existing, _, _, _ in results):
            continue
        filename = url.split('/')[-1].split('?')[0]
        extension = filename.rsplit('.', 1)[-1].lower()
        results.append((url, filename, extension,
                        extension in _IMAGE_EXTENSIONS))

    return results


# ── markdown → Qt rich text ──────────────────────────────────────────────────
_PRE_BLOCK = re.compile(r"<pre>\s*<code[^>]*>(.*?)</code>\s*</pre>",
                        re.DOTALL | re.IGNORECASE)


def _style_code_blocks(html_text):
    """Re-render fenced code blocks so they WRAP inside the bubble.

    Qt rich text does not implement ``white-space: pre-wrap``. A ``<pre>`` is
    laid out as one unbreakable line per source line, so a line of Python just
    runs off the right edge of the bubble and is clipped by it — there is no
    horizontal scrollbar in a QLabel to reach the rest.

    So a code block becomes a one-cell table instead:

    * A table cell wraps its contents like any other block, so a long line
      breaks at a space rather than disappearing.
    * ``background-color`` on a cell is the one background Qt rich text paints
      reliably (the same reason :func:`_style_tables` colours per cell), which
      is what gives the block its tinted box.

    Newlines become ``<br/>`` because the cell collapses whitespace, and only
    LEADING indentation becomes ``&nbsp;`` — converting every space would make
    the line unbreakable again, which is the bug being fixed.
    """
    t = design.tokens()

    def render(match):
        code = match.group(1)
        # Trailing newline before </code> is markdown's, not the author's.
        lines = code.rstrip("\n").split("\n")
        rendered = []
        for line in lines:
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            rendered.append("&nbsp;" * indent + stripped)
        body = "<br/>".join(rendered)
        return (
            f'<table width="100%" cellspacing="0" cellpadding="0" '
            f'style="margin:8px 0;">'
            f'<tr><td style="background-color:{t.code_bg}; padding:8px 10px; '
            f'border:1px solid {t.border};">'
            f'<span style="font-family:{design.MONO_STACK_HTML}; '
            f'font-size:{design.TEXT_BASE}px; color:{t.text};">{body}</span>'
            f'</td></tr></table>'
        )

    html_text = _PRE_BLOCK.sub(render, html_text)

    # Inline `code` spans are short and need no special handling beyond a
    # monospace face.
    inline = (f'<code style="font-family:{design.MONO_STACK_HTML}; '
              f'font-size:{design.TEXT_BASE}px;">')
    return html_text.replace("<code>", inline)


def _style_tables(html_text):
    """Inline-style tables per cell, with theme-aware colours.

    Per CELL, not per table: Qt rich text honours ``background-color`` on cells
    but not the ``background`` shorthand, and an unset cell background is
    transparent — so a table inherited the bubble's colour underneath and dark
    text on the dark bubble became unreadable.
    """
    t = design.tokens()
    table = (f'<table style="border-collapse:collapse; width:100%; '
             f'margin:10px 0; border:1px solid {t.border}; '
             f'background-color:{t.surface}; font-size:{design.TEXT_BASE}px;">')
    th = (f'<th style="background-color:{t.surface_alt}; color:{t.text}; '
          f'font-weight:bold; text-align:left; padding:7px 9px; '
          f'border:1px solid {t.border};">')
    td = (f'<td style="background-color:{t.surface}; color:{t.text}; '
          f'padding:6px 9px; border:1px solid {t.border};">')
    return (html_text.replace("<table>", table)
            .replace("<th>", th).replace("<td>", td))


def _style_lists(html_text, fg):
    """Colour list markers and items explicitly.

    Qt rich text paints an ``<ol>``/``<ul>`` marker from the BLOCK's character
    format, which is not guaranteed to pick up the ``color`` set on the hosting
    QLabel's stylesheet — unlike the item text beside it, which inherits
    normally. Stating the colour inline removes the difference in treatment,
    and matters most in the user bubble, where the text is white on brand green
    and a marker falling back to the palette default would be invisible.
    """
    for tag in ("ol", "ul", "li"):
        html_text = html_text.replace(
            f"<{tag}>", f'<{tag} style="color:{fg};">')
    return html_text


class _GfmExtension(markdown.extensions.Extension):
    """The GitHub-flavoured bits ``python-markdown`` does not ship.

    The SaaS renders with ``remark-gfm``, which adds four things over
    CommonMark: tables, strikethrough, task lists and bare-URL autolinks.
    Tables come from the ``tables`` extension; the other three were silently
    dropped here — ``~~struck~~`` rendered as literal tildes, a task list came
    out as ``[ ] item``, and a pasted URL stayed plain text. Measured, not
    assumed: ``pymdownx``, which would have provided all three, is not in the
    bundled venv.

    Each piece is registered as an inline processor or a tree processor rather
    than as a regex over the source or the output, because both of those hit
    the same wall — a ``~~`` or a URL inside a code span or a fenced block must
    be left exactly as typed, and only the parser knows where those are.
    """

    def extendMarkdown(self, md):
        # Strikethrough. Priority below `backtick` (190), so a `~~` inside a
        # code span is already consumed and cannot match here.
        #
        # The closing `~~` is written out rather than back-referenced. With
        # `(~{2})(.+?)\2` the backreference points at group 2 — the CONTENT —
        # so it only matched when the text repeated itself, and `~~struck~~`
        # came through as literal tildes.
        md.inlinePatterns.register(
            markdown.inlinepatterns.SimpleTagInlineProcessor(
                r"(~~)(.+?)~~", "s"),
            "gfm_strikethrough", 95)

        # Bare URLs. Deliberately the LOWEST priority of any link pattern, so
        # `[text](url)` and `<url>` are both matched and consumed first —
        # otherwise this would match the URL inside a markdown link and leave
        # the surrounding syntax as stray text.
        md.inlinePatterns.register(
            _AutolinkInlineProcessor(
                r"(?<![\(\[\"'>=])\b(https?://[^\s<>\)\]\"']+)"),
            "gfm_autolink", 45)

        md.treeprocessors.register(
            _TaskListProcessor(md), "gfm_tasklist", 20)


class _AutolinkInlineProcessor(markdown.inlinepatterns.InlineProcessor):
    """``https://example.com`` → a link, the way GFM does it."""

    def handleMatch(self, m, data):
        url = m.group(1)
        # Trailing punctuation belongs to the sentence, not the URL: "see
        # https://rasid.ai." should not link the full stop.
        trimmed = url.rstrip(".,;:!?")
        element = ElementTree.Element("a")
        element.set("href", trimmed)
        element.text = trimmed
        return element, m.start(0), m.start(0) + len(trimmed)


class _TaskListProcessor(markdown.treeprocessors.Treeprocessor):
    """``- [ ] item`` → a checkbox glyph.

    A glyph rather than an ``<input type=checkbox>``: Qt rich text has no form
    controls, so a real checkbox renders as nothing at all and the item loses
    its first three characters. ☐/☑ carry the same meaning in one character
    that Qt will actually draw.
    """

    # The glyph is followed by an EN SPACE (U+2002), not a plain one. Qt
    # rich text collapses runs of ordinary whitespace, so a single space
    # left the box touching the first word; an en space is one character
    # that survives collapsing and reads as a gap. Written as an escape
    # so it cannot be mistaken for a typo, or "corrected" into a space.
    GAP = " "
    MARKERS = (("[ ] ", "☐" + GAP),
               ("[x] ", "☑" + GAP),
               ("[X] ", "☑" + GAP))

    def run(self, root):
        for item in root.iter("li"):
            text = item.text or ""
            for marker, glyph in self.MARKERS:
                if text.startswith(marker):
                    item.text = glyph + text[len(marker):]
                    break
        return root


def _style_headings(html_text, fg):
    """Size and weight h1–h6 inline, mirroring the SaaS's scale.

    Qt rich text gives an ``<h1>`` a browser-default size — far larger than
    anything else in a 420px panel — and applies no colour from the hosting
    label's stylesheet. The SaaS caps its headings at ``text-base`` for h1 and
    ``text-sm`` for h2/h3 for the same reason: inside a chat message a heading
    is a label, not a page title.
    """
    sizes = {
        "h1": (design.TEXT_LG, design.WEIGHT_BOLD),
        "h2": (design.TEXT_MD, design.WEIGHT_SEMIBOLD),
        "h3": (design.TEXT_BASE, design.WEIGHT_SEMIBOLD),
        "h4": (design.TEXT_BASE, design.WEIGHT_MEDIUM),
        "h5": (design.TEXT_BASE, design.WEIGHT_MEDIUM),
        "h6": (design.TEXT_BASE, design.WEIGHT_MEDIUM),
    }
    # Rewritten to <p>, not styled in place. Qt's rich-text parser gives every
    # heading tag a font SIZE ADJUSTMENT (h1 +3 steps, h2 +2, …) and applies it
    # on top of whatever the inline style asks for, so an `<h2 font-size:13px>`
    # still came out around 18px — bigger than the body it was labelling, in a
    # 420px panel. A paragraph carries no adjustment, so the size asked for is
    # the size drawn.
    for tag, (size, weight) in sizes.items():
        html_text = html_text.replace(
            f"<{tag}>",
            f'<p style="color:{fg}; font-size:{size}px; '
            f'font-weight:{weight}; margin:8px 0 4px 0;">')
        html_text = html_text.replace(f"</{tag}>", "</p>")
    return html_text


def _style_quotes_and_rules(html_text):
    """Blockquotes and horizontal rules.

    Qt rich text draws a ``<blockquote>`` as a plain indent with no rule, and
    an ``<hr>`` in the palette's default colour. The SaaS marks a quote with a
    brand-coloured left border, which Qt rich text does not support on a
    blockquote — so this uses a one-cell table with a coloured left cell,
    the same shape :func:`_style_code_blocks` uses for the same reason.
    """
    t = design.tokens()
    quote_open = (
        f'<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:6px 0;"><tr>'
        f'<td width="3" style="background-color:{t.accent};"></td>'
        f'<td style="padding:2px 0 2px 10px; color:{t.text_secondary};">'
    )
    html_text = html_text.replace("<blockquote>", quote_open)
    html_text = html_text.replace("</blockquote>", "</td></tr></table>")
    html_text = html_text.replace(
        "<hr />",
        f'<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:10px 0;"><tr>'
        f'<td height="1" style="background-color:{t.divider};"></td>'
        f'</tr></table>')
    return html_text


LINK_TEXT_MAX = 80


def _style_links(html_text):
    """Brand-colour links, and shorten the ones that are a URL.

    The SaaS truncates link TEXT past 80 characters and keeps the whole thing
    in a tooltip. Worth copying: GoPilot answers with signed storage URLs that
    run to several hundred characters, and one of those as link text pushes a
    420px bubble into a horizontal scroll.
    """
    t = design.tokens()

    def shorten(match):
        attrs, text = match.group(1), match.group(2)
        if len(text) > LINK_TEXT_MAX and "<" not in text:
            title = text.replace('"', "&quot;")
            return (f'<a{attrs} title="{title}" '
                    f'style="color:{t.accent_text};">'
                    f'{text[:LINK_TEXT_MAX - 1]}…</a>')
        return f'<a{attrs} style="color:{t.accent_text};">{text}</a>'

    return re.sub(r"<a([^>]*)>(.*?)</a>", shorten, html_text,
                  flags=re.DOTALL)


def fold_tool_calls(calls):
    """One row per tool INVOCATION, from the two tags each one emits.

    Ported from the SaaS, whose comment explains the shape: the worker records
    one entry per ``<tool>`` tag and a single invocation emits two of them —
    ``status="processing"`` first, then ``done`` — both carrying the same
    ``id``. So ``id`` is what identifies a call.

    Three details, each of which is a bug if dropped:

    * **Fold by id, not by name.** Keying on name collapses every invocation
      of the same tool into one row, so a tool run twice in a turn shows once,
      with only the last status.
    * **Name from the FIRST tag.** The closing tag streams in character by
      character and can still be a truncated prefix when a poll lands.
    * **Status from the LAST.** processing → done only moves forward.

    Legacy rows carry no id — they wrote a single tag per call — so they are
    kept in the order they arrive rather than folded together.
    """
    folded = []
    index_by_id = {}
    for call in calls or ():
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        position = None if call_id is None else index_by_id.get(call_id)
        if position is None:
            if call_id is not None:
                index_by_id[call_id] = len(folded)
            folded.append(dict(call))
            continue
        first = folded[position]
        folded[position] = {
            **first,
            "name": first.get("name") or call.get("name"),
            "status": call.get("status"),
        }
    return folded


def render_body_html(text, fg=None):
    """Markdown source → the HTML string a ``QLabel`` will display.

    ``fg`` is the bubble's text colour, needed because some elements have to
    be coloured inline rather than inherited (see :func:`_style_lists`).

    Code blocks are transformed first. The passes do not collide because
    :func:`_style_tables` matches only the BARE ``<table>``/``<td>`` tags the
    markdown extension emits, while the code pass emits tags that carry
    attributes — so a code block is not re-styled as a data table. Keep it
    that way if either function changes. The quote and rule pass runs AFTER
    both for the same reason: it emits attributed tables of its own.
    """
    colour = fg or design.tokens().text
    body = markdown.markdown(
        str(text or ""),
        extensions=["fenced_code", "tables", "nl2br", "sane_lists",
                    _GfmExtension()])
    body = _style_tables(_style_code_blocks(body))
    body = _style_quotes_and_rules(body)
    body = _style_headings(body, colour)
    body = _style_links(body)
    return _style_lists(body, colour)


# ══════════════════════════════ the bubble ═══════════════════════════════════
class MessageBubble(QFrame):
    """A single chat message: body, attachments, result controls, timestamp.

    The width is set by the page through :meth:`set_max_width` on every resize,
    rather than being a constant. The old fixed 560px ceiling was wider than
    the whole dock, so every bubble was clipped by the panel edge and long
    words broke mid-glyph.
    """

    MIN_WIDTH = 180

    # The exchange this bubble belongs to should be trashed. Carries the
    # message id; the page owns the request, because deleting a turn removes
    # the OTHER bubble too and only the page knows about its sibling.
    delete_requested = pyqtSignal(int)

    def __init__(self, text, role="ai", timestamp=None, client=None,
                 iface=None, with_time=True, attachments=None, tone="neutral",
                 message_id=None, parent=None):
        super().__init__(parent)
        self.role = role
        self.client = client
        self.iface = iface
        # None for a bubble the server has no row for yet — the in-flight
        # reply, or a system notice. The trash control is hidden until an id
        # arrives, since there is nothing to ask the server to delete.
        self.message_id = message_id
        self._download_threads = []
        self._network_managers = []
        self._max_width = 420

        t = design.tokens()
        if role == "user":
            bg, fg, border = t.bubble_user, t.bubble_user_text, t.bubble_user
        elif role == "system":
            # A system notice needs a visible container. `bubble_system` is
            # drawn from the same ramp rung as the transcript background, so on
            # its own it was an invisible box — an error message that looked
            # like loose text in the middle of the conversation. Errors get the
            # red wash they have earned; neutral notices get a bordered card.
            if tone == "error":
                bg, fg, border = t.error_soft, t.error, t.error
            else:
                bg, fg, border = t.surface, t.text_secondary, t.border
        else:
            bg, fg, border = t.bubble_ai, t.bubble_ai_text, t.bubble_ai_border
        self._fg = fg

        self.setObjectName("bubble")
        self.setFrameShape(QFrame_StyledPanel)
        self.setStyleSheet(f"""
            QFrame#bubble {{
                background: {bg}; border: 1px solid {border};
                border-radius: {design.RADIUS_LG}px;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 8)
        lay.setSpacing(3)

        # Reasoning and tool activity go ABOVE the answer, as they do in the
        # SaaS — they are what the model did on the way to the text below, so
        # reading order is the order they happened in. Built on demand; a turn
        # with neither adds nothing to the layout.
        #
        # A ROW, not a column: the two sit side by side, reasoning left and
        # tools right. Stacked, they cost ~170px of a panel before the answer
        # began, which is most of the readable height on a 420px dock.
        self._header_layout = QHBoxLayout()
        self._header_layout.setSpacing(design.SPACE_1)
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._header_layout)
        self._thinking_panel = None
        self._thinking_body = None
        self._tools_panel = None
        self._tools_rows = None
        self._tools_open = False

        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt_RichText)
        self._body.setOpenExternalLinks(True)
        if _SELECTABLE is not None and _LINKS is not None:
            self._body.setTextInteractionFlags(_SELECTABLE | _LINKS)
        self._body.setStyleSheet(
            f"color:{fg}; font-family:{design.FONT_STACK}; "
            f"font-size:{design.TEXT_MD}px; background:transparent; "
            f"border:none;"
        )
        lay.addWidget(self._body)

        # What the user attached to their own message, echoed back so the
        # transcript records it.
        if role == "user" and attachments:
            for label in attachments:
                chip = QLabel(f"• {label}")
                chip.setWordWrap(True)
                chip.setStyleSheet(
                    f"color:{fg}; font-family:{design.FONT_STACK}; "
                    f"font-size:{design.TEXT_SM}px; background:transparent; "
                    f"border:none; font-style:italic;"
                )
                lay.addWidget(chip)

        # Result controls (images, layer downloads) are appended here.
        self._results_layout = QVBoxLayout()
        self._results_layout.setSpacing(design.SPACE_1)
        self._results_layout.setContentsMargins(0, design.SPACE_2, 0, 0)
        lay.addLayout(self._results_layout)

        # Timestamp and the trash control share a row, as they do in the
        # SaaS: the trash is a footnote on the message, not an action competing
        # with the answer.
        self._time_label = None
        self._trash_button = None
        if role != "system":
            footer = QHBoxLayout()
            footer.setContentsMargins(0, 0, 0, 0)
            footer.setSpacing(design.SPACE_1)

            if with_time and prefs.get_bool(prefs.SHOW_TIMESTAMPS):
                shown = (timestamp
                         or QDateTime.currentDateTime().toString("HH:mm"))
                self._time_label = QLabel(shown)
                self._time_label.setStyleSheet(
                    f"color:{fg}; font-family:{design.FONT_STACK}; "
                    f"font-size:{design.TEXT_XS}px; background:transparent; "
                    f"border:none;"
                )
                footer.addWidget(self._time_label)

            # The SAME control on every bubble: one red, no backing shape,
            # whichever surface it sits on. Red at rest rather than only on
            # hover — in the history list a permanent red would make the whole
            # column read as warnings, but a transcript has at most two of
            # these per exchange and the muted grey was genuinely hard to find.
            #
            # A NOTE FOR ANYONE ABOUT TO "FIX" THE CONTRAST
            # =============================================
            # On the user's brand-green bubble this red measures 1.14:1 against
            # its background, well under the 3:1 WCAG floor for a non-text
            # graphic — red and green sit at almost the same luminance, so no
            # red glyph is legible there by that metric. Two alternatives were
            # built and rejected: a pale pink glyph (3.20:1, but it reads as
            # WHITE at 11px), and a red glyph on a light disc (3.29:1 on the
            # disc, 3.75:1 disc-on-green, but it made the control a different
            # component from the one on the assistant bubble).
            #
            # Matching the two bubbles was chosen deliberately over the
            # measurement. The glyph is still told apart by HUE, which is what
            # a 3:1 luminance rule does not capture — but it will be hard for
            # anyone with a red-green deficiency, and that is the known cost.
            self._trash_button = IconButton(
                "trash", size=20, icon_size=11, tooltip="Delete this exchange",
                tone="danger", hover_tone="danger")
            self._trash_button.clicked.connect(self._on_trash_clicked)
            self._trash_button.setVisible(message_id is not None)

            # The user's bubble is right-aligned, so its footer reads
            # right-to-left: stretch first, then the controls.
            if role == "user":
                footer.insertStretch(0)
                footer.addWidget(self._trash_button)
            else:
                footer.addWidget(self._trash_button)
                footer.addStretch()
            lay.addLayout(footer)

        self.set_content(text)

    def _on_trash_clicked(self):
        if self.message_id is not None:
            self.delete_requested.emit(int(self.message_id))

    def set_message_id(self, message_id):
        """Attach a server row to this bubble, revealing the trash control.

        Needed because the assistant bubble is created BEFORE the server has
        an id for it: the reply streams into a bubble opened at send time, and
        the id only arrives with the finished task.
        """
        self.message_id = message_id
        if self._trash_button is not None:
            self._trash_button.setVisible(message_id is not None)

    # ── sizing ────────────────────────────────────────────────────────────────
    def set_max_width(self, width):
        """Cap the bubble (and its body label) at ``width`` pixels."""
        width = max(self.MIN_WIDTH, int(width))
        if width == self._max_width:
            return
        self._max_width = width
        self.setMaximumWidth(width)
        # The body gets the inner width, or word wrap measures against the
        # outer box and the last column of text sits under the border.
        inner = max(40, width - 26)
        self._body.setMaximumWidth(inner)
        for index in range(self._results_layout.count()):
            widget = self._results_layout.itemAt(index).widget()
            if widget is not None:
                widget.setMaximumWidth(inner)
        self._size_panels(inner)

    def _size_panels(self, inner):
        """Lay the reasoning and tool panels across the bubble's width.

        Both present, they split it; one on its own takes all of it, so a turn
        that only reasoned does not leave half the row empty.

        The width is pinned rather than left to the layout because a bubble is
        otherwise as wide as its CONTENT needs, and a short answer like "Found
        3 candidate sites." produced a narrow bubble with the panels crammed
        inside it. The panels are the widest thing in the bubble when they
        exist, so they set its width rather than inherit it.
        """
        panels = [p for p in (self._thinking_panel, self._tools_panel)
                  if p is not None]
        if not panels:
            return
        if len(panels) > 1:
            # Integer division, and the FIRST panel absorbs the remainder, so
            # an odd inner width does not leave a 1px gap at the right edge.
            each = (inner - design.SPACE_1) // 2
            widths = [inner - design.SPACE_1 - each, each]
        else:
            widths = [inner]
        for panel, width in zip(panels, widths):
            try:
                panel.setMinimumWidth(width)
                panel.setMaximumWidth(width)
            except RuntimeError:
                pass        # panel was removed between resize and here

    def max_width(self):
        return self._max_width

    # ── reasoning and tools ───────────────────────────────────────────────────
    def _build_panel(self, object_name, caption_text, bg, border, caption_fg,
                     height):
        """A fixed-height titled box whose content scrolls.

        Shared by both panels because they differ only in colour and label.
        The height is pinned and the body is put in a scroll area: reasoning
        runs to hundreds of words and the tool list to a dozen rows, and
        letting either size itself pushed the ANSWER off the bottom of the
        panel and made the bubble jump on every poll as the text grew.

        Returns ``(panel, host_layout)`` — callers put their content in the
        host layout and never touch the frame.
        """
        panel = QFrame()
        panel.setObjectName(object_name)
        panel.setStyleSheet(
            f"QFrame#{object_name} {{ background:{bg}; "
            f"border:1px solid {border}; "
            f"border-radius:{design.RADIUS}px; }}"
        )
        panel.setFixedHeight(height)
        panel_lay = QVBoxLayout(panel)
        panel_lay.setContentsMargins(design.SPACE_2, 5, design.SPACE_2, 5)
        panel_lay.setSpacing(3)

        caption = QLabel(caption_text)
        caption.setStyleSheet(
            f"color:{caption_fg}; font-family:{design.FONT_STACK}; "
            f"font-size:{design.TEXT_XS}px; "
            f"font-weight:{design.WEIGHT_SEMIBOLD}; "
            f"letter-spacing:0.6px; background:transparent; border:none;")
        panel_lay.addWidget(caption)

        scroll = styled_scroll_area()
        # Vertical only. A horizontal bar inside an 88px box costs a sixth of
        # the readable height, and the body word-wraps anyway.
        scroll.setHorizontalScrollBarPolicy(Qt_ScrollBarAlwaysOff)
        host = QWidget()
        paint_transparent(host)
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.setSpacing(2)
        scroll.setWidget(host)
        panel_lay.addWidget(scroll, stretch=1)
        return panel, host_lay, caption

    def set_thinking(self, text, streaming=False):
        """Show the model's reasoning above the answer.

        Always shown in full, in a fixed box that scrolls — it used to be
        expanded while streaming and then collapsed to a one-line preview,
        which meant the reasoning for a finished turn was effectively
        unreadable, and the reasoning for a REOPENED turn was not shown at all
        because nothing passed it back in. Both are now the same widget in the
        same state, whether the turn is live or loaded from history.

        Passing an empty value removes the panel, so a turn whose reasoning
        never materialised does not leave an empty box behind.
        """
        if not text:
            if self._thinking_panel is not None:
                self._thinking_panel.setParent(None)
                self._thinking_panel.deleteLater()
                self._thinking_panel = None
                self._thinking_body = None
                self._thinking_caption = None
            # The survivor now owns the whole row, so re-lay it out.
            # Without this it kept its half width and left the other
            # half of the bubble empty.
            self._size_panels(max(40, self._max_width - 26))
            return

        t = design.tokens()
        if self._thinking_panel is None:
            panel, host_lay, caption = self._build_panel(
                "thinkingPanel", "THINKING", t.accent_soft,
                t.accent_soft_border, t.accent_text,
                design.REASONING_PANEL_HEIGHT)

            body = QLabel()
            body.setWordWrap(True)
            body.setTextFormat(Qt_RichText)
            body.setAlignment(Qt_AlignTop)
            body.setStyleSheet(
                f"color:{t.text_secondary}; font-family:{design.FONT_STACK}; "
                f"font-size:{design.TEXT_BASE}px; background:transparent; "
                f"border:none;")
            host_lay.addWidget(body)
            host_lay.addStretch()

            self._thinking_panel = panel
            self._thinking_body = body
            self._thinking_caption = caption
            self._header_layout.insertWidget(0, panel)
            self._size_panels(max(40, self._max_width - 26))

        self._thinking_body.setText(
            render_body_html(text, design.tokens().text_secondary))
        # An ellipsis is the only thing that marks a live turn now. It is in
        # the caption rather than in the body so it does not move as the text
        # grows.
        self._thinking_caption.setText(
            "THINKING\u2026" if streaming else "THINKING")

    def set_tool_calls(self, calls, streaming=False):
        """List the tools this turn invoked, with their outcome."""
        folded = fold_tool_calls(calls)
        if not folded:
            if self._tools_panel is not None:
                self._tools_panel.setParent(None)
                self._tools_panel.deleteLater()
                self._tools_panel = None
                self._tools_rows = None
                self._tools_caption = None
            # The survivor now owns the whole row, so re-lay it out.
            # Without this it kept its half width and left the other
            # half of the bubble empty.
            self._size_panels(max(40, self._max_width - 26))
            return

        t = design.tokens()
        if self._tools_panel is None:
            panel, host_lay, caption = self._build_panel(
                "toolsPanel", "TOOLS", t.surface_alt, t.border, t.text_muted,
                design.REASONING_PANEL_HEIGHT)
            self._tools_panel = panel
            self._tools_rows = host_lay
            self._tools_caption = caption
            self._header_layout.addWidget(panel)
            self._size_panels(max(40, self._max_width - 26))

        clear_layout(self._tools_rows)
        for call in folded:
            self._tools_rows.addWidget(self._tool_row(call))
        self._tools_rows.addStretch()
        self._tools_caption.setText(
            f"TOOLS \u00b7 {len(folded)}" if len(folded) > 1 else "TOOLS")
        self._tools_panel.setVisible(True)

    def _tool_row(self, call):
        """One tool invocation: a status glyph and the tool's name."""
        t = design.tokens()
        status = call.get("status") or "in_progress"
        glyph, colour = {
            "success": ("check", t.success),
            "error": ("close", t.error),
        }.get(status, ("refresh", t.text_muted))

        row = QFrame()
        row.setStyleSheet("background:transparent; border:none;")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(5)

        mark = QLabel()
        mark.setPixmap(icons.pixmap(glyph, 11, colour, dpr=2.0))
        mark.setStyleSheet("background:transparent; border:none;")
        row_lay.addWidget(mark)

        name = QLabel(call.get("name") or "…")
        name.setStyleSheet(
            f"color:{t.text_secondary}; font-family:{design.MONO_STACK}; "
            f"font-size:{design.TEXT_XS}px; background:transparent; "
            f"border:none;")
        row_lay.addWidget(name, stretch=1)
        return row

    # ── content ───────────────────────────────────────────────────────────────
    def set_content(self, text):
        """Replace the body. Safe to call repeatedly while streaming."""
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        text = str(text or "")
        files = extract_file_urls(text)

        # Strip the links out of the prose — they are re-rendered as controls
        # below — then drop list bullets left empty by the removal.
        display_text = text
        for url, _, _, _ in files:
            # `!?` so the image form is stripped too. Without it the URL was
            # removed but the leading `![alt]` stayed, leaving the alt text in
            # brackets with a stray exclamation mark in the prose.
            display_text = re.sub(
                r'!?\[[^\]]*\]\(<?' + re.escape(url) + r'>?\)', '',
                display_text)
            display_text = display_text.replace(url, '')
        display_text = re.sub(r'(?m)^[ \t]*[-*+][ \t]*$', '', display_text)

        self._body.setText(render_body_html(display_text, self._fg))

        if self.role == "ai" and self.client:
            for url, filename, extension, is_image in files:
                if is_image:
                    self._add_image_result(url, filename)
                else:
                    self._add_layer_result(url, filename)
            if files and prefs.get_bool(prefs.AUTO_LOAD_LAYERS):
                self._auto_load()

    def append_text(self, text):
        """Streaming helper: re-render with the full text so far."""
        self.set_content(text)

    # ── result: a layer ───────────────────────────────────────────────────────
    def _result_button_qss(self):
        t = design.tokens()
        return f"""
            QPushButton {{
                background: {t.surface_alt}; color: {t.text};
                border: 1px solid {t.border_strong};
                border-radius: {design.RADIUS}px; padding: 7px 10px;
                font-family: {design.FONT_STACK};
                font-size: {design.TEXT_BASE}px;
                font-weight: {design.WEIGHT_MEDIUM}; text-align: left;
            }}
            QPushButton:hover {{
                background: {t.accent_soft}; border-color: {t.accent};
                color: {t.accent_text};
            }}
            QPushButton:disabled {{
                background: {t.surface_alt}; color: {t.text_muted};
                border-color: {t.border};
            }}
        """

    def _add_layer_result(self, url, filename):
        t = design.tokens()
        btn = QPushButton(f"  Add {filename} to QGIS")
        btn.setCursor(Qt_PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setIcon(icons.icon("download", design.ICON_SM, t.accent))
        btn.setIconSize(QSize(design.ICON_SM, design.ICON_SM))
        btn.setStyleSheet(self._result_button_qss())
        btn.setMaximumWidth(max(40, self._max_width - 26))
        btn.clicked.connect(
            partial(self._download_and_load, url, filename, btn))
        self._results_layout.addWidget(btn)
        return btn

    def _download_and_load(self, url, filename, button):
        if not self.client:
            QMessageBox.warning(self, "Not signed in",
                                "Cannot download without an active session.")
            return
        button.setText(f"  Downloading {filename}…")
        button.setEnabled(False)

        thread = DownloadLayerThread(self.client, url, filename)
        thread.finished.connect(
            lambda path: self._on_layer_downloaded(path, filename, button))
        thread.error.connect(
            lambda msg: self._on_download_error(msg, button, filename))
        self._download_threads.append(thread)
        thread.start()

    def _on_layer_downloaded(self, filepath, filename, button):
        t = design.tokens()
        try:
            add_layer_to_qgis(filepath, filename.rsplit('.', 1)[0], self.iface)
        except Exception as exc:
            QMessageBox.warning(self, "Could not load layer",
                                f"The file downloaded but QGIS could not open "
                                f"it:\n{exc}")
            button.setText(f"  Retry {filename}")
            button.setEnabled(True)
            return
        button.setText(f"  Added {filename}")
        button.setIcon(icons.icon("check", design.ICON_SM, t.success))
        button.setEnabled(False)

    def _on_download_error(self, message, button, filename):
        box = QMessageBox(self)
        box.setWindowTitle("Download failed")
        box.setIcon(QMessageBox_Warning)
        box.setText(f"Could not download {filename}:\n{message}")
        box.setStandardButtons(QMessageBox_Ok)
        exec_dialog(box)
        button.setText(f"  Retry {filename}")
        button.setEnabled(True)

    def _auto_load(self):
        """Honour the "Load results automatically" preference."""
        for index in range(self._results_layout.count()):
            widget = self._results_layout.itemAt(index).widget()
            if isinstance(widget, QPushButton) and widget.isEnabled():
                widget.click()

    # ── result: an image ──────────────────────────────────────────────────────
    def _add_image_result(self, url, filename):
        """Inline preview plus a Save button that is present from the start.

        The button is added up front, not after a successful fetch, so an
        expired presigned URL still leaves the user a working control instead
        of just a broken preview.
        """
        t = design.tokens()
        inner = max(40, self._max_width - 26)

        preview = QLabel("Loading preview…")
        preview.setAlignment(Qt_AlignCenter)
        preview.setMaximumWidth(inner)
        preview.setStyleSheet(
            f"background:{t.surface_alt}; color:{t.text_muted}; "
            f"border:1px solid {t.border}; "
            f"border-radius:{design.RADIUS}px; padding:14px; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_SM}px;"
        )
        self._results_layout.addWidget(preview)

        save_btn = QPushButton(f"  Save {filename}")
        save_btn.setCursor(Qt_PointingHandCursor)
        save_btn.setAutoDefault(False)
        save_btn.setIcon(icons.icon("download", design.ICON_SM, t.accent))
        save_btn.setIconSize(QSize(design.ICON_SM, design.ICON_SM))
        save_btn.setStyleSheet(self._result_button_qss())
        save_btn.setMaximumWidth(inner)
        save_btn.clicked.connect(partial(self._save_image, url, filename))
        self._results_layout.addWidget(save_btn)

        self._fetch_preview(url, filename, preview)

    def _api_token(self):
        """The signed-in API key, or None.

        Read off the client rather than from settings so the preview uses the
        same credential as every other request in the session, and so a signed
        out panel simply fetches anonymously instead of raising.
        """
        return getattr(self.client, "api_key", None) if self.client else None

    def _fetch_preview(self, url, filename, preview):
        manager = QNetworkAccessManager(self)
        self._network_managers.append(manager)
        # fromEncoded, NOT QUrl(url): a presigned S3 link is already
        # percent-encoded, and re-encoding the %2F in its credential breaks
        # the signature and returns 403.
        request = QNetworkRequest(QUrl.fromEncoded(url.encode("utf-8")))
        # Authenticate our own hosts. This request carried no credentials at
        # all, so an image served from the API's own file endpoint — which is
        # where GoPilot puts its results — came back 401 and the preview sat
        # there showing a failure. Third-party links stay anonymous, so a
        # presigned URL in an answer never sees the token.
        token = self._api_token()
        if token and is_trusted_api_url(url):
            request.setRawHeader(b"Authorization",
                                 f"Bearer {token}".encode("utf-8"))
        reply = manager.get(request)

        def on_finished():
            t = design.tokens()
            try:
                if reply.error():
                    status = (reply.attribute(_HTTP_STATUS_ATTR)
                              if _HTTP_STATUS_ATTR is not None else None)
                    preview.setText(
                        f"This link has expired ({filename})" if status == 403
                        else f"Preview unavailable ({filename})"
                    )
                    return
                pixmap = QPixmap()
                if not pixmap.loadFromData(reply.readAll()):
                    preview.setText(f"Could not decode {filename}")
                    return
                budget = max(40, self._max_width - 42)
                if pixmap.width() > budget:
                    pixmap = pixmap.scaledToWidth(
                        budget, Qt_SmoothTransformation)
                if pixmap.height() > 360:
                    pixmap = pixmap.scaledToHeight(
                        360, Qt_SmoothTransformation)
                preview.setPixmap(pixmap)
                preview.setText("")
                preview.setStyleSheet(
                    f"background:transparent; border:1px solid {t.border}; "
                    f"border-radius:{design.RADIUS}px; padding:3px;"
                )
            except Exception as exc:
                preview.setText(f"Preview failed: {exc}")
            finally:
                reply.deleteLater()

        reply.finished.connect(on_finished)

    def _save_image(self, url, filename):
        default_path = os.path.join(
            os.path.expanduser("~"), "Downloads", filename)
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save image", default_path, "Images (*.png *.jpg *.jpeg)")
        if not save_path:
            return

        thread = DownloadLayerThread(self.client, url)
        thread.finished.connect(
            lambda temp_path: self._move_file(temp_path, save_path))
        thread.error.connect(
            lambda msg: QMessageBox.warning(
                self, "Save failed", f"Could not save the image:\n{msg}")
        )
        self._download_threads.append(thread)
        thread.start()

    def _move_file(self, temp_path, save_path):
        try:
            shutil.move(temp_path, save_path)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed",
                                f"Could not write the file:\n{exc}")
