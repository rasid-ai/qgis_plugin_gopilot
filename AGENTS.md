# Working on the GoPilot QGIS plugin

A QGIS plugin that puts the RASID GoPilot assistant in a dock panel: chat with
an AI about geospatial work, and have its results land on the map as real
layers. It talks to the RASID API over HTTP and mirrors the SaaS web app at
`https://app.rasid.ai`.

This file is what the repository cannot tell you by itself: the rules that
bite, the traps that have already cost a day each, and how to check your work.
Read it before changing anything; it is short on purpose.

---

## 1. The environment

Two QGIS versions are installed and **both matter**, because they are Qt5 and
Qt6 respectively:

```
C:/Program Files/QGIS 3.36.3/bin/python-qgis.bat     # Qt5 / PyQt5
C:/Program Files/QGIS 4.0.1/bin/python-qgis.bat      # Qt6 / PyQt6
```

The plugin lives *inside* a QGIS profile, so the repo root is also the
installed plugin directory:

```
C:/Users/<you>/AppData/Roaming/QGIS/QGIS3/profiles/default/python/plugins/qgis_plugin_gopilot
```

Editing a file here changes the live plugin. There is no build or copy step for
development — reload the plugin in QGIS (or restart it) and your change is in.

Third-party packages (`markdown`, `requests`, `keyring`, …) are **not** in
QGIS's Python. They live in a bundled venv (`qgis_gopilot_venv/`, gitignored)
that `rasid_components/dependencies/venv_manager.py` puts on `sys.path` at
startup. Any script you write that imports plugin modules must do the same
thing first:

```python
from rasid_components.dependencies import venv_manager
venv_manager.ensure_venv_packages_available()
```

Omit that and you get `ModuleNotFoundError: No module named 'markdown'`, which
looks like a broken import and is not.

### Shell notes

`bash` here has `$HOME` set to `/c/SPB_Data`, not the user profile. The
QGIS launchers are `.bat` wrappers: they do **not** accept `python -c "…"`
reliably (the quoting breaks). Write a script to a file and pass the path.
Use Windows-style paths as arguments.

---

## 2. Running the checks

```bash
# the whole suite (445 tests) — run on BOTH versions
"C:/Program Files/QGIS 3.36.3/bin/python-qgis.bat" scripts/run_tests.py
"C:/Program Files/QGIS 4.0.1/bin/python-qgis.bat"  scripts/run_tests.py

# one module, class or method
"C:/Program Files/QGIS 3.36.3/bin/python-qgis.bat" scripts/run_tests.py \
    test_gopilot_dock.StyledTilesTest
```

`pytest` is not installed; this is `unittest`. The suite needs a real
(non-offscreen) platform on Windows because several tests measure laid-out
widgets, and the offscreen plugin has no font database there — `run_tests.py`
already handles that.

Two other scripts earn their keep before shipping:

```bash
scripts/smoke_ui.py     # builds every widget, renders every icon, grabs a PNG
scripts/smoke_load.py   # drives classFactory -> initGui -> _show_panel -> unload
```

`smoke_load.py` is the only thing that walks the path QGIS itself takes, which
is where plugin-level mistakes live (a lazy import that stopped being lazy, an
`unload` that leaves the dock parented to the main window).

Packaging a release is `python build_plugin.py`, which writes the installable
zip. It has its own explicit file list — **add new top-level files to it**, or
they are silently missing from the built plugin.

---

## 3. Hard rules

### 3.1 Qt enums: scoped only, via `compat.py`

**Never write a flat `Qt.Something` anywhere.** Qt6 removed them. Every Qt
constant is re-exported from [`rasid_components/compat.py`](rasid_components/compat.py)
in scoped form:

```python
from .compat import Qt_AlignCenter        # Qt.AlignmentFlag.AlignCenter
```

Need a new one? Add **one line** to `compat.py` and import it. Do not branch on
`QT_VERSION` for an enum — the scoped form works on both. (Methods are
different: `exec_()` vs `exec()` genuinely differs, and `compat.exec_dialog()`
exists for exactly that.)

