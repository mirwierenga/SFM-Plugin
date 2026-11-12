"""
SFM PART 1 - SETUP
initializes sfm watershed workspace, pulls initial maps (old LiDAR, TRIM, Hexes), & generates .bat to process new LiDAR

inputs:
- watershed ID code, if doesnt exist in FWA will check in FWA_Watersheds directory (WOLD/MGLY)

outputs:
- watershed workspace with _OLD, _TRM, _HEX filled with gdwuts existing data
- 0_Base with metafile, maplist, logfile
- Get_New_LiDAR.bat to retrieve, process, & fill _NEW

workflow:
1. validate watershed ID and locate the watershed boundary
2. create the sfm_<code> workspace and required folders
3. create meta log and mapsheet files
4. generate the 10 km mapsheet list from the watershed extent
5. retrieve existing gdwuts hexes sheets, trim DEM + hillshade, and old lidar DEM data
6. identify available new lidar for each mapsheet
7. generate Get_New_Lidar.bat which when run for each mapsheet will
    1. Initialize GDAL/QGIS environment
    2. Merge and reproject LiDAR DEMs to the dominant UTM zone (gdalwarp)
    3. Clip the merged raster to the mapsheet area (gdal_translate)
    4. Convert the raster to BC Albers (EPSG:3005) at 2 m resolution (gdalwarp)
    5. Clip again to the exact 10 km Albers tile extent (gdal_translate)
    6. Run python executable tiffconvert.py to create the final DEM format
    7. Delete temporary files
    8. Generate Hillshade from the DEM (gdaldem hillshade)
    9. Generate TRI (Terrain Ruggedness Index) (gdaldem TRI)
8. log progress and processing times

Note: adapted start from Mike VB June 2026
"""

from qgis.core import (QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterString, QgsProcessingParameterFile, QgsVectorLayer, QgsProject, QgsProcessingException, QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsGeometry, QgsVectorFileWriter,)
import os
import sys
import shutil
import csv
import re
import time
from datetime import datetime
import py7zr
from pathlib import Path
import processing

qgis_bin_dir = os.path.dirname(sys.executable)
o4w_env_path = os.path.join(qgis_bin_dir, "o4w_env.bat")
ALBERS_GRID_SHP = r"C:\Albers10k2m\LiDAR_Prep\_ALBERS10k.shp"
DEM_INDEX_CSV = r"C:\Albers10k2m\LiDAR_Prep\Map2500_DEM.csv"
OUTPUT_ROOT = Path(r"C:\Albers10k2m\LiDAR_Prep")
BUFFER_DISTANCE_METERS = 1200
ALB_MS_ID_FIELD = "HEX_ID16"
UNC_ROOT = Path(r"V:\\")

def normalize_to_albers(mapsheet: str) -> str:
    """makes sure mapsheet naming uses ALB prefix"""
    if not mapsheet:
        return ""
    mapsheet = mapsheet.strip().upper()
    if mapsheet.startswith("MAP_"):
        return mapsheet.replace("MAP_", "ALB_", 1)
    return mapsheet

def parse_mapsheets(value: str) -> list:
    """reads target mapsheet names from csv file path or comma separated txt file path"""
    value = value.strip()
    
    if value.lower().endswith(".csv"):
        path = Path(value)
        if not path.exists():
            raise QgsProcessingException(f"CSV not found: {path}")
        mapsheets = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ms = next(iter(row.values()), "").strip()
                if ms:
                    mapsheets.append(normalize_to_albers(ms))
        return mapsheets
    elif value.lower().endswith(".txt"):
        path = Path(value)
        if not path.exists():
            raise QgsProcessingException("Mapsheets .txt file not found")
        text = path.read_text(encoding="utf-8")
        tiles = [t.strip() for t in text.split(",") if t.strip()]
        return [normalize_to_albers(t) for t in tiles]  
    else:
        return [
            normalize_to_albers(t.strip())
            for t in value.split(",")
            if t.strip()
        ]

