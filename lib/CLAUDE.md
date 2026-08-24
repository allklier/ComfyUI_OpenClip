# lib/ — Core Library Modules

Business logic lives here. The three modules map to the three responsibilities: parse/generate XML, read/write image files, resolve on-disk layout.

## Modules

| File | Responsibility |
|---|---|
| `openclip_xml.py` | Parse and generate `.clip` XML for v8 and v9 |
| `image_io.py` | Read/write EXR and PNG sequences via OIIO |
| `package_layout.py` | Resolve and build the on-disk package directory tree |
| `colour_transform.py` | Apply an OCIO Display/View transform to an `IMAGE` tensor |

---

## Data Flow

**Read path:**
```
.clip XML → openclip_xml.parse()
          → resolve media path for selected version
          → image_io.read_sequence(path_pattern, start, end)
          → OIIO → torch.Tensor (IMAGE, MASK)
```

**Write path:**
```
IMAGE + MASK tensors
  → image_io.write_sequence() via OIIO → frame files on disk
  → package_layout.build() → directory tree
  → openclip_xml.generate() → .clip XML written to disk
  → .comfy.json sidecar written + XML reference added (if publish enabled)
```

---

## OpenClip XML Schema — `openclip_xml.py`

The schema version is read from the `version` attribute on the root `<clip>` element and carried through as `ParsedClip.schema_version`, but it does not change the element nesting. **Confirmed against real Flame-exported `.clip` files spanning schema versions 6, 7, 8, and 9**: the structure is identical across all of them. Media (feeds) always lives under `<tracks>/<track>/<feeds>`, and the top-level `<versions>` block (metadata only — name, creationDate, batchSetup, etc.) is always a **sibling of `<tracks>`**, never nested inside `<track>`. There is no version that wraps feeds inside `<versions>/<version>`; a earlier draft of this doc assumed there was, which was never verified and caused real Flame clips tagged `version="9"` to fail to parse with "missing `<versions>` inside `<track>`".

Flame encodes the frame range directly inside the path using `[NNNNNN-NNNNNN]` notation — there are **no separate `<startFrame>` or `<duration>` elements**. The parser converts this to a printf-style pattern plus `start_frame` / `duration` values. Clips we generate use explicit `<startFrame>` and `<duration>` elements with a `%04d`-style path; the parser accepts both formats.

```xml
<!-- Flame-generated (range notation in path); identical shape for version="6","7","8","9" -->
<clip type="clip" version="8">
  <name type="string">my_clip</name>
  <tracks>
    <track uid="...">
      <feeds currentVersion="v001">
        <feed vuid="v001" uid="...">
          <storageFormat type="format"><type>video</type></storageFormat>
          <spans>
            <span>
              <path encoding="pattern">/path/to/my_clip.[001001-001100].exr</path>
            </span>
          </spans>
        </feed>
      </feeds>
    </track>
  </tracks>
  <!-- top-level <versions> is version metadata only (names, dates) — not media -->
  <versions currentVersion="v001">
    <version uid="v001"><name>v001</name></version>
  </versions>
</clip>

<!-- Our generated v8 (explicit startFrame/duration) -->
<clip type="clip" version="8">
  <name>my_clip</name>
  <tracks><track><feeds currentVersion="v001">
    <feed vuid="v001">
      <storageFormat type="format"><type>video</type></storageFormat>
      <spans><span>
        <path>versions/v001/my_clip.%04d.exr</path>
        <startFrame>1001</startFrame>
        <duration>100</duration>
      </span></spans>
    </feed>
  </feeds></track></tracks>
</clip>
```

A real Flame `<span><path>` can also carry `encoding="file"` for a single static image (a still/screenshot, not a sequence) — no `[NNNN-NNNN]` range and no `<startFrame>`/`<duration>`. `_parse_spans` detects the absence of range notation and represents it as a one-frame span (`start_frame=1`, `duration=1`, path unchanged). `image_io.read_sequence` reads the path literally in this case rather than applying `%` frame substitution, since there is no printf token to substitute. Confirmed against two real Flame-exported clips referencing a single `.png`.