This is not only about runtime. The QGIS plugin repository runs a *static*
Qt6 checker over the source text; it flags a flat enum even inside a Qt5-only
branch, because it cannot see the guard.

Also: always import from `qgis.PyQt.*`, never `PyQt5.*` or `PyQt6.*`.

### 3.2 The API token goes to our hosts only

`config.is_trusted_api_url()` checks the parsed host against
`TRUSTED_API_DOMAINS`. It is an **allowlist, never a blocklist**, and it gates
every path that could attach credentials: downloads, inline image previews, the
tile `Authorization` header, and tile metadata.

Result URLs are often presigned third-party storage links. Sending the user's
token there would leak it to someone else's server. If you add a new outbound
request, route it through the same check.

### 3.3 `config.DEBUG` ships as `False`

`DEBUG = True` makes `logger.debug_print` log message content, session IDs,
URLs and request payloads to the QGIS log. It is useful while working and must
be `False` in anything released. Check it before you commit.

### 3.4 Leave the commented-out keyring button alone

`login_dialog.py` has a commented-out `_add_uninstall_button` hook that
uninstalls `keyring` for testing. It is deliberate — the maintainer uncomments
it on purpose. Do not "clean it up".

---

## 4. How a result gets onto the map

This is the part that has been rebuilt the most, and the part most likely to
look wrong until you know why.

### Vectors — the server sends a style

A vector file entry carries an `sld` field: an OGC SLD document. The flow is
in [`layer_style.py`](rasid_components/layer_style.py):

1. `apply_sld()` is called **before** the layer joins the project.
2. `QgsVectorLayer.loadSldStyle(path)` applies it.
3. If the result would draw nothing, the style is discarded and the previous
   renderer restored — a style that renders zero features is worse than none.

Two traps that have each produced a wrong conclusion:

- `loadSldStyle` returns **`(errorMessage, ok)`** — *message first*. Reading it
  the other way round makes every success look like a failure.
- `loadNamedStyle` cannot read SLD at all. It is a different format.
- `QgsRuleBasedRenderer.symbolForFeature` **always returns null**. If you are
  measuring "did this style draw anything", evaluate `rule.filterExpression()`
  with `QgsExpression` instead. Measuring it the other way produced "0 of 3
  features drawn" for a *working* style.

### Rasters — the server already styled them

**There is no raster SLD, and there never was.** `sld` is null for rasters.
`symbology` and `style_config` exist in the web app's schema and are read by no
line of it. The colour is baked in **server-side while the tile pyramid is
built**, and the website simply displays that pyramid.

So the plugin does not style rasters either. It offers two controls:

| Control | What it is |
|---|---|
| `Add ndvi.tif to QGIS` | the GeoTIFF — real values, queryable, and grey, because QGIS applies its own stretch |
| `Add styled view` | the pyramid — the server's colours, pixel for pixel; an **image** only |

Both are offered because they answer different questions. A tile layer cannot
be queried, analysed or re-symbolised; the GeoTIFF cannot show you the
server's colouring.

[`tiles.py`](rasid_components/tiles.py) builds the styled view, and **three
separate things each make it load successfully and draw nothing**:

1. **The rows are TMS.** gdal2tiles writes TMS unless given `--xyz`, and this
   backend does not. QGIS spells inverted rows as the **`{-y}`** placeholder.
   There is no `type=tms` — that string is not a valid provider URI and QGIS
   rejects the layer outright. For a pyramid covering one AoI the mismatch is
   *not* the famous vertical mirror: the flipped row falls outside the data, so
   every tile 404s.
2. **Do not percent-encode the whole URL.** `quote(url, safe="")` yields
   `url=https%3A%2F%2F…`, which the provider treats as a literal — requests go
   nowhere. Build the URI with `QgsDataSourceUri.setParam()` +
   `encodedUri()`, which encodes only the braces.
