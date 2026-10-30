"""Nodes added during the Harmonize work: OpenClipAOVReader, OpenClipAOVWriter,
OpenClipColourSpaceTransform, ExrSequenceReader -- the cases the earlier suites skip.
"""
from __future__ import annotations

import glob
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import torch

from ComfyUI_OpenClip.lib import image_io, openclip_xml
from ComfyUI_OpenClip.nodes import aov_reader as aov_reader_mod
from ComfyUI_OpenClip.nodes.aov_reader import OpenClipAOVReader
from ComfyUI_OpenClip.nodes.aov_writer import OpenClipAOVWriter
from ComfyUI_OpenClip.nodes.exr_sequence_reader import ExrSequenceReader, _frames_on_disk, _to_printf_pattern
from ComfyUI_OpenClip.nodes.reader import OpenClipReader
from ComfyUI_OpenClip.nodes.writer import OpenClipWriter

from .conftest import IMAGE_SIZE, blender_frame_pixels

FIXTURE_CONFIG = str(Path(__file__).parent / "fixtures" / "test_ocio.ocio")


def _handle(pattern, start=1, end=1):
    return {"abs_pattern": pattern, "start_frame": start, "end_frame": end, "clip_path": pattern}


# --- OpenClipAOVWriter ----------------------------------------------------------------


def test_aov_writer_blank_or_whitespace_name_raises():
    for name in ("", "   "):
        with pytest.raises(ValueError, match="name is required"):
            OpenClipAOVWriter().execute(name=name, matte=torch.zeros(1, 4, 4))


def test_aov_writer_strips_name_and_returns_tuple_chain():
    matte = torch.zeros(1, 4, 4)
    (chain,) = OpenClipAOVWriter().execute(name="  depth  ", matte=matte)
    assert chain == [("depth", None, matte)]


def test_aov_writer_chain_preserves_order_and_does_not_mutate_its_input():
    first = OpenClipAOVWriter().execute(name="a", matte=torch.zeros(1, 4, 4))[0]
    snapshot = list(first)
    second = OpenClipAOVWriter().execute(name="b", image=torch.zeros(1, 4, 4, 3), aov_chain=first)[0]
    assert [e[0] for e in second] == ["a", "b"]
    assert first == snapshot and second is not first


def test_aov_writer_duplicate_detected_after_stripping_and_across_a_long_chain():
    chain = None
    for name in ("a", "b", "c"):
        chain = OpenClipAOVWriter().execute(name=name, matte=torch.zeros(1, 4, 4), aov_chain=chain)[0]
    with pytest.raises(ValueError, match="duplicate AOV name 'b'"):
        OpenClipAOVWriter().execute(name=" b ", matte=torch.zeros(1, 4, 4), aov_chain=chain)


def test_aov_writer_empty_chain_input_is_treated_as_no_chain():
    (chain,) = OpenClipAOVWriter().execute(name="a", matte=torch.zeros(1, 4, 4), aov_chain=[])
    assert len(chain) == 1


def test_aov_writer_error_messages_name_the_aov():
    with pytest.raises(ValueError, match="AOV 'x'.*not both"):
        OpenClipAOVWriter().execute(name="x", image=torch.zeros(1, 4, 4, 3), matte=torch.zeros(1, 4, 4))
    with pytest.raises(ValueError, match="AOV 'x'.*exactly one"):
        OpenClipAOVWriter().execute(name="x")


# --- AOV write -> read through the real nodes: shapes the earlier tests skip ----------


def _write_with_chain(tmp_path, chain, n=2, **over):
    kw = dict(clip_path=str(tmp_path), clip_name="s", clip_filename="$(path)/$(clip_name).clip", version_name="next",
              fps=24.0, start_frame=1001, frame_padding=4, file_format="EXR", exr_bit_depth="float (32-bit)",
              exr_compression="ZIP", aov_chain=chain)
    kw.update(over)
    path = OpenClipWriter().execute(IMAGE=torch.rand(n, 16, 16, 3), **kw)[0]
    return OpenClipReader().execute(clip_path=path, version="current", start_frame=-1, end_frame=-1,
                                    path_from="", path_to="")["result"]


