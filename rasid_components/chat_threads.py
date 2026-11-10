"""Background workers for the GoPilot chat API.

Everything that touches the network lives here, off the GUI thread, because a
blocking request in a QGIS plugin freezes the whole application window — not
just the plugin panel.

These used to sit inside ``gopilot_page.py``. They were pulled out when the
chat transcript and the chat HISTORY became two separate views in the dock:
both need to list and delete sessions, and the alternative was one view
importing the other just to borrow a QThread.

CONTRACT
========
Every worker emits exactly one of ``finished`` / ``error`` and then ends. Errors
arrive as a human-readable string, already enriched with the HTTP response body
where the server sent one, so a caller can put it straight in front of the user.

OWNERSHIP
=========
A ``QThread`` that goes out of scope while running takes the interpreter down
with it ("QThread: Destroyed while thread is still running"). So callers MUST
keep a reference for the thread's lifetime — every view here appends to a
``self._threads`` list and drains it in ``cleanup()``.
"""

import json
import os
import tempfile
import time
import traceback

from qgis.PyQt.QtCore import QThread, pyqtSignal

from .logger import debug_print

# How long to wait for an answer: the task is polled every POLL_INTERVAL
# seconds, up to MAX_POLL_ATTEMPTS times (so ~2 minutes by default). A GoPilot
# turn that runs a raster job legitimately takes tens of seconds.
POLL_INTERVAL = 2
MAX_POLL_ATTEMPTS = 60

# Between two ``<think>`` blocks: a paragraph break, and nothing else.
#
# A deliberate difference from the SaaS, which puts a markdown rule (`---`)
# here. A rule renders with a 10px margin above and below, so each separator
# cost about 21px of the reasoning panel's ~61px visible height — a third of
# the box, to draw one hairline. That is a fair trade in the SaaS's full-width
# panel and a poor one in a 192x88 box beside the tool list.
#
# The blank line still separates the passes: markdown turns it into separate
# paragraphs, so they read as distinct without spending the height.
THINKING_BLOCK_SEPARATOR = "\n\n"


def _describe_error(exc):
    """Turn an exception into the most specific message we can show a user.

    ``requests`` puts the server's explanation in the response BODY, not in
    ``str(exc)`` — which is just "400 Client Error" — so a bare ``str()`` loses
    the only part the user could act on.
    """
    message = str(exc)
    response = getattr(exc, "response", None)
    if response is None:
        return message
    try:
        return f"{message}\nDetails: {response.json()}"
    except Exception:
        try:
            return f"{message}\nResponse: {response.text[:200]}"
        except Exception as detail_exc:
            debug_print(f"[GoPilot] Unreadable error body: {detail_exc}")
            return message


def thinking_text(raw):
    """Normalise ``thinking_content`` to one string, or None.

    The worker writes one entry per ``<think>`` block, so this arrives as a
    LIST of strings; older rows carry a bare string or nothing at all.

    Blocks are joined with ``THINKING_BLOCK_SEPARATOR`` — see there for why
    that is a plain paragraph break rather than the rule the SaaS uses.
    """
    if not raw:
        return None
    if isinstance(raw, (list, tuple)):
        blocks = [str(block).strip() for block in raw if str(block).strip()]
        return THINKING_BLOCK_SEPARATOR.join(blocks) or None
    text = str(raw).strip()
    return text or None


def _require_gopilot(client):
    """Fail early and clearly when the client was never authenticated."""
    if client is None or not getattr(client, "gopilot", None):
        raise RuntimeError(
            "GoPilot client not initialized. Check your API key."
        )
    return client.gopilot