3. **Read the zoom range from the pyramid.** `tilemapresource.xml` sits beside
   the tiles and lists the levels that exist plus the extent. Overstating
   `zmax` is the damaging direction — QGIS asks for a level that was never
   built and draws blank. Understating it only costs sharpness. The file also
   supplies the extent, which matters because `bbox` on the file record is
   frequently `null`.

The pyramid is not public: `/media/` answers anonymously with `403` and accepts
`Authorization: Bearer <key>`. Note the consequence — **saving a QGIS project
persists the layer URI, token included, into the `.qgs` file.** The web app has
no equivalent exposure because a browser uses a session cookie. Nothing in the
plugin can prevent it; it is a known property, not a bug to re-discover.

### Layer ordering

Newest layer on **top**, everywhere:

- `QgsLayerTreeGroup.addLayer()` **appends** — i.e. puts it at the bottom. Use
  `group.insertLayer(0, layer)`.
- `QgsProject.addMapLayer(layer)` at the root already puts it on top.

---

## 5. UI conventions

All colour, spacing, radius, type and icon sizing comes from
[`design.py`](rasid_components/design.py) — a port of the SaaS design system.
Use the tokens (`design.tokens()`, `design.TEXT_MD`, `design.RADIUS_LG`, …)
rather than literals, and check both light and dark themes.

### The stylesheet-inheritance trap

A Qt stylesheet rule with **no selector** applies to the widget *and every
descendant, including child windows*. `QMessageBox` is a `QDialog`, so a
selector-less `background: #0F172A` on a page turns every message box that page
opens black. Scope rules with an `#objectName`.

### Layout measurements are stale until the next pass

`QVBoxLayout.sizeHint()` and a scroll area's `maximum()` are not current
immediately after you add widgets. `WrappedLabel` pins its height from its own
`resizeEvent`, and each pass schedules another. If you need a real number,
defer and re-check rather than trusting the first value.

Two more that have caused off-by-ones and wrong conclusions:

- `QRect.bottom()` is **inclusive** (`top() + height() - 1`).
- A stylesheet `border` is part of the frame and reserved *outside* the
  layout's content rect — `required_height()` must add `2 * frameWidth()`.

### Footers put the primary action first

Dialog footers read *primary action, then Cancel* — a deliberate divergence
from the web app, documented at the call sites. Keep it consistent.

---

## 6. Lifecycle and crashes

Crashes here are almost always teardown, not logic.

- Use **`compat.is_alive(obj)`** before touching a C++ object that may have
  gone. It wraps `sip.isdeleted()` and degrades sanely when `sip` is missing.
  A controller can easily outlive the button it drives — a bubble re-render or
  the user leaving the page is enough. The crash it was written for: an image
  preview's `finished` signal reaching a closure holding a `QLabel` whose
  parent bubble had just been deleted.
- A Python event filter or slot still connected during interpreter
  finalization **kills the process**: exit 127, no traceback. Disconnect in
  `cleanup()`.
- Every long-lived helper (download controllers, threads, bubbles) has a
  `cleanup()`. Call it when the owning widget goes away.

`QSettings` silently drops writes when no organisation name is set —
`fileName()` is empty and `status()` reports an access error. A test harness or
probe that writes prefs must call `setOrganizationName()` first, or the
settings appear not to persist and you will blame the wrong code.

An XYZ/tile layer needs QGIS's `wms` provider library, which a bare
`QApplication` does not load. Call `QgsApplication.initQgis()` in any probe
that builds one, or the layer is invalid for a reason that has nothing to do
with your change.

---

## 7. How to verify a change

The project's working discipline, learned the hard way. It is worth following
literally.

**Measure; do not reason.** Contrast ratios, `heightForWidth`, scroll ranges,
rendered pixels, `QgsProviderRegistry` — ask the system. Several "obvious"
fixes in this repo's history had **no measurable effect** and were reverted,
and several conclusions drawn from plausible reasoning were simply wrong
(the raster SLD that does not exist; a tile host that was never wrong).

**Write a probe.** A short script in a scratch directory (not the repo) that
drives the real code path against real data beats reading code. Probes that
lie are the main risk — check that your instrument works before trusting what
it says:

