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

- `read_sequence()` returns a **one-filled (fully opaque) MASK tensor** when the source has no alpha channel, so callers always receive a valid tensor regardless of source format. **Corrected 2026-09-23** (was zero-filled): standard VFX convention (Nuke, Flame, After Effects) treats an absent alpha channel as fully opaque, not as zero coverage — a plate with no matte is a complete plate, not an invisible one. The old zero-default was a real bug, not a considered choice: it borrowed the "don't fabricate a value" reasoning from the missing-AOV case (see below), which doesn't transfer, since depth/normals have no sensible default to fabricate while alpha does. Found via a real failure: a generated background plate with no alpha, wired into `HM_MatchLuminanceRange`'s `target_mask` downstream in Harmonize, zeroed out every pixel and raised "no valid pixels to measure."
- `write_sequence()` accepts an optional MASK. If provided, it is combined with the IMAGE into a 4-channel RGBA EXR. If absent, a 3-channel RGB EXR is written.
- PNG supports RGBA natively via OIIO.

---

## AOV Channel Handling — `image_io.py`

**Redesigned 2026-09-23: generic, part-name-keyed, matching the real-world convention
only.** The old interface (a fixed three AOVs — `normal`/`normal_world`/`depth` — a
dotted single-part `<layer>.<channel>` convention, and a separate-sibling-file
convention) was this project's own synthetic-fixture invention from before any real
file had been inspected. A real Flame render (beauty + depth + normals + Cryptomatte,
AOVs imported from another DCC, Cinema 4D) confirmed the real convention is different
in every particular: true multi-part EXR, every part using plain generic `R,G,B`
channel names — no dotted `N.X`/`Z` anywhere — with the AOV's identity carried entirely
by the EXR part's own `name` attribute (`"depth"`, `"normals"`, `"cryptomatte"`, ...).
Explicit user decision (2026-09-23): drop the dotted and sibling-file conventions
rather than keep them alongside the real one — AOVs had only been used in
development/testing, nothing released or in production, so there was nothing worth
preserving compatibility for, and building against the confirmed real convention beats
speculatively supporting one that's never actually been observed in the wild.

**Single-part dot-named layers, added 2026-09-28.** Blender's multilayer EXR is a real render that *does* use dotted names: one part, `<view layer>.<pass>.<channel>`, no plain `R/G/B`. `probe_aov_names` lists each distinct prefix in subimage 0 (`_channel_layers`), before the named parts. `read_named_part` tries named parts first; if none matches, `_read_channel_layer_frame` reopens the file and returns subimage 0's `<name>.<c>` channels with the prefix stripped, so `(image, matte)` follows the same rules as a part. The prefix must match whole (`ViewLayer` does not match `ViewLayer.Normal.X`). `read_sequence(..., layer=...)` reads the beauty and alpha from `<layer>.R/G/B/A`.

**Channels are always addressed by name, never index.** OpenEXR does not preserve
channel write order, and OIIO reorders on read — confirmed against the installed OIIO
3.1.15 by round-tripping an actual file: writing `R,G,B,A,N.X,N.Y,N.Z,Nw.X,Nw.Y,Nw.Z,Z`
reads back as `R,G,B,A,Z,N.X,N.Y,N.Z,Nw.X,Nw.Y,Nw.Z` (`spec.channelindex('N.X') == 5`).
`_extract_named_channels()` resolves R/G/B/A via `spec.channelnames` at read time for
beauty; `_split_image_matte()` does the equivalent for a generic AOV part, looking up
`"R"` by name rather than assuming channel 0 is it.

**Two functions replace the old fixed-AOV read path — both generic over AOV name.**

- **`probe_aov_names(path_pattern, frame_num)`** lists every named subimage beyond 0
  (beauty) for one representative frame — header-only (`spec()`, never `read_image()`),
  so it's cheap even when a part (Cryptomatte) is large. This is what
  `OpenClipReader.available_aovs` and its in-node display (`web/openclip_reader.js`)
  are built on.
- **`read_named_part(path_pattern, start_frame, end_frame, part_name)`** decodes one
  named part across a frame range, lazily — nothing is pixel-decoded until this is
  called, and only for the one requested name, so an unrequested part (Cryptomatte
  again) is never touched regardless of how many other AOVs exist in the file. Returns
  `(image, matte)`: a part with exactly 1 channel populates `matte` only (`image` is
  `None` — nothing meaningfully multi-channel to return); a part with >=2 channels
  populates both, `matte` being the `"R"` channel if present (else channel 0).
  Real-world depth-like parts are a **replicated scalar across R/G/B** (confirmed
  empirically: `R==G==B` in the real file), so they're 3-channel on disk and populate
  both outputs, `matte` just happening to equal every channel. Whole-sequence
  presence, not per-frame: found in at least one frame of the range counts the AOV as
  present, and any individual frame that happens to lack it is zero-filled just for
  that frame — carried over unchanged from the pre-redesign behaviour, since it's a
  correct rule regardless of the interface shape around it.

**This repo makes no attempt to label which AOV is which, or what space a normals-like
part is in** — that was always true (see the old `normal_raw` output's reasoning, now
moot since there's no `normal`/`normal_world`/`normal_raw` distinction to draw at all)
and remains true here: `read_named_part()` returns exactly the channels a part has,
under exactly the name it was asked for, and leaves every semantic question — is this
depth or AO, is this normals camera-space or world-space — to the consuming project.
Declaring that meaning, not inferring it, is `OpenClipAOVReader`'s `part_name` widget
being typed by hand off the probe list, not a dropdown of "known" AOV kinds.

**Write side is symmetric.** `write_sequence()`'s `aov_entries` parameter is a list of
`(name, image, matte)` tuples (exactly one of `image`/`matte` populated per entry).
`aov_layout="Multichannel"` (default) writes each entry as its own named subimage in
the *same* file as beauty — a real multi-part EXR via `_write_multipart_exr()`
(`ImageOutput.open(filename, [spec, ...])` then `write_image()` per subimage, the
subsequent ones via `open(filename, spec_i, "AppendSubimage")` — verified against the
installed OIIO 3.1.15 with a real round-trip before writing this, not assumed from
docs). This is a real change from the pre-redesign writer, which only ever wrote a
single multichannel subimage (the old dotted-convention shape) regardless of the
`"Multichannel"` layout's name — the new write path actually produces what the reader
now expects. `aov_layout="Separate Files"` writes one sibling
`<stem>_AOV_<name>.####.ext` file per entry instead (name taken directly from the
entry, sanitized for filesystem safety, not a fixed suffix table). AOVs require
`file_format="EXR"` — PNG's 8-bit clamp-to-uint8 write path would silently corrupt
normals (signed, ~-1..1) or depth (unbounded). Duplicate names in `aov_entries` raise
`ValueError`.

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
