# Acceptance run (live ComfyUI)

Drives the real nodes through a real ComfyUI server's HTTP API. Not part of `pytest`; part of
the **full** tier (`tests/CLAUDE.md` "Tiers"). Normally run by `scripts/maintainer_test.sh`
(`docs/ci.md` "Maintainer run"), which does every step below and writes the result file.

## Setup on the test machine (no sudo, no changes to the shared install)
`/opt/ai/comfyui` is owned by `aiuser`, but its python env (`/home/aiuser/opt2/anaconda3/envs/comfyui`)
is world-readable/executable, so a private instance runs fine as another user:

1. `~/oc_accept/base/custom_nodes/ComfyUI_OpenClip` <- the ref under test (`git archive <ref>`).
2. `start_private_comfyui.sh [noocio]` runs ComfyUI from `/opt/ai/comfyui` with `--base-directory ~/oc_accept/base --port 8199 --cpu`
   (own input/output/temp/user dirs, so nothing needs write access to `aiuser`'s folders; `$OC_ACCEPT` moves `~/oc_accept`).
   `noocio` shadows `PyOpenColorIO` to test the pack without OCIO.
3. `python run_acceptance.py --work ~/oc_accept/work --pack ~/oc_accept/base/custom_nodes/ComfyUI_OpenClip
   [--flame-clip <real .clip>] --server-log ~/oc_accept/server.log [--mode noocio] [--json results.json]`
   with the same env's python (needs OpenImageIO, numpy, lxml, torch). Without `--flame-clip` a
   synthetic Flame-like clip is built (`tests/flame_fixtures.py` of the installed pack).

## What it checks (2.0)

| Test | What |
|---|---|
| T1 | all 8 nodes registered, no import traceback in the server log, the 2.0 Writer sockets and 9 Reader outputs; `noocio`: Reader → Writer works and the colour nodes fail cleanly without OCIO |
| T4 | Reader → Writer with `clip_info` wired: `next` twice (v001, v002), cache hit on re-queue, cache invalidated by a re-rendered first frame, collision refused / `overwrite` replaces; AOV Writer chain → Writer → Reader → AOV Reader in both layouts, pixels and `clip_report` AOV list |
| T5 | EXR Sequence Reader on a Blender-style multilayer sequence: layer, AOV by name, `clip_report` in the ui output, `clip_info` drives a Writer to `<folder>/render.clip` |
| T6 | issue #7 live: Reader `clip_report` in the node's ui output; `OpenClipClipInfo` start timecode and colour outputs; new clip from `woods walk ` at 01:00:00:00 matches the source field by field (`tests/flame_compare.py`); append lands in the exact trailing-space file with every Flame element kept; different start TC on append and both-zero-TC-with-other-start-frame are refused with the clip unchanged; same real TC with another start frame only warns |

## Not covered here
Opening saved workflows in the browser UI, the in-node `clip_report` widget (web JS; the ui
payload it reads is checked), the 4K fp32 memory case, Flame itself (`docs/FLAME_TEST.md`).
