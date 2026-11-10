"""The Settings view behind the gear at the bottom of the nav rail.

Sections, in the order the sketch lists them plus the two the plugin already
had nowhere sensible to put:

1. **Account** — who is signed in, token balance, sign out.
2. **Map & layers** — the on/off layer controls. These decide what the plugin
   is allowed to do to the user's QGIS project, which is why they are first
   among the real settings and default to the conservative choice.
3. **Chat** — transcript preferences.
4. **Libraries** — the bundled dependency environment: which Python this QGIS
   session runs, which per-version environment the plugin installed into, and
   the state of every required package. This used to be reachable only through
   the modal install prompt on first run, i.e. a user whose install half-failed
   had no way to look at it or retry.
5. **Feedback** and **About** — opened from here rather than occupying their own
   slots in the rail, since neither is a place you navigate TO repeatedly.

The view owns no network state beyond the profile the shell hands it, so it can
be opened before the profile request comes back.
"""

import os
import sys

from qgis.PyQt.QtCore import QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from . import design, icons, prefs
from .formatting import format_tokens
from .compat import (
    Qt_AlignVCenter, Qt_ElideMiddle, Qt_TextBrowserInteraction,
)
from .config import APP_BASE_URL, WEBSITE_URL
from .dependencies import venv_manager
from .logger import debug_print
from .widgets import (
    paint_transparent,
    paint_background,
    Chip, ElidedLabel, SectionCard, SettingRow, body_label, danger_button,
    enable_height_for_width, primary_button, secondary_button,
    styled_scroll_area,
)

PLUGIN_VERSION = "2.0.0"


