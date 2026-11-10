# -*- coding: utf-8 -*-
"""
Dependency installer for external Python packages.
Handles checking and installing required libraries in an isolated virtual environment.

Uses venv_manager for proper dependency isolation from QGIS Python environment.
"""

import sys
from qgis.PyQt.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QLabel, QTextEdit, QMessageBox,
)
from qgis.PyQt.QtCore import QThread, pyqtSignal
from ..compat import Qt_RichText, exec_dialog, QDialog_Accepted
# design/widgets/icons import nothing but Qt and the standard library, which
# is what makes them safe HERE: this dialog is what runs when the bundled
# packages are missing, so anything it touches has to work before a single
# dependency has been installed.
from .. import design
from ..widgets import primary_button, secondary_button
from . import venv_manager


class DependencyInstallerDialog(QDialog):
    """Dialog for installing required dependencies in an isolated virtual environment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.setup_ui()

    def setup_ui(self):
        """Setup the UI."""
        self.setWindowTitle("Install required libraries")
        self.setMinimumWidth(460)
        self.setMinimumHeight(200)

        t = design.tokens()

        # Scoped to this dialog. A bare `background:` here also
        # matched every child widget, so the dialog's own buttons and
        # inputs inherited a flat fill instead of their own styling —
        # and any child window would inherit it too. See
        # widgets.paint_transparent for the same mistake at a worse
        # spot in the tree.
        self.setObjectName("dependencyInstaller")
        self.setStyleSheet(
            f"QDialog#dependencyInstaller {{ background: {t.surface}; }}")

        layout = QVBoxLayout()
        layout.setSpacing(design.SPACE_3)
        layout.setContentsMargins(design.SPACE_5, design.SPACE_5,
                                  design.SPACE_5, design.SPACE_5)

        # The product's heading style rather than a bold 11pt QFont. A POINT
        # size is resolved against the screen's DPI and tracks nothing else in
        # the plugin, which is measured in px off design's type scale — so
        # this heading drifted away from every other heading in the panel.
        title = QLabel("Required libraries missing")
        title.setStyleSheet(design.heading_qss(design.TEXT_XL))
        layout.addWidget(title)

        # Description
        missing_packages = venv_manager.get_missing_packages()
        packages_display = ', '.join([pkg.split('>=')[0] for pkg in missing_packages])
        desc = QLabel(
            f"The QGIS GoPilot plugin needs to install <b>{packages_display}</b> "
            f"in an isolated virtual environment.\n\n"
            f"This keeps your QGIS Python environment clean and avoids conflicts.\n\n"
            f"Click Install to continue (requires internet connection)."
        )
        desc.setTextFormat(Qt_RichText)
        desc.setWordWrap(True)
        desc.setStyleSheet(design.body_qss(design.TEXT_MD, secondary=True))
        layout.addWidget(desc)

        # Progress/output text area. A console, so it is the one place in the
        # dialog that wants MONO_STACK — but design's stack, not a bare
        # 'Courier New', which is the only one of the five that Windows
        # actually has and is nobody's first choice of monospace.
        self.output_text = QTextEdit()
        self.output_text.setObjectName("installerOutput")
        self.output_text.setReadOnly(True)
        self.output_text.setVisible(False)
        self.output_text.setStyleSheet(
            f"""
            QTextEdit#installerOutput {{
                background: {t.surface_sunken};
                color: {t.text_secondary};
                border: {design.BORDER}px solid {t.border};
                border-radius: {design.RADIUS}px;
                padding: {design.SPACE_2}px;
                font-family: {design.MONO_STACK};
                font-size: {design.TEXT_SM}px;
            }}
            """
            + design.scrollbar_qss("QTextEdit#installerOutput")
        )
        layout.addWidget(self.output_text)

        # Add stretch to push buttons to bottom when output is hidden
        layout.addStretch()

        # A right-aligned row, secondary then primary — the button order every
        # other dialog in the plugin uses. These were two full-width stacked
        # buttons with Cancel UNDER Install, each hand-rolled with its own
        # radius, padding and weight, and the disabled state was #666 on #999
        # (a 1.6:1 grey pair belonging to no palette here).
        button_row = QHBoxLayout()
        button_row.setSpacing(design.SPACE_2)
        button_row.addStretch()

        # Primary FIRST, Cancel second — see drawing_help_dialog for why this
        # deliberately differs from the SaaS footer.
        #
        # No icon, for consistency with the other two dialogs swept with it.
        self.install_button = primary_button("Install")
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self.start_installation)
        button_row.addWidget(self.install_button)

        self.cancel_button = secondary_button("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)

        layout.addLayout(button_row)

        self.setLayout(layout)

    def start_installation(self):
        """Start the installation process."""
        self.install_button.setEnabled(False)
        self.output_text.setVisible(True)
        self.output_text.clear()
        self.setMinimumHeight(400)  # Expand dialog when showing output

        # Create and start worker thread
        self.worker = venv_manager.DepsInstallWorker(self)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.installation_finished)
        self.worker.start()

    def update_progress(self, text):
        """Update progress text."""
        self.output_text.append(text.rstrip())
        self.output_text.verticalScrollBar().setValue(
            self.output_text.verticalScrollBar().maximum()
        )

    def installation_finished(self, success, message):
        """Handle installation completion."""
        self.worker = None

        if success:
            self.output_text.append(f"\nDone — {message}")
            QMessageBox.information(
                self,
                "Success",
                "Libraries installed successfully in isolated virtual environment!\n\n"
                "The plugin will now load."
            )
            self.accept()
        else:
            self.output_text.append(f"\nFailed — {message}")
            missing = venv_manager.get_missing_packages()
            manual_install = ' '.join(missing)
            QMessageBox.critical(
                self,
                "Installation Failed",
                f"{message}\n\n"
                "Please try installing manually:\n"
                f"pip install {manual_install}"
            )
            self.install_button.setEnabled(True)


class DependencyManager:
    """Manages checking and installing dependencies using isolated virtual environment."""

    @staticmethod
    def check_package(package_name):
        """
        Check if a package is installed and importable.

        Args:
            package_name: Name of the package to check

        Returns:
            bool: True if package is available, False otherwise
        """
        # TESTING MODE: Uncomment the line below to force install dialog
        # return False

        # Ensure venv packages are on sys.path
        venv_manager.ensure_venv_packages_available()

        discoverable, _ = venv_manager._dependency_discoverable(package_name)
        return discoverable

    @staticmethod
    def get_required_packages():
        """
        Get required packages from venv_manager.

        Returns:
            list: List of required package names (import names, not pip specs)
        """
        return [import_name for import_name, _ in venv_manager.get_required_packages()]

    @staticmethod
    def check_dependencies():
        """
        Check if all required dependencies are installed.

        Returns:
            tuple: (all_installed: bool, missing_packages: list)
        """
        all_installed = venv_manager.all_dependencies_met()
        missing_specs = venv_manager.get_missing_packages()
        # Extract just the package names for backwards compatibility
        missing = [pkg.split('>=')[0].split('==')[0].split('<')[0].strip()
                   for pkg in missing_specs]

        return all_installed, missing

    @staticmethod
    def prompt_install(parent=None):
        """
        Check dependencies and prompt user to install if needed.

        Args:
            parent: Parent widget for dialog

        Returns:
            bool: True if all dependencies are available (either were already
                  installed or successfully installed now), False otherwise
        """
        # Check if Python version is supported
        if not venv_manager.python_runtime_supported():
            QMessageBox.critical(
                parent,
                "Python Version Not Supported",
                venv_manager.python_runtime_error()
            )
            return False

        all_installed, missing = DependencyManager.check_dependencies()

        if all_installed:
            return True

        # Show installation dialog
        dialog = DependencyInstallerDialog(parent)
        result = exec_dialog(dialog)

        if result == QDialog_Accepted:
            # Verify installation was successful
            all_installed, still_missing = DependencyManager.check_dependencies()
            if not all_installed:
                QMessageBox.warning(
                    parent,
                    "Installation Issue",
                    f"Some packages are still missing: {', '.join(still_missing)}\n\n"
                    "Please restart QGIS and try again."
                )
                return False
            return True

        return False
