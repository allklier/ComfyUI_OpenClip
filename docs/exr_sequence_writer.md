# OpenClip EXR Sequence Writer — reference notes

Node `OpenClipExrSequenceWriter` (`nodes/exr_sequence_writer.py`), added 2026-10-31 on
`feature/2.0-clip-info`. Interface table: `nodes/CLAUDE.md`. Tests:
`tests/test_exr_sequence_writer.py` (smoke tier, synthetic data).

## Contract

- Writes `<folder>/<stem>.%0Nd.exr`, no `.clip` file. EXR only.
- Auto fields follow `OpenClipWriter` 2.0: auto value → `clip_info`, else default; a typed
  value overrides. Defaults are imported from `nodes/writer.py` (`DEFAULT_*`, `COMPRESSIONS`,
  `_BIT_DEPTHS`), so the two writers can't drift. If `writer.py` renames those, this node's
  import breaks loudly at load time.
- `start_timecode` auto with no `clip_info` value → `00:00:00:00`, as the Writer does for a new
  clip. Every frame gets its own `smpte:TimeCode` counting up from it.
- `folder` must be absolute. Relative folders are refused rather than guessed (ComfyUI's
  output dir? cwd?). Align with the Writer's relative-path support once that is settled.

## Version token

`(?<![^._])v(\d+)(?![^._])`, rightmost match. Matches `shot_v01`, `shot.v001`, `v009`,
`shot_v1_comp`; not `shotv01`, `shot-v01`, `shot_V01`, `shot_v01x`.

- `"next"`: scans the folder for `<head>v<digits><tail>.<digits>.exr`; one past the highest
  via `openclip_xml.next_version()` (keeps that one's padding); none → the name's own token.
- typed `v<digits>`: replaces the token. Other values are refused.
- no token: no version logic; a typed `version_name` only prints a warning.

## Collision / overwrite

Existing frames = `glob(glob.escape(stem) + ".*.exr")` filtered by `^stem\.\d+\.exr$`.
Present and `overwrite` off → `VERSION ALREADY EXISTS` / `SEQUENCE ALREADY EXISTS`. With
overwrite, they are deleted before writing. AOV sibling files (`<stem>_AOV_*`) are not
counted and not deleted — same gap as `OpenClipWriter`.

## Verified facts (2026-10-31)

- `image_io.write_sequence` signature (`lib/image_io.py:274`): `colour_space`,
  `start_timecode=(ticks, rate, drop)`, `fps_rational`, `aov_entries`, `aov_layout`.
- Written frames read back through `OpenClipExrSequenceReader`: float32 pixels and mask
  bit-exact (`torch.equal`), `FramesPerSecond` (24000, 1001) at 23.976, `openclip:ColorSpace`,
  start timecode 01:00:00:00 and a window at frame 1003 → 01:00:00:02, AOVs in both layouts
  via `OpenClipAOVReader`.

## Known limits

- Not tested in Flame or a live ComfyUI.
- AOV siblings of an overwritten version are left in place if the new write has no AOVs.
