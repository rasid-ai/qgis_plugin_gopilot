"""Dynamic process creation wizard — embedded as a QWidget page.

ONE FORM, BUILT FROM THE SOLUTION'S CONFIG
==========================================
Every section below is created up front and then shown or hidden by
:meth:`_update_sections` according to the dataset type the user picks, because
the API describes what a solution supports rather than handing over a form.

STYLING IS SET ONCE, ON THE FORM
================================
The controls are plain Qt widgets — combo boxes, date edits, a spin box, radio
buttons — and ``design.form_qss()`` on the form container styles all of them
together. Done per widget, a dozen inputs means a dozen chances to miss one,
and the ones that were missed showed QGIS's native styling in the middle of
the panel. The selectors are type-scoped, so they reach the form's inputs
without leaking onto anything else.

NO TITLE BAR OF ITS OWN
=======================
The processes page's header already names the project and carries Back while
this page is up, so a second title bar here put the project name on screen
twice and cost 37px of a ~420px-wide panel. The actions sit in a pinned bar at
the foot of the form instead, where a long form needs them reachable without
scrolling back.
"""
import json

from qgis.PyQt.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QDateEdit, QFileDialog, QWidget, QMessageBox, QRadioButton,
    QButtonGroup, QTextEdit, QListWidget, QListWidgetItem,
    QFrame, QSpinBox,
)
from qgis.PyQt.QtCore import QThread, pyqtSignal, QDate
from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsMapLayer,
)
from .geometry import GeometryInputManager
from .compat import (
    Qt_AlignCenter, Qt_UserRole,
    QMessageBox_Yes, QMessageBox_No,
    QSizePolicy_Expanding, QSizePolicy_Fixed,
)
from . import design
from .widgets import (
    SectionCard, paint_background, primary_button, secondary_button,
    styled_scroll_area,
)


# ---------------------------------------------------------------------------
#  Background threads
# ---------------------------------------------------------------------------

class FetchConfigThread(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, client, project_slug):
        super().__init__()
        self.client = client
        self.project_slug = project_slug

    def run(self):
        try:
            cfg = self.client.get_process_config(self.project_slug)
            self.finished.emit(cfg)
        except Exception as e:
            self.error.emit(str(e))


class SearchCatalogueThread(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, client, payload):
        super().__init__()
        self.client = client
        self.payload = payload

    def run(self):
        try:
            res = self.client.search_catalogue(self.payload)
            self.finished.emit(res)
        except Exception as e:
            self.error.emit(str(e))


class CreateProcessThread(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, client, project_slug, payload, files=None):
        super().__init__()
        self.client = client
        self.project_slug = project_slug
        self.payload = payload
        self.files = files

    def run(self):
        try:
            res = self.client.create_process(
                self.project_slug, self.payload, self.files
            )
            self.finished.emit(res)
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
#  Wizard widget (embedded, not a popup)
# ---------------------------------------------------------------------------