- A control case is mandatory. A "blank render" probe once reported that a
  *public OpenStreetMap* basemap drew nothing; the bug was in the probe's own
  URI construction, and the false conclusion ("headless rendering is
  impossible") survived several rounds.
- `provider.block()` issues no network requests in a headless process.
- `QgsDataSourceUri(string)` — the constructor — does not read XYZ parameters.
  The provider uses `setEncodedUri()`. Emulate the real reader.
- `QBuffer(QByteArray())` segfaults: the temporary is garbage-collected and the
  buffer keeps a dangling pointer. Keep a reference.

**Then revert your fix and confirm the test goes red.** This has repeatedly
exposed tests that passed against broken code, and one that silently skipped
every run. If reverting leaves the suite green, the test is decoration.

**Watch out for tests tripping on their own documentation.** The suite scans
source text for banned patterns, and comments *describing* a removed value
match too. The module helper `_code_of(path)` blanks comment spans in place
(blanking rather than removing, so offsets and `t.accent`-style lookups still
line up) — use it for any source-scanning assertion.

---

## 8. Layout of the code

| Path | What it is |
|---|---|
| `rasid_plugin.py` | QGIS entry point: `classFactory`, `initGui`, `unload` |
| `rasid_components/compat.py` | Qt5/Qt6 scoped-enum layer. Read §3.1 |
| `rasid_components/design.py` | design tokens — colour, type, spacing, radius |
| `rasid_components/config.py` | hosts, `DEBUG`, `TRUSTED_API_DOMAINS`, `plugin_version()` |
| `rasid_components/rasid_client.py` | REST client, keyring-backed API key |
| `rasid_components/gopilot_client.py` | the LLM/chat endpoints |
| `rasid_components/chat_threads.py` | background threads: send, poll, download |
| `rasid_components/gopilot_shell.py` | the shell: nav rail + pages |
| `rasid_components/gopilot_page.py` | the chat transcript and its scrolling |
| `rasid_components/message_bubble.py` | one message: markdown, files, result controls |
| `rasid_components/downloads.py` | shared download-button controller (both panes) |
| `rasid_components/layer_style.py` | SLD → QGIS renderer (vector and raster) |
| `rasid_components/tiles.py` | the server-styled tile pyramid. Read §4 |
| `rasid_components/processes_page.py` | the Processes/Solutions pane |
| `rasid_components/prefs.py` | `QSettings` wrapper; preference keys |
| `rasid_components/dependencies/` | the bundled venv and its manager |
| `test/test_gopilot_dock.py` | the bulk of the suite (~445 tests) |
| `scripts/` | test runner and the two smoke scripts |

### Streaming

A reply is not pushed. `chat_threads` polls `GET sessions/tasks/{id}/` and the
`llm_message` field grows between polls. New replies carry
`known_message_ids` so a slow task cannot deliver a *previous* message as the
answer to the current prompt.

---

## 9. Known-but-untouched

- **Process results are not styled** — their payload carries no `sld`, and no
  tile pyramid either.
- There is no top-level `Makefile` and no `pb_tool.cfg`: the old
  plugin-builder scaffolding was deleted deliberately. `scripts/run_tests.py`
  still mentions `make test` in its docstring, which is now only history.
  (`help/` has its own Sphinx `Makefile`; it is documentation scaffolding, not
  read at runtime, and is correctly left out of the built zip.)
- `metadata.txt` points `repository` at `rasid-ai/qgis_gopilot`; the actual
  remote is `rasid-ai/qgis_plugin_gopilot`.

---

## 10. Conventions for changes

- Explain *why* in the code. This repo's comments carry the measurement that
  justified a decision, and every deliberate divergence from the SaaS says so
  and says why. That is the house style — a comment that only restates the code
  is noise, one that records what was measured saves the next person a day.
- Commit or push only when asked.
- Run the full suite on **both** QGIS versions before saying something works.
- Report outcomes plainly. If a step was skipped or a test fails, say so with
  the output.
