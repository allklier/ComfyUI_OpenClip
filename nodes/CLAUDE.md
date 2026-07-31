# nodes/ — ComfyUI Node Definitions

Each file in this directory defines one ComfyUI node class. Nodes are thin: they translate between ComfyUI's tensor/primitive types and the `lib/` functions. No business logic lives here.

## Node Inventory

### `OpenClipReader` — `reader.py`

Reads an OpenClip `.clip` XML package and outputs image tensors frame by frame (or as a batch). Also owns version selection/browsing — the former standalone `OpenClipVersionSelector` node was folded into the Reader (see **Implementation notes** below).

**Implementation notes:**
- `OUTPUT_NODE = True` so ComfyUI executes the Reader even when nothing downstream is connected — lets an artist queue-prompt the Reader in isolation just to browse a clip's versions, the way `OpenClipVersionSelector` used to work standalone.
- `version` accepts `"current"` (default) or an explicit typed version name (e.g. `v002`); an invalid name raises `ValueError` listing the valid ones (`openclip_xml.resolve_version`).
- The `available_versions` list is pushed into a read-only multiline widget on the node itself at execute time via `web/openclip_reader.js`, which listens for the node's `onExecuted` callback and writes the text into a `ComfyWidgets["STRING"]` widget (`serialize: false`, so it's never sent back to Python as an input). This is a different, more standard mechanism than the `widget.draw`/`mouse` override + custom button approach that broke `OpenClipVersionSelector` under a ComfyUI frontend rewrite (see git history), but it is still custom JS and carries some of the same forward-compat risk. The `available_versions` **output** is the guaranteed fallback if the JS ever stops populating the widget — wire it into any text-preview node.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `clip_path` | `STRING` | Path to a `.clip` file. Absolute paths are used as-is; relative paths resolve against ComfyUI's `input/` directory. |
| `version` | `STRING` | Version name to load (e.g. `v001`); `"current"` reads `currentVersion` attribute. Type any name shown in the in-node version list to switch versions. |
| `start_frame` | `INT` | First frame to load |
| `end_frame` | `INT` | Last frame to load; `-1` = all frames |
| `path_from` | `STRING` | Path prefix to replace in media paths from the XML (e.g. `/Volumes/LocalJobs`). Leave empty to let the reader auto-detect the remap. |
| `path_to` | `STRING` | Replacement prefix (e.g. `/mnt/LocalJobs`). Only applied when `path_from` is non-empty and matches. Ignored when auto-remap is active. |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch `(N, H, W, 3)` |
| `MASK` | `MASK` | Alpha tensor batch `(N, H, W)`, loaded automatically when the source has a 4th channel — zeros if no alpha in source |
| `frame_count` | `INT` | Number of frames loaded |
| `width` | `INT` | Frame width in pixels |
| `height` | `INT` | Frame height in pixels |
| `format_version` | `STRING` | OpenClip schema version detected (`"8"` or `"9"`) — the `version="…"` attribute on the root `<clip>` element, **not** a feed/version name like `v002`. |
| `version_name` | `STRING` | The **resolved** feed version actually loaded, e.g. `v002` — never the literal `"current"`, even when the `version` input was `"current"`. Wire into Writer's `version_name` if the Writer should follow the Reader's version numbering. |
| `start_frame` | `INT` | Actual first frame number used (resolved from span or explicit input) |
| `clip_path` | `STRING` | Pass-through of the resolved input clip path — wire into Writer's `clip_path` |
| `clip_name` | `STRING` | Clip name from the XML `<name>` element — wire into Writer's `clip_name` |
| `metadata` | `CLIP_METADATA` | Dict of EXR header attributes from the first frame — wire into Writer's `CLIP_METADATA` |
| `fps` | `FLOAT` | Frame rate read from the clip's `<editRate>` (one value per clip, shared by every version), as the exact rational value — e.g. the `23.976` label is really `24000/1001`, output as `23.976023976023978`, not the rounded decimal. Wire into Writer's `fps` to preserve it on round-trip. |
| `available_versions` | `STRING` | Newline-separated list of every version name in the clip, sorted, with `  (current)` appended to the one matching `currentVersion`. Also displayed live in-node via `web/openclip_reader.js`; this output is the no-JS fallback — wire it to any text-preview node if the in-node widget doesn't populate. |

