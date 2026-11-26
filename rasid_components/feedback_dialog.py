from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QMessageBox
)
from qgis.PyQt.QtCore import QSize, QThread, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from .compat import (
    Qt_AlignCenter, Qt_AlignLeft, Qt_PointingHandCursor, Qt_RichText, Qt_TextBrowserInteraction,
    exec_dialog, QMessageBox_Information
)
from . import design, icons
from .config import APP_BASE_URL, plugin_version
from .widgets import primary_button, secondary_button
import platform

# A rating star. Gold when earned, the neutral border otherwise.
#
# Previously the glyphs "☆" and "★" at font-size:24px, which the HOST font
# draws — the same class of thing removed from the draw toolbar, and the
# reason a rating could render as two different shapes on two machines.
# `icons.pixmap` draws both states here, so they are one shape at one weight.
STAR_BOX = 38
STAR_GLYPH = 20


def _star_qss(filled):
    t = design.tokens()
    edge = design.GOLD["DEFAULT"] if filled else t.border_strong
    return f"""
        QPushButton {{
            background: transparent;
            border: {design.BORDER}px solid {edge};
            border-radius: {design.RADIUS_SM}px;
            padding: 0;
        }}
        QPushButton:hover {{ border-color: {design.GOLD["DEFAULT"]}; }}
    """


