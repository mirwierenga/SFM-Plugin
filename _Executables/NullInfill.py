from pathlib import Path
from collections import deque
import struct

import sys
import numpy as np
import pandas as pd
import rasterio

from collections import deque
from time import perf_counter

# ============================================================================
# CONFIG
# ============================================================================

if len(sys.argv) > 1:

    WATERSHED = sys.argv[1].upper()

else:

    WATERSHED = "WOLF"

ROOT = Path(fr"C:\Albers10k2m\SFM_{WATERSHED}")
BASE = ROOT / "0_Base"
TRM_FOLDER = ROOT / "_TRM"
OLD_FOLDER = ROOT / "_OLD"

TRIM_FILE = TRM_FOLDER / f"{WATERSHED}_TRIM.tif"

MASTERHEX_FMT = "<11s hh ll 16s ddddd llllll hhh l hhh"

pd.set_option("display.float_format", "{:.4f}".format)

def build_working_lookup(working):

    gid_to_idx = (
        working.reset_index()
        .set_index("GlobalID")["index"]
        .to_dict()
    )

    nbr_lookup = {}

    for row in working.itertuples():

        nbr_lookup[row.GlobalID] = (
            row.n1,
            row.n2,
            row.n3,
            row.n4,
            row.n5,
            row.n6
        )

    return gid_to_idx, nbr_lookup

def load_masterhex_bin():

    infile = (
        BASE /
        f"00_{WATERSHED}_MasterHEX.bin"
    )

    fmt = "<11s hh ll 16s ddddd llllll hhh l hhh"

    rec_len = struct.calcsize(fmt)

    rows = []

    with open(infile, "rb") as f:

        while True:

            chunk = f.read(rec_len)

            if len(chunk) != rec_len:
                break

            rec = struct.unpack(
                fmt,
                chunk
            )

            rows.append({

                "Map":
                    rec[0]
                    .decode("ascii")
                    .strip(),

                "Inside":
                    int(rec[1]),

                "DEM":
                    int(rec[2]),

                "GlobalID":
                    int(rec[3]),

                "LocalID":
                    int(rec[4]),

                "HexID":
                    rec[5]
                    .decode("ascii")
                    .strip(),

                "cx":
                    float(rec[6]),

                "cy":
                    float(rec[7]),

                "sx":
                    float(rec[8]),

                "sy":
                    float(rec[9]),

                "ave100":
                    float(rec[10]),

                "n1":
                    int(rec[11]),

                "n2":
                    int(rec[12]),

                "n3":
                    int(rec[13]),

                "n4":
                    int(rec[14]),

                "n5":
                    int(rec[15]),

                "n6":
                    int(rec[16]),

                "DOM":
                    int(rec[17]),

                "RC":
                    int(rec[18]),

                "Strahler":
                    int(rec[19]),

                "Acc":
                    int(rec[20]),

                "edge":
                    int(rec[21]),

                "done":
                    int(rec[22]),

                "status":
                    int(rec[23]),
            })

    master = pd.DataFrame(rows)

    print(
        f"Loaded {len(master):,} master hexes"
    )

    print("\nFirst record:")

    print(
        master[
            [
                "GlobalID",
                "Map",
                "cx",
                "cy",
                "ave100"
            ]
        ].head(1)
    )

    return master

# ============================================================================
# NULL / EDGE CELLS
# ============================================================================

