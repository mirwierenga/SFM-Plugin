#LidarOrgMatchToCsv (QGIS Processing) - QGIS 3.4 compatible

#written in march 2026 ?

"""
Part 0 - Available New LiDAR CSV

Workflow
1. Generate or load LiDAR inventory
    - Run BAT file to create lidar_file_list.txt, or
    - Use an existing TXT file containing LiDAR filepaths

2. Apply year filter
    - Keep files within the specified year range
    - Send files with missing or out-of-range years to report.csv

3. Validate file organization
    - Compare folder type (DEM, DSM, CHM) against filename type
    - Flag mismatches as anomalies

4. Filter file types
    - Keep only DEM and DSM files
    - Record CHM files in report.csv

5. Validate naming convention
    - Verify BC tile naming format
    - Verify required map index values (1-4)
    - Reject invalid filenames

6. Create filtered file lists
    - Write valid files to output.csv
    - Write rejected/problem files to report.csv

7. Extract mapsheet and file type information
    - Determine mapsheet ID from filepath
    - Classify each file as DEM or DSM
    - Normalize and trim filepaths

8. Match LiDAR files to Map2500 index
    - Join valid LiDAR files to mapsheet records in the Map Index CSV
    - Preserve all original map index attributes

9. Create final DEM/DSM outputs
    - Generate Map2500_DEM.csv
    - Generate Map2500_DSM.csv

10. Run QA/QC checks & perform completeness audit
    - Identify duplicate DEM/DSM coverage
    - Identify DEMs with no DSM
    - Identify DSMs with no DEM
    - Compare original TXT inventory against output.csv and report.csv
    - Identify files not accounted for by either output

11. Generate final reports
    - output.csv
    - report.csv
    - Map2500_DEM.csv
    - Map2500_DSM.csv
    - duplicates.csv
    - unpaired_dem_without_dsm.csv
    - unpaired_dsm_without_dem.csv
    - missing_from_both.csv

"""

from qgis.PyQt.QtCore import QCoreApplication #provides an event loop for Qt apps without UI
from qgis.core import (
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingParameterEnum,
    QgsProcessingParameterString, QgsProcessingParameterFile,
    QgsProcessingParameterBoolean, QgsProcessingException) #base classes for processing alg that enable parameter formatting 

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import subprocess

# Regex (search pattern) and index columns

YEAR_RE      = re.compile(r"(19|20)\d{2}")              # 1900..2099
BC_TILE_RE   = re.compile(r"^\d{3}[A-Za-z]\d{3}$")      # BC tile token: 3 digits + letter + 3 digits
MAPSHEET_RE  = re.compile(r"(?i)(\d{3}[a-z]\d{3}_\d_\d_\d)")
TRIM_RE      = re.compile(r"(?i)^.*?gdwuts[\\/]*")      # trim everything up to and including 'gdwuts[/\]'
_UTM_ZONE_RE = re.compile(r"_utm0*(9|10|11)_", re.IGNORECASE)

INDEX_COLS = ["map", "x2500", "y2500", "Albers10k", "ALBx", "ALBy", "ALBzone"]

LIDAR_DIR = r"\\objectstore.nrs.bcgov\gdwuts"
MAP_ROOT = r"C:\Albers10k2m\LiDAR_Prep\Map2500_OUT2.csv"
OUTPUT_FOLDER = r"C:\Albers10k2m\LiDAR_Prep"

# Helpers

#normalizing pathways
def _norm_path(p: str) -> Path:
    return Path(str(p).strip().strip('"').strip("'"))

def normalize_and_dedupe_utm_only(fps: List[str]) -> List[str]:
    seen = set()
    out = []

    for fp in fps:
        norm = re.sub(
            _UTM_ZONE_RE,
            lambda m: f"_utm{m.group(1)}_",
            fp
        )

        if norm not in seen:
            seen.add(norm)
            out.append(norm)

    return out

