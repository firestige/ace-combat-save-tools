# Verified Save Format Findings

This is a sanitized technical record, not a distribution of game data.

## Validation

ACZero money changes and purchase availability for all 36 internal aircraft IDs
were confirmed through game loading. A before/after purchase experiment isolated
the balance field and separated purchase eligibility from ownership. Unchanged
decode/compress/encode cycles reproduced the source byte-for-byte.

AC8 money edits were validated at the file level; explicit game-loading
confirmation was not recorded for AC8.
No emulator architecture was established for the bundled ACZero release.
The Thrustmaster API DLL is a controller SDK, not evidence of an emulator.

## ACZero outer container

All words are UInt32 little-endian; arithmetic wraps modulo `2**32`.

```text
steam_id = 76561197960265728 + account_id
account_mask = (steam_id XOR (steam_id >> 32)) AND 0x5A7EC3A5

offset 0:  magic = 0x534758FE
offset 4:  first
offset 8:  second
offset 12: encoded_length

length = (first XOR account_mask XOR second XOR encoded_length) AND 0xFFFFFF
padded_length = (length + 3) AND ~3

offset 16 + padded_length: third
offset 20 + padded_length: stored_check

initial = third XOR first XOR account_mask XOR 0xF5C8B683
step = third XOR second XOR 0xBCAF5269
key(i) = (initial + (i + 1) * step) mod 2**32
```

XOR each word of the payload at offset 16 with `key(i)`. Decompress only the
first `length` decoded payload bytes as a raw LZ4 block, without a frame header
or preceding uncompressed-size field. Ignore the remaining container tail.

The outer check covers ciphertext, including the encoded length word:

```text
ROR7(x) = ((x >> 7) OR (x << 25)) AND 0xFFFFFFFF
check = sum(ROR7(word) for word in file[12 : 16+padded_length]) mod 2**32
check_key = (initial + (padded_length / 4 + 1) * step) mod 2**32
stored_check XOR check_key == check
```

Recompression can change the compressed length. Update its encoded low 24 bits,
the aligned encrypted payload, and the two trailer words; preserve unrelated
header bits and remaining container bytes. Recompute the inner checksum first.

## ACZero decoded layout

Validated decoded length: 86,960 bytes. Initial UInt32: `0x12345678`.
These offsets belong to decoded data, not the on-disk ciphertext:

| Field | Offset/type |
| --- | --- |
| Current money | `0x264`, UInt32 LE |
| Aircraft purchase eligibility | `0x268 + 5 * id`, byte, IDs 0 through 35 |
| Aircraft ownership flags | `0x388 + 5 * id`, separate five-byte groups |
| Campaign clear count | `0x6C54`, UInt32 LE; not needed for purchase unlocking |
| Inner checksum | Last four bytes, UInt32 LE |

```python
inner_checksum = sum(byte ^ 0xAA for byte in decoded[:-4]) & 0xFFFFFFFF
```

Set only the individual eligibility byte to `1`. Do not fill the whole
five-byte group. The actual shop checks eligibility independently of ownership.
Hidden aircraft query mappings include ID 33 for X-02 and ID 34 for FALKEN;
ID 35's display name was not independently established.

The purchase experiment changed an ownership group, whereas only one of three
occurrences of the old money followed the balance change. Do not replace every
matching numeric value. A natural unlock notification is not the same as shop
eligibility; modifying medals or mission ranks is unnecessary for this operation.

## AC8 GVAS

Typical location:

```text
%LOCALAPPDATA%\BANDAI NAMCO Entertainment\ACE COMBAT 8\Saved\SaveGames\Campaign.sav
```

Parse the property tags instead of hardcoding offsets:

- `PackedData`: `ArrayProperty<ByteProperty>`, nested property stream.
- `CurrentMRP`: `UInt64Property`, eight-byte little-endian value.
- `Checksum`: outer `UInt32Property`, four-byte little-endian value.

```python
checksum = zlib.crc32(packed_data_bytes, 0x41916EBD)
```

Include the nested `None` terminator, exclude the byte array's count and outer
properties. Do not add another final XOR. Reproduce the original check before
changing anything; preserve `TotalMRP` unless explicitly requested.

## Boundaries

Only the first ACZero campaign record has been validated for editing.
Alternate slots, complete mission histories, natural unlock requirements, and
all display-name mappings are not fully documented.
Setting a clear count does not recreate a complete playthrough. Do not modify
game binaries, account binding, or cloud metadata as a fallback.
