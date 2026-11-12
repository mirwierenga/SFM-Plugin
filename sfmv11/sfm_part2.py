"""
SFM PART 2 - HEXCONFIG
calls HexConfig.py to generate hexagon grid, assign DEM elevation values & populates unique cell IDS

inputs:
- watershed IDs
"""

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from qgis.core import (QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterString, QgsProcessingException,)

class HexConfig(QgsProcessingAlgorithm):
    INPUT_CODE = 'INPUT_CODE'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(self.INPUT_CODE, 'Enter Watershed ID (4-Letter Code)', defaultValue=''))

    def processAlgorithm(self, parameters, context, feedback):
        raw_code = self.parameterAsString(parameters, self.INPUT_CODE, context)
        file_base = raw_code.strip().upper()
        
        if len(file_base) != 4:
            raise QgsProcessingException(f"Invalid code length: '{file_base}'. Must be exactly 4 characters.")
        #verify project workspace directory exists
        project_workspace = Path(r"C:\Albers10k2m") / f"SFM_{file_base}"
        if not project_workspace.exists() or not project_workspace.is_dir():
            raise QgsProcessingException(f"Workspace directory not found: {project_workspace}. " f"Ensure Part 1 (Setup) has been run successfully for '{file_base}'.")

        #log File path inside 0_Base directory
        log_file_path = project_workspace / "0_Base" / f"_{file_base}_Log.csv"

        def write_part2_log(message):
            try:
                os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
                file_exists = os.path.exists(log_file_path)
                with open(log_file_path, mode="a", newline="", encoding="utf-8") as logfile:
                    import csv
                    writer = csv.writer(logfile)
                    if not file_exists:
                        writer.writerow(["Log Details"])
                    writer.writerow([message])
            except Exception as e:
                feedback.reportError(f"Critical Logging Error: {e}")

        #path to the hexconfig
        script_path = r"C:\Albers10k2m\_Executables\HexConfig.py"
        if not os.path.exists(script_path):
            raise QgsProcessingException(f"Script not found at: {script_path}")

        command = ["python", script_path, file_base]
        start_time = time.perf_counter()
        timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_part2_log(f"{timestamp_start}    SFM_pt02 HexConfig: start")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            #capture stdout and stderr to display errors in QGIS
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creation_flags
            )
            stdout, stderr = process.communicate()
            if stdout.strip():
                feedback.pushInfo(f"HexConfig Output:\n{stdout}")
            if process.returncode != 0:
                err_msg = stderr.strip() if stderr.strip() else stdout.strip()
                raise QgsProcessingException(
                    f"HexConfig.py failed with return code {process.returncode}.\n\n"
                    f"Error Details:\n{err_msg}"
                )
        except Exception as e:
            if isinstance(e, QgsProcessingException):
                raise e
            raise QgsProcessingException(f"Failed to execute HexConfig.py: {e}")

        end_time = time.perf_counter()
        seconds = end_time - start_time
        minutes = int(seconds / 60)
        timestamp_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_part2_log(f"{timestamp_end}    SFM_pt02 HexConfig: total time elapsed {seconds:.0f}s [{minutes:.1f} min]")
        return {}

    def name(self):
        return 'albers_hex_config'

    def displayName(self):
        return 'Part 2 - HexConfig'

    def group(self, context=None):
        return 'SFM'

    def groupId(self, context=None):
        return 'sfm'

    def shortHelpString(self):
        return (
            "Hexconfig generates the hexagons with DEM elevations values and populates " 
            "the new unique cell_ID into the adjacent 6 cells."
        )    
    
    def createInstance(self):
        return HexConfig()