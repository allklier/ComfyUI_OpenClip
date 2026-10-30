"""ExrSequenceReader: plain EXR sequences without a .clip wrapper (Blender/Nuke renders)."""
from __future__ import annotations

import numpy as np
import pytest
import torch

from ComfyUI_OpenClip.nodes.aov_reader import OpenClipAOVReader
from ComfyUI_OpenClip.nodes.exr_sequence_reader import ExrSequenceReader, _to_printf_pattern
from ComfyUI_OpenClip.nodes.reader import OpenClipReader

from .conftest import blender_frame_pixels


def _run(seq_dir, **overrides):
    kwargs = dict(sequence_path=str(seq_dir / "render.####.exr"), layer="CamelMatte.Combined",
                  start_frame=-1, end_frame=-1, frame_cap=-1, fps=25.0, colour_space="")
    kwargs.update(overrides)
    return ExrSequenceReader().execute(**kwargs)["result"]


def test_outputs_match_openclip_reader():
    # 2.0: the same 9 outputs as OpenClipReader; IMAGE/MASK at slots 0/1 (HM_ImageSwitch).
    assert ExrSequenceReader.RETURN_TYPES == OpenClipReader.RETURN_TYPES
    assert ExrSequenceReader.RETURN_NAMES == OpenClipReader.RETURN_NAMES
    assert ExrSequenceReader.RETURN_NAMES[:2] == ("IMAGE", "MASK")


@pytest.mark.parametrize("given, expected", [
    ("/a/render.####.exr", "/a/render.%04d.exr"),
    ("/a/render_#####.exr", "/a/render_%05d.exr"),
    ("/a/render.%04d.exr", "/a/render.%04d.exr"),
    ("/v1.2/render.#.exr", "/v1.2/render.%01d.exr"),
])
def test_frame_token_forms(given, expected):
    assert _to_printf_pattern(given) == expected


def test_no_frame_token_raises():
    with pytest.raises(ValueError, match="frame token"):
        _to_printf_pattern("/a/render.exr")


def test_reads_range_from_disk_and_fills_outputs(blender_multilayer_fixtures):
    images, masks, info, aov_handle, frame_count, width, height, fps, report = _run(blender_multilayer_fixtures)
    start_frame, clip_path, clip_name, colour_space = (
        info["start_frame"], info["clip_path"], info["clip_name"], info["colour_space"])

    assert (frame_count, width, height, start_frame) == (3, 64, 64, 1)
    px = blender_frame_pixels(3)
    assert torch.allclose(images[2], torch.from_numpy(np.stack([px[f"CamelMatte.Combined.{c}"] for c in "RGB"], -1)), atol=1e-6)
    assert torch.allclose(masks[2], torch.from_numpy(px["CamelMatte.Combined.A"]), atol=1e-6)
    assert clip_path == str(blender_multilayer_fixtures) and clip_name == "render"
    assert colour_space == "lin_ap1_scene"  # from the header when the widget is blank
    assert fps == 25.0 and info["fps"] == 25.0
    assert "ViewLayer.Normal" in info["aovs"]
    # A sequence has no version, schema or start timecode.
    assert (info["version_name"], info["format_version"], info["start_timecode"], info["versions"]) == ("", "", "", [])
    assert info["frame_padding"] == 4
    assert "VERSIONS" not in report and "  ViewLayer.Normal" in report.split("\n")
    assert report.startswith(f"CLIP     render   ({blender_multilayer_fixtures}/render.%04d.exr)")
    assert (aov_handle["start_frame"], aov_handle["end_frame"]) == (1, 3)


def test_frame_cap_and_explicit_range(blender_multilayer_fixtures):
    assert _run(blender_multilayer_fixtures, frame_cap=2)[4] == 2
    result = _run(blender_multilayer_fixtures, start_frame=2, end_frame=3)
    assert (result[4], result[2]["start_frame"]) == (2, 2)


def test_colour_space_widget_overrides_header(blender_multilayer_fixtures):
    assert _run(blender_multilayer_fixtures, colour_space="ACEScg")[2]["colour_space"] == "ACEScg"


def test_aov_handle_feeds_openclip_aov_reader(blender_multilayer_fixtures):
    aov_handle = _run(blender_multilayer_fixtures)[3]
    image, _matte = OpenClipAOVReader().execute(aov_handle, "ViewLayer.Normal")
    px = blender_frame_pixels(2)
    assert torch.allclose(image[1], torch.from_numpy(np.stack([px[f"ViewLayer.Normal.{c}"] for c in "XYZ"], -1)), atol=1e-6)


def test_no_matching_frames_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="no frames"):
        _run(tmp_path)


def test_is_changed_stable_and_never_raises(blender_multilayer_fixtures, tmp_path):
    kwargs = dict(sequence_path=str(blender_multilayer_fixtures / "render.####.exr"), start_frame=-1, end_frame=-1)
    first = ExrSequenceReader.IS_CHANGED(**kwargs)
    assert first and first == ExrSequenceReader.IS_CHANGED(**kwargs)
    # Linked sequence_path arrives as None; a missing folder must not raise either.
    assert ExrSequenceReader.IS_CHANGED(sequence_path=None, start_frame=-1, end_frame=-1) == ""
    assert ExrSequenceReader.IS_CHANGED(sequence_path=str(tmp_path / "nope/r.####.exr"), start_frame=-1, end_frame=-1) == ""
