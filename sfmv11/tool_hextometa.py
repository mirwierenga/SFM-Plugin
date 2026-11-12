"""
SFM TOOL - WRITE HEX TO METAFILE

tool writes selected hex ids from qgis layers to the watershed metafile using a selected prefix

workflow:
1. select the required hex features in qgis
2. run write hexids to meta
3. choose trim, lidar, or outlet
4. the tool finds the correct layer and watershed folder automatically
5. the selected hex ids are added or updated in the metafile
6. for outlet, only one selected feature is allowed
7. the updated metafile is saved in the watershed 0_base folder

"""

from PyQt5.QtCore import QCoreApplication, QSize
from qgis.core import (QgsProject, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterEnum, QgsProcessingOutputFile, QgsProcessingException)
import os

class writehextometa(QgsProcessingAlgorithm):

    LAYER_SUFFIX = "_MasterEDGE"
    OUTLET_LAYER_SUFFIX = "_2pt"
    BASE_ROOT_FOLDER = r"C:\Albers10k2m"

    PREFIX_OPTION = "PREFIX_OPTION"
    OUTPUT_CSV_OUT = "OUTPUT_CSV_OUT"

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def name(self):
        return "writehextometacsv"

    def displayName(self):
        return self.tr("Write HexIDs to Meta")

    def group(self):
        return self.tr("SFM Tools")

    def groupId(self):
        return "sfmtools"

    def _find_target_layer(self, suffix):
        """
        Finds the layer based on the specified suffix AND checks if it has active selections.
        Falls back to just the suffix matching if no selection is found project-wide.
        """
        fallback = None
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name().endswith(suffix):
                if layer.selectedFeatureCount() > 0:
                    return layer
                fallback = layer
        return fallback

    def _get_dynamic_paths(self, layer_name):
        """
        Extracts the 4 letters (e.g. GRNL) and places them in the middle of the path layout.
        Returns a tuple: (Target Folder Path, Target Full CSV File Path)
        """
        only_letters = "".join(char for char in layer_name if char.isalpha())
        four_letter_code = only_letters[:4] if only_letters else "UNKNOWN"
        
        target_folder = os.path.join(self.BASE_ROOT_FOLDER, f"SFM_{four_letter_code}", "0_Base")
        target_file = os.path.join(target_folder, f"_{four_letter_code}_Meta.csv")
        
        return target_folder, target_file

    def shortHelpString(self):
        """Displays clear selection status text on the right side of the window with full prefix details."""
        master_layer = self._find_target_layer(self.LAYER_SUFFIX)
        outlet_layer = self._find_target_layer(self.OUTLET_LAYER_SUFFIX)
        
        if outlet_layer and outlet_layer.selectedFeatureCount() > 0:
            target_layer = outlet_layer
            label_prefix = "Input Layer (2pt)"
        else:
            target_layer = master_layer
            label_prefix = "Input Layer (Edge)"

        if target_layer is not None:
            layer_name = target_layer.name()
            selection_count = target_layer.selectedFeatureCount()
            _, target_file = self._get_dynamic_paths(layer_name)
        else:
            layer_name = f"*{self.LAYER_SUFFIX} / *{self.OUTLET_LAYER_SUFFIX}"
            selection_count = 0
            target_file = os.path.join(self.BASE_ROOT_FOLDER, "SFM_XXXX", "0_Base", "_XXXX_Meta.csv")
            
        return self.tr(
            f"<h3>Write HexIDs to Meta</h3>"
            f"<p>Tool appends or updates the ID of user-selected hex features to metafile with a designated prefix</p>"
            f"<h4>Prefix options:</h4>"
            f"<ul>"
            f"  <li><b>TRIM:</b> Appends infill selected NULL edge(s) with TRIM data</li>"
            f"  <li><b>LDR:</b> Appends infill selected NULL edge(s) with old LiDAR data</li>"
            f"  <li><b>OUT:</b> Watershed outlet cell (replaces if exists)</li>"
            f"  <li><b>TAR:</b> Watershed target cell (replaces if exists)</li>"
            f"</ul>"
            f"<hr>"
            f"<h4>Current Status:</h4>"
            f"  <li><b>{label_prefix}:</b> <code style='color: #2b8cbe;'>{layer_name}</code></li>"
            f"  <li><b>Selected Features:</b> <b style='color: {'#2ca25f' if selection_count > 0 else '#dd1c77'};'>{selection_count}</b></li>"
            f"  <li><b>Output File:</b> <code style = 'color: #2b8cbe;'>{target_file}</code></li>"
        )
        
    def createInstance(self):
        return writehextometa()

    def initAlgorithm(self, config=None):
        default_index = 0  # default to Trim

        outlet_layer = self._find_target_layer(self.OUTLET_LAYER_SUFFIX)
        if outlet_layer and outlet_layer.selectedFeatureCount() > 0:
            default_index = 2  # Outlet

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PREFIX_OPTION,
                self.tr("Select prefix to write to Metafile"),
                options=["Trim", "Lidar", "Outlet", "Target"],
                defaultValue=default_index,
                allowMultiple=False
            )
        )

        self.addOutput(
            QgsProcessingOutputFile(
                self.OUTPUT_CSV_OUT,
                self.tr("Updated CSV File")
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        prefix_idx = self.parameterAsEnum(parameters, self.PREFIX_OPTION, context)
        if prefix_idx == 0:
            prefix = "TRM"
            target_suffix = self.LAYER_SUFFIX
            target_field_name = "globalid"

        elif prefix_idx == 1:
            prefix = "LDR"
            target_suffix = self.LAYER_SUFFIX
            target_field_name = "globalid"

        elif prefix_idx == 2:
            prefix = "OUT"
            target_suffix = self.OUTLET_LAYER_SUFFIX
            target_field_name = "NodeID"

        else:
            prefix = "TAR"
            target_suffix = self.OUTLET_LAYER_SUFFIX
            target_field_name = "NodeID"

        layer = self._find_target_layer(target_suffix)
        if layer is None:
            raise QgsProcessingException(f"No layer ending with '{target_suffix}' was found in project.")
        output_dir, output_csv = self._get_dynamic_paths(layer.name())

        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
                feedback.pushInfo(f"Created directory structure: {output_dir}")
            except Exception as e:
                raise QgsProcessingException(f"Failed to create directory '{output_dir}': {str(e)}")

        selected_features = layer.selectedFeatures()
        selection_count = len(selected_features)

        if selection_count == 0:
            raise QgsProcessingException(f"'{layer.name()}' does not have any selected features.\n")

        if prefix in ["OUT", "TAR"] and selection_count > 1:
            raise QgsProcessingException(
                f"Selected {selection_count} features, only 1 feature can be assigned for {prefix}"
            )

        feedback.pushInfo(f"Targeting Layer: {layer.name()}")
        feedback.pushInfo(f"Processing prefix: '{prefix}' on file: {output_csv}")

        fields = layer.fields()
        resolved_id_field_name = None
        
        normalized_target = target_field_name.lower().replace(" ", "")
        
        for field in fields:
            normalized_field = field.name().lower().replace(" ", "")
            if normalized_field == normalized_target:
                resolved_id_field_name = field.name()
                break

        if not resolved_id_field_name:
            raise QgsProcessingException(f"The layer '{layer.name()}' does not contain an identification field matching '{target_field_name}'.")

        new_lines = []
        for feature in selected_features:
            gid = feature[resolved_id_field_name]
            gid_str = str(gid) if gid is not None else "NULL"
            new_lines.append(f"{prefix} {gid_str}\n")

        if os.path.exists(output_csv) and os.path.getsize(output_csv) > 0:
            with open(output_csv, "r", encoding="utf-8") as csv_file:
                existing_lines = csv_file.readlines()
            
            existing_lines = [line for line in existing_lines if line.strip()]

            if prefix in ["OUT", "TAR"]:
                updated_lines = []
                replaced = False

                for line in existing_lines:
                    if line.startswith(prefix):
                        if new_lines:
                            updated_lines.append(new_lines[0])
                            replaced = True
                    else:
                        updated_lines.append(
                            line if line.endswith("\n") else f"{line}\n"
                        )

                if not replaced and new_lines:
                    updated_lines.append(new_lines[0])

            else:
                updated_lines = [
                    line if line.endswith("\n") else f"{line}\n"
                    for line in existing_lines
                ]

                for n_line in new_lines:
                    updated_lines.append(n_line)
        else:
            updated_lines = new_lines

        with open(output_csv, "w", encoding="utf-8") as csv_file:
            csv_file.writelines(updated_lines)

        feedback.pushInfo(f"IDs successfully written to: {os.path.basename(output_csv)}")

        return {self.OUTPUT_CSV_OUT: output_csv}
