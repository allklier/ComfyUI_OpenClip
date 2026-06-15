# nodes/ — ComfyUI Node Definitions

Each file in this directory defines one ComfyUI node class. Nodes are thin: they translate between ComfyUI's tensor/primitive types and the `lib/` functions. No business logic lives here.

## Node Inventory

### `OpenClipReader` — `reader.py`

Reads an OpenClip `.clip` XML package and outputs image tensors frame by frame (or as a batch).

**Inputs**

| Name | Type | Description |
|---|---|---|
| `clip_path` | `STRING` | Path to a `.clip` file. Absolute paths are used as-is; relative paths resolve against ComfyUI's `input/` directory. |
| `version` | `STRING` | Version name to load (e.g. `v001`); `"current"` reads `currentVersion` attribute |
| `start_frame` | `INT` | First frame to load |
| `end_frame` | `INT` | Last frame to load; `-1` = all frames |
| `load_alpha` | `BOOLEAN` | If true, split RGBA EXR into IMAGE + MASK outputs |
| `path_from` | `STRING` | Path prefix to replace in media paths from the XML (e.g. `/Volumes/LocalJobs`). Leave empty to let the reader auto-detect the remap. |
| `path_to` | `STRING` | Replacement prefix (e.g. `/mnt/LocalJobs`). Only applied when `path_from` is non-empty and matches. Ignored when auto-remap is active. |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch `(N, H, W, 3)` |
| `MASK` | `MASK` | Alpha tensor batch `(N, H, W)` — zeros if no alpha in source |
| `frame_count` | `INT` | Number of frames loaded |
| `width` | `INT` | Frame width in pixels |
| `height` | `INT` | Frame height in pixels |
| `clip_version` | `STRING` | OpenClip schema version detected (`"8"` or `"9"`) |
| `start_frame` | `INT` | Actual first frame number used (resolved from span or explicit input) |
| `clip_path` | `STRING` | Pass-through of the resolved input clip path — wire into Writer's `clip_path` |
| `clip_name` | `STRING` | Clip name from the XML `<name>` element — wire into Writer's `clip_name` |
| `metadata` | `CLIP_METADATA` | Dict of EXR header attributes from the first frame — wire into Writer's `CLIP_METADATA` |

---

### `OpenClipWriter` — `writer.py`

Writes a frame sequence and generates a valid OpenClip `.clip` XML package.

OpenClip XML is always written as version 8 (v9 is not yet released by Autodesk).

**Inputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch |
| `MASK` | `MASK` | Optional alpha; merged into EXR alpha channel if connected |
| `CLIP_METADATA` | `CLIP_METADATA` | Optional EXR header metadata to write into output frames (e.g. tape name, timecodes). Only applied when connected; writer format settings (compression, etc.) always override any matching keys. |
| `clip_path` | `STRING` | Folder path for the output package. If a `.clip` file path is supplied (e.g. wired from Reader), the parent directory is used automatically. |
| `clip_name` | `STRING` | Clip name used for directory and file naming. Can be wired from Reader's `clip_name` output. |
| `clip_filename` | `STRING` | Destination pattern; default `$path/$clip_name.clip`. `$path` expands to `clip_path` (normalised to a folder); `$clip_name` expands to `clip_name`. Supports `..` segments, e.g. `$path/../processed/$clip_name.clip`. The `.clip` extension is stripped before passing the name to the layout builder, so the package is named correctly regardless. |
| `version_name` | `STRING` | Version label, e.g. `v001` |
| `start_frame` | `INT` | Frame number for the first output file — wire from Reader's `start_frame` to preserve numbering |
| `frame_padding` | `INT` | Zero-padding width (default `4` → `%04d`) |
| `fps` | `COMBO` | Frame rate: `23.976`, `24`, `25`, `29.97`, `30`, `48`, `50`, `59.94`, `60` — written into `<editRate>` and `<sampleRate>` |
| `file_format` | `COMBO` | `EXR` or `PNG` |
| `exr_bit_depth` | `COMBO` | `half (16-bit)` or `float (32-bit)` — EXR only |
| `exr_compression` | `COMBO` | `ZIP`, `PIZ`, `DWAB` — EXR only |
| `layout` | `COMBO` | `Standard Flame` or `Flat` — see `lib/CLAUDE.md` |
| `publish` | `BOOLEAN` | Write ComfyUI workspace JSON as `<clip_name>.<version_name>.comfy.json` in the version's media folder and reference it inside the version's `<userData>` in the XML |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `clip_path` | `STRING` | Absolute path to the written `.clip` file |

---

### `OpenClipVersionSelector` — `version_selector.py`

Inspects a `.clip` file and lets an artist pick a version interactively. Intended to be wired upstream of `OpenClipReader`.

**Implementation notes:**
- `selected_version` is a `STRING` (not `COMBO`) — `COMBO` caused ComfyUI backend validation to reject submitted values that weren't in the static INPUT_TYPES list.
- `latest_version` is a `STRING` that exists as an INPUT_TYPES entry so it is serialized with the graph, but Python **ignores its value**. The JS `_makeReadOnly()` helper disables editing and overrides the draw method to render it as a non-interactive label.
- The JS **↻ Refresh & Check** button calls `/openclip/versions?path=<clip_path>`, updates `latest_version` to the clip's `currentVersion`, and shows an `alert()` if `selected_version` is non-empty but not found. Python also raises `ValueError` at execution time on an invalid version.
- An empty `selected_version` silently falls back to `currentVersion` in Python.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `clip_path` | `STRING` | Path to a `.clip` file. Absolute paths are used as-is; relative paths resolve against ComfyUI's `input/` directory. |
| `selected_version` | `STRING` | Editable field for the desired version name (e.g. `v002`). Empty → uses `currentVersion`. |
| `latest_version` | `STRING` | Display-only. Updated by the Refresh & Check button to show `currentVersion` from the XML. Not used by Python. |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `clip_path` | `STRING` | Pass-through of the input path (for chaining directly into Reader) |
| `selected_version` | `STRING` | The version chosen (or `currentVersion` if input was empty) |
| `current_version` | `STRING` | The version marked as `currentVersion` in the XML |

**Typical wiring — version selection into Reader:**
```
OpenClipVersionSelector.clip_path        → OpenClipReader.clip_path
OpenClipVersionSelector.selected_version → OpenClipReader.version
```

**Typical wiring — full read → process → write round-trip:**
```
OpenClipReader.start_frame → OpenClipWriter.start_frame   (preserves frame numbering)
OpenClipReader.clip_path   → OpenClipWriter.clip_path     (same destination folder)
OpenClipReader.clip_name   → OpenClipWriter.clip_name     (same clip name)
OpenClipReader.metadata    → OpenClipWriter.CLIP_METADATA (carry EXR header attrs)
# Writer clip_filename stays at default "$path/$clip_name"
# Change version_name on the Writer to add a new version to the same clip
```
