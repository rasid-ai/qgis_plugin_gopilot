"""The message bar at the bottom of the chat: plus, input, send.

Shape, per the sketch: one rounded pill holding a ``+`` on the left, a growing
text area in the middle and a circular Send on the right, with a row of
attachment chips above the input once anything is attached.

THE PLUS MENU
=============
``+`` opens the three ways to give GoPilot something to work on, which is the
grouping the sketch calls out ("uploads / select a layer / draw a layer"):

* **Upload files** — a file picker limited to what the backend accepts.
* **Select a layer** — picks a loaded QGIS vector layer and converts it to
  GeoJSON.
* **Draw on the map** — hands the canvas a draw tool.

They sit behind one button rather than three in the bar because the dock is
~420px wide and Send plus three affordances left no room for the actual text.

WHY A QPlainTextEdit AND NOT A QLineEdit
========================================
The old composer was a ``QLineEdit``: one line, no wrapping, no newlines. A
GoPilot prompt is routinely a paragraph, and a single-line field makes you
compose a paragraph through a 300px porthole. :class:`GrowingTextEdit` starts
at one line and grows to six, then scrolls — the same behaviour as the SaaS's
auto-sizing textarea.

ENTER
=====
Enter sends and Shift+Enter inserts a newline, which is what a chat input is
expected to do. A user who pastes multi-line prompts can invert it in Settings
(:data:`prefs.SEND_ON_ENTER`), after which Enter adds a line and Ctrl+Enter
sends.
"""

import os

from qgis.PyQt.QtCore import QPoint, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QMenu, QMessageBox,
    QPlainTextEdit, QVBoxLayout,
    QWidget,
)

from . import design, icons, prefs, uploads
from .compat import (
    QFrame_NoFrame, QSizePolicy_Fixed, QSizePolicy_Preferred, Qt_AlignBottom,
    Qt_ControlModifier, Qt_Key_Enter, Qt_Key_Return, Qt_ScrollBarAlwaysOff,
    Qt_ScrollBarAsNeeded, Qt_ShiftModifier,
)
from .widgets import Chip, IconButton, paint_transparent

# What the backend accepts: raster (tiff), vector (geojson) and tabular
# (csv/tsv). Kept in step with the SaaS's `fileKinds.ts`; anything else is
# rejected server-side, so offering it here would only waste an upload.
UPLOAD_FILTER = (
    "GoPilot inputs (*.tif *.tiff *.geojson *.json *.csv *.tsv);;"
    "Rasters (*.tif *.tiff);;"
    "Vectors (*.geojson *.json);;"
    "Tables (*.csv *.tsv);;"
    "All files (*)"
)

MAX_INPUT_LINES = 6


class GrowingTextEdit(QPlainTextEdit):
    """A text area that grows with its content, up to :data:`MAX_INPUT_LINES`."""

    submitted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt_ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt_ScrollBarAlwaysOff)
        self.setFrameShape(QFrame_NoFrame)
        self.setSizePolicy(QSizePolicy_Preferred, QSizePolicy_Fixed)
        self.setTabChangesFocus(True)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._sync_height())
        self._sync_height()

    def _line_height(self):
        return max(14, self.fontMetrics().lineSpacing())

    def _sync_height(self):
        # For a QPlainTextEdit the document layout reports its height in LINES,
        # not pixels — which is exactly what is needed to clamp to a line count.
        lines = int(self.document().size().height()) or 1
        visible = max(1, min(lines, MAX_INPUT_LINES))
        line_h = self._line_height()

        # Pad the text box out to the button height and split the difference
        # above and below, so a single line of prompt sits on the same centre
        # line as the + and Send glyphs beside it. The old version added a flat
        # 6px, all of which landed BELOW the text (a QPlainTextEdit lays its
        # document out from the top), so the text rode high by about 3px —
        # close enough to look accidental rather than aligned.
        extra = max(0, design.COMPOSER_BUTTON - line_h)
        pad_top = extra // 2
        pad_bottom = extra - pad_top
        # Protected in C++, callable from a Python subclass. Used instead of
        # the document margin because that one is symmetric on all four sides
        # and would indent the text away from the + button horizontally.
        self.setViewportMargins(0, pad_top, 0, pad_bottom)
        self.setFixedHeight(visible * line_h + pad_top + pad_bottom)

        # Past the cap the box stops growing, so the overflow has to scroll.
        self.setVerticalScrollBarPolicy(
            Qt_ScrollBarAsNeeded if lines > MAX_INPUT_LINES
            else Qt_ScrollBarAlwaysOff
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt_Key_Return, Qt_Key_Enter):
            modifiers = event.modifiers()
            shift = bool(modifiers & Qt_ShiftModifier)
            ctrl = bool(modifiers & Qt_ControlModifier)
            send_on_enter = prefs.get_bool(prefs.SEND_ON_ENTER)
            if send_on_enter and not shift and not ctrl:
                self.submitted.emit()
                return
            if not send_on_enter and ctrl:
                self.submitted.emit()
                return
        super().keyPressEvent(event)


