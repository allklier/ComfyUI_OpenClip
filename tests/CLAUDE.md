# tests/ — Test Suite

Run with `pytest`. No ComfyUI process is required — nodes are exercised by calling their Python methods directly.

## Fixtures

Fixtures live in `tests/fixtures/` and are checked into the repo:

| File | Description |
|---|---|
| `v8_single_version.clip` | Minimal v8 clip with one version |
| `v8_multi_version.clip` | v8 clip with three versions; `currentVersion` is `v002` |
| `v9_single_version.clip` | Minimal v9 clip |
| `test_rgb.exr` | 64×64 16-bit half RGB EXR |
| `test_rgba.exr` | 64×64 16-bit half RGBA EXR |
| `test_rgb.png` | 64×64 8-bit RGB PNG |
| `test_rgba.png` | 64×64 8-bit RGBA PNG |

---

## Unit Tests

### `test_openclip_xml.py`

| Test | What it checks |
|---|---|
| `test_parse_v8_single_version` | Parses fixture; version name, path pattern, start frame, duration all match expected values |
| `test_parse_v8_multi_version` | All three versions present; `currentVersion` attribute correctly read |
| `test_parse_v9_single_version` | v9 fixture parsed; same outputs as equivalent v8 clip |
| `test_generate_v8` | `generate()` produces valid XML; round-trips through `parse()` unchanged |
| `test_round_trip_preserves_version_schema` | v8 input → parse → generate → output is still v8 |
| `test_missing_version_raises` | Requesting a version name not in the clip raises `ValueError` |
| `test_current_version_fallback` | `version="current"` resolves to the `currentVersion` attribute value |
| `test_publish_metadata_in_xml` | When publish path provided, `<userMetadata>` element with correct relative path is present |
| `test_no_publish_metadata_absent` | Without publish, no `<userMetadata>` element is written |

### `test_image_io.py`

| Test | What it checks |
|---|---|
| `test_read_exr_rgb` | Reads `test_rgb.exr`; tensor shape `(1, 64, 64, 3)`, dtype float32 |
| `test_read_exr_rgba_split` | Reads `test_rgba.exr` with `load_alpha=True`; IMAGE `(1,64,64,3)`, MASK `(1,64,64)` |
| `test_read_exr_rgba_no_alpha` | Reads `test_rgba.exr` with `load_alpha=False`; MASK is all zeros |
| `test_read_png_rgb` | Reads `test_rgb.png`; correct shape and normalised float values |
| `test_read_png_rgba` | Reads `test_rgba.png` with `load_alpha=True`; MASK non-zero |
| `test_write_exr_half_zip` | Writes EXR (half, ZIP); file exists, OIIO reports correct bit depth and compression |
| `test_write_exr_half_piz` | Same for PIZ compression |
| `test_write_exr_half_dwab` | Same for DWAB compression |
| `test_write_exr_float_zip` | Writes EXR (float32, ZIP); bit depth verified |
| `test_write_exr_rgba` | Writes RGBA EXR; re-read alpha channel matches written MASK tensor |
| `test_write_png_rgb` | Writes PNG; file valid, values within tolerance |
| `test_write_png_rgba` | Writes RGBA PNG; alpha channel preserved |
| `test_frame_padding` | `%04d` default produces `frame.0001.exr`; `%06d` produces `frame.000001.exr` |
| `test_read_missing_frame_raises` | Attempting to read a non-existent frame raises `FileNotFoundError` |

### `test_package_layout.py`

| Test | What it checks |
|---|---|
| `test_standard_flame_paths` | `build("Standard Flame", ...)` returns correct absolute paths for `.clip`, sidecar, and media dir |
| `test_flat_paths` | `build("Flat", ...)` returns correct paths with no `versions/` subdirectory |
| `test_standard_flame_creates_dirs` | After `build()`, directories exist on disk |
| `test_flat_creates_dirs` | Same for flat layout |
| `test_path_tokens_relative_to_clip` | Media path token in XML is relative; resolves correctly from the `.clip` file's directory |
| `test_unknown_layout_raises` | Passing an unknown layout name raises `ValueError` |

---

## System Tests — `test_system.py`

System tests write to a temporary directory (`tmp_path` pytest fixture) and make no assumptions about ComfyUI being running.

| Test | What it checks |
|---|---|
| `test_read_v8_clip_end_to_end` | Load `v8_single_version.clip` fixture via Reader logic; verify IMAGE shape, `width`, `height`, `frame_count` |
| `test_read_v8_multi_version_select` | Load `v8_multi_version.clip` requesting `v003`; verify frames from that version, not `currentVersion` |
| `test_read_v8_current_version` | Load with `version="current"`; frames match the `currentVersion` version |
| `test_write_standard_flame_layout` | Write 4 frames as EXR + Standard Flame layout; verify directory tree, `.clip` XML exists, media paths resolve to actual files |
| `test_write_flat_layout` | Same for Flat layout |
| `test_write_png_sequence` | Write 4 frames as PNG; files exist and are valid images |
| `test_write_publish_sidecar` | Write with `publish=True`; `<clip>.<version>.comfy.json` exists in the version media dir; XML has `<comfyWorkflow>` inside the version's `<userData>`, not at root level |
| `test_write_no_publish_no_sidecar` | Write with `publish=False`; no `.comfy.json` written |
| `test_round_trip_rgb` | Write 4 RGB frames; read them back; pixel values match within float tolerance |
| `test_round_trip_rgba` | Write 4 RGBA frames; read back with `load_alpha=True`; RGB and alpha match within tolerance |
| `test_round_trip_new_version` | Write `v001` then write `v002` to the same clip dir; read back `v002` and verify correct frames |
| `test_exr_half_vs_float_precision` | Write same frame as half and float; float preserves more precision; both readable |
| `test_exr_compression_variants` | Write ZIP, PIZ, DWAB; all produce valid EXRs that round-trip pixel data |
| `test_version_selector_wiring` | VersionSelector on `v8_multi_version.clip` returns all three versions and correct `current_version` |
