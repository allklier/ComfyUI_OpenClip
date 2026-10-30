# Real Flame clips for the full tier

`tests/test_flame_clips.py` (marker `flame_clips`, runs with `pytest --tier full`) uses every
`.clip` it finds in `tests/fixtures/flame/<folder>/`. That folder is **gitignored** and listed in
`.comfyignore`: these are real exports, not ours to redistribute. Missing folders skip, so the
set can grow one clip at a time. On the test machine, `scripts/maintainer_test.sh` links
`$OC_FLAME_CLIPS_DIR` (default `~/oc_accept/flame_clips`) in as `tests/fixtures/flame`.

## How to export one

From Flame, an Open Clip export (Write File node or Export, Open Clip on, EXR), with the media
in the same folder as the `.clip` or anywhere readable. Copy the `.clip` (and the media, if it
lives elsewhere) into `tests/fixtures/flame/<folder>/`. The test copies the `.clip` to a temp
folder before writing anything and only reads the media: absolute media paths are used as they
are, and a path whose folder doesn't exist on this machine is looked up next to the `.clip`.
3 to 10 frames are enough (the tests read 2; `OPENCLIP_FLAME_FRAME_CAP` changes that).
Record the Flame version in the table when you add one.

## Planned set

`test_planned_clip_covers_what_it_is_for` checks that each folder holds one clip that really
shows its case (catches a mislabelled export).

| Folder | Clip | What it covers | Flame version |
|---|---|---|---|
| `tc_0100` | any name | 23.976 (24000/1001) at start timecode 01:00:00:00 (issue #7) | |
| `tc_drop` | any name | 29.97 drop-frame: Flame's `dropMode` spelling and `nbTicks` meaning (both UNVERIFIED) | |
| `tc_pal` | any name | 25 fps at 10:00:00:00 | |
| `tc_numbered` (optional) | any name | frame numbers taken from the timecode (first frame = `nbTicks`, e.g. 86400) | |
| `name_trailing` | `woods walk .clip` | `<name>` with a trailing space (issue #7) | |
| `name_leading` | ` lead café.clip` | leading space and non-ASCII in the name | |
| `versions` | any name | two or more Flame versions (v0 + v1): numbering continues v1 → v2 | |
| `aces_alpha` | any name | ACEScg, 32-bit float, with alpha | |
| `audio` | any name | a clip with an audio track: only the picture track is touched | |
| `padding` | any name | frame padding other than 6 | |
| `matte` (optional) | any name | a matte export: what channels Flame writes for it | |

## Also used when present

- `testclips/clip_1/alpha_test.clip` (gitignored; the issue-#7 repro clip, Flame 2027.2), its
  media path rewritten to `testclips/clip_1/testclip_1.*`.
- `tests/fixtures/autodesk_examples/` (Autodesk's `/opt/Autodesk/openclip_examples`). These are
  hand-made examples, not exports: no `<startTimecode>`, but EXRs at 01:00:00:00, read by the
  Reader since 2026-10-29 (`test_real_clip_exr_timecode_when_the_xml_has_none`).
- `$OPENCLIP_FLAME_CLIPS`: extra `.clip` files or folders, separated by `:`.

## What is checked per clip

Reader → Writer with `clip_info` wired and every field on auto, into a new clip and appended
to (a copy of) the source, then appended again. `tests/flame_compare.py` compares, against the
source XML and EXRs: start timecode (`rate`, `nbTicks`, `dropMode`; the source's first-frame EXR
timecode when its XML has none), `colourSpace`, bit depth (`channelsDepth`),
`editRate`/`sampleRate` as exact fractions, `<name>` and the `.clip` file name verbatim,
schema version, width/height, path padding and frame range, Flame's `<name>_<version>.` media
naming and version numbering; on append, every original element unchanged except the two
`currentVersion` attributes. Per written frame: `smpte:TimeCode` = start + offset and equal to
the source frame's, `openclip:ColorSpace`/`autodeskColorSpace` = the tag, pixels equal to the
source frame. The Writer's bit depth is on auto, so it comes through `clip_info`.
A clip whose versions differ in size is expected to be refused by the Reader with the
"VERSIONS OF DIFFERENT SIZES" error (`test_real_clip_with_versions_of_different_sizes_is_refused`;
the round-trip tests check that error instead).