# Year parsing, accepts: "", "YYYY", "YYYY-YYYY", "YYYY-", "-YYYY".
def parse_year_range(s: Optional[str]) -> Tuple[int, int]:
    if not s:
        return 0, 9999
    s = s.strip()
    if not s:
        return 0, 9999
    if s.startswith("-"):       # "-YYYY"
        return 0, int(s[1:])
    if s.endswith("-"):         # "YYYY-"
        return int(s[:-1]), 9999
    if "-" in s:                # "YYYY-YYYY"
        a, b = map(int, s.split("-", 1))
        if a > b:
            raise ValueError("Start year must be <= end year.")
        return a, b
    y = int(s)               # "YYYY"
    return y, y

# Scanning to TXT
# finds files under root whose name matches a pattern using pure python
def _scan_py(root: str, pattern: str) -> List[str]:
    rootp = Path(root)
    if not rootp.is_dir():
        raise ValueError(f"Not a directory: {rootp}")
    return [str(p) for p in rootp.rglob(pattern)]

def run_bat_and_wait(bat_path: Path, feedback):
    """
    Run a BAT file synchronously and fail if it errors.
    """
    bat_path = Path(bat_path)

    if not bat_path.exists():
        raise FileNotFoundError(f"BAT file not found: {bat_path}")

    feedback.pushInfo(f"Running BAT file:\n{bat_path}")

    proc = subprocess.run(
        ["cmd.exe", "/c", str(bat_path)],
        capture_output=True,
        text=True
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"BAT failed with code {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}"
        )

    feedback.pushInfo("BAT file completed successfully.")

#identifying parts of filepaths
def mapsheet(s: str) -> Optional[str]:
    m = MAPSHEET_RE.search(str(s).replace("\\", "/"))
    return m.group(1).lower() if m else None

def kind_of(path: str) -> Optional[str]: 
    p = str(path).replace("/", "\\").lower()
    if ("\\dem\\" in p) or p.endswith("_dem.tif"):
        return "dem"
    if ("\\dsm\\" in p) or p.endswith("_dsm.tif"):
        return "dsm"
    return None

def trim_after_gdwuts(p: str) -> str:
    return TRIM_RE.sub("", str(p)).lstrip("/\\").replace("/", "\\")

def _folder_kind(token: str) -> Optional[str]:
    t = token.lower()
    return t if t in {"chm", "dem", "dsm"} else None

def _file_kinds(token: str) -> Set[str]:
    t = token.lower()
    kinds: Set[str] = set()
    if "chm" in t: kinds.add("chm")
    if "dem" in t: kinds.add("dem")
    if "dsm" in t: kinds.add("dsm")
    return kinds

def _fp_of(parts: List[str]) -> str:
    return str(Path(parts[0]).joinpath(*parts[1:]))

def uniq_keep_order(seq: List[str]) -> List[str]:
    seen = set(); out = []
    for x in seq:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

# Core filtering pipeline