**Typical wiring — full read → process → write round-trip:**
```
OpenClipReader.start_frame   → OpenClipWriter.start_frame   (preserves frame numbering)
OpenClipReader.clip_path     → OpenClipWriter.clip_path     (same destination folder)
OpenClipReader.clip_name     → OpenClipWriter.clip_name     (same clip name)
OpenClipReader.metadata      → OpenClipWriter.CLIP_METADATA (carry EXR header attrs)
OpenClipReader.fps           → OpenClipWriter.fps           (preserve frame rate)
OpenClipReader.version_name  → OpenClipWriter.version_name  (optional: Writer follows Reader's version numbering)
# Writer clip_filename stays at default "$(path)/$(clip_name)"
```

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
| `clip_filename` | `STRING` | Destination pattern; default `$(path)/$(clip_name).clip`. `$(path)` expands to `clip_path` (normalised to a folder); `$(clip_name)` expands to `clip_name`. Parentheses delimit the token explicitly, so literal text can follow directly with no ambiguity, e.g. `$(path)/$(clip_name)_clean.clip`. Supports `..` segments, e.g. `$(path)/../processed/$(clip_name).clip`. The `.clip` extension is stripped before passing the name to the layout builder, so the package is named correctly regardless. The resulting name is used for **both** the `.clip` file and the media sequence filenames (e.g. `$(clip_name)_clean` → `myshot_clean.clip` and `myshot_clean.0001.exr`); there's no way to rename only the `.clip` file. |
| `version_name` | `STRING` | Version label, e.g. `v001` |
| `include_version_in_filename` | `BOOLEAN` | If true, inserts `version_name` into the frame sequence filenames only, between `clip_name` and the frame counter (e.g. `myshot.v001.0001.exr`). Does **not** affect the `.clip` filename — that stays governed by `clip_filename`/`clip_name` alone, so multiple versions still merge into one `.clip` file via `_merge_version`. Default `False`. |
| `start_frame` | `INT` | Frame number for the first output file — wire from Reader's `start_frame` to preserve numbering |
| `frame_padding` | `INT` | Zero-padding width (default `4` → `%04d`) |
| `fps` | `FLOAT` | Frame rate (default `24.0`, `step 0.001`). Matched to the nearest supported rate (`23.976`, `24`, `25`, `29.97`, `30`, `48`, `50`, `59.94`, `60` — see `lib.openclip_xml.FPS_OPTIONS`) within a small tolerance via `fps_label_from_float()`, then written as that label into `<editRate>` and `<sampleRate>`. Raises `ValueError` if the value doesn't match any supported rate. The NTSC rates are exact rationals (e.g. `23.976` = `24000/1001`), so matching compares against the true rational value, not the rounded decimal label. |
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

### `OpenClipColourTransform` — `colour_transform.py`

Applies an OCIO Display/View transform (i.e. a real "view transform" like Flame's, not a bare colour-space-to-colour-space conversion) to an `IMAGE` tensor. See `lib/CLAUDE.md` for why this distinction matters.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch |
| `ocio_config` | `STRING` | Path to an `.ocio` config file. Defaults to `$OCIO` if set, else the most recent Flame install's `aces2.0_config` under `/opt/Autodesk/colour_mgmt/` (`_default_ocio_config()`). |
| `input_colour_space` | `STRING` | Source colour space name as registered in the config, e.g. `Log3G10 RedWideGamutRGB` |
| `view` | `STRING` | View transform name for the output display, e.g. `ACES 2.0 - SDR 100 nits (Rec.709)` (default). Matches Flame's per-clip view-transform picker; other values the config exposes (`Un-tone-mapped`, HDR views, etc.) work too. |
| `MASK` | `MASK` (optional) | Passed through unchanged; zero-filled if not connected |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | Transformed RGB tensor batch |
| `MASK` | `MASK` | Pass-through of the input `MASK` |

---
