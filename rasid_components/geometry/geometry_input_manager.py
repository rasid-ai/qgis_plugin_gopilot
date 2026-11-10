# -*- coding: utf-8 -*-
"""
Modular Geometry Input Manager

Provides a reusable interface for drawing geometries on the QGIS map canvas
and selecting layers from the project. Used across multiple components:
- GoPilot chat interface
- Process creation wizard
- Any other service that needs geometry input

Usage:
    from .geometry import GeometryInputManager

    # Initialize
    self.geo_manager = GeometryInputManager(iface, parent_widget=self)

    # Connect to completion signal
    self.geo_manager.geometry_ready.connect(self._on_geometry_ready)

    # Start drawing
    self.geo_manager.start_draw_mode()

    # Or pick a layer
    self.geo_manager.show_layer_picker()
"""

import json
from qgis.PyQt.QtCore import QObject, pyqtSignal, QTimer
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QListWidget, QHBoxLayout, QLabel, QMessageBox
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes, QgsJsonExporter, QgsCoordinateReferenceSystem, QgsCoordinateTransform
from ..compat import exec_dialog
from .. import design
from ..widgets import primary_button, secondary_button
from .modern_draw_tool import ModernDrawTool
from .drawing_help_dialog import DrawingHelpDialog


class GeometryInputManager(QObject):
    """Manages geometry input from drawing or layer selection.

    Signals:
        geometry_ready(dict): Emitted when geometry is ready. Dict contains:
            - 'geojson': GeoJSON dict with geometry data
            - 'source': 'draw' or 'layer'
            - 'name': Layer name (if from layer) or 'Drawn Polygon'

    Attributes:
        iface: QGIS interface object
        parent_widget: Parent QWidget for dialogs
        canvas: QGIS map canvas
    """

    geometry_ready = pyqtSignal(dict)  # {'geojson': {...}, 'source': 'draw'|'layer', 'name': str}
    draw_started = pyqtSignal()  # Emitted when drawing mode starts
    draw_cancelled = pyqtSignal()  # Emitted when drawing is cancelled

    def __init__(self, iface, parent_widget=None):
        """Initialize the geometry input manager.

        Args:
            iface: QGIS interface object (can be None for non-QGIS contexts)
            parent_widget: Parent QWidget for dialogs and message boxes
        """
        super().__init__(parent_widget)
        self.iface = iface
        self.parent_widget = parent_widget
        self.canvas = iface.mapCanvas() if iface else None

        # Drawing state
        self._draw_tool = None
        self._prev_map_tool = None
        self._parent_dialog = None

    def _host_window(self):
        """QGIS's own main window — the one thing we must never minimize.

        Returns None outside QGIS (``iface`` is optional), in which case
        nothing is treated as the host and a genuinely standalone parent
        window can still step aside.
        """
        try:
            return self.iface.mainWindow() if self.iface else None
        except (AttributeError, RuntimeError):
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Drawing Mode
    # ─────────────────────────────────────────────────────────────────────────

    def start_draw_mode(self, minimize_parent=False, show_help=True, help_context="general"):
        """Start drawing mode on the map canvas.

        Args:
            minimize_parent: If True, minimize the parent window so the canvas
                is visible. Defaults to False, and is ignored when the parent's
                top-level window is QGIS itself (see the guard below): a docked
                panel sits BESIDE the canvas, so there is nothing to get out of
                the way, and minimizing the host application to draw on it is
                never what the user asked for. Only a window this plugin owns
                can be stepped aside.
            show_help: If True, shows help dialog before starting (if user hasn't disabled it)
            help_context: Context for help dialog ('gopilot', 'wizard', 'general')
        """
        if not self.canvas:
            if self.parent_widget:
                QMessageBox.warning(
                    self.parent_widget,
                    "No Canvas",
                    "Map canvas not available. Cannot start drawing."
                )
            return

        # Show help dialog if enabled
        if show_help:
            if not DrawingHelpDialog.show_help(
                parent=self.parent_widget,
                title="How to Draw on Map",
                context=help_context
            ):
                # User cancelled the help dialog
                return

        # Store previous map tool to restore later
        self._prev_map_tool = self.canvas.mapTool()

        # Create and activate drawing tool
        self._draw_tool = ModernDrawTool(self.canvas)

        self._draw_tool.polygon_finished.connect(self._on_draw_finished)
        self.canvas.setMapTool(self._draw_tool)

        # Step the parent window aside only if the plugin OWNS that window.
        #
        # `parent_widget.window()` walks up to the top-level widget, and every
        # part of this plugin now lives inside a QDockWidget — so when the
        # panel is docked, that walk arrives at the QGIS MAIN WINDOW. Calling
        # showMinimized() on it minimized the whole application, and the
        # showNormal()/activateWindow()/raise_() that restored it afterwards
        # was what appeared to pick QGIS up and move it. Both were written
        # when the caller was a separate modal dialog, where minimizing the
        # dialog was the right thing and `window()` could only be the dialog.
        if minimize_parent and self.parent_widget:
            window = self.parent_widget.window()
            if window is not None and window is not self._host_window():
                self._parent_dialog = window
                window.showMinimized()

        self.draw_started.emit()

    def _on_draw_finished(self, coords):
        """Handle completed drawing.

        Args:
            coords: List of [lon, lat] coordinates in EPSG:4326
        """
        # Create GeoJSON polygon
        geojson = {
            "type": "Polygon",
            "coordinates": [coords]
        }

        # IMPORTANT: this slot is invoked synchronously from inside the draw
        # tool's own event handler (_finish, reached via a toolbar button click
        # or a canvas right-click). Deactivating / deleting the tool or swapping
        # the canvas map tool *now* is a reentrant use-after-free that crashes
        # QGIS. Defer the whole teardown to the next event-loop tick, once we
        # have safely unwound out of the tool's event handler.
        QTimer.singleShot(0, self._teardown_draw_tool)

        # Emit geometry ready signal
        self.geometry_ready.emit({
            'geojson': geojson,
            'source': 'draw',
            'name': 'Drawn Polygon'
        })

    def _teardown_draw_tool(self):
        """Tear down the draw tool and restore map state (deferred, safe)."""
        tool = self._draw_tool
        self._draw_tool = None
        if tool:
            try:
                tool.polygon_finished.disconnect(self._on_draw_finished)
            except (RuntimeError, TypeError):
                pass
            try:
                tool.deactivate()
            except RuntimeError:
                pass
            try:
                tool.deleteLater()
            except RuntimeError:
                pass

        # Restore previous state
        self._restore_map_state()

    def cancel_draw_mode(self):
        """Cancel drawing mode and restore previous state."""
        if self._draw_tool:
            self._draw_tool.reset()
        self._restore_map_state()
        self.draw_cancelled.emit()

    def _restore_map_state(self):
        """Restore map tool and parent window state."""
        # Restore previous map tool immediately
        if self._prev_map_tool and self.canvas:
            try:
                self.canvas.setMapTool(self._prev_map_tool)
            except RuntimeError:
                # Map tool was deleted
                pass
            self._prev_map_tool = None

        # Restore parent dialog with a small delay for modern tool
        # This ensures the floating toolbar closes cleanly before window restoration
        if self._parent_dialog:
            # Capture the dialog reference in the closure
            dialog = self._parent_dialog
            self._parent_dialog = None

            def restore_window():
                try:
                    # Check if dialog is still valid (not deleted)
                    if dialog and not dialog.isHidden():
                        dialog.showNormal()
                        dialog.activateWindow()
                        dialog.raise_()  # Bring to front
                except RuntimeError:
                    # Dialog was deleted, ignore
                    pass

            # Use timer for smooth restoration (especially with modern tool's toolbar)
            QTimer.singleShot(100, restore_window)

    # ─────────────────────────────────────────────────────────────────────────
    # Layer Picker
    # ─────────────────────────────────────────────────────────────────────────

    def show_layer_picker(self, title="Select Layer", message=None):
        """Show dialog to select a vector layer from the project.

        Args:
            title: Dialog window title
            message: Custom message to show in dialog (optional)

        Returns:
            bool: True if layer was selected, False if cancelled
        """
        if not self.parent_widget:
            return False

        # Get all vector layers
        layers = [layer for layer in QgsProject.instance().mapLayers().values()
                  if layer.type() == 0]  # 0 = Vector layer

        if not layers:
            QMessageBox.warning(
                self.parent_widget,
                "No Layers",
                "No vector layers found in the project."
            )
            return False

        # Create picker dialog.
        #
        # This dialog had NO styling at all — a bare QDialog, QLabel,
        # QListWidget and two raw QPushButtons — so it came up in the host
        # application's font and the host's list colours while the panel that
        # opened it was in Inter. It carried no hardcoded sizes or colours
        # either, which is exactly why a sweep for bad styling never found
        # it: there was nothing there to find.
        dialog = QDialog(self.parent_widget)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(420)
        dialog.setMinimumHeight(320)
        t = design.tokens()
        dialog.setObjectName("layerPicker")
        dialog.setStyleSheet(
            f"QDialog#layerPicker {{ background: {t.surface}; }}")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(design.SPACE_5, design.SPACE_5,
                                  design.SPACE_5, design.SPACE_5)
        layout.setSpacing(design.SPACE_3)

        # Message label
        if not message:
            message = "Select a vector layer to use as geometry input:"
        label = QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet(design.body_qss(design.TEXT_MD))
        layout.addWidget(label)

        # Layer list
        layer_list = QListWidget()
        layer_list.setStyleSheet(
            design.list_qss()
            + f"QListWidget {{ background:{t.surface_sunken}; "
              f"border:{design.BORDER}px solid {t.border}; }}"
        )
        for layer in layers:
            geom_type = QgsWkbTypes.geometryDisplayString(layer.geometryType())
            layer_list.addItem(
                f"{layer.name()} ({layer.featureCount()} features, {geom_type})"
            )
        layout.addWidget(layer_list)

        # Primary FIRST, Cancel second — see drawing_help_dialog for why this
        # deliberately differs from the SaaS footer. The two buttons were
        # already in this order; only their styling changed.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(design.SPACE_2)
        btn_row.addStretch()
        btn_ok = primary_button("Select")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(dialog.accept)
        btn_row.addWidget(btn_ok)
        btn_cancel = secondary_button("Cancel")
        btn_cancel.clicked.connect(dialog.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        # Show dialog and handle selection
        if exec_dialog(dialog) and layer_list.currentRow() >= 0:
            selected_layer = layers[layer_list.currentRow()]
            self._convert_layer_to_geojson(selected_layer)
            return True

        return False

    def _convert_layer_to_geojson(self, layer):
        """Convert selected layer to GeoJSON and emit signal.

        Args:
            layer: QgsVectorLayer to convert
        """
        try:
            # Validate layer type
            if not isinstance(layer, QgsVectorLayer):
                raise Exception("Not a vector layer")

            if layer.featureCount() == 0:
                raise Exception("Layer is empty (no features)")

            # Transform to WGS84 (EPSG:4326)
            src_crs = layer.crs()
            dst_crs = QgsCoordinateReferenceSystem("EPSG:4326")

            exporter = QgsJsonExporter(layer)
            if src_crs != dst_crs:
                xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
                exporter.setTransformGeometries(xform)

            # Export to GeoJSON string
            geojson_str = exporter.exportFeatures(layer.getFeatures())

            # Parse to dict
            geojson = json.loads(geojson_str)

            # Emit geometry ready signal
            self.geometry_ready.emit({
                'geojson': geojson,
                'source': 'layer',
                'name': layer.name()
            })

        except Exception as e:
            if self.parent_widget:
                QMessageBox.warning(
                    self.parent_widget,
                    "Layer Conversion Error",
                    f"Failed to convert layer to GeoJSON:\n{str(e)}"
                )

    # ─────────────────────────────────────────────────────────────────────────
    # Utility Methods
    # ─────────────────────────────────────────────────────────────────────────

    def is_drawing(self):
        """Check if currently in drawing mode.

        Returns:
            bool: True if drawing mode is active
        """
        return self._draw_tool is not None and self.canvas.mapTool() == self._draw_tool

    @staticmethod
    def reset_drawing_help():
        """Reset the 'don't show again' preference for the drawing help dialog.

        This allows users to see the help dialog again if they had previously
        disabled it.
        """
        DrawingHelpDialog.reset_preference()

    def cleanup(self):
        """Clean up resources and restore state."""
        if self._draw_tool:
            try:
                # Safely disconnect signal (correct method name)
                self._draw_tool.polygon_finished.disconnect(self._on_draw_finished)
            except (RuntimeError, TypeError):
                # Signal already disconnected or object deleted
                pass

            try:
                # Deactivate the tool first to clean up its resources
                self._draw_tool.deactivate()
            except RuntimeError:
                # Tool already deleted
                pass

            # Schedule deletion
            try:
                self._draw_tool.deleteLater()
            except RuntimeError:
                pass

            self._draw_tool = None

        self._restore_map_state()