class Composer(QFrame):
    """The whole input bar. Emits :attr:`submitted` with text and attachments."""

    # (text, file_paths, geojson or None)
    submitted = pyqtSignal(str, list, object)
    layer_requested = pyqtSignal()
    draw_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files = []            # local paths queued for upload
        self._geojson = None        # attached geometry, as a GeoJSON dict
        self._geojson_label = ""
        self._busy = False

        self.setObjectName("composerHost")
        t = design.tokens()
        self.setStyleSheet(
            f"QFrame#composerHost {{ background:{t.surface}; "
            f"border:none; border-top:1px solid {t.border}; }}"
        )

        outer = QVBoxLayout(self)
        # Symmetric vertical padding: the nav rail's Settings footer is sized
        # and centred from these numbers so the two columns' rules and glyphs
        # line up. See the alignment contract in design.py.
        outer.setContentsMargins(design.SPACE_3, design.COMPOSER_PAD,
                                 design.SPACE_3, design.COMPOSER_PAD)
        outer.setSpacing(design.SPACE_2)

        # Attachment chips, hidden until something is attached.
        self._chips_host = QWidget()
        paint_transparent(self._chips_host)
        self._chips_layout = QHBoxLayout(self._chips_host)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(design.SPACE_1)
        self._chips_layout.addStretch()
        self._chips_host.setVisible(False)
        outer.addWidget(self._chips_host)

        outer.addWidget(self._build_pill())

    def _build_pill(self):
        t = design.tokens()
        pill = QFrame()
        pill.setObjectName("composerPill")
        pill.setStyleSheet(f"""
            QFrame#composerPill {{
                background: {t.composer_bg};
                border: 1px solid {t.border_strong};
                border-radius: {design.RADIUS_XL}px;
            }}
        """)
        lay = QHBoxLayout(pill)
        lay.setContentsMargins(6, design.COMPOSER_PILL_PAD,
                               6, design.COMPOSER_PILL_PAD)
        lay.setSpacing(design.SPACE_1)

        # The + and Send are deliberately the same size with the same glyph
        # size, one at each end of the pill: they are the two ends of a single
        # control, and a Send that is bigger or squarer reads as a different
        # kind of thing bolted on.
        self.btn_plus = IconButton(
            "plus", size=design.COMPOSER_BUTTON, icon_size=design.ICON,
            tooltip="Attach files, a layer, or a drawn area")
        self.btn_plus.clicked.connect(self._open_attach_menu)
        lay.addWidget(self.btn_plus, alignment=Qt_AlignBottom)

        self.input = GrowingTextEdit()
        self.input.setPlaceholderText("Ask GoPilot about your map…")
        self.input.setStyleSheet(f"""
            QPlainTextEdit {{
                background: transparent; border: none;
                color: {t.text}; font-family: {design.FONT_STACK};
                font-size: {design.TEXT_MD}px;
                selection-background-color: {t.accent};
                selection-color: {t.text_on_accent};
            }}
        """)
        self.input.document().setDocumentMargin(0)
        self.input.submitted.connect(self.submit)
        lay.addWidget(self.input, stretch=1)

        self.btn_send = IconButton(
            "send", size=design.COMPOSER_BUTTON, icon_size=design.ICON,
            tooltip="Send", filled=True)
        self.btn_send.clicked.connect(self.submit)
        lay.addWidget(self.btn_send, alignment=Qt_AlignBottom)
        return pill

    # ── attachments ───────────────────────────────────────────────────────────
    def _open_attach_menu(self):
        t = design.tokens()
        menu = QMenu(self)
        menu.setStyleSheet(design.menu_qss())

        upload = menu.addAction(
            icons.icon("upload", design.ICON_SM, t.text_secondary),
            "Upload files…")
        upload.triggered.connect(self.choose_files)

        layer = menu.addAction(
            icons.icon("layers", design.ICON_SM, t.text_secondary),
            "Select a layer…")
        layer.triggered.connect(self.layer_requested.emit)

        draw = menu.addAction(
            icons.icon("draw", design.ICON_SM, t.text_secondary),
            "Draw an area on the map")
        draw.triggered.connect(self.draw_requested.emit)

        # Open UPWARD: the composer sits at the bottom of the dock, so a menu
        # dropped below the button would land off-screen and Qt would shove it
        # back over the button itself.
        anchor = self.btn_plus.mapToGlobal(self.btn_plus.rect().topLeft())
        menu.exec(QPoint(anchor.x(), anchor.y() - menu.sizeHint().height() - 4))

    def set_upload_limits(self, limits):
        """Adopt the server's accepted extensions and size ceilings.

        Called once per session by the page. Until it arrives — or if the
        request failed — the picker keeps its built-in filter and nothing is
        validated locally; see :mod:`.uploads` for why there is no hardcoded
        fallback.
        """
        self._upload_limits = limits or {}

    def choose_files(self):
        limits = getattr(self, "_upload_limits", None)
        # The server's own list of extensions, so the dialog cannot offer a
        # type the upload will reject.
        name_filter = (uploads.file_dialog_filter(limits) if limits
                       else UPLOAD_FILTER)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select files to send to GoPilot", "", name_filter)
        self.add_files(paths or [])

    def add_files(self, paths):
        """Queue several files, reporting every one the server would refuse.

        Checked here rather than on send so the rejection names the file while
        the user is still looking at the picker — and all of them at once,
        rather than one per round trip to the API.
        """
        limits = getattr(self, "_upload_limits", None)
        accepted, rejected = uploads.partition_uploads(paths, limits)
        for path in accepted:
            self.add_file(path)
        if rejected:
            detail = "\n".join(
                f"\u2022 {os.path.basename(path)} \u2014 {reason}"
                for path, reason in rejected)
            QMessageBox.warning(
                self, "Some files were not added",
                f"The server does not accept these:\n\n{detail}")

    def add_file(self, path):
        """Queue a local file for upload with the next message."""
        if path and path not in self._files:
            self._files.append(path)
            self._render_chips()

    def set_geometry(self, geojson, label):
        """Attach drawn/selected geometry. Replaces any previous geometry."""
        self._geojson = geojson
        self._geojson_label = label or "Area"
        self._render_chips()

    def clear_attachments(self):
        self._files = []
        self._geojson = None
        self._geojson_label = ""
        self._render_chips()

    def has_attachments(self):
        return bool(self._files or self._geojson)

    def attachment_labels(self):
        """Human-readable names, for echoing into the sent message bubble."""
        labels = []
        if self._geojson:
            labels.append(self._geojson_label)
        labels.extend(os.path.basename(path) for path in self._files)
        return labels

    def _render_chips(self):
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if self._geojson:
            chip = Chip(self._geojson_label, icon_name="map-pin",
                        tone="accent")
            chip.removed.connect(self._remove_geometry)
            self._chips_layout.addWidget(chip)

        for path in list(self._files):
            chip = Chip(os.path.basename(path), icon_name="attach",
                        tone="neutral")
            chip.removed.connect(lambda p=path: self._remove_file(p))
            self._chips_layout.addWidget(chip)

        self._chips_layout.addStretch()
        self._chips_host.setVisible(self.has_attachments())

    def _remove_file(self, path):
        if path in self._files:
            self._files.remove(path)
            self._render_chips()

    def _remove_geometry(self):
        self._geojson = None
        self._geojson_label = ""
        self._render_chips()

    # ── sending ───────────────────────────────────────────────────────────────
    def submit(self):
        if self._busy:
            return
        text = self.input.toPlainText().strip()
        if not text and not self.has_attachments():
            return
        # Hand over copies: the page keeps them for the lifetime of the request
        # while the composer is already being cleared for the next message.
        self.submitted.emit(text, list(self._files), self._geojson)
        self.input.clear()
        self.clear_attachments()

    def set_busy(self, busy):
        """Disable input while a turn is in flight."""
        self._busy = bool(busy)
        self.input.setReadOnly(self._busy)
        self.btn_send.setEnabled(not self._busy)
        self.btn_plus.setEnabled(not self._busy)
        if not self._busy:
            self.input.setFocus()

    def set_placeholder(self, text):
        self.input.setPlaceholderText(text)

    def focus_input(self):
        self.input.setFocus()

    def refresh_theme(self):
        t = design.tokens()
        self.setStyleSheet(
            f"QFrame#composerHost {{ background:{t.surface}; "
            f"border:none; border-top:1px solid {t.border}; }}"
        )
        self.btn_plus.refresh_theme()
        self.btn_send.refresh_theme()
        self._render_chips()
