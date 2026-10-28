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
from lxml import etree

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
    assert Path(clip) == tmp_path / "out" / "s.clip"


@pytest.mark.parametrize("field, value", [
    ("clip_name", "../escaped"), ("clip_name", "a/b"), ("clip_name", "a\\b"), ("clip_name", ".."),
    ("version_name", "../v001"), ("version_name", "v/001"), ("version_name", ".."),
])
def test_writer_rejects_names_that_leave_their_folder_and_writes_nothing(tmp_path, field, value):
    # A crafted .clip <name> wired from a Reader must not redirect where the Writer writes.
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError, match=f"{field} .* must be a plain name"):
        _write(out, **{field: value})
    assert list(tmp_path.rglob("*.*")) == []


def test_writer_allows_dots_inside_a_name(tmp_path):
    clip = _write(tmp_path, clip_name="shot..v2", version_name="v1.2")
    assert Path(clip) == tmp_path / "shot..v2.clip"


def test_writer_suffix_pattern_names_clip_and_frames_alike(tmp_path):
    clip = _write(tmp_path, clip_filename="$(path)/$(clip_name)_clean", include_version_in_filename=False)
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


def test_writer_colour_space_default_is_the_name_every_flame_resolves(tmp_path):
    # "Rec.709 video" is the pre-2026 SynColor name; Flame 2026+'s OCIO config keeps it as an
    # alias of "Rec.1886 Rec.709 - Display", so one tag serves 2024-2027 (PR #5 review).
    clip = _write(tmp_path)
    assert openclip_xml.read_format(clip).colour_space == "Rec.709 video"


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
    # Since appends are in place (issue #4), each version's feed keeps its own colour space.
    _write(tmp_path, colour_space="ACEScg")
    clip = _write(tmp_path, colour_space="sRGB")
    spaces = [e.text for e in etree.parse(clip).iter("colourSpace")]
    assert spaces == ["ACEScg", "sRGB"]


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
        clip = _write(tmp_path, n=3, include_version_in_filename=False)
    v1 = sorted((Path(clip).parent / "s" / "v001").glob("*.exr"))
    stamp = [p.stat().st_mtime_ns for p in v1]
    _write(tmp_path, n=1, version_name="v002", overwrite=True, include_version_in_filename=False)
    assert [p.stat().st_mtime_ns for p in sorted((Path(clip).parent / "s" / "v001").glob("*.exr"))] == stamp
    assert len(list((Path(clip).parent / "s" / "v002").glob("*.exr"))) == 1
    assert openclip_xml.parse(clip).versions["v002"].spans[0].duration == 1


def test_overwrite_with_glob_characters_in_the_name_leaves_other_clips_frames_alone(tmp_path):
    # Was a real bug: overwriting "sh*" globbed "sh*.*.exr" and deleted shotB's frames.
    flat = dict(layout="Flat", include_version_in_filename=False, version_name="v001")
    _write(tmp_path, clip_name="shotB", **flat)
    _write(tmp_path, clip_name="sh*", **flat)
    _write(tmp_path, clip_name="sh*", n=1, overwrite=True, **flat)
    assert sorted(p.name for p in tmp_path.glob("*.exr")) == ["sh*.0001.exr", "shotB.0001.exr", "shotB.0002.exr"]


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
    assert names == ["s_v001.0001.exr", "s_v001.0002.exr", "s_v002.0001.exr"]


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


# --- issue #4: schema version and append-in-place ---------------------------------------------------------