class CreateProcessWizard(QWidget):
    """Dynamic form that adapts to the solution's process-config."""

    process_created = pyqtSignal(dict)
    cancelled = pyqtSignal()

    def __init__(self, client, iface, project_slug, project_title, parent=None):
        super().__init__(parent)
        self.client = client
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.project_slug = project_slug
        self._threads = []
        self._aoi_coords = None
        self._catalogue_results_raw = None
        self._config = {}
        self._upload_path = None

        # Initialize geometry input manager
        self.geo_manager = GeometryInputManager(iface, parent_widget=self)
        self.geo_manager.geometry_ready.connect(self._on_geometry_ready)

        t = design.tokens()
        paint_background(self, t.canvas, "createProcessWizard")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # No title bar here — see the module docstring. The page header above
        # already reads "<project>" with Back beside it.
        scroll = styled_scroll_area()
        outer.addWidget(scroll, stretch=1)

        self._form = QWidget()
        # Every input in the form, styled in one place.
        self._form.setStyleSheet(design.form_qss())
        self._form_layout = QVBoxLayout(self._form)
        self._form_layout.setSpacing(design.SPACE_3)
        self._form_layout.setContentsMargins(
            design.SPACE_3, design.SPACE_3, design.SPACE_3, design.SPACE_3)
        scroll.setWidget(self._form)

        self._loading_label = QLabel("Loading process configuration…")
        self._loading_label.setAlignment(Qt_AlignCenter)
        self._loading_label.setStyleSheet(
            design.body_qss(design.TEXT_BASE, secondary=True))
        self._form_layout.addWidget(self._loading_label)

        # ── Action bar, pinned below the scroll area ──
        # Surface-alt with a top rule, the same treatment the composer's band
        # gets, so the form reads as scrolling content under a fixed footer.
        btn_bar = QFrame()
        btn_bar.setObjectName("wizardActions")
        btn_bar.setStyleSheet(
            f"QFrame#wizardActions {{ background:{t.surface_alt}; "
            f"border:none; border-top:{design.BORDER}px solid {t.border}; }}"
        )
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(design.SPACE_3, design.SPACE_2,
                                      design.SPACE_3, design.SPACE_2)
        btn_layout.setSpacing(design.SPACE_2)

        # No icon. A tick means "done" or "confirmed", and this button is
        # neither — pressing it STARTS the work. It also made this the only
        # create action in the panel carrying a glyph: Create project in GoBox
        # and New process in the header are both label-only.
        self._submit_btn = primary_button("Create process")
        self._submit_btn.setEnabled(False)
        self._submit_btn.clicked.connect(self._on_submit)
        btn_layout.addWidget(self._submit_btn)

        # Secondary, not danger. Cancelling a form the user has not submitted
        # destroys nothing, and a red button beside the primary action reads as
        # the dangerous half of a destructive choice.
        self._cancel_btn = secondary_button("Cancel")
        self._cancel_btn.clicked.connect(self.cancelled.emit)
        btn_layout.addWidget(self._cancel_btn)

        btn_layout.addStretch()
        outer.addWidget(btn_bar)

        self._fetch_config()

    # ------------------------------------------------------------------
    #  Config fetch
    # ------------------------------------------------------------------

    def _fetch_config(self):
        t = FetchConfigThread(self.client, self.project_slug)
        t.finished.connect(self._on_config_loaded)
        t.error.connect(self._on_config_error)
        self._threads.append(t)
        t.start()

    def _on_config_error(self, msg):
        self._loading_label.setText(f"Failed to load config: {msg}")

    def _on_config_loaded(self, config):
        self._config = config
        self._loading_label.hide()
        self._build_form(config)
        self._submit_btn.setEnabled(True)

    # ------------------------------------------------------------------
    #  Build dynamic form
    # ------------------------------------------------------------------

    def _build_form(self, config):
        t = design.tokens()
        supports = config.get("supports", {})
        sentinel_cfg = config.get("sentinel", {})

        # Process name
        grp = self._make_group("Process name", icon_name="message")
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Enter a name for this process")
        grp.layout().addWidget(self._name_input)

        # Dataset type selection
        supported_types = []
        # Sentence case, like every other label in the panel. Proper nouns
        # keep their capitals.
        type_labels = {
            "mapbox": "Mapbox tiles",
            "sentinel_catalogue": "Sentinel catalogue",
            "sentinel_series": "Sentinel time series",
            "planet": "Planet imagery",
            "raster_upload": "Upload raster",
            "raster_link": "Raster link",
        }
        for key, label in type_labels.items():
            if supports.get(key):
                supported_types.append((key, label))

        self._dataset_type = None
        self._type_radios = {}

        if len(supported_types) == 1:
            self._dataset_type = supported_types[0][0]
        elif len(supported_types) > 1:
            grp = self._make_group("Dataset type", icon_name="layers")
            self._type_btn_group = QButtonGroup(self)
            for i, (key, label) in enumerate(supported_types):
                rb = QRadioButton(label)
                self._type_radios[key] = rb
                self._type_btn_group.addButton(rb, i)
                grp.layout().addWidget(rb)
            # NOTE: connect signals and set default AFTER all sections are built (see below)

        # --- AOI section ---
        self._aoi_group = self._make_group(
            "Area of interest", icon_name="map-pin")
        aoi_layout = self._aoi_group.layout()

        aoi_btn_row = QHBoxLayout()
        self._draw_btn = secondary_button("Draw on map", icon_name="draw")
        self._draw_btn.clicked.connect(self._start_draw_aoi)
        aoi_btn_row.addWidget(self._draw_btn)

        self._layer_combo = QComboBox()
        self._layer_combo.addItem("-- From Layer --")
        # Only show vector layers (shapefiles, GeoJSON, etc.) - exclude rasters (TIFFs)
        vector_layer_count = 0
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == QgsMapLayer.LayerType.VectorLayer:
                self._layer_combo.addItem(layer.name(), layer.id())
                vector_layer_count += 1

        # Add helpful message if no vector layers available
        if vector_layer_count == 0:
            self._layer_combo.addItem("(No vector layers in project)", None)
            self._layer_combo.setEnabled(False)

        self._layer_combo.currentIndexChanged.connect(self._on_layer_selected)
        aoi_btn_row.addWidget(self._layer_combo)
        aoi_layout.addLayout(aoi_btn_row)

        # Read-only, so it is styled as a sunken well rather than as a field
        # someone could type into: same radius and border as an input, but the
        # canvas fill and the muted monospace-ish body size of a readout.
        self._aoi_display = QTextEdit()
        self._aoi_display.setReadOnly(True)
        # Fixed height AND a Fixed vertical POLICY. The height alone is not
        # enough: setFixedHeight sets the min and max but leaves the policy
        # alone, and a QTextEdit's is Expanding. A layout item reports its
        # widget's policy, not its limits, so the card went on advertising
        # itself as vertically expanding (expandingDirections 3 against 1 for
        # its siblings) and the form handed it the spare height instead of the
        # trailing stretch — 342px against a 160px hint, which is where the
        # bands of empty space above and below its title came from.
        self._aoi_display.setFixedHeight(62)
        self._aoi_display.setSizePolicy(QSizePolicy_Expanding,
                                        QSizePolicy_Fixed)
        self._aoi_display.setPlaceholderText(
            "No area set. Draw on the map or pick a layer.")
        self._aoi_display.setStyleSheet(
            f"QTextEdit {{ background:{t.surface_sunken}; color:{t.text}; "
            f"border:{design.BORDER}px solid {t.border}; "
            f"border-radius:{design.RADIUS}px; padding:6px 8px; "
            f"font-family:{design.FONT_STACK}; "
            f"font-size:{design.TEXT_SM}px; }}"
        )
        aoi_layout.addWidget(self._aoi_display)

        # --- Date range ---
        sentinel_fields = sentinel_cfg.get("fields", {})
        start_date_cfg = sentinel_fields.get("start_date", {})
        end_date_cfg = sentinel_fields.get("end_date", {})

        start_label = start_date_cfg.get("label", "Start Date")
        self._end_date_hidden = end_date_cfg.get("hidden", False)

        self._date_group = self._make_group("Date range", icon_name="history")
        date_layout = self._date_group.layout()
        date_row = QHBoxLayout()
        date_row.addWidget(self._field_label(start_label))
        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDate(QDate.currentDate().addMonths(-1))
        date_row.addWidget(self._start_date)

        end_label = end_date_cfg.get("label", "End Date")
        self._end_date_label = self._field_label(end_label)
        date_row.addWidget(self._end_date_label)
        self._end_date = QDateEdit()
        self._end_date.setCalendarPopup(True)
        self._end_date.setDate(QDate.currentDate())
        date_row.addWidget(self._end_date)
        date_layout.addLayout(date_row)

        # --- Sentinel settings (populated from config) ---
        self._sentinel_group = self._make_group(
            "Sentinel settings", icon_name="settings")
        s_layout = self._sentinel_group.layout()
        sentinel_choices = sentinel_cfg.get("choices", {})
        sentinel_defaults = sentinel_cfg.get("defaults", {})

        # Data Collection
        s_layout.addWidget(self._field_label("Data collection"))
        self._sentinel_collection = QComboBox()
        for ch in sentinel_choices.get("data_collection", []):
            self._sentinel_collection.addItem(ch.get("label", ch.get("value")), ch.get("value"))
        s_layout.addWidget(self._sentinel_collection)

        # Eval Script
        s_layout.addWidget(self._field_label("Eval script"))
        self._sentinel_eval_script = QComboBox()
        for ch in sentinel_choices.get("eval_script", []):
            self._sentinel_eval_script.addItem(ch.get("label", "").strip(), ch.get("value"))
        s_layout.addWidget(self._sentinel_eval_script)

        # Sort By
        s_layout.addWidget(self._field_label("Sort by"))
        self._sentinel_sort = QComboBox()
        for ch in sentinel_choices.get("sort_by", []):
            self._sentinel_sort.addItem(ch.get("label", ch.get("value")), ch.get("value"))
        s_layout.addWidget(self._sentinel_sort)

        # Apply sentinel date defaults
        if sentinel_defaults.get("start_date"):
            self._start_date.setDate(QDate.fromString(sentinel_defaults["start_date"], "yyyy-MM-dd"))
        if sentinel_defaults.get("end_date"):
            self._end_date.setDate(QDate.fromString(sentinel_defaults["end_date"], "yyyy-MM-dd"))

        # --- Catalogue search ---
        self._catalogue_group = self._make_group(
            "Catalogue search", icon_name="search")
        cat_layout = self._catalogue_group.layout()

        self._search_btn = secondary_button(
            "Search available imagery", icon_name="search")
        self._search_btn.clicked.connect(self._on_search_catalogue)
        cat_layout.addWidget(self._search_btn)

        self._catalogue_list = QListWidget()
        # Fixed height and policy, for the same reason as the AOI well.
        self._catalogue_list.setFixedHeight(150)
        self._catalogue_list.setSizePolicy(QSizePolicy_Expanding,
                                           QSizePolicy_Fixed)
        self._catalogue_list.setStyleSheet(
            design.list_qss()
            + f"QListWidget {{ background:{t.surface_sunken}; "
              f"border:{design.BORDER}px solid {t.border}; }}"
        )
        cat_layout.addWidget(self._catalogue_list)

        # --- Zoom level (for mapbox) ---
        defaults = config.get("defaults", {})
        self._zoom_group = self._make_group("Zoom level", icon_name="sliders")
        z_layout = self._zoom_group.layout()
        self._zoom_spin = QSpinBox()
        self._zoom_spin.setRange(0, 20)
        self._zoom_spin.setValue(defaults.get("zoom_level", 18))
        z_layout.addWidget(self._zoom_spin)

        # --- Raster upload ---
        self._upload_group = self._make_group(
            "Upload raster (TIFF)", icon_name="upload")
        u_layout = self._upload_group.layout()
        upload_row = QHBoxLayout()
        self._upload_label = QLabel("No file selected")
        self._upload_label.setStyleSheet(
            design.body_qss(design.TEXT_BASE, secondary=True))
        upload_row.addWidget(self._upload_label, stretch=1)
        upload_btn = secondary_button("Choose file", icon_name="upload")
        upload_btn.clicked.connect(self._on_choose_file)
        upload_row.addWidget(upload_btn)
        u_layout.addLayout(upload_row)

        # --- Raster link ---
        self._link_group = self._make_group("Raster link", icon_name="attach")
        l_layout = self._link_group.layout()
        self._link_input = QLineEdit()
        self._link_input.setPlaceholderText("https://example.com/raster.tif")
        l_layout.addWidget(self._link_input)

        self._form_layout.addStretch()

        # Now that all sections exist, connect radio signals and set default
        if self._type_radios:
            for key, rb in self._type_radios.items():
                rb.toggled.connect(lambda checked, k=key: self._on_type_changed(k, checked))
            first_key = supported_types[0][0]
            self._type_radios[first_key].setChecked(True)
        self._update_sections()

    def _field_label(self, text):
        """A label above or beside an input.

        Secondary colour and a trailing colon, applied in one place so the
        sentinel settings, the date range and anything added later agree. The
        form's own QLabel rule covers labels that do not go through here; this
        is for the ones that should read as quieter than body copy.
        """
        label = QLabel(f"{text}:")
        label.setStyleSheet(design.body_qss(design.TEXT_BASE, secondary=True))
        return label

    def _make_group(self, title, icon_name=None):
        """One titled card per form section.

        A ``SectionCard``, the same component Settings, GoBox and Projects use,
        rather than the ``QGroupBox`` this used to be. The group box drew a
        hairline frame with the title notched into it and sat on the page
        background — a different idiom from every other surface in the panel,
        and its stylesheet set a bare ``font-weight`` and ``color`` that every
        label and input inside it then inherited.

        Returns the card. Callers reach its layout through ``layout()``, which
        ``SectionCard`` inherits from ``QFrame``, so the existing
        ``grp.layout().addWidget(...)`` calls keep working.
        """
        card = SectionCard(title, icon_name=icon_name)
        self._form_layout.addWidget(card)
        return card

    # ------------------------------------------------------------------
    #  Section visibility
    # ------------------------------------------------------------------

    def _on_type_changed(self, key, checked):
        if checked:
            self._dataset_type = key
            self._update_sections()

    def _update_sections(self):
        dt = self._dataset_type
        self._aoi_group.setVisible(dt in ("mapbox", "sentinel_catalogue", "sentinel_series", "planet"))
        self._date_group.setVisible(dt in ("sentinel_catalogue", "sentinel_series", "planet"))
        self._sentinel_group.setVisible(dt == "sentinel_catalogue")
        self._catalogue_group.setVisible(dt in ("sentinel_catalogue", "planet"))
        # Only show zoom level if this is the pure mapbox solution (only supports mapbox, nothing else)
        supports = self._config.get("supports", {})
        is_pure_mapbox = (dt == "mapbox" and
                          supports.get("mapbox") and
                          not any(supports.get(k) for k in ["sentinel_catalogue", "sentinel_series", "planet", "raster_upload", "raster_link"]))
        self._zoom_group.setVisible(is_pure_mapbox)
        self._upload_group.setVisible(dt == "raster_upload")
        self._link_group.setVisible(dt == "raster_link")

        # Show/hide end date based on config's hidden flag
        if self._end_date_hidden:
            self._end_date.hide()
            self._end_date_label.hide()
        else:
            self._end_date.show()
            self._end_date_label.show()

    # ------------------------------------------------------------------
    #  AOI drawing
    # ------------------------------------------------------------------

    def _start_draw_aoi(self):
        """Start drawing AOI using the geometry manager"""
        # minimize_parent=False, for the same reason the chat page passes it:
        # this wizard is a page inside the docked panel, not the standalone
        # dialog it used to be, so its top-level window is QGIS's.
        self.geo_manager.start_draw_mode(
            minimize_parent=False,
            show_help=True,
            help_context="wizard"
        )

    def _on_geometry_ready(self, geometry_data):
        """Handle geometry input completion from drawing or layer selection.

        Args:
            geometry_data: Dict with 'geojson', 'source', and 'name' keys
        """
        geojson = geometry_data['geojson']

        # Extract coordinates from GeoJSON
        # Handle both Polygon and FeatureCollection types
        if geojson.get('type') == 'Polygon':
            # Single polygon from drawing
            coords = geojson['coordinates'][0]
        elif geojson.get('type') == 'FeatureCollection':
            # From layer - get first feature's coordinates
            features = geojson.get('features', [])
            if features:
                first_geom = features[0].get('geometry', {})
                if first_geom.get('type') == 'Polygon':
                    coords = first_geom['coordinates'][0]
                elif first_geom.get('type') == 'MultiPolygon':
                    coords = first_geom['coordinates'][0][0]
                else:
                    coords = []
            else:
                coords = []
        else:
            coords = []

        # Store and display
        self._aoi_coords = coords
        self._aoi_display.setPlainText(json.dumps(coords))

    def _on_layer_selected(self, index):
        if index <= 0:
            return
        layer_id = self._layer_combo.itemData(index)
        layer = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            return

        # Validate layer type
        from qgis.core import QgsVectorLayer, QgsWkbTypes
        if not isinstance(layer, QgsVectorLayer):
            QMessageBox.warning(
                self, "Invalid Layer Type",
                "Please select a vector layer (shapefile, GeoJSON, etc.)"
            )
            self._layer_combo.setCurrentIndex(0)
            return

        # Check if layer has features
        if layer.featureCount() == 0:
            QMessageBox.warning(
                self, "Empty Layer",
                "The selected layer has no features. Please select a layer with at least one polygon."
            )
            self._layer_combo.setCurrentIndex(0)
            return

        # Get features (selected or all)
        feats = layer.selectedFeatures() if layer.selectedFeatureCount() > 0 else list(layer.getFeatures())
        if not feats:
            QMessageBox.warning(
                self, "No Features",
                "No features found in the selected layer."
            )
            self._layer_combo.setCurrentIndex(0)
            return

        # Check geometry type
        geom = feats[0].geometry()
        if geom.isEmpty():
            QMessageBox.warning(
                self, "Invalid Geometry",
                "The selected feature has no geometry."
            )
            self._layer_combo.setCurrentIndex(0)
            return

        geom_type = geom.wkbType()
        is_polygon = QgsWkbTypes.flatType(geom_type) == QgsWkbTypes.Type.Polygon
        is_multipolygon = QgsWkbTypes.flatType(geom_type) == QgsWkbTypes.Type.MultiPolygon

        if not (is_polygon or is_multipolygon):
            QMessageBox.warning(
                self, "Invalid Geometry Type",
                "Please select a layer with Polygon or MultiPolygon geometry.\n\n"
                f"Current geometry type: {QgsWkbTypes.displayString(geom_type)}"
            )
            self._layer_combo.setCurrentIndex(0)
            return

        # Warn if multiple features
        if len(feats) > 1:
            reply = QMessageBox.question(
                self, "Multiple Features",
                f"The layer has {len(feats)} features. Only the first feature will be used.\n\n"
                "Do you want to continue?",
                QMessageBox_Yes | QMessageBox_No, QMessageBox_No
            )
            if reply != QMessageBox_Yes:
                self._layer_combo.setCurrentIndex(0)
                return

        # Transform to WGS84
        src_crs = layer.crs()
        dst_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())

        try:
            if src_crs != dst_crs:
                geom.transform(xform)

            # Extract polygon coordinates
            poly = None
            if is_polygon:
                poly = geom.asPolygon()
            elif is_multipolygon:
                mpoly = geom.asMultiPolygon()
                if mpoly and len(mpoly) > 0:
                    poly = mpoly[0]
                    if len(mpoly) > 1:
                        QMessageBox.information(
                            self, "MultiPolygon Detected",
                            f"The feature is a MultiPolygon with {len(mpoly)} parts.\n"
                            "Only the first polygon will be used as AOI."
                        )

            if poly and len(poly) > 0:
                ring = poly[0]
                self._aoi_coords = [[p.x(), p.y()] for p in ring]
                self._aoi_display.setPlainText(json.dumps(self._aoi_coords))
                self._layer_combo.setCurrentIndex(0)
                return
            else:
                QMessageBox.warning(
                    self, "Invalid Polygon",
                    "Could not extract polygon coordinates from the selected feature."
                )
                self._layer_combo.setCurrentIndex(0)
                return

        except Exception as e:
            QMessageBox.critical(
                self, "Error Processing Layer",
                f"An error occurred while processing the layer:\n\n{str(e)}"
            )
            self._layer_combo.setCurrentIndex(0)
            return

    # ------------------------------------------------------------------
    #  Catalogue search
    # ------------------------------------------------------------------

    def _on_search_catalogue(self):
        if not self._aoi_coords or len(self._aoi_coords) < 3:
            QMessageBox.warning(self, "AOI Required", "Draw or select an AOI first.")
            return

        lons = [c[0] for c in self._aoi_coords]
        lats = [c[1] for c in self._aoi_coords]
        # Send bbox as list - the client will convert to string format if needed
        bbox = [min(lons), min(lats), max(lons), max(lats)]

        start = self._start_date.date().toString("yyyy-MM-dd")
        end = self._end_date.date().toString("yyyy-MM-dd")

        data_collection = self._sentinel_collection.currentData() or self._sentinel_collection.currentText()
        sort_by = self._sentinel_sort.currentData() or self._sentinel_sort.currentText()

        payload = {
            "bbox": bbox,  # Pass as list, not JSON string
            "sentinel_start_date": start,
            "sentinel_end_date": end,
            "sentinel_data_collection": data_collection,
            "sentinel_sort_by": sort_by,
        }

        if self._dataset_type == "planet":
            payload["planet_start_date"] = start
            payload["planet_end_date"] = end

        self._search_btn.setEnabled(False)
        self._search_btn.setText("Searching...")

        t = SearchCatalogueThread(self.client, payload)
        t.finished.connect(self._on_search_results)
        t.error.connect(self._on_search_error)
        self._threads.append(t)
        t.start()

    def _on_search_results(self, results):
        self._search_btn.setEnabled(True)
        self._search_btn.setText("Search Available Imagery")
        self._catalogue_list.clear()

        items = results if isinstance(results, list) else results.get("features", results.get("results", []))
        self._catalogue_results_raw = items

        if not items:
            self._catalogue_list.addItem("No imagery found for this area and date range.")
            return

        for i, item in enumerate(items):
            props = item.get("properties", item) if isinstance(item, dict) else {}
            item_id = props.get("id", item.get("id", f"Image {i + 1}"))
            date = props.get("datetime", props.get("acquired", ""))
            cloud = props.get("eo:cloud_cover", props.get("cloud_cover", ""))
            label = f"{item_id}"
            if date:
                label += f"  |  {date[:10]}"
            if cloud != "":
                label += f"  |  Cloud: {cloud}%"
            li = QListWidgetItem(label)
            li.setData(Qt_UserRole, i)
            self._catalogue_list.addItem(li)

        self._catalogue_list.setCurrentRow(0)

    def _on_search_error(self, msg):
        self._search_btn.setEnabled(True)
        self._search_btn.setText("Search Available Imagery")
        QMessageBox.warning(self, "Search Failed", msg)

    # ------------------------------------------------------------------
    #  File upload
    # ------------------------------------------------------------------

    def _on_choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Raster File", "", "TIFF Files (*.tif *.tiff)"
        )
        if path:
            self._upload_path = path
            self._upload_label.setText(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])

    # ------------------------------------------------------------------
    #  Submit
    # ------------------------------------------------------------------

    def _on_submit(self):
        name = self._name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Enter a process name.")
            return

        dt = self._dataset_type
        if not dt:
            QMessageBox.warning(self, "Missing Type", "Select a dataset type.")
            return

        # Get default zoom level from config
        defaults = self._config.get("defaults", {})
        default_zoom = defaults.get("zoom_level", 18)

        payload = {
            "name": name,
            "zoom_level": default_zoom,  # Always include default, will override for mapbox if needed
        }
        files = None

        # Resolve dataset_choice_id from config's dataset_type_to_id mapping
        # API uses short keys (mapb, lras, uras, sent, plan) — map from our full names
        _type_abbrev = {
            "mapbox": "mapb",
            "raster_link": "lras",
            "raster_upload": "uras",
            "sentinel_catalogue": "sent",
            "sentinel_series": "sts",
            "planet": "plan",
        }
        type_to_id = self._config.get("dataset_type_to_id", {})
        abbrev = _type_abbrev.get(dt, dt)
        choice_id = type_to_id.get(abbrev) or type_to_id.get(dt)

        if choice_id is not None:
            payload["dataset_choice_id"] = choice_id

        aoi_json = json.dumps(self._aoi_coords) if self._aoi_coords else None

        if dt == "mapbox":
            if not aoi_json:
                QMessageBox.warning(self, "Missing AOI", "Draw or select an AOI.")
                return
            payload["mapbox_aoi"] = aoi_json
            # Override zoom level if user can see the spinner (pure mapbox solution)
            if self._zoom_group.isVisible():
                payload["zoom_level"] = self._zoom_spin.value()

        elif dt == "sentinel_catalogue":
            if not aoi_json:
                QMessageBox.warning(self, "Missing AOI", "Draw or select an AOI.")
                return
            payload["sentinel_aoi"] = aoi_json
            payload["sentinel_data_collection"] = self._sentinel_collection.currentData() or self._sentinel_collection.currentText()
            payload["sentinel_start_date"] = self._start_date.date().toString("yyyy-MM-dd")
            payload["sentinel_end_date"] = self._end_date.date().toString("yyyy-MM-dd")
            payload["sentinel_sort_by"] = self._sentinel_sort.currentData() or self._sentinel_sort.currentText()
            # Use fixed_value from config if set, otherwise use dropdown selection
            sentinel_fields = self._config.get("sentinel", {}).get("fields", {})
            eval_fixed = sentinel_fields.get("eval_script", {}).get("fixed_value")
            if eval_fixed:
                payload["sentinel_eval_script"] = eval_fixed
            else:
                eval_script = self._sentinel_eval_script.currentData() or self._sentinel_eval_script.currentText()
                if eval_script:
                    payload["sentinel_eval_script"] = eval_script
            selected = self._catalogue_list.currentItem()
            if selected and self._catalogue_results_raw:
                idx = selected.data(Qt_UserRole)
                if idx is not None and idx < len(self._catalogue_results_raw):
                    payload["sentinel_search_results"] = json.dumps(
                        self._catalogue_results_raw[idx]
                    )

        elif dt == "sentinel_series":
            if not aoi_json:
                QMessageBox.warning(self, "Missing AOI", "Draw or select an AOI.")
                return
            sentinel_fields = self._config.get("sentinel", {}).get("fields", {})
            start_date = self._start_date.date().toString("yyyy-MM-dd")
            end_date_cfg = sentinel_fields.get("end_date", {})
            if end_date_cfg.get("hidden", False):
                end_date = start_date
            else:
                end_date = self._end_date.date().toString("yyyy-MM-dd")
            payload["sentinel_aoi"] = aoi_json
            payload["sentinel_start_date"] = start_date
            payload["sentinel_end_date"] = end_date
            # Use fixed values from config for all hidden fields
            payload["sentinel_data_collection"] = sentinel_fields.get(
                "data_collection", {}).get("fixed_value", "sentinel-2-l1c")
            payload["sentinel_eval_script"] = sentinel_fields.get(
                "eval_script", {}).get("fixed_value", "s2-l1c-all-bands")
            payload["sentinel_series_interval_range"] = sentinel_fields.get(
                "series_interval_range", {}).get("fixed_value", 7)
            payload["sentinel_series_gap_between_intervals"] = sentinel_fields.get(
                "series_gap_between_intervals", {}).get("fixed_value", 0)

        elif dt == "planet":
            if not aoi_json:
                QMessageBox.warning(self, "Missing AOI", "Draw or select an AOI.")
                return
            payload["planet_aoi"] = aoi_json
            payload["planet_start_date"] = self._start_date.date().toString("yyyy-MM-dd")
            payload["planet_end_date"] = self._end_date.date().toString("yyyy-MM-dd")
            selected = self._catalogue_list.currentItem()
            if selected and self._catalogue_results_raw:
                idx = selected.data(Qt_UserRole)
                if idx is not None and idx < len(self._catalogue_results_raw):
                    payload["planet_search_results"] = json.dumps(
                        self._catalogue_results_raw[idx]
                    )

        elif dt == "raster_upload":
            if not self._upload_path:
                QMessageBox.warning(self, "Missing File", "Choose a TIFF file to upload.")
                return
            files = {"upload_raster": open(self._upload_path, "rb")}

        elif dt == "raster_link":
            link = self._link_input.text().strip()
            if not link:
                QMessageBox.warning(self, "Missing Link", "Enter a raster URL.")
                return
            payload["link_to_raster"] = link

        self._submit_btn.setEnabled(False)
        self._submit_btn.setText("Creating...")

        t = CreateProcessThread(self.client, self.project_slug, payload, files)
        t.finished.connect(self._on_created)
        t.error.connect(self._on_create_error)
        self._threads.append(t)
        t.start()

    def _on_created(self, result):
        self._submit_btn.setEnabled(True)
        self._submit_btn.setText("Create Process")
        QMessageBox.information(
            self, "Process Created",
            "Process created successfully!"
        )
        self.process_created.emit(result)

    def _on_create_error(self, msg):
        self._submit_btn.setEnabled(True)
        self._submit_btn.setText("Create Process")
        QMessageBox.critical(self, "Error", f"Failed to create process:\n{msg}")
