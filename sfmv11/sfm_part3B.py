"""
Part 3B - Full 4m DEM
calls FullDEM4m.exe which converts watershed 2m DEM tiles & TRIM data into big full 4m DEM & generates hillshade

Inputs:
- watershed ID code
"""

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterString,
    QgsProcessingException,
)
import csv

class FullDEM(QgsProcessingAlgorithm):
    INPUT_CODE = 'INPUT_CODE'

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(
                self.INPUT_CODE,
                'Enter Watershed ID',
                defaultValue=''
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        raw_code = self.parameterAsString(parameters, self.INPUT_CODE, context)
        file_base = raw_code.strip().upper()
        
        if len(file_base) != 4:
            raise QgsProcessingException(f"Invalid code length '{file_base}'")

        # Verify that the project workspace directory exists
        project_workspace = Path(r"C:\Albers10k2m") / f"SFM_{file_base}"
        if not project_workspace.exists() or not project_workspace.is_dir():
            raise QgsProcessingException(
                f"Workspace directory not found: {project_workspace}. "
            )

        # Log File path inside 0_Base directory
        log_file_path = project_workspace / "0_Base" / f"_{file_base}_Log.csv"

        def write_part3_log(message):
            # Writes directly to log file silently without spamming processing feedback window
            try:
                os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
                file_exists = os.path.exists(log_file_path)
                with open(log_file_path, mode="a", newline="", encoding="utf-8") as logfile:
                    writer = csv.writer(logfile)
                    if not file_exists:
                        writer.writerow(["Log Details"])
                    writer.writerow([message])
            except Exception as e:
                feedback.reportError(f"Critical Logging Error: {e}")

        # Path to executable
        exe_path = r"C:\Albers10k2m\_Executables\FullDEM4m.exe"
        if not os.path.exists(exe_path):
            raise QgsProcessingException(f"Executable not found at: {exe_path}")

        # Added "0" as the final argument in the command list
        command = [exe_path, file_base, "0"]
        
        start_time = time.perf_counter()
        timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        write_part3_log(f"{timestamp_start}    4m DEM: start")

        # Run the executable synchronously and cleanly
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False
            )

            if result.returncode != 0:
                raise QgsProcessingException(f"4m_DEM.exe failed with return code {result.returncode}")

        except Exception as e:
            if isinstance(e, QgsProcessingException):
                raise e
            raise QgsProcessingException(f"Failed to execute 4m_DEM.exe: {e}")

        end_time = time.perf_counter()
        seconds = end_time - start_time
        minutes = seconds / 60.0
        timestamp_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        write_part3_log(f"{timestamp_end}    4m DEM: total time elapsed {seconds:.0f}s [{minutes:.1f} min]")
        return {}

    def name(self):
        return 'create_FullDEM'

    def displayName(self):
        return 'Create 4m Full DEM'

    def group(self, context=None):
        return 'SFM'

    def groupId(self, context=None):
        return 'sfm'

    def shortHelpString(self):
        return (
            "Creates a new 4m x 4m DEM from 2m DEMs and stitches them alltogether"
            "to be used to create REM (relative elevation model)"
        )    

    def createInstance(self):
        return FullDEM()