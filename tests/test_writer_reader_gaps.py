"""Writer / Reader / XML cases with thin or no coverage elsewhere: clip_filename tokens,
fps edge cases, wired colour_space, CLIP_METADATA carry-through, version-collision message,
path remap through the Reader node, static-image clips, overwrite scoping, publish edge cases.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import torch

from ComfyUI_OpenClip.lib import image_io, openclip_xml
from ComfyUI_OpenClip.lib.openclip_xml import ClipSpan, ClipVersion
from ComfyUI_OpenClip.nodes.reader import OpenClipReader
from ComfyUI_OpenClip.nodes.writer import OpenClipWriter, _normalize_to_folder, _resolve_destination


def _w(tmp_path, **over):
    kw = dict(clip_path=str(tmp_path), clip_name="s", clip_filename="$(path)/$(clip_name).clip", version_name="next",
              fps=24.0, start_frame=1, frame_padding=4, file_format="EXR", exr_bit_depth="half (16-bit)",
              exr_compression="ZIP")
    kw.update(over)
    return kw


def _write(tmp_path, n=2, image=None, **over):
    return OpenClipWriter().execute(IMAGE=image if image is not None else torch.rand(n, 8, 8, 3), **_w(tmp_path, **over))[0]


def _read(clip, **over):
    kw = dict(clip_path=clip, version="current", start_frame=-1, end_frame=-1, path_from="", path_to="")
    kw.update(over)
    return OpenClipReader().execute(**kw)["result"]


# --- clip_filename token expansion ---------------------------------------------------------


@pytest.mark.parametrize("pattern, expected", [
    ("$(path)/$(clip_name).clip", ("/a/b", "nm")),
    ("$(path)/$(clip_name)_clean", ("/a/b", "nm_clean")),
    ("$(path)/$(clip_name)_clean.clip", ("/a/b", "nm_clean")),
    ("$(path)/../sib/$(clip_name).clip", ("/a/sib", "nm")),
    ("$(path)/sub/dir/$(clip_name)", ("/a/b/sub/dir", "nm")),
    ("/abs/elsewhere/fixed", ("/abs/elsewhere", "fixed")),
    ("/x/y/shot.CLIP", ("/x/y", "shot")),            # suffix match is case-insensitive
    ("$(path)/$(clip_name)/", ("/a/b", "nm")),        # trailing slash normalised away
    ("$(path)//$(clip_name).clip", ("/a/b", "nm")),   # doubled separator
    ("$(path)/pre_$(clip_name)_$(clip_name).clip", ("/a/b", "pre_nm_nm")),  # repeated token
])
def test_resolve_destination_expansion(pattern, expected):
    # clip_path given as a .clip file -> its folder is what $(path) means
    assert _resolve_destination("/a/b/old.clip", "nm", pattern) == expected


def test_resolve_destination_accepts_a_folder_clip_path_with_dotted_name():
    assert _resolve_destination("/a/b/v1.2", "nm", "$(path)/$(clip_name).clip") == ("/a/b/v1.2", "nm")


@pytest.mark.parametrize("args, match", [
    (("", "nm", "$(path)/x"), "clip_path is required"),
    (("/a", "", "$(clip_name)"), "clip_name is required"),
    (("/a", "n", ""), "clip_filename is required"),
    (("/a", "n", "shot"), "parent directory"),
    (("/a", "n", "$(clip_name)"), "parent directory"),
])
def test_resolve_destination_errors(args, match):
    with pytest.raises(ValueError, match=match):
        _resolve_destination(*args)


def test_resolve_destination_without_tokens_needs_neither_path_nor_name():
    assert _resolve_destination("", "", "/o/fixed.clip") == ("/o", "fixed")


def test_normalize_to_folder():
    assert _normalize_to_folder("") == ""
    assert _normalize_to_folder("/a/b/c.clip") == "/a/b"
    assert _normalize_to_folder("/a/b/c.CLIP") == "/a/b"
    assert _normalize_to_folder("/a/b") == "/a/b"


def test_writer_dotdot_destination_lands_beside_the_source(tmp_path):
    (tmp_path / "src").mkdir()
    clip = _write(tmp_path, clip_path=str(tmp_path / "src"), clip_filename="$(path)/../out/$(clip_name).clip")
    assert Path(clip) == tmp_path / "out" / "s" / "s.clip"


def test_writer_suffix_pattern_names_clip_and_frames_alike(tmp_path):
    clip = _write(tmp_path, clip_filename="$(path)/$(clip_name)_clean")
    assert Path(clip).name == "s_clean.clip"
    assert sorted(p.name for p in Path(clip).parent.rglob("*.exr")) == ["s_clean.0001.exr", "s_clean.0002.exr"]


# --- fps edge cases ----------------------------------------------------------------------------


@pytest.mark.parametrize("value, label", [
    (23.976, "23.976"), (24000 / 1001, "23.976"), (24.0, "24"), (24.0004, "24"), (25, "25"),
    (29.97, "29.97"), (30000 / 1001, "29.97"), (30.0, "30"), (48, "48"), (50, "50"), (59.94, "59.94"), (60, "60"),
])
def test_fps_label_from_float_accepts(value, label):
    assert openclip_xml.fps_label_from_float(value) == label


# 23.98 (a common 2-decimal rounding of 23.976) is outside the 1e-3 tolerance: characterised here, not endorsed.
@pytest.mark.parametrize("value", [0, -24.0, 24.01, 12, 23.9, 23.98, 120, math.inf, -math.inf])
def test_fps_label_from_float_rejects(value):
    with pytest.raises(ValueError, match="does not match any supported rate"):
        openclip_xml.fps_label_from_float(value)


def test_fps_label_from_float_rejects_nan():
    with pytest.raises(ValueError):
        openclip_xml.fps_label_from_float(float("nan"))


def test_fps_label_tolerance_is_adjustable():
    assert openclip_xml.fps_label_from_float(24.4, tol=0.5) == "24"


def test_fps_to_rational_unknown_label_defaults_to_24():
    assert openclip_xml.fps_to_rational("23.976") == (24000, 1001)
    assert openclip_xml.fps_to_rational("bogus") == (24, 1)


@pytest.mark.parametrize("fps, label", [(24.0, "24"), (25.0, "25"), (30000 / 1001, "29.97"), (60.0, "60")])
def test_writer_to_reader_fps_round_trip(tmp_path, fps, label):
    num, den = openclip_xml._FPS_TABLE[label]
    assert _read(_write(tmp_path, fps=fps))[13] == pytest.approx(num / den)


# --- wired colour_space -----------------------------------------------------------------------------


def test_writer_colour_space_default_is_the_flame_display_name(tmp_path):
    clip = _write(tmp_path)
    assert openclip_xml.read_format(clip).colour_space == "Rec.1886 Rec.709 - Display"


def test_writer_colour_space_input_lands_in_the_clip_xml(tmp_path):
    clip = _write(tmp_path, colour_space="ACEScg")
    assert openclip_xml.read_format(clip).colour_space == "ACEScg"


def test_writer_colour_space_xml_pipe_is_independent_of_the_exr_header_pipe(tmp_path):
    # The XML <colourSpace> and the EXR-header colour space (via CLIP_METADATA) are different pipes.
    clip = _write(tmp_path, colour_space="ACEScg", CLIP_METADATA={"oiio:ColorSpace": "ARRI LogC4 / AWG4"})
    assert openclip_xml.read_format(clip).colour_space == "ACEScg"
    assert _read(clip)[12] == "ARRI LogC4 / AWG4"


def test_writer_colour_space_of_a_second_version_is_not_validated_against_the_first(tmp_path):
    # Characterisation: only width/height/fps/file_format are format-mismatch checked on merge.
    _write(tmp_path, colour_space="ACEScg")
    clip = _write(tmp_path, colour_space="sRGB")
    assert openclip_xml.read_format(clip).colour_space == "sRGB"


# --- CLIP_METADATA carry-through --------------------------------------------------------------------------


def test_metadata_scalars_round_trip_through_writer_and_reader(tmp_path):
    meta = {"Artist": "jan", "take": 7, "exposure": 1.5}
    out = _read(_write(tmp_path, CLIP_METADATA=meta))[10]
    assert out["Artist"] == "jan" and out["take"] == 7 and out["exposure"] == pytest.approx(1.5)


def test_writer_format_options_beat_carried_metadata(tmp_path):
    clip = _write(tmp_path, exr_compression="PIZ", CLIP_METADATA={"compression": "zip"})
    assert _read(clip)[10]["compression"] == "piz"


def test_unwritable_metadata_values_are_skipped_not_fatal(tmp_path):
    out = _read(_write(tmp_path, CLIP_METADATA={"ok": "yes", "bad": {"nested": 1}, "worse": object()}))[10]
    assert out["ok"] == "yes" and "bad" not in out and "worse" not in out


def test_empty_metadata_dict_is_harmless(tmp_path):
    assert _read(_write(tmp_path, CLIP_METADATA={}))[2] == 2


def test_metadata_is_carried_into_every_frame_not_only_the_first(tmp_path):
    clip = _write(tmp_path, n=3, CLIP_METADATA={"Artist": "jan"})
    media = Path(clip).parent / "versions" / "v001"
    for f in sorted(media.glob("*.exr")):
        _, _, meta = image_io.read_sequence(str(f), 0, 0, load_alpha=True)
        assert meta["Artist"] == "jan", f.name


def test_metadata_does_not_leak_into_png_output(tmp_path):
    clip = _write(tmp_path, file_format="PNG", CLIP_METADATA={"Artist": "jan"})
    assert _read(clip)[2] == 2


# --- version collision / overwrite ------------------------------------------------------------------------


def test_version_collision_error_message_is_unmistakable_and_actionable(tmp_path):
    clip = _write(tmp_path, version_name="v001")
    with pytest.raises(ValueError) as ei:
        _write(tmp_path, version_name="v001")
    msg = str(ei.value)
    assert "VERSION ALREADY EXISTS" in msg
    assert "'v001'" in msg and clip in msg
    for hint in ("'next'", "'overwrite'", "different version_name"):
        assert hint in msg


def test_collision_leaves_existing_frames_and_clip_untouched(tmp_path):
    clip = _write(tmp_path, version_name="v001")
    before = {p: p.read_bytes() for p in Path(clip).parent.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        _write(tmp_path, version_name="v001", image=torch.rand(5, 8, 8, 3))
    assert {p: p.read_bytes() for p in Path(clip).parent.rglob("*") if p.is_file()} == before


def test_overwrite_of_one_version_leaves_other_versions_frames_alone(tmp_path):
    for _ in range(2):
        clip = _write(tmp_path, n=3)
    v1 = sorted((Path(clip).parent / "versions" / "v001").glob("*.exr"))
    stamp = [p.stat().st_mtime_ns for p in v1]
    _write(tmp_path, n=1, version_name="v002", overwrite=True)
    assert [p.stat().st_mtime_ns for p in sorted((Path(clip).parent / "versions" / "v001").glob("*.exr"))] == stamp
    assert len(list((Path(clip).parent / "versions" / "v002").glob("*.exr"))) == 1
    assert openclip_xml.parse(clip).versions["v002"].spans[0].duration == 1


def test_overwrite_true_on_a_fresh_version_name_just_writes(tmp_path):
    _write(tmp_path)
    clip = _write(tmp_path, version_name="v007", overwrite=True)
    assert set(openclip_xml.parse(clip).versions) == {"v001", "v007"}


def test_next_after_a_non_numbered_version_starts_at_v001_alongside_it(tmp_path):
    _write(tmp_path, version_name="final")
    clip = _write(tmp_path)
    assert set(openclip_xml.parse(clip).versions) == {"final", "v001"}


def test_next_preserves_four_digit_padding_of_existing_versions(tmp_path):
    _write(tmp_path, version_name="v0009")
    assert "v0010" in openclip_xml.parse(_write(tmp_path)).versions


def test_include_version_in_filename_and_overwrite_delete_only_that_versions_frames(tmp_path):
    clip = _write(tmp_path, include_version_in_filename=True, layout="Flat")
    clip = _write(tmp_path, include_version_in_filename=True, layout="Flat")  # v002 in the same flat dir
    _write(tmp_path, include_version_in_filename=True, layout="Flat", version_name="v002", overwrite=True, n=1)
    names = sorted(p.name for p in Path(clip).parent.glob("*.exr"))
    assert names == ["s.v001.0001.exr", "s.v001.0002.exr", "s.v002.0001.exr"]


# --- publish ---------------------------------------------------------------------------------------------------


def test_publish_without_workflow_info_writes_no_sidecar_and_no_reference(tmp_path):
    clip = _write(tmp_path, publish=True, extra_pnginfo=None)
    assert list(Path(clip).parent.rglob("*.comfy.json")) == []
    assert openclip_xml.parse(clip).versions["v001"].publish_path is None


def test_publish_writes_workflow_json_and_reference_survives_a_second_version(tmp_path):
    wf = {"workflow": {"nodes": [{"id": 1}], "links": []}}
    clip = _write(tmp_path, publish=True, extra_pnginfo=wf)
    _write(tmp_path)  # v002 without publish; the merge must keep v001's reference
    parsed = openclip_xml.parse(clip)
    ref = parsed.versions["v001"].publish_path
    assert ref and json.loads((Path(clip).parent / ref).read_text()) == wf["workflow"]
    assert parsed.versions["v002"].publish_path is None


# --- Reader: path remap, static clips, multi-span -----------------------------------------------------------------


def _clip_with_absolute_media(tmp_path, media_root: str):
    pkg = tmp_path / "pkg"
    media = pkg / "versions" / "v001"
    media.mkdir(parents=True)
    image_io.write_sequence(str(media / "c.%04d.exr"), torch.rand(2, 8, 8, 3), None, 1001, "EXR")
    versions = {"v001": ClipVersion(uid="v001", name="v001", spans=[
        ClipSpan(path=f"{media_root}/versions/v001/c.%04d.exr", start_frame=1001, duration=2)])}
    (pkg / "c.clip").write_bytes(openclip_xml.generate("c", versions, "v001"))
    return str(pkg / "c.clip"), pkg


def test_reader_auto_remaps_a_foreign_absolute_media_root(tmp_path):
    clip, _ = _clip_with_absolute_media(tmp_path, "/Volumes/Gone/jobs/c")
    assert _read(clip)[2] == 2


def test_reader_explicit_path_remap(tmp_path):
    clip, pkg = _clip_with_absolute_media(tmp_path, "/nonexistent_root/zz")
    out = _read(clip, path_from="/nonexistent_root/zz", path_to=str(pkg))
    assert out[2] == 2


def test_reader_wrong_explicit_remap_fails_loudly(tmp_path):
    clip, pkg = _clip_with_absolute_media(tmp_path, "/nonexistent_root/zz")
    with pytest.raises(FileNotFoundError):
        _read(clip, path_from="/nonexistent_root/zz", path_to=str(pkg / "elsewhere"))


def test_reader_reads_a_static_still_clip(tmp_path):
    media = tmp_path / "pkg"
    media.mkdir()
    image_io.write_sequence(str(media / "still.%04d.png"), torch.rand(1, 8, 8, 3), None, 0, "PNG")
    (media / "still.0000.png").rename(media / "still.png")
    (media / "still.clip").write_text(
        "<clip version='8'><name>still</name><tracks><track><feeds currentVersion='v1'><feed vuid='v1'>"
        "<storageFormat type='format'><type>video</type></storageFormat>"
        "<spans><span><path encoding='file'>still.png</path></span></spans></feed></feeds></track></tracks></clip>"
    )
    out = _read(str(media / "still.clip"))
    assert out[0].shape == (1, 8, 8, 3) and out[2] == 1


def test_reader_uses_only_the_first_span_of_a_multi_span_version(tmp_path):
    # Characterisation: ClipVersion.spans can hold several, the Reader reads spans[0] only.
    pkg = tmp_path / "pkg"
    (pkg / "m").mkdir(parents=True)
    image_io.write_sequence(str(pkg / "m" / "a.%04d.exr"), torch.rand(2, 8, 8, 3), None, 1, "EXR")
    image_io.write_sequence(str(pkg / "m" / "b.%04d.exr"), torch.rand(3, 8, 8, 3), None, 10, "EXR")
    versions = {"v001": ClipVersion(uid="v001", name="v001", spans=[
        ClipSpan(path="m/a.%04d.exr", start_frame=1, duration=2), ClipSpan(path="m/b.%04d.exr", start_frame=10, duration=3)])}
    (pkg / "c.clip").write_bytes(openclip_xml.generate("c", versions, "v001"))
    assert _read(str(pkg / "c.clip"))[2] == 2


def test_parse_generate_round_trip_keeps_a_multi_span_version(tmp_path):
    versions = {"v001": ClipVersion(uid="v001", name="v001", spans=[
        ClipSpan(path="m/a.%04d.exr", start_frame=1, duration=2), ClipSpan(path="m/b.%04d.exr", start_frame=10, duration=3)])}
    p = tmp_path / "c.clip"
    p.write_bytes(openclip_xml.generate("c", versions, "v001"))
    spans = openclip_xml.parse(str(p)).versions["v001"].spans
    assert [(s.path, s.start_frame, s.duration) for s in spans] == [("m/a.%04d.exr", 1, 2), ("m/b.%04d.exr", 10, 3)]


def test_reader_available_versions_text_for_many_versions_is_sorted_with_current_marked(tmp_path):
    for _ in range(3):
        clip = _write(tmp_path, n=1)
    lines = _read(clip)[14].split("\n")
    assert lines == ["v001", "v002", "v003  (current)"]


def test_reader_schema_version_output_round_trips_v9_fixture_name(v9_single_clip):
    parsed = openclip_xml.parse(str(v9_single_clip))
    assert parsed.schema_version == "9"
