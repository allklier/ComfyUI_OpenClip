# tests/ — Test Suite

Run with `pytest` (or `pytest -m "not slow"` to skip the one UHD test). No ComfyUI process is required — nodes are exercised by calling their Python methods directly. OpenImageIO and OpenColorIO **are** installed in the local dev environment, so the suite does real EXR/PNG and OCIO work. Markers (`slow`, `flame_clips`) and the `--tier` option are registered in `conftest.py`, not `pyproject.toml`.

## Tiers (2.0, 2026-10-29)

**Why:** up to 1.1.1 every test checked self-consistency (our Writer ↔ our Reader). Issue #7's three bugs (lost start timecode, empty colour tag, trimmed name) passed all of them, because the Writer and Reader agreed with each other and nothing compared the output with what Flame wrote. The acceptance run tested ComfyUI behaviour, not Flame compatibility. The tiers below add comparisons against the *source* clip, from synthetic Flame-shaped clips (always) and real Flame exports (when present). Modelled on LTX Alpha Gen's tiers (decisions 67/77 there).

| Tier | Contents | Command | Where / when |
|---|---|---|---|
| **smoke** | unit tests + synthetic Flame-like boundary cases (`test_flame_like.py`); real-clip tests skip ("full tier only") | `pytest` (= `pytest --tier smoke`), ≈700 tests, ≈4 s | CI (`tests.yml`) on every push/PR/release; locally after every change |
| **full** | smoke + real-clip fidelity (`test_flame_clips.py`, Autodesk examples, `testclips/`) — skip per clip when absent | `pytest --tier full` (only the real clips: `pytest --tier full -m flame_clips`) | maintainer, before promotion |
| **full + live** | full + automated acceptance on a live ComfyUI (normal and without OCIO) → `openclip-test-<ref>.json` | `scripts/maintainer_test.sh <ref> --host akflame5` (see `docs/ci.md`) | maintainer, before promotion |
| **manual Flame check** | 5 steps in Flame | `docs/FLAME_TEST.md` | user/tester, as needed |

**Synthetic vs real split.** The synthetic cases (`flame_fixtures.py`) are built from the structure of real exports (the repro clip, the v6 corpus clip) and cover every boundary we know of, on every push. What they cannot prove is that Flame writes those shapes: drop-frame (`dropMode` spelling, `nbTicks` meaning) and non-ASCII names are assumptions there. The real clips answer that, and run the *same* checks (`flame_roundtrip.py`), so a real clip can be added without writing a test. Real clips are never in git (`tests/fixtures/flame/` gitignored; `tests/flame_clips.md` is the manifest of what to export).

**Comparison helper** (`flame_compare.py`): reads the fields Flame uses from the picture track (`feed_fields`), derives what the Writer must write from the *source* (`expected_feed`), and reports differences as readable strings (`new_clip_diffs`, `append_diffs`, `exr_frame_diffs`, `pixel_diffs`). Its timecode label/BCD code and frame-rate table are deliberately separate implementations from `lib/timecode.py`/`lib/openclip_xml.py`, so a shared bug can't agree with itself. Since 2026-10-29 it also expects the source's first-frame EXR `smpte:TimeCode` as the start timecode when the XML has none (`exr_tc_field`), and the source's `channelsDepth`. Three guard tests make sure the helper does catch a lost `nbTicks`, a lost EXR-only timecode and bit depth, and a changed original element.