class SendMessageThread(QThread):
    """Create or continue a session, then poll the task until it answers.

    STREAMING, SUCH AS IT IS
    ========================
    There is no socket and no SSE: the API streams by letting you re-read the
    task. ``GET sessions/tasks/{id}/`` carries an ``llm_message`` that GROWS
    between polls — its ``content``, ``thinking_content`` and
    ``tool_calls_content`` all fill in as the worker writes them — so polling
    it every couple of seconds and re-rendering is what the SaaS calls
    streaming, and :attr:`partial` is this thread's half of that.

    This used to read ``llm_message`` only once, on ``status == "completed"``,
    and then keep nothing but ``content``. Everything the model did on the way
    — its reasoning, which tools it ran, the files it produced — was fetched
    and thrown away on every single poll, and the panel showed a static
    "thinking…" line for the whole turn and then the finished answer in one
    jump.
    """

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    # The in-flight assistant message, as it stands at this poll. Emitted on
    # every poll where anything renderable changed.
    partial = pyqtSignal(dict)

    def __init__(self, client, message, session_id=None, input_metadata=None,
                 geojson_data=None, files=None):
        super().__init__()
        self.client = client
        self.message = message
        self.session_id = session_id
        self.input_metadata = input_metadata
        self.geojson_data = geojson_data
        self.files = list(files) if files else None

    # ── helpers ───────────────────────────────────────────────────────────────
    def _collect_uploads(self):
        """Paths to upload: the caller's files plus any attached geometry."""
        uploads = list(self.files) if self.files else []
        if self.geojson_data:
            # A unique name per send: a fixed "attached_geometry.geojson" in the
            # temp dir is overwritten by the next message, so two sends racing
            # (or a retry) uploaded whichever geometry won the write.
            handle, path = tempfile.mkstemp(
                prefix="gopilot_aoi_", suffix=".geojson")
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(self.geojson_data, fh, indent=2)
            uploads.append(path)
            debug_print(f"[GoPilot] Attaching geometry: {path}")
        return uploads or None

    def _start_turn(self, gopilot, uploads):
        """Open or continue the conversation; return the API's response dict."""
        if not self.session_id:
            self.progress.emit("Creating new chat session…")
            result = gopilot.create_session(
                title="New Chat",
                content=self.message,
                input_metadata=self.input_metadata,
                files=uploads,
            )
            debug_print(f"[GoPilot] Create-session response: {result}")
            if not isinstance(result, dict):
                raise RuntimeError(f"Invalid session response: {type(result)}")
            # The create endpoint already QUEUES this first message. Sending it
            # again here would double every opening turn.
            self.session_id = result.get("session_id") or result.get("id")
            if not self.session_id:
                raise RuntimeError(
                    f"Create-session response has no session ID: {result}")
            return result

        self.progress.emit("Sending message…")
        result = gopilot.send_message(
            session_id=self.session_id,
            content=self.message,
            input_metadata=self.input_metadata,
            files=uploads,
        )
        debug_print(f"[GoPilot] Send-message response: {result}")
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected response type: {type(result)}")
        return result

    def _await_task(self, gopilot, task_id):
        """Poll ``task_id`` to completion, emitting the answer as it grows.

        Returns the final ``llm_message`` dict — not just its text, which is
        what this returned before and the reason the thinking panel, the tool
        list and the message's files had nothing to render from.
        """
        self.progress.emit("GoPilot is thinking…")
        last_signature = None
        latest = None

        for _ in range(MAX_POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL)
            if self.isInterruptionRequested():
                return None
            try:
                status = gopilot.get_task_status(task_id)
            except Exception as poll_error:
                # A single dropped poll is not a failed task — keep waiting.
                debug_print(f"[GoPilot] Polling error: {poll_error}")
                continue

            state = status.get("status", "unknown")
            pct = status.get("progress", 0)
            if pct:
                self.progress.emit(f"GoPilot is thinking… {pct}%")

            message = status.get("llm_message")
            if isinstance(message, dict):
                latest = message
                # Only emit when something a reader would SEE has changed.
                # A poll that returns an identical body still arrives every
                # couple of seconds, and re-rendering rich text on each one
                # resets the caret and costs a relayout for no visible gain.
                signature = (
                    message.get("content") or "",
                    thinking_text(message.get("thinking_content")),
                    len(message.get("tool_calls_content") or ()),
                    tuple(sorted(
                        (call.get("id"), call.get("name"), call.get("status"))
                        for call in (message.get("tool_calls_content") or ())
                        if isinstance(call, dict)
                    )),
                    len(message.get("files") or ()),
                )
                if signature != last_signature:
                    last_signature = signature
                    self.partial.emit(message)

            if state == "completed":
                return latest or {}
            if state == "failed":
                raise RuntimeError(
                    f"GoPilot failed: "
                    f"{status.get('error_message') or status.get('error') or 'Task failed'}")
        return None

    def _last_assistant_message(self, gopilot):
        """Fall back to reading the transcript when polling timed out.

        Returns the whole message, not just its text, so a turn recovered this
        way renders with the same reasoning, tools and files as one that
        arrived through the poll loop.
        """
        for msg in reversed(gopilot.get_messages(self.session_id) or []):
            if msg.get("role") == "assistant" or msg.get("is_user") is False:
                if msg.get("content") or msg.get("files"):
                    return msg
        return None

    # ── entry point ───────────────────────────────────────────────────────────
    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            debug_print(f"[GoPilot] Sending: {self.message[:100]}…")

            uploads = self._collect_uploads()
            result = self._start_turn(gopilot, uploads)

            task_info = result.get("task", {})
            task_id = (task_info.get("task_id")
                       if isinstance(task_info, dict) else None)
            if not task_id:
                raise RuntimeError("No task_id in response")

            message = self._await_task(gopilot, task_id)
            if self.isInterruptionRequested():
                return
            if not message:
                message = self._last_assistant_message(gopilot)
            if not message:
                raise RuntimeError(
                    "GoPilot timed out. The answer may still arrive — reopen "
                    "this chat from History in a moment."
                )

            self.finished.emit({
                "session_id": self.session_id,
                "task_id": task_id,
                # `message` stays the plain text, because that is what every
                # existing caller reads; `llm_message` carries the rest of the
                # turn beside it.
                "message": message.get("content", ""),
                "llm_message": message,
            })
        except Exception as exc:
            debug_print(f"[GoPilot] SendMessageThread: {traceback.format_exc()}")
            self.error.emit(_describe_error(exc))