def load_working_cells():

    nulls = pd.read_csv(
        BASE / f"02_{WATERSHED}_MasterNULL.csv",
        skipinitialspace=True
    )

    edges = pd.read_csv(
        BASE / f"02_{WATERSHED}_MasterEDGE.csv",
        skipinitialspace=True
    )

    nulls.columns = nulls.columns.str.strip()
    edges.columns = edges.columns.str.strip()

    #
    # Convert common numeric fields
    #

    numeric_cols = [
        "GlobalID",
        "LocalID",
        "DEM",
        "Inside",
        "cx",
        "cy",
        "n1",
        "n2",
        "n3",
        "n4",
        "n5",
        "n6",
        "ave100",
    ]

    for col in numeric_cols:

        if col in nulls.columns:
            nulls[col] = pd.to_numeric(
                nulls[col],
                errors="coerce"
            )

        if col in edges.columns:
            edges[col] = pd.to_numeric(
                edges[col],
                errors="coerce"
            )

    #
    # IMPORTANT
    # Match VB logic exactly
    #
    # NULL file -> Type 0
    # EDGE file -> Type 1
    #

    nulls["fill_type"] = 0
    edges["fill_type"] = 1

    working = pd.concat(
        [nulls, edges],
        ignore_index=True
    )

    print()
    print(f"NULL cells : {len(nulls):,}")
    print(f"EDGE cells : {len(edges):,}")
    print(f"TOTAL      : {len(working):,}")
    print()

    print(
        working["fill_type"]
        .value_counts()
        .sort_index()
    )

    return working

# ============================================================================
# META
# ============================================================================

def read_meta():

    meta_file = (
        BASE /
        f"_{WATERSHED}_Meta.csv"
    )

    lidar_ids = []
    trim_ids = []

    with open(meta_file) as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            print("META:", line)

            if line.startswith("LDR"):

                lidar_ids.append(
                    int(line[4:])
                )

            elif line.startswith("TRM"):

                trim_ids.append(
                    int(line[4:])
                )

    print()
    print("LDR seeds:", lidar_ids)
    print("TRM seeds:", trim_ids)
    print()

    return lidar_ids, trim_ids


# ============================================================================
# LAKE FILL
# VB Infill_9999
# ============================================================================

def fill_lakes(working):

    lookup = (
        working
        .set_index("GlobalID")
        .to_dict("index")
    )

    ring = 1

    while True:

        changes = 0

        for idx, row in working.iterrows():

            #
            # Only process NULL cells
            #

            if row["fill_type"] != 0:
                continue

            vals = []

            for nbr_id in (
                row.n1,
                row.n2,
                row.n3,
                row.n4,
                row.n5,
                row.n6
            ):

                nbr = lookup.get(nbr_id)

                if nbr is None:
                    continue

                #
                # VB-style ring expansion
                #

                if nbr["fill_type"] >= ring:

                    z = nbr["ave100"]

                    if z > 0:

                        vals.append(z)

            #
            # Need at least 2 valid neighbours
            #

            if len(vals) >= 2:

                new_z = float(
                    np.mean(vals)
                )

                working.at[
                    idx,
                    "ave100"
                ] = new_z

                working.at[
                    idx,
                    "fill_type"
                ] = ring + 1

                lookup[row.GlobalID]["ave100"] = new_z

                lookup[row.GlobalID]["fill_type"] = (
                    ring + 1
                )

                changes += 1

        remaining = (
            working["ave100"] < 0
        ).sum()

        print(
            f"Ring {ring}: "
            f"filled={changes:,} "
            f"remaining={remaining:,}"
        )

        if changes == 0:
            break

        ring += 1

    print()

    print(
        f"Maximum ring used: "
        f"{ring - 1}"
    )

    print(
        f"Nulls remaining after lake fill: "
        f"{(working['ave100'] < 0).sum():,}"
    )

    print(
        f"Positive cells after lake fill: "
        f"{(working['ave100'] > 0).sum():,}"
    )

    print()

    return working

# ============================================================================
# REGION GROW
# VB Find_DEM_Cells / FindAdjacent
# ============================================================================

def grow_region(start_id, working):

    lookup = (
        working[
            [
                "GlobalID",
                "n1",
                "n2",
                "n3",
                "n4",
                "n5",
                "n6"
            ]
        ]
        .set_index("GlobalID")
        .to_dict("index")
    )

    region = set()

    queue = deque([start_id])

    while queue:

        gid = queue.popleft()

        if gid in region:
            continue

        row = lookup.get(gid)

        if row is None:
            continue

        region.add(gid)

        for nbr in (
            row["n1"],
            row["n2"],
            row["n3"],
            row["n4"],
            row["n5"],
            row["n6"]
        ):

            if nbr in lookup:

                queue.append(nbr)

    return region