def run_pipeline(txt_path: Path, y0: int, y1: int, out_dir: Path, feedback):
    txt_path = _norm_path(txt_path)
    if not txt_path.exists():
        raise FileNotFoundError(f"TXT not found: {txt_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "output.csv"
    rep_csv = out_dir / "report.csv"

    lines = [ln.strip() for ln in txt_path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    parts_list = [list(Path(s).parts) for s in lines]

    report_rows: List[List[str]] = []

    # 1) Year filter
    keep_year: List[List[str]] = []
    for p in parts_list:
        fp = _fp_of(p)
        m = YEAR_RE.search(fp)
        if not m:
            report_rows.append(p)   # no year found
            continue
        yr = int(m.group(0))
        if y0 <= yr <= y1:
            keep_year.append(p)
        else:
            report_rows.append(p)

    # 2) Type mismatch report (based on folder vs filename tokens)
    anomaly_seen: Set[str] = set()
    for p in keep_year:
        if len(p) > 5:  #path must have at least 6 components
            folder = _folder_kind(p[4])
            kinds = _file_kinds(p[5])
            if folder is None or not kinds:
                continue
            fp = _fp_of(p)

            def _add_once():
                if fp not in anomaly_seen:
                    report_rows.append(p); anomaly_seen.add(fp)

            #detect inconsistencies between file type and folder
            if "chm" in kinds and folder in {"dem", "dsm"}: _add_once(); continue
            if "dem" in kinds and folder in {"chm", "dsm"}: _add_once(); continue
            if "dsm" in kinds and folder in {"chm", "dem"}: _add_once(); continue

    # 2b) Include all CHM rows in report
    chm_seen: Set[str] = set()
    for p in keep_year:
        if len(p) > 5:
            folder_token = p[4].lower()
            name_token = p[5].lower()
            if ('chm' in name_token) or (folder_token == 'chm'):
                fp = _fp_of(p)
                if fp not in chm_seen:
                    report_rows.append(p); chm_seen.add(fp)

    # 3) Keep only DEM/DSM folders
    keep_type = [p for p in keep_year if len(p) > 4 and p[4].lower() in {"dem", "dsm"}]

    # 4) Strict filename checks (BC tile + three indices 1..4)
    valid_digits = {"1", "2", "3", "4"}
    keep_scale: List[List[str]] = []
    for p in keep_type:
        if len(p) <= 5:
            report_rows.append(p); continue
        split = p[5].split("_")
        if len(split) < 2 or not BC_TILE_RE.match(split[1]):    # BC tile must be at split[1]
            report_rows.append(p); continue
        if len(split) < 5 or not ({split[2], split[3], split[4]} <= valid_digits):
            report_rows.append(p); continue
        keep_scale.append(p)

    # 5) Write output.csv (normalized to backslashes)
    with out_csv.open("w", encoding="utf-8") as f:
        for p in keep_scale:
            f.write(_fp_of(p).replace("/", "\\") + "\n")

    # 6) Report CSV
    seen: Set[str] = set()
    with rep_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for p in report_rows:
            fp = _fp_of(p)
            if fp not in seen:
                w.writerow([fp]); seen.add(fp)

    feedback.pushInfo(f"Valid files written to: {out_csv}")
    feedback.pushInfo(f"Issues written to: {rep_csv}")
    feedback.pushInfo(f"Number of anomalies: {len(report_rows)}")

    return out_csv, rep_csv


# DEM/DSM expansion (preserve ALL map-index columns)

def expand_index_rows(index_rows, header_order, mapping, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header_order + ["filepath"])

        for r in index_rows:
            ms = str(r.get("map", "")).strip().lower()
            fps = mapping.get(ms)

            # Skip mapsheets with no matched filepaths
            if not fps:
                continue

            base = [r.get(c, "") for c in header_order]

            for fp in fps:
                w.writerow(base + [" " + fp])


def write_dem_dsm(output_csv: Path,
                  map_index_csv: Path,
                  out_dem: Path,
                  out_dsm: Path,
                  feedback):

    # 1) Read map index (preserve ALL columns)
    if not Path(map_index_csv).exists():
        raise FileNotFoundError(f"Map index CSV not found: {map_index_csv}")

    with open(map_index_csv, "r", encoding="utf-8", newline="") as f:
        rdr = csv.DictReader(f)

        # Raw header strings as they appear in the file (may include spaces)
        header_raw = rdr.fieldnames or []
        if not header_raw:
            raise ValueError("Map index CSV has no header row.")

        # Build normalized header order (trimmed), and a mapping normalized->raw
        header_order = [h.strip() for h in header_raw]
        raw_for_norm = {}
        for h_raw in header_raw:
            h_norm = h_raw.strip()
            if h_norm not in raw_for_norm:  # keep first occurrence if duplicates post-strip
                raw_for_norm[h_norm] = h_raw

        # Ensure we have a 'map' column (case/space-insensitive)
        norm_map = None
        for h in header_order:
            if h.lower() == "map":
                norm_map = h
                break
        if norm_map is None:
            raise ValueError("Map index is missing required column 'map' (case/space-insensitive).")

        # Normalize each row's keys to the trimmed header names
        index_rows: List[dict] = []
        for row in rdr:
            norm_row = {}
            for h_norm in header_order:
                h_raw = raw_for_norm[h_norm]       # original key present in DictReader
                norm_row[h_norm] = row.get(h_raw, "")
            # Guarantee 'map' exists exactly as 'map'
            if "map" not in norm_row:
                norm_row["map"] = norm_row.get(norm_map, "")
            index_rows.append(norm_row)

    # 2) Read output.csv (one filepath per line)
    files: List[str] = []
    if Path(output_csv).exists():
        for ln in Path(output_csv).read_text(encoding="utf-8", errors="ignore").splitlines():
            fp = ln.strip()
            if fp:
                files.append(fp)

    # 3) Build maps of mapsheet -> [trimmed filepaths], split by kind (DEM/DSM)
    dem_map: Dict[str, List[str]] = {}
    dsm_map: Dict[str, List[str]] = {}
    for fp in files:
        k = kind_of(fp)
        ms = mapsheet(fp)
        tr = trim_after_gdwuts(fp)
        if ms and k in {"dem", "dsm"}:
            (dem_map if k == "dem" else dsm_map).setdefault(ms, []).append(tr)

    # Conditional utm09 → utm9 normalization per mapsheet
    for m in dem_map:
        dem_map[m] = normalize_and_dedupe_utm_only(dem_map[m])

    for m in dsm_map:
        dsm_map[m] = normalize_and_dedupe_utm_only(dsm_map[m])

    # 4) Expand and write, preserving ALL map-index columns
    expand_index_rows(index_rows, header_order, dem_map, out_dem)
    expand_index_rows(index_rows, header_order, dsm_map, out_dsm)

    feedback.pushInfo(f"Valid DEM files matched to mapsheets written to: {out_dem}")
    feedback.pushInfo(f"Valid DSM files matched to mapsheets written to: {out_dsm}")


# Missing-files & duplicates

def write_missing_and_duplicates(output_csv: Path, out_folder: Path, feedback):
    rows = []
    if Path(output_csv).exists():
        for ln in Path(output_csv).read_text(encoding="utf-8", errors="ignore").splitlines():

            raw = ln.strip()
            filep = normalize_and_dedupe_utm_only(
                [trim_after_gdwuts(raw)]
            )[0]

            if not filep:
                continue
            k = kind_of(filep); ms = mapsheet(filep)
            if k and ms:
                rows.append({"file": filep, "kind": k, "map": ms})

    dup_path = out_folder / "duplicates.csv"
    miss_dir = out_folder / "missing_files"
    miss_dir.mkdir(parents=True, exist_ok=True)
    dem_wo_dsm_path = miss_dir / "unpaired_dem_without_dsm.csv"
    dsm_wo_dem_path = miss_dir / "unpaired_dsm_without_dem.csv"

    if not rows:
        with dup_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["map", "duplicates"])
        with dem_wo_dsm_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["map", "dem_without_dsm"])
        with dsm_wo_dem_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["map", "dsm_without_dem"])
        return dup_path, dem_wo_dsm_path, dsm_wo_dem_path, None

    # map -> kind -> ordered unique file list
    by_map_kind: Dict[str, Dict[str, List[str]]] = {}
    for r in rows:
        m = r["map"]; k = r["kind"]; fpath = r["file"]
        by_map_kind.setdefault(m, {}).setdefault(k, [])
        if fpath not in by_map_kind[m][k]:
            by_map_kind[m][k].append(fpath)

    # duplicates.csv (eligible = at least one DEM and DSM and total > 2)
    duplicates_lines = []
    for m, kinds in by_map_kind.items():
        dem_count = len(kinds.get("dem", []))
        dsm_count = len(kinds.get("dsm", []))
        total = dem_count + dsm_count
        if (dem_count > 0) and (dsm_count > 0) and (total > 2):
            dem_join = ";".join(kinds.get("dem", []))
            dsm_join = ";".join(kinds.get("dsm", []))
            duplicates_lines.append([m, f"DEM:{dem_join} DSM:{dsm_join}"])

    with dup_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["map", "duplicates"])
        for row in duplicates_lines: w.writerow(row)

    feedback.pushInfo(f"Valid duplicates written to: {dup_path}")

    # Unpaired sets
    dem_only_maps = []
    dsm_only_maps = []
    for m, kinds in by_map_kind.items():
        dem_present = len(kinds.get("dem", [])) > 0
        dsm_present = len(kinds.get("dsm", [])) > 0
        if dem_present and not dsm_present: dem_only_maps.append(m)
        if dsm_present and not dem_present: dsm_only_maps.append(m)

    with dem_wo_dsm_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["map", "dem_without_dsm"])
        for m in dem_only_maps:
            w.writerow([m, ";".join(by_map_kind[m]["dem"])])
    with dsm_wo_dem_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["map", "dsm_without_dem"])
        for m in dsm_only_maps:
            w.writerow([m, ";".join(by_map_kind[m]["dsm"])])

    feedback.pushInfo(f"Valid DEM files without corresponding DSM files written to: {dem_wo_dsm_path}")
    feedback.pushInfo(f"Valid DSM files without corresponding DEM files written to: {dsm_wo_dem_path}")

    return dup_path, dem_wo_dsm_path, dsm_wo_dem_path, None


