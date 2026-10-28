# tests/ — Test Suite

Run with `pytest` (or `pytest -m "not slow"` to skip the one UHD test). No ComfyUI process is required — nodes are exercised by calling their Python methods directly. OpenImageIO and OpenColorIO **are** installed in the local dev environment, so the suite does real EXR/PNG and OCIO work (≈400 tests, ≈2 s). Not covered by anything here: a live ComfyUI server (executor, caching, socket wiring, node loading) and `web/openclip_reader.js`; those need the acceptance run on akflame5. `slow` is registered in `conftest.py` (`pytest_configure`), not `pyproject.toml`.

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

AOV fixtures (`aov_pixels`, `aov_multipart_fixtures`, `write_multipart_exr`, `conftest.py`) are generated on the fly rather than checked in, matching `image_fixtures`'s pattern. As of the 2026-09-23 AOV redesign there's only one convention to fixture: `aov_multipart_fixtures` generates a **true multi-part** EXR (`aov_multipart.####.exr`) matching the real-world part-name convention confirmed against an actual Flame render (AOVs imported from Cinema 4D): one subimage each for `beauty`/`depth`/`normals`/a Cryptomatte-like part, every part using generic `R,G,B` channel names, identity carried only by each part's `name` attribute — depth is a replicated scalar across `R,G,B`, matching what was observed empirically. `write_multipart_exr` is a callable fixture exposing the underlying multi-part writer helper for tests needing a custom part layout (e.g. a genuinely single-channel part, or non-default names). `aov_pixels` exposes the exact source arrays so tests can assert exact (not just shape) round-trip values. See lib/CLAUDE.md "AOV Channel Handling". (The old `aov_fixtures` fixture — dotted single-part channel names, a separate-sibling-file layout — was removed the same day along with the interface it tested; that convention was this project's own synthetic-fixture invention, never something a real render produced.)

---

## Unit Tests

### `test_openclip_xml.py`

