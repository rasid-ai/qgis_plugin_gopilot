"""Offscreen smoke test for the GoPilot dock UI.

Builds every widget the refactor introduces, renders each registered icon, and
grabs a PNG of the shell — without a running QGIS. It exists because the only
other way to find out that a stylesheet selector is malformed or that a Qt6
enum moved is to restart QGIS and click through the plugin by hand.

Run it against BOTH installed QGIS versions (Qt5 and Qt6) before shipping:

    "C:/Program Files/QGIS 3.36.3/bin/python-qgis.bat" scripts/smoke_ui.py
    "C:/Program Files/QGIS 4.0.1/bin/python-qgis.bat"  scripts/smoke_ui.py

Pass ``--out DIR`` to write icon-sheet and shell screenshots there.

A note on reading those screenshots: Windows renders text with SUBPIXEL
antialiasing, and ``QWidget.grab()`` captures it. Zoom into any small glyph —
especially digits — and you will find orange, magenta and blue pixels along its
strokes. That is ClearType fringing, present in body text and list markers
alike; it is not a colour someone set. Sample the same text in two places
before concluding an element is mis-coloured.
"""

import argparse
import os
import sys
import traceback

# The plugin is imported as a package from its parent directory, the same way
# QGIS loads it, so the relative imports inside it resolve.
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(PLUGIN_DIR))
PKG = os.path.basename(PLUGIN_DIR)

# NOT the offscreen platform by default. On Windows the offscreen plugin has no
# real font database, so every widget renders with zero-width glyphs: the
# screenshots come out structurally correct and COMPLETELY TEXTLESS, which
# looks exactly like a styling bug and is not one. The native platform renders
# text properly, and `QWidget.grab()` does not need the window to be visible.
# Override with QT_QPA_PLATFORM=offscreen if running somewhere headless.
if not os.environ.get("QT_QPA_PLATFORM"):
    if not sys.platform.startswith("win"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

from qgis.PyQt.QtCore import QT_VERSION_STR  # noqa: E402
from qgis.PyQt.QtGui import QColor, QPainter, QPalette, QPixmap  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402

failures = []


def settle(app, passes=8):
    """Let the layout converge before measuring or grabbing.

    Self-sizing widgets (the wrapped setting descriptions, the growing
    composer) pin a new minimum height from their resizeEvent, which schedules
    another layout pass. One processEvents() captures the frame BEFORE that
    second pass lands, so a screenshot taken then shows text overflowing its
    row — a test artifact that looks exactly like a layout bug.
    """
    for _ in range(passes):
        app.processEvents()


AI_REPLY = """Here is what I found for the area you drew.

**Land cover, 2026**

| Class | Area (km²) | Share |
| --- | --- | --- |
| Cropland | 412.6 | 48% |
| Urban | 180.1 | 21% |
| Water | 94.3 | 11% |

The change since 2020 is mostly cropland turning urban along the north edge.

1. Reprojected your layer to EPSG:3857
2. Sampled Sentinel-2 L2A, cloud cover under 10%
3. Classified with the regional model

```python
stats = zonal_stats(aoi, "landcover_2026.tif")
```
"""


def demo_conversation(shell, app):
    """Fill the chat with one realistic turn and show every transient state.

    The empty state is what a screenshot of a fresh panel shows, so without
    this the bubbles, the markdown rendering, the attachment chips and the
    status strip are never actually looked at — which is where the layout bugs
    live.
    """
    page = shell.chat_page
    shell.show_view("chat")
    page._remove_empty_state()
    page.add_message(
        "Summarise land cover for this area and compare it with 2020",
        is_user=True, timestamp="09:41",
        attachments=["Area attached", "districts.geojson"],
    )
    page.add_message(AI_REPLY, is_user=False, timestamp="09:42")
    page._add_system_message("**Something went wrong**\n\nRequest timed out.",
                             tone="error")
    page.composer.add_file("/tmp/sentinel_tile.tif")
    page.composer.set_geometry({"type": "FeatureCollection", "features": []},
                               "Drawn area")
    page.composer.input.setPlainText(
        "And clip it to the municipal boundary please")
    page._set_status("GoPilot is thinking… 60%")
    settle(app)
    bar = page._scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    settle(app)


def apply_palette(app, dark):
    """Install a light or dark application palette.

    The plugin decides which theme it is in from the luminance of the palette's
    Window colour (``design.is_dark``), so setting that colour here is enough
    to exercise both code paths without a running QGIS.
    """
    palette = QPalette()
    window = QColor("#201D1F") if dark else QColor("#F1EEEF")
    text = QColor("#F1EEEF") if dark else QColor("#322E30")
    base = QColor("#141213") if dark else QColor("#FFFFFF")
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, window)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    app.setPalette(palette)


