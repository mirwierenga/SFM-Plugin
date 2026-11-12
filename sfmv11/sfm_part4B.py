"""
Part 4B - Create Extent
Tool runs series of checks on 2pt arrow 600m corridor around initial shapefile to determine what flows in/out to redefine watershed extent boundary

Inputs:
- watershed ID code
- 2pt arrow 600m 'corridor' boundary shapefile
- manually selected output arc

Workflow:
1. read networks up & downstream & run checks:
    1. keep if any arc in network is outlet, otherwise continue
    2. keep if downstream flow deep into intial boundary, otherwise continue
    3. reject if no arrows in network touch intial boundary, otherwise continue
    4. reject if downstream flow on edge, otherwise continue
    5. keep if all arrows in network inside boundary, otherwise continue
    6. if reach this point, add to prompt manual user checks (using hillshades & REM)
2. prompt user verification
3. build _XXXX_extent.shp

"""

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, 
    QListWidget, QListWidgetItem, QPushButton, QMessageBox
)
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsSpatialIndex,
    QgsRectangle,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsGeometry,
    QgsPointXY
)
from qgis.utils import iface

import os
import math
import time
import processing


class FindExtent(QDialog):
    def __init__(self, parent=iface.mainWindow()):
        super().__init__(parent)
        self.setWindowTitle("Corridor Flow Trace Extent - Boundary Extent NEW")
        self.resize(450, 260)
        
        # Streamlined stylesheet: Only forces the blue background on the dialog box, 
        # allowing comboboxes, lists, and buttons to use standard QGIS / OS defaults.
        self.setStyleSheet("""
            QDialog {
                background-color: #ADD8E6 !important;
            }
        """)
        
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.line_layers = []
        self.poly_layers = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                if layer.geometryType() == 1:  # Line
                    self.line_layers.append(layer)
                elif layer.geometryType() == 2:  # Polygon
                    self.poly_layers.append(layer)

        layout.addWidget(QLabel("Input Flow Lines Layer (Corridor):"))
        self.line_combo = QComboBox()
        for lyr in self.line_layers:
            self.line_combo.addItem(lyr.name(), lyr)
        layout.addWidget(self.line_combo)
        
        layout.addWidget(QLabel("Initial Boundary Layer:"))
        self.poly_combo = QComboBox()
        for lyr in self.poly_layers:
            self.poly_combo.addItem(lyr.name(), lyr)
        layout.addWidget(self.poly_combo)

        layout.addWidget(QLabel("Neighboring Watersheds Layers (Optional - Multiple Selection):"))
        self.neighbor_list = QListWidget()
        self.neighbor_list.setSelectionMode(QListWidget.MultiSelection)
        for lyr in self.poly_layers:
            item = QListWidgetItem(lyr.name())
            item.setData(Qt.UserRole, lyr)
            self.neighbor_list.addItem(item)
        layout.addWidget(self.neighbor_list)
        
        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton("Run Automated Trace")
        self.reset_btn = QPushButton("Reset Selections")
        self.cancel_btn = QPushButton("Cancel")
        
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(self.reset_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)
        
        self.run_btn.clicked.connect(self.execute_analysis)
        self.reset_btn.clicked.connect(self.handle_reset_action)
        self.cancel_btn.clicked.connect(self.reject)

    def handle_reset_action(self):
        clayer = self.line_combo.currentData()
        if clayer:
            clayer.removeSelection()
            clayer.triggerRepaint()
        iface.mapCanvas().refresh()
        QMessageBox.information(self, "Interface Reset", "Map selections cleared.")

    def format_milestone_log(self, map_code, action_msg):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        return f"{timestamp}\tSFM_pt4 Boundary extent NEW\t\t{map_code}\t{action_msg}"

    def format_final_log(self, map_code, action_msg, elapsed_seconds):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        minutes = round(elapsed_seconds / 60.0, 1)
        return f"{timestamp}\tSFM_pt4 Boundary extent NEW\t\t{map_code}\t{action_msg} ... time = {elapsed_seconds:.1f} sec [{minutes:.1f} min]"

    def append_raw_line_to_log(self, filepath, log_string):
        with open(filepath, mode='a', encoding='utf-8') as f:
            f.write(log_string + "\n")

    def execute_analysis(self):
        clayer = self.line_combo.currentData()
        base_boundary_layer = self.poly_combo.currentData()
        
        selected_neighbor_items = self.neighbor_list.selectedItems()
        neighbor_layers = [item.data(Qt.UserRole) for item in selected_neighbor_items]
        
        if not clayer or not base_boundary_layer:
            QMessageBox.critical(self, "Error", "Please select valid input layers.")
            return

        pre_selected_fids = set(clayer.selectedFeatureIds())
        if not pre_selected_fids:
            QMessageBox.warning(
                self, 
                "Selection Required", 
                "Please select the outlet arc feature(s) in the flow lines layer before running."
            )
            return

        start_time = time.time()
        self.hide()
        
        try:
            base_boundary_path = base_boundary_layer.source().split("|")[0]
            boundary_filename = os.path.basename(base_boundary_path)
            map_code = boundary_filename[:4].upper()
            base_drive = base_boundary_path.split("FWA_Watersheds")[0] if "FWA_Watersheds" in base_boundary_path else "C:/Albers10k2m/"
            
            target_directory = os.path.join(base_drive, f"SFM_{map_code}", "1_Main")
            log_directory = os.path.join(base_drive, f"SFM_{map_code}", "0_base")
            
            os.makedirs(target_directory, exist_ok=True)
            os.makedirs(log_directory, exist_ok=True)

            shp_output_path = os.path.join(target_directory, f"_{map_code}_extent.shp")
            log_txt_path = os.path.join(log_directory, f"_{map_code}_Log.csv")

            log_line = self.format_milestone_log(map_code, "Begin ...")
            self.append_raw_line_to_log(log_txt_path, log_line)

            base_boundary_features = list(base_boundary_layer.getFeatures())

            neighbor_geoms = []
            for n_layer in neighbor_layers:
                for f in n_layer.getFeatures():
                    if f.geometry() and not f.geometry().isEmpty():
                        neighbor_geoms.append(f.geometry())
            
            geom_neighbors_union = QgsGeometry.unaryUnion(neighbor_geoms) if neighbor_geoms else None

            buffer_in_25_res = processing.run("native:buffer", {
                'INPUT': base_boundary_layer, 'DISTANCE': -25.0, 'SEGMENTS': 5, 
                'END_CAP_STYLE': 0, 'JOIN_STYLE': 0, 'MITER_LIMIT': 2, 'DISSOLVE': True, 'OUTPUT': 'memory:'
            })

            buffer_in_290_res = processing.run("native:buffer", {
                'INPUT': base_boundary_layer, 'DISTANCE': -290.0, 'SEGMENTS': 5, 
                'END_CAP_STYLE': 0, 'JOIN_STYLE': 0, 'MITER_LIMIT': 2, 'DISSOLVE': True, 'OUTPUT': 'memory:'
            })

            buffer_out_25_res = processing.run("native:buffer", {
                'INPUT': base_boundary_layer, 'DISTANCE': 25.0, 'SEGMENTS': 5, 
                'END_CAP_STYLE': 0, 'JOIN_STYLE': 0, 'MITER_LIMIT': 2, 'DISSOLVE': True, 'OUTPUT': 'memory:'
            })

            geom_boundary_union = QgsGeometry.unaryUnion([f.geometry() for f in base_boundary_features])
            geom_buffer_in_25 = QgsGeometry.unaryUnion([f.geometry() for f in buffer_in_25_res['OUTPUT'].getFeatures()])
            geom_buffer_in_290 = QgsGeometry.unaryUnion([f.geometry() for f in buffer_in_290_res['OUTPUT'].getFeatures()])
            geom_buffer_out_25 = QgsGeometry.unaryUnion([f.geometry() for f in buffer_out_25_res['OUTPUT'].getFeatures()])

            log_line = self.format_milestone_log(map_code, "Network tracing started ...")
            self.append_raw_line_to_log(log_txt_path, log_line)

            tolerance = 10.0
            index = QgsSpatialIndex(clayer)
            dict_features = {f.id(): f for f in clayer.getFeatures()}

            def get_nodes(fg):
                if not fg or fg.isEmpty():
                    return []
                try:
                    if fg.isMultipart():
                        multi_line = fg.asMultiPolyline()
                        if multi_line: return [multi_line[0][0], multi_line[-1][-1]]
                    else:
                        line = fg.asPolyline()
                        if line: return [line[0], line[-1]]
                except Exception:
                    pass
                return []

            def geometry_intersects_combined(geom, combined_poly):
                if not geom or geom.isEmpty() or not combined_poly or combined_poly.isEmpty():
                    return False
                return geom.intersects(combined_poly) or geom.within(combined_poly)

            global_visited = set()
            auto_accepted_fids = set()
            manual_clusters = []       
            all_fids = set(dict_features.keys())

            fields = clayer.fields()
            field_names = [f.name().lower() for f in fields]
            
            node_id_idx = field_names.index('nodeid') if 'nodeid' in field_names else -1
            tnode_idx = field_names.index('tnode') if 'tnode' in field_names else (field_names.index('tnodeid') if 'tnodeid' in field_names else -1)
            tx_idx = field_names.index('tx') if 'tx' in field_names else -1
            ty_idx = field_names.index('ty') if 'ty' in field_names else -1

            for seed_fid in all_fids:
                if seed_fid in global_visited:
                    continue

                current_network_set = set()
                processing_queue = {seed_fid}

                while processing_queue:
                    feat_id = processing_queue.pop()
                    if feat_id in current_network_set: continue
                    current_network_set.add(feat_id)
                    global_visited.add(feat_id)

                    feat = dict_features.get(feat_id)
                    if not feat: continue
                    nodes = get_nodes(feat.geometry())
                    if not nodes: continue

                    for node in [nodes[0], nodes[-1]]:
                        rect = QgsRectangle(node.x()-tolerance, node.y()-tolerance, node.x()+tolerance, node.y()+tolerance)
                        for fid in index.intersects(rect):
                            if fid not in current_network_set and fid not in global_visited:
                                f_item = dict_features.get(fid)
                                if f_item:
                                    fnodes = get_nodes(f_item.geometry())
                                    if fnodes and (math.hypot(fnodes[0].x()-node.x(), fnodes[0].y()-node.y()) <= tolerance or 
                                                   math.hypot(fnodes[-1].x()-node.x(), fnodes[-1].y()-node.y()) <= tolerance):
                                        processing_queue.add(fid)

                if not current_network_set:
                    continue

                if not current_network_set.isdisjoint(pre_selected_fids):
                    auto_accepted_fids.update(current_network_set)
                    continue

                touches_boundary = False
                for fid in current_network_set:
                    if dict_features[fid].geometry().intersects(geom_boundary_union):
                        touches_boundary = True
                        break

                if not touches_boundary:
                    continue

                if geom_neighbors_union and not geom_neighbors_union.isEmpty():
                    touches_neighbor = False
                    for fid in current_network_set:
                        if dict_features[fid].geometry().intersects(geom_neighbors_union):
                            touches_neighbor = True
                            break
                    if touches_neighbor:
                        continue

                terminal_feats = []
                sink_pts = []

                if node_id_idx != -1 and tnode_idx != -1:
                    network_node_ids = {str(dict_features[fid].attribute(node_id_idx)) for fid in current_network_set if dict_features[fid].attribute(node_id_idx) is not None}
                    for fid in current_network_set:
                        tn_val = dict_features[fid].attribute(tnode_idx)
                        if tn_val is not None and str(tn_val) not in network_node_ids:
                            terminal_feats.append(dict_features[fid])

                if not terminal_feats:
                    max_d = -1.0
                    best_f = None
                    for fid in current_network_set:
                        f_item = dict_features[fid]
                        for fn in get_nodes(f_item.geometry()):
                            d = QgsGeometry.fromPointXY(fn).distance(geom_boundary_union)
                            if d > max_d:
                                max_d = d
                                best_f = f_item
                    if best_f:
                        terminal_feats.append(best_f)

                for tf in terminal_feats:
                    s_pt = None
                    if tx_idx != -1 and ty_idx != -1:
                        tx_val, ty_val = tf.attribute(tx_idx), tf.attribute(ty_idx)
                        if tx_val is not None and ty_val is not None:
                            s_pt = QgsGeometry.fromPointXY(QgsPointXY(float(tx_val), float(ty_val)))
                    if not s_pt:
                        fnodes = get_nodes(tf.geometry())
                        if fnodes: s_pt = QgsGeometry.fromPointXY(fnodes[-1])
                    if s_pt: sink_pts.append(s_pt)

                end_arc_in_25m_int = any(geometry_intersects_combined(s_pt, geom_buffer_in_25) for s_pt in sink_pts)
                if not end_arc_in_25m_int:
                    end_arc_in_25m_int = any(geometry_intersects_combined(tf.geometry(), geom_buffer_in_25) for tf in terminal_feats)

                if end_arc_in_25m_int:
                    auto_accepted_fids.update(current_network_set)
                    continue

                end_arc_flowing_away = any(not geometry_intersects_combined(s_pt, geom_buffer_out_25) for s_pt in sink_pts)
                if not end_arc_flowing_away:
                    end_arc_flowing_away = any(not geometry_intersects_combined(tf.geometry(), geom_buffer_out_25) for tf in terminal_feats)

                if end_arc_flowing_away:
                    continue

                all_lines_inside = all(geometry_intersects_combined(dict_features[fid].geometry(), geom_boundary_union) for fid in current_network_set)
                if all_lines_inside:
                    auto_accepted_fids.update(current_network_set)
                    continue

                intersects_290m = any(geometry_intersects_combined(dict_features[fid].geometry(), geom_buffer_in_290) for fid in current_network_set)
                if intersects_290m:
                    auto_accepted_fids.update(current_network_set)
                    continue

                manual_clusters.append(current_network_set)

            elapsed_seconds = round(time.time() - start_time, 2)
            log_line = self.format_milestone_log(map_code, f"User verification initialized after {elapsed_seconds} sec ...")
            self.append_raw_line_to_log(log_txt_path, log_line)

            filtered_manual_clusters = []
            for cluster in manual_clusters:
                if len(cluster) <= 60:
                    auto_accepted_fids.update(cluster)
                else:
                    filtered_manual_clusters.append(cluster)
            manual_clusters = filtered_manual_clusters

            idx = 0
            session_decisions = {} 
            terminated_early = False
            
            lock_active = True
            target_cluster_fids = set()
            original_lock_flags = clayer.flags()

            def enforce_selection_lock():
                if lock_active and target_cluster_fids:
                    current_sel = set(clayer.selectedFeatureIds())
                    if current_sel != target_cluster_fids:
                        clayer.removeSelection()
                        clayer.selectByIds(list(target_cluster_fids))

            clayer.setFlags(clayer.flags() & ~QgsVectorLayer.LayerFlag(1))
            clayer.selectionChanged.connect(enforce_selection_lock)

            try:
                while idx < len(manual_clusters):
                    current_cluster = manual_clusters[idx]
                    target_cluster_fids = current_cluster
                    
                    clayer.removeSelection()
                    clayer.selectByIds(list(current_cluster))
                    
                    canvas = iface.mapCanvas()
                    canvas.setCenter(clayer.boundingBoxOfSelected().center())
                    canvas.zoomScale(4800.0)
                    canvas.refresh()

                    msg_box = QMessageBox(iface.mainWindow())
                    msg_box.setWindowModality(Qt.NonModal)
                    msg_box.setIcon(QMessageBox.Question)
                    msg_box.setWindowTitle("Network Validation Check - Boundary Extent NEW")
                    msg_box.setText(f"Straddling cluster evaluation [{idx + 1} of {len(manual_clusters)}]\nSize: {len(current_cluster)} lines.")
                    
                    keep_btn = msg_box.addButton("Keep", QMessageBox.YesRole)
                    reject_btn = msg_box.addButton("Reject", QMessageBox.NoRole)
                    back_btn = msg_box.addButton("Back", QMessageBox.ActionRole)
                    terminate_btn = msg_box.addButton("Terminate", QMessageBox.RejectRole)
                    
                    if idx == 0:
                        back_btn.setEnabled(False)
                    
                    msg_box.show()
                    while msg_box.isVisible():
                        enforce_selection_lock()
                        QCoreApplication.processEvents()
                        time.sleep(0.02)
                    
                    clicked = msg_box.clickedButton()
                    
                    if clicked == keep_btn:
                        session_decisions[idx] = "KEEP"
                        idx += 1
                    elif clicked == reject_btn:
                        session_decisions[idx] = "REJECT"
                        idx += 1
                    elif clicked == back_btn:
                        idx -= 1
                        if idx in session_decisions:
                            del session_decisions[idx]
                    elif clicked == terminate_btn:
                        terminated_early = True
                        break
                    elif not clicked:
                        terminated_early = True
                        break
            finally:
                lock_active = False
                try:
                    clayer.selectionChanged.disconnect(enforce_selection_lock)
                except Exception:
                    pass
                try:
                    clayer.setFlags(original_lock_flags)
                except Exception:
                    pass

            accepted_fids = set(auto_accepted_fids)
            for m_idx, outcome in session_decisions.items():
                if outcome == "KEEP":
                    accepted_fids.update(manual_clusters[m_idx])

            clayer.removeSelection()

            if not accepted_fids:
                QMessageBox.warning(iface.mainWindow(), "Cancelled", "No networks accepted. Output skipped.")
                self.show()
                return

            log_line = self.format_milestone_log(map_code, "Finalizing shapefile ...")
            self.append_raw_line_to_log(log_txt_path, log_line)
            
            clayer.selectByIds(list(accepted_fids))
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.onlySelectedFeatures = True
            opts.driverName = "ESRI Shapefile"
            opts.fileEncoding = "UTF-8"
            
            QgsVectorFileWriter.writeAsVectorFormatV3(
                clayer, shp_output_path, QgsProject.instance().transformContext(), opts
            )
            clayer.removeSelection()
            
            final_layer = QgsVectorLayer(shp_output_path, f"SFM_{map_code.lower()}_extent_v2", "ogr")
            QgsProject.instance().addMapLayer(final_layer)
            
            elapsed_seconds = round(time.time() - start_time, 2)
            log_line = self.format_final_log(map_code, "Exit", elapsed_seconds)
            self.append_raw_line_to_log(log_txt_path, log_line)

            status_msg = "Done!" if not terminated_early else "Process Terminated Early. Progress verified prior to exit has been saved."
            QMessageBox.information(iface.mainWindow(), "Success", f"{status_msg}\nSaved to: {target_directory}")

        except Exception as err:
            QMessageBox.critical(iface.mainWindow(), "Pipeline Failure Error", str(err))
            self.show()
        finally:
            if 'final_layer' in locals():
                self.close()


class BoundaryExtent(QgsProcessingAlgorithm):
    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def name(self):
        return "boundaryextentnew"

    def displayName(self):
        return self.tr("Boundary Extent NEW")

    def group(self):
        return self.tr("SFM Tools")

    def groupId(self):
        return "sfmtools"

    def shortHelpString(self):
        return self.tr("Launches the updated verification pipeline panel safely on the main thread.")

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def createInstance(self):
        return BoundaryExtent()

    def initAlgorithm(self, config=None):
        pass

    def processAlgorithm(self, parameters, context, feedback):
        dialog = FindExtent()
        dialog.exec_()
        return {}