def test_four_channel_aov_image_round_trips_and_matte_is_channel_zero(tmp_path):
    rgba = torch.rand(2, 16, 16, 4)
    chain = OpenClipAOVWriter().execute(name="ao_rgba", image=rgba)[0]
    out = _write_with_chain(tmp_path, chain)
    image, matte = OpenClipAOVReader().execute(out[3], "ao_rgba")
    assert torch.equal(image, rgba)
    assert torch.equal(matte, rgba[..., 0])  # no "R" channel name for >3 channels -> channel 0


def test_two_channel_aov_image_round_trips(tmp_path):
    rg = torch.rand(2, 16, 16, 2)
    chain = OpenClipAOVWriter().execute(name="motion", image=rg)[0]
    image, matte = OpenClipAOVReader().execute(_write_with_chain(tmp_path, chain)[3], "motion")
    assert torch.equal(image, rg) and torch.equal(matte, rg[..., 0])


def test_mixed_chain_image_and_matte_entries_do_not_cross_contaminate(tmp_path):
    normals = torch.rand(2, 16, 16, 3) * 2 - 1
    depth = torch.rand(2, 16, 16) * 50
    ids = torch.rand(2, 16, 16)
    chain = None
    for name, kw in (("normals", {"image": normals}), ("depth", {"matte": depth}), ("ids", {"matte": ids})):
        chain = OpenClipAOVWriter().execute(name=name, aov_chain=chain, **kw)[0]
    out = _write_with_chain(tmp_path, chain)
    assert out[2]["aovs"] == ["normals", "depth", "ids"]
    reader = OpenClipAOVReader()
    assert torch.equal(reader.execute(out[3], "normals")[0], normals)
    img, m = reader.execute(out[3], "depth")
    assert img is None and torch.equal(m, depth)
    assert torch.equal(reader.execute(out[3], "ids")[1], ids)


def test_aov_survives_half_precision_within_tolerance_and_does_not_clamp(tmp_path):
    hdr = torch.rand(2, 16, 16, 3) * 400 - 100  # well outside 0..1, negative
    chain = OpenClipAOVWriter().execute(name="pos", image=hdr)[0]
    out = _write_with_chain(tmp_path, chain, exr_bit_depth="half (16-bit)")
    image, _ = OpenClipAOVReader().execute(out[3], "pos")
    assert torch.allclose(image, hdr, rtol=1e-3, atol=0.1) and image.min() < 0 and image.max() > 100


def test_aov_names_with_awkward_characters_in_separate_files_layout(tmp_path):
    matte = torch.rand(2, 16, 16)
    chain = OpenClipAOVWriter().execute(name="Z depth/main", matte=matte)[0]
    out = _write_with_chain(tmp_path, chain, aov_layout="Separate Files", include_version_in_filename=False)
    siblings = sorted(p.name for p in (tmp_path / "s" / "v001").glob("*.exr"))
    assert any("AOV_Z_depth_main" in n for n in siblings)  # sanitized for the filesystem
    # the sibling file carries the *original* name as its part name, which the probe recovers
    assert out[2]["aovs"] == ["Z depth/main"]
    m_image, m_matte = OpenClipAOVReader().execute(out[3], "Z depth/main")
    assert m_image is None  # single channel -> matte only
    assert torch.allclose(m_matte, matte, atol=1e-3)


def test_aov_chain_with_mismatched_batch_length_is_a_loud_failure(tmp_path):
    chain = OpenClipAOVWriter().execute(name="short", matte=torch.rand(1, 16, 16))[0]
    with pytest.raises((IndexError, ValueError, RuntimeError)):
        _write_with_chain(tmp_path, chain, n=3)


# --- OpenClipAOVReader ------------------------------------------------------------------


def test_aov_reader_blank_part_name_raises():
    for name in ("", "  "):
        with pytest.raises(ValueError, match="part_name is required"):
            OpenClipAOVReader().execute(_handle("/x/f.%04d.exr"), name)


def test_aov_reader_strips_part_name(aov_multipart_fixtures, aov_pixels):
    handle = _handle(str(aov_multipart_fixtures / "aov_multipart.%04d.exr"), 0, 0)
    image, _ = OpenClipAOVReader().execute(handle, "  normals ")
    assert torch.allclose(image[0], torch.from_numpy(aov_pixels["normal"]), atol=1e-6)


