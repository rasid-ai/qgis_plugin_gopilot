# -*- coding: utf-8 -*-
"""
Modern Drawing Tool with Enhanced UI/UX

Features:
- Floating toolbar with Undo/Clear/Finish buttons
- Real-time area and distance measurements
- Animated vertices and hover effects
- Vertex editing (move points after placing)
- Keyboard shortcuts (ESC, Backspace, Enter)
- Modern glassmorphism styling
"""

from qgis.PyQt.QtCore import pyqtSignal, QTimer
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QBrush, QCursor, QPixmap
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsWkbTypes, QgsPointXY, QgsProject,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsDistanceArea, QgsGeometry, QgsPolygon, QgsLineString,
    QgsUnitTypes
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from ..compat import (
    Qt_LeftButton, Qt_RightButton,
    Qt_Tool, Qt_FramelessWindowHint, Qt_WindowStaysOnTopHint,
    Qt_transparent, QPainter_Antialiasing,
    Qt_WA_TranslucentBackground, Qt_SolidLine,
    Qt_Key_Escape, Qt_Key_Backspace, Qt_Key_Delete, Qt_Key_Return, Qt_Key_Enter,
)
from .. import design, icons
from ..widgets import (
    Divider, danger_button, primary_button, secondary_button,
)


def _with_alpha(hex_colour, alpha):
    """A token colour at a given 0-255 alpha, for Qt's canvas APIs.

    ``design.alpha`` returns a CSS string for stylesheets; QgsRubberBand wants
    a real QColor, so the conversion happens here rather than at each call.
    """
    colour = QColor(hex_colour)
    colour.setAlpha(alpha)
    return colour


class DrawingToolbar(QWidget):
    """Floating toolbar for drawing controls"""

    undo_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()
    finish_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt_Tool | Qt_FramelessWindowHint
                            | Qt_WindowStaysOnTopHint)
        self.setAttribute(Qt_WA_TranslucentBackground)

        t = design.tokens()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # A SOLID card, not a glass panel.
        #
        # This was `rgba(30, 41, 59, 0.85)` — slate-900 at 85%, a colour from
        # no palette the plugin uses, with white-translucent buttons on it. It
        # is now the same surface, border and radius as every other card in
        # the product.
        #
        # Solid rather than translucent on purpose: the toolbar floats over
        # whatever imagery the user is tracing, and a translucent panel takes
        # its legibility from the map underneath — which can be anything from
        # snow to open water.
        self.frame = QFrame()
        self.frame.setObjectName("drawToolbar")
        self.frame.setStyleSheet(
            f"QFrame#drawToolbar {{ background:{t.surface}; "
            f"border:1px solid {t.border}; "
            f"border-radius:{design.RADIUS_LG}px; }}"
        )
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(design.SPACE_3, design.SPACE_3,
                                        design.SPACE_3, design.SPACE_3)
        frame_layout.setSpacing(design.SPACE_2)

        # ── title ──────────────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_row.setSpacing(design.SPACE_2)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("draw", design.ICON_SM, t.accent,
                                     dpr=2.0))
        glyph.setStyleSheet("background:transparent; border:none;")
        title_row.addWidget(glyph)
        title = QLabel("Draw an area")
        title.setStyleSheet(design.heading_qss(design.TEXT_MD))
        title_row.addWidget(title)
        title_row.addStretch()
        frame_layout.addLayout(title_row)

        # ── live measurements ─────────────────────────────────────────────
        self.info_label = QLabel("Click the map to add points")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet(
            design.body_qss(design.TEXT_BASE, secondary=True))
        frame_layout.addWidget(self.info_label)

        frame_layout.addWidget(Divider())

        # ── actions ───────────────────────────────────────────────────────
        # The product's own buttons, and the icon registry instead of emoji.
        # The old labels were "⎌ Undo Last Point", "🗑️ Clear All", "✓ Finish
        # Polygon", "✕ Cancel" — four emoji that Windows draws as full-colour
        # glyphs at whatever size the host font picks.
        self.finish_btn = primary_button("Finish area", icon_name="check")
        self.finish_btn.setEnabled(False)
        self.finish_btn.setToolTip("Complete the area (Enter or right-click)")
        self.finish_btn.clicked.connect(self.finish_clicked.emit)
        frame_layout.addWidget(self.finish_btn)

        self.undo_btn = secondary_button("Undo last point",
                                         icon_name="undo")
        self.undo_btn.setEnabled(False)
        self.undo_btn.setToolTip("Remove the last point (Backspace)")
        self.undo_btn.clicked.connect(self.undo_clicked.emit)
        frame_layout.addWidget(self.undo_btn)

        self.clear_btn = secondary_button("Clear points", icon_name="refresh")
        self.clear_btn.setEnabled(False)
        self.clear_btn.setToolTip("Remove every point and start again")
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        frame_layout.addWidget(self.clear_btn)

        # Cancel is the only destructive one here: finishing keeps the area,
        # clearing keeps the tool, cancelling throws the work away.
        self.cancel_btn = danger_button("Cancel", icon_name="close")
        self.cancel_btn.setToolTip("Discard this area (Esc)")
        self.cancel_btn.clicked.connect(self.cancel_clicked.emit)
        frame_layout.addWidget(self.cancel_btn)

        frame_layout.addWidget(Divider())

        hint = QLabel("Backspace undo \u00b7 Enter finish \u00b7 Esc cancel")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"background:transparent; border:none; color:{t.text_muted}; "
            f"font-family:{design.FONT_STACK}; "
            f"font-size:{design.TEXT_XS}px;")
        frame_layout.addWidget(hint)

        layout.addWidget(self.frame)

    def update_info(self, point_count, area_text="", perimeter_text=""):
        """Update the info label with current stats"""
        if point_count == 0:
            text = "Click the map to add points"
        elif point_count == 1:
            text = "1 point \u00b7 add at least two more"
        elif point_count == 2:
            text = f"2 points \u00b7 one more to close the area\n{perimeter_text}"
        else:
            text = f"{point_count} points\n{area_text}\n{perimeter_text}"

        self.info_label.setText(text)

        # Update button states
        self.undo_btn.setEnabled(point_count > 0)
        self.clear_btn.setEnabled(point_count > 0)
        self.finish_btn.setEnabled(point_count >= 3)