# ============================================================================
# SAMPLE DEM
# ============================================================================

def sample_average_100(ds, band, x, y):

    vals = []

    for dx in range(-9, 10, 2):
        for dy in range(-9, 10, 2):

            try:

                row, col = ds.index(
                    x + dx,
                    y + dy
                )

                z = band[row, col]

                if z > 0:
                    vals.append(z)

            except:
                pass

    if not vals:
        return -9999

    return float(np.mean(vals))


# ============================================================================
# TRIM FILL
# ============================================================================

def apply_trim_fill(
        working,
        region,
        gid_to_idx
):

    if not TRIM_FILE.exists():

        print()
        print(
            f"TRIM file missing:"
        )
        print(
            f"{TRIM_FILE}"
        )
        print(
            "Skipping TRIM fill."
        )
        print()

        return

    with rasterio.open(TRIM_FILE) as ds:

        print(
            "TRIM Resolution:",
            ds.res
        )

        band = ds.read(1)

        count = 0

        region_df = working[
            working["GlobalID"].isin(region)
        ]

        for row in region_df.itertuples():

            z = sample_average_100(
                ds,
                band,
                row.cx,
                row.cy
            )

            working.at[
                row.Index,
                "ave100"
            ] = z

            count += 1

            if count % 500 == 0:

                print(
                    f"Processed {count:,}"
                )

        print(
            f"TRIM processed {count:,} cells"
        )

    lookup = (
        working
        .set_index("GlobalID")
        .to_dict("index")
    )

    #
    # Only negative cells inside
    # the current TRIM region
    #

    active = set(
        working.loc[
            (
                working["GlobalID"].isin(region)
            )
            &
            (
                working["ave100"] < 0
            ),
            "GlobalID"
        ]
    )

    print()
    print("TRIM cleanup")

    pass_num = 1

    while active:

        changes = 0
        solved = []

        for gid in active:

            row = lookup.get(gid)

            if row is None:
                continue

            vals = []

            for nbr in (
                row["n1"],
                row["n2"],
                row["n3"],
                row["n4"],
                row["n5"],
                row["n6"]
            ):

                nbr_row = lookup.get(nbr)

                if nbr_row is None:
                    continue

                z = nbr_row["ave100"]

                if z > 0:
                    vals.append(z)

            if len(vals) >= 2:

                z = float(np.mean(vals))

                idx = gid_to_idx.get(gid)

                if idx is None:
                    continue

                working.at[
                    idx,
                    "ave100"
                ] = z

                lookup[gid]["ave100"] = z

                solved.append(gid)

                changes += 1

        for gid in solved:
            active.remove(gid)

        print(
            f"TRIM cleanup pass {pass_num}: "
            f"filled={changes:,} "
            f"remaining={len(active):,}"
        )

        if changes == 0:
            break

        pass_num += 1

    print()

    print(
        f"Final positives: "
        f"{(working['ave100'] > 0).sum():,}"
    )

    print(
        f"Final negatives: "
        f"{(working['ave100'] < 0).sum():,}"
    )

    print()

# ============================================================================
# PROCESS META
# ============================================================================

def process_meta_fills(
        working,
        gid_to_idx
):

    lidar_ids, trim_ids = read_meta()

    #
    # LDR regions
    #

    for gid in lidar_ids:

        print(
            f"LDR seed {gid}"
        )

        region = grow_region(
            gid,
            working
        )

        print(
            f"LDR region size = "
            f"{len(region):,}"
        )

        apply_lidar_fill(
            working,
            region,
            gid_to_idx
        )

    #
    # TRIM regions
    #

    for gid in trim_ids:

        print(
            f"TRIM seed {gid}"
        )

        region = grow_region(
            gid,
            working
        )

        print(
            f"TRIM region size = "
            f"{len(region):,}"
        )

        apply_trim_fill(
            working,
            region,
            gid_to_idx
        )

    print()

    print(
        "After meta fills:"
    )

    print(
        "Positive:",
        (working["ave100"] > 0).sum()
    )

    print(
        "Negative:",
        (working["ave100"] < 0).sum()
    )

    print()

    return working

