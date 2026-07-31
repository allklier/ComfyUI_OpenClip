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
