"""
SFM TOOL - ADD DSM

adds dsm processing to an existing sfm watershed workspace, outputs a get_new_dsm.bat file in the 
sfm_<code> workspace ready to process the available dsm data into 2 m bc albers dsm hillshade and tri products

workflow:
1. validate the 4-character watershed id
2. confirm the sfm_<code> workspace and mapsheet list exist
3. create the _dsm output folder
4. load the project mapsheet list
5. identify the matching dsm index records for each mapsheet
6. determine the appropriate utm zone and locate the source dsm tiffs
7. generate the required processing commands for each mapsheet
8. reproject source dsm data to utm
9. clip the data to the required buffered extent
10. convert the dsm to bc albers at 2 m resolution
11. clip the result to the exact 10 km albers tile
12. run tiffconvert to create the final dsm
13. generate hillshade and tri products from the new dsm
14. write all processing commands to get_new_dsm.bat
15. log processing progress file counts skipped mapsheets and elapsed time

"""

from qgis.core import (QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterString, QgsVectorLayer, QgsProject, QgsProcessingException, QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsGeometry,)
import os
import csv
import time
from datetime import datetime
from pathlib import Path
import processing

ALBERS_GRID_SHP = r"C:\Albers10k2m\LiDAR_Prep\_ALBERS10k.shp"
DSM_INDEX_CSV = r"C:\Albers10k2m\LiDAR_Prep\Map2500_DSM.csv"
BUFFER_DISTANCE_METERS = 1200
ALB_MS_ID_FIELD = "HEX_ID16"
UNC_ROOT = Path(r"V:\\")

def normalize_to_albers(mapsheet: str) -> str:
    """Makes sure mapsheet naming uses ALB prefix"""
    if not mapsheet:
        return ""
    mapsheet = mapsheet.strip().upper()
    if mapsheet.startswith("MAP_"):
        return mapsheet.replace("MAP_", "ALB_", 1)
    return mapsheet

def parse_mapsheets(value: str) -> list:
    """Reads target mapsheet names from csv file path or comma separated txt file path"""
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
        return [normalize_to_albers(t.strip()) for t in value.split(",") if t.strip()]

