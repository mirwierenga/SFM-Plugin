"""
Part 4C - Export Extent CSV
Exports processed & finalized watershed extent 2pt layer to CSV format 

Inputs:
Final _XXXX_extent.shp layer

Outputs:
C:\Albers10k2m\SFM_XXXX\1_Main\_XXXX_extent.csv

Workflow: uses QGIS's built-in QgsVectorFileWriter to export the selected 
line layer directly to a CSV format
"""

import os
import time
from datetime import datetime
from pathlib import Path

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingException,
    QgsProject,
    QgsVectorFileWriter
)

class ExportExtentCsv(QgsProcessingAlgorithm):
    INPUT_LAYER = 'INPUT_LAYER'

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_LAYER,
                'Select Line Extent Layer',
                types=[QgsProcessing.TypeVectorLine]
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        # Grab the selected line layer from the dropdown menu
        target_layer = self.parameterAsVectorLayer(parameters, self.INPUT_LAYER, context)
        
        if not target_layer:
            raise QgsProcessingException("Invalid input layer selected.")

        layer_name = target_layer.name()
        
        # Extract the 4-letter watershed code dynamically from the layer name (e.g., SFM_XXXX_...)
        parts = layer_name.split('_')
        if len(parts) >= 2:
            file_base = parts[1].strip().upper()
        else:
            raise QgsProcessingException(
                f"Could not extract watershed code from layer name '{layer_name}'. "
                "Expected format like 'SFM_XXXX_...'"
            )

        if len(file_base) != 4:
            raise QgsProcessingException(
                f"Extracted code '{file_base}' from layer name is not 4 characters. "
                "Please ensure the layer matches naming conventions (SFM_XXXX_...)."
            )

        # Verify project workspace directory exists
        project_workspace = Path(r"C:\Albers10k2m") / f"SFM_{file_base}"
        if not project_workspace.exists() or not project_workspace.is_dir():
            raise QgsProcessingException(
                f"Workspace directory not found: {project_workspace}. "
                f"Please ensure Part 1 (Setup) has been run successfully for '{file_base}'."
            )

        # Target directory: C:\Albers10k2m\SFM_XXXX\1_Main
        output_dir = project_workspace / "1_Main"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Output filename: _XXXX_extent.csv
        output_csv_path = str(output_dir / f"_{file_base}_extent.csv")

        start_time = time.perf_counter()
        timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        feedback.pushInfo(f"{timestamp_start} SFM Export Extent CSV: start for watershed {file_base}")

        try:
            # Export vector layer to CSV using QgsVectorFileWriter
            save_options = QgsVectorFileWriter.SaveVectorOptions()
            save_options.driverName = "CSV"
            save_options.fileEncoding = "UTF-8"
            save_options.layerOptions = ["GEOMETRY=AS_WKT"]

            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                target_layer,
                output_csv_path,
                QgsProject.instance().transformContext(),
                save_options
            )

            if error[0] != QgsVectorFileWriter.NoError:
                raise QgsProcessingException(f"Failed to export layer to CSV. Error code: {error[0]}")

        except Exception as e:
            if isinstance(e, QgsProcessingException):
                raise e
            raise QgsProcessingException(f"An error occurred during CSV export: {e}")

        end_time = time.perf_counter()
        seconds = end_time - start_time
        timestamp_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        feedback.pushInfo(f"{timestamp_end} SFM Export Extent CSV: completed successfully in {seconds:.2f}s")
        feedback.pushInfo(f"Saved to: {output_csv_path}")
        
        return {}

    def name(self):
        return 'albers_export_extent_csv'

    def displayName(self):
        return 'Part X - Export Extent to CSV'

    def group(self, context=None):
        return 'SFM'

    def groupId(self, context=None):
        return 'sfm'

    def shortHelpString(self):
        return (
            "Selects an open line layer from a dropdown menu, automatically extracts "
            "the 4-letter watershed code from its name, and exports the data to "
            "C:\\Albers10k2m\\SFM_XXXX\\1_Main\\_XXXX_extent.csv."
        )   
    
    def createInstance(self):
        return ExportExtentCsv()