def test_aov_reader_unknown_part_returns_none_pair(aov_multipart_fixtures):
    handle = _handle(str(aov_multipart_fixtures / "aov_multipart.%04d.exr"), 0, 0)
    assert OpenClipAOVReader().execute(handle, "no_such_pass") == (None, None)


def test_aov_reader_is_case_sensitive(aov_multipart_fixtures):
    handle = _handle(str(aov_multipart_fixtures / "aov_multipart.%04d.exr"), 0, 0)
    assert OpenClipAOVReader().execute(handle, "Depth") == (None, None)


def test_aov_reader_cryptomatte_like_part_is_returned_raw_like_any_other(aov_multipart_fixtures):
    handle = _handle(str(aov_multipart_fixtures / "aov_multipart.%04d.exr"), 0, 0)
    image, matte = OpenClipAOVReader().execute(handle, "cryptomatte")
    assert image.shape == (1, IMAGE_SIZE, IMAGE_SIZE, 3) and torch.equal(matte, image[..., 0])


def test_aov_reader_beauty_part_is_addressable_by_its_name(aov_multipart_fixtures, aov_pixels):
    handle = _handle(str(aov_multipart_fixtures / "aov_multipart.%04d.exr"), 0, 0)
    image, matte = OpenClipAOVReader().execute(handle, "beauty")
    assert image.shape[-1] == 4 and torch.allclose(matte[0], torch.from_numpy(aov_pixels["beauty"][..., 0]))


def test_aov_reader_start_after_end_raises(aov_multipart_fixtures):
    with pytest.raises(ValueError, match="start_frame"):
        OpenClipAOVReader().execute(_handle(str(aov_multipart_fixtures / "aov_multipart.%04d.exr"), 2, 1), "depth")


def test_aov_reader_input_types_shape():
    spec = OpenClipAOVReader.INPUT_TYPES()
    assert list(spec["required"]) == ["aov_handle", "part_name"]
    assert spec["required"]["aov_handle"] == ("AOV_HANDLE",)
    assert OpenClipAOVReader.RETURN_TYPES == ("IMAGE", "MASK")


# --- OpenClipAOVReader.IS_CHANGED (no coverage before) ----------------------------------


@pytest.fixture
def aov_frame(tmp_path):
    pattern = str(tmp_path / "f.%04d.exr")
    image_io.write_sequence(pattern, torch.rand(2, 8, 8, 3), None, 1, "EXR")
    return pattern


