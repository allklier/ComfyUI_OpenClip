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

### Module Layout

```
ComfyOpenClip/
├── __init__.py                 # Registers nodes with ComfyUI
├── nodes/                      # See nodes/CLAUDE.md
│   ├── CLAUDE.md
│   ├── reader.py               # OpenClipReader node class
│   ├── writer.py               # OpenClipWriter node class
│   └── version_selector.py     # OpenClipVersionSelector node class
├── lib/                        # See lib/CLAUDE.md
│   ├── CLAUDE.md
│   ├── openclip_xml.py         # Parse and generate .clip XML (v8 + v9)
│   ├── image_io.py             # Read/write EXR and PNG via OIIO
│   └── package_layout.py       # Resolve and build on-disk package structure
├── tests/                      # See tests/CLAUDE.md
│   ├── CLAUDE.md
│   ├── fixtures/
│   ├── test_openclip_xml.py
│   ├── test_image_io.py
│   ├── test_package_layout.py
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