class SettingsPage(QWidget):
    """Scrollable settings. Emits requests; never acts on the session itself."""

    logout_requested = pyqtSignal()
    feedback_requested = pyqtSignal()

    def __init__(self, client=None, iface=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.iface = iface
        self._install_worker = None

        t = design.tokens()
        paint_background(self, t.canvas, "settingsPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # No title here: the shell draws one view-header strip for every view.
        scroll = styled_scroll_area()
        body = QWidget()
        paint_transparent(body)
        # The scroll area asks its content widget how tall it must be at the
        # viewport width; the content has to be willing to answer, or the
        # wrapped descriptions inside the cards get one line each.
        enable_height_for_width(body)
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(design.SPACE_3, design.SPACE_3,
                                      design.SPACE_3, design.SPACE_4)
        self._body.setSpacing(design.SPACE_3)

        self._body.addWidget(self._build_account())
        self._body.addWidget(self._build_layers())
        self._body.addWidget(self._build_chat())
        self._body.addWidget(self._build_libraries())
        self._body.addWidget(self._build_help())
        self._body.addStretch()

        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        self.refresh_libraries()

    # ── 1. Account ────────────────────────────────────────────────────────────
    def _build_account(self):
        card = SectionCard("Account", icon_name="user")
        t = design.tokens()

        self._name_label = body_label("Signed in", design.TEXT_MD)
        card.add(self._name_label)

        self._email_label = body_label("", design.TEXT_SM, secondary=True)
        # Hidden until the profile lands: an empty label still claims a line,
        # which left a blank gap in the card for everyone.
        self._email_label.setVisible(False)
        card.add(self._email_label)

        self._balance_label = QLabel("")
        self._balance_label.setOpenExternalLinks(True)
        self._balance_label.setTextInteractionFlags(Qt_TextBrowserInteraction)
        self._balance_label.setStyleSheet(
            f"background:transparent; border:none; color:{t.accent_text}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_MD}px; "
            f"font-weight:{design.WEIGHT_SEMIBOLD};"
        )
        self._balance_label.setVisible(False)
        card.add(self._balance_label)

        card.add_spacing(design.SPACE_1)
        row = QHBoxLayout()
        row.setSpacing(design.SPACE_2)
        profile_btn = secondary_button("Open profile", icon_name="user")
        profile_btn.clicked.connect(
            lambda: self._open_url(f"{APP_BASE_URL}/profile"))
        row.addWidget(profile_btn)
        topup_btn = secondary_button("Buy tokens", icon_name="sparkle")
        topup_btn.clicked.connect(
            lambda: self._open_url(f"{APP_BASE_URL}/payment/refill"))
        row.addWidget(topup_btn)
        row.addStretch()
        card.add_layout(row)

        card.add_divider()
        logout_btn = danger_button("Sign out", icon_name="logout")
        logout_btn.clicked.connect(self.logout_requested.emit)
        card.add(logout_btn)
        return card

    def set_profile(self, data):
        """Fill in the account card once the shell's profile request returns."""
        profile = (data or {}).get("profile", {}) or {}
        first = profile.get("first_name", "") or ""
        last = profile.get("last_name", "") or ""
        name = f"{first} {last}".strip() or profile.get("email") or "Signed in"
        self._name_label.setText(name)

        email = profile.get("email", "") or ""
        self._email_label.setText(email)
        self._email_label.setVisible(bool(email) and email != name)

        self._set_balance((data or {}).get("gotokens"),
                          (data or {}).get("gotokens_error"))

    def _set_balance(self, gotokens, error=None):
        """Show the GoToken balance, or nothing at all if it is unknown.

        The old version read ``data["balance"]["amount"]`` with a default of
        ``0``. That field no longer exists — the balance moved to
        ``/billing/gotokens/`` when the product moved to GoTokens — so every
        user was shown a confident "0 tokens available" regardless of what they
        actually held.

        Hence the rule here: **absent is not zero.** A missing wallet, a failed
        request or an unparseable figure hides the label; only a real number is
        ever printed. A balance is something people make decisions on, so being
        silent beats being wrong.
        """
        t = design.tokens()

        if error:
            # The request did not come back. Say that, rather than going quiet
            # and leaving the user to wonder — and rather than printing a
            # figure we never received.
            self._balance_label.setText(
                f'<a href="{APP_BASE_URL}/payment/refill" '
                f'style="color:{t.text_secondary}; text-decoration:none;">'
                f'Token balance unavailable &mdash; open in browser</a>'
            )
            self._balance_label.setVisible(True)
            return

        wallet = (gotokens or {}).get("wallet") if gotokens else None
        if not isinstance(wallet, dict):
            # Request succeeded and there is genuinely no wallet (no plan).
            self._balance_label.setVisible(False)
            return

        formatted = format_tokens(wallet.get("balance"))
        if formatted is None:
            self._balance_label.setVisible(False)
            return

        overdrawn = bool(wallet.get("is_overdrawn"))
        colour = t.error if overdrawn else t.accent_text
        suffix = " · overdrawn" if overdrawn else " available"
        self._balance_label.setText(
            f'<a href="{APP_BASE_URL}/payment/refill" '
            f'style="color:{colour}; text-decoration:none;">'
            f'{formatted} tokens{suffix}</a>'
        )
        self._balance_label.setVisible(True)

    # ── 2. Map & layers ───────────────────────────────────────────────────────
    def _build_layers(self):
        card = SectionCard("Map & layers", icon_name="layers")
        card.add(body_label(
            "What GoPilot is allowed to do to the current QGIS project.",
            design.TEXT_SM, secondary=True,
        ))
        card.add_spacing(design.SPACE_1)

        self._group_row = SettingRow(
            "Group results in one layer group",
            "Keeps everything GoPilot adds together instead of scattering it "
            "through your layer panel.",
            checked=prefs.get_bool(prefs.LAYER_GROUP_ENABLED),
        )
        self._group_row.toggled.connect(
            lambda on: self._save(prefs.LAYER_GROUP_ENABLED, on))
        self._group_row.toggled.connect(self._sync_group_name_enabled)
        card.add(self._group_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(design.SPACE_2)
        name_label = body_label("Group name", design.TEXT_BASE, secondary=True)
        name_row.addWidget(name_label)
        t = design.tokens()
        self._group_name = QLineEdit(prefs.get_str(prefs.LAYER_GROUP_NAME))
        self._group_name.setMaximumWidth(150)
        self._group_name.setStyleSheet(f"""
            QLineEdit {{
                background:{t.surface}; color:{t.text};
                border:1px solid {t.border_strong};
                border-radius:{design.RADIUS_SM}px; padding:5px 9px;
                font-family:{design.FONT_STACK};
                font-size:{design.TEXT_BASE}px;
            }}
            QLineEdit:focus {{ border-color:{t.focus_ring}; }}
            QLineEdit:disabled {{
                background:{t.surface_alt}; color:{t.text_disabled};
            }}
        """)
        # editingFinished, not textChanged: writing to QSettings on every
        # keystroke means a partially-typed name is what gets persisted if the
        # user never leaves the field.
        self._group_name.editingFinished.connect(self._save_group_name)
        name_row.addStretch()
        name_row.addWidget(self._group_name)
        card.add_layout(name_row)
        self._sync_group_name_enabled(self._group_row.is_checked())

        card.add_divider()

        self._zoom_row = SettingRow(
            "Zoom to layer after loading",
            "Moves the canvas to a result as soon as it is added.",
            checked=prefs.get_bool(prefs.ZOOM_TO_LAYER),
        )
        self._zoom_row.toggled.connect(
            lambda on: self._save(prefs.ZOOM_TO_LAYER, on))
        card.add(self._zoom_row)

        self._auto_row = SettingRow(
            "Load results automatically",
            "Off by default: results are added only when you click the "
            "download button on a message.",
            checked=prefs.get_bool(prefs.AUTO_LOAD_LAYERS),
        )
        self._auto_row.toggled.connect(
            lambda on: self._save(prefs.AUTO_LOAD_LAYERS, on))
        card.add(self._auto_row)
        return card

    def _sync_group_name_enabled(self, enabled):
        """The group name is meaningless with grouping off, so grey it out."""
        self._group_name.setEnabled(bool(enabled))

    def _save_group_name(self):
        name = self._group_name.text().strip()
        if not name:
            name = prefs.DEFAULTS[prefs.LAYER_GROUP_NAME]
            self._group_name.setText(name)
        self._save(prefs.LAYER_GROUP_NAME, name)

    # ── 3. Chat ───────────────────────────────────────────────────────────────
    def _build_chat(self):
        card = SectionCard("Chat", icon_name="message")

        self._timestamps_row = SettingRow(
            "Show message timestamps",
            checked=prefs.get_bool(prefs.SHOW_TIMESTAMPS),
        )
        self._timestamps_row.toggled.connect(
            lambda on: self._save(prefs.SHOW_TIMESTAMPS, on))
        card.add(self._timestamps_row)

        self._enter_row = SettingRow(
            "Enter sends the message",
            "With this off, Enter adds a new line and Ctrl+Enter sends.",
            checked=prefs.get_bool(prefs.SEND_ON_ENTER),
        )
        self._enter_row.toggled.connect(
            lambda on: self._save(prefs.SEND_ON_ENTER, on))
        card.add(self._enter_row)

        card.add_divider()
        card.add(body_label(
            "Light and dark follow your QGIS theme. Switch it in "
            "Settings → Options → General, then reopen this panel.",
            design.TEXT_SM, secondary=True,
        ))
        return card

    # ── 4. Libraries ──────────────────────────────────────────────────────────
    def _build_libraries(self):
        card = SectionCard("Libraries", icon_name="library")
        self._libraries_card = card

        self._recheck_btn = secondary_button("Re-check", icon_name="refresh")
        self._recheck_btn.clicked.connect(self.refresh_libraries)
        card.add_header_widget(self._recheck_btn)

        card.add(body_label(
            "GoPilot installs its Python packages into its own folder, one per "
            "Python version, so a QGIS upgrade never leaves you with "
            "libraries built for the wrong interpreter.",
            design.TEXT_SM, secondary=True,
        ))
        card.add_spacing(design.SPACE_1)

        self._runtime_label = body_label("", design.TEXT_BASE)
        card.add(self._runtime_label)

        # Elided, not wrapped: an absolute path has no spaces, so word wrap
        # cannot break it and its full width becomes the minimum width of every
        # card on this page. Middle elide keeps the drive and the
        # version-stamped leaf folder, which are the parts worth reading.
        self._env_label = ElidedLabel("", elide=Qt_ElideMiddle)
        self._env_label.setStyleSheet(
            design.body_qss(design.TEXT_SM, secondary=True))
        card.add(self._env_label)

        card.add_spacing(design.SPACE_1)
        self._packages_host = QWidget()
        paint_transparent(self._packages_host)
        self._packages_layout = QVBoxLayout(self._packages_host)
        self._packages_layout.setContentsMargins(0, 0, 0, 0)
        self._packages_layout.setSpacing(1)
        card.add(self._packages_host)

        self._install_status = body_label("", design.TEXT_SM, secondary=True)
        self._install_status.setVisible(False)
        card.add(self._install_status)

        actions = QHBoxLayout()
        actions.setSpacing(design.SPACE_2)
        self._install_btn = primary_button("Install missing",
                                           icon_name="download")
        self._install_btn.clicked.connect(self._install_missing)
        actions.addWidget(self._install_btn)
        self._open_folder_btn = secondary_button("Open folder",
                                                 icon_name="folder-open")
        self._open_folder_btn.clicked.connect(self._open_env_folder)
        actions.addWidget(self._open_folder_btn)
        actions.addStretch()
        card.add_layout(actions)
        return card

    def refresh_libraries(self):
        """Re-probe the Python runtime and every required package."""
        version = (f"{sys.version_info.major}.{sys.version_info.minor}"
                   f".{sys.version_info.micro}")
        supported = venv_manager.python_runtime_supported()
        t = design.tokens()
        self._runtime_label.setText(f"QGIS is running Python {version}")
        self._runtime_label.setStyleSheet(
            design.body_qss(design.TEXT_BASE) if supported
            else (f"background:transparent; border:none; color:{t.error}; "
                  f"font-family:{design.FONT_STACK}; "
                  f"font-size:{design.TEXT_BASE}px;")
        )
        if not supported:
            self._runtime_label.setText(venv_manager.python_runtime_error())

        try:
            env_dir = venv_manager.get_venv_dir()
        except Exception as exc:
            debug_print(f"[RASID] Could not resolve env dir: {exc}")
            env_dir = ""
        self._env_dir = env_dir
        self._env_label.setText(env_dir or "Environment not created yet")

        while self._packages_layout.count():
            item = self._packages_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        try:
            deps = venv_manager.check_dependencies()
        except Exception as exc:
            debug_print(f"[RASID] Dependency check failed: {exc}")
            deps = []
            self._packages_layout.addWidget(body_label(
                f"Could not check packages: {exc}", design.TEXT_SM,
                secondary=True))

        missing = 0
        for dep in deps:
            installed = dep.get("installed")
            if not installed:
                missing += 1
            self._packages_layout.addWidget(
                self._package_row(dep, installed))

        self._install_btn.setEnabled(bool(missing) and supported)
        self._install_btn.setText(
            f"Install missing ({missing})" if missing else "All installed"
        )
        self._open_folder_btn.setEnabled(bool(env_dir) and
                                         os.path.isdir(env_dir))

    def _package_row(self, dep, installed):
        """One package line: name, resolved version, and a state chip."""
        t = design.tokens()
        row = QWidget()
        paint_transparent(row)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(design.SPACE_2)

        dot = QLabel()
        dot.setPixmap(icons.pixmap(
            "check" if installed else "alert", 12,
            t.success if installed else t.warning, dpr=2.0))
        paint_transparent(dot, border=True)
        lay.addWidget(dot, alignment=Qt_AlignVCenter)

        name = dep.get("pip_name") or dep.get("name") or "?"
        lay.addWidget(body_label(name, design.TEXT_BASE, wrap=False))
        lay.addStretch()

        version = dep.get("version")
        if installed and version:
            lay.addWidget(Chip(str(version), tone="neutral", removable=False,
                               max_chars=14))
        elif installed:
            lay.addWidget(Chip("installed", tone="success", removable=False,
                               max_chars=14))
        else:
            lay.addWidget(Chip("missing", tone="warning", removable=False,
                               max_chars=14))
        return row

    def _install_missing(self):
        """Create/repair the per-version environment and install what is gone."""
        if self._install_worker is not None and self._install_worker.isRunning():
            return
        self._install_btn.setEnabled(False)
        self._install_status.setVisible(True)
        self._install_status.setText("Starting…")

        worker = venv_manager.DepsInstallWorker(self)
        worker.progress.connect(self._install_status.setText)
        worker.finished.connect(self._on_install_finished)
        self._install_worker = worker
        worker.start()

    def _on_install_finished(self, success, message):
        t = design.tokens()
        self._install_status.setText(message)
        self._install_status.setStyleSheet(
            f"background:transparent; border:none; "
            f"color:{t.success if success else t.error}; "
            f"font-family:{design.FONT_STACK}; font-size:{design.TEXT_SM}px;"
        )
        self.refresh_libraries()

    def _open_env_folder(self):
        path = getattr(self, "_env_dir", "")
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ── 5. Help ───────────────────────────────────────────────────────────────
    def _build_help(self):
        card = SectionCard("About & feedback", icon_name="info")
        card.add(body_label(
            "RASID turns satellite imagery into decisions. GoPilot is the "
            "conversational way in: ask for an analysis and it runs in the "
            "cloud, then lands in your QGIS project.",
            design.TEXT_SM, secondary=True,
        ))

        card.add_spacing(design.SPACE_1)
        feedback_btn = secondary_button("Send feedback", icon_name="message")
        feedback_btn.clicked.connect(self.feedback_requested.emit)
        card.add(feedback_btn)

        links = QHBoxLayout()
        links.setSpacing(design.SPACE_2)
        # Straight to the website rather than a dialog holding a copy of
        # its text. The embedded About page had to be kept in step with
        # rasid.ai by hand, and silently drifted from it the moment the site
        # changed; a link cannot go stale.
        about_btn = secondary_button(
            "About RASID", icon_name="info",
            tooltip="Opens rasid.ai/about in your browser")
        about_btn.clicked.connect(
            lambda: self._open_url(f"{WEBSITE_URL}/about"))
        links.addWidget(about_btn)
        terms_btn = secondary_button(
            "Terms", icon_name="info",
            tooltip="Opens the terms of service in your browser")
        terms_btn.clicked.connect(
            lambda: self._open_url(f"{APP_BASE_URL}/terms"))
        links.addWidget(terms_btn)
        links.addStretch()
        card.add_layout(links)

        card.add_divider()
        card.add(body_label(
            f"GoPilot for QGIS {PLUGIN_VERSION} · © 2026 RASID",
            design.TEXT_XS, secondary=True,
        ))
        return card

    # ── shared ────────────────────────────────────────────────────────────────
    def _save(self, key, value):
        prefs.set_value(key, value)

    def _open_url(self, url):
        QDesktopServices.openUrl(QUrl(url))

    def cleanup(self):
        worker = self._install_worker
        if worker is not None:
            try:
                if worker.isRunning():
                    # A pip subprocess cannot be interrupted safely mid-write,
                    # so wait for it rather than killing the thread under it.
                    worker.wait(3000)
            except RuntimeError as exc:
                debug_print(f"[RASID] Install worker teardown: {exc}")
        self._install_worker = None
