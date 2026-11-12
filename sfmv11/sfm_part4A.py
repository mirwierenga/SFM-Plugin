"""
Part 4A - Boundary Buffer
creates buffered layers to create ' corridor', then calls BoundaryCirc.exe & BoundaryREM.exe to fill the corridor & run 2pt top-down line trace and make REM for boundary

Inputs:
- watershed ID code
- Buffer distance (300m, 400m, or 500m)
- Simplification tolerance (50m, 74m, or 100m)
"""

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterString,
    QgsProcessingParameterEnum,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingUtils
)
import processing
import csv


class BoundaryCirc(QgsProcessingAlgorithm):

    WATERSHED_CODE = "WATERSHED_CODE"
    BUFFER_DIST = "BUFFER_DIST"
    TOLERANCE_VAL = "TOLERANCE_VAL"

    BUFFER_OPTIONS = [300, 400, 500]
    TOLERANCE_OPTIONS = [50.0, 75.0, 100.0]

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def name(self):
        return "boundarycirc"

    def displayName(self):
        return self.tr("Part 4 - BoundaryCirc & REM Boundary")

    def group(self, context=None):
        return self.tr("SFM")

    def groupId(self, context=None):
        return self.tr("sfm")

    def shortHelpString(self):
        return self.tr(
            "Creates boundary 'corridor' and runs top-down flow model to create a 2pt line "
            "shapefile inside the new corridor around the original watershed shape. Also "
            "resolves circular sinks in the arc networks and produces a REM around the original "
            "boundary from 4m DEM"
        )

    def createInstance(self):
        return BoundaryCirc()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(
                self.WATERSHED_CODE,
                self.tr("4-Letter Watershed ID Code"),
                defaultValue=""
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.BUFFER_DIST,
                self.tr("Buffer Distance (meters)"),
                options=[f"{opt} m" for opt in self.BUFFER_OPTIONS],
                defaultValue=0
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.TOLERANCE_VAL,
                self.tr("Simplification Tolerance"),
                options=[str(opt) for opt in self.TOLERANCE_OPTIONS],
                defaultValue=0
            )
        )

    def write_log(self, log_path, message):
        """Helper to append timestamped messages directly to the CSV log file."""
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            file_exists = os.path.exists(log_path)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, mode="a", newline="", encoding="utf-8") as logfile:
                writer = csv.writer(logfile)
                if not file_exists:
                    writer.writerow(["Log Details"])
                writer.writerow([f"{timestamp}    {message}"])
        except Exception:
            pass

    def process_buffer(
        self,
        input_layer_path,
        distance,
        tolerance,
        suffix,
        output_folder,
        file_base,
        log_file_path,
        context,
        feedback
    ):
        self.write_log(
            log_file_path,
            f"Creating {suffix} buffer ({abs(distance)} m) with tolerance ({tolerance})"
        )

        buffer_result = processing.run(
            "native:buffer",
            {
                "INPUT": input_layer_path,
                "DISTANCE": distance,
                "SEGMENTS": 5,
                "END_CAP_STYLE": 0,
                "JOIN_STYLE": 0,
                "MITER_LIMIT": 2,
                "DISSOLVE": False,
                "OUTPUT": "memory:"
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )

        no_holes_result = processing.run(
            "native:deleteholes",
            {
                "INPUT": buffer_result["OUTPUT"],
                "MIN_AREA": 0,
                "OUTPUT": "memory:"
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )

        singleparts_result = processing.run(
            "native:multiparttosingleparts",
            {
                "INPUT": no_holes_result["OUTPUT"],
                "OUTPUT": "memory:"
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )

        singleparts_layer = QgsProcessingUtils.mapLayerFromString(singleparts_result["OUTPUT"], context)
        
        if not singleparts_layer or singleparts_layer.featureCount() == 0:
            err_msg = f"Buffer distance of {distance}m collapsed geometry for '{suffix}' buffer entirely."
            self.write_log(log_file_path, f"ERROR: {err_msg}")
            raise QgsProcessingException(err_msg)

        output_file = os.path.join(
            output_folder,
            f"{file_base}_v0_{suffix}.shp"
        )

        largest_polygon_result = processing.run(
            "native:extractbyexpression",
            {
                "INPUT": singleparts_result["OUTPUT"],
                "EXPRESSION": "$area = maximum($area)",
                "OUTPUT": "memory:"
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )

        processing.run(
            "native:simplifygeometries",
            {
                "INPUT": largest_polygon_result["OUTPUT"],
                "METHOD": 0,
                "TOLERANCE": tolerance,
                "OUTPUT": output_file
            },
            context=context,
            feedback=feedback,
            is_child_algorithm=True
        )

        self.write_log(log_file_path, f"Saved: {os.path.basename(output_file)}")
        return output_file

    def processAlgorithm(self, parameters, context, feedback):
        raw_code = self.parameterAsString(parameters, self.WATERSHED_CODE, context)
        file_base = raw_code.strip().upper()

        if len(file_base) != 4:
            raise QgsProcessingException(f"Invalid Watershed Code '{raw_code}'. It must be exactly 4 letters.")

        # Input vector shapefile path
        input_shp_path = Path(r"C:\Albers10k2m\FWA_Watersheds") / f"{file_base}_v0.shp"
        if not input_shp_path.exists():
            raise QgsProcessingException(f"Input shapefile not found at: {input_shp_path}")

        buffer_index = self.parameterAsEnum(parameters, self.BUFFER_DIST, context)
        chosen_buffer = self.BUFFER_OPTIONS[buffer_index]

        tolerance_index = self.parameterAsEnum(parameters, self.TOLERANCE_VAL, context)
        chosen_tolerance = self.TOLERANCE_OPTIONS[tolerance_index]

        # Automatically create project workspace and subfolders if they don't exist
        project_workspace = Path(r"C:\Albers10k2m") / f"SFM_{file_base}"
        project_workspace.mkdir(parents=True, exist_ok=True)
        
        output_folder = project_workspace / "1_Main"
        output_folder.mkdir(parents=True, exist_ok=True)
        
        base_folder = project_workspace / "0_Base"
        base_folder.mkdir(parents=True, exist_ok=True)

        log_file_path = base_folder / f"_{file_base}_Log.csv"

        self.write_log(log_file_path, f"Processing layer for watershed: {file_base}")

        # 1. Run Buffer Pre-steps
        outside_file = self.process_buffer(
            str(input_shp_path),
            chosen_buffer,
            chosen_tolerance,
            "outside",
            str(output_folder),
            file_base,
            log_file_path,
            context,
            feedback
        )

        inside_file = self.process_buffer(
            str(input_shp_path),
            -chosen_buffer,
            chosen_tolerance,
            "inside",
            str(output_folder),
            file_base,
            log_file_path,
            context,
            feedback
        )

        # Helper function for fast, synchronous execution of executables
        def run_executable(exe_name, task_label):
            exe_path = Path(r"C:\Albers10k2m\_Executables") / exe_name
            if not exe_path.exists():
                err_msg = f"Executable not found at: {exe_path}"
                self.write_log(log_file_path, f"ERROR: {err_msg}")
                raise QgsProcessingException(err_msg)

            command = [str(exe_path), file_base]
            start_time = time.perf_counter()

            self.write_log(log_file_path, f"SFM_pt04 {task_label}: start")

            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    shell=False
                )

                if result.returncode != 0:
                    err_msg = result.stderr.strip() if result.stderr else f"Exit code {result.returncode}"
                    self.write_log(log_file_path, f"ERROR: {exe_name} failed: {err_msg}")
                    raise QgsProcessingException(f"{exe_name} failed: {err_msg}")

            except Exception as e:
                if isinstance(e, QgsProcessingException):
                    raise e
                err_msg = f"Failed to execute {exe_name}: {e}"
                self.write_log(log_file_path, f"ERROR: {err_msg}")
                raise QgsProcessingException(err_msg)

            end_time = time.perf_counter()
            seconds = end_time - start_time
            minutes = seconds / 60.0

            self.write_log(
                log_file_path,
                f"SFM_pt04 {task_label}: total time elapsed {seconds:.0f}s [{minutes:.1f} min]"
            )

        # 2. Execute boundarycirc.exe synchronously
        run_executable("BoundaryCirc.exe", "BoundaryCirc")

        # 3. Execute boundaryREM.exe synchronously
        run_executable("BoundaryREM.exe", "BoundaryREM")

        rem_tif_path = project_workspace / "1_Main" / "rem_boundary.tif"
        if rem_tif_path.exists():
            self.write_log(log_file_path, f"Verified REM raster output at: {rem_tif_path}")
        else:
            self.write_log(log_file_path, f"WARNING: REM raster expected but not found at: {rem_tif_path}")

        self.write_log(log_file_path, f"--- Part 4 - BoundaryCirc & REM Completed Successfully for {file_base} ---")

        return {}