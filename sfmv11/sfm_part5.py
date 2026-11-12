import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
)

class FlowModel(QgsProcessingAlgorithm):
    INPUT_CODE = 'INPUT_CODE'
    IS_TEST = 'IS_TEST'

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(
                self.INPUT_CODE,
                'Enter Watershed ID',
                defaultValue=''
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.IS_TEST,
                'Run as Test Mode',
                defaultValue=False
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        raw_code = self.parameterAsString(parameters, self.INPUT_CODE, context)
        is_test_mode = self.parameterAsBoolean(parameters, self.IS_TEST, context)
        
        file_base = raw_code.strip().upper()
        
        if len(file_base) != 4:
            raise QgsProcessingException(f"Invalid code length: '{file_base}'.")

        # Verify that the project workspace directory exists
        project_workspace = Path(r"C:\Albers10k2m") / f"SFM_{file_base}"
        if not project_workspace.exists() or not project_workspace.is_dir():
            raise QgsProcessingException(
                f"Workspace directory not found: {project_workspace}. "
                f"Please ensure Part 1 (Setup) has been run successfully for '{file_base}'."
            )

        # Determine run mode argument for 5A
        run_mode = "test" if is_test_mode else "full"

        # Log File path inside 0_Base directory
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

        # Helper function to run executables safely with feedback/cancellation support
        def run_executable(exe_path, command_args, step_name):
            if not os.path.exists(exe_path):
                raise QgsProcessingException(f"Executable not found at: {exe_path}")

            full_command = [exe_path] + command_args
            feedback.pushInfo(f"Running: {' '.join(full_command)}")

            try:
                process = subprocess.Popen(
                    full_command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False
                )

                while process.poll() is None:
                    if feedback.isCanceled():
                        process.terminate()
                        raise QgsProcessingException("Algorithm was cancelled by the user.")
                    time.sleep(0.2)

                return_code = process.returncode
                if return_code != 0:
                    raise QgsProcessingException(f"{Path(exe_path).name} failed with return code {return_code}")

            except Exception as e:
                if isinstance(e, QgsProcessingException):
                    raise e
                raise QgsProcessingException(f"Failed to execute {Path(exe_path).name}: {e}")

        start_time = time.perf_counter()
        timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        write_part2_log(f"{timestamp_start}    SFM_pt05 FlowModel: start ({run_mode.upper()})")

        # 1. Run 5A_Model.exe
        exe_path_5a = r"C:\Albers10k2m\_Executables\5A_Model.exe"
        run_executable(exe_path_5a, [file_base, run_mode], "5A_Model")

        # 2. Run 5B_Lakes.exe
        exe_path_lakes = r"C:\Albers10k2m\_Executables\5B_Lakes.exe"
        run_executable(exe_path_lakes, [file_base], "5B_Lakes")

        # 3. Run 5C_Topo.exe
        exe_path_topo = r"C:\Albers10k2m\_Executables\5C_Topo.exe"
        run_executable(exe_path_topo, [file_base], "5C_Topo")

        end_time = time.perf_counter()
        seconds = end_time - start_time
        minutes = seconds / 60
        timestamp_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        write_part2_log(f"{timestamp_end}    SFM_pt05 FlowModel: total time elapsed {seconds:.0f}s [{minutes:.1f} min]")
        return {}

    def name(self):
        return 'surface_flow_model'

    def displayName(self):
        return 'Part 5 - Surface Flow Model'

    def group(self, context=None):
        return 'SFM'

    def groupId(self, context=None):
        return 'sfm'

    def shortHelpString(self):
        return (
            "creates 2pt flow paths / river class / lake class outputs"
        ) 

    def createInstance(self):
        return FlowModel()