All media paths inside the XML are relative to the `.clip` file's directory.

---

## Package Layouts — `package_layout.py`

### Standard Flame layout
Mirrors what Flame produces on export. Safe for direct library scan and import.

```
<output_dir>/
  <clip_name>/
    <clip_name>.clip              ← XML manifest
    versions/
      v001/
        <clip_name>.0001.exr
        <clip_name>.0002.exr
        <clip_name>.v001.comfy.json   ← ComfyUI workflow sidecar (publish only)
```

### Flat layout
Simpler structure; suitable for drag-and-drop Flame import or stand-alone review.

```
<output_dir>/
  <clip_name>.clip                ← XML manifest
  <clip_name>.0001.exr
  <clip_name>.0002.exr
  <clip_name>.v001.comfy.json     ← ComfyUI workflow sidecar (publish only)
```

Path tokens in the XML are always relative to the `.clip` file, so both layouts produce valid, portable packages.

---

## Alpha Channel Handling — `image_io.py`

- `read_sequence()` returns a zero-filled MASK tensor when the source has no alpha channel, so callers always receive a valid tensor regardless of source format.
- `write_sequence()` accepts an optional MASK. If provided, it is combined with the IMAGE into a 4-channel RGBA EXR. If absent, a 3-channel RGB EXR is written.
- PNG supports RGBA natively via OIIO.

---

## AOV Channel Handling — `image_io.py`

Per `EXR_REQUIREMENTS.md`'s convention table, AOV channels are single-part EXR with dotted `<layer>.<channel>` naming: `N.X`/`N.Y`/`N.Z` (camera-space normals), `Nw.X`/`Nw.Y`/`Nw.Z` (world-space normals), `Z` (depth). `AOV_CHANNELS` maps each Reader output key (`"normal"`, `"normal_world"`, `"depth"`) to its channel-name group.

**Channels are always addressed by name, never index.** OpenEXR does not preserve channel write order, and OIIO reorders on read — confirmed against the installed OIIO 3.1.15 by round-tripping an actual file: writing `R,G,B,A,N.X,N.Y,N.Z,Nw.X,Nw.Y,Nw.Z,Z` reads back as `R,G,B,A,Z,N.X,N.Y,N.Z,Nw.X,Nw.Y,Nw.Z` (`spec.channelindex('N.X') == 5`). `_extract_named_channels()` resolves every channel group (including plain R/G/B/A) via `spec.channelnames` at read time; a group is all-or-nothing — if any one of its names is absent, the whole group is treated as not present, never partially filled.

**Two on-disk layouts, auto-detected, no toggle:** `read_sequence_with_aovs()` tries each AOV group on the beauty file itself first (single multichannel EXR), then falls back to a sibling `<name>_AOV_<Suffix>.####.ext` file (separate-file-per-AOV convention — suffixes fixed in `AOV_SUFFIXES`, matching `ComfyUI_Harmonize/tests/generate_synthetic_exr.py` exactly), then zero-fills at the beauty frame's H×W if neither has it. `_aov_sibling_pattern()` builds the sibling path pattern by inserting the suffix into the beauty pattern's stem, immediately before the frame token (or before the extension for a static/no-frame-token path — the same Flame `encoding="file"` still-image case `openclip_xml.py` handles). This mirrors the alpha-channel precedent above (zero-fill on absence, no toggle) rather than reintroducing the removed `load_alpha` toggle's silent-default-off bug class.

