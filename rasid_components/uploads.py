"""Which files may be uploaded, per the server's own limits.

WHY THE SERVER DECIDES
======================
The accepted extensions and the per-kind size ceilings live in the backend's
file-kinds table, and ``GET upload-limits/`` is the only place they are
correct. The plugin used to send whatever the user picked and let the upload
fail, so the only way to learn the real list was to be rejected by it — and the
rejection names one reason at a time, for one file at a time.

NO HARDCODED FALLBACK
=====================
Deliberately, and copied from the SaaS's reasoning: a stale local copy of this
list is the exact bug the endpoint exists to kill. While the limits are missing
— still loading, or the request failed — :func:`partition_uploads` accepts
everything and lets the server answer. A courtesy check that is sometimes
absent beats a wrong list that is always there.
"""

import os


def extension_of(path):
    """Lowercase extension without the dot, or ``""``."""
    name = os.path.basename(str(path or ""))
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def upload_kind(limits, path):
    """Which of the server's kinds ``path`` belongs to, or None.

    The ``kinds`` map is the only place the UI learns which extension is which
    kind, so this is a lookup rather than a guess based on the name.
    """
    extension = extension_of(path)
    if not extension or not isinstance(limits, dict):
        return None
    for kind, spec in (limits.get("kinds") or {}).items():
        if not isinstance(spec, dict):
            continue
        allowed = [str(e).lower().lstrip(".")
                   for e in (spec.get("extensions") or ())]
        if extension in allowed:
            return kind
    return None


def format_bytes(count):
    """A size a person can read: 1.5 MB, 900 kB, 12 bytes."""
    try:
        value = float(count)
    except (TypeError, ValueError):
        return "?"
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if value >= scale:
            sized = value / scale
            # One decimal below 10, none above: "1.5 MB" but "250 MB".
            return (f"{sized:.1f} {unit}" if sized < 10
                    else f"{sized:.0f} {unit}")
    return f"{int(value)} bytes"


def partition_uploads(paths, limits):
    """Split ``paths`` into ``(accepted, rejections)``.

    ``rejections`` is a list of ``(path, reason)``, one reason per file, so the
    caller can tell the user about every problem at once rather than one per
    round trip.

    With no limits, everything is accepted — see the module docstring.
    """
    paths = list(paths or ())
    if not isinstance(limits, dict) or not limits.get("kinds"):
        return paths, []

    accepted = []
    rejections = []
    for path in paths:
        kind = upload_kind(limits, path)
        if kind is None:
            extension = extension_of(path)
            rejections.append(
                (path, f"{('.' + extension) if extension else 'that type'} "
                       f"is not supported"))
            continue

        spec = (limits.get("kinds") or {}).get(kind) or {}
        maximum = spec.get("max_bytes")
        try:
            size = os.path.getsize(path)
        except OSError:
            # Unreadable here means the upload would fail anyway, but say so
            # in terms of the file rather than letting requests raise later.
            rejections.append((path, "could not be read"))
            continue

        if maximum and size > maximum:
            rejections.append(
                (path, f"too large for a {kind} file "
                       f"(max {format_bytes(maximum)}, "
                       f"this is {format_bytes(size)})"))
            continue
        accepted.append(path)

    return accepted, rejections


def accepted_extensions(limits):
    """Every extension the server accepts, for a file dialog's filter."""
    if not isinstance(limits, dict):
        return []
    extensions = [str(e).lower().lstrip(".")
                  for e in (limits.get("extensions") or ())]
    if extensions:
        return sorted(set(extensions))
    # Fall back to the union of the kinds, for a payload that omits the
    # flattened list.
    union = set()
    for spec in (limits.get("kinds") or {}).values():
        if isinstance(spec, dict):
            union.update(str(e).lower().lstrip(".")
                         for e in (spec.get("extensions") or ()))
    return sorted(union)


def file_dialog_filter(limits):
    """A Qt name filter for the upload picker, or a permissive default."""
    extensions = accepted_extensions(limits)
    if not extensions:
        return "All files (*)"
    patterns = " ".join(f"*.{e}" for e in extensions)
    return f"Supported files ({patterns});;All files (*)"