Not covered by anything here: `web/openclip_reader.js` in a browser, and Flame itself (`docs/FLAME_TEST.md`).

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
| `test_outputs_match_openclip_reader` | Same 9 `RETURN_TYPES`/`RETURN_NAMES` as `OpenClipReader` (2.0), `IMAGE`/`MASK` at 0/1 |
| `test_frame_token_forms` / `test_no_frame_token_raises` | `####`, `#####`, `%04d` and a dot in the folder name convert correctly; a path with no token raises |
| `test_reads_range_from_disk_and_fills_outputs` | Range found on disk; pixels, matte, `clip_info` (folder/stem, header colour space, fps, padding, empty version/schema, no timecode in that fixture) and `clip_report` (no VERSIONS) come out right |
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
| `test_reader_resolves_current_to_actual_version_name` | `clip_info["version_name"]` is the actual resolved name (e.g. `v003`), never the literal `"current"`; `clip_report`'s VERSIONS lists all versions with `(current)` and `← loaded` |
| `test_reader_resolves_explicit_version_name` | Requesting an explicit version name resolves `clip_info["version_name"]` to that same name |
| `test_write_next_version_defaults_to_v001_on_new_clip` | `version_name="next"` on a fresh clip resolves to `v001` |
| `test_write_next_version_increments_past_existing` | Three successive writes with `version_name="next"` produce `v001`, `v002`, `v003` |
| `test_write_overwrite_replaces_version_and_deletes_stale_frames` | `overwrite=True` on an existing `version_name` deletes that version's old frame files before writing fewer new ones — no orphaned stale frames remain |
| `test_write_read_aov_multichannel_round_trip` / `test_write_read_aov_separate_files_round_trip` | Full node-level round trip: `OpenClipAOVWriter` chain → `OpenClipWriter.aov_chain` → `.clip` on disk → `OpenClipReader.aov_handle`/`available_aovs` → `OpenClipAOVReader` outputs match, for both `aov_layout` values. The separate-files case confirms the probe lists the sibling AOVs and that the Reader's own `aov_handle` (pointing at the beauty file) reads them back. |
| `test_read_no_aovs_returns_empty_probe` | A clip written without AOVs produces an empty `available_aovs` string, and `OpenClipAOVReader` on any name returns `(None, None)` — no fabricated zero tensor |
| `test_reader_probe_and_extract_real_world_multipart` | Node-level regression test: `OpenClipReader`'s probe and `OpenClipAOVReader` resolve AOVs from a real-world part-name-keyed multi-part EXR built by hand (`write_multipart_exr`), including a Cryptomatte-like part the probe lists but nothing decodes |
| `test_writer_aov_png_raises` | Writer-level `ValueError` when a non-empty `aov_chain` is connected with `file_format="PNG"` |
| `test_writer_duplicate_aov_name_raises` | Two `OpenClipAOVWriter` entries sharing a name raise when chained |
| `test_aov_writer_requires_exactly_one_of_image_matte` | `OpenClipAOVWriter` raises if neither or both of `image`/`matte` are connected |
| `test_colour_space_round_trips_through_writer_and_reader` | A camera-log-style colour survives a full Writer→Reader round trip via `clip_info` |
| `test_colour_space_defaults_to_rec709_video_when_not_carried` | 2.0: no colour anywhere → the Writer writes `Rec.709 video` and the Reader reads it back (was `""`) |
| `test_autodesk_example` (12 cases), `test_autodesk_expectations_cover_every_example`, `test_autodesk_movie_clips_still_parse` | Every clip in `tests/fixtures/autodesk_examples/` (Flame's `/opt/Autodesk/openclip_examples/`, copied with media, **gitignored** — not ours to redistribute) reads the expected frames or raises its specific clear error. Full tier only (`flame_clips`); skipped when the folder is absent; to populate it, `rsync -a akflame5:/opt/Autodesk/openclip_examples/ tests/fixtures/autodesk_examples/` |

---

## 2.0: issue #7 and `clip_info` — `test_clip_info.py`

Uses `_flame_clip()`, a synthetic Flame-style clip modelled on the real repro clip (schema 9, 24000/1001, 6-digit range path, XML `colourSpace`, EXRs with `autodeskColorSpace` and `smpte:TimeCode`, file named `woods walk .clip` with the trailing space). `named_result()` (conftest) maps a node's result tuple to `{output name: value}` plus `info.<key>` for `clip_info`.

| Test group | What it checks |
|---|---|
| `test_timecode_*`, `test_smpte_bcd_*` | `lib/timecode.py`: NDF and drop-frame parse/format (incl. a round trip over 0–40000 ticks), bad text, Flame's own BCD value for 10:44:22:06, DF flag, none above 30 fps; BCD decode (`from_smpte_bcd`) of what `smpte_bcd` encodes, Flame's and Autodesk's words, refusals (rate above 30, frame ≥ rate) |
| `test_parse_*`, `test_generate_writes_the_versions_start_timecode` | Feed `startTimecode`/`colourSpace` parsed; names with spaces kept; blank name → stem; `generate()` writes ticks and `DF` |
| `test_issue7_*` | **One regression test per issue-#7 bug** (each was reproduced on 1.1.1 with the old interface): new clip keeps `nbTicks`; append keeps it on the new feed (also without `clip_info`); per-frame EXR `smpte:TimeCode`; Reader colour from XML; colour tag kept Reader→Writer; trailing-space name appends to the source (no `woods walk.clip`); mismatch message suggests a new `clip_name` |
| `test_append_*`, `test_invalid_start_timecode_*`, `test_rates_above_30_*` | The three append rules (mismatch errors also with `overwrite`, nothing on disk changes; same real TC → warning; both 00:00:00:00 → error), a clip with no XML `startTimecode` uses its first frame's EXR timecode (Reader → Writer append lines up), a clip with no timecode at all counts as 00:00:00:00, bad timecode text writes nothing, 50 fps gets the XML timecode but no EXR one |
| `test_writer_*`, `test_version_name_is_never_taken_from_clip_info`, `test_clip_info_with_an_unwritable_schema_version_falls_back_to_6`, `test_clip_name_and_path_still_required_without_clip_info` | Auto fields: defaults without `clip_info`, every value from `clip_info`, every widget overriding it; sockets and auto defaults of `INPUT_TYPES`; `"next"` gives Flame's `v0` → `v1` |
| `test_destination_that_is_the_source_file_by_another_spelling_*`, `test_names_with_spaces_typed_by_hand_*`, `test_name_rules_still_apply_to_clip_info_names` | Exact-file append through a symlinked folder; untrimmed typed names; `_check_name_component` also guards `clip_info` names |
| `test_reader_*`, `test_header_colour_lookup_order`, `test_clip_report_*` | Reader outputs and `clip_info` values; colour order XML → `autodeskColorSpace` → `openclip:ColorSpace` → `oiio:ColorSpace`; start timecode XML → the feed's first frame's EXR timecode (also for a sub-range read; XML wins; none → `""`); `bit_depth` half/float/`""` (PNG), shown once (FORMAT line); exact `clip_report` text and `ui` payload; AOVs one per line |
| `test_writer_bit_depth_auto_takes_clip_info_else_half` | `exr_bit_depth` options start with `auto`; auto = `clip_info`'s depth (float → float), an explicit choice overrides, no `clip_info` or a PNG source → half |
| `test_clip_info_node_*`, `test_clip_info_colour_feeds_colour_space_transform_same_as_input` | `OpenClipClipInfo` outputs (`bit_depth` second to last, `clip_report` last); its `colour_space` drives the Colour Space Transform's "Same as input (via pipe)" |
| `test_exr_sequence_clip_info_drives_a_new_clip`, `test_exr_sequence_start_timecode_from_the_first_frame_read` | EXR Sequence Reader `clip_info` → Writer: folder/stem, padding 5, schema 6, TC 0 when the EXRs have none, header colour, bit depth; start timecode = the first frame read's `smpte:TimeCode` |
| `test_real_flame_clip_round_trip` | Against `testclips/clip_1/alpha_test.clip` (gitignored; copied with its `<path>` rewritten; skips when absent): TC 10:44:22:05, colour, padding 6; new clip and append both get `nbTicks` 927893 and per-frame EXR TC; the original feed is unchanged |

---

## Flame fidelity — `test_flame_like.py` (smoke) and `test_flame_clips.py` (full, 2026-10-29)

Helpers (not test modules): `flame_compare.py` (fields, expectations, diffs, timecode oracle), `flame_fixtures.py` (`FlameLike` case + `build()`: tab-indented Flame-shaped XML, hex uids, track `<name>`, full `<storageFormat>`, `recordTimecode`, absolute `<name>_<version>.[first-last].exr` paths, `<versions>` with Flame userData; EXRs written with plain OIIO carrying PIZ, `autodeskColorSpace`, `framesPerSecond`, per-frame `smpte:TimeCode`), `flame_roundtrip.py` (`new_clip_check`, `append_check`: Reader → Writer with `clip_info` wired, all fields on auto, bit depth included since 2026-10-29; `MASK` wired when the source has alpha).

### `test_flame_like.py` (35 cases × new clip + append, + append-to-an-append for 6)

| Case group | Cases |
|---|---|
| timecode / rate | `tc_0100` (23.976 @ 01:00:00:00), `tc_104422_v01` (10:44:22:05, Flame 2027 `v01` numbering), `tc_drop_2997` (01:00:00;00), `tc_drop_2997_odd_minute` (00:01:00;02), `tc_drop_5994` (no EXR TC above 30), `tc_pal` (25 @ 10:00:00:00), `tc_numbered_86400` (frames from TC, padding 8), `tc_none` (no `<startTimecode>`), `tc_exr_only`, `tc_exr_only_numbered`, `tc_exr_only_drop_2997` (timecode only in the EXRs, `FlameLike(xml_tc=False)`, as Autodesk's examples), `rate_23976_decimal`, `rate_25_decimal_v6` (text `<editRate>`, as the v6 corpus) |
| numbering | `start_0`, `start_1`, `start_1001` (padding 4), `pad_6` |
| names | `name_trailing_space` (`woods walk `), `name_leading_space_non_ascii` (` lead café`) |
| versions | `versions_v0_v1` (→ v2), `versions_v001` (→ v004), `no_versions_block` |
| colour | ACEScg, Rec.709 video, Rec.1886 Rec.709 - Display, Raw, Matte, missing (→ Writer default), EXR-only `autodeskColorSpace`, `aces_alpha_float` (ACEScg, 32-bit, RGBA) |
| structure | audio track after / before the video track, schemas 5–9 |

Regression tests for the two bugs the first maintainer run found on real clips (2026-10-29): `test_aovs_from_a_flame_source_round_trip` (both layouts; Flame's `name="rgb"` header carried onto every AOV part) and `test_append_compares_the_format_with_the_current_version` (mixed-size versions; the format check used the first feed; drives the Writer directly since the Reader refuses such clips). Both fail with the fixes reverted. `test_reader_refuses_a_clip_whose_versions_differ_in_size` (whatever version is asked for; every version's size in the message; `IS_CHANGED` doesn't raise) and `test_reader_skips_versions_with_no_recorded_size` lock in the Reader's mixed-size refusal (user decision 2026-10-29). Plus `test_comparison_helper_catches_*` (the helper fails on a lost `nbTicks` / a changed Flame element) and `test_oracle_*` (oracle vs published values and Flame's own BCD word).

### `test_flame_clips.py` (marker `flame_clips`)

Data-driven; sources and per-clip checks are listed in `tests/flame_clips.md`. Per clip: `test_real_clip_to_new_clip_matches_field_by_field`, `test_real_clip_append_matches_and_keeps_every_flame_element` (also: version numbering continues Flame's), `test_real_clip_append_to_an_append`; `test_planned_clip_covers_what_it_is_for` per planned folder; `test_real_clip_exr_timecode_when_the_xml_has_none`: a source with EXR `smpte:TimeCode` but no XML `<startTimecode>` (Autodesk's examples) is written at that timecode (was a pinned strict-xfail gap until 2026-10-29); `test_real_clip_with_versions_of_different_sizes_is_refused`: a clip whose versions differ in size gets the Reader's clear error (the three round-trip tests then check that error instead of failing). Clips this project can't read (pattern, movie, invalid XML) or whose media is missing skip with the reason. Locally (2026-10-29): `alpha_test.clip` and 6 Autodesk examples pass, no xfails. On akflame5's `~/LocalJobs` the only mixed-size clip is `sh_230_inpaint_front.clip` (v01–v05 1920×1088, v06–v07 1280×720).

The existing `test_autodesk_*` (test_system.py) and `test_real_flame_clip_round_trip` (test_clip_info.py) are marked `flame_clips` too, so smoke is the same set locally and on CI.

---

## Robustness, scale and node-gap suites (added 2026-10-24)

Contract being locked in: **fail loudly and name the file; never return a plausible-looking wrong tensor.** Where behaviour is found to be weaker than that, pin it as `xfail(strict=True)`, so fixing the library flips it to a failure that forces the marker's removal. None in these suites (the four found on 2026-10-24 were fixed in 1.0.1, see bottom); the pinned EXR-timecode gap in `test_flame_clips.py` was fixed on 2026-10-29.

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
| `test_exr_reader_outputs_wire_into_writer_and_round_trip_via_openclip_reader` | ExrSequenceReader → AOVReader → AOVWriter → OpenClipWriter (only `clip_info` wired) → OpenClipReader → AOVReader: pixels, mask, AOV bit-exact |
| `test_cst_*` | `OpenClipColourSpaceTransform`: dropdown layout, whitespace stripping, whitespace-only pipe value, pipe ignored unless target is "Same as input", identity on same space, unknown space / unreadable config raise `PyOpenColorIO.Exception`, HDR/negative not clamped, batch/non-square/float32/input untouched, mask returned as-is, label values unique |
| `test_cst_every_dropdown_label_resolves_in_the_real_flame_config` | Runs only where Flame's ACES 2.0 config exists (skipped locally, e.g. on akflame5 it would run): every `CAMERA_COLOUR_SPACES` value exists in that config and converts to ACEScg |

### `test_writer_reader_gaps.py`

| Test group | What it checks |
|---|---|
| `test_resolve_destination_*`, `test_normalize_to_folder`, `test_writer_dotdot_...`, `test_writer_suffix_pattern_...` | `clip_filename` tokens: suffixes, `..`, repeated tokens, case-insensitive `.clip`, trailing/double slashes, all error cases, tokenless absolute path |
| `test_fps_label_from_float_accepts/rejects`, `..._tolerance_is_adjustable`, `test_fps_to_rational_...`, `test_writer_to_reader_fps_round_trip` | fps matching incl. rational NTSC values; rejects 0/negative/inf/off-grid values; `23.98` is currently *rejected* (characterised) |
| `test_writer_colour_space_*` | `colour_space` lands in the XML, default is `Rec.709 video`, the Writer's colour wins over a carried header colour in XML and EXR alike (2.0: one colour, not two pipes), second version's value is not mismatch-checked (characterised) |
| `test_metadata_*`, `test_writer_format_options_beat_carried_metadata`, `test_unwritable_metadata_values_are_skipped_not_fatal` | Header metadata carry-through (`clip_info["metadata"]`, was `CLIP_METADATA`): scalars, every frame, writer options win, un-settable values skipped, PNG unaffected |
| `test_version_collision_error_message_...`, `test_collision_leaves_existing_...`, `test_overwrite_*`, `test_next_*`, `test_include_version_in_filename_and_overwrite_...` | Collision message contents (banner, version, clip path, three fixes), nothing modified on collision, overwrite scoped to one version (incl. Flat layout with versioned filenames), `next` with non-numbered / 4-digit-padded versions |
| `test_writer_rejects_names_that_leave_their_folder_and_writes_nothing`, `test_writer_allows_dots_inside_a_name`, `test_overwrite_with_glob_characters_in_the_name_leaves_other_clips_frames_alone` | Security fixes from the 2026-10-28 scan triage: `clip_name`/`version_name` with `/`, `\`, `.`, `..` raise and write nothing (`shot..v2` is fine); overwriting `sh*` no longer deletes `shotB`'s frames. All fail with the fixes switched off |
| `test_publish_*` | Publish without workflow info; sidecar + reference survive a later non-publish version |
| `test_reader_auto_remaps_...`, `test_reader_explicit_path_remap`, `test_reader_wrong_explicit_remap_fails_loudly` | Foreign absolute media roots through the Reader node |
| `test_new_clip_*`, `test_invalid_format_version_*`, `test_append_*`, `test_overwrite_replaces_feed_and_version_in_place`, `test_wired_format_version_*`, `test_matching_format_version_*`, `test_format_version_is_a_widget_defaulting_to_auto`, `test_writer_start_frame_defaults_to_auto_then_flames_1` | Issue #4: new clip defaults to schema 6 / explicit value; invalid value raises before disk; appending keeps the schema and every Flame element (`FLAME_CLIP` fixture modelled on a real v6 corpus clip, incl. audio track and double-encoded "AutodeskÂ®") and adds exactly one feed + one version; overwrite replaces in place; differing wired value warns and keeps the file's |
| `test_append_to_legacy_standard_clip_keeps_the_old_layout`, `test_overwrite_with_version_in_filename_also_removes_old_dot_named_frames` | Old-layout clips stay appendable (new version lands in `versions/<v>/`, both versions readable); overwrite with the toggle on also clears `<clip>.<version>.*` frames from before the separator change |
| `test_reader_reads_a_static_still_clip`, `test_reader_uses_only_the_first_span_...`, `test_parse_generate_round_trip_keeps_a_multi_span_version`, `test_reader_available_versions_text_...` | Static-image clip end to end, multi-span behaviour (Reader reads `spans[0]` only), version list text |

### Bugs found 2026-10-24, fixed in 1.0.1 (xfail markers removed)

| Test | Bug that was fixed |
|---|---|
| `test_writer_unsupported_fps_leaves_nothing_on_disk`, `test_writer_format_mismatch_leaves_no_new_frames_behind` | Writer validated fps / existing-clip format after writing frames, leaving orphans; now validates first |
| `test_fps_label_from_float_rejects_nan` | `fps_label_from_float(nan)` returned `"23.976"` |
| `test_mismatched_resolution_error_names_the_offending_frame` | Mid-sequence size change gave a bare `torch.stack` error; now names the file |
| `test_write_static_path_without_frame_token`, `test_write_multiple_frames_to_token_less_path_raises` | `write_sequence` used `%` instead of `frame_path()`; a token-less path now writes a still, several frames to one raises |