def apply_lidar_fill(
        working,
        region,
        gid_to_idx
):

    print(
        f"Processing {len(region):,} lidar cells..."
    )

    region_df = working[
        working["GlobalID"].isin(region)
    ]

    count = 0

    #
    # Pass 1
    # Sample OLD lidar
    #

    for map_name, group in region_df.groupby("Map"):

        x = int(map_name[4:7])
        y = int(map_name[8:11])

        tif = (
            OLD_FOLDER /
            f"dem_{x:03d}_{y:03d}.tif"
        )

        print(
            f"{map_name} : "
            f"{len(group):,} cells"
        )

        if not tif.exists():

            print(
                f"Missing {tif}"
            )

            continue

        with rasterio.open(tif) as ds:

            print(
                "Resolution:",
                ds.res
            )

            band = ds.read(1)

            for row in group.itertuples():

                z = sample_average_100(
                    ds,
                    band,
                    row.cx,
                    row.cy
                )

                working.at[
                    row.Index,
                    "ave100"
                ] = z

                count += 1

                if count <= 10:

                    print(
                        f"GID={row.GlobalID} "
                        f"Z={z}"
                    )

                if count % 500 == 0:

                    print(
                        f"Processed {count:,}"
                    )

    print(
        f"LDR processed {count:,} cells"
    )

    print(
        f"After lidar positives="
        f"{(working['ave100'] > 0).sum():,}"
    )

    print(
        f"After lidar negatives="
        f"{(working['ave100'] < 0).sum():,}"
    )

    lookup = (
        working
        .set_index("GlobalID")
        .to_dict("index")
    )

    #
    # Only negative cells inside
    # the current LDR region
    #

    active = set(
        working.loc[
            (
                working["GlobalID"].isin(region)
            )
            &
            (
                working["ave100"] < 0
            ),
            "GlobalID"
        ]
    )

    print()
    print("LDR cleanup")

    pass_num = 1

    while active:

        changes = 0
        solved = []

        for gid in active:

            row = lookup.get(gid)

            if row is None:
                continue

            vals = []

            for nbr in (
                row["n1"],
                row["n2"],
                row["n3"],
                row["n4"],
                row["n5"],
                row["n6"]
            ):

                nbr_row = lookup.get(nbr)

                if nbr_row is None:
                    continue

                z = nbr_row["ave100"]

                if z > 0:
                    vals.append(z)

            if len(vals) >= 2:

                z = float(np.mean(vals))

                idx = gid_to_idx.get(gid)

                if idx is None:
                    continue

                working.at[
                    idx,
                    "ave100"
                ] = z

                lookup[gid]["ave100"] = z

                solved.append(gid)

                changes += 1

        for gid in solved:
            active.remove(gid)

        print(
            f"Cleanup pass {pass_num}: "
            f"filled={changes:,} "
            f"remaining={len(active):,}"
        )

        if changes == 0:
            break

        pass_num += 1

    print()

    print(
        f"Final positives: "
        f"{(working['ave100'] > 0).sum():,}"
    )

    print(
        f"Final negatives: "
        f"{(working['ave100'] < 0).sum():,}"
    )

    print()
# ============================================================================
# MERGE FILLS INTO MASTER
# ============================================================================

