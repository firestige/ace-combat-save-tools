import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--output-dir", type=Path)
parser.add_argument("--apply", action="store_true")
amount = parser.add_mutually_exclusive_group()
amount.add_argument("--set", type=int, dest="target")
amount.add_argument("--add", type=int, dest="increment")
args = parser.parse_args()
editing = args.target is not None or args.increment is not None
if args.apply and not editing:
    parser.error("--apply requires --set or --add")
SEED = 0x41916EBD
SOURCE = args.source.resolve()
OUTPUT_DIR = args.output_dir or Path(tempfile.mkdtemp(prefix="campaign-mrp-"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_game_closed():
    require(os.name == "nt", "--apply is supported only on Windows")
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "if (Get-Process -ErrorAction Stop | Where-Object { $_.ProcessName -match '^AceCombat' }) { exit 1 }",
        ],
        capture_output=True, text=True, check=False,
    )
    require(result.returncode == 0, result.stderr.strip() or "Close the game before applying edits")


def read_string(data, offset):
    require(0 <= offset <= len(data) - 4, "String offset out of bounds")
    length = struct.unpack_from("<i", data, offset)[0]
    end = offset + 4 + length
    require(0 < length <= 256 and end <= len(data), "Invalid property string")
    require(data[end - 1] == 0, "Property string is not null-terminated")
    return data[offset + 4 : end - 1].decode("ascii"), end


def read_type(data, offset, depth=0):
    require(depth < 8, "Unsupported type nesting")
    name, offset = read_string(data, offset)
    require(offset + 4 <= len(data), "Truncated type header")
    count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    require(count <= 8, "Unsupported type parameter count")
    children = []
    for _ in range(count):
        child, offset = read_type(data, offset, depth + 1)
        children.append(child)
    return (name, children), offset


def property_value(data, name, expected_type, start=0, end=None):
    if end is None:
        end = len(data)
    encoded = name.encode("ascii") + b"\0"
    needle = struct.pack("<i", len(encoded)) + encoded
    header = data.find(needle, start, end)
    require(header >= 0, f"Missing property: {name}")
    require(data.find(needle, header + 1, end) < 0, f"Ambiguous property: {name}")
    actual_name, offset = read_string(data, header)
    actual_type, offset = read_type(data, offset)
    require(actual_name == name and actual_type == expected_type, f"Unexpected type: {name}")
    require(offset + 5 <= end, f"Truncated header: {name}")
    size = struct.unpack_from("<I", data, offset)[0]
    require(data[offset + 4] == 0, f"Unsupported property flags: {name}")
    value_offset = offset + 5
    require(value_offset + size <= end, f"Value exceeds container: {name}")
    return value_offset, size


if args.apply:
    require_game_closed()
original = SOURCE.read_bytes()
require(original[:4] == b"GVAS", "Not a GVAS save")
packed_offset, packed_size = property_value(
    original, "PackedData", ("ArrayProperty", [("ByteProperty", [])])
)
require(packed_size >= 4, "Truncated byte array")
packed_length = struct.unpack_from("<I", original, packed_offset)[0]
require(packed_length + 4 == packed_size, "Byte array size mismatch")
packed_start = packed_offset + 4
packed_end = packed_start + packed_length
require(
    original[packed_end - 9 : packed_end] == b"\x05\0\0\0None\0",
    "PackedData has no expected property terminator",
)
mrp_offset, mrp_size = property_value(
    original, "CurrentMRP", ("UInt64Property", []), packed_start, packed_end
)
checksum_offset, checksum_size = property_value(
    original, "Checksum", ("UInt32Property", []), packed_end
)
require(mrp_size == 8 and checksum_size == 4, "Unexpected scalar sizes")
old_mrp = struct.unpack_from("<Q", original, mrp_offset)[0]
target_mrp = args.target if args.target is not None else old_mrp + (args.increment or 0)
require(0 <= target_mrp < 2**64, "Target MRP is outside UInt64 range")
old_checksum = struct.unpack_from("<I", original, checksum_offset)[0]
require(
    zlib.crc32(original[packed_start:packed_end], SEED) == old_checksum,
    "Original checksum cannot be reproduced; save was not modified",
)

backup = None
if args.apply:
    backup = SOURCE.with_name(
        f"Campaign.backup-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
    )
    with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(SOURCE.name, original)
    with zipfile.ZipFile(backup, "r") as archive:
        require(archive.testzip() is None, "ZIP integrity verification failed")
        require(archive.namelist() == [SOURCE.name], "Unexpected ZIP contents")
        require(archive.read(SOURCE.name) == original, "ZIP backup differs from original")

modified = bytearray(original)
struct.pack_into("<Q", modified, mrp_offset, target_mrp)
new_checksum = zlib.crc32(modified[packed_start:packed_end], SEED)
struct.pack_into("<I", modified, checksum_offset, new_checksum)
allowed = set(range(mrp_offset, mrp_offset + 8)) | set(
    range(checksum_offset, checksum_offset + 4)
)
changed = [i for i, (a, b) in enumerate(zip(original, modified)) if a != b]
require(set(changed) <= allowed, "Unrelated save bytes changed")
output = OUTPUT_DIR / ("Campaign.modified.sav" if editing else "Campaign.verified.sav")
require(output.resolve() != SOURCE, "Output path would overwrite the source")
output.write_bytes(modified)
require(output.read_bytes() == modified, "Candidate save read-back failed")
require(SOURCE.read_bytes() == original, "Source changed during analysis")

if args.apply:
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="Campaign.mrp-", suffix=".tmp", dir=SOURCE.parent, delete=False
        ) as stream:
            staged = Path(stream.name)
            stream.write(modified)
            stream.flush()
            os.fsync(stream.fileno())
        require(staged.read_bytes() == modified, "Staged save read-back failed")
        require(SOURCE.read_bytes() == original, "Live save changed; replacement aborted")
        require_game_closed()
        os.replace(staged, SOURCE)
        staged = None
    finally:
        if staged is not None:
            staged.unlink()

written = SOURCE.read_bytes() if args.apply else output.read_bytes()
require(written == modified and len(written) == len(original), "Final save read-back failed")
require(struct.unpack_from("<Q", written, mrp_offset)[0] == target_mrp, "Final MRP is incorrect")
require(
    zlib.crc32(written[packed_start:packed_end], SEED)
    == struct.unpack_from("<I", written, checksum_offset)[0],
    "Final checksum verification failed",
)
if backup is not None:
    with zipfile.ZipFile(backup, "r") as archive:
        require(archive.read(SOURCE.name) == original, "Final ZIP backup verification failed")
print(
    json.dumps(
        {
            "save": str(SOURCE),
            "output": str(output),
            "backup_zip": str(backup) if backup is not None else None,
            "backup_verified": backup is not None,
            "applied_to_live_save": args.apply,
            "source_unchanged": not args.apply,
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "modified_sha256": hashlib.sha256(written).hexdigest(),
            "file_length": len(written),
            "old_mrp": old_mrp,
            "new_mrp": target_mrp,
            "mrp_delta": target_mrp - old_mrp,
            "mrp_offset": mrp_offset,
            "checksum_offset": checksum_offset,
            "old_checksum": f"0x{old_checksum:08X}",
            "new_checksum": f"0x{new_checksum:08X}",
            "changed_byte_offsets": changed,
            "save_read_back_and_checksum_verified": True,
        },
        indent=2,
    )
)