class Setup(QgsProcessingAlgorithm):
    INPUT_CODE = 'INPUT_CODE'
    MASTER_WATERSHED_SHP = r"C:\Albers10k2m\basedata\FWA_WatershedGroups_246.shp"
    WATERSHED_DIR = r"C:\Albers10k2m\FWA_Watersheds"
    def initAlgorithm(self, config=None):
        os.makedirs(self.WATERSHED_DIR, exist_ok=True)
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
            raise QgsProcessingException(f"Invalid code length: '{file_base}'. Must be exactly 4 characters.")

        feedback.pushInfo(f"--- Searching for Watershed Code: {file_base} ---")
        output_shp_path = os.path.join(self.WATERSHED_DIR, f"{file_base}_v0.shp")
        v0_shp_path = os.path.join(self.WATERSHED_DIR, f"{file_base}_v0.shp")
        watershed_layer = None

        # 1. Try to extract from Master Watershed layer if it exists
        if os.path.exists(self.MASTER_WATERSHED_SHP):
            master_layer = QgsVectorLayer(self.MASTER_WATERSHED_SHP, "Master_Watersheds", "ogr")
            if master_layer.isValid():
                expression = f'"WTRSHDGRPC" = \'{file_base}\''
                extracted_watershed = processing.run(
                    "native:extractbyexpression",
                    {
                        "INPUT": master_layer,
                        "EXPRESSION": expression,
                        "OUTPUT": "memory:EXTRACTED_WATERSHED",
                    },
                    context=context,
                    # feedback=feedback,
                )["OUTPUT"]

                if extracted_watershed.featureCount() > 0:
                    feedback.pushInfo(f"Found match for {file_base} in FWA Master. Exporting shapefile...")

                    save_options = QgsVectorFileWriter.SaveVectorOptions()
                    save_options.driverName = "ESRI Shapefile"
                    save_options.fileEncoding = "UTF-8"

                    error = QgsVectorFileWriter.writeAsVectorFormatV3(
                        extracted_watershed,
                        output_shp_path,
                        master_layer.transformContext(),
                        save_options
                    )

                    if error[0] != QgsVectorFileWriter.NoError:
                        raise QgsProcessingException(f"Failed to save watershed shapefile to {output_shp_path}. Error code: {error[0]}")

                    feedback.pushInfo(f"Successfully created {output_shp_path}")
                    watershed_layer = QgsVectorLayer(output_shp_path, f"{file_base}", "ogr")

        # 2. Fallback check: Look for XXXX_v0.shp or existing SFM_XXXX.shp in WATERSHED_DIR
        if watershed_layer is None or not watershed_layer.isValid():
            if os.path.exists(v0_shp_path):
                feedback.pushInfo(f"Code '{file_base}' not found in FWA Master. Found local: {v0_shp_path}")
                watershed_layer = QgsVectorLayer(v0_shp_path, f"{file_base}", "ogr")
            elif os.path.exists(output_shp_path):
                feedback.pushInfo(f"Code '{file_base}' not found in FWA Master. Found local: {output_shp_path}")
                watershed_layer = QgsVectorLayer(output_shp_path, f"{file_base}", "ogr")

        # 3. Final validation check
        if watershed_layer is None or not watershed_layer.isValid():
            raise QgsProcessingException(
                f"Watershed code '{file_base}' was not found in FWA, "
                f"and not existing '{file_base}_v0.shp' found in: {self.WATERSHED_DIR}"
            )
        workspace_root = "C:/Albers10k2m/"
        project_workspace = os.path.join(workspace_root, f"SFM_{file_base}")
        global OUTPUT_ROOT
        OUTPUT_ROOT = Path(project_workspace)

        subdirs = [
            "0_Base", 
            "1_Main", 
            "2_Profile", 
            "3_Catchments", 
            "4_REM", 
            "5_Deliverables", 
            "9_Temp", 
            "_BEC", 
            "_HEX", 
            "_NEW", 
            "_OLD", 
            "_TRM"
        ]
        feedback.pushInfo(f"--- Starting Setup for {file_base} ---")
        
        # Create Workspace Directories
        os.makedirs(project_workspace, exist_ok=True)
        for sub in subdirs:
            os.makedirs(os.path.join(project_workspace, sub), exist_ok=True)
            
        base_dir = os.path.join(project_workspace, "0_Base")
        
        # Handle Meta File
        meta_filename = f"_{file_base}_Meta.csv"
        meta_file_path = os.path.join(base_dir, meta_filename)
        req_meta = os.path.join(workspace_root, "required", "_Meta.csv")
        
        if not os.path.exists(meta_file_path):
            if os.path.exists(req_meta):
                shutil.copyfile(req_meta, meta_file_path)
                feedback.pushInfo(f"Meta file created: {meta_file_path}")
            else:
                with open(meta_file_path, 'w') as f:
                    f.write("BUF 300\n")
                    
        # Read buffer distance from meta if available
        buffdist = 300
        if os.path.exists(meta_file_path):
            with open(meta_file_path, 'r') as f:
                for line in f:
                    if line.startswith("BUF"):
                        try:
                            buffdist = int(line.split()[1])
                        except IndexError:
                            pass
        # feedback.pushInfo(f"Buffer Distance configured to: {buffdist}m")
        
        # Setup Log File
        log_file_path = os.path.join(base_dir, f"_{file_base}_Log.csv")
        with open(log_file_path, 'w', newline='', encoding="utf-8") as log_file:
            writer = csv.writer(log_file)
            writer.writerow(["Log Details"])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([f"{timestamp}    SFM_pt01 Setup    {file_base} initialized"])
            
        feedback.pushInfo(f"Log file created: {log_file_path}")
        
        # Add layer to QGIS canvas project if not already present
        if watershed_layer not in QgsProject.instance().mapLayers().values():
            QgsProject.instance().addMapLayer(watershed_layer)
        
        # Calculate Bounding Box & 10km Grid MapList
        extent = watershed_layer.extent()
        minx, miny, maxx, maxy = extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()
        
        hull_width = maxx - minx
        hull_height = maxy - miny
        # feedback.pushInfo(f"Watershed Extent -> Width: {hull_width:.1f}m, Height: {hull_height:.1f}m")
        
        x_ul = int(minx // 10000) * 10000
        y_ul = int(maxy // 10000) * 10000 + 10000
        x_lr = int(maxx // 10000) * 10000
        y_lr = int(miny // 10000) * 10000 + 10000
        
        maplist_path = os.path.join(base_dir, f"_{file_base}_MapList.csv")
        
        maps_count = 0
        with open(maplist_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["Map"])
            
            y = y_ul
            while y >= y_lr:
                x = x_ul
                while x <= x_lr:
                    map_name = f"Map_{int(x/10000):03d}_{int(y/10000):03d}"
                    writer.writerow([map_name])
                    maps_count += 1
                    x += 10000
                y -= 10000
                
        feedback.pushInfo(f"{maps_count} mapsheets in {maplist_path}")
        
        # Execute Data Retrieval Logic Directly
        feedback.pushInfo("--- Starting to retrieve HEX csvs, TRIM data, & OLD LiDAR DEMs---")
        self.run_data_retrieval(file_base, maplist_path, log_file_path, feedback)
        
        # Start Part 1B (LiDAR BAT Creation) using the generated maplist
        feedback.pushInfo("--- Part 1B - Get New LiDAR BAT Creation ---")
        try:
            bat_creator = BatCreation()
            bat_creator.processAlgorithm(
                {
                    "MAPSHEETS": maplist_path
                },
                context,
                feedback
            )
            feedback.pushInfo("--- Part 1B - New LiDAR BAT Creation Completed ---")
        except Exception as e:
            feedback.reportError(f"Error during Part 1B: {e}")
            raise QgsProcessingException(f"Part 1B execution failed: {e}")
        
        feedback.pushInfo("--- Part 1 Completed Successfully ---")
        return {}

    def run_data_retrieval(self, project_code, csv_path, log_path, feedback):
        file_configs = [
            {
                "template": "HEX_{x}_{y}_0400_pnts.7z",
                "src": Path(r"V:\albers10k2m\_completed_hex"),
                "dest": Path(r"C:/Albers10k2m/SFN_{}/_HEX".format(project_code) if False else r"C:/Albers10k2m/SFM_{}/_HEX".format(project_code)),
                "type": ""
            },
            {
                "template": "TRM",
                "src": Path(r"V:\albers10k2m\_completed_trim"),
                "dest": Path(r"C:/Albers10k2m/SFM_{}/_TRM".format(project_code)),
                "type": ""
            },
            {
                "template": "dem_{x}_{y}.tif",
                "src": Path(r"V:\albers10k2m\_completed_dem"),
                "dest": Path(r"C:/Albers10k2m/SFM_{}/_OLD".format(project_code)),
                "type": "old "
            },
        ]

        def scan_folder(folder_path):
            if not os.path.exists(folder_path):
                return {}
            try:
                return {f.lower(): f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))}
            except Exception:
                return {}

        def write_log_message(message):
            feedback.pushInfo(message)
            file_exists = os.path.exists(log_path)
            try:
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                with open(log_path, mode="a", newline="", encoding="utf-8") as logfile:
                    writer = csv.writer(logfile)
                    if not file_exists:
                        writer.writerow(["Log Details"])
                    writer.writerow([message])
            except Exception as e:
                feedback.reportError(f"Critical Logging Error: {e}")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_log_message(f"{timestamp}    SFM_pt01 Move Maps: start")
        
        start_time = time.perf_counter()
        
        coordinates = set()
        coord_pattern = re.compile(r"(\d{3})_(\d{3})")
        
        try:
            with open(csv_path, mode="r", newline="", encoding="utf-8") as csvfile:
                reader = csv.reader(csvfile)
                for row in reader:
                    if not row:
                        continue
                    map_string = row[0].strip()
                    match = coord_pattern.search(map_string)
                    if match:
                        coordinates.add(match.groups())
        except Exception:
            write_log_message(f"error csv mapping file")
            return

        if not coordinates:
            write_log_message("no maplist found")
            return

        total_maptiles = len(coordinates)

        for config in file_configs:
            filename_template = config["template"]
            src_dir = config["src"]
            dest_dir = config["dest"]
            data_type = config["type"]

            os.makedirs(dest_dir, exist_ok=True)
            
            cached_files = scan_folder(src_dir)
            if not cached_files:
                continue

            is_trim = (filename_template == "TRM")
            found_count = 0

            for x_coord, y_coord in coordinates:
                matched_filenames = []

                if is_trim:
                    x_prefix = x_coord[:2]
                    y_first_digit = y_coord[0]
                    y_second_digit = y_coord[1]
                    y_third_digit = y_coord[2]
                    
                    if y_third_digit == '0':
                        y_prefix = y_first_digit + y_second_digit
                    else:
                        combined_two = y_first_digit + y_second_digit
                        y_prefix = str(int(combined_two) + 1).zfill(2)
                    
                    trim_re = re.compile(rf"^(trm|hil)_{x_prefix}\d_{y_prefix}\d.*\.tif$")
                    
                    matched_keys = [k for k in cached_files.keys() if trim_re.match(k)]
                    for key in matched_keys:
                        matched_filenames.append(cached_files[key])
                else:
                    target_filename = filename_template.format(x=x_coord, y=y_coord)
                    target_lower = target_filename.lower()
                    if target_lower in cached_files:
                        matched_filenames.append(cached_files[target_lower])

                if matched_filenames:
                    found_count += 1
                    for exact_name in matched_filenames:
                        source_file = os.path.join(src_dir, exact_name)
                        destination_file = os.path.join(dest_dir, exact_name)

                        try:
                            if exact_name.lower().endswith(".7z"):
                                with py7zr.SevenZipFile(source_file, mode='r') as archive:
                                    archive.extractall(path=dest_dir)
                            else:
                                shutil.copy2(source_file, destination_file)
                        except Exception:
                            pass
            
            loop_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data_label = data_type.strip() if data_type else filename_template[:4]
            if data_label.lower() == "old":
                write_log_message(f"{loop_timestamp}    SFM_pt01 Move Maps: OLD DEM COMPLETE found {found_count}")
            else:
                write_log_message(f"{loop_timestamp}    SFM_pt01 Move Maps: {data_type}{filename_template[:4]} COMPLETE")

        end_time = time.perf_counter()
        seconds = end_time - start_time
        minutes = seconds / 60
        end_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        write_log_message(f"{end_timestamp}    SFM_pt01 Move Maps: total time elapsed {seconds:.0f}s [{minutes:.1f} min]")

    def name(self):
        return 'albers_workspace_setup'

    def displayName(self):
        return 'Part 1 - Setup'

    def group(self, context=None):
        return 'SFM'

    def groupId(self, context=None):
        return 'sfm'

    def shortHelpString(self):
        return (
            "Takes input FWA watershed ID and sets up workspace root directory in Albers10k2m. Creates base files (logfile, metafile, mapsheets csv). "
            "Pulls existing data from gdwuts (Old LiDAR DEMS / TRIM DEM and Hillshades / HEX files) and creates Get_New_LiDAR.bat "
            "which once run finds existing new LiDAR and processes it to be used as DEMs, Hilldshades, and TRIs for each mapsheet."
        )    
    

    def createInstance(self):
        return Setup()


class BatCreation(QgsProcessingAlgorithm):
    """
    Writes to Get_New_LiDAR.bat

    For each mapsheet:
    1. Initialize GDAL/QGIS environment
    2. Merge and reproject LiDAR DEMs to the dominant UTM zone (gdalwarp)
    3. Clip the merged raster to the mapsheet area (gdal_translate)
    4. Convert the raster to BC Albers (EPSG:3005) at 2 m resolution (gdalwarp)
    5. Clip again to the exact 10 km Albers tile extent (gdal_translate)
    6. Run tiffconvert.py to create the final DEM format
    7. Delete temporary files
    8. Generate Hillshade from the DEM (gdaldem hillshade)
    9. Generate TRI (Terrain Ruggedness Index) (gdaldem TRI)
    """

    BAT_TEMPLATE = (
        "REM --- STEP 0: Initialize QGIS / GDAL Environment ---\n"
        "REM {mapsheet}\n\n"
        "REM --- STEP 1: Warp input rasters to UTM Zone ---\n"
        '"{gdalwarp}" ^\n'
        "  -t_srs EPSG:{utm_epsg} ^\n"
        "  -dstnodata -9999 ^\n"
        "  -overwrite ^\n"
        "{input_tiffs} ^\n"
        '  "{warp_tif}"\n\n'
        "REM --- STEP 2: Clip to bounding window ---\n"
        '"{gdal_translate}" ^\n'
        "  -of GTiff ^\n"
        '  -co "TILED=NO" ^\n'
        "  -a_nodata -9999 ^\n"
        "  -projwin {projwin} ^\n"
        '  "{warp_tif}" ^\n'
        '  "{final_tif}"\n\n'
        'if exist "{warp_tif}" del "{warp_tif}"\n\n'
        "REM --- STEP 3: Convert to BC Albers (EPSG:3005) at 2m resolution using averaging ---\n"
        '"{gdalwarp}" ^\n'
        "  -t_srs EPSG:3005 ^\n"
        "  -tr 2 2 ^\n"
        "  -r average ^\n"
        "  -dstnodata -9999 ^\n"
        "  -overwrite ^\n"
        '  "{final_tif}" ^\n'
        '  "{low_temp_tif}"\n\n'
        "REM --- STEP 4: Clip low_ raster to exact 10k x 10k Albers grid tile extent ---\n"
        '"{gdal_translate}" ^\n'
        "  -of GTiff ^\n"
        '  -co "TILED=NO" ^\n'
        "  -a_nodata -9999 ^\n"
        "  -projwin {albers_projwin} ^\n"
        '  "{low_temp_tif}" ^\n'
        '  "{low_tif}"\n\n'
        "REM --- STEP 5: Run custom TiffConvert Python script ---\n"
        'python "{tiff_convert_py}" "{low_tif}" "{new_tif}"\n\n'
        'if exist "{final_tif}" del "{final_tif}"\n\n'
        'if exist "{low_tif}" del "{low_tif}"\n\n'
        'if exist "{low_temp_tif}" del "{low_temp_tif}"\n\n'
        "REM --- STEP 6: Create hillshade from NEW geotiff with edge processing ---\n"
        '"{gdaldem}" hillshade ^\n'
        "  -compute_edges ^\n"
        '  "{new_tif}" ^\n'
        '  "{hil_tif}"\n\n'
        "REM --- STEP 7: Create TRI from NEW geotiff with edge processing ---\n"
        '"{gdaldem}" TRI ^\n'
        "  -compute_edges ^\n"
        '  "{new_tif}" ^\n'
        '  "{tri_tif}"\n\n'
        "echo COMPLETED {mapsheet}\n"
    )

    def name(self):
        return "part_1"

    def displayName(self):
        return "Part 1 - Setup"

    def group(self, context=None):
        return "SFM"

    def groupId(self, context=None):
        return "sfm"

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "MAPSHEETS",
                "Mapsheet CSV or TXT",
                extension="csv"
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        mapsheet_raw = parameters["MAPSHEETS"]
        feedback.pushInfo(f"Maplist: {mapsheet_raw}")

        input_path = Path(mapsheet_raw)
        if input_path.suffix.lower() == ".csv" and len(input_path.stem) >= 5:
            code_4 = input_path.stem[1:5].upper()
        else:
            code_4 = "XXXX"

        shared_output_root = Path(r"C:\Albers10k2m\SFM_{}\_NEW".format(code_4))
        shared_output_root.mkdir(parents=True, exist_ok=True)
        #feedback.pushInfo(f"Unified Output Root: {shared_output_root}")

        mapsheets = parse_mapsheets(mapsheet_raw)
        feedback.pushInfo(f"Mapsheets loaded: {len(mapsheets)}")

        raster_type = "DEM"
        index_csv = DEM_INDEX_CSV

        # feedback.pushInfo(f"Raster type: {raster_type}")
        
        master_bat_lines = [
            "@echo on",
            "setlocal",
        ]

        log_file_path = OUTPUT_ROOT / "0_Base" / f"_{code_4}_Log.csv"
        def write_part2_log(message):
            feedback.pushInfo(message)
            try:
                os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
                with open(log_file_path, mode="a", newline="", encoding="utf-8") as logfile:
                    writer = csv.writer(logfile)
                    writer.writerow([message])
            except Exception as e:
                feedback.reportError(f"Critical Logging Error: {e}")

        part2_start_time = time.perf_counter()
        timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_part2_log(f"{timestamp_start}    SFM_pt01 BatCreation: start")

        processed_count = 0
        for alb_mapsheet in mapsheets:
            tile_start_time = time.perf_counter()
            feedback.pushInfo(f"Processing tile: {alb_mapsheet}")
            
            alb_10k_tile = processing.run(
                "native:extractbyexpression",
                {
                    "INPUT": ALBERS_GRID_SHP,
                    "EXPRESSION": f'"{ALB_MS_ID_FIELD}" = \'{alb_mapsheet}\'',
                    "OUTPUT": "memory:ALB_10K_TILE",
                },
                context=context,
                #feedback=feedback,
            )["OUTPUT"]

            if alb_10k_tile.featureCount() != 1:
                raise QgsProcessingException(f"Expected 1 Albers tile for {alb_mapsheet}, got {alb_10k_tile.featureCount()}")

            tile_feat = next(alb_10k_tile.getFeatures())
            tile_geom = tile_feat.geometry()

            buffered_alb_tile = processing.run(
                "native:buffer",
                {
                    "INPUT": alb_10k_tile,
                    "DISTANCE": BUFFER_DISTANCE_METERS,
                    "DISSOLVE": True,
                    "OUTPUT": "memory:BUFFER",
                },
                context=context,
                #feedback=feedback,
            )["OUTPUT"]

            csv_uri = f"file:///{index_csv}?delimiter=,&xField=x2500&yField=y2500&crs=EPSG:3005"
            points = QgsVectorLayer(csv_uri, f"{raster_type}_points", "delimitedtext")
            if not points.isValid():
                raise QgsProcessingException(f"CSV failed to load: {index_csv}")
            if points.featureCount() == 0:
                raise QgsProcessingException("CSV produced no point features")

            selected = processing.run(
                "native:extractbylocation",
                {
                    "INPUT": points,
                    "PREDICATE": [0],
                    "INTERSECT": buffered_alb_tile,
                    "OUTPUT": "memory:SELECTED",
                },
                context=context,
                #feedback=feedback,
            )["OUTPUT"]
            
            selected_features = list(selected.getFeatures())
            if not selected_features:
                timestamp_tile = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                write_part2_log(f"{timestamp_tile}    SFM_pt01 BatCreation: No DEM index records found for {alb_mapsheet}, skipped")
                continue

            zone_counts = {}
            for f in selected_features:
                try:
                    zone = int(f["ALBzone"])
                    zone_counts[zone] = zone_counts.get(zone, 0) + 1
                except (TypeError, ValueError):
                    continue

            if not zone_counts:
                timestamp_tile = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                write_part2_log(
                    f"{timestamp_tile}    SFM_pt01 BatCreation: No valid ALBzone values found for {alb_mapsheet}, skipped"
                )

                continue
                
            utm_zone = max(zone_counts, key=zone_counts.get)
            utm_epsg = 32600 + utm_zone
            #feedback.pushInfo(f"Output EPSG: {utm_epsg}")

            tif_paths = []
            seen_tiles = set()
            for f in selected_features:
                tile_id = f["map"]
                if not tile_id:
                    continue
                tile_id_clean = str(tile_id).strip().lower()
                if tile_id_clean in seen_tiles:
                    continue
                seen_tiles.add(tile_id_clean)

                if len(tile_id_clean) >= 4:
                    major_group = tile_id_clean[:3]
                    letter_map = tile_id_clean[:4]
                    target_folder = UNC_ROOT / major_group / letter_map
                    search_pattern = f"*/{raster_type.lower()}/*{tile_id_clean}*.tif"
                    
                    matching_tifs = list(target_folder.glob(search_pattern))
                    if matching_tifs:
                        try:
                            newest_year = max(
                                int(p.parent.parent.name)
                                for p in matching_tifs
                                if p.parent.parent.name.isdigit()
                            )
                            newest_matches = [
                                p for p in matching_tifs
                                if p.parent.parent.name == str(newest_year)
                            ]
                            for match in newest_matches:
                                tif_paths.append(str(match))
                        except ValueError:
                            pass

            tif_paths = sorted(set(tif_paths))
            num_tiffs_found = len(tif_paths)
            if not tif_paths:
                continue

            CLIP_BUFFER = 800
            alb_rect_geom = QgsGeometry.fromRect(
                tile_geom.boundingBox().buffered(CLIP_BUFFER)
            )

            utm_crs = QgsCoordinateReferenceSystem(f"EPSG:{utm_epsg}")
            tx = QgsCoordinateTransform(
                QgsCoordinateReferenceSystem("EPSG:3005"),
                utm_crs,
                QgsProject.instance()
            )

            alb_rect_geom.transform(tx)
            utm_bbox = alb_rect_geom.boundingBox()

            projwin = (
                f"{utm_bbox.xMinimum():.1f} "
                f"{utm_bbox.yMaximum():.1f} "
                f"{utm_bbox.xMaximum():.1f} "
                f"{utm_bbox.yMinimum():.1f}"
            )
            
            tile_bat_text = self.write_bat_for_tile(
                alb_mapsheet,
                tif_paths,
                utm_epsg,
                projwin,
                shared_output_root,
                tile_geom,
            )
            master_bat_lines.append(tile_bat_text)
            master_bat_lines.append("")
            
            tile_elapsed = time.perf_counter() - tile_start_time
            processed_count += 1
            timestamp_tile = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            write_part2_log(f"{timestamp_tile}    SFM_pt01 BatCreation: {num_tiffs_found} tiffs found for {alb_mapsheet} and successfully written to bat ({tile_elapsed:.1f}s)")

        part2_elapsed = time.perf_counter() - part2_start_time
        timestamp_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_part2_log(f"{timestamp_end}    SFM_pt01 BatCreation: total time elapsed {part2_elapsed:.0f}s [{part2_elapsed/60:.1f} min]")

        master_bat_lines.extend([
            "",
            "echo.",
            "echo ALL MAPSHEETS COMPLETE",
            "pause"
        ])
        
        master_bat_path = OUTPUT_ROOT / "Get_New_LiDAR.bat"
        master_bat_path.write_text(
            "\n".join(master_bat_lines),
            encoding="utf-8"
        )
        feedback.pushInfo(f"Created Master BAT: {master_bat_path}")
        return {}

    def write_bat_for_tile(self, alb_mapsheet, tif_paths, utm_epsg, projwin, shared_output_root, tile_geom):
        temp_name = alb_mapsheet.replace("ALB_", "MAP_")
        raw_name = alb_mapsheet.replace("ALB_", "RAW_")
        low_temp_name = alb_mapsheet.replace("ALB_", "low_temp_")
        low_name = alb_mapsheet.replace("ALB_", "low_")
        new_name = alb_mapsheet.replace("ALB_", "NEW_")
        hil_name = alb_mapsheet.replace("ALB_", "HIL_")
        tri_name = alb_mapsheet.replace("ALB_", "TRI_")

        warp_tif = shared_output_root / f"{temp_name}.tif"
        final_tif = shared_output_root / f"{raw_name}.tif"
        low_temp_tif = shared_output_root / f"{low_temp_name}.tif"
        low_tif = shared_output_root / f"{low_name}.tif"
        new_tif = shared_output_root / f"{new_name}.tif"
        hil_tif = shared_output_root / f"{hil_name}.tif"
        tri_tif = shared_output_root / f"{tri_name}.tif"

        bbox = tile_geom.boundingBox()
        albers_projwin = (
            f"{bbox.xMinimum():.1f} "
            f"{bbox.yMaximum():.1f} "
            f"{bbox.xMaximum():.1f} "
            f"{bbox.yMinimum():.1f}"
        )

        input_tiffs = " ^\n".join(
            f'  "{p}"'
            for p in tif_paths
        )

        bat_text = self.BAT_TEMPLATE.format(
            mapsheet=alb_mapsheet,
            gdalwarp=os.path.join(qgis_bin_dir, "gdalwarp.exe"),
            gdal_translate=os.path.join(qgis_bin_dir, "gdal_translate.exe"),
            gdaldem=os.path.join(qgis_bin_dir, "gdaldem.exe"),
            utm_epsg=utm_epsg,
            input_tiffs=input_tiffs,
            warp_tif=warp_tif,
            projwin=projwin,
            final_tif=final_tif,
            low_temp_tif=low_temp_tif,
            albers_projwin=albers_projwin,
            low_tif=low_tif,
            tiff_convert_py=r"C:\Albers10k2m\_Executables\TiffConvert.py",
            new_tif=new_tif,
            hil_tif=hil_tif,
            tri_tif=tri_tif,
        )
        return bat_text

    def createInstance(self):
        return BatCreation()