def merge_into_master(master, working):

    update_dict = (
        working
        .set_index("GlobalID")
        ["ave100"]
        .to_dict()
    )

    before = (
        master["ave100"] > 0
    ).sum()

    count = 0

    for idx, gid in enumerate(
        master["GlobalID"]
    ):

        z = update_dict.get(gid)

        if z is None:
            continue

        master.at[idx, "ave100"] = z

        count += 1

    after = (
        master["ave100"] > 0
    ).sum()

    print(f"Merged {count:,} records")

    print(
        f"Positive before: {before:,}"
    )

    print(
        f"Positive after : {after:,}"
    )

    return master
    
# ============================================================================
# QC2 OUTPUT
# ============================================================================

def write_qc2(master):

    outfile = (
        BASE /
        f"04_{WATERSHED}_MasterHEX_qc2.csv"
    )

    with open(outfile, "w") as f:

        f.write(
            "Map,GlobalID,Inside,DEM,"
            "HexID,cx,cy,ave100\n"
        )

        for row in master.itertuples():

            if row.ave100 <= 0:
                continue

            f.write(
                f"{row.Map},"
                f"{row.GlobalID:08d},"
                f"{row.Inside:02d},"
                f"{row.DEM:02d},"
                f"{row.HexID},"
                f"{row.cx:.4f},"
                f"{row.cy:.4f},"
                f"{row.ave100:.4f}\n"
            )

    print(
        f"Written: {outfile}"
    )


# ============================================================================
# MAIN
# ============================================================================
def main():

    start_time = perf_counter()

    print(
        f"\nWatershed: {WATERSHED}\n"
    )

    print("Loading MasterHEX.bin")

    master = load_masterhex_bin()

    #
    # Check seed in raw master
    #

    print("\nMASTER BEFORE PROCESSING")

    print(
        master.loc[
            master["GlobalID"] == 134511,
            ["GlobalID", "Map", "ave100"]
        ]
    )

    print("Loading NULL / EDGE")

    working = load_working_cells()

    #
    # GlobalID -> DataFrame index lookup
    #

    gid_to_idx = (
        working.reset_index()
        .set_index("GlobalID")["index"]
        .to_dict()
    )

    #
    # Lake fill
    #

    print("\nLake fill")

    working = fill_lakes(
        working
    )

    print(
        "\nNulls after lake fill:",
        (working["ave100"] < 0).sum()
    )

    #
    # LDR/TRIM fills
    #

    print("\nMeta fills")

    working = process_meta_fills(
        working,
        gid_to_idx
    )

    #
    # Verify seed updated in working
    #

    print("\nWORKING AFTER LDR")

    print(
        working.loc[
            working["GlobalID"] == 134511,
            ["GlobalID", "Map", "ave100"]
        ]
    )

    print()

    print(
        "Working positive:",
        (working["ave100"] > 0).sum()
    )

    print(
        "Working negative:",
        (working["ave100"] < 0).sum()
    )

    #
    # Merge
    #

    print("\nMerging")

    master = merge_into_master(
        master,
        working
    )

    #
    # Verify seed after merge
    #

    print("\nMASTER AFTER MERGE")

    print(
        master.loc[
            master["GlobalID"] == 134511,
            ["GlobalID", "Map", "ave100"]
        ]
    )

    #
    # Count remaining negatives
    #

    remaining = master[
        master["ave100"] < 0
    ]

    print(
        f"\nMaster negatives: "
        f"{len(remaining):,}"
    )

    if len(remaining) > 0:

        print(
            remaining[
                [
                    "GlobalID",
                    "Map",
                    "ave100"
                ]
            ].head(20)
        )

    #
    # Check test map
    #

    map049 = master[
        master["Map"] == "MAP_147_049"
    ]

    print(
        "\nMAP_147_049 negatives:",
        (map049["ave100"] < 0).sum()
    )

    print(
        "MAP_147_049 positives:",
        (map049["ave100"] > 0).sum()
    )

    #
    # QC export
    #

    write_qc2(master)

    #
    # Runtime
    #

    elapsed = perf_counter() - start_time

    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = elapsed % 60

    print()

    print(
        f"Finished in "
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds:05.2f}"
    )


if __name__ == "__main__":
    main()