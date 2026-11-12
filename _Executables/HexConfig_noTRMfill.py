from doctest import master
from pathlib import Path
import pandas as pd
import math
import rasterio
from rasterio.transform import rowcol
from rasterio.merge import merge
import numpy as np
import struct
import sys
from datetime import datetime, time

pd.set_option("display.float_format", "{:.4f}".format) # no e :o
NO_NEIGHBOUR = 99999999

def log(message):
    """write to logfile"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", newline="") as f:
        f.write(f"{timestamp},  {message}\n")

def merge_hexes(hex_files):
    """Merge all HEX CSVs into one master dataframe"""
    dfs = []
    next_global_id = 1
    for file in hex_files:
        df = pd.read_csv(file, usecols=["NodeID", "Map", "HExid", "X0", "Y0",], skipinitialspace=True,
            dtype={ "NodeID": np.int32,  "Map": str, "HExid": str, "X0": np.float64, "Y0": np.float64,})

        n = len(df)
        df = df.rename(columns={"NodeID": "local_id","Map": "map","HExid": "hex_id16","X0": "cx","Y0": "cy",})
        df["map"] = (df["map"] .str.replace("HEX_", "MAP_", regex=False))
        df.insert(0, "global_id", np.arange(next_global_id, next_global_id + n, dtype=np.int32))
        next_global_id += n
        dfs.append(df)

    master = pd.concat(dfs,  ignore_index=True)
    n = len(master)
    master["sx"] = np.zeros(n, dtype=np.float32)
    master["sy"] = np.zeros(n, dtype=np.float32)
    master["inside"] = np.zeros(n, dtype=np.int8)
    master["dem"] = np.zeros(n, dtype=np.int8)
    master["ave100"] = np.zeros(n, dtype=np.float32)
    master["DOM"] = np.zeros(n, dtype=np.int8)
    master["RC"] = np.zeros(n, dtype=np.int8)
    master["Strahler"] = np.zeros(n, dtype=np.int8)
    master["Acc"] = np.zeros(n, dtype=np.int32)
    master["edge"] = np.zeros(n, dtype=np.int8)
    master["done"] = np.zeros(n, dtype=np.int8)
    master["status"] = np.zeros(n, dtype=np.int8)

    return master


def get_hex_offsets():
    """SetN1N6 hex geometry."""
    rx3 = math.sqrt(3)
    radius_o = ((400 * 2) / (3 * rx3)) ** 0.5
    radius_i = radius_o * rx3 / 2
    yheight = radius_o / 2
    yheight2 = radius_o + yheight
    xwidth2 = radius_o * rx3

    return radius_i, yheight2, xwidth2


def set_n1_n6(master_df):
    radius_i, yheight2, xwidth2 = get_hex_offsets()
    offsets = {
        "n1": (radius_i, yheight2),
        "n2": (xwidth2, 0),
        "n3": (radius_i, -yheight2),
        "n4": (-radius_i, -yheight2),
        "n5": (-xwidth2, 0),
        "n6": (-radius_i, yheight2),
    }
    # Build coordinate lookup
    coord_lookup = {}
    for gid, cx, cy in zip(master_df["global_id"], master_df["cx"], master_df["cy"]):
        key = (round(cx, 1), round(cy, 1))
        coord_lookup[key] = gid
    TOL = 0.05
    for field, (dx, dy) in offsets.items():
        neighbours = []
        for cx, cy in zip(master_df["cx"], master_df["cy"]):
            target_x = cx + dx
            target_y = cy + dy
            found = NO_NEIGHBOUR

            #check nearby rounded candidates
            for xoff in (-0.1, 0, 0.1):
                for yoff in (-0.1, 0, 0.1):

                    key = (round(target_x + xoff, 1), round(target_y + yoff, 1))
                    gid = coord_lookup.get(key)

                    if gid is not None:
                        found = gid
                        break

                if found != NO_NEIGHBOUR:
                    break

            neighbours.append(found)
        master_df[field] = neighbours

    return master_df


def average_window(dem_array, rows, cols):
    n = len(rows)
    averages = np.full(n, np.nan, dtype=np.float32)
    touches_null = np.zeros(n, dtype=np.int8)
    nrows, ncols = dem_array.shape

    nodata_mask = (dem_array == -9999)
    valid_mask = ((dem_array > 0) & (dem_array != -9999))

    for i in range(n):
        r = rows[i]
        c = cols[i]
        r1 = max(0, r - 5)
        r2 = min(nrows, r + 5)
        c1 = max(0, c - 5)
        c2 = min(ncols, c + 5)

        if nodata_mask[r1:r2, c1:c2].any():
            touches_null[i] = 1

        valid = dem_array[r1:r2, c1:c2][valid_mask[r1:r2, c1:c2]]
        if valid.size:
            averages[i] = valid.mean()
    return averages, touches_null


def get_neighbour_dem_files(dem_folder, map_name):
    x = int(map_name[4:7])
    y = int(map_name[8:11])
    files = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tif = (dem_folder / f"NEW_{x+dx:03d}_{y+dy:03d}.tif")
            if tif.exists():
                files.append(tif)
    return files


def process_dem_tiles(master_df, dem_folder):
    maps = sorted(master_df["map"].unique())
    master_df["touches_null"] = 0

    for map_name in maps:
        dem_files = get_neighbour_dem_files(dem_folder, map_name)

        #if len(dem_files) == 0:
        #    print("No DEMs found")
        #    continue
        datasets = [rasterio.open(f) for f in dem_files]
        mosaic, transform = merge(datasets, nodata=-9999)
        dem_array = mosaic[0]

        for ds in datasets:
            ds.close()
        mask = (master_df["map"] == map_name)

        rows, cols = rowcol(transform, master_df.loc[mask, "cx"].to_numpy(), master_df.loc[mask, "cy"].to_numpy())

        rows = np.asarray(rows)
        cols = np.asarray(cols)

        averages, touches_null = (average_window(dem_array, rows, cols))

        master_df.loc[mask, "ave100"] = np.where(np.isnan(averages), -9999, averages)
        master_df.loc[mask, "dem"] = (master_df.loc[mask, "ave100"] == -9999).astype(np.int8)
        master_df.loc[mask, "touches_null"] = touches_null

    return master_df


def create_edge_null_outputs(master_df, output_folder, filebase):
    output_folder = Path(output_folder)
    master_df["edge"] = 0

    edges = master_df[(master_df["dem"] == 0) & (master_df["touches_null"] == 1)].copy()

    master_df.loc[edges.index, "edge"] = 1
    nulls = master_df[master_df["dem"] == 1].copy()
    for col in ["n1", "n2", "n3", "n4", "n5", "n6",]:
        edges[col] = edges[col].replace(NO_NEIGHBOUR, 0)
        nulls[col] = nulls[col].replace(NO_NEIGHBOUR, 0)

    edge_file = (output_folder / f"02_{filebase}_MasterEDGE.csv")
    null_file = (output_folder / f"02_{filebase}_MasterNULL.csv")

    def write_vb_csv(df, outfile):
        with open(outfile, "w") as f:
            f.write("Map, GlobalID, LocalID, DEM,Inside, HexID, cx, cy, n1, n2, n3, n4, n5, n6, ave100\n")
            for row in df.itertuples():
                f.write(
                    f"{row.map},"
                    f"{row.global_id:08d},"
                    f"{row.local_id:07d},"
                    f"{row.dem:02d},"
                    f"{row.inside:02d},"
                    f"{row.hex_id16},"
                    f"{row.cx:.4f},"
                    f"{row.cy:.4f},"
                    f"{row.n1:08d},"
                    f"{row.n2:08d},"
                    f"{row.n3:08d},"
                    f"{row.n4:08d},"
                    f"{row.n5:08d},"
                    f"{row.n6:08d},"
                    f"{row.ave100:.4f}\n"
                )

    write_vb_csv(edges, edge_file)
    write_vb_csv(nulls, null_file)


def write_masterhex_bin(master_df, output_file):
    fmt = "<11s hh ll 16s ddddd llllll hhh l hhh"
    rec_len = struct.calcsize(fmt)

    with open(output_file, "wb") as f:
        for row in master_df.itertuples(index=False):
            record = struct.pack(
                fmt,

                str(row.map)
                .ljust(11)[:11]
                .encode("ascii"),

                np.int16(row.inside),
                np.int16(row.dem),

                np.int32(row.global_id),
                np.int32(row.local_id),

                str(row.hex_id16)
                .ljust(16)[:16]
                .encode("ascii"),

                np.float64(row.cx),
                np.float64(row.cy),
                np.float64(row.sx),
                np.float64(row.sy),
                np.float64(row.ave100),

                np.int32(row.n1),
                np.int32(row.n2),
                np.int32(row.n3),
                np.int32(row.n4),
                np.int32(row.n5),
                np.int32(row.n6),

                np.int16(row.DOM),
                np.int16(row.RC),
                np.int16(row.Strahler),

                np.int32(row.Acc),

                np.int16(row.edge),
                np.int16(row.done),
                np.int16(row.status),
            )
            f.write(record)
    expected_size = len(master_df) * rec_len
    actual_size = Path(output_file).stat().st_size


def create_maplist_full(master_df, output_folder, filebase):
    maps = sorted(master_df["map"].unique())
    rows = []

    for mapfile in maps:
        x = int(mapfile[4:7]) * 10000
        y = int(mapfile[8:11]) * 10000
        rows.append(
            [mapfile, x, y, 1, 1, 0, 0,])

    df = pd.DataFrame(rows, columns=[ "Mapfile", "x", "y", "DEM", "inside", "Low", "High",])

    outfile = (output_folder / f"_{filebase}_MapList_FULL.csv")

    df.to_csv(outfile, index=False)


def main():
    watershed = (sys.argv[1].upper().strip())

    root_folder = Path(fr"C:\Albers10k2m\SFM_{watershed}")
    base_folder = (root_folder / "0_Base")
    hex_folder = (root_folder / "_HEX")
    dem_folder = (root_folder / "_NEW")

    global LOG_FILE
    LOG_FILE = (base_folder /f"_{watershed}_Log.csv")
    start_time = time.perf_counter()
    log("SFM_pt02 HexConfig start")

    hex_files = sorted(hex_folder.glob("HEX_*_0400_pnts.csv"))

    #print("mergehex")
    log("SFM_pt02 MergeHex: start")
    master = merge_hexes(hex_files)
    log("SFM_pt02 MergeHex: finished " f"({len(master):,} hexes)")
    #print(f"merged {len(master):,} hexes")

    create_maplist_full(master, base_folder, watershed)

    log("SFM_pt02 setn1n6: start")
    # print("\nsetn1n6")
    master = set_n1_n6(master)
    log("SFM_pt02 setn1n6: finished")

    log("SFM_pt02 process dems: start")
    #print("\nprocessdems")
    master = process_dem_tiles(master, dem_folder)
    log("SFM_pt02 process dems: finished")

    # print("\ncreating edge and nulls")
    log("SFM_pt02 edge/nulls: start")
    create_edge_null_outputs(master, base_folder, watershed)

    for col in ["global_id", "local_id", "n1", "n2", "n3", "n4", "n5", "n6", "Acc", "inside", "dem", "DOM", "RC", "Strahler", "edge", "done", "status",]:
        master[col] = (master[col].fillna(0).astype(int))
    log("SFM_pt02 edge/nulls: finished")

    bin_file = (base_folder / f"00_{watershed}_MasterHex.bin")
    log(f"SFM_pt02 writing to {bin_file}")
    #print("\nwriting masterhex")
    write_masterhex_bin(master, bin_file)
    end_time = time.perf_counter()
    elapsed_time = end_time - start_time
    mins = int(elapsed_time // 60)
    log("SFM_pt02 HexConfig finished")
    log("Exit ... time = {:d} mins, {:.2f} seconds".format(mins, elapsed_time % 60))

if __name__ == "__main__":
    main()