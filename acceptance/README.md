# Acceptance run (live ComfyUI)

Drives the real nodes through a real ComfyUI server's HTTP API. Not part of `pytest`.

## Setup on akflame5 (no sudo, no changes to the shared install)
`/opt/ai/comfyui` is owned by `aiuser`, but its python env (`/home/aiuser/opt2/anaconda3/envs/comfyui`)
is world-readable/executable, so a private instance runs fine as `allklier`:

1. `~/oc_accept/base/custom_nodes/ComfyUI_OpenClip` <- the release under test (`git archive <tag>` + rsync).
2. `start_private_comfyui.sh [noocio]` runs ComfyUI from `/opt/ai/comfyui` with `--base-directory ~/oc_accept/base --port 8199 --cpu`
   (own input/output/temp/user dirs, so nothing needs write access to `aiuser`'s folders). `noocio` shadows `PyOpenColorIO` to test the pack without OCIO.
3. `python run_acceptance.py --work ~/oc_accept/work --flame-clip <real .clip> --server-log ~/oc_accept/server.log [--mode noocio]`
   using the same env's python (needs OpenImageIO + numpy).

## Not covered here
Opening saved workflows in the browser UI, the in-node `available_versions` widget (web JS), and
the 4K fp32 memory case. Real multi-part Flame AOV and real Blender renders weren't found on the box:
T5 uses a generated file with the exact channel layout of a Blender 5.2 multilayer render.
