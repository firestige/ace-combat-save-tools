# Ace Combat Save Tools

Local, offline tools and a reusable Copilot skill for ACE COMBAT save analysis.
This is an unofficial project, not affiliated with the publisher.

## Included

- ACZero PC save decoder/editor: money and aircraft purchase eligibility.
- Reusable skill: `.github/skills/ace-combat-save-editing/SKILL.md`.
- [Format findings and validation boundaries](docs/FORMAT.md), including AC8 checksum notes.

ACZero money and all 36 aircraft purchase flags were verified by loading edited
saves in the game. Ownership and campaign progress were preserved. The supported
decoded layout is 86,960 bytes, first campaign record only; other builds and
campaign slots are not guaranteed. AC8 is documented, but no AC8 editor is
included in this release.

## Setup

Python 3.13 was used for validation. Windows is required for `--apply`.

```powershell
python -m pip install -r requirements.txt
$tool = '.github\skills\ace-combat-save-editing\scripts\aczero_save.py'
$save = 'C:\Program Files (x86)\Steam\userdata\<accountid>\2288340\remote\aczero\save\save.sav'

# Read and validate; writes analysis copies, not the source.
python $tool --source $save

# Generate a candidate before deciding to replace the real save.
python $tool --source $save --unlock-aircraft

# Close the game. This creates and verifies a same-directory ZIP backup.
python $tool --source $save --unlock-aircraft --apply

# Money edit; use only when explicitly wanted.
python $tool --source $save --set 12000000 --apply
```

Replace the example Steam root and `<accountid>` with your actual values.
`--account-id` is the owning account's 32-bit folder ID, not a SteamID64.
Supply it explicitly when analyzing a copy outside the standard Steam layout.
`--amount` searches for a number; only `--set` changes money.
The tool does not discover the active campaign slot.

Without `--apply`, the source is unchanged. Outputs go to a fresh temporary
directory unless `--output-dir` is supplied. Logs and JSON results contain local
paths and save hashes: do not publish them without reviewing/redacting them.

## Safety and restoration

Close the game before replacing or restoring a save. The tool verifies both
checksums, restricts decoded changes, verifies ZIP backups, checks for source
changes, and replaces through a same-directory temporary file.
Run a fresh read-only decode after applying, then load the save in the game.
Restore by extracting the chosen ZIP's save file into its original directory
with the game closed. Steam Cloud can overwrite local saves; resolve conflicts
deliberately. Do not edit cloud metadata blindly.

Aircraft unlocking grants purchase eligibility, not ownership or free purchases.
It does not unlock weapons, colors, medals, or reconstruct a completed campaign.
Money and unlock operations can be combined only when both are intended.

## Privacy

This repository contains no game saves, game binaries, memory dumps, account
IDs, private paths, credentials, or recorded per-user save hashes.
All processing is local; the script has no network upload functionality.
Never commit generated artifacts or backup archives.

## 中文摘要

工具默认只生成分析副本；只有 `--apply` 才会备份并覆盖来源存档。
全飞机解锁仅开启购买资格，保留余额、已购飞机和现有关卡进度。
修改前关闭游戏，写入后重新读回校验，再进入游戏验证。
不要将账号 ID、存档、程序、内存快照或运行日志上传到公开仓库。
