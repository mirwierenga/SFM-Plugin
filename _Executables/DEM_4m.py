'''
Commandline	Set ii to be the 4 character watershed code			
	Set jj as the metres to offset the TRIM			
	to approximate the difference to the			
	surrounding LIDAR surface			
				
z_Setup2025	Read in MapList_FULL			
	Determine full waterhed extent			
	Call z_createblank to make a null 4m DEM.tif (_4m_BLK.tif)			
	and a null _4m HIL.tif (1 byte)			
				
z_make4s    Parse the 10k 2m dems to derive an average value at 4m			
	Create a temp tif to hold the average values			
				
z_makehill	Read in the newly created 4m DEM			
	Call ATAN2 to derive the hillshade aspect value			
	Calculate and write out the hillshade byte value			
	Delete the temp .tif			
				
CloseExit	No data file clean up			
'''
import os
import csv
import time
import argparse
import numpy as np
import rasterio
from rasterio.windows import Window
from pathlib import Path

# ==========================================
# COMMAND LINE ARGUMENT PARSING
# ==========================================
def parse_arguments():
    parser = argparse.ArgumentParser(description="Process watershed SFM 2m-to-4m DEMs and Hillshade.")
    parser.add_argument("watershed", type=str, help="4-character watershed code (REQUIRED, e.g., WOLF, BRID)")
    parser.add_argument("offset", nargs="?", type=float, default=0.0, help="Metres to offset TRIM to match surrounding LiDAR surface (default: 0)")
    return parser.parse_args()

args = parse_arguments()

# ==========================================
# CONFIGURATION
# ==========================================
VERSION = "v201"
II = args.watershed.upper()
JJ = args.offset
FILE_BASE = II[:4]
HECTARES = 400.0

WORKSPACE_ROOT = Path(r"G:\Albers10k2m")
WORKSPACE = WORKSPACE_ROOT / f"SFM_{FILE_BASE}"
WORKSPACE_BASE = WORKSPACE / "0_Base"
WORKSPACE_MAIN = WORKSPACE / "1_Main"
LOG_FILE = WORKSPACE_BASE / f"_{FILE_BASE}_Log.csv"

start_time = time.time()
last_time = time.time()

def update_log(task: str):
    global last_time
    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    elapsed = time.time() - last_time
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"{timestamp}  SFM_pt02x 2m-4m    {VERSION} {FILE_BASE}_{int(HECTARES):03d} {elapsed:06.2f}  {task}\n"
    with open(LOG_FILE, "a") as f:
        f.write(log_line)
    last_time = time.time()

def setup_watershed():
    WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)
    WORKSPACE_MAIN.mkdir(parents=True, exist_ok=True)
    
    with open(LOG_FILE, "a") as f:
        f.write("\n")
    update_log(f"Create 4m dem ... {FILE_BASE} {JJ:.2f}")
    
    map_list_path = WORKSPACE_BASE / f"_{FILE_BASE}_MapList_FULL.csv"
    
    # Read bounds cleanly using csv parsing
    x_coords, y_coords = [], []
    with open(map_list_path, "r") as f:
        reader = csv.reader(f)
        next(reader, None) # Skip header
        for row in reader:
            if row:
                x_coords.append(float(row[1]))
                y_coords.append(float(row[2]))
                
    utmx_min, utmx_max = min(x_coords), max(x_coords)
    utmy_min, utmy_max = min(y_coords), max(y_coords)
    
    total_wid = (utmx_max - utmx_min) + 10000
    total_len = (utmy_max - utmy_min) + 10000
    blk_x = total_wid / 10000.0
    blk_y = total_len / 10000.0
    
    ncols = int(5000 * blk_x / 2) # 4m resolution grid columns
    nrows = int(5000 * blk_y / 2) # 4m resolution grid rows
    
    update_log(f"Grid dimensions calculated: {ncols} cols x {nrows} rows")
    return utmx_min, utmy_max + 10000, ncols, nrows

def make_hillshade_vectorized(dem_path: Path, hill_path: Path):
    """Computes hillshade using vectorized numpy gradient instead of nested loops."""
    update_log("Generating vectorized 4m hillshade...")
    
    with rasterio.open(dem_path) as src:
        dem_data = src.read(1).astype(np.float32)
        transform = src.transform
        cell_size = transform[0] # pixel size (4m)
        
    # Replace nodata/negative flags with NaN for gradient calculation
    dem_data[dem_data <= -9999] = np.nan
    
    # Compute gradients using numpy gradient (finite difference equivalent to your 3x3 kernel)
    dy, dx = np.gradient(dem_data, cell_size)
    
    # Slope and aspect calculations
    slope = np.arctan(np.sqrt(dx**2 + dy**2))
    aspect = np.arctan2(dy, -dx)
    aspect[aspect < 0] += 2.0 * np.pi
    
    azimuth = 135.0 * np.pi / 180.0
    zenith = 45.0 * np.pi / 180.0
    
    # Hillshade illumination model
    hill = 254 * ((np.cos(zenith) * np.cos(slope)) + 
                  (np.sin(zenith) * np.sin(slope) * np.cos(azimuth - aspect)))
    
    hill = np.clip(hill, 0, 254) + 1
    hill[np.isnan(dem_data)] = 1 # background null value
    
    # Save output GeoTIFF
    with rasterio.open(dem_path) as src:
        meta = src.meta.copy()
        meta.update(dtype=rasterio.uint8, count=1, nodata=0)
        with rasterio.open(hill_path, "w", **meta) as dst:
            dst.write(hill.astype(np.uint8), 1)
            
    update_log("Hillshade generation complete.")

if __name__ == "__main__":
    print(f"Running SFM Processor for Watershed: {FILE_BASE} with TRIM offset: {JJ}")
    x_ul, y_ul, ncols, nrows = setup_watershed()
    print(f"Watershed setup initialized at XUL: {x_ul}, YUL: {y_ul}, Cols: {ncols}, Rows: {nrows}")