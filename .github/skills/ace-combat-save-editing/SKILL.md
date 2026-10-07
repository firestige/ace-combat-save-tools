---
name: ace-combat-save-editing
description: Use when modifying or analyzing ACE COMBAT 8 Campaign.sav or the bundled ACZero PC save, including MRP, money, aircraft purchase eligibility, checksums, backup and restore.
---

# Ace Combat Save Editing

Read [the verified findings](../../../docs/FORMAT.md) before editing. Do not apply AC8's GVAS layout or CRC32 seed to ACZero.

## Safety and scope

- Identify the actual game/version, save file and Steam account. Do not assume the bundled ACZero is PCSX2; its implementation was not established.
- Ask what the user wants to change. Aircraft purchase eligibility, ownership, weapons, colors, medals and campaign completion are separate states.
- Close the game before replacement. Back up the current save to a uniquely named ZIP in the same directory and verify its contents byte-for-byte.
- Decode and validate both existing checksums before editing. Abort on unexpected layout or flags.
- First build and verify a copy. Allow only the requested decoded fields and required checksums to change.
- Write atomically only after confirming the source has not changed. Decode the written file again; game loading remains a separate user validation.
- Use the latest save, not an old analysis snapshot. Preserve the current balance unless explicitly asked to change it.
- Read all three slot summaries before editing. Require an explicit `--slot 1|2|3`; refuse empty/unrecognized slots and never infer occupancy from the mission ID alone.

## Bundled ACZero PC

Validation is limited to executable file/product version **1.0.2.1**,
Steam installation Build ID **25201480**, checked on 2026-10-07.
The Build ID identifies the installed Steam package, not a separate executable
version or a guarantee about other releases. Do not claim other-version support.

The user verified both setting money to 12,000,000 and unlocking every aircraft for purchase without granting ownership or changing campaign progress.

Archive tool: [scripts/aczero_save.py](scripts/aczero_save.py). Requires Python and `lz4`; restore/install this dependency only if missing. It does not need Capstone or pefile. Default mode writes analysis/candidate files only; `--apply` explicitly replaces the supplied save after verified ZIP backup.

```powershell
$save = 'C:\Program Files (x86)\Steam\userdata\<accountid>\2288340\remote\aczero\save\save.sav'
$tool = '.github\skills\ace-combat-save-editing\scripts\aczero_save.py'

# Decode and verify; no source modification.
python $tool --source $save

# Build a candidate, then explicitly apply after review.
python $tool --source $save --slot 1 --unlock-aircraft
python $tool --source $save --slot 1 --unlock-aircraft --apply

# Money edit; do not combine with unlock unless both are requested.
python $tool --source $save --slot 1 --set 12000000 --apply
```

For copies outside the Steam directory, supply `--account-id` with the owning account's numeric folder ID. This is the 32-bit account ID, not the 64-bit Steam ID. Never guess the account or change it to bypass a checksum failure.

Known decoded offsets for the 86,960-byte layout, shown for slot 1.
Add `(slot - 1) * 0x70DC` for slot 2 or 3; record starts are
`0x118`, `0x71F4`, `0xE2D0`. Slots 1 and 2 are game-verified; slot 3
requires user game-loading validation even after offline checks pass.
See [slot 2 evidence](../../../docs/VALIDATION.md) for the FALKEN purchase
preview and money display; screenshots do not individually prove all 36 planes.

- Balance: `0x264`, little-endian UInt32.
- Aircraft purchase eligibility: `0x268 + 5 * id`, one byte, `id=0..35`, set only this byte to `1`.
- Aircraft ownership: `0x388 + 5 * id`, separate five-byte state; preserve the whole ownership area.
- Campaign clear count: `0x6C54`, little-endian UInt32. **Do not modify for aircraft purchase unlocking.**
- Inner checksum: last four bytes, little-endian `sum(byte ^ 0xAA for byte in data[:-4]) & 0xFFFFFFFF`.

Slot existence uses the shared header level at `8 + 24 * (slot - 1)`,
with `0xFFFFFFFF` as the empty marker. For occupied slots this must match
`record_start + 0x94`. Never initialize an empty slot by editing these fields.
Verify non-target records and the shared header are unchanged.

Do not set all five aircraft flags to one: that changes weapons/colors or other adjacent state. Do not describe a clear-count edit as reconstructing a full playthrough. All aircraft display names and natural unlock conditions are not fully validated.

## AC8

This capability is intentionally documented only in the skill and its script,
not in the public README or format/validation documents. Use
[scripts/campaign_mrp.py](scripts/campaign_mrp.py), which needs only the
Python standard library. An explicit `--source` is required; the default
is validation/candidate output only. `--apply` backs up and replaces the source.

```powershell
$tool = '.github\skills\ace-combat-save-editing\scripts\campaign_mrp.py'
$save = Join-Path $env:LOCALAPPDATA 'BANDAI NAMCO Entertainment\ACE COMBAT 8\Saved\SaveGames\Campaign.sav'
python $tool --source $save
python $tool --source $save --add 1000000
python $tool --source $save --set 6600000 --apply
```

`Campaign.sav` uses GVAS, a byte-array `PackedData`, and nested `CurrentMRP` typed `UInt64Property`. Parse tags; do not reuse absolute offsets. The outer `Checksum` is little-endian UInt32:

```python
checksum = zlib.crc32(packed_data_bytes, 0x41916EBD)
```

Include the inner `None` terminator, exclude the array count and outer properties. First reproduce the original checksum exactly. AC8 money edits were file-verified; unlike ACZero, no explicit user game-loading confirmation was recorded.
