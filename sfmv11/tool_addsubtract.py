"""
SFM TOOL - ADD SUBTRACT

allows selected features from an original 2pt corridor layer to be added to or removed from a new extent layer

workflow:
1. identify available vector layers in the current qgis project
2. separate likely source and target extent layers
3. allow the user to select the original 2pt corridor layer
4. allow the user to select the new extent layer
5. select the add or remove operation
6. check that valid source and target layers are available
7. check that features have been selected in the source layer
8. compare selected features against the target layer using a spatial index
9. add matching source features to the target layer when in add mode
10. remove matching features from the target layer when in remove mode
11. prevent duplicate geometries from being added
12. commit the changes to the target layer
13. refresh the qgis map and report the number of features changed

"""

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.core import ( QgsProject, QgsVectorLayer, QgsSpatialIndex, QgsFeature)
from qgis.PyQt.QtWidgets import ( QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QMessageBox)
from qgis.PyQt.QtGui import QColor, QIcon, QAction
import os

class AddSubtract(QDialog):
    def __init__(self, iface, is_add_mode=True, parent=None):
        parent_window = parent if parent else iface.mainWindow()
        super().__init__(parent_window)
        self.iface = iface
        self.is_add_mode = is_add_mode
        
        action_title = "Add to Extent" if self.is_add_mode else "Remove from Extent"
        self.setWindowTitle(action_title)
        self.resize(430, 160)
        
        self.setStyleSheet("""
            QDialog {
                background-color: #BEE3BA;
            }
            QLabel {
                color: #1f3a11;
            }
            QComboBox {
                background-color: #ffffff;
                color: #2b2b2b;
                border: 1px solid #BEE3BA;
                border-radius: 3px;
                padding: 4px;
            }
            QPushButton#executeBtn {
                background-color: #ffffff;
                color: #1f3a11;
                border: 1px solid #BEE3BA;
                border-radius: 3px;
                padding: 6px 16px;
            }
            QPushButton#executeBtn:hover {
                background-color: #f0f7ec;
            }
        """)
        
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.source_layers = []
        self.target_layers = []
        
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == 1:
                lname = layer.name().lower()
                if "extent" in lname or "final" in lname:
                    self.target_layers.append(layer)
                else:
                    self.source_layers.append(layer)

        if not self.source_layers:
            self.source_layers = list(QgsProject.instance().mapLayers().values())

        layout.addWidget(QLabel("Original 2pt Corridor Layer:"))
        self.source_combo = QComboBox()
        for lyr in self.source_layers:
            if isinstance(lyr, QgsVectorLayer) and lyr.geometryType() == 1:
                self.source_combo.addItem(lyr.name(), lyr)
        layout.addWidget(self.source_combo)

        layout.addWidget(QLabel("New Extent Layer:"))
        self.target_combo = QComboBox()
        for lyr in self.target_layers:
            self.target_combo.addItem(lyr.name(), lyr)
        layout.addWidget(self.target_combo)
        
        # Action Button Layout
        btn_layout = QHBoxLayout()
        action_label = "Add" if self.is_add_mode else "Remove"
        
        self.run_btn = QPushButton(action_label)
        self.run_btn.setObjectName("executeBtn")
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.run_btn)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        self.run_btn.clicked.connect(self.process_operation)

    def process_operation(self):
        source_layer = self.source_combo.currentData()
        target_layer = self.target_combo.currentData()
        
        if not target_layer or not source_layer:
            QMessageBox.critical(self, "Error", "No valid target or source layer")
            return

        selected_features = source_layer.selectedFeatures()
        if len(selected_features) == 0:
            QMessageBox.warning(self, "No Selection Found", f"Select features in '{source_layer.name()}'")
            return

        if not target_layer.isEditable():
            target_layer.startEditing()
            
        target_spatial_index = QgsSpatialIndex(target_layer)
        modified_count = 0

        for feat in selected_features:
            geom = feat.geometry()
            bbox = geom.boundingBox()
            
            if self.is_add_mode:
                # Add Logic
                is_duplicate = False
                for existing_fid in target_spatial_index.intersects(bbox):
                    existing_feat = target_layer.getFeature(existing_fid)
                    if existing_feat and existing_feat.geometry().equals(geom):
                        is_duplicate = True
                        break
                if is_duplicate:
                    continue
                    
                new_feat = QgsFeature(target_layer.fields())
                new_feat.setGeometry(geom)
                for field in target_layer.fields():
                    field_name = field.name()
                    if feat.attribute(field_name) is not None:
                        new_feat.setAttribute(field_name, feat.attribute(field_name))
                
                if target_layer.addFeature(new_feat):
                    modified_count += 1
            else:
                # Remove Logic
                for existing_fid in target_spatial_index.intersects(bbox):
                    existing_feat = target_layer.getFeature(existing_fid)
                    if existing_feat and existing_feat.geometry().equals(geom):
                        if target_layer.deleteFeature(existing_fid):
                            modified_count += 1
                            break

        if modified_count == 0:
            target_layer.rollBack()
            action_name = "additions" if self.is_add_mode else "removals"
            QMessageBox.information(self, "Complete", f"No changes made. No valid features found for {action_name}.")
            self.close()
            return

        if target_layer.commitChanges():
            source_layer.removeSelection()
            target_layer.triggerRepaint()
            self.iface.mapCanvas().refresh()
            action_str = "appended" if self.is_add_mode else "removed"
            QMessageBox.information(self, "Success", f"Successfully {action_str} {modified_count} features.")
        else:
            target_layer.rollBack()
            QMessageBox.critical(self, "Write Error", "Failed to save changes to the target layer.")
        
        self.close()