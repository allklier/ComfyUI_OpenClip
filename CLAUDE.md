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
- Reader outputs start_frame, clip_path, clip_name, and version_name so they can be wired directly into the Writer for round-trip workflows without retyping
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
| Reader schema-version output naming | `format_version` (was `clip_version`) | The old name collided conceptually with the (now removed) `OpenClipVersionSelector`'s `selected_version`/`current_version` (feed versions like `v002`), even though this output is the OpenClip **schema** version (`"8"`/`"9"`, the root `<clip version="…">` attribute) — an artist saw `format_version="9"` next to a selected feed version and assumed it was a bug |
| Version selection lives on Reader, not a separate node | `OpenClipVersionSelector` removed; Reader gained `OUTPUT_NODE = True`, a `version_name` output (resolved actual version, e.g. `v002` — never the literal `"current"`), and kept the `available_versions` STRING output | One fewer node to wire for the common case (browse versions, then load one); `OUTPUT_NODE = True` lets an artist queue-prompt the Reader alone to inspect a clip's versions without wiring anything downstream, the way `OpenClipVersionSelector` worked standalone. Writer already had a `version_name` input, so `Reader.version_name → Writer.version_name` lets the Writer follow the Reader's numbering on request. Breaking change: any saved workflow JSON still containing an `OpenClipVersionSelector` node shows as a missing node when reopened — accepted as a one-time cost since this is early-stage, single-pipeline tooling. |
| In-node version list display | `web/openclip_reader.js` writes `available_versions` into a read-only multiline widget via the node's `onExecuted` callback, **plus** `available_versions` stays a normal STRING output | Satisfies "see the list without a separate preview node" while keeping a zero-JS fallback. This is a different mechanism than what broke `OpenClipVersionSelector` before (that used `widget.draw`/`mouse` overrides + a custom button + a custom `/openclip/versions` API route); this instead uses the same execute-time `onExecuted(message)` hook that ComfyUI's own image-preview nodes rely on. Still custom JS, still carries some risk on a future frontend rewrite — hence the output fallback is mandatory, not optional, per explicit user decision. |
| Reader `load_alpha` toggle | Removed; Reader always loads the alpha channel into MASK when the source has one | An artist hit a silent black-mask bug from leaving the (default-off) toggle unchecked on an RGBA source. `image_io.read_sequence` already reads all channels regardless of the flag and only slices differently, so there is no I/O cost to always attempting alpha; a 3-channel source still yields a zero-filled MASK exactly as before. The underlying `image_io.read_sequence(..., load_alpha: bool)` parameter is kept — it's a reasonable lib-level control and existing tests exercise both branches directly. |
| Reader cache invalidation | `OpenClipReader.IS_CHANGED()` hashes the `.clip` file's + first resolved frame's `(mtime_ns, size)` | ComfyUI only re-executes a node when its `IS_CHANGED()` classmethod returns a different value than last run (confirmed against ComfyUI's `execution.py`/`comfy_execution/caching.py`); without it, an artist re-rendering a clip on disk with the same `clip_path`/`version`/frame range saw a stale cached image until a server restart. Deliberately checks only the `.clip` file and the first frame, not every frame in the range, to keep the check cheap on long sequences — a re-render that changes only a middle/late frame without touching the `.clip` file or frame 1 (e.g. re-rendering a handful of flagged frames on a farm) would not be detected; explicit user decision to accept that gap for the cheaper check. |
| Colour transform pipeline | `apply_colour_transform()` uses `ocio.DisplayViewTransform(src, display, view)` instead of `config.getProcessor(input_space, output_space)` | The plain-`getProcessor` form connects directly to a *display-referred* colour space, which in an OCIO v2/ACES config is encoding-only (EOTF + primaries) — it skips the View's ACES Output Transform (tone-mapping/gamut compression) entirely. Confirmed against the Academy's public reference ACES 2.0 config (same one Flame's `aces2.0_config` ships unmodified): a saturated red came out with a negative channel and a value above `1.0` via the old path, vs. correctly compressed into range via `DisplayViewTransform`. This matched the reported symptom exactly (bad reds; a downstream model hallucinating on out-of-range pixel values) and matched why bypassing the node with Flame's own view-transformed render worked. Added a new `view` node input (default `"ACES 2.0 - SDR 100 nits (Rec.709)"`, `lib.colour_transform.OUTPUT_VIEW`) rather than hardcoding a view, so an artist can pick alternate views (`Un-tone-mapped`, HDR, etc.) the way Flame lets them pick a view transform — explicit user decision over auto-picking the config's default view. Breaking change: saved workflows need the new `view` input filled in, same one-time cost as the `load_alpha`/`OpenClipVersionSelector` changes. |

### Module Layout

```
ComfyUI_OpenClip/
├── __init__.py                 # Registers nodes with ComfyUI
├── nodes/                      # See nodes/CLAUDE.md
│   ├── CLAUDE.md
│   ├── reader.py               # OpenClipReader node class
│   ├── writer.py               # OpenClipWriter node class
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
