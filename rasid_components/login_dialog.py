"""The sign-in dialog, built to match the SaaS's auth page.

WHAT THE SAAS DOES, AND WHAT THIS COPIES
========================================
``src/pages/auth/auth.tsx`` is a full-bleed topographic background, a soft
white wash over it so text stays legible on the map texture, and one centred
card carrying the mark, a heading and the form. All of that is reproduced
here, minus the accolade badge the SaaS puts above its card.

It replaced a 750x500 two-column dialog — a dark slate panel on the left
holding the mark, a form on the right — which shared no part of its design
with the SaaS or with the rest of the plugin.

WHAT IT CANNOT COPY
===================
The SaaS signs in with email and password, or with Google. The plugin signs in
with an API KEY: it has no browser session to carry and no redirect to come
back to. So the form is one field, and the SaaS's "Or continue with Google"
slot is taken by the route to obtaining a key.

THE CARD IS LIGHT IN BOTH THEMES
================================
Deliberately, and this is the one place in the plugin that ignores the QGIS
theme. The background is a light topographic map, so a dark card would sit on
it badly and dark-theme text would be illegible against the white wash. The
LIGHT token set is therefore resolved explicitly rather than through
``design.tokens()``.
"""

# login dialog UI
import os
import sys
# subprocess is used only to run a Python interpreter resolved from the QGIS
# install (sys.executable / known install paths) with a fixed argument list
# ("-m", "pip", "uninstall", "keyring", "-y"). No shell is used and no argument
# is derived from untrusted input, so the Bandit B404/B603 findings are false
# positives; the call site is suppressed inline with justification.
import subprocess  # nosec B404

from qgis.PyQt.QtCore import QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from . import branding, design
from .compat import (
    Qt_AlignCenter, Qt_AlignVCenter, Qt_KeepAspectRatioByExpanding,
    Qt_PointingHandCursor, Qt_SmoothTransformation,
    QLineEdit_Password, QLineEdit_Normal, QMessageBox_Yes, QMessageBox_No
)
from .config import APP_BASE_URL

BACKGROUND_IMAGE = "auth-topo.jpg"
# How much white to lay over the map texture. The SaaS uses `bg-white/45`, and
# the same value here: the card and badge have to stay readable over contour
# lines without washing the texture out altogether.
BACKGROUND_WASH = 0.45

CARD_WIDTH = 430              # the SaaS's max-w-md, less the dock's margins
DIALOG_WIDTH = 720
DIALOG_HEIGHT = 580

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _colour(hex_colour, alpha=1.0):
    """A ``QColor`` from a token, optionally translucent."""
    colour = QColor(hex_colour)
    if alpha < 1.0:
        colour.setAlphaF(alpha)
    return colour


