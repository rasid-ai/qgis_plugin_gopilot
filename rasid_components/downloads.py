# -*- coding: utf-8 -*-
"""One download button, driven properly.

WHAT WAS WRONG
==============
Both places that download a result — the chat bubble's "Add X to QGIS" and
the processes pane's "Download result" — ran the same two bugs, because they
were the same code written twice:

1. **A one-way trip.** On success the button was disabled and relabelled
   "Added" / "Already loaded" and left that way for good. Removing the layer
   from QGIS did not bring the button back, so the only way to get the layer
   again was to navigate away and return, which rebuilt the pane. The user
   reported exactly that.

2. **An indefinite wait.** The label said "Downloading…" and nothing else
   until the file either arrived or failed. A 60-second timeout and a large
   GeoTIFF look identical to a stalled plugin.

WHAT THIS DOES INSTEAD
======================
The button is a small state machine — idle, downloading, added — and the
"added" state is not terminal:

* progress is reported per 8KB chunk, so the label counts up ("43%", or
  bytes when the server sends no Content-Length);
* the project is watched, and when the layer this button added is removed
  the button returns to idle by itself;
* clicking an added button adds it again, reusing the file already on disk
  rather than fetching it twice.

The caller supplies the wording and the "put this file in the project" step,
because those genuinely differ between the two panes — the chat knows about
SLD styling and names layers after the file, the processes pane names them
after the process. Everything else is here, once.
"""

import os

from qgis.PyQt.QtCore import QObject, pyqtSignal

from . import design, icons
from .chat_threads import DownloadLayerThread
from .logger import debug_print
from .uploads import format_bytes


def progress_text(prefix, done, total):
    """``"Downloading x… 43%"``, or bytes when the total is unknown.

    A percentage needs a Content-Length, and a chunked response has none —
    so rather than invent one, the byte count is shown. Both tell the user
    the download is moving, which is the point.
    """
    if total and total > 0:
        share = max(0, min(100, int(done * 100 / total)))
        return f"{prefix} {share}%"
    if done > 0:
        return f"{prefix} {format_bytes(done)}"
    return prefix