def write_missing_from_both(txt_path: Path, output_csv: Path, report_csv: Path, out_missing: Path, feedback):
    def _norm(s: str) -> str:
        return s.strip().replace("/", "\\").strip('"').strip("'")

    txt_p = Path(txt_path)
    if not txt_p.exists():
        raise FileNotFoundError(f"TXT not found: {txt_path}")
    txt_set = {_norm(ln) for ln in txt_p.read_text(encoding="utf-8", errors="ignore").splitlines() if _norm(ln)}

    out_set = set()
    oc = Path(output_csv)
    if oc.exists():
        out_set = {_norm(ln) for ln in oc.read_text(encoding="utf-8", errors="ignore").splitlines() if _norm(ln)}

    rep_set = set()
    rc = Path(report_csv)
    if rc.exists():
        with rc.open("r", encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            for row in r:
                if row and _norm(row[0]):
                    rep_set.add(_norm(row[0]))

    accounted_for = out_set | rep_set #filepaths in output and report
    missing = sorted(txt_set - accounted_for) #filepaths unaccounted for bet output, reportt and txt

    out_missing.parent.mkdir(parents=True, exist_ok=True)
    with out_missing.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["missing"])
        for fp in missing: w.writerow([fp])

    feedback.pushInfo(f"TXT rows: {len(txt_set)} "
                      f"output: {len(out_set)} "
                      f"report: {len(rep_set)} "
                      f"missing-from-both: {len(missing)}")
    return out_missing


