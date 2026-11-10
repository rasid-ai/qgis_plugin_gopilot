"""The History view — every saved GoPilot conversation.

In the old window this was a 200px-wide splitter pane permanently stapled to
the left of the transcript. In a ~420px dock that is not affordable: it left
the chat itself under 200px, which is narrower than a single message bubble
wants to be. So history became one of the rail's views — full width when you
want it, invisible when you don't.

WHY THIS IS NOT A QListWidget
=============================
The previous implementation used ``QListWidget`` + ``setItemWidget`` per row.
Three things went wrong with that and all of them are structural:

* Row height came from ``item.setSizeHint(widget.sizeHint())``, measured before
  the widget had been laid out, so rows were mis-sized until something forced a
  relayout.
* Click handling was done by REASSIGNING ``item_widget.mousePressEvent`` to a
  lambda, which silently replaces the widget's own event handling.
* ``itemSelectionChanged`` was connected inside the load callback, so every
  refresh added another connection to the same slot — by the fifth reload the
  recolour ran five times per click.

Plain row widgets in a scroll area have none of those problems: each row sizes
itself, handles its own events, and is destroyed on reload.

NOTHING ABOVE THE LIST
======================
The view is the list and nothing else. It briefly carried a New chat button, a
filter field and a Refresh button, and all three were removed:

* **New chat** is the top button of the nav rail, two cells away.
* **Filter** earned its 36px only once the list was long, and the titles are
  already elided to one line each, so scanning is fast without it. If it comes
  back it should filter SERVER-side, not re-filter one page of results.
* **Refresh** was dead weight: :meth:`load` already runs every time the view
  is opened. The one moment a reload control is genuinely useful is after a
  FAILED load, so that is the only place it now appears — see
  :meth:`_show_message`.
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QLineEdit,
    QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget,
)

from . import design
from .chat_threads import DeleteChatThread, FetchChatsThread, stop_threads, RenameChatThread
from .compat import (
    Qt_Key_Escape,
    QMessageBox_No, QMessageBox_Question, QMessageBox_Yes, Qt_AlignCenter,
    Qt_LeftButton, Qt_PointingHandCursor, exec_dialog,
)
from .logger import debug_print
from .widgets import (
    paint_transparent,
    paint_background,
    ElidedLabel, IconButton, clear_layout, secondary_button,
    styled_scroll_area,
)


class _ChatRow(QFrame):
    """One conversation: title, timestamp, delete. Clickable as a whole."""

    clicked = pyqtSignal()
    delete_requested = pyqtSignal()
    rename_requested = pyqtSignal(str)      # the new title

    def __init__(self, chat, parent=None):
        super().__init__(parent)
        self.chat_id = chat.get("id")
        self.title = chat.get("title") or "Untitled chat"
        self._selected = False
        self.setCursor(Qt_PointingHandCursor)
        self.setObjectName("chatRow")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(design.SPACE_3, design.SPACE_2,
                               design.SPACE_2, design.SPACE_2)
        lay.setSpacing(design.SPACE_2)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._title_label = ElidedLabel(self.title)
        text_col.addWidget(self._title_label)

        # The editor lives in the row rather than in a dialog: a modal for one
        # short string is heavier than the edit, and it would cover the list
        # the user is renaming within. Hidden until the pen is pressed, and it
        # takes the title label's slot so the row does not change height.
        self._title_edit = QLineEdit(self.title)
        self._title_edit.setVisible(False)
        self._title_edit.returnPressed.connect(self._commit_rename)
        self._title_edit.editingFinished.connect(self._cancel_rename)
        text_col.addWidget(self._title_edit)

        self._time_label = QLabel(chat.get("timestamp", ""))
        text_col.addWidget(self._time_label)
        lay.addLayout(text_col, stretch=1)

        self._rename_btn = IconButton(
            "draw", size=26, icon_size=13, tooltip="Rename chat",
            tone="muted", hover_tone="accent",
        )
        self._rename_btn.clicked.connect(self.start_rename)
        lay.addWidget(self._rename_btn)

        # Muted at rest, red on hover: the control is on every row, so a
        # permanent red would make the list read as a column of warnings, but
        # it still has to say "this deletes" before you commit to it.
        self._delete_btn = IconButton(
            "trash", size=26, icon_size=13, tooltip="Delete chat",
            tone="muted", hover_tone="danger",
        )
        self._delete_btn.clicked.connect(self.delete_requested.emit)
        lay.addWidget(self._delete_btn)

        self._apply_style()

    def start_rename(self):
        """Swap the title for an editor, selected and focused."""
        self._title_edit.setText(self.title)
        self._title_label.setVisible(False)
        self._title_edit.setVisible(True)
        self._title_edit.selectAll()
        self._title_edit.setFocus()

    def _end_rename(self):
        self._title_edit.setVisible(False)
        self._title_label.setVisible(True)

    def _commit_rename(self):
        """Enter: ask for the rename, unless nothing changed."""
        new_title = self._title_edit.text().strip()
        self._end_rename()
        if not new_title or new_title == self.title:
            return
        # The row shows the new title straight away and the panel corrects it
        # if the server disagrees — a rename that waits for a round trip reads
        # as a click that did nothing.
        self.set_title(new_title)
        self.rename_requested.emit(new_title)

    def _cancel_rename(self):
        """Focus left the editor. Treated as a cancel, not a commit.

        editingFinished also fires on Enter, AFTER returnPressed has already
        hidden the editor — so this checks visibility and does nothing in that
        case, or every rename would be immediately followed by a cancel.
        """
        if self._title_edit.isVisible():
            self._end_rename()

    def set_title(self, title):
        self.title = title
        self._title_label.setText(title)

    def keyPressEvent(self, event):
        """Escape cancels an in-progress rename rather than closing anything."""
        if (event.key() == Qt_Key_Escape
                and self._title_edit.isVisible()):
            self._end_rename()
            return
        super().keyPressEvent(event)

    def _apply_style(self):
        t = design.tokens()
        if self._selected:
            bg, border = t.accent_soft, t.accent_soft_border
            title_color = t.accent_text
        else:
            bg, border = "transparent", "transparent"
            title_color = t.text
        # Hover is declared on the object-name selector so it never leaks to
        # other frames in the tree.
        self.setStyleSheet(f"""
            QFrame#chatRow {{
                background: {bg}; border: 1px solid {border};
                border-radius: {design.RADIUS}px;
            }}
            QFrame#chatRow:hover {{
                background: {t.accent_soft if self._selected else t.hover};
            }}
        """)
        self._title_label.setStyleSheet(
            f"background:transparent; border:none; color:{title_color}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_BASE}px; "
            f"font-weight:{design.WEIGHT_MEDIUM};"
        )
        self._time_label.setStyleSheet(
            f"background:transparent; border:none; color:{t.text_muted}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_XS}px;"
        )
        self._title_edit.setStyleSheet(
            design.input_qss(radius=design.RADIUS_SM, padding="2px 6px",
                             font_size=design.TEXT_BASE))

    def set_selected(self, selected):
        selected = bool(selected)
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_style()

    def mousePressEvent(self, event):
        # A click while renaming belongs to the editor, not to "open chat".
        if self._title_edit.isVisible():
            return
        # Only the left button selects; right-click is left alone so a future
        # context menu does not also open the chat.
        if event.button() == Qt_LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class HistoryPanel(QWidget):
    """The History view. Lists sessions, opens one, deletes one."""

    chat_selected = pyqtSignal(str, str)   # (chat_id, title)
    new_chat_requested = pyqtSignal()

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self._threads = []
        self._rows = []
        self._all_chats = []
        self._current_id = None

        t = design.tokens()
        paint_background(self, t.canvas, "historyPanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = styled_scroll_area()
        self._list_host = QWidget()
        paint_transparent(self._list_host)
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(
            design.SPACE_3, design.SPACE_3, design.SPACE_3, design.SPACE_3)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll, stretch=1)

        self._status = QLabel("")
        self._status.setAlignment(Qt_AlignCenter)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(design.body_qss(design.TEXT_BASE,
                                                   secondary=True))

    # ── loading ───────────────────────────────────────────────────────────────
    def load(self):
        """(Re)fetch the session list."""
        self._show_message("Loading chats…")
        thread = FetchChatsThread(self.client)
        thread.finished.connect(self._on_loaded)
        thread.error.connect(self._on_error)
        self._threads.append(thread)
        thread.start()

    def _on_loaded(self, chats):
        self._all_chats = chats
        self._render()

    def _on_error(self, message):
        debug_print(f"[GoPilot] History load failed: {message}")
        self._show_message("Could not load your chats.", retry=True)

    # ── rendering ─────────────────────────────────────────────────────────────
    def _clear_list(self):
        self._rows = []
        # Recursive, because the error state nests the retry button in a row
        # layout; a widgets-only sweep leaves it orphaned and floating over
        # the list. See widgets.clear_layout.
        clear_layout(self._list_layout)

    def _show_message(self, text, retry=False):
        """Replace the list with one centred line — loading / empty / error.

        ``retry=True`` adds a Try again button. That is the only reason this
        view needs a reload control at all: the list refetches every time the
        view is opened, so a permanent Refresh button sat there doing nothing
        99% of the time — but a failed load with no way to retry was a dead
        end.
        """
        self._clear_list()
        self._status = QLabel(text)
        self._status.setAlignment(Qt_AlignCenter)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(design.body_qss(design.TEXT_BASE,
                                                   secondary=True))
        self._list_layout.addStretch()
        self._list_layout.addWidget(self._status)
        if retry:
            row = QHBoxLayout()
            row.addStretch()
            button = secondary_button("Try again", icon_name="refresh")
            button.clicked.connect(self.load)
            row.addWidget(button)
            row.addStretch()
            self._list_layout.addLayout(row)
        self._list_layout.addStretch()

    def _render(self):
        if not self._all_chats:
            self._show_message(
                "No saved chats yet.\nStart a conversation and it will "
                "appear here."
            )
            return

        self._clear_list()
        for chat in self._all_chats:
            row = _ChatRow(chat)
            row.set_selected(chat.get("id") == self._current_id)
            row.clicked.connect(
                lambda c=chat: self.chat_selected.emit(
                    str(c.get("id")), c.get("title") or "Untitled chat")
            )
            row.delete_requested.connect(lambda c=chat: self._confirm_delete(c))
            row.rename_requested.connect(
                lambda title, c=chat, r=row: self._rename(c, r, title))
            self._rows.append(row)
            self._list_layout.addWidget(row)
        self._list_layout.addStretch()

    def set_current(self, chat_id):
        """Highlight ``chat_id`` as the open conversation."""
        self._current_id = str(chat_id) if chat_id else None
        for row in self._rows:
            row.set_selected(str(row.chat_id) == self._current_id)

    # ── deletion ──────────────────────────────────────────────────────────────
    def _confirm_delete(self, chat):
        title = chat.get("title") or "this chat"
        box = QMessageBox(self)
        box.setWindowTitle("Delete chat")
        box.setIcon(QMessageBox_Question)
        box.setText(f'Delete "{title}"?')
        box.setInformativeText("This cannot be undone.")
        box.setStandardButtons(QMessageBox_Yes | QMessageBox_No)
        box.setDefaultButton(QMessageBox_No)
        if exec_dialog(box) != QMessageBox_Yes:
            return

        chat_id = chat.get("id")
        thread = DeleteChatThread(self.client, chat_id)
        thread.finished.connect(lambda cid=chat_id: self._on_deleted(cid))
        thread.error.connect(
            lambda msg: QMessageBox.warning(
                self, "Delete failed", f"Could not delete the chat:\n{msg}")
        )
        self._threads.append(thread)
        thread.start()

    def _rename(self, chat, row, title):
        """Send a new title, and put the old one back if it is refused."""
        chat_id = chat.get("id")
        if not chat_id:
            return
        previous = chat.get("title") or ""
        chat["title"] = title

        thread = RenameChatThread(self.client, chat_id, title)
        thread.finished.connect(
            lambda accepted, c=chat, r=row: self._on_renamed(c, r, accepted))
        thread.error.connect(
            lambda msg, c=chat, r=row, old=previous:
            self._on_rename_failed(c, r, old, msg))
        self._threads.append(thread)
        thread.start()

    def _on_renamed(self, chat, row, title):
        """Adopt the server's title: it may have trimmed or truncated ours."""
        chat["title"] = title
        try:
            row.set_title(title)
        except RuntimeError:
            return      # the list was reloaded while the request was in flight
        if str(chat.get("id")) == str(self._current_id):
            self.chat_selected.emit(str(chat.get("id")), title)

    def _on_rename_failed(self, chat, row, previous, message):
        chat["title"] = previous
        try:
            row.set_title(previous)
        except RuntimeError:
            pass
        debug_print(f"[GoPilot] Rename failed: {message}")

    def _on_deleted(self, chat_id):
        self._all_chats = [c for c in self._all_chats
                           if c.get("id") != chat_id]
        if str(chat_id) == self._current_id:
            self._current_id = None
            self.new_chat_requested.emit()
        self._render()

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def cleanup(self):
        stop_threads(self._threads)