**Multi-part EXR (real Flame renders) — a second, part-name-keyed convention.** `EXR_REQUIREMENTS.md` flagged "single-part dotted vs. true multi-part" as unconfirmed. A real Flame render (beauty + depth + normals + Cryptomatte) confirmed it's multi-part, but **not** in the way first assumed: every part uses plain generic `R,G,B` channel names — there is no dotted `N.X`/`Z` anywhere in the file. The AOV's identity is carried entirely by the EXR part's own `name` attribute (`"depth"`, `"normals"`, `"cryptomatte"`, ...). This makes sense once traced back: Flame didn't generate these AOVs itself — they were imported from another DCC (Cinema 4D in the confirmed case) — so the dotted layer.channel convention (which is this project's own synthetic-fixture convention, matching `ComfyUI_Harmonize/tests/generate_synthetic_exr.py`) never applied to this data at all.

Because every part shares the same generic channel names, a Cryptomatte part is **indistinguishable from a real AOV by channel name alone** — the channel-name-based "skip if nothing known" optimization from an earlier version of this fix was actively wrong here (it couldn't tell Cryptomatte apart from depth/normals, so it decoded Cryptomatte's often-large data on every read). The fix instead identifies parts by **name**: `_read_frame()` reads subimage 0 as beauty/alpha (unchanged, and still tries the dotted-channel AOV convention against subimage 0's own channels first), then — only if at least one of `depth_part_name`/`normal_world_part_name`/`normal_raw_part_name` is non-empty — walks the remaining subimages via `seek_subimage()`, comparing each one's cheap `spec()`-level `name` attribute against those three targets and decoding (`read_image()`) only a match. An unmatched part (Cryptomatte, or anything else) is never pixel-decoded.

`depth_part_name` (default `"depth"`) and `normal_raw_part_name` (default `"normals"`) match the one real file investigated so far; `normal_world_part_name` defaults to `""` (disabled) since that file had no separate world-space part. **Depth's data is a replicated scalar across R/G/B** (confirmed empirically: `R==G==B` everywhere in the real file) — read as channel 0. **This repo does not try to determine whether an ambiguous normals-like part is camera-space or world-space** — Flame/this pipeline has no way to know, since the data didn't originate there. That part is exposed as `normal_raw` (3-channel, unlabeled) rather than being routed into the space-committed `normal`/`normal_world` groups, which stay zero-filled unless a distinctly-named part matches `normal_world_part_name` (or the dotted-channel convention finds a `normal`/`normal_world` group on subimage 0). Space labelling of `normal_raw` is explicitly left to the consuming project (Harmonize) via its own node/toggle — an explicit user decision, not a default this repo picks, since guessing space would risk exactly the "plausible-looking wrong result" failure class this whole feature exists to prevent.

`_read_named_channels_if_exists()` (the separate-file sibling case) does **not** get this part-name treatment — no real or synthetic sibling AOV file has been observed to be multi-part, so it stays a plain single-subimage read.

`write_sequence()`'s optional `aovs`/`aov_layout` parameters mirror the read side: `"Multichannel"` (default) appends AOV channels onto the same EXR as beauty via explicit `spec.channelnames`; `"Separate Files"` writes one extra file per present AOV group at the same sibling-pattern convention. AOVs require `file_format="EXR"` — PNG's 8-bit clamp-to-uint8 write path would silently corrupt normals (signed, ~-1..1) or depth (unbounded), so `write_sequence()` raises `ValueError` if both are requested together.

---

## Colour-Space Metadata — `image_io.py`

`_extract_frame_metadata()` captures all of a frame's OIIO `extra_attribs` generically (already used for the `CLIP_METADATA` round-trip), which is where an EXR's `oiio:ColorSpace` attribute — if the file has one — shows up. The Reader's `colour_space` output reads this to satisfy `EXR_REQUIREMENTS.md` requirement #3.

**`oiio:ColorSpace` is not a safe round-trip vessel by itself on write.** Verified against the installed OIIO 3.1.15: its EXR plugin treats this key as an enum tied to its internal OCIO built-in-config registry, not a free string. On write, a recognized name gets silently *renamed* to its canonical form (`"ACEScg"` → `"lin_ap1_scene"`, `"Linear"` → `"lin_rec709_scene"`), and an unrecognized one — an arbitrary camera log profile name, a show-specific LUT name, exactly what requirements #2/#3 need to carry — is **silently dropped from the file entirely**, no error. So `_write_exr()` also stashes the value verbatim under `COLOUR_SPACE_BACKUP_KEY` (`"openclip:ColorSpace"`, a plain custom-namespaced attribute OIIO does not special-case) whenever `metadata` carries `COLOUR_SPACE_KEY` (`"oiio:ColorSpace"`). The Reader prefers the backup key and falls back to the plain key, so: this project's own write→read round trip is always lossless regardless of whether OIIO's registry recognizes the value, while a first-ever read of a third-party file (real Flame/camera EXRs that only ever set the conventional `oiio:ColorSpace`) still works via the fallback — best-effort, since we don't control how they wrote it.

---

## Publish (ComfyUI Workspace JSON) — `openclip_xml.py`

"Publish" mirrors the Flame concept of committing a finished version. When publish is enabled:

1. The active ComfyUI workflow JSON is written **into the version's media directory** as `<clip_name>.<version_name>.comfy.json` (e.g. `myshot.v001.comfy.json`), adjacent to the image sequence.
2. The `.clip` XML records the path inside the version's `<userData>` block (relative to the `.clip` file):

```xml
<versions currentVersion="v001">
  <version uid="v001">
    <userData type="dict">
      <appName type="string">ComfyUI</appName>
      <versionNumber type="uint64">1</versionNumber>
      <comfyWorkflow path="versions/v001/myshot.v001.comfy.json" />
    </userData>
  </version>
</versions>
```

The path is stored in `ClipVersion.publish_path` (relative to the `.clip` file's directory). Versions written without publish have `publish_path = None` and no `<comfyWorkflow>` element. The parser reads `<comfyWorkflow>` back so multi-version merges preserve existing publish references.

---

## Colour Transforms — `colour_transform.py`

`apply_colour_transform()` builds an `ocio.DisplayViewTransform` (`src=input_space`, `display=output_space`, `view=view`) rather than calling `config.getProcessor(input_space, output_space)` directly between two colour spaces.

This distinction matters specifically because `output_space` (default `"Rec.1886 Rec.709 - Display"`) is a **display-referred** colour space. In an OCIO v2 / ACES config, a display colour space's own `colorspaces:`/`display_colorspaces:` definition is *encoding only* (EOTF + primaries) — it has no tone-mapping or gamut compression baked in. The actual ACES Output Transform (what Flame calls a "view transform": filmic highlight roll-off, gamut compression back into the display volume) lives on the **View**, which is a separate `ViewTransform` object composed with the display only when you go through `DisplayViewTransform`. Calling `getProcessor(scene_referred_space, display_space)` directly skips the view entirely and just re-encodes scene-linear values straight into the display's gamma/primaries — bright or saturated pixels end up negative or above `1.0` instead of being compressed into range. Confirmed against the Academy's public reference ACES 2.0 config (which Flame's `aces2.0_config` ships unmodified): a saturated red at `(2.0, 0.05, 0.05)` in ACEScg comes out as `(1.66, -0.52, 0.09)` via the plain-`getProcessor` path vs. `(1.00, 0.28, 0.31)` via `DisplayViewTransform` with the `"ACES 2.0 - SDR 100 nits (Rec.709)"` view.

`tests/fixtures/test_ocio.ocio` mirrors this Display/View split with a minimal stand-in: a `"Raw"` view (identity passthrough, no view transform — proves the pipe-through path works) and an `"ACES 2.0 - SDR 100 nits (Rec.709)"` view whose `ViewTransform` halves scene-referred values (a stand-in for real tone-mapping — proves the view transform is actually being applied, not skipped).

**`apply_colourspace_transform()` (added 2026-08-19) is the plain `getProcessor(input_space, output_space)` call this section just described as wrong for scene→display work — and it's exactly right for a different job:** reconciling two colour spaces that are *both already scene-referred* (e.g. a plate's native camera gamut vs. a common working space like ACEScg), where no tone-mapping should be involved at all. Both endpoints there are ordinary `colorspaces:` entries, not `display_colorspaces:` ones, so there's no View to skip and no out-of-range risk of the kind measured above. Built for Harmonize's foreground/background primaries-reconciliation need — see `nodes/CLAUDE.md`'s `OpenClipColourSpaceTransform` entry.
