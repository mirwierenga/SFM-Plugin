import os
import re
import struct
import sys


def convert_dem_exact_binary(input_path: str, output_path: str):
    # 1. If output file exists, delete it first
    if os.path.exists(output_path):
        os.remove(output_path)

    # 2. Extract spatial coordinate codes from filename (e.g. LOW_138_077.tif)
    filename = os.path.basename(input_path)
    match = re.search(r"(\d{3})_(\d{3})", filename)
    if not match:
        raise ValueError(
            f"Could not parse grid coordinates from filename: '{filename}'"
        )

    x_code, y_code = match.groups()
    x_ul = float(x_code) * 10000.0  # e.g., 1380000.0
    y_ul = float(y_code) * 10000.0  # e.g., 770000.0

    # 3. Read input TIFF to extract Tag 273 (StripOffsets) -> demLIST
    with open(input_path, "rb") as f_in:
        # Read IFD offset from bytes 4-7
        f_in.seek(4)
        ifd_offset = struct.unpack("<I", f_in.read(4))[0]

        # Read IFD tag count
        f_in.seek(ifd_offset)
        tag_count_bytes = f_in.read(2)
        tag_count = struct.unpack("<H", tag_count_bytes)[0]

        # Loop through tags to locate Tag 273
        tag_273_offset = None
        f_in.seek(ifd_offset + 2)
        for _ in range(tag_count):
            tag_id, tag_type, count, val_offset = struct.unpack(
                "<HHII", f_in.read(12)
            )
            if tag_id == 273:
                tag_273_offset = val_offset
                break

        if tag_273_offset is None:
            raise ValueError(
                "Tag 273 (StripOffsets) not found in input TIFF file."
            )

        # Read 5,000 scanline offsets into demLIST
        f_in.seek(tag_273_offset)
        dem_list = [
            struct.unpack("<I", f_in.read(4))[0] for _ in range(5000)
        ]

        # 4. Construct exact 1000-byte header buffer (zero-initialized)
        header = bytearray(1000)

        # Pos 1..8 (Idx 0..7): Standard Little-Endian TIFF Magic Header
        header[0:8] = b"II\x2a\x00\x08\x00\x00\x00"

        # Pos 9..10 (Idx 8..9): IFD Tag Count = 16
        header[8:10] = struct.pack("<H", 16)

        # Pos 11..202 (Idx 10..201): 16 Tag Directory Records (12 bytes each)
        tags_data = [
            (256, 3, 1, 5000),  # ImageWidth
            (257, 3, 1, 5000),  # ImageLength
            (258, 3, 1, 32),  # BitsPerSample
            (259, 3, 1, 1),  # Compression
            (262, 3, 1, 1),  # PhotometricInterpretation
            (273, 4, 1, 1000),  # StripOffsets
            (277, 3, 1, 1),  # SamplesPerPixel
            (278, 3, 1, 5000),  # RowsPerStrip
            (279, 4, 1, 100000000),  # StripByteCounts
            (284, 3, 1, 1),  # PlanarConfiguration
            (339, 3, 1, 3),  # SampleFormat
            (65536 - 31986, 12, 3, 200),  # ModelPixelScaleTag
            (65536 - 31614, 12, 6, 230),  # ModelTiepointTag
            (65536 - 30801, 3, 32, 280),  # GeoKeyDirectoryTag
            (65536 - 30799, 2, 25, 360),  # GeoAsciiParamsTag
            (65536 - 23423, 2, 5, 500),  # GeoTIFF Custom Tag
        ]

        tags_bytes = b"".join(
            struct.pack("<HHII", t_id, t_type, count, val)
            for t_id, t_type, count, val in tags_data
        )
        header[10:202] = tags_bytes

        # Pos 201..224 (Idx 200..223): TIFFres (cellx=2.0, celly=2.0, cellz=0.0)
        # Note: Exactly replicates VB6 overwriting byte positions 201-202
        cell = 2.0
        header[200:224] = struct.pack("<ddd", cell, cell, 0.0)

        # Pos 231..278 (Idx 230..277): TIFFutms (6 doubles)
        header[230:278] = struct.pack("<dddddd", 0.0, 0.0, 0.0, x_ul, y_ul, 0.0)

        # Pos 281..344 (Idx 280..343): Binary Projection key structure (64 bytes)
        proj_bytes = (
            bytes([1, 0, 1, 0, 0, 0, 7, 0])
            + bytes([0, 4, 0, 0, 1, 0, 1, 0])
            + bytes([1, 4, 0, 0, 1, 0, 1, 0])
            + bytes([2, 4, 177, 135, 18, 0, 0, 0])
            + bytes([1, 8, 177, 135, 6, 0, 18, 0])
            + bytes([6, 8, 0, 0, 1, 0, 142, 35])
            + bytes([0, 12, 0, 0, 1, 0, 189, 11])
            + bytes([4, 12, 0, 0, 1, 0, 41, 35])
        )
        header[280:344] = proj_bytes

        # Pos 361..392 (Idx 360..391): ASCII CRS string ("NAD83 / BC Albers|NAD83|")
        ascii_bytes = (
            bytes([0x4E, 0x41, 0x44, 0x38, 0x33, 0x20, 0x2F, 0x20])
            + bytes([0x42, 0x43, 0x20, 0x41, 0x6C, 0x62, 0x65, 0x72])
            + bytes([0x73, 0x7C, 0x4E, 0x41, 0x44, 0x38, 0x33, 0x7C])
            + bytes([0, 0, 0, 0, 0, 0, 0, 0])
        )
        header[360:392] = ascii_bytes

        # Pos 500..505 (Idx 499..504): NoData String Value ("\x00-9999")
        header[499:505] = b"\x00-9999"

        # Pos 999..1000 (Idx 998..999): Original input file IFD tagCOUNT short
        header[998:1000] = tag_count_bytes

        # 5. Write exact binary stream to destination file
        with open(output_path, "wb") as f_out:
            # Write 1000-byte binary header
            f_out.write(header)

            # Copy elevation scanlines from input file using demLIST offsets
            for offset in dem_list:
                f_in.seek(offset)
                f_out.write(f_in.read(20000))  # 5,000 floats * 4 bytes = 20,000

    print(f"Exact binary conversion complete:\n  Output: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python tiffconvert.py <path_to_old> <path_to_new>")
        sys.exit(1)

    convert_dem_exact_binary(sys.argv[1], sys.argv[2])