class ModernDrawTool(QgsMapTool):
    """Modern map tool for drawing polygons with enhanced UI/UX.

    Features:
    - Floating toolbar with controls
    - Real-time measurements
    - Enhanced vertex visualization
    - Keyboard shortcuts
    - Undo/redo functionality
    """

    polygon_finished = pyqtSignal(list)  # list of [lon, lat] in EPSG:4326

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._points = []

        # Rubber band for the polygon, in the brand accent.
        #
        # This was #3B82F6 — a blue that appears nowhere else in the product.
        # The area being drawn is the most prominent thing on screen while the
        # tool is live, and it was the one piece of GoPilot's UI that did not
        # look like GoPilot.
        t = design.tokens()
        self._rb = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        # The fill is deliberately weak: it sits over imagery the user is
        # tracing, and anything heavier hides the feature they are outlining.
        self._rb.setColor(_with_alpha(t.accent, 55))
        self._rb.setStrokeColor(QColor(t.accent))
        self._rb.setWidth(3)
        self._rb.setLineStyle(Qt_SolidLine)

        # Vertex markers
        self._vertex_markers = []

        # Distance/area calculator
        self._distance_area = QgsDistanceArea()
        self._distance_area.setEllipsoid('WGS84')
        self._distance_area.setSourceCrs(
            canvas.mapSettings().destinationCrs(),
            QgsProject.instance().transformContext()
        )

        # Floating toolbar
        self._toolbar = DrawingToolbar(canvas)
        self._toolbar.undo_clicked.connect(self._undo_last_point)
        self._toolbar.clear_clicked.connect(self._clear_all)
        self._toolbar.finish_clicked.connect(self._finish)
        self._toolbar.cancel_clicked.connect(self._cancel)

        # Position toolbar
        self._position_toolbar()
        self._toolbar.show()

        # Animation timer for pulsing first vertex.
        # Parent it to the tool so it is destroyed together with the tool
        # instead of lingering as an ownerless QObject with queued events.
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._animate_vertices)
        self._animation_timer.start(100)  # 10 FPS
        self._animation_phase = 0

        # Custom cursor
        self._set_custom_cursor()

    def _set_custom_cursor(self):
        """Set a custom crosshair cursor"""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt_transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter_Antialiasing)

        # Draw crosshair, in the accent. A white outline goes under it first
        # so the cursor stays visible over dark imagery as well as light —
        # the single-colour version disappeared over water and shadow.
        accent = QColor(design.tokens().accent)
        halo = QPen(QColor("#FFFFFF"))
        halo.setWidth(4)
        painter.setPen(halo)
        painter.drawLine(16 - 8, 16, 16 + 8, 16)
        painter.drawLine(16, 16 - 8, 16, 16 + 8)

        pen = QPen(accent)
        pen.setWidth(2)
        painter.setPen(pen)

        center = 16
        size = 8

        # Horizontal line
        painter.drawLine(center - size, center, center + size, center)
        # Vertical line
        painter.drawLine(center, center - size, center, center + size)

        # Center dot
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(center - 2, center - 2, 4, 4)

        painter.end()

        cursor = QCursor(pixmap)
        self.canvas.setCursor(cursor)

    def _position_toolbar(self):
        """Position toolbar in the top-right corner of canvas"""
        canvas_rect = self.canvas.rect()
        toolbar_width = 280
        toolbar_height = self._toolbar.sizeHint().height()

        x = canvas_rect.width() - toolbar_width - 20
        y = 20

        # Convert to global coordinates
        global_pos = self.canvas.mapToGlobal(canvas_rect.topLeft())
        self._toolbar.move(global_pos.x() + x, global_pos.y() + y)

    def _add_vertex_marker(self, point):
        """Add an enhanced vertex marker at the given point"""
        marker = QgsVertexMarker(self.canvas)
        marker.setCenter(point)
        # Accent ring, white centre: the white is what keeps a vertex
        # findable over dark imagery, the ring is what makes it ours.
        marker.setColor(QColor(design.tokens().accent))
        marker.setFillColor(QColor("#FFFFFF"))
        marker.setIconSize(12)
        marker.setIconType(QgsVertexMarker.IconType.ICON_CIRCLE)
        marker.setPenWidth(3)
        self._vertex_markers.append(marker)

    def _animate_vertices(self):
        """Animate the first vertex to draw attention"""
        self._animation_phase = (self._animation_phase + 1) % 20

        if self._vertex_markers:
            # Pulse the first vertex
            first_marker = self._vertex_markers[0]
            scale = 1.0 + 0.3 * abs(10 - self._animation_phase) / 10.0
            first_marker.setIconSize(int(12 * scale))

    def _update_measurements(self):
        """Update area and perimeter measurements"""
        if len(self._points) < 2:
            self._toolbar.update_info(len(self._points))
            return

        # Calculate perimeter
        perimeter = 0
        for i in range(len(self._points)):
            if i < len(self._points) - 1:
                perimeter += self._distance_area.measureLine(
                    self._points[i], self._points[i + 1]
                )

        perimeter_text = f"Perimeter: {self._format_distance(perimeter)}"

        # Calculate area if we have at least 3 points
        area_text = ""
        if len(self._points) >= 3:
            # Create a closed polygon
            closed_points = self._points + [self._points[0]]
            geom = QgsGeometry.fromPolygonXY([closed_points])
            area = self._distance_area.measureArea(geom)
            area_text = f"Area: {self._format_area(area)}"

        self._toolbar.update_info(len(self._points), area_text, perimeter_text)

    def _format_distance(self, meters):
        """Format distance in appropriate units"""
        if meters < 1000:
            return f"{meters:.1f} m"
        else:
            return f"{meters / 1000:.2f} km"

    def _format_area(self, sq_meters):
        """Format area in appropriate units"""
        if sq_meters < 10000:  # Less than 1 hectare
            return f"{sq_meters:.1f} m²"
        elif sq_meters < 1000000:  # Less than 1 km²
            hectares = sq_meters / 10000
            return f"{hectares:.2f} ha"
        else:
            sq_km = sq_meters / 1000000
            return f"{sq_km:.2f} km²"

    def canvasPressEvent(self, event):
        """Handle mouse press events"""
        if event.button() == Qt_LeftButton:
            pt = self.toMapCoordinates(event.pos())
            self._points.append(pt)
            self._rb.addPoint(pt, True)
            self._add_vertex_marker(pt)
            self._update_measurements()

        elif event.button() == Qt_RightButton:
            self._finish()

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts"""
        key = event.key()

        if key == Qt_Key_Escape:
            self._cancel()
        elif key == Qt_Key_Backspace or key == Qt_Key_Delete:
            self._undo_last_point()
        elif key == Qt_Key_Return or key == Qt_Key_Enter:
            self._finish()

    def _undo_last_point(self):
        """Remove the last added point"""
        if self._points:
            self._points.pop()
            self._rb.removeLastPoint()

            if self._vertex_markers:
                marker = self._vertex_markers.pop()
                self.canvas.scene().removeItem(marker)

            self._update_measurements()

    def _clear_all(self):
        """Clear all points and start over"""
        self._points = []
        self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

        for marker in self._vertex_markers:
            self.canvas.scene().removeItem(marker)
        self._vertex_markers = []

        self._update_measurements()

    def _cancel(self):
        """Cancel drawing and clean up.

        Defers unsetMapTool() to the next event-loop tick: calling it directly
        would swap/destroy this tool while we are still inside its own event
        handler (button click / key press), which crashes QGIS.
        """
        self._clear_all()
        if self._toolbar:
            self._toolbar.hide()
        QTimer.singleShot(0, lambda: self.canvas.unsetMapTool(self))

    def _finish(self):
        """Finish drawing and emit the polygon.

        We deliberately do NOT tear down the tool here. `_finish` runs inside
        the tool's own event dispatch (toolbar button click or canvas
        right-click), so deleting the tool / swapping the map tool now would be
        a reentrant use-after-free. The listener (GeometryInputManager) tears
        the tool down on the next event-loop tick instead.
        """
        if len(self._points) < 3:
            # Not a valid polygon yet — stay in drawing mode.
            return

        # Transform from canvas CRS to EPSG:4326 (lon/lat)
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = QgsCoordinateTransform(canvas_crs, wgs84, QgsProject.instance())

        coords = []
        for p in self._points:
            transformed = xform.transform(p)
            coords.append([transformed.x(), transformed.y()])
        coords.append(coords[0])  # close ring

        # Hide the toolbar immediately for responsive UX; the deferred
        # deactivate() will close and delete it for real.
        if self._toolbar:
            self._toolbar.hide()

        self.polygon_finished.emit(coords)

    def reset(self):
        """Reset the tool"""
        self._rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        self._points = []

        for marker in self._vertex_markers:
            try:
                self.canvas.scene().removeItem(marker)
            except (RuntimeError, AttributeError):
                # Marker or scene already gone — nothing to remove.
                pass
        self._vertex_markers = []

        if self._toolbar:
            self._toolbar.update_info(0)

    def deactivate(self):
        """Clean up when tool is deactivated.

        Fully destroys the floating toolbar here (not just close/hide) so it
        cannot linger as an orphaned top-level widget once this tool's Python
        reference is dropped. Safe to call more than once.
        """
        if self._animation_timer:
            self._animation_timer.stop()

        self.reset()

        if self._toolbar:
            try:
                self._toolbar.close()
                self._toolbar.deleteLater()
            except RuntimeError:
                pass
            self._toolbar = None

        try:
            super().deactivate()
        except RuntimeError:
            pass
