# game-localization

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-compatible-orange.svg)](https://agentskills.io/)

**English** · [简体中文](README.zh-CN.md)

> Localize any PC game into your language — not just RPG Maker.

An **engine-agnostic game localization pipeline** for AI agents:
detect the engine → extract text → translate with control codes frozen →
write back fail-closed → audit against a backup. Ships with six runnable
Python scripts and no third-party dependencies.

**The core idea:** the engine only decides *where the text lives* and
*what its markup looks like*. It does not change the workflow. Everything
engine-specific is compressed into a small profile, so adding an engine means
adding one profile — not rewriting the pipeline.

---

## Why not just use an existing tool

There are mature game translation tools out there
([RPGMTL](https://github.com/MizaGBF/RPGMTL),
[rvpacker-txt-rs](https://github.com/RPG-Maker-Translation-Tools/rvpacker-txt-rs),
[RPGMLocalizer](https://github.com/Lord0fTurk/RPGMLocalizer), RuneTranslate, …),
and they solve real problems. This pipeline targets the gaps they leave:

| # | Gap | Typical tool | This pipeline |
|---|---|---|---|
| 1 | **Control-code coverage** | Protects the generic set: `\V \C \N \I` | Regex-covers **every** code the engine actually uses. In one measured title, 83% of all control codes were `\m[n]` (face variants) and `\z[n]` (sprite poses) — the generic set misses both, and getting them wrong crashes the game |
| 2 | **Write semantics** | Missing path → create it | **Fail-closed:** a missing path raises. Rationale below |
| 3 | **Completion criterion** | "Does it still contain kana?" | **Path-by-path comparison against the pristine backup.** Rationale below |
| 4 | **Spreadsheet paste** | Usually not addressed | Built-in detection; `--dry` pinpoints the exact row |
| 5 | **Content policy** | All-or-nothing | System text and sensitive content handled separately; skips are recorded |
| 6 | **Pre-release cleanup** | Usually not addressed | Dedicated checklist with a never-delete list |

### On gap 2

The original writer parsed paths with
`re.findall(r'([^\[\]]+)|\[(\d+)\]', path)`.
For a key without brackets like `terms.messages.autosave`, that treats the
**whole string as one key** and silently creates it in the root object.
**The JSON stays valid. Nothing raises. Nothing logs.** You only find out from a diff.

*Fail-closed* means: prefer noise over a silent corruption.

### On gap 3

Judging completion by "does it still contain kana?" fails in both directions:

- **False negatives** — Japanese words written entirely in kanji are invisible to it.
  「勉強」「料理」「元気」「statistics」 contain zero kana; they look translated and are not.
- **False positives** — perfectly legal target-language content trips it.
  The Chinese middle dot in 「李·爱铃」, the Japanese long vowel mark 「ーーー」,
  a deliberately preserved character name.

Measured: one scan reported 73 leftovers; after checking each one,
**only 35 were genuinely untranslated** — the other 38 were preserved names,
name prefixes, middle dots and long vowel marks.
**"N leftovers" ≠ "N untranslated".**

`audit.py` reports candidates, not verdicts, and auto-triages the obvious noise
first (25,175 "identical" entries reduced to 545 needing review —
24,627 of them were pure ASCII asset identifiers).

---

## Install

The repo is both a standard Agent Skill and a set of standalone CLI scripts.

### As an Agent Skill

```
your-skills-dir/
└── game-localization/
    ├── SKILL.md
    ├── references/
    ├── scripts/
    └── assets/
```

Drop it into your agent's skills directory. Works with any client following the
[Agent Skills open standard](https://agentskills.io/) — Claude Code, OpenClaw,
WorkBuddy, and others.

> ⚠️ `SKILL.md` and the `references/` docs are written in Chinese (the pipeline
> was validated on Japanese → Chinese). The scripts, their CLI output, and the
> engine profiles are language-neutral. An English overview of the rules is at
> the top of `SKILL.md`.

On Windows, if you keep the repo on another drive, a directory junction avoids
copying:

```cmd
mklink /J "%USERPROFILE%\.openclaw\skills\game-localization" "<repo-path>"
```

### As standalone scripts

No third-party dependencies. Python 3.8+:

```bash
git clone https://github.com/pcmxiao/game-localization.git
cd game-localization/scripts
python detect_engine.py "/path/to/game"
```

---

## The five steps

### 1. Detect the engine

```bash
python scripts/detect_engine.py <game-dir>
```

Prints the engine **and the tier** (the tier matters more than the name):

| Tier | Meaning | How to handle |
|---|---|---|
| **L0** | Plaintext | Edit directly ✅ |
| **L1** | Packed | Unpack with a dedicated tool → edit → repack |
| **L2** | Binary / encrypted | Dedicated unpacker; prefer a loose-file override over repacking |
| **L3** | Compiled into the binary | Not editable → external hooks: Textractor / LunaTranslator / XUnity AutoTranslator |
| **L4** | Physically impossible | Text baked into images, burned-in subtitles, console ROMs → OCR + redraw, or an emulator agent |

**Knowing it is L4 up front beats discovering it two hours in.**

### 2. Extract

```bash
python scripts/extract_sheet.py --root <game-dir> --engine auto --out sheet.csv
```

Produces one engine-neutral sheet, openable straight in Excel:

```
id,carrier,file,path,src,text,status,note
```

Only **player-visible** text is extracted. Event names, `note` comments and
asset identifiers stay out — nobody sees them, so translating them only adds risk.

### 3. Translate → 4. Write back

Fill the `text` column in Excel / LibreOffice / any spreadsheet.
**Do not touch the other columns.**

```bash
# Always verify first
python scripts/apply_sheet.py --root <game-dir> --sheet sheet.csv --dry

# Then write (backs up outside the game directory automatically)
python scripts/apply_sheet.py --root <game-dir> --sheet sheet.csv
```

`--dry` checks that the control-code multisets match, that every path exists,
and that the source text still matches. Pasting from a spreadsheet **shifts whole
columns** (this happened twice in practice and is invisible to the eye);
`--dry` pinpoints the exact row.

### 5. Audit

```bash
python scripts/audit.py --root <game-dir> --backup <backup-dir> --report audit.json
```

Compares against the backup and reports four buckets:

```
total entries                     77257
  ✓ changed                       51842
  · identical but ignore           24630  ← auto-triaged
  ⚠ identical, needs judgement       545  ← review each
  ⚠ changed but still foreign        38  ← coarse filter, verify
  ⚠ code in a text slot               0  ← do not touch
```

Then clean up per [`references/release.md`](references/release.md).

---

## Adding an engine

A profile answers exactly five questions:

| Field | Meaning |
|---|---|
| `detect` | Which files/folders identify it |
| `open` | Plaintext, or needs unpacking |
| `carriers` | Which files and fields hold text |
| `freeze` | The engine's markup / control-code syntax |
| `seal` | What to write back (override dir / patch file / repack) |

**The sheet in the middle is engine-neutral** — that is the decoupling point.
Write one handler that yields `(file, path, text)` and both `apply_sheet.py`
and `audit.py` work immediately.

No profile is not a blocker either: for any plaintext container
(JSON / CSV / INI / YAML / Lua / `.po`),
[`references/carriers.md`](references/carriers.md) documents a generic fallback —
deep traversal plus field-name heuristics — you can write on the spot.

### Built in

RPG Maker MV/MZ · generic plaintext fallback · gettext

Text locations for other engines are in
[`references/carriers.md`](references/carriers.md); markup syntax is in
[`references/freeze-restore.md`](references/freeze-restore.md).

---

## Layout

```
game-localization/
├── SKILL.md                      # The pipeline and its hard rules (what agents read)
├── scripts/
│   ├── gameloc_core.py           # Engine-neutral core
│   ├── detect_engine.py          # Identify engine + tier
│   ├── extract_sheet.py          # Extract into the translation sheet
│   ├── apply_sheet.py            # Fail-closed write-back
│   ├── audit.py                  # Verify against the backup
│   └── selftest.py               # 74 assertions, one per real-world failure
├── references/
│   ├── carriers.md               # Where each engine keeps its text
│   ├── freeze-restore.md         # Control-code freezing and restoring
│   ├── pitfalls.md               # Real-world failure catalogue
│   ├── release.md                # Pre-release cleanup
│   └── engines/index.md          # Tiers, index, how to add an engine
├── assets/
│   └── glossary.template.csv     # Glossary template
└── evals/
    └── evals.json
```

---

## Where this came from

Distilled from a real RPG Maker MZ localization: 77,257 visible strings and
44,158 control codes (83% of them `\m` and `\z`). Along the way we found and
fixed a dozen **silent** failures — the path parser swallowing a key name, a
scan script forgetting to pass `events` so 139 maps reported a false "zero
leftovers", a screening regex matching the empty string and flagging 498 rows,
and a substring match classing 「メイク」 (makeup) as explicit content.

All of them are in [`references/pitfalls.md`](references/pitfalls.md).
This repository contains no game text, no translations and no per-game glossaries —
a game's translation belongs to that game's authors, not to the tool.

---

## License

MIT
