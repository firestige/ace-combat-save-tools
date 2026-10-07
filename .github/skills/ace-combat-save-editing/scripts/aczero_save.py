import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import lz4.block

MASK = 0xFFFFFFFF
SLOT_COUNT = 3
RECORD_START = 0x118
RECORD_STRIDE = 0x70DC
parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--account-id", type=int)
parser.add_argument("--output-dir", type=Path)
parser.add_argument("--amount", type=int, default=8650)
parser.add_argument("--set", type=int, dest="target")
parser.add_argument("--unlock-aircraft", action="store_true")
parser.add_argument("--slot", type=int, choices=range(1, SLOT_COUNT + 1))
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
editing = args.target is not None or args.unlock_aircraft
if editing and args.slot is None:
    parser.error("Edits require an explicit --slot 1, 2, or 3")
if args.apply and args.target is None and not args.unlock_aircraft:
    parser.error("--apply requires --set or --unlock-aircraft")
if args.target is not None and not 0 <= args.target < 2**32:
    parser.error("--set must be within UInt32 range")
if not 0 <= args.amount < 2**32:
    parser.error("--amount must be within UInt32 range")
SOURCE = args.source.resolve()
if args.account_id is None:
    if len(SOURCE.parents) < 5 or not SOURCE.parents[4].name.isdecimal():
        parser.error("Cannot infer Steam account ID; specify --account-id for a copied save")
    account = int(SOURCE.parents[4].name)
else:
    account = args.account_id
if not 0 <= account < 2**32:
    parser.error("--account-id must be within UInt32 range")
DIRECTORY = args.output_dir or Path(tempfile.mkdtemp(prefix="aczero-edit-"))
DIRECTORY.mkdir(parents=True, exist_ok=True)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def require_game_closed():
    require(os.name == "nt", "--apply is supported only on Windows")
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "if (Get-Process -Name acz -ErrorAction SilentlyContinue) { exit 1 }",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    require(result.returncode == 0, result.stderr.strip() or "Close ACZero before applying edits")


def rotate_right_7(value):
    return ((value >> 7) | (value << 25)) & MASK


def describe_slots(data):
    slots = []
    for index in range(SLOT_COUNT):
        shift = index * RECORD_STRIDE
        start = RECORD_START + shift
        summary_level = struct.unpack_from("<I", data, 8 + 24 * index)[0]
        record_level = struct.unpack_from("<I", data, start + 0x94)[0]
        header = struct.unpack_from("<5I", data, start)
        if summary_level == MASK:
            state = "empty" if header == (19, MASK, 0, 0, 0) else "unknown"
        else:
            state = "occupied" if summary_level == record_level else "unknown"
        available = [data[0x268 + shift + 5 * aircraft] for aircraft in range(36)]
        owned = [data[0x388 + shift + 5 * aircraft] for aircraft in range(36)]
        flags_valid = all(value in (0, 1) for value in available + owned)
        if state == "occupied" and not flags_valid:
            state = "unknown"
        slots.append({
            "slot": index + 1,
            "state": state,
            "summary_level": None if summary_level == MASK else summary_level,
            "money": struct.unpack_from("<I", data, 0x264 + shift)[0] if state == "occupied" else None,
            "available_aircraft": sum(available) if state == "occupied" else None,
            "owned_aircraft_ids": (
                [aircraft for aircraft, value in enumerate(owned) if value]
                if state == "occupied" else None
            ),
            "game_loading_verified": index in (0, 1),
        })
    return slots


def lz4_decode(data, expected_size):
    output = bytearray()
    offset = 0
    while offset < len(data):
        token = data[offset]
        offset += 1
        literal_length = token >> 4
        if literal_length == 15:
            while True:
                require(offset < len(data), "Truncated literal length")
                extension = data[offset]
                offset += 1
                literal_length += extension
                if extension != 255:
                    break
        require(offset + literal_length <= len(data), "Truncated literals")
        output.extend(data[offset : offset + literal_length])
        offset += literal_length
        require(len(output) <= expected_size, "LZ4 literals exceed expected size")
        if offset == len(data):
            break
        require(offset + 2 <= len(data), "Truncated match offset")
        distance = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        require(0 < distance <= len(output), "Invalid LZ4 match offset")
        match_length = (token & 15) + 4
        if (token & 15) == 15:
            while True:
                require(offset < len(data), "Truncated match length")
                extension = data[offset]
                offset += 1
                match_length += extension
                if extension != 255:
                    break
        require(len(output) + match_length <= expected_size, "LZ4 match exceeds expected size")
        for _ in range(match_length):
            output.append(output[-distance])
    return bytes(output)


