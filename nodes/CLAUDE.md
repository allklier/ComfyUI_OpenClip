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

---

### `OpenClipWriter` — `writer.py`

Writes a frame sequence and generates a valid OpenClip `.clip` XML package.

OpenClip XML is always written as version 8 (v9 is not yet released by Autodesk).

**Inputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch |
| `MASK` | `MASK` | Optional alpha; merged into EXR alpha channel if connected |
| `output_dir` | `STRING` | Root directory for the package |
| `clip_name` | `STRING` | Name of the clip (used for directory and file naming) |
| `version_name` | `STRING` | Version label, e.g. `v001` |
| `start_frame` | `INT` | Frame number for the first output file |
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

**Typical wiring:**
```
OpenClipVersionSelector.clip_path        → OpenClipReader.clip_path
OpenClipVersionSelector.selected_version → OpenClipReader.version
```