# Modeled on a real Flame 2025 export (schema 6) from the shared corpus, cut down to 8x8 so the
# writer's format-mismatch check passes, plus an audio track and an element this project knows
# nothing about. Every one of these must survive an append untouched.
FLAME_CLIP = """<?xml version="1.0" encoding="UTF-8"?>
<clip type="clip" version="{schema}">
\t<name type="string">s</name>
\t<userData type="dict">
\t\t<appName type="binary">AutodeskÂ® FlameÂ®</appName>
\t\t<appVersion type="string">2025.2.1</appVersion>
\t\t<module type="string">Batch</module>
\t</userData>
\t<sourceName type="string">ReelName</sourceName>
\t<tracks>
\t\t<track uid="89504a4c507049c0bbb0489ca799e0f1">
\t\t\t<trackType>video</trackType>
\t\t\t<editRate type="rate">25</editRate>
\t\t\t<name type="string">s_track</name>
\t\t\t<feeds currentVersion="v02">
\t\t\t\t<feed vuid="v01" uid="aaa1">
\t\t\t\t\t<storageFormat type="format">
\t\t\t\t\t\t<type>video</type>
\t\t\t\t\t\t<colourSpace type="string">Rec.709 video</colourSpace>
\t\t\t\t\t\t<height type="uint">8</height>
\t\t\t\t\t\t<rowOrdering type="string">down</rowOrdering>
\t\t\t\t\t\t<width type="uint">8</width>
\t\t\t\t\t</storageFormat>
\t\t\t\t\t<userData type="dict">
\t\t\t\t\t\t<recordTimecode type="time" label="00:00:00:00"><rate>25</rate><nbTicks>0</nbTicks><dropMode>NDF</dropMode></recordTimecode>
\t\t\t\t\t</userData>
\t\t\t\t\t<sampleRate>25</sampleRate>
\t\t\t\t\t<spans><span><path encoding="pattern">/Volumes/LocalJobs/s/s__v01.[000001-000078].exr</path></span></spans>
\t\t\t\t</feed>
\t\t\t\t<feed vuid="v02" uid="aaa2">
\t\t\t\t\t<storageFormat type="format"><type>video</type><height type="uint">8</height><width type="uint">8</width></storageFormat>
\t\t\t\t\t<spans><span><path encoding="pattern">/Volumes/LocalJobs/s/s__v02.[000001-000078].exr</path></span></spans>
\t\t\t\t</feed>
\t\t\t</feeds>
\t\t</track>
\t\t<track uid="t1">
\t\t\t<trackType>audio</trackType>
\t\t\t<feeds><feed vuid="v01" uid="f9"><spans><span><path encoding="file">audio.wav</path></span></spans></feed></feeds>
\t\t</track>
\t</tracks>
\t<versions currentVersion="v02">
\t\t<version uid="v01">
\t\t\t<name>v01</name>
\t\t\t<creationDate>2024/12/22 12:21:46 PM</creationDate>
\t\t\t<batchSetup type="string">/opt/batch/s_v01.batch</batchSetup>
\t\t\t<userData type="dict"><appName type="binary">Flame</appName><versionNumber type="uint64">1</versionNumber></userData>
\t\t</version>
\t\t<version uid="v02">
\t\t\t<name>v02</name>
\t\t\t<creationDate>2024/12/22 12:30:00 PM</creationDate>
\t\t\t<userData type="dict"><versionNumber type="uint64">2</versionNumber></userData>
\t\t</version>
\t</versions>
</clip>
"""


def _flame_clip(tmp_path, schema="6") -> Path:
    clip = tmp_path / "s" / "s.clip"
    clip.parent.mkdir(parents=True)
    clip.write_text(FLAME_CLIP.format(schema=schema), encoding="utf-8")
    return clip


def _assert_original_preserved(before: etree._Element, after: etree._Element, path="clip"):
    """Every element of `before` is still at the same child index in `after`, with the same text
    and attributes (bar currentVersion, which an append moves on purpose). `after` may have
    extra children appended after the originals."""
    strip = lambda a: {k: v for k, v in a.items() if k != "currentVersion"}
    assert after.tag == before.tag, path
    assert (after.text or "").strip() == (before.text or "").strip(), path
    assert strip(after.attrib) == strip(before.attrib), path
    assert len(after) >= len(before), path
    for i, child in enumerate(before):
        _assert_original_preserved(child, after[i], f"{path}/{child.tag}[{i}]")


def _feeds_and_versions(root):
    track = root.find("tracks/track")
    return ([f.get("vuid") for f in track.find("feeds")], [v.get("uid") for v in root.find("versions")])


