# Manual Flame check

Run before promoting a release, and whenever Writer XML changes. What only Flame can answer:
does it import, place and show what we write. Record the Flame version and the result per step,
and add the result to the release's Verification line (`docs/ci.md`, Promote).

Use the ComfyUI workflow from the README round trip (Reader → Writer, `clip_info` wired,
Writer fields on auto) on a private ComfyUI (`acceptance/README.md`) or your own.

| # | Do | Expect |
|---|---|---|
| 1 | Export a clip from Flame at start timecode **01:00:00:00** (23.976). In ComfyUI write it to a **new** clip (`clip_filename` `$(path)/$(clip_name)_cf.clip`). Import the new `.clip` in Flame. | Imports without a warning; 23.976; starts at 01:00:00:00; colour tag shown matches the source; frames play. |
| 2 | In Flame, add a version to that ComfyUI-written clip (render into it from Batch, Open Clip, "add version"). | Flame adds a second version next to v001 (note the name it picks); both versions play; the clip still opens in ComfyUI's Reader (`clip_report` lists both). |
| 3 | In ComfyUI, append to the **Flame** clip from step 1 (default `clip_filename`). Re-import or refresh in Flame. | One clip, v0 and v1 (Flame numbering continued); both at 01:00:00:00, same length, aligned (not 86,409 frames long); the original version unchanged. |
| 4 | Look at the colour tag of the clips from steps 1 and 3 in Flame (clip info / colour management). | Same tag as the source (e.g. `Rec.709 video` or `Rec.1886 Rec.709 - Display`), not empty and not the Writer default when the source had another one. |
| 5 | Name a Write node `woods walk ` (trailing space), export, append from ComfyUI as in step 3. | The version lands in `woods walk .clip`; no separate `woods walk.clip` appears. |

Record:

| Date | Flame version | Release / commit | Steps passed | Notes |
|---|---|---|---|---|
| 2026-10-30 | 2024.1 (Beak, via PR #8) | v2.0.0 / 1f1a489 | 1, 2, 3, 4, 5 | New clip imports at 01:00:00:00 (23.976, 118 frames, `Rec.709 video`); Flame added v02/v03 and ComfyUI v04 on top of v1, all at 01:00:00:00 and the Reader opens the five-version clip; append to the Flame clip gives v0 + v1, 118 frames (no more 86,409); colour tag on every version; trailing space lands in `fast walk .clip`. Extra: append from a 00:00:00:00 clip refused; pixels bit-exact. Found: a Reader window lost its timecode offset (fixed in 2.0.1) |