def test_aov_is_changed_stable_then_detects_rerender(aov_frame):
    h = _handle(aov_frame)
    first = OpenClipAOVReader.IS_CHANGED(aov_handle=h, part_name="depth")
    assert first and first == OpenClipAOVReader.IS_CHANGED(aov_handle=h, part_name="depth")
    st = os.stat(aov_frame % 1)
    image_io.write_sequence(aov_frame, torch.rand(2, 8, 8, 3) * 3, None, 1, "EXR", "float (32-bit)")
    os.utime(aov_frame % 1, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert OpenClipAOVReader.IS_CHANGED(aov_handle=h, part_name="depth") != first


def test_aov_is_changed_never_raises_on_bad_inputs(tmp_path):
    cls = OpenClipAOVReader
    assert cls.IS_CHANGED(aov_handle=None, part_name=None) == ""
    assert cls.IS_CHANGED(aov_handle={}, part_name="x") == ""  # malformed handle dict
    assert cls.IS_CHANGED(aov_handle=_handle(str(tmp_path / "gone.%04d.exr")), part_name="x") == ""
    assert cls.IS_CHANGED(aov_handle="not a dict", part_name="x", unique_id="zz", extra=1) == ""


def test_aov_is_changed_falls_back_to_the_last_execute_when_handle_is_linked(aov_frame):
    cls = OpenClipAOVReader
    cls().execute(_handle(aov_frame), "depth", unique_id="node-7")
    try:
        remembered = cls.IS_CHANGED(aov_handle=None, part_name="depth", unique_id="node-7")
        assert remembered and remembered == cls.IS_CHANGED(aov_handle=_handle(aov_frame), part_name="depth")
        assert cls.IS_CHANGED(aov_handle=None, part_name="depth", unique_id="other-node") == ""
    finally:
        aov_reader_mod._LAST_READ_PLAN.pop("node-7", None)


# --- ExrSequenceReader ---------------------------------------------------------------------


def _run(seq_path, **over):
    kw = dict(sequence_path=str(seq_path), layer="", start_frame=-1, end_frame=-1, frame_cap=-1, fps=24.0, colour_space="")
    kw.update(over)
    return ExrSequenceReader().execute(**kw)["result"]


def _plain_seq(tmp_path, stem="plain", n=4, start=1, mask=True, metadata=None):
    pattern = str(tmp_path / f"{stem}.%04d.exr")
    image_io.write_sequence(pattern, torch.rand(n, 16, 16, 3), torch.full((n, 16, 16), 0.5) if mask else None,
                            start, "EXR", "float (32-bit)", "ZIP", metadata=metadata)
    return pattern


@pytest.mark.parametrize("bad", ["/a/render.exr", "/a/render.####.####.exr", "/a/##/render.exr", ""])
def test_to_printf_pattern_rejects_missing_or_ambiguous_tokens(bad):
    with pytest.raises(ValueError, match="frame token"):
        _to_printf_pattern(bad)


def test_to_printf_pattern_ignores_hashes_in_directory_names():
    assert _to_printf_pattern("/a/##/render.###.exr") == "/a/##/render.%03d.exr"


def test_blank_sequence_path_raises():
    for p in ("", "   "):
        with pytest.raises(ValueError, match="sequence_path is required"):
            _run(p)


def test_nonexistent_directory_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _run(tmp_path / "nope" / "r.####.exr")


def test_plain_rgba_sequence_with_blank_layer(tmp_path):
    _plain_seq(tmp_path)
    r = _run(tmp_path / "plain.####.exr")
    assert r[0].shape == (4, 16, 16, 3) and torch.all(r[1] == 0.5)
    assert r[4:7] == (4, 16, 16)
    assert (r[2]["start_frame"], r[2]["clip_path"], r[2]["clip_name"]) == (1, str(tmp_path), "plain")
    assert r[2]["aovs"] == []  # no AOV parts


def test_plain_rgb_sequence_gets_an_opaque_mask(tmp_path):
    _plain_seq(tmp_path, mask=False)
    assert torch.all(_run(tmp_path / "plain.####.exr")[1] == 1.0)


@pytest.mark.parametrize("pattern, stem", [
    ("shot_v01_####.exr", "shot_v01"),
    ("a.b.####.exr", "a.b"),
    ("plain-name.####.exr", "plain-name"),
])
def test_clip_name_is_the_filename_stem_before_the_frame_token(tmp_path, pattern, stem):
    image_io.write_sequence(str(tmp_path / pattern.replace("####", "%04d")), torch.rand(1, 8, 8, 3), None, 1, "EXR")
    assert _run(tmp_path / pattern)[2]["clip_name"] == stem


def test_discovery_ignores_other_stems_and_extensions_and_sorts_numerically(tmp_path):
    for n in (2, 10, 9):
        image_io.write_sequence(str(tmp_path / "shot.%d.exr"), torch.rand(1, 4, 4, 3), None, n, "EXR")
    image_io.write_sequence(str(tmp_path / "other.%04d.exr"), torch.rand(1, 4, 4, 3), None, 1, "EXR")
    (tmp_path / "shot.0003.png").write_bytes(b"")
    (tmp_path / "shot.txt").write_text("x")
    assert _frames_on_disk(str(tmp_path / "shot.%d.exr")) == [2, 9, 10]


def test_discovery_handles_regex_metacharacters_in_the_stem(tmp_path):
    stem = "my.shot[v1]+(final)"
    image_io.write_sequence(str(tmp_path / f"{stem}.%04d.exr"), torch.rand(3, 4, 4, 3), None, 5, "EXR")
    assert _frames_on_disk(str(tmp_path / f"{stem}.%04d.exr")) == [5, 6, 7]
    assert _run(tmp_path / f"{stem}.####.exr")[4] == 3


def test_explicit_start_only_and_end_only_use_disk_discovery_for_the_other_end(tmp_path):
    _plain_seq(tmp_path, n=6, start=10)  # frames 10..15
    r = _run(tmp_path / "plain.####.exr", start_frame=12)
    assert (r[4], r[2]["start_frame"]) == (4, 12)  # frame_count, start
    r = _run(tmp_path / "plain.####.exr", end_frame=12)
    assert r[4] == 3 and r[2]["start_frame"] == 10


def test_explicit_range_does_not_require_a_directory_scan_to_succeed(tmp_path):
    _plain_seq(tmp_path, n=3, start=1)
    assert _run(tmp_path / "plain.####.exr", start_frame=1, end_frame=2)[4] == 2


def test_start_after_end_raises(tmp_path):
    _plain_seq(tmp_path)
    with pytest.raises(ValueError, match="start_frame"):
        _run(tmp_path / "plain.####.exr", start_frame=3, end_frame=2)


def test_frame_cap_zero_raises_rather_than_returning_nothing(tmp_path):
    # Characterisation: frame_cap=0 collapses the range to empty, which read_sequence rejects.
    _plain_seq(tmp_path)
    with pytest.raises(ValueError, match="start_frame"):
        _run(tmp_path / "plain.####.exr", frame_cap=0)


def test_blank_layer_on_a_blender_multilayer_file_names_the_layers(blender_multilayer_fixtures):
    with pytest.raises(RuntimeError, match="layers in this file"):
        _run(blender_multilayer_fixtures / "render.####.exr", layer="")


def test_unknown_layer_raises_naming_the_missing_channels(blender_multilayer_fixtures):
    with pytest.raises(RuntimeError, match=r"NoSuchLayer\.R"):
        _run(blender_multilayer_fixtures / "render.####.exr", layer="NoSuchLayer")


def test_layer_widget_is_stripped(blender_multilayer_fixtures):
    r = _run(blender_multilayer_fixtures / "render.####.exr", layer="  CamelMatte.Combined  ")
    assert r[4] == 3


def test_relative_sequence_path_resolves_against_the_input_directory(tmp_path, monkeypatch):
    _plain_seq(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", types.SimpleNamespace(get_input_directory=lambda: str(tmp_path)))
    assert _run("plain.####.exr")[4] == 4


def test_printf_style_path_is_accepted(tmp_path):
    _plain_seq(tmp_path)
    assert _run(tmp_path / "plain.%04d.exr")[4] == 4


def test_header_colour_space_prefers_openclip_backup_key(tmp_path):
    _plain_seq(tmp_path, metadata={"oiio:ColorSpace": "ARRI LogC4 / AWG4"})
    assert _run(tmp_path / "plain.####.exr")[2]["colour_space"] == "ARRI LogC4 / AWG4"


def test_metadata_output_is_the_clip_metadata_dict(tmp_path):
    _plain_seq(tmp_path, metadata={"Artist": "jan"})
    meta = _run(tmp_path / "plain.####.exr")[2]["metadata"]
    assert isinstance(meta, dict) and meta["Artist"] == "jan"


def test_fps_output_is_the_widget_value_as_float(tmp_path):
    _plain_seq(tmp_path)
    r = _run(tmp_path / "plain.####.exr", fps=23.976)
    assert isinstance(r[7], float) and r[7] == 23.976 and r[2]["fps"] == 23.976


def test_ui_payload_carries_the_clip_report(blender_multilayer_fixtures):
    out = ExrSequenceReader().execute(
        sequence_path=str(blender_multilayer_fixtures / "render.####.exr"), layer="CamelMatte.Combined",
        start_frame=-1, end_frame=-1, frame_cap=-1, fps=24.0, colour_space="")
    assert out["ui"] == {"clip_report": [out["result"][8]]}


def test_is_changed_detects_a_rerendered_first_frame(tmp_path):
    pattern = _plain_seq(tmp_path)
    kw = dict(sequence_path=str(tmp_path / "plain.####.exr"), start_frame=-1, end_frame=-1)
    before = ExrSequenceReader.IS_CHANGED(**kw)
    st = os.stat(pattern % 1)
    image_io.write_sequence(pattern, torch.rand(1, 16, 16, 3) * 5, None, 1, "EXR", "float (32-bit)")
    os.utime(pattern % 1, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert ExrSequenceReader.IS_CHANGED(**kw) != before


def test_is_changed_falls_back_to_last_execute_for_a_linked_path(tmp_path):
    from ComfyUI_OpenClip.nodes import exr_sequence_reader as mod
    _plain_seq(tmp_path)
    ExrSequenceReader().execute(sequence_path=str(tmp_path / "plain.####.exr"), layer="", start_frame=-1, end_frame=-1,
                                frame_cap=-1, fps=24.0, colour_space="", unique_id="esr-1")
    try:
        assert ExrSequenceReader.IS_CHANGED(sequence_path=None, start_frame=None, end_frame=None, unique_id="esr-1") != ""
    finally:
        mod._LAST_READ_PLAN.pop("esr-1", None)


def test_exr_reader_outputs_wire_into_writer_and_round_trip_via_openclip_reader(blender_multilayer_fixtures, tmp_path):
    r = _run(blender_multilayer_fixtures / "render.####.exr", layer="CamelMatte.Combined", colour_space="ACEScg")
    images, masks, info, handle, *_ = r
    normals, _ = OpenClipAOVReader().execute(handle, "ViewLayer.Normal")
    chain = OpenClipAOVWriter().execute(name="normals", image=normals)[0]
    # Everything else (name, start frame, fps, colour, metadata) comes from clip_info.
    clip = OpenClipWriter().execute(
        IMAGE=images, MASK=masks, clip_info=info, aov_chain=chain, clip_path=str(tmp_path / "out"),
        file_format="EXR", exr_bit_depth="float (32-bit)", exr_compression="ZIP")[0]
    back = OpenClipReader().execute(clip_path=clip, version="current", start_frame=-1, end_frame=-1, path_from="", path_to="")["result"]
    assert torch.equal(back[0], images) and torch.equal(back[1], masks)
    assert back[2]["start_frame"] == info["start_frame"] and back[2]["clip_name"] == info["clip_name"]
    n_back, _ = OpenClipAOVReader().execute(back[3], "normals")
    assert torch.equal(n_back, normals)
    assert openclip_xml.read_format(clip).colour_space == "ACEScg"


# --- OpenClipColourSpaceTransform ------------------------------------------------------------

pytest.importorskip("PyOpenColorIO")
import PyOpenColorIO as ocio  # noqa: E402

from ComfyUI_OpenClip.nodes.colour_space_transform import (  # noqa: E402
    ACESCG_LABEL, CAMERA_COLOUR_SPACES, CUSTOM_LABEL, SAME_AS_INPUT_LABEL, TARGET_COLOUR_SPACES,
    OpenClipColourSpaceTransform,
)


def _cst(**over):
    kw = dict(IMAGE=torch.full((1, 4, 4, 3), 0.25), ocio_config=FIXTURE_CONFIG, source=CUSTOM_LABEL,
              custom_source="scaled_2x", target_colour_space=CUSTOM_LABEL, custom_target_colour_space="linear")
    kw.update(over)
    return OpenClipColourSpaceTransform().execute(**kw)


def test_cst_input_types_dropdown_layout():
    req = OpenClipColourSpaceTransform.INPUT_TYPES()["required"]
    assert req["source"][0][-1] == CUSTOM_LABEL and req["source"][0][0] == ACESCG_LABEL
    assert req["target_colour_space"][0][:2] == [ACESCG_LABEL, SAME_AS_INPUT_LABEL]
    assert req["target_colour_space"][0][-1] == CUSTOM_LABEL
    assert SAME_AS_INPUT_LABEL not in req["source"][0]
    assert req["target_colour_space"][0] == TARGET_COLOUR_SPACES
    opt = OpenClipColourSpaceTransform.INPUT_TYPES()["optional"]
    assert opt["same_as_input_colour_space"][1]["forceInput"] is True


def test_cst_whitespace_in_custom_names_and_config_is_stripped():
    out, _ = _cst(custom_source="  scaled_2x ", custom_target_colour_space=" linear  ", ocio_config=f"  {FIXTURE_CONFIG}  ")
    assert torch.allclose(out, torch.full_like(out, 0.5), atol=1e-5)


def test_cst_whitespace_only_pipe_value_counts_as_unconnected():
    with pytest.raises(ValueError, match="same_as_input_colour_space"):
        _cst(target_colour_space=SAME_AS_INPUT_LABEL, same_as_input_colour_space="   ")


def test_cst_pipe_value_is_ignored_unless_target_is_same_as_input():
    out, _ = _cst(same_as_input_colour_space="scaled_2x")  # target stays Custom -> linear
    assert torch.allclose(out, torch.full_like(out, 0.5), atol=1e-5)


def test_cst_custom_fields_ignored_for_labelled_choices(monkeypatch):
    import ComfyUI_OpenClip.nodes.colour_space_transform as mod
    monkeypatch.setitem(mod.CAMERA_COLOUR_SPACES, "Fixture Cam", "scaled_2x")
    out, _ = _cst(source="Fixture Cam", custom_source="garbage that is never looked up")
    assert torch.allclose(out, torch.full_like(out, 0.5), atol=1e-5)


def test_cst_same_as_input_with_identical_space_is_identity():
    images = torch.rand(2, 5, 7, 3)
    out, _ = _cst(IMAGE=images, custom_source="scaled_2x", target_colour_space=SAME_AS_INPUT_LABEL,
                  same_as_input_colour_space="scaled_2x")
    assert torch.allclose(out, images, atol=1e-6)


def test_cst_unknown_colour_space_name_raises_ocio_error_naming_it():
    with pytest.raises(ocio.Exception, match="not_a_space"):
        _cst(custom_source="not_a_space")
    with pytest.raises(ocio.Exception, match="not_a_space"):
        _cst(custom_target_colour_space="not_a_space")


def test_cst_unknown_pipe_space_raises_ocio_error():
    with pytest.raises(ocio.Exception, match="Gamma 9.9"):
        _cst(target_colour_space=SAME_AS_INPUT_LABEL, same_as_input_colour_space="Gamma 9.9")


def test_cst_unreadable_config_path_raises():
    # OCIO 2.6 raises ExceptionMissingFile here (2.5 raised ocio.Exception); in
    # both versions ExceptionMissingFile is not a subclass of ocio.Exception.
    with pytest.raises((ocio.Exception, ocio.ExceptionMissingFile)):
        _cst(ocio_config="/no/such/config.ocio")


def test_cst_hdr_and_negative_values_are_not_clamped():
    images = torch.tensor([-0.5, 0.0, 4.0, 100.0]).view(1, 1, 4, 1).expand(1, 2, 4, 3).contiguous()
    out, _ = _cst(IMAGE=images)
    assert torch.allclose(out, images * 2, atol=1e-4)


def test_cst_batch_nonsquare_float32_and_input_untouched():
    images = torch.rand(3, 5, 9, 3)
    original = images.clone()
    out, mask = _cst(IMAGE=images)
    assert out.shape == images.shape and out.dtype == torch.float32
    assert mask.shape == (3, 5, 9) and torch.all(mask == 1)
    assert torch.equal(images, original) and out is not images


def test_cst_connected_mask_is_returned_unchanged():
    mask = torch.rand(1, 4, 4)
    _, out_mask = _cst(MASK=mask)
    assert out_mask is mask


def test_cst_wrong_channel_count_raises():
    with pytest.raises(ValueError):
        _cst(IMAGE=torch.rand(1, 4, 4, 4))


def test_cst_labels_map_to_unique_non_empty_ocio_names_except_deliberate_aliases():
    values = list(CAMERA_COLOUR_SPACES.values())
    assert all(isinstance(v, str) and v.strip() for v in values)
    assert len(set(values)) == len(values)


_FLAME_CONFIGS = sorted(glob.glob("/opt/Autodesk/colour_mgmt/configs/flame_configs/*/aces2.0_config/config.ocio"))


@pytest.mark.skipif(not _FLAME_CONFIGS, reason="Flame's ACES 2.0 OCIO config is not installed on this machine")
def test_cst_every_dropdown_label_resolves_in_the_real_flame_config():
    config = ocio.Config.CreateFromFile(_FLAME_CONFIGS[-1])
    for label, space in CAMERA_COLOUR_SPACES.items():
        assert config.getColorSpace(space) is not None, f"{label!r} -> {space!r} missing from {_FLAME_CONFIGS[-1]}"
    images = torch.rand(1, 4, 4, 3)
    for label in CAMERA_COLOUR_SPACES:
        out, _ = OpenClipColourSpaceTransform().execute(
            IMAGE=images, ocio_config=_FLAME_CONFIGS[-1], source=label, custom_source="",
            target_colour_space=ACESCG_LABEL, custom_target_colour_space="")
        assert torch.isfinite(out).all()