if args.apply:
    require_game_closed()
original = SOURCE.read_bytes()
require(len(original) >= 24, "Truncated save")
magic, first, second, encoded_length = struct.unpack_from("<4I", original)
require(magic == 0x534758FE, "Unexpected save magic")
steam_id = 76561197960265728 + account
account_mask = (steam_id ^ (steam_id >> 32)) & 0x5A7EC3A5
length = (first ^ account_mask ^ second ^ encoded_length) & 0xFFFFFF
padded_length = (length + 3) & ~3
require(4 <= length and padded_length + 24 <= len(original), "Invalid packed size")
third, stored_check = struct.unpack_from("<2I", original, 16 + padded_length)
initial = third ^ first ^ account_mask ^ 0xF5C8B683
step = third ^ second ^ 0xBCAF5269
checksum = sum(
    rotate_right_7(value[0])
    for value in struct.iter_unpack("<I", original[12 : 16 + padded_length])
) & MASK
checksum_key = (initial + (padded_length // 4 + 1) * step) & MASK
require(stored_check ^ checksum_key == checksum, "Container checksum mismatch")
packed = bytearray(original[16 : 16 + padded_length])
for i in range(padded_length // 4):
    value = struct.unpack_from("<I", packed, i * 4)[0]
    key = (initial + (i + 1) * step) & MASK
    struct.pack_into("<I", packed, i * 4, value ^ key)
round_trip = bytearray(original)
for i in range(padded_length // 4):
    value = struct.unpack_from("<I", packed, i * 4)[0]
    key = (initial + (i + 1) * step) & MASK
    struct.pack_into("<I", round_trip, 16 + i * 4, value ^ key)
struct.pack_into("<I", round_trip, 20 + padded_length, checksum ^ checksum_key)
require(round_trip == original, "Container byte-for-byte round trip failed")
decoded = lz4_decode(packed[:length], 64 * 1024 * 1024)
require(
    lz4.block.decompress(bytes(packed[:length]), uncompressed_size=len(decoded)) == decoded,
    "Independent LZ4 decoder disagrees",
)
require(len(decoded) >= 8, "Truncated inner save")
require(struct.unpack_from("<I", decoded)[0] == 0x12345678, "Unexpected inner save magic")
inner_checksum = struct.unpack_from("<I", decoded, len(decoded) - 4)[0]
require(
    sum(byte ^ 0xAA for byte in decoded[:-4]) & MASK == inner_checksum,
    "Inner save checksum mismatch",
)
original_decoded = decoded
require(len(decoded) == 86960, "Unrecognized save layout")
slots_before = describe_slots(decoded)
selected_slot = args.slot or 1
if editing:
    require(
        slots_before[selected_slot - 1]["state"] == "occupied",
        f"Slot {selected_slot} is empty or unrecognized; refusing to edit it",
    )
slot_shift = (selected_slot - 1) * RECORD_STRIDE
balance_offset = 0x264 + slot_shift
clear_count_offset = 0x6C54 + slot_shift
owned_start = 0x388 + slot_shift
old_balance = struct.unpack_from("<I", decoded, balance_offset)[0]
availability_offsets = [0x268 + slot_shift + 5 * aircraft for aircraft in range(36)]
owned_offsets = [owned_start + 5 * aircraft for aircraft in range(36)]
require(
    not editing or all(decoded[offset] in (0, 1) for offset in availability_offsets + owned_offsets),
    "Unrecognized aircraft state flags",
)
available_before = sum(decoded[offset] for offset in availability_offsets)
owned_before = [aircraft for aircraft, offset in enumerate(owned_offsets) if decoded[offset]]
allowed = set(range(len(decoded) - 4, len(decoded)))
changed_offsets = set()
backup = None
if args.apply:
    edit_kind = "aircraft-unlock" if args.unlock_aircraft else "mrp-edit"
    backup = SOURCE.with_name(f"save.before-{edit_kind}-{datetime.now():%Y%m%d-%H%M%S-%f}.zip")
    with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(SOURCE.name, original)
    with zipfile.ZipFile(backup, "r") as archive:
        require(archive.testzip() is None, "ZIP backup integrity failed")
        require(archive.namelist() == [SOURCE.name], "Unexpected ZIP backup contents")
        require(archive.read(SOURCE.name) == original, "ZIP backup differs from original")
if args.target is not None or args.unlock_aircraft:
    edited = bytearray(decoded)
    if args.target is not None:
        struct.pack_into("<I", edited, balance_offset, args.target)
        allowed.update(range(balance_offset, balance_offset + 4))
    if args.unlock_aircraft:
        for offset in availability_offsets:
            edited[offset] = 1
        allowed.update(availability_offsets)
    inner_checksum = sum(byte ^ 0xAA for byte in edited[:-4]) & MASK
    struct.pack_into("<I", edited, len(edited) - 4, inner_checksum)
    changed_offsets = {
        i for i, (before, after) in enumerate(zip(decoded, edited)) if before != after
    }
    require(changed_offsets <= allowed, "Unrelated decoded save bytes changed")
    decoded = bytes(edited)
for index in range(SLOT_COUNT):
    if index + 1 == selected_slot:
        continue
    start = RECORD_START + index * RECORD_STRIDE
    end = start + RECORD_STRIDE
    require(decoded[start:end] == original_decoded[start:end], "Non-target slot changed")
require(decoded[:RECORD_START] == original_decoded[:RECORD_START], "Common save header changed")
compressed = lz4.block.compress(decoded, store_size=False)
new_length = len(compressed)
new_padded_length = (new_length + 3) & ~3
require(new_length < 0x1000000, "Recompressed data exceeds container capacity")
rebuilt = bytearray(original)
if len(rebuilt) < new_padded_length + 24:
    rebuilt.extend(bytes(new_padded_length + 24 - len(rebuilt)))
new_encoded_length = (
    (new_length ^ first ^ account_mask ^ second) & 0xFFFFFF
) | (encoded_length & 0xFF000000)
struct.pack_into("<I", rebuilt, 12, new_encoded_length)
padding_size = new_padded_length - new_length
padding = bytes(packed[length:]) if new_length == length else bytes(padding_size)
plain_payload = compressed + padding
for i in range(new_padded_length // 4):
    value = struct.unpack_from("<I", plain_payload, i * 4)[0]
    key = (initial + (i + 1) * step) & MASK
    struct.pack_into("<I", rebuilt, 16 + i * 4, value ^ key)
new_check = sum(
    rotate_right_7(value[0])
    for value in struct.iter_unpack("<I", rebuilt[12 : 16 + new_padded_length])
) & MASK
new_check_key = (initial + (new_padded_length // 4 + 1) * step) & MASK
struct.pack_into("<2I", rebuilt, 16 + new_padded_length, third, new_check ^ new_check_key)
rebuilt_payload = bytearray(rebuilt[16 : 16 + new_padded_length])
for i in range(new_padded_length // 4):
    value = struct.unpack_from("<I", rebuilt_payload, i * 4)[0]
    struct.pack_into("<I", rebuilt_payload, i * 4, value ^ ((initial + (i + 1) * step) & MASK))
require(
    lz4.block.decompress(bytes(rebuilt_payload[:new_length]), uncompressed_size=len(decoded))
    == decoded,
    "Recompressed and rewrapped payload does not round trip",
)
require(
    ((first ^ account_mask ^ second ^ struct.unpack_from("<I", rebuilt, 12)[0]) & 0xFFFFFF)
    == new_length,
    "Rebuilt container size mismatch",
)
require(
    (
        struct.unpack_from("<I", rebuilt, 20 + new_padded_length)[0]
        ^ new_check_key
    )
    == (
        sum(
            rotate_right_7(value[0])
            for value in struct.iter_unpack("<I", rebuilt[12 : 16 + new_padded_length])
        ) & MASK
    ),
    "Rebuilt outer checksum mismatch",
)
require(
    struct.unpack_from("<I", decoded, len(decoded) - 4)[0]
    == sum(byte ^ 0xAA for byte in decoded[:-4]) & MASK,
    "Modified inner checksum mismatch",
)
if args.target is not None:
    require(struct.unpack_from("<I", decoded, balance_offset)[0] == args.target, "Modified balance mismatch")
if args.unlock_aircraft:
    require(all(decoded[offset] == 1 for offset in availability_offsets), "Aircraft unlock failed")
    require(
        all(decoded[offset] == original_decoded[offset] for offset in owned_offsets),
        "Owned aircraft changed",
    )
    require(
        decoded[clear_count_offset:clear_count_offset + 4]
        == original_decoded[clear_count_offset:clear_count_offset + 4],
        "Campaign clear count changed",
    )
rebuilt_output = DIRECTORY / (
    f"save.slot-{selected_slot}.aircraft-purchasable.sav" if args.unlock_aircraft else (
        "save.rebuilt-unchanged.sav" if args.target is None
        else f"save.slot-{selected_slot}.mrp-{args.target}.sav"
    )
)
rebuilt_output.write_bytes(rebuilt)
require(rebuilt_output.read_bytes() == rebuilt, "Rebuilt copy read-back failed")
output = DIRECTORY / "save.decoded.bin"
output.write_bytes(decoded)
require(output.read_bytes() == decoded, "Decoded output read-back failed")
candidates = {}
for format_name, format_code in (("uint32_le", "<I"), ("uint32_be", ">I"), ("uint64_le", "<Q")):
    needle = struct.pack(format_code, args.amount)
    positions = []
    start = 0
    while True:
        pos = decoded.find(needle, start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + 1
    candidates[format_name] = positions
require(SOURCE.read_bytes() == original, "Live save changed during analysis")
if args.apply:
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="save.mrp-", suffix=".tmp", dir=SOURCE.parent, delete=False
        ) as stream:
            staged = Path(stream.name)
            stream.write(rebuilt)
            stream.flush()
            os.fsync(stream.fileno())
        require(staged.read_bytes() == rebuilt, "Staged save read-back failed")
        require(SOURCE.read_bytes() == original, "Live save changed; replacement aborted")
        require_game_closed()
        os.replace(staged, SOURCE)
        staged = None
    finally:
        if staged is not None:
            staged.unlink()
    require(SOURCE.read_bytes() == rebuilt, "Final live save read-back failed")
    with zipfile.ZipFile(backup, "r") as archive:
        require(archive.read(SOURCE.name) == original, "Final ZIP backup verification failed")
print(json.dumps({
    "source_sha256": hashlib.sha256(original).hexdigest(),
    "packed_size": length,
    "decoded_size": len(decoded),
    "container_checksum_verified": True,
    "container_byte_exact_round_trip": True,
    "independent_lz4_verified": True,
    "inner_checksum_verified": True,
    "inner_checksum": inner_checksum,
    "selected_slot": selected_slot,
    "slots_before": slots_before,
    "slots_after": describe_slots(decoded),
    "non_target_slots_unchanged": True,
    "current_balance_offset": hex(balance_offset),
    "current_balance_uint32_le": struct.unpack_from("<I", decoded, balance_offset)[0],
    "old_balance": old_balance,
    "recompressed_size": new_length,
    "recompressed_and_rewrapped_verified": True,
    "rebuilt_copy_byte_identical": rebuilt == original,
    "rebuilt_unchanged_copy": str(rebuilt_output),
    "backup_zip": str(backup) if backup is not None else None,
    "applied_to_live_save": args.apply,
    "unrelated_decoded_fields_unchanged": changed_offsets <= allowed,
    "available_aircraft_before": available_before,
    "available_aircraft_after": sum(decoded[offset] for offset in availability_offsets),
    "owned_aircraft_ids": owned_before,
    "owned_aircraft_unchanged": (
        decoded[owned_start:owned_start + 0xB4]
        == original_decoded[owned_start:owned_start + 0xB4]
    ),
    "campaign_clear_count": struct.unpack_from("<I", decoded, clear_count_offset)[0],
    "changed_decoded_byte_offsets": sorted(changed_offsets),
    "searched_amount": args.amount,
    "amount_candidates": candidates,
    "decoded_copy": str(output),
    "source_unchanged": not args.apply,
}, indent=2))