| Test | What it checks |
|---|---|
| `test_parse_v8_single_version` | Parses fixture; version name, path pattern, start frame, duration all match expected values |
| `test_parse_v8_multi_version` | All three versions present; `currentVersion` attribute correctly read |
| `test_parse_v9_single_version` | v9 fixture parsed; same outputs as equivalent v8 clip |
| `test_generate_produces_valid_xml_defaulting_to_schema_6`, `test_generate_writes_explicit_schema_version` | `generate()` writes `version="6"` by default, or the `schema_version` given |
| `test_round_trip_preserves_schema_version` | v8 generated → parse → still v8 |
| `test_parse_without_root_name_uses_file_stem`, `test_current_version_falls_back_…`, `test_parse_reads_first_video_track_not_audio`, `test_parse_stereo_reads_left_eye`, … | Autodesk-example clip shapes, inline so they run without the gitignored example files (issue #4) |
| `test_append_without_versions_block_lists_existing_feeds` | `append_version()` on a clip with no `<versions>` lists the existing feeds as bare entries plus the new one |
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
| `test_read_exr_rgb_no_alpha_channel_defaults_to_opaque` etc. | No alpha channel / `load_alpha=False` → MASK is fully opaque (`ones`), per the 2026-09-23 fix (was zeros) |
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
| `test_probe_aov_names` | `probe_aov_names()` lists every named part in a real-world multi-part EXR, including a Cryptomatte-like part nothing else asks for |
| `test_probe_aov_names_beauty_only_is_empty` | A plain beauty-only file probes to an empty list, not an error |
| `test_read_named_part_multipart_real_world_convention` | `read_named_part()` against the real-world multi-part fixture: a 3-channel replicated-scalar depth populates both `image` and `matte` (`matte`="R"); a genuine 3-channel normals part likewise; a nonexistent name returns `(None, None)` |
| `test_read_named_part_single_channel_is_matte_only` | A genuinely single-channel part (not a replicated scalar) returns `image=None`, `matte`=that one channel |
| `test_read_named_part_absent_in_whole_range_returns_none` | A name never present in any frame of the range returns `(None, None)`, not a fabricated zero tensor |
| `test_read_named_part_zero_fills_frames_individually_missing` | Present in frames 0 and 2 but not 1: whole-sequence signal (found), frame 1 zero-filled just for that frame |
| `test_write_aov_multichannel_round_trip` / `test_write_aov_separate_files_round_trip` | `write_sequence(aov_entries=[(name, image, matte), ...])` round-trips through `probe_aov_names()`/`read_named_part()`; multichannel produces one true multi-part file (no siblings), separate-files produces one sibling file per entry named after the AOV itself |
| `test_write_aov_png_raises` | A non-empty `aov_entries` + `file_format="PNG"` raises `ValueError` |
| `test_write_aov_invalid_layout_raises` | An unknown `aov_layout` value raises `ValueError` |
| `test_write_aov_duplicate_name_raises` | Two `aov_entries` sharing a name raises `ValueError` at the `write_sequence()` level (defense in depth alongside `OpenClipAOVWriter`'s own check) |
| `test_aov_sibling_pattern` | `_aov_sibling_pattern()` inserts a sanitized version of the AOV's own name (not a fixed suffix table) correctly for frame-token, static, and unsafe-character path forms |
| `test_exr_round_trip_does_not_clamp` | An out-of-`0..1`-range image (negative + `>1.0` values) round-trips through EXR unchanged at both half and float bit depth — regression test for `EXR_REQUIREMENTS.md` requirement #1 |
| `test_png_write_does_clamp_intentionally` | The same image written as PNG *does* clamp — locks in that this is intentional (8-bit format), unlike EXR above |
| `test_colour_space_metadata_round_trips_verbatim` | A colour-space value round-trips verbatim through `COLOUR_SPACE_BACKUP_KEY`, including names OIIO's `oiio:ColorSpace` registry would rename or silently drop (`ACEScg`, a camera log profile name) |

| `test_probe_aov_names_lists_dot_named_layers` | Every dot-named layer of a Blender-style single-part file is listed |
| `test_read_sequence_layer_reads_prefixed_beauty_and_alpha` | `layer="CamelMatte.Combined"` reads that layer's R/G/B and A exactly, across frames |
| `test_read_sequence_missing_layer_names_the_available_layers` | A dotted-only file with no `layer` raises and names the layers |
| `test_read_named_part_dot_named_layer` | `ViewLayer.Normal` comes back as X/Y/Z image + X matte, `ViewLayer.Depth` as matte only; a partial prefix (`ViewLayer`) matches nothing |

`blender_multilayer_fixtures` (conftest) writes three frames (`render.0001.exr`–`0003`) with the channel layout of the real Blender 5.2 renders (`BLENDER_CHANNELS`), tagged `lin_ap1_scene`; `blender_frame_pixels(frame)` returns the exact values.

### `test_exr_sequence_reader.py`

| Test | What it checks |
|---|---|
| `test_outputs_match_openclip_reader` | Same `RETURN_TYPES`/`RETURN_NAMES` as `OpenClipReader` |
| `test_frame_token_forms` / `test_no_frame_token_raises` | `####`, `#####`, `%04d` and a dot in the folder name convert correctly; a path with no token raises |
| `test_reads_range_from_disk_and_fills_outputs` | Range found on disk; pixels, matte, placeholders, folder/stem, header colour space and fps come out right |
| `test_frame_cap_and_explicit_range` | `frame_cap` and explicit start/end |
| `test_colour_space_widget_overrides_header` | A non-blank widget wins over the header |
| `test_aov_handle_feeds_openclip_aov_reader` | `aov_handle` → `OpenClipAOVReader("ViewLayer.Normal")` returns the exact normals |
| `test_no_matching_frames_raises` | An empty folder raises `FileNotFoundError` |
| `test_is_changed_stable_and_never_raises` | Stable fingerprint; `""` for a linked (None) or missing path, never an exception |

### `test_package_layout.py`

| Test | What it checks |
|---|---|
| `test_standard_flame_paths` | `build("Standard Flame", ...)` returns correct absolute paths for `.clip`, sidecar, and media dir |
| `test_flat_paths` | `build("Flat", ...)` returns correct paths with no `versions/` subdirectory |
| `test_standard_flame_creates_dirs` | After `build()`, directories exist on disk |
| `test_flat_creates_dirs` | Same for flat layout |
| `test_path_tokens_relative_to_clip` | Media path token in XML is relative; resolves correctly from the `.clip` file's directory |
| `test_unknown_layout_raises` | Passing an unknown layout name raises `ValueError` |
| `test_standard_flame_version_in_filename_is_flames_own_layout`, `test_flat_version_in_filename_uses_underscore` | Toggle on → `<clip>/<clip>_<version>.%04d.ext` beside `<clip>.clip` (Flame's layout); `_` separator in Flat too |
| `test_existing_legacy_standard_clip_keeps_its_layout`, `test_new_layout_clip_wins_over_a_legacy_one` | Pre-2026-10-27 `<clip>/<clip>.clip` + `versions/` is detected and kept; a new-layout clip takes precedence if both exist |

### `test_colour_transform.py`

| Test | What it checks |
|---|---|
| `test_apply_raw_view_is_identity` | `view="Raw"` (no view transform) passes values through unchanged |
| `test_apply_default_view_applies_view_transform` | Default view actually applies the view's transform (not skipped) — regression test for the plain-`getProcessor`-into-a-Display-space bug, see `lib/CLAUDE.md` |
| `test_apply_scale_transform` | `scaled_2x` colour space converts correctly through to the reference space |
| `test_apply_transform_does_not_modify_input` | Input tensor is never mutated |
| `test_apply_transform_returns_float32` / `test_apply_transform_preserves_shape` | dtype and shape invariants |
| `test_apply_transform_wrong_channels_raises` | 4-channel input raises `ValueError` |
| `test_node_mask_passthrough` / `test_node_no_mask_returns_ones` | `OpenClipColourTransform.execute()` MASK behaviour |
| `test_node_raises_on_empty_config` / `test_node_raises_on_empty_space` / `test_node_raises_on_empty_view` | Required-input validation |
| `test_node_output_constant` | End-to-end node call using the real default `view` |
| `test_default_display_falls_back_to_its_legacy_name`, `test_legacy_input_name_resolves_and_inverse_works`, `test_missing_view_lists_the_available_ones`, `test_new_name_wins_when_the_config_has_it` (+ in `test_colour_space_transform.py`: `test_dropdown_entry_resolves_to_its_legacy_name`, `test_unmapped_name_still_fails_loudly_in_a_legacy_config`, `test_every_legacy_name_maps_from_a_name_this_project_uses`) | Pre-2026 Flame name fallback against `fixtures/test_ocio_legacy.ocio`, a self-made config using SynColor names (`Rec.709 video`, `scene-linear Alexa WideGamut`) — Autodesk's legacy configs are not redistributed |

---

## System Tests — `test_system.py`

System tests write to a temporary directory (`tmp_path` pytest fixture) and make no assumptions about ComfyUI being running.

| Test | What it checks |
|---|---|
| `test_read_v8_clip_end_to_end` | Load `v8_single_version.clip` fixture via Reader logic; verify IMAGE shape, `width`, `height`, `frame_count` |
| `test_reader_is_changed_stable_when_unchanged` | `OpenClipReader.IS_CHANGED()` returns the same value for the same inputs, so ComfyUI's cache is reused when nothing changed |
| `test_reader_is_changed_detects_rerender` | Overwriting the first frame in place (same path, new mtime) changes `IS_CHANGED()`'s return value, forcing ComfyUI to re-execute instead of serving a stale cached read |
| `test_reader_is_changed_survives_linked_clip_path` | **Cache regression.** ComfyUI passes `None` for any input driven by a link, so `IS_CHANGED()` must not raise -- an exception becomes `float("NaN")`, which never equals itself, so the node and (via caching.py's ancestry walk) the entire downstream graph re-execute on every queue. A text node feeding `clip_path` caused exactly this in a real workflow |
| `test_reader_is_changed_survives_every_input_linked` | The same, with every input linked at once |
| `test_reader_is_changed_never_raises_on_bad_clip_path` | A nonexistent clip returns the stable fallback rather than raising -- a broken path must fail in `execute()`, with a real error message, not silently destroy caching |
| `test_reader_is_changed_detects_rerender_when_clip_path_is_linked` | **The case the `_LAST_READ_PLAN` memory exists for.** With `clip_path` linked (so `IS_CHANGED` receives `None`), a re-render of the media must still invalidate — otherwise an artist rendering a new clip version is served a stale cached read forever |
| `test_reader_is_changed_returns_constant_before_any_execute` | Before the first `execute()` there is nothing remembered; returns the stable constant rather than raising |
| `test_reader_is_changed_memory_is_per_node_instance` | Two readers on different clips keyed by `UNIQUE_ID` don't share a fingerprint |
| `test_reader_is_changed_absorbs_unknown_inputs` | `**kwargs` absorbs inputs added to `INPUT_TYPES` later, so adding one can't reintroduce the NaN failure |
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
| `test_write_read_aov_multichannel_round_trip` / `test_write_read_aov_separate_files_round_trip` | Full node-level round trip: `OpenClipAOVWriter` chain → `OpenClipWriter.aov_chain` → `.clip` on disk → `OpenClipReader.aov_handle`/`available_aovs` → `OpenClipAOVReader` outputs match, for both `aov_layout` values. The separate-files case confirms the probe lists the sibling AOVs and that the Reader's own `aov_handle` (pointing at the beauty file) reads them back. |
| `test_read_no_aovs_returns_empty_probe` | A clip written without AOVs produces an empty `available_aovs` string, and `OpenClipAOVReader` on any name returns `(None, None)` — no fabricated zero tensor |
| `test_reader_probe_and_extract_real_world_multipart` | Node-level regression test: `OpenClipReader`'s probe and `OpenClipAOVReader` resolve AOVs from a real-world part-name-keyed multi-part EXR built by hand (`write_multipart_exr`), including a Cryptomatte-like part the probe lists but nothing decodes |
| `test_writer_aov_png_raises` | Writer-level `ValueError` when a non-empty `aov_chain` is connected with `file_format="PNG"` |
| `test_writer_duplicate_aov_name_raises` | Two `OpenClipAOVWriter` entries sharing a name raise when chained |
| `test_aov_writer_requires_exactly_one_of_image_matte` | `OpenClipAOVWriter` raises if neither or both of `image`/`matte` are connected |
| `test_colour_space_round_trips_through_writer_and_reader` | `colour_space` survives a full Writer→Reader round trip for a camera-log-style value, via `CLIP_METADATA` |
| `test_colour_space_empty_when_not_carried` | `colour_space` is `""` when no `CLIP_METADATA` was wired |
| `test_autodesk_example` (12 cases), `test_autodesk_expectations_cover_every_example`, `test_autodesk_movie_clips_still_parse` | Every clip in `tests/fixtures/autodesk_examples/` (Flame's `/opt/Autodesk/openclip_examples/`, copied with media, **gitignored** — not ours to redistribute) reads the expected frames or raises its specific clear error. Skipped when the folder is absent; to populate it, `rsync -a akflame5:/opt/Autodesk/openclip_examples/ tests/fixtures/autodesk_examples/` |

---

## Robustness, scale and node-gap suites (added 2026-10-24)

Contract being locked in: **fail loudly and name the file; never return a plausible-looking wrong tensor.** Where behaviour is found to be weaker than that, pin it as `xfail(strict=True)`, so fixing the library flips it to a failure that forces the marker's removal. There are currently none (the four found on 2026-10-24 were fixed in 1.0.1, see bottom).

### `test_robustness.py`

| Test group | What it checks |
|---|---|
| `test_unopenable_frame_mid_sequence_raises_naming_the_file` (EXR+PNG × zero-byte / garbage / 12-byte stub) | `FileNotFoundError` naming the frame file ("OIIO could not open") |
| `test_truncated_body_raises_instead_of_returning_partial_pixels` (EXR+PNG) | Half a file → `RuntimeError` naming the file, not partial pixels |
| `test_corrupt_frame_outside_requested_range_is_never_touched` | A bad frame outside the range is not read |
| `test_corrupt_frame_fails_probe_and_named_part_reads`, `test_aov_reader_node_surfaces_a_corrupt_frame` | `probe_aov_names` / `read_named_part` / `OpenClipAOVReader` fail loudly on a corrupt frame |
| `test_missing_frame_mid_sequence_raises_naming_the_frame` | Gap in a sequence → `FileNotFoundError` with the frame name |
| `test_reader_node_surfaces_a_corrupt_frame`, `test_reader_is_changed_survives_a_corrupt_first_frame` | Node-level; `IS_CHANGED` (stat-only) must not raise even when the frame is corrupt |
| `test_mismatched_resolution_mid_sequence_raises` | 16² → 32² mid-sequence raises `RuntimeError` |
| `test_alpha_present_in_only_some_frames_is_opaque_where_absent` | Frames lacking alpha get an opaque MASK, others keep theirs |
| `test_channels_are_addressed_by_name_not_position` | EXR written as `A,B,G,R` reads back correctly |
| `test_two_channel_exr_raises_with_found_channels_listed`, `test_layer_missing_from_one_frame_raises` | Missing R/G/B (or layer) raises and lists what was found |
| `test_named_part_zero_filled_at_the_frames_own_size_when_frame_lacks_it` | Per-frame zero-fill for a part absent from one frame |
| `test_unparseable_clip_raises_value_error_naming_the_file` (empty / non-XML / binary), `test_truncated_clip_raises_value_error` | `.clip` that is not XML → `ValueError` naming the file (cause: `XMLSyntaxError`) from both `parse()` and `read_format()` — was a raw `XMLSyntaxError` before 2026-10-27 |
| `test_structurally_invalid_clip_raises_value_error` (9 cases) | Missing `<tracks>`/`<track>`/`<feeds>`/`vuid`/`<spans>`/`<path>`, range end < start → `ValueError` with the element named (a missing `<name>` is no longer an error — the file stem is used) |
| `test_read_format_without_storage_format_returns_unknowns`, `test_clip_with_no_feeds_...`, `test_missing_clip_file_raises_file_not_found` | Remaining XML failure modes; no `<storageFormat>` gives 0/"" fields rather than raising |
| `test_unknown_edit_rate_label_silently_falls_back_to_24` | *Characterisation* of the silent 24 fps default |
| `test_reader_*` (empty path, nonexistent clip, unknown version lists available, media deleted, start>end, range beyond media names the first missing frame, zero-span version) | Reader node on broken packages |
| `test_write_*` (unwritable location, bad bit depth/compression writes nothing, mask shorter than images, writer fps validation, resolution / file-format mismatch against an existing clip, corrupt existing clip is not clobbered) | Write-side failure modes |

### `test_large_sequences.py`

| Test | What it checks |
|---|---|
| `test_read_sequence_over_max_frames_...`, `..._at_exactly_max_frames_...`, `test_read_named_part_over_max_frames_raises`, `test_write_sequence_over_max_frames_raises_before_writing` | `MAX_FRAMES` boundary on every entry point (no disk access needed; write leaves nothing behind) |
| `test_empty_or_inverted_range_raises` | `start > end` → `ValueError` |
| `test_clip_xml_version_and_span_caps`, `test_clip_xml_span_cap_on_parse` | `MAX_VERSIONS` (on generate) and `MAX_SPANS` (on parse) |
| `test_long_sequence_round_trips_in_order_with_exact_values` | 240 frames, each filled with `i/1000`; order and exact fp32 values preserved |
| `test_frame_numbers_crossing_the_padding_width_stay_ordered` | `%04d` across 9999→10000 |
| `test_reader_node_frame_cap_on_a_long_clip`, `test_exr_sequence_reader_long_sequence_with_gaps_discovery` | 150-frame clip with cap/offset; 60-frame plain sequence with a gap |
| `test_aov_reader_over_a_long_multipart_sequence` | 60 multi-part frames, single-channel part → matte only, correct per-frame values |
| `test_writer_next_version_many_times_stays_sequential` | 12 successive `next` writes → `v001…v012`, current is `v012` |
| `test_fp32_frames_with_alpha_round_trip_bit_exact_at_moderate_size` | 3×960×540 fp32 with alpha, values far outside 0..1, `torch.equal` |
| `test_all_compressions_survive_a_non_tile_aligned_odd_size` | 301×517 through ZIP/PIZ/DWAB (DWAB within lossy tolerance, smooth content) |
| `test_uhd_fp32_multi_frame_round_trip_with_aov_and_timing` (`@slow`) | 3×3840×2160 fp32 + depth AOV through Writer→Reader→AOVReader, bit-exact, <120 s (≈1 s in practice) |

### `test_harmonize_nodes.py`

| Test group | What it checks |
|---|---|
| `test_aov_writer_*` | Blank/whitespace name, name stripped, chain order preserved and input chain not mutated, duplicate detected after stripping across a long chain, error messages name the AOV |
| `test_four_channel_aov_...`, `test_two_channel_aov_...`, `test_mixed_chain_...`, `test_aov_survives_half_precision_...`, `test_aov_names_with_awkward_characters_...`, `test_aov_chain_with_mismatched_batch_length_...` | Real Writer→Reader→AOVReader round trips for shapes not covered before (4-ch → matte is channel 0; mixed image/matte chain; HDR/negative values not clamped; sanitised sibling filenames; chain shorter than the batch fails) |
| `test_aov_reader_*` | Blank/stripped `part_name`, unknown part → `(None, None)`, names are case-sensitive, Cryptomatte-like and beauty parts addressable, start>end, `INPUT_TYPES` shape |
| `test_aov_is_changed_*` | **First coverage of `OpenClipAOVReader.IS_CHANGED`**: stable, detects a re-render, never raises on bad input, falls back to the last `execute()` for a linked handle |
| `test_to_printf_pattern_*`, `test_blank_sequence_path_raises`, `test_nonexistent_directory_...` | `ExrSequenceReader` path parsing: missing/ambiguous tokens, hashes in directory names, blank / missing folder |
| `test_plain_rgba_sequence_...`, `test_plain_rgb_sequence_gets_an_opaque_mask`, `test_clip_name_is_the_filename_stem_...` | Plain (non-Blender) sequences, opaque MASK, clip_name derivation |
| `test_discovery_*`, `test_explicit_start_only_and_end_only_...`, `test_start_after_end_raises`, `test_frame_cap_zero_raises_...` | Disk discovery (other stems/extensions ignored, numeric sort, regex metacharacters in stem), half-open ranges, `frame_cap=0` characterisation |
| `test_blank_layer_on_a_blender_...`, `test_unknown_layer_...`, `test_layer_widget_is_stripped`, `test_relative_sequence_path_...`, `test_printf_style_path_is_accepted`, `test_header_colour_space_...`, `test_metadata_output_...`, `test_fps_output_...`, `test_ui_payload_...` | Layer handling, relative path via `folder_paths`, outputs |
| `test_is_changed_*` (ExrSequenceReader) | Re-rendered first frame detected; linked path falls back to `_LAST_READ_PLAN` |
| `test_exr_reader_outputs_wire_into_writer_and_round_trip_via_openclip_reader` | ExrSequenceReader → AOVReader → AOVWriter → OpenClipWriter (metadata, colour_space, start_frame, clip_name wired) → OpenClipReader → AOVReader: pixels, mask, AOV bit-exact |
| `test_cst_*` | `OpenClipColourSpaceTransform`: dropdown layout, whitespace stripping, whitespace-only pipe value, pipe ignored unless target is "Same as input", identity on same space, unknown space / unreadable config raise `PyOpenColorIO.Exception`, HDR/negative not clamped, batch/non-square/float32/input untouched, mask returned as-is, label values unique |
| `test_cst_every_dropdown_label_resolves_in_the_real_flame_config` | Runs only where Flame's ACES 2.0 config exists (skipped locally, e.g. on akflame5 it would run): every `CAMERA_COLOUR_SPACES` value exists in that config and converts to ACEScg |

### `test_writer_reader_gaps.py`

| Test group | What it checks |
|---|---|
| `test_resolve_destination_*`, `test_normalize_to_folder`, `test_writer_dotdot_...`, `test_writer_suffix_pattern_...` | `clip_filename` tokens: suffixes, `..`, repeated tokens, case-insensitive `.clip`, trailing/double slashes, all error cases, tokenless absolute path |
| `test_fps_label_from_float_accepts/rejects`, `..._tolerance_is_adjustable`, `test_fps_to_rational_...`, `test_writer_to_reader_fps_round_trip` | fps matching incl. rational NTSC values; rejects 0/negative/inf/off-grid values; `23.98` is currently *rejected* (characterised) |
| `test_writer_colour_space_*` | Wired `colour_space` input lands in the XML, default is the Flame display name, independent of the EXR-header pipe, second version's value is not mismatch-checked (characterised) |
| `test_metadata_*`, `test_writer_format_options_beat_carried_metadata`, `test_unwritable_metadata_values_are_skipped_not_fatal` | `CLIP_METADATA` carry-through: scalars, every frame, writer options win, un-settable values skipped, PNG unaffected |
| `test_version_collision_error_message_...`, `test_collision_leaves_existing_...`, `test_overwrite_*`, `test_next_*`, `test_include_version_in_filename_and_overwrite_...` | Collision message contents (banner, version, clip path, three fixes), nothing modified on collision, overwrite scoped to one version (incl. Flat layout with versioned filenames), `next` with non-numbered / 4-digit-padded versions |
| `test_writer_rejects_names_that_leave_their_folder_and_writes_nothing`, `test_writer_allows_dots_inside_a_name`, `test_overwrite_with_glob_characters_in_the_name_leaves_other_clips_frames_alone` | Security fixes from the 2026-10-28 scan triage: `clip_name`/`version_name` with `/`, `\`, `.`, `..` raise and write nothing (`shot..v2` is fine); overwriting `sh*` no longer deletes `shotB`'s frames. All fail with the fixes switched off |
| `test_publish_*` | Publish without workflow info; sidecar + reference survive a later non-publish version |
| `test_reader_auto_remaps_...`, `test_reader_explicit_path_remap`, `test_reader_wrong_explicit_remap_fails_loudly` | Foreign absolute media roots through the Reader node |
| `test_new_clip_*`, `test_invalid_format_version_*`, `test_append_*`, `test_overwrite_replaces_feed_and_version_in_place`, `test_wired_format_version_*`, `test_matching_format_version_*`, `test_format_version_input_is_optional_and_last` | Issue #4: new clip defaults to schema 6 / explicit value; invalid value raises before disk; appending keeps the schema and every Flame element (`FLAME_CLIP` fixture modelled on a real v6 corpus clip, incl. audio track and double-encoded "AutodeskÂ®") and adds exactly one feed + one version; overwrite replaces in place; differing wired value warns and keeps the file's |
| `test_append_to_legacy_standard_clip_keeps_the_old_layout`, `test_overwrite_with_version_in_filename_also_removes_old_dot_named_frames` | Old-layout clips stay appendable (new version lands in `versions/<v>/`, both versions readable); overwrite with the toggle on also clears `<clip>.<version>.*` frames from before the separator change |
| `test_reader_reads_a_static_still_clip`, `test_reader_uses_only_the_first_span_...`, `test_parse_generate_round_trip_keeps_a_multi_span_version`, `test_reader_available_versions_text_...` | Static-image clip end to end, multi-span behaviour (Reader reads `spans[0]` only), version list text |

### Bugs found 2026-10-24, fixed in 1.0.1 (xfail markers removed)

| Test | Bug that was fixed |
|---|---|
| `test_writer_unsupported_fps_leaves_nothing_on_disk`, `test_writer_format_mismatch_leaves_no_new_frames_behind` | Writer validated fps / existing-clip format after writing frames, leaving orphans; now validates first |
| `test_fps_label_from_float_rejects_nan` | `fps_label_from_float(nan)` returned `"23.976"` |
| `test_mismatched_resolution_error_names_the_offending_frame` | Mid-sequence size change gave a bare `torch.stack` error; now names the file |
| `test_write_static_path_without_frame_token`, `test_write_multiple_frames_to_token_less_path_raises` | `write_sequence` used `%` instead of `frame_path()`; a token-less path now writes a still, several frames to one raises |
