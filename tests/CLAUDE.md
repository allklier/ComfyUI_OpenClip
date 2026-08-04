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
| `test_ocio.ocio` | Minimal OCIO v2 config with a Display/View split mirroring an ACES config: a `"Raw"` view (identity passthrough) and an `"ACES 2.0 - SDR 100 nits (Rec.709)"` view whose `ViewTransform` halves values (stand-in for real tone-mapping) |

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
| `test_fps_label_from_float_exact_integer_rate` | `25.0` matches label `"25"` |
| `test_fps_label_from_float_matches_ntsc_decimal_approximation` | `23.976` (decimal approximation) matches label `"23.976"`, not `"24"` |
| `test_fps_label_from_float_matches_exact_rational` | `24000/1001` (the true rational) matches label `"23.976"` |
| `test_fps_label_from_float_out_of_tolerance_raises` | A value with no nearby supported rate (`26.5`) raises `ValueError` |
| `test_missing_version_raises` | Requesting a version name not in the clip raises `ValueError` |
| `test_current_version_fallback` | `version="current"` resolves to the `currentVersion` attribute value |
| `test_next_version_no_existing_returns_v001` | `next_version({})` returns `"v001"` |
| `test_next_version_increments_highest` | `next_version` returns one past the highest existing `v<NNN>` |
| `test_next_version_ignores_non_numbered_names` | A non-`v<NNN>` version name (e.g. `"final"`) doesn't affect the computed next number |
| `test_next_version_preserves_padding_width` | `v009` → `v010`, not `v10` |
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

### `test_colour_transform.py`

| Test | What it checks |
|---|---|
| `test_apply_raw_view_is_identity` | `view="Raw"` (no view transform) passes values through unchanged |
| `test_apply_default_view_applies_view_transform` | Default view actually applies the view's transform (not skipped) — regression test for the plain-`getProcessor`-into-a-Display-space bug, see `lib/CLAUDE.md` |
| `test_apply_scale_transform` | `scaled_2x` colour space converts correctly through to the reference space |
| `test_apply_transform_does_not_modify_input` | Input tensor is never mutated |
| `test_apply_transform_returns_float32` / `test_apply_transform_preserves_shape` | dtype and shape invariants |
| `test_apply_transform_wrong_channels_raises` | 4-channel input raises `ValueError` |
| `test_node_mask_passthrough` / `test_node_no_mask_returns_zeros` | `OpenClipColourTransform.execute()` MASK behaviour |
| `test_node_raises_on_empty_config` / `test_node_raises_on_empty_space` / `test_node_raises_on_empty_view` | Required-input validation |
| `test_node_output_constant` | End-to-end node call using the real default `view` |

---

## System Tests — `test_system.py`

System tests write to a temporary directory (`tmp_path` pytest fixture) and make no assumptions about ComfyUI being running.

| Test | What it checks |
|---|---|
| `test_read_v8_clip_end_to_end` | Load `v8_single_version.clip` fixture via Reader logic; verify IMAGE shape, `width`, `height`, `frame_count` |
| `test_reader_is_changed_stable_when_unchanged` | `OpenClipReader.IS_CHANGED()` returns the same value for the same inputs, so ComfyUI's cache is reused when nothing changed |
| `test_reader_is_changed_detects_rerender` | Overwriting the first frame in place (same path, new mtime) changes `IS_CHANGED()`'s return value, forcing ComfyUI to re-execute instead of serving a stale cached read |
| `test_read_v8_multi_version_select` | Load `v8_multi_version.clip` requesting `v003`; verify frames from that version, not `currentVersion` |
| `test_read_v8_current_version` | Load with `version="current"`; frames match the `currentVersion` version |
| `test_write_standard_flame_layout` | Write 4 frames as EXR + Standard Flame layout; verify directory tree, `.clip` XML exists, media paths resolve to actual files |
| `test_write_include_version_in_filename` | Write with `include_version_in_filename=True`; frame files are named `myshot.v001.####.exr` while the `.clip` filename stays `myshot.clip` |
| `test_write_flat_layout` | Same for Flat layout |
| `test_write_png_sequence` | Write 4 frames as PNG; files exist and are valid images |
| `test_write_publish_sidecar` | Write with `publish=True`; `<clip>.<version>.comfy.json` exists in the version media dir; XML has `<comfyWorkflow>` inside the version's `<userData>`, not at root level |
| `test_write_no_publish_no_sidecar` | Write with `publish=False`; no `.comfy.json` written |
| `test_round_trip_rgb` | Write 4 RGB frames; read them back; pixel values match within float tolerance |
| `test_round_trip_rgba` | Write 4 RGBA frames; read back (alpha always loaded by the Reader node); RGB and alpha match within tolerance |
| `test_round_trip_preserves_fps` | Write with `fps=23.976` (decimal approximation of 24000/1001); Reader's `fps` output matches the exact rational, not the literal input |
| `test_round_trip_new_version` | Write `v001` then write `v002` to the same clip dir; read back `v002` and verify correct frames |
| `test_exr_half_vs_float_precision` | Write same frame as half and float; float preserves more precision; both readable |
| `test_exr_compression_variants` | Write ZIP, PIZ, DWAB; all produce valid EXRs that round-trip pixel data |
| `test_reader_resolves_current_to_actual_version_name` | Reader's `version_name` output is the actual resolved name (e.g. `v003`), never the literal `"current"`; `available_versions` lists all versions with `  (current)` marking the right one |
| `test_reader_resolves_explicit_version_name` | Requesting an explicit version name resolves `version_name` to that same name |
| `test_write_next_version_defaults_to_v001_on_new_clip` | `version_name="next"` on a fresh clip resolves to `v001` |
| `test_write_next_version_increments_past_existing` | Three successive writes with `version_name="next"` produce `v001`, `v002`, `v003` |
| `test_write_overwrite_replaces_version_and_deletes_stale_frames` | `overwrite=True` on an existing `version_name` deletes that version's old frame files before writing fewer new ones — no orphaned stale frames remain |