def test_new_clip_defaults_to_schema_version_6(tmp_path):
    clip = _write(tmp_path)
    assert etree.parse(clip).getroot().get("version") == "6"


@pytest.mark.parametrize("value, expected", [("8", "8"), (" 9 ", "9"), ("5", "5"), ("", "6")])
def test_new_clip_uses_explicit_format_version(tmp_path, value, expected):
    clip = _write(tmp_path, format_version=value)
    assert etree.parse(clip).getroot().get("version") == expected
    assert _read(clip)[5] == expected  # Reader.format_version -> wireable straight back in


@pytest.mark.parametrize("bad", ["10", "v8", "8.0", "4"])
def test_invalid_format_version_raises_before_anything_touches_disk(tmp_path, bad):
    with pytest.raises(ValueError, match="format_version"):
        _write(tmp_path, format_version=bad)
    assert list(tmp_path.rglob("*")) == []


def test_invalid_format_version_on_append_leaves_the_existing_package_alone(tmp_path):
    clip = _write(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*"))
    text = Path(clip).read_text()
    with pytest.raises(ValueError, match="format_version"):
        _write(tmp_path, format_version="x")
    assert sorted(p for p in tmp_path.rglob("*")) == before
    assert Path(clip).read_text() == text


def test_append_to_our_own_v6_clip_keeps_v6_and_adds_one_feed_and_version(tmp_path):
    clip = _write(tmp_path)
    clip = _write(tmp_path, format_version="")
    root = etree.parse(clip).getroot()
    assert root.get("version") == "6"
    assert _feeds_and_versions(root) == (["v001", "v002"], ["v001", "v002"])
    assert root.find("versions/version[@uid='v002']/userData/versionNumber").text == "2"


@pytest.mark.parametrize("schema", ["6", "7", "8"])
def test_append_to_flame_clip_preserves_schema_and_flame_metadata(tmp_path, schema):
    clip = _flame_clip(tmp_path, schema)
    before = etree.parse(str(clip), etree.XMLParser(remove_blank_text=True)).getroot()
    _write(tmp_path, fps=25.0, format_version="")
    root = etree.parse(str(clip), etree.XMLParser(remove_blank_text=True)).getroot()

    assert root.get("version") == schema
    _assert_original_preserved(before, root)
    assert _feeds_and_versions(root) == (["v01", "v02", "v03"], ["v01", "v02", "v03"])
    assert root.find("tracks/track/feeds").get("currentVersion") == "v03"
    assert root.find("versions").get("currentVersion") == "v03"
    assert root.find("versions/version[@uid='v03']/userData/versionNumber").text == "3"
    # The audio track and Flame's own text (including its double-encoded "AutodeskÂ®") are untouched.
    assert [f.get("vuid") for f in root.find("tracks").findall("track")[1].find("feeds")] == ["v01"]
    assert root.findtext("userData/appName") == "AutodeskÂ® FlameÂ®"
    assert _read(str(clip), version="v03")[2] == 2


def test_append_creates_versions_block_when_the_clip_has_none(tmp_path):
    clip = _flame_clip(tmp_path)
    root = etree.parse(str(clip)).getroot()
    root.remove(root.find("versions"))
    clip.write_bytes(etree.tostring(root))
    _write(tmp_path, fps=25.0)
    root = etree.parse(str(clip)).getroot()
    # Existing feeds get bare <version uid=".."/> entries, as in Autodesk's One_Track_Two_Versions.clip.
    assert _feeds_and_versions(root) == (["v01", "v02", "v03"], ["v01", "v02", "v03"])
    assert root.find("versions").get("currentVersion") == "v03"


def test_overwrite_replaces_feed_and_version_in_place(tmp_path):
    clip = _flame_clip(tmp_path)
    _write(tmp_path, fps=25.0, version_name="v01", overwrite=True, include_version_in_filename=False)
    root = etree.parse(str(clip)).getroot()
    assert root.get("version") == "6"
    assert _feeds_and_versions(root) == (["v01", "v02"], ["v01", "v02"])  # same order, no duplicate
    feed = root.find("tracks/track/feeds/feed")
    assert "versions/v01/s." in feed.findtext("spans/span/path")
    version = root.find("versions/version")
    assert version.find("batchSetup") is None  # the replaced version's entry is ours now
    assert version.findtext("userData/versionNumber") == "1"
    # The untouched version keeps Flame's data.
    assert root.findtext("versions/version[@uid='v02']/creationDate") == "2024/12/22 12:30:00 PM"
    assert root.find("tracks/track/feeds").get("currentVersion") == "v01"


def test_wired_format_version_that_differs_on_append_warns_and_keeps_the_files(tmp_path, capsys):
    clip = _flame_clip(tmp_path, "7")
    _write(tmp_path, fps=25.0, format_version="8")
    assert etree.parse(str(clip)).getroot().get("version") == "7"
    out = capsys.readouterr().out
    assert "WARNING: format_version '8' ignored" in out and "'7'" in out


def test_matching_format_version_on_append_does_not_warn(tmp_path, capsys):
    _flame_clip(tmp_path, "6")
    _write(tmp_path, fps=25.0, format_version="6")
    assert "WARNING" not in capsys.readouterr().out


def test_format_version_input_is_optional_and_last():
    # Appended last so saved workflows' positional widgets_values stay valid.
    types = OpenClipWriter.INPUT_TYPES()
    assert list(types["optional"])[-1] == "format_version"
    assert types["optional"]["format_version"] == ("STRING", {"default": "6"})
    assert "format_version" not in types["required"]


# --- Standard Flame layout change (2026-10-27): old-layout clips stay readable and appendable ---

def _legacy_clip(tmp_path, stem="s"):
    """A clip as the Writer wrote it before 2026-10-27: s/s.clip + s/versions/v001/<stem>.####.exr."""
    media = tmp_path / "s" / "versions" / "v001"
    media.mkdir(parents=True)
    image_io.write_sequence(str(media / f"{stem}.%04d.exr"), torch.rand(2, 8, 8, 3), None, 1,
                            "EXR", "half (16-bit)", "ZIP")
    clip = tmp_path / "s" / "s.clip"
    clip.write_bytes(openclip_xml.generate("s", {"v001": ClipVersion(
        uid="v001", name="v001",
        spans=[ClipSpan(path=f"versions/v001/{stem}.%04d.exr", start_frame=1, duration=2)])}, "v001"))
    return clip


@pytest.mark.parametrize("version_in_filename", [False, True])
def test_append_to_legacy_standard_clip_keeps_the_old_layout(tmp_path, version_in_filename):
    clip = _legacy_clip(tmp_path)
    out = _write(tmp_path, include_version_in_filename=version_in_filename)
    assert Path(out) == clip
    assert not (tmp_path / "s.clip").exists()
    stem = "s_v002" if version_in_filename else "s"
    assert (tmp_path / "s" / "versions" / "v002" / f"{stem}.0001.exr").exists()
    for v in ("v001", "v002"):
        assert _read(str(clip), version=v)[2] == 2


def test_overwrite_with_version_in_filename_also_removes_old_dot_named_frames(tmp_path):
    clip = _legacy_clip(tmp_path, stem="s.v001")
    _write(tmp_path, version_name="v001", overwrite=True, include_version_in_filename=True)
    names = sorted(p.name for p in (tmp_path / "s" / "versions" / "v001").glob("*.exr"))
    assert names == ["s_v001.0001.exr", "s_v001.0002.exr"]
    assert _read(str(clip), version="v001")[2] == 2


def test_writer_start_frame_defaults_to_flames_1():
    # Flame numbers its own renders from 1; 1001 is the Nuke/film convention (PR #5 review).
    assert OpenClipWriter.INPUT_TYPES()["required"]["start_frame"][1]["default"] == 1


def test_include_version_in_filename_defaults_on_for_flames_layout():
    # Flame is the default platform, and only this layout lets Flame add its own versions (PR #5 review).
    assert OpenClipWriter.INPUT_TYPES()["required"]["include_version_in_filename"][1]["default"] is True
