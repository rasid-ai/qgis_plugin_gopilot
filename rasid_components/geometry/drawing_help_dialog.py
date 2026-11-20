# -*- coding: utf-8 -*-
"""
Drawing Help Dialog

Shows instructions to users on how to use the drawing tool.
Supports "Don't show again" preference.
"""

from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QVBoxLayout,
)
from qgis.PyQt.QtCore import Qt, QSettings, QPoint
from qgis.PyQt.QtGui import QIcon, QPixmap, QPainter, QColor, QPolygon
from ..compat import (
    Qt_AlignVCenter,
    Qt_transparent, QPainter_Antialiasing, QDialog_Accepted, exec_dialog,
)
from .. import design
from ..widgets import primary_button, secondary_button


def create_drawing_icon(size=64):
    """Create a drawing/pencil icon"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt_transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter_Antialiasing)

    # The same accent, and the same weak fill, as the rubber band the user is
    # about to draw — so the illustration looks like the thing it explains.
    t = design.tokens()
    accent = QColor(t.accent)
    fill = QColor(t.accent)
    fill.setAlpha(55)
    painter.setPen(accent)
    painter.setBrush(fill)

    # Draw polygon points
    points = [
        (size * 0.3, size * 0.5),
        (size * 0.5, size * 0.2),
        (size * 0.7, size * 0.4),
        (size * 0.6, size * 0.7),
    ]

    polygon = QPolygon([QPoint(int(x), int(y)) for x, y in points])
    painter.drawPolygon(polygon)

    # The click indicator is a VERTEX, so it is drawn as one: an accent ring
    # with a white centre, matching QgsVertexMarker on the canvas. It used to
    # be a solid #e74c3c dot, a red belonging to no palette here.
    painter.setPen(accent)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawEllipse(int(size * 0.3 - 4), int(size * 0.5 - 4), 8, 8)

    painter.end()
    return QIcon(pixmap)


class DrawingHelpDialog(QDialog):
    """Dialog showing instructions for using the drawing tool."""

    SETTINGS_KEY = "qgis_gopilot/show_drawing_help"

    def __init__(self, parent=None, title="How to Draw", context="general"):
        """Initialize the help dialog.

        Args:
            parent: Parent widget
            title: Dialog title
            context: Context for customized instructions ('gopilot', 'wizard', 'general')
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(450)
        self.setMinimumHeight(300)
        self.dont_show_again = False

        # Build UI
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Icon and title
        header_layout = QHBoxLayout()

        icon_label = QLabel()
        icon_label.setPixmap(create_drawing_icon(64).pixmap(64, 64))
        header_layout.addWidget(icon_label)

        # The product's own heading style and sentence case, rather than a
        # 16pt QFont — a point size does not track the panel's px scale, so
        # this heading was larger than any other in the plugin.
        title_label = QLabel("Drawing on the map")
        title_label.setStyleSheet(design.heading_qss(design.TEXT_XL))
        header_layout.addWidget(title_label, alignment=Qt_AlignVCenter)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Instructions
        instructions = self._get_instructions(context)

        for instruction in instructions:
            if not instruction.strip():
                # Instruction lists used to carry "" entries as spacers, and
                # rendering those as empty labels is what left this dialog
                # 643px tall for a dozen lines of text. The lists no longer
                # have any; this stays so that adding one cannot bring the
                # padding back.
                continue
            if instruction.startswith("---"):
                # Separator
                separator = QLabel()
                separator.setFixedHeight(1)
                separator.setStyleSheet(f"background:{design.tokens().border};")
                layout.addWidget(separator)
            else:
                # Instruction line
                instr_label = QLabel(instruction)
                instr_label.setWordWrap(True)
                # body_qss, not a hand-rolled rule: the hand-rolled one set a
                # colour and a size but no font-family, so every line of this
                # dialog rendered in the host application's font while the
                # rest of the plugin was in Inter.
                instr_label.setStyleSheet(
                    design.body_qss(design.TEXT_MD) + " padding:4px 0;")
                layout.addWidget(instr_label)

        layout.addSpacing(8)

        # Tips section
        # A heading, so it uses the heading style — and at TEXT_MD, not
        # TEXT_BASE. At 12px bold it was SMALLER than the 13px body it
        # introduces, which reads as a footnote rather than a section label.
        tips_label = QLabel("Tips")
        tips_label.setStyleSheet(
            design.heading_qss(design.TEXT_MD) + " padding:4px 0;")
        layout.addWidget(tips_label)

        tips = [
            "You need at least three points to close an area",
            # Was "The panel steps aside while you draw, then comes back",
            # which described a minimize/restore that no longer happens \u2014
            # and never should have, since it minimized QGIS itself.
            "The panel stays where it is; drawing controls float on the map",
            "Each click adds one point \u2014 Backspace removes the last",
        ]

        for tip in tips:
            tip_label = QLabel(f"\u00b7  {tip}")
            tip_label.setStyleSheet(
                design.body_qss(design.TEXT_SM, secondary=True)
                + " padding:2px 0 2px 16px;")
            layout.addWidget(tip_label)

        layout.addStretch()

        # Don't show again checkbox
        self.checkbox = QCheckBox("Don't show this again")
        # design.radio_qss styles QCheckBox too, so the box matches the ones
        # in the process wizard instead of being the host style's default.
        self.checkbox.setStyleSheet(design.radio_qss(design.TEXT_BASE))
        layout.addWidget(self.checkbox)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        # Primary FIRST, Cancel second — the order the process wizard already
        # used and the one this product wants. It is deliberately not the
        # SaaS's `justify-end` footer, which puts Cancel first; if you are
        # here to make the two match, this is the difference and it is on
        # purpose.
        #
        # No icon, like every other dialog footer. Icons stay on the buttons
        # that sit IN a page ("Draw on map", "Choose file"), where they help
        # a control stand out from body copy; a dialog footer has two buttons
        # and no such competition.
        self.ok_btn = primary_button("Start drawing")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.ok_btn)

        self.cancel_btn = secondary_button("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

        # Scoped by object name. A bare ``QDialog`` rule matches this dialog
        # AND any QDialog inside it — QMessageBox is a QDialog — which is the
        # leak that produced black message boxes elsewhere in the plugin.
        self.setObjectName("drawingHelpDialog")
        self.setStyleSheet(
            f"QDialog#drawingHelpDialog {{ "
            f"background:{design.tokens().surface}; }}")

    def _get_instructions(self, context):
        """Get context-specific instructions.

        Args:
            context: 'gopilot', 'wizard', or 'general'

        Returns:
            List of instruction strings
        """
        # No "How to draw a polygon on the map:" lead-in: the dialog is
        # titled "Drawing on the map", so the line restated the heading
        # directly above it. The "" spacers it was separated by are gone with
        # it — the layout's spacing and each label's padding do that job.
        base_instructions = [
            "<b>Left-click</b> the map to add a point",
            "<b>Right-click</b> to close the area and finish",
        ]

        if context == "gopilot":
            base_instructions.append("Your drawn geometry will be attached to your message and sent to the AI.")
        elif context == "wizard":
            base_instructions.append("Your drawn polygon will be used as the Area of Interest (AOI) for processing.")
        else:
            base_instructions.append("Your drawn polygon will be used as the geometry input.")

        return base_instructions

    def accept(self):
        """Handle OK button click."""
        if self.checkbox.isChecked():
            self.dont_show_again = True
            self._save_preference(False)
        super().accept()

    def _save_preference(self, show_again):
        """Save user preference to not show dialog again.

        Args:
            show_again: Whether to show the dialog in the future
        """
        settings = QSettings()
        settings.setValue(self.SETTINGS_KEY, show_again)

    @classmethod
    def should_show(cls):
        """Check if dialog should be shown based on user preference.

        Returns:
            bool: True if dialog should be shown
        """
        settings = QSettings()
        # Default to True (show help) if not set
        return settings.value(cls.SETTINGS_KEY, True, type=bool)

    @classmethod
    def reset_preference(cls):
        """Reset the preference to show the dialog again."""
        settings = QSettings()
        settings.setValue(cls.SETTINGS_KEY, True)

    @classmethod
    def show_help(cls, parent=None, title="How to Draw", context="general"):
        """Show the help dialog if user hasn't disabled it.

        Args:
            parent: Parent widget
            title: Dialog title
            context: Context for instructions ('gopilot', 'wizard', 'general')

        Returns:
            bool: True if user clicked OK to continue, False if cancelled
        """
        if not cls.should_show():
            return True  # Don't show, but continue

        dialog = cls(parent, title, context)
        result = exec_dialog(dialog)
        return result == QDialog_Accepted
