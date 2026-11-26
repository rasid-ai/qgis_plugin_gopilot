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

from qgis.PyQt.QtCore import pyqtSignal, QPoint, QTimer
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QBrush, QCursor, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
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
from .. import design, icons, prefs
from ..widgets import IconButton, primary_button
from ..window_drag import WindowDragFilter


def _with_alpha(hex_colour, alpha):
    """A token colour at a given 0-255 alpha, for Qt's canvas APIs.

    ``design.alpha`` returns a CSS string for stylesheets; QgsRubberBand wants
    a real QColor, so the conversion happens here rather than at each call.
    """
    colour = QColor(hex_colour)
    colour.setAlpha(alpha)
    return colour


class DrawingToolbar(QWidget):
    """Floating draw controls: ONE ROW, movable, parked where you leave it.

    SHAPE
    =====
    This was a 280x300 card — a title, three lines of measurements, four
    full-width stacked buttons and a keyboard-hint line. Measured, that is
    9% of the area of a 1280x720 canvas and 42% of its HEIGHT, sitting on top
    of the imagery the user is tracing, and it could not be moved out of the
    way. It is now a single ~40px strip: the measurements read inline, only
    the primary action keeps its label, and the other three are icon buttons
    whose tooltips carry the wording and the shortcut. About 1.5% of the same
    canvas.

    MOVABLE
    =======
    Frameless windows have no caption for the OS to drag, so the move
    arithmetic is ours — :class:`~..window_drag.WindowDragFilter`, the same
    one the undocked panel uses. The whole strip is a handle except where a
    button sits, since a button consumes its own press. The position is
    remembered between draws, because a bar you have to re-park on every
    polygon is barely better than one that cannot move.
    """

    undo_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()
    finish_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()

    # Kept off the canvas edges when falling back to the default corner, and
    # used to decide whether a remembered position is still reachable.
    EDGE_MARGIN = 16

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
        frame_layout = QHBoxLayout(self.frame)
        frame_layout.setContentsMargins(design.SPACE_2, design.SPACE_1,
                                        design.SPACE_2, design.SPACE_1)
        frame_layout.setSpacing(design.SPACE_2)

        # ── drag handle ───────────────────────────────────────
        self.grip = QLabel()
        self.grip.setPixmap(icons.pixmap("grip", design.ICON_SM,
                                         t.text_muted, dpr=2.0))
        self.grip.setToolTip("Drag to move these controls")
        self.grip.setStyleSheet("background:transparent; border:none;")
        frame_layout.addWidget(self.grip)

        # ── live measurements, inline ──────────────────────────
        # No word wrap: wrapping is what let this grow to three lines. The
        # strip stays one row, and the text is written short enough to fit.
        self.info_label = QLabel("Click the map to add points")
        self.info_label.setStyleSheet(
            design.body_qss(design.TEXT_BASE, secondary=True))
        frame_layout.addWidget(self.info_label)
        frame_layout.addStretch()

        # ── actions ─────────────────────────────────────────
        # Only the primary action keeps a label. The other three became icon
        # buttons: a third of the width each, and their tooltips carry both
        # the wording and the shortcut, which is what the removed hint line
        # was for. (The labels before that were "⌬ Undo Last Point",
        # "🗑️ Clear All", "✓ Finish Polygon", "✕ Cancel" — emoji the host
        # font drew at whatever size it liked.)
        self.finish_btn = primary_button("Finish")
        self.finish_btn.setEnabled(False)
        self.finish_btn.setToolTip("Complete the area (Enter or right-click)")
        self.finish_btn.clicked.connect(self.finish_clicked.emit)
        frame_layout.addWidget(self.finish_btn)

        self.undo_btn = IconButton(
            "undo", size=28, icon_size=14,
            tooltip="Undo last point (Backspace)")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo_clicked.emit)
        frame_layout.addWidget(self.undo_btn)

        # "trash", not "refresh". With the labels gone the glyph is the only
        # thing distinguishing these, and rendered at 14px `undo` and
        # `refresh` are the same arc at two rotations — side by side they
        # read as one control repeated. A bin is a different silhouette, and
        # clearing every point IS a delete, which is what this button was
        # before the emoji were removed ("🗑️ Clear All").
        self.clear_btn = IconButton(
            "trash", size=28, icon_size=14, tone="muted",
            hover_tone="danger",
            tooltip="Clear every point and start again")
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self.clear_clicked.emit)
        frame_layout.addWidget(self.clear_btn)

        # Cancel is the only destructive one here: finishing keeps the area,
        # clearing keeps the tool, cancelling throws the work away. Red on
        # HOVER rather than at rest — a permanently red control on a one-row
        # strip beside three neutral ones reads as an error state.
        self.cancel_btn = IconButton(
            "close", size=28, icon_size=14, tone="muted",
            hover_tone="danger", tooltip="Discard this area (Esc)")
        self.cancel_btn.clicked.connect(self.cancel_clicked.emit)
        frame_layout.addWidget(self.cancel_btn)

        layout.addWidget(self.frame)

        # The strip is its own drag handle. Attached to the FRAME and to the
        # two passive widgets on it, not to `self`: the outer widget is
        # translucent and is not what the pointer lands on. The buttons are
        # deliberately left out, since a button consumes its own press and
        # must keep working as a button.
        self._drag = WindowDragFilter()
        for handle in (self.frame, self.grip, self.info_label):
            self._drag.attach(handle)

    # ── position ───────────────────────────────────────────
    def remembered_position(self):
        """The saved screen position, if it is still on a connected screen.

        Returns None when nothing is saved or the saved point is no longer
        reachable. A position from a monitor that has since been unplugged
        would otherwise park the bar off-screen with no way to get it back.
        """
        raw = prefs.get_str(prefs.DRAW_BAR_POS)
        if not raw:
            return None
        try:
            x_text, y_text = raw.split(",", 1)
            point = QPoint(int(x_text), int(y_text))
        except (ValueError, TypeError):
            return None
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(point):
                return point
        return None

    def remember_position(self):
        """Store where the user parked it, as the bar goes away."""
        try:
            position = self.pos()
        except RuntimeError:
            return
        prefs.set_value(prefs.DRAW_BAR_POS,
                        f"{position.x()},{position.y()}")

    def hideEvent(self, event):
        # Hiding is the one moment common to every way this bar goes away:
        # finish, cancel, and the tool being deactivated underneath it.
        self.remember_position()
        super().hideEvent(event)

    def cleanup(self):
        """Uninstall the drag filter. Must run before teardown.

        A Python event filter left installed on a widget that is being
        destroyed is still called during its destruction, and if the
        interpreter has begun finalising there is nothing left to call — the
        process dies with no traceback. See `WindowDragFilter.detach_all`.
        """
        self._drag.detach_all()

    def update_info(self, point_count, area_text="", perimeter_text=""):
        """Update the inline measurements, and what is clickable.

        One line, and short. `area_text` and `perimeter_text` arrive as bare
        values ("12.4 km\u00b2"); they used to carry "Area: " / "Perimeter: "
        prefixes and a newline each, which is three of the rows this strip no
        longer has.
        """
        if point_count == 0:
            text = "Click the map to add points"
        elif point_count == 1:
            text = "1 point \u00b7 two more needed"
        elif point_count == 2:
            text = "2 points \u00b7 one more needed"
            if perimeter_text:
                text += f" \u00b7 {perimeter_text}"
        else:
            parts = [f"{point_count} points"]
            parts.extend(p for p in (area_text, perimeter_text) if p)
            text = " \u00b7 ".join(parts)

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
        """Put the bar back where the user left it, else top-centre.

        Top CENTRE rather than the old top-right corner: the strip is wide
        and short now, and the right corner of a QGIS canvas is where the
        plugin panel is usually docked, so the bar and the panel were
        stacking up in the same place.
        """
        remembered = self._toolbar.remembered_position()
        if remembered is not None:
            self._toolbar.move(remembered)
            return

        canvas_rect = self.canvas.rect()
        width = self._toolbar.sizeHint().width()
        margin = self._toolbar.EDGE_MARGIN
        x = max(margin, (canvas_rect.width() - width) // 2)
        global_pos = self.canvas.mapToGlobal(canvas_rect.topLeft())
        self._toolbar.move(global_pos.x() + x, global_pos.y() + margin)

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

        perimeter_text = self._format_distance(perimeter)

        # Calculate area if we have at least 3 points
        area_text = ""
        if len(self._points) >= 3:
            # Create a closed polygon
            closed_points = self._points + [self._points[0]]
            geom = QgsGeometry.fromPolygonXY([closed_points])
            area = self._distance_area.measureArea(geom)
            area_text = self._format_area(area)

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
                # cleanup() BEFORE close(): it saves the parked position and
                # takes the drag filter off its handles. A Python event
                # filter still installed on a widget being destroyed is
                # called during that destruction, which kills the process
                # outright if the interpreter is already finalising.
                self._toolbar.cleanup()
                self._toolbar.close()
                self._toolbar.deleteLater()
            except RuntimeError:
                pass
            self._toolbar = None

        try:
            super().deactivate()
        except RuntimeError:
            pass