class _AuthBackground(QWidget):
    """The topographic image, cover-scaled, under a white wash.

    Painted rather than set as a stylesheet ``background-image``: that
    stretches the image to the widget, which visibly distorts a contour map.
    This scales with ``KeepAspectRatioByExpanding`` and centre-crops — the
    same ``object-fit: cover`` emulation the card thumbnails use.

    The scaled pixmap is cached against the widget size, or a dialog resize
    would rescale a 1376x768 image on every paint event.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = QPixmap(os.path.join(_PLUGIN_ROOT, BACKGROUND_IMAGE))
        self._scaled = None
        self._scaled_for = None

    def paintEvent(self, event):
        light = design.Tokens(False)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        try:
            if self._source.isNull():
                # No asset shipped, or an unreadable one: the card still has to
                # be legible, so fall back to the canvas rather than leaving
                # the dialog transparent (which composites to black).
                painter.fillRect(self.rect(), _colour(light.canvas))
                return

            if self._scaled_for != self.size():
                self._scaled = self._source.scaled(
                    self.size(), Qt_KeepAspectRatioByExpanding,
                    Qt_SmoothTransformation)
                self._scaled_for = self.size()

            # Centre the overflow, so the crop takes equal slices off each side.
            painter.drawPixmap((self.width() - self._scaled.width()) // 2,
                               (self.height() - self._scaled.height()) // 2,
                               self._scaled)
            painter.fillRect(self.rect(),
                             _colour(light.surface, BACKGROUND_WASH))
        finally:
            painter.end()


class LoginView(QWidget):
    """Ask for an API key, over the SaaS's auth background.

    A plain widget rather than a dialog, so the panel can host it: GoPilot
    docks on first use whether or not anyone is signed in, and a modal window
    in front of QGIS is the wrong shape for "you are not signed in yet". The
    dock swaps this for the shell once a key validates.

    :class:`LoginDialog` still wraps it for any caller that wants a window.
    """

    # The key the user typed. The view does not validate it — only the plugin
    # knows how to reach the API and what to do with a refusal.
    sign_in_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loginView")

        # Light tokens throughout — see the module docstring.
        t = design.Tokens(False)
        self._tokens = t

        # The background fills the dialog; everything else sits on top of it.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._background = _AuthBackground(self)
        outer.addWidget(self._background)

        centre = QVBoxLayout(self._background)
        centre.setContentsMargins(design.SPACE_4, design.SPACE_4,
                                  design.SPACE_4, design.SPACE_4)
        centre.setSpacing(design.SPACE_2)
        centre.addStretch()

        centre.addWidget(self._build_card(t), alignment=Qt_AlignCenter)
        centre.addStretch()

    # ── the card ────────────────────────────────────────────────────────────
    def _build_card(self, t):
        card = QFrame()
        card.setObjectName("authCard")
        card.setFixedWidth(CARD_WIDTH)
        # Translucent white with a hairline, the SaaS's `bg-white/90` and
        # `border-white/70`. Qt has no backdrop-blur, so the fill carries the
        # separation on its own and is kept high enough to stay readable over
        # contour lines.
        card.setStyleSheet(
            f"QFrame#authCard {{ "
            f"background:{design.alpha(t.surface, 0.93)}; "
            f"border:1px solid {design.alpha('#FFFFFF', 0.70)}; "
            f"border-radius:{design.RADIUS_LG}px; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(design.SPACE_5, design.SPACE_4,
                               design.SPACE_5, design.SPACE_4)
        lay.setSpacing(design.SPACE_2)

        lay.addLayout(self._build_brand_row(t))
        lay.addSpacing(design.SPACE_2)

        heading = QLabel("Welcome back")
        heading.setAlignment(Qt_AlignCenter)
        heading.setStyleSheet(
            f"background:transparent; border:none; color:{t.text}; "
            f"font-family:{design.DISPLAY_STACK_HTML}; "
            f"font-size:{design.TEXT_XL}px; "
            f"font-weight:{design.WEIGHT_BOLD};")
        lay.addWidget(heading)

        subtitle = QLabel("Connect the plugin with your RASID API key")
        subtitle.setAlignment(Qt_AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(
            f"background:transparent; border:none; color:{t.text_secondary}; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_BASE}px;")
        lay.addWidget(subtitle)

        lay.addSpacing(design.SPACE_3)
        lay.addWidget(self._build_api_key_field(t))
        lay.addSpacing(design.SPACE_1)
        lay.addWidget(self._build_error_label(t))
        lay.addSpacing(design.SPACE_2)
        lay.addWidget(self._build_connect_button(t))
        lay.addSpacing(design.SPACE_3)
        lay.addLayout(self._build_divider(t, "Or"))
        lay.addSpacing(design.SPACE_3)
        lay.addWidget(self._build_get_key_button(t))
        lay.addSpacing(design.SPACE_3)
        lay.addWidget(self._build_signup_line(t))

        # TESTING: uncomment to get the keyring uninstall control back.
        # self._add_uninstall_button(lay)
        return card

    def _build_brand_row(self, t):
        """Mark and wordmark, side by side and centred, as in the SaaS."""
        row = QHBoxLayout()
        row.setSpacing(design.SPACE_2)
        row.addStretch()

        mark = QLabel()
        mark.setPixmap(branding.brand_mark(t, size=30))
        mark.setStyleSheet("background:transparent; border:none;")
        row.addWidget(mark, alignment=Qt_AlignVCenter)

        wordmark = QLabel(branding.WORDMARK)
        wordmark.setStyleSheet(
            f"background:transparent; border:none; color:{t.accent}; "
            f"font-family:{design.DISPLAY_STACK_HTML}; "
            f"font-size:{design.TEXT_LG}px; "
            f"font-weight:{design.WEIGHT_BOLD};")
        row.addWidget(wordmark, alignment=Qt_AlignVCenter)
        row.addStretch()
        return row

    def _build_api_key_field(self, t):
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(design.SPACE_1)

        self.api_key_label = QLabel("API key")
        self.api_key_label.setStyleSheet(
            f"background:transparent; border:none; color:{t.text}; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_BASE}px; "
            f"font-weight:{design.WEIGHT_MEDIUM};")
        column.addWidget(self.api_key_label)

        row = QHBoxLayout()
        row.setSpacing(design.SPACE_1)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit_Password)
        self.api_key_input.setPlaceholderText("rsd_live_…")
        self.api_key_input.setMinimumHeight(38)
        # The design system's own field styling, pinned to the light tokens.
        self.api_key_input.setStyleSheet(
            f"QLineEdit {{ background:{t.surface}; color:{t.text}; "
            f"border:1px solid {t.border_strong}; "
            f"border-radius:{design.RADIUS}px; padding:8px 12px; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_MD}px; }}"
            f"QLineEdit:focus {{ border:1px solid {t.focus_ring}; }}"
        )
        # Enter in the field signs in, rather than doing nothing.
        self.api_key_input.returnPressed.connect(self._submit)
        row.addWidget(self.api_key_input, stretch=1)

        self._eye_btn = QPushButton("Show")
        self._eye_btn.setFixedHeight(38)
        self._eye_btn.setCursor(Qt_PointingHandCursor)
        self._eye_btn.setAutoDefault(False)
        # A word, not an emoji. The eye/monkey pair rendered at whatever size
        # and colour the host font decided, which on Windows is a full-colour
        # glyph that matched nothing else in the dialog.
        self._eye_btn.setStyleSheet(
            f"QPushButton {{ background:{t.surface_alt}; color:{t.text}; "
            f"border:1px solid {t.border_strong}; "
            f"border-radius:{design.RADIUS}px; padding:0 12px; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_BASE}px; }}"
            f"QPushButton:hover {{ background:{t.hover}; }}"
        )
        self._eye_btn.setToolTip("Show API key")
        self._eye_btn.clicked.connect(self._toggle_api_key_visibility)
        row.addWidget(self._eye_btn)

        column.addLayout(row)
        return host

    def _build_connect_button(self, t):
        self.login_button = QPushButton("Sign in")
        self.login_button.setCursor(Qt_PointingHandCursor)
        self.login_button.setMinimumHeight(40)
        self.login_button.setDefault(True)
        self.login_button.setStyleSheet(
            f"QPushButton {{ background:{t.accent}; "
            f"color:{t.text_on_accent}; border:none; "
            f"border-radius:{design.RADIUS}px; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_MD}px; "
            f"font-weight:{design.WEIGHT_SEMIBOLD}; }}"
            f"QPushButton:hover {{ background:{t.accent_hover}; }}"
            f"QPushButton:pressed {{ background:{t.accent_pressed}; }}"
        )
        self.login_button.clicked.connect(self._submit)
        return self.login_button

    def _build_divider(self, t, text):
        """A rule with a word in it — the SaaS's "Or continue with"."""
        row = QHBoxLayout()
        row.setSpacing(design.SPACE_2)
        for _ in range(2):
            rule = QFrame()
            rule.setFixedHeight(1)
            rule.setStyleSheet(f"background:{t.divider}; border:none;")
            row.addWidget(rule, stretch=1)
        label = QLabel(text.upper())
        label.setStyleSheet(
            f"background:transparent; border:none; color:{t.text_muted}; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_XS}px; letter-spacing:0.6px;")
        row.insertWidget(1, label)
        return row

    def _build_get_key_button(self, t):
        """Where the SaaS puts "continue with Google".

        The plugin cannot do OAuth — there is no redirect to come back to — so
        the slot holds the route to getting a key instead.
        """
        self.get_key_btn = QPushButton("Get an API key")
        self.get_key_btn.setCursor(Qt_PointingHandCursor)
        self.get_key_btn.setMinimumHeight(40)
        self.get_key_btn.setAutoDefault(False)
        self.get_key_btn.setStyleSheet(
            f"QPushButton {{ background:{t.surface}; color:{t.text}; "
            f"border:1px solid {t.border_strong}; "
            f"border-radius:{design.RADIUS}px; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_MD}px; "
            f"font-weight:{design.WEIGHT_MEDIUM}; }}"
            f"QPushButton:hover {{ background:{t.surface_alt}; "
            f"border-color:{t.accent}; }}"
        )
        self.get_key_btn.setToolTip(f"Opens {APP_BASE_URL}/api-keys")
        self.get_key_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(f"{APP_BASE_URL}/api-keys"))
        )
        return self.get_key_btn

    def _build_error_label(self, t):
        """Where a refused key is reported.

        In the card, not in a message box: the panel is docked now, and a
        modal popup to say "that key is wrong" takes the user away from the
        field they need to correct. Hidden until there is something to say, so
        it costs no height in the normal case.
        """
        label = QLabel()
        label.setWordWrap(True)
        label.setAlignment(Qt_AlignCenter)
        label.setVisible(False)
        label.setStyleSheet(
            f"background:{t.error_soft}; "
            f"border:1px solid {design.alpha(t.error, 0.35)}; "
            f"border-radius:{design.RADIUS}px; padding:7px 10px; "
            f"color:{t.error}; font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_BASE}px;")
        self.error_label = label
        return label

    def _submit(self):
        """Hand the typed key to whoever is listening."""
        key = self.api_key_input.text().strip()
        if not key:
            self.show_error("Enter your API key to continue.")
            self.api_key_input.setFocus()
            return
        self.clear_error()
        self.sign_in_requested.emit(key)

    def show_error(self, message):
        self.error_label.setText(str(message))
        self.error_label.setVisible(True)

    def clear_error(self):
        self.error_label.clear()
        self.error_label.setVisible(False)

    def set_busy(self, busy, label="Signing in…"):
        """Disable the form while a key is being checked.

        Validation is a network round trip, and without this a second click
        starts a second one against the same field.
        """
        self.login_button.setEnabled(not busy)
        self.login_button.setText(label if busy else "Sign in")
        self.api_key_input.setEnabled(not busy)
        self._eye_btn.setEnabled(not busy)

    def focus_input(self):
        self.api_key_input.setFocus()

    def _build_signup_line(self, t):
        label = QLabel(
            f'<span style="color:{t.text_secondary};">'
            f"Don't have an account? </span>"
            f'<a href="{APP_BASE_URL}/auth?active_form=register" '
            f'style="color:{t.accent_text}; font-weight:600; '
            f'text-decoration:none;">Create one</a>'
        )
        label.setOpenExternalLinks(True)
        label.setAlignment(Qt_AlignCenter)
        label.setStyleSheet(
            f"background:transparent; border:none; "
            f"font-family:{design.FONT_STACK_HTML}; "
            f"font-size:{design.TEXT_BASE}px;")
        self.signup_label = label
        return label

    def _add_uninstall_button(self, layout):
        """Add uninstall button for testing purposes."""
        layout.addSpacing(30)
        uninstall_btn = QPushButton("🗑 Uninstall Keyring (Testing)")
        uninstall_btn.setCursor(Qt_PointingHandCursor)
        uninstall_btn.setStyleSheet("""
            QPushButton {
                background: #7f1d1d;
                color: white;
                border: 2px solid #991b1b;
                border-radius: 6px;
                padding: 10px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #991b1b;
            }
        """)
        uninstall_btn.clicked.connect(self._uninstall_keyring)
        layout.addWidget(uninstall_btn)

    def _toggle_api_key_visibility(self):
        """Flip the field between masked and visible.

        The label is a WORD, matching the button it is on. It used to swap
        between an eye and a see-no-evil monkey, which Windows renders as
        full-colour emoji at whatever size the host font chooses — the one
        glyph in the dialog that followed none of its styling.
        """
        if self.api_key_input.echoMode() == QLineEdit_Password:
            self.api_key_input.setEchoMode(QLineEdit_Normal)
            self._eye_btn.setText("Hide")
            self._eye_btn.setToolTip("Hide API key")
        else:
            self.api_key_input.setEchoMode(QLineEdit_Password)
            self._eye_btn.setText("Show")
            self._eye_btn.setToolTip("Show API key")

    def _uninstall_keyring(self):
        """Uninstall keyring package for testing purposes."""
        reply = QMessageBox.question(
            self,
            "Uninstall Keyring",
            "This will uninstall the keyring package.\nAre you sure?",
            QMessageBox_Yes | QMessageBox_No
        )

        if reply != QMessageBox_Yes:
            return

        try:
            # Find Python executable (same logic as installer)
            qgis_path = sys.executable

            if os.name == 'nt':  # Windows
                qgis_dir = os.path.dirname(qgis_path)
                python_candidates = [
                    os.path.join(qgis_dir, "python3.exe"),
                    os.path.join(qgis_dir, "python.exe"),
                    os.path.join(qgis_dir, "..", "bin", "python3.exe"),
                    os.path.join(qgis_dir, "..", "bin", "python.exe"),
                ]
                python_exe = None
                for candidate in python_candidates:
                    if os.path.exists(candidate):
                        python_exe = candidate
                        break
                if not python_exe:
                    python_exe = "python3"
            else:  # Linux/Mac
                python_exe = "python3"

            # Build uninstall command
            cmd = [python_exe, "-m", "pip", "uninstall", "keyring", "-y"]

            # Run uninstall (hide console window on Windows)
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            result = subprocess.run(  # nosec B603 - fixed args, no shell, trusted interpreter
                cmd,
                capture_output=True,
                text=True,
                startupinfo=startupinfo
            )

            if result.returncode == 0:
                QMessageBox.information(
                    self,
                    "Success",
                    "Keyring uninstalled successfully!\n\nRestart QGIS to test the install dialog."
                )
            else:
                QMessageBox.warning(
                    self,
                    "Uninstall Failed",
                    f"Failed to uninstall keyring:\n{result.stderr}"
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Error uninstalling keyring:\n{str(e)}"
            )


class LoginDialog(QDialog):
    """A window wrapper around :class:`LoginView`.

    The panel hosts the view directly now, so this exists for callers that
    genuinely want a modal — and so the plugin keeps a working fallback if the
    dock cannot be created. It forwards the attributes ``rasid_plugin`` reads
    (``api_key_input``, ``login_button``) rather than duplicating the form.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to GoPilot")
        self.setMinimumSize(DIALOG_WIDTH, DIALOG_HEIGHT)
        self.setObjectName("loginDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view = LoginView(self)
        layout.addWidget(self.view)

        # Accept on submit: a modal's job is to hand the key back to its
        # caller, not to validate it.
        self.view.sign_in_requested.connect(lambda _key: self.accept())

    # The form's controls, so existing call sites keep working unchanged.
    @property
    def api_key_input(self):
        return self.view.api_key_input

    @property
    def login_button(self):
        return self.view.login_button

    @property
    def get_key_btn(self):
        return self.view.get_key_btn

    @property
    def _eye_btn(self):
        return self.view._eye_btn

    def _toggle_api_key_visibility(self):
        self.view._toggle_api_key_visibility()
