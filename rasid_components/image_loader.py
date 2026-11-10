"""Shared async image loader that uses the authenticated session."""
import requests
import hashlib
import os

from qgis.PyQt.QtWidgets import QLabel
from qgis.PyQt.QtCore import QRectF
from qgis.PyQt.QtGui import QPainter, QPainterPath, QPixmap
from qgis.PyQt.QtCore import QThread, pyqtSignal
from .compat import (
    QPainter_Antialiasing, Qt_KeepAspectRatio,
    Qt_KeepAspectRatioByExpanding, Qt_SmoothTransformation, Qt_transparent,
)
from .config import API_HOST, is_trusted_api_url
from .logger import debug_print

BASE_URL = API_HOST

# Disk cache directory inside the plugin folder
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".image_cache")


def _cache_path(url):
    """Return a filesystem path for the cached version of *url*."""
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    ext = os.path.splitext(url.split("?")[0])[1] or ".img"
    return os.path.join(_CACHE_DIR, url_hash + ext)


def _read_cache(url):
    """Return cached bytes or None."""
    path = _cache_path(url)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def _write_cache(url, data):
    """Persist *data* to disk for *url*."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_path(url)
    with open(path, "wb") as f:
        f.write(data)


def resolve_url(url):
    """Turn a relative /media/... path into a full URL."""
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    # Relative path — prepend base
    if url.startswith("/"):
        return BASE_URL + url
    return BASE_URL + "/" + url


class FetchImageThread(QThread):
    finished = pyqtSignal(QLabel, bytes)

    def __init__(self, session, url, label):
        super().__init__()
        self.session = session
        self.url = resolve_url(url)
        self.label = label

    def run(self):
        if not self.url:
            return
        try:
            # Check disk cache first
            cached = _read_cache(self.url)
            if cached:
                self.finished.emit(self.label, cached)
                return
            # Download and cache. The authenticated session only for our own
            # hosts: a thumbnail URL can be a presigned storage link, and
            # using the session unconditionally sent the user's bearer token
            # to whichever third party the API happened to name.
            if is_trusted_api_url(self.url):
                r = self.session.get(self.url, timeout=15)
            else:
                r = requests.get(self.url, timeout=15)
            if r.status_code == 200 and r.content:
                _write_cache(self.url, r.content)
                self.finished.emit(self.label, r.content)
            else:
                debug_print(f"[image_loader] Thread: Failed - status {r.status_code}, content length {len(r.content) if r.content else 0}")
        except Exception as e:
            debug_print(f"[image_loader] Thread: Exception - {type(e).__name__}: {e}")


def scale_to_cover(pixmap, width, height):
    """Scale ``pixmap`` to FILL a ``width`` x ``height`` box, cropping the rest.

    The equivalent of CSS ``object-fit: cover``, and the behaviour a card
    thumbnail needs. ``KeepAspectRatio`` — the other mode — fits the image
    INSIDE the box, so anything whose aspect ratio differs from the box is
    letterboxed: a near-square solution image in a 16:10 card thumbnail sat
    centred with a bar of empty grey down either side, while a wide one filled
    the card properly. Two cards in the same grid, looking like two different
    designs, depending on the source image.

    Expanding first and then cropping to the centre means every thumbnail fills
    its card edge to edge whatever shape it arrives in. The cost is that the
    edges of a very tall or very wide image are cut, which for a decorative
    thumbnail is the right trade — it is not the image's only showing, the
    detail view presents it whole.
    """
    if pixmap.isNull() or width <= 0 or height <= 0:
        return pixmap
    scaled = pixmap.scaled(width, height, Qt_KeepAspectRatioByExpanding,
                           Qt_SmoothTransformation)
    # Centre the crop: the subject of an aerial or satellite image is far more
    # often in the middle than against an edge.
    left = max(0, (scaled.width() - width) // 2)
    top = max(0, (scaled.height() - height) // 2)
    return scaled.copy(left, top, width, height)


def round_pixmap(pixmap, radius, top_only=False):
    """Clip ``pixmap`` to rounded corners.

    A stylesheet ``border-radius`` on a QLabel rounds its BACKGROUND, not its
    pixmap — the image is content and is drawn straight over the rounded
    corners. So a card thumbnail had square top corners as soon as its image
    arrived, while the empty placeholder underneath was correctly rounded.
    The mask has to be applied to the image itself.

    ``top_only`` leaves the bottom square, which is what a thumbnail sitting
    at the top of a card needs: its lower edge meets the card's content and
    should stay flush.
    """
    if pixmap.isNull() or radius <= 0:
        return pixmap
    width, height = pixmap.width(), pixmap.height()
    out = QPixmap(width, height)
    out.fill(Qt_transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter_Antialiasing, True)
        path = QPainterPath()
        if top_only:
            path.moveTo(0, height)
            path.lineTo(0, radius)
            path.quadTo(0, 0, radius, 0)
            path.lineTo(width - radius, 0)
            path.quadTo(width, 0, width, radius)
            path.lineTo(width, height)
            path.closeSubpath()
        else:
            path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, pixmap)
    finally:
        painter.end()
    out.setDevicePixelRatio(pixmap.devicePixelRatio())
    return out


def load_image(client, url, label, threads_list, size=None, fit="contain",
               radius=0, round_top_only=False):
    """Start an async image load into a QLabel.

    Args:
        client: RasidClient with authenticated session
        url: image URL (absolute or relative)
        label: target QLabel
        threads_list: list to keep thread references alive
        size: optional (w, h) tuple to scale the result
        fit: ``"contain"`` (default) fits the whole image inside the box,
            letterboxing if the aspect ratios differ — right for a detail
            view, where the point is to see all of the result.
            ``"cover"`` fills the box and crops the overflow — right for a
            card or list thumbnail, where an even grid matters more than
            seeing every pixel. See :func:`scale_to_cover`.
    """
    resolved = resolve_url(url)
    if not resolved:
        return

    def on_loaded(lbl, data):
        pm = QPixmap()
        pm.loadFromData(data)
        width, height = size if size else (lbl.width(), lbl.height())
        if fit == "cover":
            pm = scale_to_cover(pm, width, height)
        else:
            pm = pm.scaled(width, height, Qt_KeepAspectRatio,
                           Qt_SmoothTransformation)
        if radius:
            pm = round_pixmap(pm, radius, top_only=round_top_only)
        try:
            lbl.setPixmap(pm)
        except RuntimeError:
            # The target label was destroyed while this download was in
            # flight — a card grid relaying out mid-request does exactly
            # that. Nothing to paint into, so drop it rather than raise into
            # Qt's event loop.
            debug_print("[image_loader] target label gone before paint")

    t = FetchImageThread(client.session, url, label)
    t.finished.connect(on_loaded)
    threads_list.append(t)
    t.start()
