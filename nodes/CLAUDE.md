# nodes/ — ComfyUI Node Definitions

Each file in this directory defines one ComfyUI node class. Nodes are thin: they translate between ComfyUI's tensor/primitive types and the `lib/` functions. No business logic lives here.

## Node Inventory

### `OpenClipReader` — `reader.py`

Reads an OpenClip `.clip` XML package and outputs image tensors frame by frame (or as a batch). Also owns version selection/browsing — the former standalone `OpenClipVersionSelector` node was folded into the Reader (see **Implementation notes** below).

**Implementation notes:**
- `OUTPUT_NODE = True` so ComfyUI executes the Reader even when nothing downstream is connected — lets an artist queue-prompt the Reader in isolation just to browse a clip's versions, the way `OpenClipVersionSelector` used to work standalone.
- **`IS_CHANGED` must never raise, and must tolerate `None` on every input.** ComfyUI builds its arguments with `get_input_data(..., execution_list=None)`, whose `mark_missing()` substitutes `None` for every input driven by a *link* rather than a widget — deliberately, so `IS_CHANGED` only ever sees constants. If it raises, `execution.py` logs a warning and substitutes `float("NaN")` as the node's change signature; NaN never compares equal to itself, so the node re-executes on every queue — and because `caching.py`'s `CacheKeySetInputSignature.get_node_signature()` folds each node's signature into every descendant's, one raising `IS_CHANGED` here defeats the cache for the **entire downstream graph**. This was a real production bug (2026-08-24): a text node feeding `clip_path` made it arrive as `None`, `.strip()` raised `AttributeError`, and a Harmonize workflow re-ran Lotus and the whole Module A relight on every queue. The contract it implements is: **inputs changed → ComfyUI handles it** (a linked value is in the cache key via the ancestry walk); **files changed on disk under an unchanged graph → IS_CHANGED reports it**; neither → stable value, cache reused.

