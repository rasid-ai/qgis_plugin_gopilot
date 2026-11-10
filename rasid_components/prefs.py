"""Typed, persisted user preferences.

Thin wrapper over ``QSettings`` so the plugin's options live in the QGIS
profile (and survive a restart) without every caller repeating the key string
and the string-to-bool dance.

WHY THE BOOL HELPER EXISTS
==========================
``QSettings`` on Windows stores values in the registry and hands booleans back
as the STRINGS ``"true"``/``"false"``. ``bool("false")`` is ``True``, so a
naive read turns every switched-off setting back on. :func:`get_bool` parses
the value properly and is the only way bools should be read here.

KEYS
====
All keys are namespaced ``rasid_plugin/<name>``, matching the existing
credential keys (``rasid_plugin/email``) so a user clearing the plugin's
settings clears all of it at once.
"""

from qgis.PyQt.QtCore import QSettings

from .logger import debug_print

_NS = "rasid_plugin"

# ── keys, with their defaults ────────────────────────────────────────────────
# Map & layers
LAYER_GROUP_ENABLED = "layer_group_enabled"
LAYER_GROUP_NAME = "layer_group_name"
ZOOM_TO_LAYER = "zoom_to_layer"
AUTO_LOAD_LAYERS = "auto_load_layers"
# Chat
SHOW_TIMESTAMPS = "show_timestamps"
SEND_ON_ENTER = "send_on_enter"

DEFAULTS = {
    LAYER_GROUP_ENABLED: True,
    LAYER_GROUP_NAME: "GoPilot",
    ZOOM_TO_LAYER: True,
    # Off by default on purpose: silently adding layers to someone's project
    # the moment an answer arrives is a side effect on THEIR document, which
    # should be opted into, not out of.
    AUTO_LOAD_LAYERS: False,
    SHOW_TIMESTAMPS: True,
    SEND_ON_ENTER: True,
}


def _full_key(name):
    return f"{_NS}/{name}"


def get_bool(name):
    """Read a boolean preference, tolerating the string forms QSettings returns."""
    default = DEFAULTS.get(name, False)
    raw = QSettings().value(_full_key(name), default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(raw, (int, float)):
        return bool(raw)
    return bool(default)


def get_str(name):
    """Read a string preference, falling back to the default when blank."""
    default = DEFAULTS.get(name, "")
    value = QSettings().value(_full_key(name), default)
    text = str(value).strip() if value is not None else ""
    return text or str(default)


def set_value(name, value):
    """Persist a preference. Booleans are stored as real bools where possible."""
    try:
        QSettings().setValue(_full_key(name), value)
    except Exception as exc:
        # A read-only registry/ini should degrade to "setting does not stick",
        # never to a traceback in the user's face.
        debug_print(f"[RASID] Could not save preference {name}: {exc}")


def reset(name):
    """Remove a preference so its default applies again."""
    try:
        QSettings().remove(_full_key(name))
    except Exception as exc:
        debug_print(f"[RASID] Could not reset preference {name}: {exc}")
