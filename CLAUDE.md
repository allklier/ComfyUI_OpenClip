# CLAUDE.md — OpenClip Support for ComfyUI

## Solution Overview

To simplify integration of ComfyUI into VFX pipelines, a set of read/write nodes that are
based on the OpenClip standard makes renders and version management easier.

### Requirements

- Support the latest OpenClip standard by Autodesk (version 8 and 9)
- Support reading of PNG and OpenEXR sequences
- If possible include the JSON file of the ComfyUI workspace when writing a 'published' OpenClip
- All the controls existing for OpenClip in Flame (versioning, etc.)
- All the controls existing in current Read/Write nodes of ComfyUI (frame ranges, etc.)
- Ability to handle alpha channels
- Reader outputs start_frame, clip_path, and clip_name so they can be wired directly into the Writer for round-trip workflows without retyping
- Writer accepts a clip_filename pattern (`$(path)/$(clip_name)`) to compose the output destination from wired inputs with optional prefixes/suffixes
- EXR header metadata (tape name, timecodes, scene/take, etc.) extracted on read and optionally carried through to written files via a CLIP_METADATA output/input pair

## Architecture Decisions

### Design Choices

| Concern | Decision | Rationale |
|---|---|---|
| Workflow direction | Full round-trip (read + write) | Clips flow from Flame → ComfyUI for processing and back |
| OpenClip version compat | Detect on read; round-trip same version | Preserves schema fidelity; no silent upgrades |
| OpenClip write version | Always v8 | v9 not yet released by Autodesk |
| XML structure on write | Flame-compatible structure | `<trackType>`, `<editRate>`, full `<storageFormat>` with dimensions/depth, `<sampleRate>`, `<startTimecode>`, `<versions>` block |
| Package layout on write | Configurable: Standard Flame or Flat | Pipeline needs vary; Standard Flame safe for direct library scan |
| Alpha channel | Optional MASK input merged as RGBA EXR alpha | Matches ComfyUI's IMAGE/MASK tensor convention |
| Publish (workspace JSON) | Sidecar `.comfy.json` + path reference in XML | Inspectable on disk; mirrors Flame "publish" concept for artists |
| Image I/O | OpenImageIO (OIIO) | Handles EXR and PNG in one library; full multi-part and deep EXR support |
| Distribution | ComfyUI Manager custom node pack | One-click install; still works as manual copy to `custom_nodes/` |
| Round-trip wiring | Reader outputs start_frame, clip_path, clip_name, CLIP_METADATA, fps | Direct connections to Writer avoid retyping and prevent numbering drift |
| Writer destination | `clip_path` + `clip_name` + `clip_filename` pattern replaces flat `output_dir` | `$(path)`/`$(clip_name)` tokens allow suffixes without breaking wired connections |
| `clip_filename` token syntax | Explicit parentheses: `$(path)`, `$(clip_name)` (not bare `$path`/`$clip_name`) | Prevents adjacent literal text (e.g. `$(clip_name)_clean`) from being mistaken for part of the token name if more tokens are added later |
| `clip_filename` scope | Same expanded name is used for the `.clip` file *and* the media sequence filenames | Both come from `package_layout.build(clip_name=...)`; there's no way to rename just the `.clip` file independently of the frames |
| EXR metadata carry | CLIP_METADATA type (Python dict of OIIO extra_attribs) | Optional; only applied when input is connected; writer format attrs always win |
| Version in frame filenames | `include_version_in_filename` checkbox (not a `clip_filename` token) | A `$(version)` token in `clip_filename` would change the `.clip` filename per version too (since that name is shared, see `clip_filename` scope above), breaking multi-version merge into one `.clip` file; a separate boolean keeps the `.clip` filename stable while still disambiguating frames on disk (`Flat` layout reuses one directory across versions with no other disambiguator) and matches Flame's own `name.version.####.ext` convention |
| FPS type on Reader/Writer | `FLOAT`, not `COMBO`/`STRING` | Lets other ComfyUI nodes (which commonly use plain `FLOAT` for fps) wire directly; NTSC rates (23.976, 29.97, 59.94) are exact rationals internally (e.g. 24000/1001), so Reader outputs the true rational value and Writer matches an incoming float to the nearest supported rate within tolerance (`openclip_xml.fps_label_from_float`) rather than doing exact/decimal comparison |
| Reader schema-version output naming | `format_version` (was `clip_version`) | The old name collided conceptually with `OpenClipVersionSelector`'s `selected_version`/`current_version` (feed versions like `v002`), even though this output is the OpenClip **schema** version (`"8"`/`"9"`, the root `<clip version="…">` attribute) — an artist saw `format_version="9"` next to a selected feed version and assumed it was a bug |
| VersionSelector version listing | Python `available_versions` STRING output (newline list, `currentVersion` marked), not a JS-driven UI | The prior design used a litegraph "↻ Refresh & Check" button plus a custom-drawn read-only `latest_version` widget (`widget.draw`/`widget.mouse` overrides) that fetched `/openclip/versions`. This broke silently (button click did nothing) under a newer ComfyUI node-canvas frontend, because those overrides depend on litegraph's internal widget contract, which a canvas rewrite has no obligation to preserve. Replaced with a plain execute-time output with no custom JS — wire it to any text-preview node. The `/openclip/versions` route and `web/openclip_version_selector.js` were removed as now-unused. |

### Module Layout

```
ComfyUI_OpenClip/
├── __init__.py                 # Registers nodes with ComfyUI
├── nodes/                      # See nodes/CLAUDE.md
│   ├── CLAUDE.md
│   ├── reader.py               # OpenClipReader node class
│   ├── writer.py               # OpenClipWriter node class
│   ├── version_selector.py     # OpenClipVersionSelector node class
│   ├── colour_transform.py     # OpenClipColourTransform node class
│   └── _paths.py               # Shared clip path resolution helpers
├── lib/                        # See lib/CLAUDE.md
│   ├── CLAUDE.md
│   ├── openclip_xml.py         # Parse and generate .clip XML (v8 + v9)
│   ├── image_io.py             # Read/write EXR and PNG via OIIO
│   ├── package_layout.py       # Resolve and build on-disk package structure
│   └── colour_transform.py     # Apply OCIO colour space transforms
├── tests/                      # See tests/CLAUDE.md
│   ├── CLAUDE.md
│   ├── fixtures/
│   ├── test_openclip_xml.py
│   ├── test_image_io.py
│   ├── test_package_layout.py
│   ├── test_paths.py
│   ├── test_colour_transform.py
│   └── test_system.py
├── pyproject.toml
└── requirements.txt
```

### Dependencies

| Package | Purpose |
|---|---|
| `OpenImageIO` (OIIO) | Read/write EXR and PNG sequences |
| `torch` | Tensor I/O (provided by ComfyUI environment) |
| `lxml` | Robust XML parse and serialise for `.clip` files |
| `OpenColorIO` (OCIO) | Colour space transforms for the OpenClip Colour Transform node |