class FetchChatsThread(QThread):
    """List the user's chat sessions, newest first as the API returns them."""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            history = gopilot.get_session_history()
            chats = [
                {
                    "id": session.get("id"),
                    "title": session.get("title") or "Untitled chat",
                    # "2026-06-27T21:33:58.818Z" -> "2026-06-27 21:33"
                    "timestamp": (session.get("created_at", "")[:16]
                                  .replace("T", " ")),
                }
                for session in history.get("results", [])
            ]
            self.finished.emit(chats)
        except Exception as exc:
            self.error.emit(_describe_error(exc))


class FetchMessagesThread(QThread):
    """Load the full transcript of one session."""

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, client, session_id):
        super().__init__()
        self.client = client
        self.session_id = session_id

    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            self.finished.emit(gopilot.get_messages(self.session_id) or [])
        except Exception as exc:
            self.error.emit(_describe_error(exc))


class HideMessageThread(QThread):
    """Move one exchange to the trash.

    ``finished`` carries the ids the server actually moved. The backend takes
    the whole turn, so asking it to trash a reply moves the prompt with it —
    which is why the caller needs the list rather than assuming it got the one
    id it sent.
    """

    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, client, message_id):
        super().__init__()
        self.client = client
        self.message_id = message_id

    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            result = gopilot.hide_message(self.message_id) or {}
            moved = result.get("trashed_ids")
            if not isinstance(moved, list) or not moved:
                # Older builds answer with no id list; the one we asked for is
                # the only thing we can be sure about.
                moved = [self.message_id]
            self.finished.emit([int(i) for i in moved
                                if isinstance(i, (int, float, str))
                                and str(i).lstrip("-").isdigit()])
        except Exception as exc:
            self.error.emit(_describe_error(exc))