# QGIS Processing Algorithm

class LidarOrgMatchToCsv(QgsProcessingAlgorithm):
    PARAM_ACTION = "ACTION"
    PARAM_DIR = "APPLICABLE_DIR"
    PARAM_PATTERN = "PATTERN"
    PARAM_TXT = "TXT"
    PARAM_YEAR = "YEAR_RANGE"
    PARAM_MAP_INDEX = "MAP_INDEX"
    PARAM_OUT_FOLDER = "OUTPUT_FOLDER"
    PARAM_WRITE_MISSING = "WRITE_MISSING_FROM_BOTH"

    ACTIONS = ["Create TXT from directory", "Use existing TXT"]

    def tr(self, string):
        return QCoreApplication.translate("LidarOrgMatchToCsv", string)

    def createInstance(self):
        return LidarOrgMatchToCsv()

    def name(self):
        return "lidar_file_script"

    def displayName(self):
        return self.tr("Part 0 - Available New LiDAR csv")

    def group(self):
        return self.tr("SFM")

    def groupId(self):
        return "sfm"

    def shortHelpString(self):
        return self.tr(
            "NOTE: Run only when new LiDAR available\n"
            "Produces output/report CSVs, DEM/DSM map-index matching, "
            "duplicates, unpaired reports, and missing-from-both comparison. "
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterEnum(
                self.PARAM_ACTION, self.tr("Action"),
                options=self.ACTIONS, defaultValue=0
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_DIR,
                self.tr("Directory to scan (required if creating TXT)"),
                optional=True,
                defaultValue=LIDAR_DIR
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_PATTERN,
                self.tr("Filename glob (used when scanning)"),
                defaultValue="*_*1m_*.tif"
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.PARAM_TXT,
                self.tr("Path to an existing TXT of file paths (used if not scanning)"),
                behavior=QgsProcessingParameterFile.File,
                fileFilter="Text files (*.txt);;All files (*.*)",
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.PARAM_YEAR,
                self.tr("Year filter: YYYY, YYYY-YYYY, YYYY-, -YYYY (blank = all)"),
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.PARAM_MAP_INDEX,
                self.tr("Map index CSV path"),
                behavior=QgsProcessingParameterFile.File,
                fileFilter="CSV files (*.csv);;All files (*.*)",
                defaultValue=MAP_ROOT
            )
        )
        self.addParameter(
            QgsProcessingParameterFile(
                self.PARAM_OUT_FOLDER,
                self.tr("Output folder (all CSVs will be written here)"),
                behavior=QgsProcessingParameterFile.Folder,
                defaultValue=OUTPUT_FOLDER
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARAM_WRITE_MISSING,
                self.tr("Also write missing_from_both.csv (requires TXT)"),
                defaultValue=True
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        action_idx = self.parameterAsEnum(parameters, self.PARAM_ACTION, context)
        make_txt = (action_idx == 0)

        scan_dir = self.parameterAsString(parameters, self.PARAM_DIR, context)
        pattern = self.parameterAsString(parameters, self.PARAM_PATTERN, context) or "*_*1m_*.tif"
        txt_path = self.parameterAsString(parameters, self.PARAM_TXT, context)
        year_range = self.parameterAsString(parameters, self.PARAM_YEAR, context)
        map_index = self.parameterAsString(parameters, self.PARAM_MAP_INDEX, context)
        out_folder_str = self.parameterAsString(parameters, self.PARAM_OUT_FOLDER, context)

        if not out_folder_str:
            raise QgsProcessingException("Please provide an Output folder path.")
        out_folder = Path(out_folder_str)
        out_folder.mkdir(parents=True, exist_ok=True)
        csv_dir = out_folder / "csv_files"
        csv_dir.mkdir(parents=True, exist_ok=True)

        y0, y1 = parse_year_range(year_range)

        # Decide/get TXT
        if make_txt:

            # ---- Paths ----
            bat_file = Path(
                r"C:\Albers10k2m\LiDAR_Prep\LidarDEM_ObjectStore.bat"
            )

            file_list_txt = Path(
                r"C:\Albers10k2m\LiDAR_Prep\lidar_file_list.txt"
            )

            # ---- Run BAT to generate TXT ----
            run_bat_and_wait(bat_file, feedback)

            if not file_list_txt.exists():
                raise QgsProcessingException(
                    f"TXT was not created by BAT file:\n{file_list_txt}"
                )

            txt_in = file_list_txt

            # Optional: log line count
            try:
                line_count = sum(
                    1 for _ in file_list_txt.open("r", encoding="utf-8", errors="ignore")
                )
                feedback.pushInfo(f"TXT contains {line_count} file paths.")
            except Exception:
                pass
        else:
            if not txt_path:
                raise QgsProcessingException("Path to an existing TXT is required (Action = Use existing TXT).")
            txt_in = Path(txt_path)

        # Run pipeline
        output_csv, report_csv = run_pipeline(txt_in, y0, y1, csv_dir, feedback)

        # DEM/DSM expansion (Map index join, preserve ALL columns)
        dem_csv = out_folder / "Map2500_DEM.csv"
        dsm_csv = out_folder / "Map2500_DSM.csv"
        write_dem_dsm(output_csv, Path(map_index), dem_csv, dsm_csv, feedback)

        # Duplicates + unpaired
        dup_csv, dem_wo_dsm, dsm_wo_dem, _ = write_missing_and_duplicates(output_csv, out_folder, feedback)

        # Missing-from-both (optional)
        write_missing = self.parameterAsBool(parameters, self.PARAM_WRITE_MISSING, context)
        missing_from_both_csv = None
        if write_missing:
            missing_from_both_csv = out_folder / "csv_files" / "missing_from_both.csv"
            write_missing_from_both(txt_in, output_csv, report_csv, missing_from_both_csv, feedback)

        return {
            "OUTPUT_CSV": str(output_csv),
            "REPORT_CSV": str(report_csv),
            "DEM_CSV": str(dem_csv),
            "DSM_CSV": str(dsm_csv),
            "DUPLICATES_CSV": str(dup_csv),
            "DEM_WO_DSM_CSV": str(dem_wo_dsm),
            "DSM_WO_DEM_CSV": str(dsm_wo_dem),
            "MISSING_FROM_BOTH_CSV": (str(missing_from_both_csv) if missing_from_both_csv else "")
        }