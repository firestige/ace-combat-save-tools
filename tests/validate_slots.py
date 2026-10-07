import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import lz4.block

TOOL = (
    Path(__file__).resolve().parents[1]
    / ".github" / "skills" / "ace-combat-save-editing" / "scripts" / "aczero_save.py"
)
MASK = 0xFFFFFFFF
STRIDE = 0x70DC
ACCOUNT = 42


def require(condition, message):
    if not condition:
        raise ValueError(message)


def wrap(decoded):
    payload = lz4.block.compress(bytes(decoded), store_size=False)
    padded = (len(payload) + 3) & ~3
    account_mask = ((76561197960265728 + ACCOUNT) ^ ((76561197960265728 + ACCOUNT) >> 32)) & 0x5A7EC3A5
    first, second, third = 0x12345678, 0x87654321, 0xABCDEF01
    initial = third ^ first ^ account_mask ^ 0xF5C8B683
    step = third ^ second ^ 0xBCAF5269
    encoded = (len(payload) ^ first ^ account_mask ^ second) & 0xFFFFFF
    result = bytearray(max(86960, padded + 24))
    struct.pack_into("<4I", result, 0, 0x534758FE, first, second, encoded)
    aligned = payload + bytes(padded - len(payload))
    for index, word in enumerate(struct.iter_unpack("<I", aligned)):
        struct.pack_into("<I", result, 16 + 4 * index, word[0] ^ ((initial + (index + 1) * step) & MASK))
    checksum = sum(
        ((word[0] >> 7) | (word[0] << 25)) & MASK
        for word in struct.iter_unpack("<I", result[12:16 + padded])
    ) & MASK
    struct.pack_into(
        "<2I", result, 16 + padded, third,
        checksum ^ ((initial + (padded // 4 + 1) * step) & MASK),
    )
    return bytes(result)


def seal(decoded):
    struct.pack_into("<I", decoded, len(decoded) - 4, sum(value ^ 0xAA for value in decoded[:-4]) & MASK)
    return wrap(decoded)


with tempfile.TemporaryDirectory(prefix="aczero-three-slot-validation-") as directory:
    root = Path(directory)
    before = bytearray(86960)
    struct.pack_into("<I", before, 0, 0x12345678)
    for index in range(3):
        shift = index * STRIDE
        start = 0x118 + shift
        struct.pack_into("<5I", before, start, 2, 4, 2, 1, 54)
        struct.pack_into("<I", before, 8 + index * 24, 2)
        struct.pack_into("<I", before, start + 0x94, 2)
        struct.pack_into("<I", before, 0x264 + shift, (index + 1) * 1000)
        before[0x268 + shift] = 1
        before[0x388 + shift] = 1
    source = root / "synthetic.sav"
    source.write_bytes(seal(before))
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    calls = 0

    def run(arguments, expect_success=True, expected_error=None):
        global calls
        calls += 1
        output = root / f"run-{calls}"
        command = [
            sys.executable, str(TOOL), "--source", str(source),
            "--account-id", str(ACCOUNT), "--output-dir", str(output),
        ] + arguments
        result = subprocess.run(command, capture_output=True, text=True)
        if expect_success:
            require(result.returncode == 0, result.stderr)
            return json.loads(result.stdout), output
        require(result.returncode != 0, "Invalid operation unexpectedly succeeded")
        if expected_error:
            require(expected_error in result.stderr, "Failure was not the expected rejection")
        require(not list(output.glob("*.sav")), "Rejected edit generated a candidate")
        return None, output

    summary, _ = run([])
    require([slot["money"] for slot in summary["slots_before"]] == [1000, 2000, 3000], "Slot listing mismatch")
    for slot in range(1, 4):
        shift = (slot - 1) * STRIDE
        for operation in (["--set", "123456"], ["--unlock-aircraft"], ["--set", "123456", "--unlock-aircraft"]):
            result, output = run(["--slot", str(slot)] + operation)
            after = (output / "save.decoded.bin").read_bytes()
            allowed = set(range(len(before) - 4, len(before)))
            if "--set" in operation:
                allowed.update(range(0x264 + shift, 0x268 + shift))
                require(struct.unpack_from("<I", after, 0x264 + shift)[0] == 123456, "Wrong target balance")
            if "--unlock-aircraft" in operation:
                allowed.update(0x268 + shift + 5 * index for index in range(36))
                require(all(after[0x268 + shift + 5 * index] == 1 for index in range(36)), "Incomplete aircraft flags")
            changed = {i for i, (a, b) in enumerate(zip(before, after)) if a != b}
            require(changed <= allowed, "Unexpected decoded changes")
            require(after[:0x118] == before[:0x118], "Shared header changed")
            for index in range(3):
                start = 0x118 + index * STRIDE
                if index + 1 != slot:
                    require(after[start:start + STRIDE] == before[start:start + STRIDE], "Other slot changed")
            require(result["owned_aircraft_unchanged"], "Ownership changed")
            require(result["selected_slot"] == slot, "Selected slot reporting mismatch")
            require(hashlib.sha256(source.read_bytes()).hexdigest() == original_hash, "Source changed")
    run(["--set", "1"], False, "explicit --slot")
    run(["--unlock-aircraft"], False, "explicit --slot")
    run(["--slot", "0"], False, "invalid choice")
    run(["--slot", "4"], False, "invalid choice")
    empty = bytearray(before)
    struct.pack_into("<I", empty, 8 + 24, MASK)
    struct.pack_into("<5I", empty, 0x118 + STRIDE, 19, MASK, 0, 0, 0)
    source.write_bytes(seal(empty))
    summary, _ = run([])
    require(summary["slots_before"][1]["state"] == "empty", "Empty detection failed")
    require(summary["slots_before"][1]["money"] is None, "Empty slot exposes a fabricated balance")
    run(["--slot", "2", "--set", "1"], False, "refusing to edit")
    unknown = bytearray(before)
    struct.pack_into("<I", unknown, 8 + 24, 3)
    source.write_bytes(seal(unknown))
    summary, _ = run([])
    require(summary["slots_before"][1]["state"] == "unknown", "Conflicting metadata not detected")
    run(["--slot", "2", "--unlock-aircraft"], False, "refusing to edit")
    no_mission = bytearray(before)
    struct.pack_into("<I", no_mission, 0x118 + STRIDE + 4, MASK)
    source.write_bytes(seal(no_mission))
    summary, _ = run(["--slot", "2", "--set", "1"])
    require(summary["slots_before"][1]["state"] == "occupied", "Mission sentinel mistaken for empty slot")
    print(f"Passed {calls} targeted CLI validations; all three slots, separate/combined edits, slot isolation, empty/unknown guards, and mission sentinel.")