Knowing *which* files to stat is the awkward part when `clip_path` is linked. It cannot be recovered from the arguments, and `dynprompt` is `None` at this call site too, so even a hidden `PROMPT` input arrives as `{}`. **`UNIQUE_ID` is delivered, though**, so the node keeps a process-local `_LAST_READ_PLAN[unique_id]` recorded by `execute()`, and `IS_CHANGED` stats those files when it can't resolve its own. That covers the case that actually bites: nothing in the graph moved, but the artist rendered a new clip version (real symptom, 2026-08-24: "the clip is stuck on v01 when there is now a v04"). Cost is one extra read on the first queue after a restart, when the memory is empty.
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
| `frame_cap` | `INT` | Caps the read to at most this many frames from the resolved start (the clip's own native start when `start_frame` is `-1`) — independent of each clip's own frame numbering, which varies (Flame commonly starts at 1001, but not always). `-1` (default) disables the cap. Appended after `path_to`, not placed next to `start_frame`/`end_frame` where it semantically belongs, so existing saved workflows' positional `widgets_values` stay valid. |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch `(N, H, W, 3)` |
| `MASK` | `MASK` | Alpha tensor batch `(N, H, W)`, loaded automatically when the source has a 4th channel — fully opaque (all-ones) if no alpha in source, not zeros (standard VFX convention: absent alpha means no matte, not "nothing here" — corrected 2026-09-23, see `lib/CLAUDE.md` "Alpha Channel Handling") |
| `frame_count` | `INT` | Number of frames loaded |
| `width` | `INT` | Frame width in pixels |
| `height` | `INT` | Frame height in pixels |
| `format_version` | `STRING` | OpenClip schema version detected (e.g. `"6"`–`"9"`; wire into Writer's `format_version` to write a new clip at the same schema) — the `version="…"` attribute on the root `<clip>` element, **not** a feed/version name like `v002`. |
| `version_name` | `STRING` | The **resolved** feed version actually loaded, e.g. `v002` — never the literal `"current"`, even when the `version` input was `"current"`. Wire into Writer's `version_name` if the Writer should follow the Reader's version numbering. |
| `start_frame` | `INT` | Actual first frame number used (resolved from span or explicit input) |
| `clip_path` | `STRING` | Pass-through of the resolved input clip path — wire into Writer's `clip_path` |
| `clip_name` | `STRING` | Clip name from the XML `<name>` element — wire into Writer's `clip_name` |
| `metadata` | `CLIP_METADATA` | Dict of EXR header attributes from the first frame — wire into Writer's `CLIP_METADATA` |
| `aov_handle` | `AOV_HANDLE` | Lazy handle for AOV extraction — locates the file(s), decodes nothing. Wire into one `OpenClipAOVReader` per AOV you actually want; a plain beauty-only clip costs nothing extra since nothing decodes until an `OpenClipAOVReader` asks for a name. See `OpenClipAOVReader` below. |
| `colour_space` | `STRING` | Colour-space/transfer-function identity from the EXR header (e.g. `ACEScg`, a camera log profile name), empty string if the source has none. Prefers a project-namespaced backup attribute over the raw `oiio:ColorSpace` key — see `lib/CLAUDE.md` "Colour-Space Metadata" for why. |
| `fps` | `FLOAT` | Frame rate read from the clip's `<editRate>` (one value per clip, shared by every version), as the exact rational value — e.g. the `23.976` label is really `24000/1001`, output as `23.976023976023978`, not the rounded decimal. Wire into Writer's `fps` to preserve it on round-trip. |
| `available_versions` | `STRING` | Newline-separated list of every version name in the clip, sorted, with `  (current)` appended to the one matching `currentVersion`. Also displayed live in-node via `web/openclip_reader.js`; this output is the no-JS fallback — wire it to any text-preview node if the in-node widget doesn't populate. |
| `available_aovs` | `STRING` | Newline-separated list of every named EXR part found beyond subimage 0 (beauty), for one representative frame — header-only, nothing is pixel-decoded to build this list. Copy a name from here into an `OpenClipAOVReader`'s `part_name` (no dropdown is possible — ComfyUI gives a node no way to write another node's widget value — same mechanism as `available_versions`). Also displayed live in-node via `web/openclip_reader.js`. Also lists `Separate Files` AOVs (sibling `<stem>_AOV_*` files, with their original names recovered from the file's part `name`), so `Reader → OpenClipAOVReader` works for both `aov_layout` values (2026-10-24). Empty string for a plain beauty-only clip. |

**AOVs are a separate, generic mechanism as of 2026-09-23** — see `OpenClipAOVReader`/`OpenClipAOVWriter` below. This replaced a fixed three-AOV interface (`NORMAL`/`NORMAL_WORLD`/`DEPTH`/`NORMAL_RAW` outputs, `depth_layer_name`/`normal_world_layer_name`/`normal_raw_layer_name` widgets) that had two problems: every Reader instance carried those three widgets whether or not the clip had any AOVs at all, and adding a new AOV meant changing the Reader's own interface. Breaking change, made deliberately (explicit user decision, 2026-09-23) rather than kept alongside the old interface — AOVs had only been used in development/testing, nothing released or in production, so there was nothing worth preserving compatibility for.

**Typical wiring — full read → process → write round-trip:**
```
OpenClipReader.start_frame   → OpenClipWriter.start_frame   (preserves frame numbering)
OpenClipReader.clip_path     → OpenClipWriter.clip_path     (same destination folder)
OpenClipReader.clip_name     → OpenClipWriter.clip_name     (same clip name)
OpenClipReader.metadata      → OpenClipWriter.CLIP_METADATA (carry EXR header attrs, incl. colour space)
OpenClipReader.fps           → OpenClipWriter.fps           (preserve frame rate)
OpenClipReader.version_name  → OpenClipWriter.version_name  (optional: Writer follows Reader's version numbering)
OpenClipReader.aov_handle    → OpenClipAOVReader.aov_handle (one instance per AOV; chain OpenClipAOVWriter instances into OpenClipWriter.aov_chain to write them back out)
# Writer clip_filename stays at default "$(path)/$(clip_name)"
```

Leave Writer's `version_name` at its default `"next"` (don't wire `Reader.version_name` in) if each run should land as a new version. Wiring `Reader.version_name` in means re-running the same version collides on the next execute — set Writer's `overwrite` to `True` if that re-processing is intentional.

---

### `OpenClipWriter` — `writer.py`

Writes a frame sequence and generates a valid OpenClip `.clip` XML package.

A new `.clip` is written as schema `version="6"` by default (set by `format_version`); appending to an existing `.clip` edits it in place and keeps its own schema version and all of Flame's metadata — see `CLAUDE.md`'s design-decisions table (issue #4).

**Inputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch |
| `MASK` | `MASK` | Optional alpha; merged into EXR alpha channel if connected |
| `CLIP_METADATA` | `CLIP_METADATA` | Optional EXR header metadata to write into output frames (e.g. tape name, timecodes). Only applied when connected; writer format settings (compression, etc.) always override any matching keys. |
| `aov_chain` | `AOV_WRITE_CHAIN` | Optional. Feed the last `OpenClipAOVWriter` in a daisy-chain here — see `OpenClipAOVWriter` below. Each entry becomes a real named EXR part/subimage (or a separate sibling file, depending on `aov_layout`), not a channel-name convention. |
| `format_version` | `STRING` | Optional, default `"6"` (added 2026-10-27, issue #4). Root `<clip version="…">` for a **new** `.clip`; allowed `5`–`9`, anything else raises before anything is written. Wire `OpenClipReader.format_version` in to match a source clip. Ignored (with a printed warning if it differs) when appending to an existing `.clip`, which keeps its own version. Last optional input, so saved workflows' widget order is unchanged. |
| `colour_space` | `STRING` | Optional, default `"Rec.1886 Rec.709 - Display"` (added 2026-10-04 — previously a bare Python default, unwireable from any reader). Sets the `.clip` XML's own declared format colour space (`ClipFormat.colour_space`). This is **not** the same pipe as the colour-space round trip that already works via `CLIP_METADATA`'s `oiio:ColorSpace`/`openclip:ColorSpace` EXR header keys (see `CLAUDE.md`'s design-decisions table) — nothing currently reads `ClipFormat.colour_space` back out on the Reader side. |
| `aov_layout` | `COMBO` | `Multichannel` (default) writes each `aov_chain` entry as its own named subimage in the same file as beauty — a true multi-part EXR, matching the real-world convention (see `lib/CLAUDE.md` "AOV Channel Handling"), not the old single-part-many-channels scheme. `Separate Files` writes one extra `<clip_name>_AOV_<name>.####.ext` sibling file per entry instead, `name` taken directly from the chain entry (sanitized for filesystem safety) rather than a fixed suffix table. A non-empty `aov_chain` with `file_format="PNG"` raises `ValueError` — PNG's 8-bit path would silently corrupt AOV data. |
| `clip_path` | `STRING` | Folder path for the output package. If a `.clip` file path is supplied (e.g. wired from Reader), the parent directory is used automatically. |
| `clip_name` | `STRING` | Clip name used for directory and file naming. Can be wired from Reader's `clip_name` output. |
| `clip_filename` | `STRING` | Destination pattern; default `$(path)/$(clip_name).clip`. `$(path)` expands to `clip_path` (normalised to a folder); `$(clip_name)` expands to `clip_name`. Parentheses delimit the token explicitly, so literal text can follow directly with no ambiguity, e.g. `$(path)/$(clip_name)_clean.clip`. Supports `..` segments, e.g. `$(path)/../processed/$(clip_name).clip`. The `.clip` extension is stripped before passing the name to the layout builder, so the package is named correctly regardless. The resulting name is used for **both** the `.clip` file and the media sequence filenames (e.g. `$(clip_name)_clean` → `myshot_clean.clip` and `myshot_clean.0001.exr`); there's no way to rename only the `.clip` file. |
| `version_name` | `STRING` | Version label, e.g. `v001`. Default `"next"` — a Writer-only sentinel (mirrors the Reader's `"current"`) resolved via `openclip_xml.next_version()`: reads the existing `.clip`, returns one past the highest `v<NNN>` version present, or `v001` if the clip doesn't exist yet. A brand-new Writer just works with no typing; wire `Reader.version_name` in, or type an explicit name, to opt out of auto-selection. |
| `overwrite` | `BOOLEAN` | Default `False`. If the resolved `version_name` already exists in the `.clip` and this is `False`, raises `ValueError` (with a `VERSION ALREADY EXISTS` banner and a fix-it list) instead of writing. If `True`, deletes that version's existing frame files first (so a shorter re-render doesn't leave stale trailing frames), then overwrites the frames and the XML `<version>`/`<feed>` entry. Never triggers when `version_name` resolved from `"next"`, since that's computed to not collide. |
| `include_version_in_filename` | `BOOLEAN` | If true, inserts `version_name` into the frame sequence filenames only, between `clip_name` and the frame counter (e.g. `myshot.v001.0001.exr`). Does **not** affect the `.clip` filename — that stays governed by `clip_filename`/`clip_name` alone, so multiple versions still merge into one `.clip` file via `_merge_version`. Default `False`. |
| `start_frame` | `INT` | Frame number for the first output file — wire from Reader's `start_frame` to preserve numbering |
| `frame_padding` | `INT` | Zero-padding width (default `4` → `%04d`) |
| `fps` | `FLOAT` | Frame rate (default `24.0`, `step 0.001`). Matched to the nearest supported rate (`23.976`, `24`, `25`, `29.97`, `30`, `48`, `50`, `59.94`, `60` — see `lib.openclip_xml.FPS_OPTIONS`) within a small tolerance via `fps_label_from_float()`, then written as that label into `<editRate>` and `<sampleRate>`. Raises `ValueError` (including for NaN/inf) if the value doesn't match any supported rate. Checked, together with the existing-clip format check (width/height/fps/file_format), *before* any directory or frame is written, so a refused run leaves nothing on disk. The NTSC rates are exact rationals (e.g. `23.976` = `24000/1001`), so matching compares against the true rational value, not the rounded decimal label. |
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

### `OpenClipAOVReader` — `aov_reader.py`

Extracts one named EXR part from an `OpenClipReader`'s `aov_handle`. Generic and lazy: `aov_handle` only locates the file(s), and nothing is pixel-decoded until this node executes and only for the one part named `part_name` — an unrequested part (e.g. a large Cryptomatte) is never touched. One instance per AOV you actually want; a clip with no AOVs costs zero of these.

**Implementation notes:**
- Same `IS_CHANGED` contract and reasoning as `OpenClipReader` — `aov_handle` is a linked input, so it arrives as `None` inside `IS_CHANGED`, and the node keeps its own process-local `_LAST_READ_PLAN[unique_id]` to fingerprint the file it actually read last time. Must never raise, for the same cache-poisoning reason as the Reader.
- `part_name` is typed by hand, copied from `OpenClipReader.available_aovs` — no dropdown is possible.
- Output shape follows one rule: a part with exactly 1 channel goes entirely to `matte`, `image` is `None` (nothing meaningfully multi-channel to return). A part with >=2 channels populates both — `image` is all of them, `matte` is the channel literally named `"R"` if present (looked up by name, never assumed positionally — this project's channel-by-name discipline), else channel 0. Real-world depth-like parts are a replicated scalar across R/G/B (confirmed against a real Flame render) — 3 channels on disk, so `image` *is* populated in that case, `matte` just happens to equal every channel of it.
- Both outputs are `None`, not a zero-filled placeholder, if `part_name` isn't found in any frame of the handle's range — same "don't fabricate an absent value" contract the rest of this project's AOV handling already uses.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `aov_handle` | `AOV_HANDLE` | From `OpenClipReader` |
| `part_name` | `STRING` | EXR part `name` to extract — copy from `OpenClipReader.available_aovs` |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `image` | `IMAGE` | All of the part's channels, batch `(N, H, W, C)` — `None` if the part is single-channel, or not found |
| `matte` | `MASK` | The `"R"` channel (or the sole channel, if single-channel), batch `(N, H, W)` — `None` if not found |

---

### `OpenClipExrSequenceReader` — `exr_sequence_reader.py`

Reads a plain EXR frame sequence with no `.clip` wrapper (Blender, Nuke). Only outputs what a plain sequence actually has (13, not `OpenClipReader`'s 16 — see `CLAUDE.md`'s design-decisions table, "outputs reversed 2026-10-04"): no `format_version`/`version_name`/`available_versions`, since there's no version concept here. `IMAGE`/`MASK` stay at the same slots (0/1) as `OpenClipReader` so it can stand in for it behind `HM_ImageSwitch`/`HM_MaskSwitch`; `aov_handle` still feeds `OpenClipAOVReader` the same way, just from a different slot index.

**Implementation notes:**
- `sequence_path` takes `name.####.exr` (any number of `#`) or `name.%04d.exr`; relative paths resolve against the input directory, like `clip_path`. `start_frame`/`end_frame` of `-1` mean the first/last frame found on disk; `frame_cap` works as on `OpenClipReader`.
- `layer` picks the beauty from a single-part multilayer EXR by prefix (`CamelMatte.Combined` reads `CamelMatte.Combined.R/G/B/A`). Blank reads plain `R/G/B/A`. A wrong or missing layer raises with the file's layer list.
- `clip_path`/`clip_name` are genuinely derived, not placeholders: `clip_path` is the sequence's folder and `clip_name` the filename stem before the frame token.
- `fps` is a widget (Blender writes none into the EXR). A blank `colour_space` widget falls back to the header, the same way `OpenClipReader` reads it.
- Same `IS_CHANGED` contract as `OpenClipReader`: it fingerprints the first frame, falls back to the last read when `sequence_path` is linked, and never raises.
- Shares `web/openclip_reader.js` for the in-node `available_aovs` text; that script only draws the texts a node actually sends.

---

### `OpenClipAOVWriter` — `aov_writer.py`

Adds one named AOV to a write chain. Daisy-chain as many instances as needed — each takes the previous one's `aov_chain` output as its own optional input — then feed the last one into `OpenClipWriter.aov_chain`. Same shape as Harmonize's own plate-pair nodes (`docs/guide-data.md` §15 there): one quantity per node, chainable, pass-through.

**Implementation notes:**
- Exactly one of `image`/`matte` must be connected — both or neither raises `ValueError`, since which one was meant is an artist decision, not something to guess at.
- `matte` (a single-channel `MASK`) is written as one channel named `"R"`, so reading it back through `OpenClipAOVReader` lands it on the `matte` output again (the single-channel rule there doesn't care what the one channel is named, but `"R"` keeps the convention consistent both directions).
- Duplicate names raise here (pinpointing the node that introduced the duplicate) and again in `OpenClipWriter`/`image_io.write_sequence` (in case a chain is ever built some other way) — defense in depth, not redundant by accident.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `name` | `STRING` | Required. The EXR part name this entry will be written under. |
| `image` | `IMAGE` (optional) | Written as that many channels. Connect exactly one of `image`/`matte`. |
| `matte` | `MASK` (optional) | Written as a single channel named `"R"`. Connect exactly one of `image`/`matte`. |
| `aov_chain` | `AOV_WRITE_CHAIN` (optional) | The previous `OpenClipAOVWriter` in the chain, if any. Omit on the first node. |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `aov_chain` | `AOV_WRITE_CHAIN` | This entry appended to whatever chain came in — feed into the next `OpenClipAOVWriter`, or into `OpenClipWriter.aov_chain` if this is the last one. |

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
| `direction` | `["forward", "inverse"]` | `forward` (default): `input_colour_space` → `(view)`, scene-referred to display-referred. `inverse`: reverses it — the incoming `IMAGE` is assumed to already be in `(view)` (e.g. a display-referred plate such as an AI-generated background) and is transformed back into `input_colour_space`, scene-referred. Added 2026-08-19 for the Harmonize sibling project's need to linearize a display-referred bg plate through a real named OCIO colour space rather than a bare transfer-function guess. |
| `MASK` | `MASK` (optional) | Passed through unchanged; fully opaque (all-ones) if not connected -- absent matte means no matte, not "nothing here" (standard VFX convention, corrected 2026-09-23 from the old zero-filled default -- see lib/CLAUDE.md "Alpha Channel Handling") |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | Transformed RGB tensor batch |
| `MASK` | `MASK` | Pass-through of the input `MASK` |

---

### `OpenClipColourSpaceTransform` — `colour_space_transform.py`

Added 2026-08-19, for Harmonize's need to reconcile two plates' colour primaries (e.g. an ARRI-native foreground vs. a Rec.709 AI-generated background) into one working space before compositing math runs on them. Applies a **plain** OCIO scene-to-scene colour-space conversion — no Display, no View, no tone-mapping — deliberately a different OCIO call from `OpenClipColourTransform`'s `DisplayViewTransform`, not a mode of it. See `lib/CLAUDE.md` for why: a display space's own OCIO definition is encoding-only, the tone-mapping lives on the View, and running a plain scene-to-scene conversion through a View would incorrectly bolt tone-mapping onto what should be a pure gamut/encoding remap.

`source` offers ~10 common camera-footage gamuts by familiar name (`CAMERA_COLOUR_SPACES` in `colour_space_transform.py`), each verified to exist in the real production Flame config (`aces2.0_config`, 2026-08-19) — not guessed. All but Canon resolve to that config's already-linear `Linear <camera gamut>` entry, matching a plate that already went through `OpenClipReader`'s log-curve decode on read; Canon has no paired linear entry in that config, only the log-encoded name, kept as the best available option but flagged unverified. `Custom...` plus the `custom_source` STRING covers anything not in the curated list. `ACEScg` sits first in the list (dict order), being the pipeline's own working space and the most common pick on either end of this node.

`target_colour_space` (added 2026-08-20) is a dropdown too, not free text — the original free-text field had no defense against a typo'd OCIO name. Options: `ACEScg`; `Same as input (via pipe)`, which reads the real space name from the optional `same_as_input_colour_space` STRING input (wire an `OpenClipReader`'s `colour_space` output into it — that output is the verbatim tag embedded in the source file/metadata, not cross-checked against this OCIO config, so an unresolvable name still surfaces as OCIO's own error at execute time, same fail-loud behaviour `Custom...` already had); the same curated camera list as `source` (covers a plain Rec.709 target via the `sRGB` entry); or `Custom...`, filling the new `custom_target_colour_space` STRING.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch |
| `ocio_config` | `STRING` | Same default resolution as `OpenClipColourTransform` |
| `source` | curated list + `Custom...` | Familiar camera-footage name; resolves internally to the real OCIO colour-space name |
| `custom_source` | `STRING` | Real OCIO colour-space name, used only when `source` is `Custom...` |
| `target_colour_space` | `ACEScg` / `Same as input (via pipe)` / curated list / `Custom...` | Destination colour space, picked not typed |
| `custom_target_colour_space` | `STRING` | Real OCIO colour-space name, used only when `target_colour_space` is `Custom...` |
| `same_as_input_colour_space` | `STRING` (optional, pipe) | Connect an `OpenClipReader.colour_space` output; used only when `target_colour_space` is `Same as input (via pipe)` |
| `MASK` | `MASK` (optional) | Passed through unchanged; fully opaque (all-ones) if not connected -- absent matte means no matte, not "nothing here" (standard VFX convention, corrected 2026-09-23 from the old zero-filled default -- see lib/CLAUDE.md "Alpha Channel Handling") |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | Transformed RGB tensor batch |
| `MASK` | `MASK` | Pass-through of the input `MASK` |

---
