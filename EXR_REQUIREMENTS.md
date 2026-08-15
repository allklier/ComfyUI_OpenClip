EXR / AOV Support Requirements for ComfyUI_OpenClip

This document defines required enhancements to support high-fidelity EXR round-trips
for the ComfyUI Harmonize pipeline (see parent project `ComfyUI_Harmonize/CLAUDE.md`).

Goals
- Preserve linear float precision (fp16/fp32) end-to-end, avoid implicit clamping/tonemapping.
- Expose and preserve AOV channels so node graphs can operate on normals, depth, albedo, roughness, masks.
- Correctly tag color spaces (support ACEScg) and allow opt-in conversion for preview only.

Requirements
1) Read/Write behavior
  - Read multi-layer EXR and expose each layer as a named channel or virtual file.
  - Write multi-layer EXR preserving original channel types and ordering.
  - Do not clamp or tonemap float data by default; provide an explicit preview/tonemap toggle.

2) Numeric precision
  - Preserve fp16/fp32 when reading/writing; avoid converting to 8-bit or sRGB unless requested.
  - API should allow consumer nodes to request a target dtype (fp32, fp16) when loading into GPU memory.

3) AOV enumeration API
  - Provide a function to list available AOV names in a file.
  - Allow reading a specific AOV into a numpy array or GPU-backed tensor with metadata (colorspace, data type).

4) Color-space and metadata
  - Preserve EXR metadata and add/recognize common tags (e.g., ACEScg, scene-referred, primaries).
  - Provide helper utilities to annotate files with ACEScg or other scene-linear tags when writing.

5) Integration into ComfyUI
  - Expose nodes or node helper functions that return AOV lists and load chosen AOVs into the graph.
  - Ensure compatible interfaces with `EXRIONode` expectations from ComfyUI_Harmonize.

6) Tests and examples
  - Add unit tests that round-trip a synthetic EXR with extra channels and verify content/metadata preserved.
  - Provide an example read/write script and link to `ComfyUI_Harmonize/tests/generate_synthetic_exr.py`.

7) Performance
  - Support lazy loading / tiled reading for large EXRs where possible.
  - Expose GPU upload helpers to avoid unnecessary CPU copies.

Acceptance criteria
- New branch `enhance/exr-aov-support` includes `EXR_REQUIREMENTS.md`.
- PR demonstrates a simple read/write example and references the parent `ComfyUI_Harmonize` design doc.