def check(label, fn):
    """Run one step, record the traceback instead of aborting the whole run."""
    try:
        result = fn()
        print(f"  ok   {label}")
        return result
    except Exception:
        failures.append((label, traceback.format_exc()))
        print(f"  FAIL {label}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="write screenshots here")
    args = ap.parse_args()

    app = QApplication(sys.argv[:1])
    print(f"Qt {QT_VERSION_STR}  |  platform {app.platformName()}")

    design = __import__(f"{PKG}.rasid_components.design", fromlist=["x"])
    icons = __import__(f"{PKG}.rasid_components.icons", fromlist=["x"])

    # Put the plugin's bundled site-packages on sys.path, exactly as the plugin
    # itself does on load. Without this, importing a view fails on `markdown`,
    # which ships in the per-Python-version venv rather than in QGIS.
    venv_manager = __import__(
        f"{PKG}.rasid_components.dependencies.venv_manager", fromlist=["x"])
    if not venv_manager.ensure_venv_packages_available():
        missing = venv_manager.get_missing_packages()
        print(f"  !! missing packages for this interpreter: "
              f"{', '.join(missing) or 'unknown'}")
        print(f"     expected in {venv_manager.get_venv_dir()}")
        print("     widget checks will fail until they are installed")

    print("\n[tokens]")
    check("light tokens", lambda: design.Tokens(False).accent)
    check("dark tokens", lambda: design.Tokens(True).accent)
    check("tokens() resolves", lambda: design.tokens().surface)
    for name in ("scrollbar_qss", "primary_button_qss", "secondary_button_qss",
                 "ghost_button_qss", "danger_button_qss", "input_qss",
                 "menu_qss", "list_qss"):
        check(f"design.{name}()", getattr(design, name))
    check("card_qss", lambda: design.card_qss("x"))
    check("chip_qss tones",
          lambda: [design.chip_qss(t) for t in
                   ("accent", "neutral", "success", "warning", "error")])

    print("\n[icons]")
    names = icons.available()
    for name in names:
        check(
            f"icon {name}",
            lambda n=name: icons.pixmap(n, 18, "#008B6A", dpr=2.0),
        )

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        # One contact sheet of every icon, so a broken path is visible rather
        # than merely not raising.
        cols, cell = 8, 48
        rows = (len(names) + cols - 1) // cols
        sheet = QPixmap(cols * cell, rows * cell)
        sheet.fill(QColor("#F1EEEF"))
        p = QPainter(sheet)
        for i, name in enumerate(names):
            pm = icons.pixmap(name, 28, "#322E30", dpr=2.0)
            x = (i % cols) * cell + (cell - 28) // 2
            y = (i // cols) * cell + (cell - 28) // 2
            p.drawPixmap(x, y, 28, 28, pm)
        p.end()
        sheet.save(os.path.join(args.out, "icons.png"))
        print(f"  wrote {os.path.join(args.out, 'icons.png')}")

    print("\n[widgets]")
    shell_mod = check(
        "import gopilot_shell",
        lambda: __import__(f"{PKG}.rasid_components.gopilot_shell",
                           fromlist=["x"]),
    )
    if shell_mod is not None:
        for mode in ("light", "dark"):
            print(f"\n[shell: {mode}]")
            # The palette has to be installed BEFORE the widgets are built:
            # tokens are resolved in each constructor, which is what makes a
            # panel opened after a theme switch pick up the new colours.
            apply_palette(app, dark=(mode == "dark"))
            design.invalidate()

            shell = check(f"build GoPilotShell ({mode})",
                          lambda: shell_mod.GoPilotShell(client=None,
                                                         iface=None))
            if shell is None:
                continue
            shell.resize(design.DOCK_DEFAULT_WIDTH, 760)
            check("show shell", shell.show)
            check("settle layout", lambda: settle(app))
            for view in ("chat", "projects", "gobox", "processes", "history",
                         "settings"):
                check(
                    f"switch to {view}",
                    lambda v=view, s=shell: (s.show_view(v), settle(app)),
                )
                if args.out:
                    path = os.path.join(args.out, f"{mode}-{view}.png")
                    check(f"grab {view}",
                          lambda p=path, s=shell: s.grab().save(p))
            check("populate a conversation",
                  lambda s=shell: demo_conversation(s, app))
            if args.out:
                path = os.path.join(args.out, f"{mode}-conversation.png")
                check("grab conversation",
                      lambda p=path, s=shell: s.grab().save(p))

            check("cleanup shell", shell.cleanup)
            shell.deleteLater()
            settle(app)
        if args.out:
            print(f"  wrote view screenshots to {args.out}")

    print("\n" + "=" * 60)
    if failures:
        for label, tb in failures:
            print(f"\n--- {label} ---\n{tb}")
        print(f"{len(failures)} failure(s)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