class AddDSM(QgsProcessingAlgorithm):
    INPUT_CODE = 'INPUT_CODE'

    DSM_BAT_TEMPLATE = (
        "REM {mapsheet}\n\n"
        '"{gdalwarp}" ^\n'
        "  -t_srs EPSG:{utm_epsg} ^\n"
        "  -dstnodata -9999 ^\n"
        "  -overwrite ^\n"
        "{input_tiffs} ^\n"
        '  "{warp_tif}"\n\n'
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
        "REM --- STEP 5: Run custom TiffConvert executable ---\n"
        '"{tiff_convert_exe}" "{low_tif}" "{new_tif}"\n\n'
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

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterString(self.INPUT_CODE, 'Enter Watershed ID (4-letter code)', defaultValue=''))

    def processAlgorithm(self, parameters, context, feedback):
        raw_code = self.parameterAsString(parameters, self.INPUT_CODE, context)
        code_4 = raw_code.strip().upper()

        if len(code_4) != 4:
            raise QgsProcessingException(f"Invalid code length: '{code_4}'. Must be exactly 4 characters.")

        workspace_root = Path(r"C:\Albers10k2m")
        project_workspace = workspace_root / f"SFM_{code_4}"

        if not project_workspace.exists():
            raise QgsProcessingException(f"Workspace 'SFM_{code_4}' does not exist at {workspace_root}. Please run Part 1 Setup first.")

        shared_output_root = project_workspace / "_DSM"
        shared_output_root.mkdir(parents=True, exist_ok=True)
        feedback.pushInfo(f"DSM Output Root: {shared_output_root}")

        maplist_path = project_workspace / "0_Base" / f"_{code_4}_MapList.csv"
        if not maplist_path.exists():
            raise QgsProcessingException(f"MapList CSV not found at: {maplist_path}. Ensure Part 1 was executed successfully.")

        mapsheets = parse_mapsheets(str(maplist_path))
        total_mapsheets = len(mapsheets)
        feedback.pushInfo(f"Mapsheets loaded: {total_mapsheets}")
        master_bat_lines = ["@echo on", "setlocal",]

        log_file_path = project_workspace / "0_Base" / f"_{code_4}_Log.csv"
        def write_dsm_log(message):
            feedback.pushInfo(message)
            try:
                with open(log_file_path, mode="a", newline="", encoding="utf-8") as logfile:
                    writer = csv.writer(logfile)
                    writer.writerow([message])
            except Exception as e:
                feedback.reportError(f"Critical Logging Error: {e}")

        dsm_start_time = time.perf_counter()
        timestamp_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_dsm_log(f"{timestamp_start}    Add DSM: start")

        processed_count = 0
        for idx, alb_mapsheet in enumerate(mapsheets, start=1):
            tile_start_time = time.perf_counter()
            feedback.pushInfo(f"Processing {idx}/{total_mapsheets}: {alb_mapsheet}")
            
            alb_10k_tile = processing.run("native:extractbyexpression", {"INPUT": ALBERS_GRID_SHP, "EXPRESSION": f'"{ALB_MS_ID_FIELD}" = \'{alb_mapsheet}\'', "OUTPUT": "memory:ALB_10K_TILE",}, context=context,)["OUTPUT"]
            if alb_10k_tile.featureCount() != 1:
                feedback.pushInfo(f"No DSM found for({alb_mapsheet})")
                continue

            tile_feat = next(alb_10k_tile.getFeatures())
            tile_geom = tile_feat.geometry()

            buffered_alb_tile = processing.run("native:buffer", {"INPUT": alb_10k_tile, "DISTANCE": BUFFER_DISTANCE_METERS, "DISSOLVE": True, "OUTPUT": "memory:BUFFER",}, context=context,)["OUTPUT"]

            csv_uri = f"file:///{DSM_INDEX_CSV}?delimiter=,&xField=x2500&yField=y2500&crs=EPSG:3005"
            points = QgsVectorLayer(csv_uri, "DSM_points", "delimitedtext")
            if not points.isValid():
                feedback.pushInfo(f"No DSM found for({alb_mapsheet})")
                continue
            if points.featureCount() == 0:
                feedback.pushInfo(f"No DSM found for({alb_mapsheet})")
                continue

            selected = processing.run("native:extractbylocation", {"INPUT": points, "PREDICATE": [0], "INTERSECT": buffered_alb_tile, "OUTPUT": "memory:SELECTED",}, context=context,)["OUTPUT"]          
            selected_features = list(selected.getFeatures())
            zone_counts = {}
            for f in selected_features:
                try:
                    zone = int(f["ALBzone"])
                    zone_counts[zone] = zone_counts.get(zone, 0) + 1
                except (TypeError, ValueError):
                    continue

            if not zone_counts:
                feedback.pushInfo(f"No DSM found for({alb_mapsheet})")
                continue
                
            utm_zone = max(zone_counts, key=zone_counts.get)
            utm_epsg = 32600 + utm_zone
            tif_paths = []
            seen_tifs = set()

            for f in selected_features:
                rel_path = f["filepath"]
                if not rel_path:
                    continue
                
                clean_rel_path = str(rel_path).strip().replace("/", "\\")
                full_tif_path = UNC_ROOT / clean_rel_path
                
                path_str = str(full_tif_path)
                if path_str in seen_tifs:
                    continue
                
                if full_tif_path.exists():
                    seen_tifs.add(path_str)
                    tif_paths.append(path_str)

            tif_paths = sorted(set(tif_paths))
            num_tiffs_found = len(tif_paths)
            if not tif_paths:
                feedback.pushInfo(f"No DSM found for({alb_mapsheet})")
                continue

            CLIP_BUFFER = 800
            alb_rect_geom = QgsGeometry.fromRect(tile_geom.boundingBox().buffered(CLIP_BUFFER))

            utm_crs = QgsCoordinateReferenceSystem(f"EPSG:{utm_epsg}")
            tx = QgsCoordinateTransform( QgsCoordinateReferenceSystem("EPSG:3005"), utm_crs, QgsProject.instance())

            alb_rect_geom.transform(tx)
            utm_bbox = alb_rect_geom.boundingBox()

            projwin = ( f"{utm_bbox.xMinimum():.1f} " f"{utm_bbox.yMaximum():.1f} " f"{utm_bbox.xMaximum():.1f} " f"{utm_bbox.yMinimum():.1f}")
            
            map_mapsheet = alb_mapsheet.replace("ALB_", "MAP_")
            temp_name = map_mapsheet.replace("MAP_", "MAP_DSM_")
            raw_name = map_mapsheet.replace("MAP_", "RAW_DSM_")
            low_temp_name = map_mapsheet.replace("MAP_", "low_temp_dsm_")
            low_name = map_mapsheet.replace("MAP_", "low_dsm_")
            new_name = map_mapsheet.replace("MAP_", "DSM_")
            hil_name = map_mapsheet.replace("MAP_", "HIL_DSM_")
            tri_name = map_mapsheet.replace("MAP_", "TRI_DSM_")

            warp_tif = shared_output_root / f"{temp_name}.tif"
            final_tif = shared_output_root / f"{raw_name}.tif"
            low_temp_tif = shared_output_root / f"{low_temp_name}.tif"
            low_tif = shared_output_root / f"{low_name}.tif"
            new_tif = shared_output_root / f"{new_name}.tif"
            hil_tif = shared_output_root / f"{hil_name}.tif"
            tri_tif = shared_output_root / f"{tri_name}.tif"

            bbox = tile_geom.boundingBox()
            albers_projwin = (f"{bbox.xMinimum():.1f} " f"{bbox.yMaximum():.1f} " f"{bbox.xMaximum():.1f} " f"{bbox.yMinimum():.1f}")
            input_tiffs = " ^\n".join(f'  "{p}"' for p in tif_paths)

            osgeo_root = os.environ.get("OSGEO4W_ROOT", r"C:\OSGeo4W64")
            if not Path(osgeo_root).exists():
                raise QgsProcessingException(f"OSGEO4W_ROOT not found at '{osgeo_root}'. Please set the OSGEO4W_ROOT environment variable or install OSGeo4W in the default location.")
            gdal_bin = Path(osgeo_root) / "bin"
            tiff_convert_exe = r"C:\Albers10k2m\_Executables\TiffConvert.exe"

            tile_bat_text = self.DSM_BAT_TEMPLATE.format(
                mapsheet=raw_name,
                utm_epsg=utm_epsg,
                gdalwarp=str(gdal_bin / "gdalwarp.exe"),
                gdal_translate=str(gdal_bin / "gdal_translate.exe"),
                gdaldem=str(gdal_bin / "gdaldem.exe"),
                input_tiffs=input_tiffs,
                warp_tif=str(warp_tif),
                final_tif=str(final_tif),
                low_temp_tif=str(low_temp_tif),
                low_tif=str(low_tif),
                new_tif=str(new_tif),
                hil_tif=str(hil_tif),
                tri_tif=str(tri_tif),
                projwin=projwin,
                albers_projwin=albers_projwin,
                tiff_convert_exe=tiff_convert_exe,
            )

            master_bat_lines.append(tile_bat_text)
            master_bat_lines.append("")
            
            tile_elapsed = time.perf_counter() - tile_start_time
            processed_count += 1
            timestamp_tile = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            write_dsm_log(f"{timestamp_tile}    Add DSM: [{idx}/{total_mapsheets}] {num_tiffs_found} DSM tiffs found for {alb_mapsheet} and successfully written to bat (took {tile_elapsed:.1f}s)")

        dsm_elapsed = time.perf_counter() - dsm_start_time
        timestamp_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_dsm_log(f"{timestamp_end}    Add DSM: total time elapsed {dsm_elapsed:.0f}s [{int(dsm_elapsed/60):.1f} min]")

        master_bat_lines.extend(["", "echo.", "echo ALL DSM MAPSHEETS COMPLETE", "pause"])
        master_bat_path = project_workspace / "Get_New_DSM.bat"
        master_bat_path.write_text("\n".join(master_bat_lines), encoding="utf-8")
        feedback.pushInfo(f"Created Master BAT: {master_bat_path}")
        return {}

    def name(self):
        return 'add_dsm'

    def displayName(self):
        return 'Add DSM'

    def group(self, context=None):
        return 'SFM Tools'

    def groupId(self, context=None):
        return 'sfmtools'

    def createInstance(self):
        return AddDSM()