class RenameChatThread(QThread):
    """Set a chat's title."""

    finished = pyqtSignal(str)      # the title the server accepted
    error = pyqtSignal(str)

    def __init__(self, client, session_id, title):
        super().__init__()
        self.client = client
        self.session_id = session_id
        self.title = title

    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            result = gopilot.update_session(
                self.session_id, title=self.title) or {}
            # Echo the server's title rather than ours: it may trim, truncate
            # or otherwise normalise what it stored.
            self.finished.emit(str(result.get("title") or self.title))
        except Exception as exc:
            self.error.emit(_describe_error(exc))


class FetchUploadLimitsThread(QThread):
    """What may be uploaded. Fails quietly — see :mod:`.uploads`."""

    finished = pyqtSignal(dict)

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            self.finished.emit(gopilot.get_upload_limits() or {})
        except Exception as exc:
            debug_print(f"[GoPilot] Upload limits unavailable: {exc}")
            self.finished.emit({})


class FetchPromptSuggestionsThread(QThread):
    """Draw the empty chat's starter prompts from the admin-configured set.

    Fails quietly. These are a nicety on a screen that must render instantly
    and offline — the page keeps a static set to fall back on, so an error
    here is an unremarkable "use the built-in openers", not something to show
    the user.
    """

    finished = pyqtSignal(list)

    def __init__(self, client, limit=6):
        super().__init__()
        self.client = client
        self.limit = limit

    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            self.finished.emit(
                gopilot.get_prompt_suggestions(limit=self.limit) or [])
        except Exception as exc:
            debug_print(f"[GoPilot] Prompt suggestions unavailable: {exc}")
            self.finished.emit([])


class DeleteChatThread(QThread):
    """Delete one session server-side."""

    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, client, session_id):
        super().__init__()
        self.client = client
        self.session_id = session_id

    def run(self):
        try:
            gopilot = _require_gopilot(self.client)
            gopilot.delete_session(self.session_id)
            self.finished.emit()
        except Exception as exc:
            self.error.emit(_describe_error(exc))


class DownloadLayerThread(QThread):
    """Download one result file to a fresh temp directory."""

    finished = pyqtSignal(str)  # local path
    error = pyqtSignal(str)

    def __init__(self, client, url, filename=None):
        super().__init__()
        self.client = client
        self.url = url
        self.filename = filename

    def run(self):
        try:
            path = self.client.download_file(
                self.url, tempfile.mkdtemp(prefix="rasid_download_"))
            # Presigned URLs often have no extension, and QGIS picks its
            # provider from the suffix — so restore it from the link's label.
            if self.filename:
                extension = os.path.splitext(self.filename)[1]
                if extension and not path.lower().endswith(extension.lower()):
                    os.replace(path, path + extension)
                    path += extension
            self.finished.emit(path)
        except Exception as exc:
            self.error.emit(_describe_error(exc))


def stop_threads(threads, timeout_ms=1500):
    """Ask every thread in ``threads`` to stop, then wait briefly for each.

    Called from each view's ``cleanup()``. ``requestInterruption`` is what lets
    :class:`SendMessageThread` leave its polling loop early instead of sitting
    in ``sleep`` until the timeout expires. Exceptions are swallowed on purpose:
    this runs during teardown, where a half-destroyed thread is expected and
    raising would leave the rest of the list running.
    """
    for thread in threads:
        if thread is None:
            continue
        try:
            if thread.isRunning():
                thread.requestInterruption()
                thread.quit()
                thread.wait(timeout_ms)
        except (RuntimeError, AttributeError) as exc:
            debug_print(f"[GoPilot] Error stopping thread: {exc}")
    threads.clear()