class DownloadButton(QObject):
    """Drives one button through download → add → (layer removed) → idle.

    ``add_layer(filepath)`` does the pane-specific work of putting the file
    into the project and must return the layer, or None. The layer is only
    needed to notice when the user removes it.
    """

    #: Emitted after a layer has been added, for callers that want to react.
    loaded = pyqtSignal(object)

    def __init__(self, button, client, url, add_layer,
                 idle_text, busy_text, done_text, filename=None,
                 threads=None, parent=None):
        super().__init__(parent)
        self.button = button
        self.client = client
        self.url = url
        # MUST be passed on to the download thread.
        #
        # A result URL usually ends in `/download/` with no extension, and
        # the saved file is then just "download". QGIS picks its provider
        # from the suffix, so it refuses the file outright:
        # "Cannot open ...\\rasid_download_xxxx\\download". The thread
        # restores the extension from this name, and omitting it here is
        # what broke every chat download — the one thing this controller was
        # meant to stop happening twice.
        self.filename = filename
        self._add_layer = add_layer
        self._idle_text = idle_text
        self._busy_text = busy_text
        self._done_text = done_text
        # Shared with the owning view so its cleanup() stops this download
        # along with everything else it started.
        self._threads = threads if threads is not None else []
        self._thread = None
        self._layer_id = None
        self._path = None
        self._closing = False
        # Whether this button has ever been actioned, by a click or by the
        # auto-load preference. Distinct from "is a layer in the project
        # right now": auto-load must fire ONCE per result, and must not fire
        # again after the user has deliberately removed the layer.
        self._has_run = False

        button.clicked.connect(self.start)
        self._watch_project()
        self.set_idle()

    # ── states ────────────────────────────────────────────────────────────────
    def set_idle(self):
        t = design.tokens()
        self.button.setText(self._idle_text)
        self.button.setIcon(icons.icon("download", design.ICON_SM, t.accent))
        self.button.setEnabled(True)
        self.button.setToolTip("")

    def _set_busy(self, text):
        self.button.setText(text)
        self.button.setEnabled(False)

    def _set_added(self):
        t = design.tokens()
        self.button.setText(self._done_text)
        self.button.setIcon(icons.icon("check", design.ICON_SM, t.success))
        # ENABLED on purpose. "Added" is a statement about what happened, not
        # a dead end: a user who deleted the layer by accident, or who wants a
        # second copy, can click again. The old behaviour disabled this and
        # left no way back except reopening the pane.
        self.button.setEnabled(True)
        self.button.setToolTip("Add it to QGIS again")

    def _set_retry(self, reason=""):
        self.button.setText(f"Retry {self._idle_text}"
                            if not self._idle_text.startswith("Retry")
                            else self._idle_text)
        self.button.setEnabled(True)
        if reason:
            self.button.setToolTip(reason)

    # ── the download ──────────────────────────────────────────────────────────
    def has_run(self):
        """True once this result has been fetched, or attempted, even once."""
        return self._has_run

    def mark_loaded(self, layer_name):
        """Show the loaded state for a layer added in an earlier session.

        A pane reopened later has no controller history, but the layer may
        well still be in the project — so the button is set to match what
        the user can see in their layer tree. It stays clickable, and if
        they remove that layer the usual watch returns it to idle.
        """
        try:
            from qgis.core import QgsProject
            for layer in QgsProject.instance().mapLayers().values():
                if layer.name() == layer_name:
                    self._layer_id = layer.id()
                    break
        except Exception as exc:          # pragma: no cover
            debug_print(f"[GoPilot] Could not match an existing layer: {exc}")
        self._has_run = True
        try:
            self._set_added()
        except RuntimeError:
            self._closing = True

    def start(self):
        if self._closing or self.client is None:
            return
        self._has_run = True

        # Already on disk from a previous click: skip the network entirely.
        # Results are immutable, so the bytes cannot have changed, and this
        # makes "add it again" instant instead of a second download.
        if self._path and os.path.exists(self._path):
            self._set_busy(self._busy_text)
            self._install(self._path)
            return

        self._set_busy(self._busy_text)
        thread = DownloadLayerThread(self.client, self.url, self.filename)
        thread.progress.connect(self._on_progress)
        thread.finished.connect(self._on_downloaded)
        thread.error.connect(self._on_error)
        self._thread = thread
        self._threads.append(thread)
        thread.start()

    def _on_progress(self, done, total):
        if self._closing:
            return
        try:
            self._set_busy(progress_text(self._busy_text, done, total))
        except RuntimeError:
            self._closing = True        # the button has gone

    def _on_downloaded(self, filepath):
        if self._closing:
            return
        self._path = filepath
        self._install(filepath)

    def _install(self, filepath):
        try:
            layer = self._add_layer(filepath)
        except Exception as exc:
            debug_print(f"[GoPilot] Could not add the layer: {exc}")
            try:
                self._set_retry(str(exc))
            except RuntimeError:
                self._closing = True
            return
        try:
            self._layer_id = layer.id() if layer is not None else None
        except (AttributeError, RuntimeError):
            self._layer_id = None
        try:
            self._set_added()
        except RuntimeError:
            self._closing = True
            return
        self.loaded.emit(layer)

    def _on_error(self, message):
        if self._closing:
            return
        try:
            self._set_retry(message)
        except RuntimeError:
            self._closing = True

    # ── noticing that the layer went ─────────────────────────────────────────
    def _watch_project(self):
        """Return to idle when the layer this button added is removed.

        Without this the button keeps claiming "Added" for a layer that is no
        longer in the project, and refuses to fetch it again.
        """
        try:
            from qgis.core import QgsProject
            QgsProject.instance().layersRemoved.connect(self._on_layers_gone)
        except Exception as exc:          # pragma: no cover
            debug_print(f"[GoPilot] Could not watch layer removals: {exc}")

    def _on_layers_gone(self, layer_ids):
        if self._closing or not self._layer_id:
            return
        if self._layer_id not in (layer_ids or ()):
            return
        self._layer_id = None
        try:
            self.set_idle()
        except RuntimeError:
            self._closing = True

    # ── teardown ─────────────────────────────────────────────────────────────
    def cleanup(self):
        """Stop talking to the UI and let go of the project signal."""
        self._closing = True
        try:
            from qgis.core import QgsProject
            QgsProject.instance().layersRemoved.disconnect(
                self._on_layers_gone)
        except (TypeError, RuntimeError, Exception):
            pass
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        for signal in ("progress", "finished", "error"):
            try:
                getattr(thread, signal).disconnect()
            except (TypeError, RuntimeError):
                pass
