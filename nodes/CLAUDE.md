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
- The `clip_report` text (2.0; replaced the two 1.x displays `available_versions`/`available_aovs`) is pushed into one read-only multiline widget (monospace) on the node itself at execute time via `web/openclip_reader.js`, which listens for the node's `onExecuted` callback and writes the text into a `ComfyWidgets["STRING"]` widget (`serialize: false`, so it's never sent back to Python as an input). This is a different, more standard mechanism than the `widget.draw`/`mouse` override + custom button approach that broke `OpenClipVersionSelector` under a ComfyUI frontend rewrite (see git history), but it is still custom JS and carries some of the same forward-compat risk. The `clip_report` **output** is the guaranteed fallback if the JS ever stops populating the widget — wire it into any text-preview node.
- **Colour lookup (2.0, issue #7):** the loaded version's `<feed><storageFormat><colourSpace>` → EXR `autodeskColorSpace` (Flame writes its tag there) → `openclip:ColorSpace` → `oiio:ColorSpace` (`image_io.colour_space_from_metadata` does the EXR half). 1.x read only the two OIIO keys, so every Flame clip came back as `''`.
- **Start timecode** is the feed's `<startTimecode>` (`rate`/`nbTicks`/`dropMode`), shown as `HH:MM:SS:FF` (`;FF` drop-frame). When the feed has none, the `smpte:TimeCode` in the header of the **feed's own first frame** (`feed_start_timecode()`, 2026-10-29; Autodesk's example clips carry it only there; header only, rates up to 30, the BCD decode is `timecode.from_smpte_bcd`); `""` when neither exists. It is the clip's start, also when `start_frame`/`frame_cap` read a sub-range — that is why the feed's first frame is opened rather than the first frame read (no offset arithmetic, which drop-frame would complicate): a timecode is never derived from a frame number or vice versa (explicit user decision). The Writer's append check uses the same function for the existing clip.
- **Versions of different sizes are refused (2026-10-29, explicit user decision: stricter than Flame).** `execute()` compares the `<storageFormat>` width/height of every feed in the picture track (`openclip_xml.feed_sizes`, XML only); any difference raises `VERSIONS OF DIFFERENT SIZES` with each version's size, whichever version was asked for. Feeds with no recorded size are skipped. Reason: switching versions would change the image size inside a ComfyUI workflow. Not in `IS_CHANGED` (it must never raise; `execute()` gives the error). The Writer still appends to such a clip at the current version's size (its format check is unchanged).
- `clip_name` is the XML `<name>` **verbatim** (Flame keeps leading/trailing spaces; issue #7); the file stem only when `<name>` is missing or blank.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `clip_path` | `STRING` | Path to a `.clip` file. Absolute paths are used as-is; relative paths resolve against ComfyUI's `input/` directory. |
| `version` | `STRING` | Version name to load (e.g. `v001`); `"current"` reads `currentVersion` attribute. Type any name shown in the in-node version list to switch versions. |
| `start_frame` | `INT` | First frame to load |
| `end_frame` | `INT` | Last frame to load; `-1` = all frames |
| `path_from` | `STRING` | Path prefix to replace in media paths from the XML (e.g. `/Volumes/LocalJobs`). Leave empty to let the reader auto-detect the remap. |
| `path_to` | `STRING` | Replacement prefix (e.g. `/mnt/LocalJobs`). Only applied when `path_from` is non-empty and matches. Ignored when auto-remap is active. |
| `frame_cap` | `INT` | Caps the read to at most this many frames from the resolved start (the clip's own native start when `start_frame` is `-1`) — independent of each clip's own frame numbering, which varies (Flame commonly starts at 1, Nuke and film pipelines at 1001). `-1` (default) disables the cap. Appended after `path_to`, not placed next to `start_frame`/`end_frame` where it semantically belongs, so existing saved workflows' positional `widgets_values` stay valid. |

**Outputs** (2.0: 9, was 16)

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch `(N, H, W, 3)` — slot 0 |
| `MASK` | `MASK` | Alpha tensor batch `(N, H, W)` — slot 1; fully opaque (all-ones) if the source has no alpha (see `lib/CLAUDE.md` "Alpha Channel Handling") |
| `clip_info` | `CLIP_INFO` | Dict with everything about the clip — see **`CLIP_INFO`** below. Wire into `OpenClipWriter.clip_info`; unpack single values with `OpenClipClipInfo`. |
| `aov_handle` | `AOV_HANDLE` | Lazy handle for AOV extraction — locates the file(s), decodes nothing. Wire into one `OpenClipAOVReader` per AOV you actually want. |
| `frame_count` | `INT` | Number of frames loaded |
| `width` | `INT` | Frame width in pixels |
| `height` | `INT` | Frame height in pixels |
| `fps` | `FLOAT` | Frame rate from `<editRate>` as the exact rational value (`23.976` label → `24000/1001`) |
| `clip_report` | `STRING` | The text shown in the node (below); no-JS fallback for the in-node display |

Removed in 2.0 (now in `clip_info`, or in `clip_report`): `format_version`, `version_name`, `start_frame`, `clip_path`, `clip_name`, `metadata`, `colour_space`, `available_versions`, `available_aovs`.

**`clip_report`** (agreed layout; one version/AOV per line so names can be copied):
```
CLIP     woods walk   (/jobs/x/woods walk .clip)
FORMAT   Open Clip schema 6 · EXR half PIZ · 1920×1080 · 23.976 fps
         colour: Rec.709 video
TIME     start TC 01:00:00:00 · frames 1–9 (9) · padding 6

VERSIONS
  v0
  v1  (current)  ← loaded

AOVS
  depth
  normals
```
`FORMAT`'s file part comes from the first frame's header (`image_io.describe_frame`). The EXR Sequence Reader has no `VERSIONS` section and no schema; `CLIP` shows its sequence pattern.

**`CLIP_INFO`** (`nodes/_clip_info.py`; a plain dict, all keys always present): `clip_path` (source `.clip`; the sequence folder for the EXR Sequence Reader), `clip_name`, `version_name` (the version actually read), `start_frame` (first frame actually read), `start_timecode`, `fps`, `colour_space`, `format_version`, `frame_padding` (digits of the media's frame token), `bit_depth` (`half`/`float` from the first frame's EXR header, `""` otherwise, e.g. PNG; 2026-10-29 — the `clip_report` FORMAT line already shows it, so it isn't repeated), `metadata` (first frame's EXR header attributes — what `CLIP_METADATA` carried in 1.x). Report-only extras: `media_pattern`, `file_format`, `width`, `height`, `frame_count`, `versions`, `current_version`, `aovs`.

**AOVs are a separate, generic mechanism as of 2026-09-23** — see `OpenClipAOVReader`/`OpenClipAOVWriter` below. This replaced a fixed three-AOV interface (`NORMAL`/`NORMAL_WORLD`/`DEPTH`/`NORMAL_RAW` outputs, `depth_layer_name`/`normal_world_layer_name`/`normal_raw_layer_name` widgets) that had two problems: every Reader instance carried those three widgets whether or not the clip had any AOVs at all, and adding a new AOV meant changing the Reader's own interface. Breaking change, made deliberately (explicit user decision, 2026-09-23) rather than kept alongside the old interface — AOVs had only been used in development/testing, nothing released or in production, so there was nothing worth preserving compatibility for.

**Typical wiring — full read → process → write round-trip (2.0):**
```
OpenClipReader.IMAGE      → (processing) → OpenClipWriter.IMAGE
OpenClipReader.clip_info  → OpenClipWriter.clip_info   (path, name, start frame, start timecode, fps,
                                                       colour, schema, padding, header metadata)
OpenClipReader.aov_handle → OpenClipAOVReader.aov_handle (one per AOV; chain OpenClipAOVWriter into OpenClipWriter.aov_chain)
# Writer fields left on auto; clip_filename "$(path)/$(clip_name).clip" -> appends a new version to the source clip
```
The Writer's `version_name` is never taken from `clip_info`: its default `"next"` lands each run as a new version. Type a name (and enable `overwrite`) to re-process a specific version. Add a suffix in `clip_filename` (`$(path)/$(clip_name)_clean.clip`) or type a `clip_name` to write a separate clip instead.

---

### `OpenClipWriter` — `writer.py`

Writes a frame sequence and generates a valid OpenClip `.clip` XML package.

A new `.clip` is written as schema `version="6"` by default (set by `format_version`); appending to an existing `.clip` edits it in place and keeps its own schema version and all of Flame's metadata — see `CLAUDE.md`'s design-decisions table (issue #4).

**2.0: sockets `IMAGE`, `MASK`, `clip_info`, `aov_chain`; everything else is a field with an "auto" value.** Auto takes the value from `clip_info` when connected, else a default; any other value overrides `clip_info`:

| Field | Auto | Auto means |
|---|---|---|
| `clip_path`, `clip_name` | `""` | from `clip_info`, else required |
| `start_frame` | `-1` | from `clip_info`, else `1` |
| `start_timecode` | `""` | from `clip_info`, else (appending) the existing clip's current feed, else `00:00:00:00` |
| `fps` | `0` | from `clip_info`, else `24` |
| `colour_space` | `""` | from `clip_info`, else `Rec.709 video` |
| `format_version` | `""` | from `clip_info` (if 5–9, else 6 with a warning), else `6` |
| `frame_padding` | `0` | from `clip_info`, else `4` |
| `exr_bit_depth` | `auto` | from `clip_info` (`half`/`float`), else `half (16-bit)` (2026-10-29; `auto` is the first combo option, so it is the default; adding an option to a combo doesn't move other widgets' saved values) |
| `version_name` | `"next"` | **never** from `clip_info` |

Header metadata comes only from `clip_info["metadata"]` (Writer format options still win; a carried `smpte:TimeCode` is replaced by the per-frame one).

**Start timecode (2.0, issue #7).** Written to each new feed's `<startTimecode>` (`rate` = rounded fps, so 24 for 23.976 as Flame writes it; `nbTicks`; `dropMode` `NDF`/`DF`) and, per frame, as the EXR `smpte:TimeCode` counting up from it (`lib/timecode.py`; not written for rates above 30, whose frame numbers don't fit the BCD field). Rules when the destination `.clip` exists (also with `overwrite` on), all checked before anything is written:
- start timecode differs from the existing clip's current feed (taken as the Reader reports it: XML, else its first frame's EXR `smpte:TimeCode`, else `00:00:00:00`; 2026-10-29, so a Reader → Writer append to an EXR-only-timecode clip lines up) → error;
- same real timecode, different start frame → printed warning only (the timecode lines the versions up);
- both `00:00:00:00` and different start frames → error.

**Exact-file append (issue #7).** Names are never stripped. When the destination resolves to the same file as `clip_info`'s source `.clip` (`os.path.samefile`, so a symlinked or differently spelled path counts), the write uses that file's own folder and name verbatim. A source whose `<name>` differs from its file stem still resolves to `<name>.clip`, i.e. a new clip — the rule is "same file", not "always the source".

**`"next"` keeps the existing numbering's padding:** Flame's `v0` → `v1`, ours `v001` → `v002`; a new clip starts at `v001`.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch |
| `MASK` | `MASK` | Optional alpha; merged into EXR alpha channel if connected |
| `clip_info` | `CLIP_INFO` | Optional, from either reader. Supplies every auto field above, plus the header metadata. |
| `aov_chain` | `AOV_WRITE_CHAIN` | Optional. Feed the last `OpenClipAOVWriter` in a daisy-chain here — see `OpenClipAOVWriter` below. Each entry becomes a real named EXR part/subimage (or a separate sibling file, depending on `aov_layout`), not a channel-name convention. |
| `format_version` | `STRING` | Auto `""` (see table). Root `<clip version="…">` for a **new** `.clip`; a typed value must be `5`–`9` and raises before anything is written otherwise. Ignored (with a printed warning when typed and different) when appending, which keeps the file's own version. |
| `colour_space` | `STRING` | Auto `""` → `clip_info`'s colour, else `Rec.709 video` (the pre-2026 SynColor name, which Flame 2026+'s OCIO config keeps as an alias, so one tag works on every Flame — see `CLAUDE.md`). Written to the XML `<colourSpace>` **and** the EXR `oiio:ColorSpace`/`openclip:ColorSpace` keys (and replaces a carried `autodeskColorSpace`): one colour value, no longer two pipes as in 1.x. |
| `start_timecode` | `STRING` | Auto `""` (see table and **Start timecode** above). `HH:MM:SS:FF`, or `HH:MM:SS;FF` for drop-frame (29.97/59.94 only); bad text raises before anything is written. |
| `aov_layout` | `COMBO` | `Multichannel` (default) writes each `aov_chain` entry as its own named subimage in the same file as beauty — a true multi-part EXR, matching the real-world convention (see `lib/CLAUDE.md` "AOV Channel Handling"), not the old single-part-many-channels scheme. `Separate Files` writes one extra `<clip_name>_AOV_<name>.####.ext` sibling file per entry instead, `name` taken directly from the chain entry (sanitized for filesystem safety) rather than a fixed suffix table. A non-empty `aov_chain` with `file_format="PNG"` raises `ValueError` — PNG's 8-bit path would silently corrupt AOV data. |
| `clip_path` | `STRING` | Folder path for the output package (auto `""` = `clip_info`'s). If a `.clip` file path is supplied, the parent directory is used automatically. |
| `clip_name` | `STRING` | Clip name used for directory and file naming (auto `""` = `clip_info`'s). Never trimmed. Must be a plain name: `/`, `\`, `.` and `..` raise before anything is written (2026-10-28), since a wired value can come from someone else's `.clip` `<name>`; move the destination with `clip_filename` instead. |
| `clip_filename` | `STRING` | Destination pattern; default `$(path)/$(clip_name).clip`. `$(path)` expands to `clip_path` (normalised to a folder); `$(clip_name)` expands to `clip_name`. Parentheses delimit the token explicitly, so literal text can follow directly with no ambiguity, e.g. `$(path)/$(clip_name)_clean.clip`. Supports `..` segments, e.g. `$(path)/../processed/$(clip_name).clip`. The `.clip` extension is stripped before passing the name to the layout builder, so the package is named correctly regardless. The resulting name is used for **both** the `.clip` file and the media sequence filenames (e.g. `$(clip_name)_clean` → `myshot_clean.clip` and `myshot_clean.0001.exr`); there's no way to rename only the `.clip` file. |
| `version_name` | `STRING` | Version label, e.g. `v001`. Default `"next"` — a Writer-only sentinel (mirrors the Reader's `"current"`) resolved via `openclip_xml.next_version()`: reads the existing `.clip`, returns one past the highest `v<NNN>` version present, or `v001` if the clip doesn't exist yet. A brand-new Writer just works with no typing; type an explicit name to opt out of auto-selection (never taken from `clip_info`). Same plain-name rule as `clip_name`. |
| `overwrite` | `BOOLEAN` | Default `False`. If the resolved `version_name` already exists in the `.clip` and this is `False`, raises `ValueError` (with a `VERSION ALREADY EXISTS` banner and a fix-it list) instead of writing. If `True`, deletes that version's existing frame files first (so a shorter re-render doesn't leave stale trailing frames), then overwrites the frames and the XML `<version>`/`<feed>` entry. The frame match is glob-escaped, so `*`/`?`/`[` in a name can't delete another clip's frames (bug fixed 2026-10-28). Never triggers when `version_name` resolved from `"next"`, since that's computed to not collide. |
| `include_version_in_filename` | `BOOLEAN` | Default `True` since 2026-10-28 (was `False`), so a new clip has Flame's own layout and Flame can add versions to it. If true, inserts `_<version_name>` into the frame sequence filenames (e.g. `myshot_v001.0001.exr`; was `myshot.v001.0001.exr` before 2026-10-27) and, in `Standard Flame`, puts every version's frames in one `<clip_name>/` folder instead of one subfolder per version — **Flame's own Open Clip export layout**. Does **not** affect the `.clip` filename — that stays governed by `clip_filename`/`clip_name` alone, so multiple versions still merge into one `.clip` file via `_merge_version`. Default `False`. |
| `start_frame` | `INT` | Frame number for the first output file. Auto `-1` = `clip_info`'s, else `1` (Flame's own convention; 1001 is the Nuke/film one). |
| `frame_padding` | `INT` | Zero-padding width. Auto `0` = `clip_info`'s, else `4` (→ `%04d`) |
| `fps` | `FLOAT` | Frame rate. Auto `0` = `clip_info`'s, else `24` (`step 0.001`). Matched to the nearest supported rate (`23.976`, `24`, `25`, `29.97`, `30`, `48`, `50`, `59.94`, `60` — see `lib.openclip_xml.FPS_OPTIONS`) within a small tolerance via `fps_label_from_float()`, then written as that label into `<editRate>` and `<sampleRate>`. Raises `ValueError` (including for NaN/inf) if the value doesn't match any supported rate. Checked, together with the existing-clip format check (width/height/fps/file_format), *before* any directory or frame is written, so a refused run leaves nothing on disk. The NTSC rates are exact rationals (e.g. `23.976` = `24000/1001`), so matching compares against the true rational value, not the rounded decimal label. |
| `file_format` | `COMBO` | `EXR` or `PNG` |
| `exr_bit_depth` | `COMBO` | `auto` (default: `clip_info`'s, else half), `half (16-bit)` or `float (32-bit)` — EXR only |
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
- `part_name` is typed by hand, copied from the AOVS section of the reader's `clip_report` — no dropdown is possible.
- Output shape follows one rule: a part with exactly 1 channel goes entirely to `matte`, `image` is `None` (nothing meaningfully multi-channel to return). A part with >=2 channels populates both — `image` is all of them, `matte` is the channel literally named `"R"` if present (looked up by name, never assumed positionally — this project's channel-by-name discipline), else channel 0. Real-world depth-like parts are a replicated scalar across R/G/B (confirmed against a real Flame render) — 3 channels on disk, so `image` *is* populated in that case, `matte` just happens to equal every channel of it.
- Both outputs are `None`, not a zero-filled placeholder, if `part_name` isn't found in any frame of the handle's range — same "don't fabricate an absent value" contract the rest of this project's AOV handling already uses.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `aov_handle` | `AOV_HANDLE` | From `OpenClipReader` |
| `part_name` | `STRING` | EXR part `name` to extract — copy from the AOVS section of the reader's `clip_report` |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `image` | `IMAGE` | All of the part's channels, batch `(N, H, W, C)` — `None` if the part is single-channel, or not found |
| `matte` | `MASK` | The `"R"` channel (or the sole channel, if single-channel), batch `(N, H, W)` — `None` if not found |

---

### `OpenClipExrSequenceReader` — `exr_sequence_reader.py`

Reads a plain EXR frame sequence with no `.clip` wrapper (Blender, Nuke). 2.0: the same 9 outputs as `OpenClipReader` (`IMAGE`, `MASK` at slots 0/1 for `HM_ImageSwitch`/`HM_MaskSwitch`, `clip_info`, `aov_handle`, `frame_count`, `width`, `height`, `fps`, `clip_report`). Its `clip_info` has `version_name` and `format_version` empty (a sequence has none; the Writer then uses its defaults), `start_timecode` = the **first frame read**'s EXR `smpte:TimeCode` (`""` when absent; 2026-10-29: a plain sequence has no clip start of its own, and the Writer numbers the new clip from the first frame read, so that frame's timecode belongs to it — unlike `OpenClipReader`, which reports its feed's start), `clip_path` = the sequence folder, `clip_name` = the filename stem, `frame_padding` from the frame token. Its `clip_report` has no VERSIONS section.

**Implementation notes:**
- `sequence_path` takes `name.####.exr` (any number of `#`) or `name.%04d.exr`; relative paths resolve against the input directory, like `clip_path`. `start_frame`/`end_frame` of `-1` mean the first/last frame found on disk; `frame_cap` works as on `OpenClipReader`.
- `layer` picks the beauty from a single-part multilayer EXR by prefix (`CamelMatte.Combined` reads `CamelMatte.Combined.R/G/B/A`). Blank reads plain `R/G/B/A`. A wrong or missing layer raises with the file's layer list.
- `clip_info`'s `clip_path`/`clip_name` are genuinely derived, not placeholders: the sequence's folder and the filename stem before the frame token, so a Writer fed this `clip_info` writes `<folder>/<stem>.clip`.
- `fps` is a widget (Blender writes none into the EXR). A blank `colour_space` widget falls back to the header (`autodeskColorSpace` → `openclip:ColorSpace` → `oiio:ColorSpace`).
- Same `IS_CHANGED` contract as `OpenClipReader`: it fingerprints the first frame, falls back to the last read when `sequence_path` is linked, and never raises.
- Shares `web/openclip_reader.js` for the in-node `clip_report` text.

---

### `OpenClipClipInfo` — `clip_info.py` ("OpenClip Clip Info", 2.0)

Unpacks a reader's `clip_info` into one output per value, for wiring single values elsewhere (e.g. `colour_space` into `OpenClipColourSpaceTransform`'s `same_as_input_colour_space`). The Writer takes `clip_info` whole and doesn't need it.

| Input | Type |
|---|---|
| `clip_info` | `CLIP_INFO` |

| Output | Type | Notes |
|---|---|---|
| `clip_path`, `clip_name`, `version_name` | `STRING` | as in `CLIP_INFO` |
| `start_frame` | `INT` | first frame read |
| `start_timecode` | `STRING` | `HH:MM:SS:FF`, `""` if none |
| `fps` | `FLOAT` | |
| `colour_space`, `format_version` | `STRING` | |
| `frame_padding` | `INT` | not in the agreed socket list, added under "every value as its own socket" |
| `metadata` | `CLIP_METADATA` | EXR header attributes of the first frame |
| `bit_depth` | `STRING` | `half`/`float`, `""` for non-EXR (2026-10-29) |
| `clip_report` | `STRING` | same text as the reader's |

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

Pre-2026 Flame (SynColor): both colour nodes work with Autodesk's SynColor-emulation OCIO configs (`legacy_configs/`, shipped with Flame 2026+, not redistributed here) — names this project uses are translated to their legacy equivalents when the loaded config lacks them (`lib.colour_transform.LEGACY_NAMES`), and a missing `view` raises listing the config's views. See `lib/CLAUDE.md` "Colour Transforms".

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

`target_colour_space` (added 2026-08-20) is a dropdown too, not free text — the original free-text field had no defense against a typo'd OCIO name. Options: `ACEScg`; `Same as input (via pipe)`, which reads the real space name from the optional `same_as_input_colour_space` STRING input (wire `OpenClipClipInfo.colour_space` into it — 2.0; was the Reader's own `colour_space` output — that value is the verbatim tag embedded in the source file/metadata, not cross-checked against this OCIO config, so an unresolvable name still surfaces as OCIO's own error at execute time, same fail-loud behaviour `Custom...` already had); the same curated camera list as `source` (covers a plain Rec.709 target via the `sRGB` entry); or `Custom...`, filling the new `custom_target_colour_space` STRING.

**Inputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | RGB tensor batch |
| `ocio_config` | `STRING` | Same default resolution as `OpenClipColourTransform` |
| `source` | curated list + `Custom...` | Familiar camera-footage name; resolves internally to the real OCIO colour-space name |
| `custom_source` | `STRING` | Real OCIO colour-space name, used only when `source` is `Custom...` |
| `target_colour_space` | `ACEScg` / `Same as input (via pipe)` / curated list / `Custom...` | Destination colour space, picked not typed |
| `custom_target_colour_space` | `STRING` | Real OCIO colour-space name, used only when `target_colour_space` is `Custom...` |
| `same_as_input_colour_space` | `STRING` (optional, pipe) | Connect `OpenClipClipInfo.colour_space`; used only when `target_colour_space` is `Same as input (via pipe)` |
| `MASK` | `MASK` (optional) | Passed through unchanged; fully opaque (all-ones) if not connected -- absent matte means no matte, not "nothing here" (standard VFX convention, corrected 2026-09-23 from the old zero-filled default -- see lib/CLAUDE.md "Alpha Channel Handling") |

**Outputs**

| Name | Type | Description |
|---|---|---|
| `IMAGE` | `IMAGE` | Transformed RGB tensor batch |
| `MASK` | `MASK` | Pass-through of the input `MASK` |

---