class SubmitFeedbackThread(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, client, feedback_data):
        super().__init__()
        self.client = client
        self.feedback_data = feedback_data

    def run(self):
        try:
            result = self.client.submit_feedback(self.feedback_data)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class FeedbackDialog(QDialog):
    """Modern feedback dialog matching web app design."""

    def __init__(self, client, current_page=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.current_page = current_page
        self._threads = []
        self._selected_rating = 0
        self._star_buttons = []

        self.setWindowTitle("Feedback")
        self.setMinimumWidth(460)
        self.setMinimumHeight(380)
        t = design.tokens()
        # Scoped to this dialog. A bare `background:` here also
        # matched every child widget, so the dialog's own buttons and
        # inputs inherited a flat fill instead of their own styling —
        # and any child window would inherit it too. See
        # widgets.paint_transparent for the same mistake at a worse
        # spot in the tree.
        self.setObjectName("feedbackDialog")
        self.setStyleSheet(
            f"QDialog#feedbackDialog {{ background: {t.surface}; }}")

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(design.SPACE_6, design.SPACE_6,
                                       design.SPACE_6, design.SPACE_6)
        main_layout.setSpacing(design.SPACE_4)

        # Header
        title = QLabel("Provide feedback")
        title.setAlignment(Qt_AlignCenter)
        title.setStyleSheet(design.heading_qss(design.TEXT_XL))
        main_layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("We value your input to improve the platform.")
        subtitle.setAlignment(Qt_AlignCenter)
        subtitle.setStyleSheet(
            design.body_qss(design.TEXT_BASE, secondary=True))
        main_layout.addWidget(subtitle)

        main_layout.addSpacing(design.SPACE_2)

        # Star rating section
        rating_label = QLabel("How would you rate your experience?")
        rating_label.setStyleSheet(design.field_label_qss())
        main_layout.addWidget(rating_label)

        # Star buttons
        stars_layout = QHBoxLayout()
        stars_layout.setSpacing(design.SPACE_2)
        stars_layout.setAlignment(Qt_AlignLeft)

        for i in range(1, 6):
            star_btn = QPushButton()
            star_btn.setFixedSize(STAR_BOX, STAR_BOX)
            star_btn.setIconSize(QSize(STAR_GLYPH, STAR_GLYPH))
            star_btn.setCursor(Qt_PointingHandCursor)
            star_btn.setProperty("star_value", i)
            star_btn.setToolTip(f"{i} out of 5")
            star_btn.clicked.connect(lambda checked, val=i: self._set_rating(val))
            stars_layout.addWidget(star_btn)
            self._star_buttons.append(star_btn)

        main_layout.addLayout(stars_layout)
        # Paint the empty state through the same path that paints the filled
        # one, so there is exactly one place where a star's look is decided.
        self._set_rating(0)

        main_layout.addSpacing(design.SPACE_2)

        # Message section
        message_label = QLabel("Your message *")
        message_label.setStyleSheet(design.field_label_qss())
        main_layout.addWidget(message_label)

        self.message_input = QTextEdit()
        self.message_input.setPlaceholderText("Tell us what you like or what we can improve...")
        self.message_input.setMinimumHeight(100)
        self.message_input.setStyleSheet(
            design.input_qss() + design.scrollbar_qss("QTextEdit"))
        main_layout.addWidget(self.message_input)

        main_layout.addStretch()

        # Button bar
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(design.SPACE_2)
        btn_layout.addStretch()

        # Primary FIRST, Cancel second — see drawing_help_dialog for why this
        # deliberately differs from the SaaS footer.
        #
        # No icon. The SaaS renders this button as the bare string
        # "Submit Feedback" and only ever puts a glyph in it for the pending
        # spinner, so an icon at rest is the plugin inventing decoration the
        # product does not have.
        self.submit_btn = primary_button("Submit feedback")
        self.submit_btn.setDefault(True)
        self.submit_btn.clicked.connect(self._submit_feedback)
        btn_layout.addWidget(self.submit_btn)

        # secondary_button, not ghost_button: every other Cancel in the plugin
        # is the bordered one, and so is the SaaS's own
        # (`bg-white border border-slate-300`). The borderless version made
        # this the odd dialog out and read as a link rather than a button.
        cancel_btn = secondary_button("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        main_layout.addLayout(btn_layout)

    def _set_rating(self, rating):
        """Paint the stars for ``rating``, and remember it.

        One branch per star rather than the three near-identical stylesheet
        blocks this used to carry, two of which were byte-for-byte the same
        apart from the border colour.
        """
        self._selected_rating = rating
        t = design.tokens()
        for index, button in enumerate(self._star_buttons, start=1):
            filled = index <= rating
            colour = design.GOLD["DEFAULT"] if filled else t.text_muted
            name = "star-filled" if filled else "star"
            button.setIcon(QIcon(
                icons.pixmap(name, STAR_GLYPH, colour, dpr=2.0)))
            button.setStyleSheet(_star_qss(filled))

    def _submit_feedback(self):
        """Submit feedback to API."""
        message = self.message_input.toPlainText().strip()
        if not message:
            QMessageBox.warning(
                self, "Missing Message",
                "Please enter your feedback message."
            )
            return

        # Build feedback data
        feedback_data = {
            "message": message,
            "rating": self._selected_rating,
            "feedback_infos": {
                "plugin_version": plugin_version(),
                "platform": platform.system(),
                "platform_version": platform.version(),
                "current_page": self.current_page or "unknown"
            }
        }

        # Disable during submission
        self.submit_btn.setEnabled(False)
        self.submit_btn.setText("Submitting...")

        # Submit in background
        thread = SubmitFeedbackThread(self.client, feedback_data)
        thread.finished.connect(self._on_submit_success)
        thread.error.connect(self._on_submit_error)
        self._threads.append(thread)
        thread.start()

    def _on_submit_success(self, result):
        """Handle successful submission."""
        self.submit_btn.setEnabled(True)
        self.submit_btn.setText("Submit feedback")

        # Create message box with clickable link
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Thank You!")
        msg_box.setIcon(QMessageBox_Information)
        msg_box.setText(
            "Your feedback has been submitted successfully.<br><br>"
            "We appreciate you taking the time to help us improve!<br><br>"
            "To view and manage all your feedbacks, visit:<br>"
            f'<a href="{APP_BASE_URL}/feedback">{APP_BASE_URL}/feedback</a>'
        )
        msg_box.setTextFormat(Qt_RichText)
        msg_box.setTextInteractionFlags(Qt_TextBrowserInteraction)
        exec_dialog(msg_box)

        self.accept()  # Close dialog

    def _on_submit_error(self, error_msg):
        """Handle submission error."""
        self.submit_btn.setEnabled(True)
        self.submit_btn.setText("Submit feedback")

        QMessageBox.critical(
            self, "Submission Failed",
            f"Failed to submit feedback:\n\n{error_msg}\n\n"
            f"Please try again."
        )
