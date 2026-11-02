"""OpenClipExrSequenceWriter: plain '<folder>/<name>.####.exr' sequences, no .clip file."""
from __future__ import annotations

import os

import OpenImageIO as oiio
import pytest
import torch

from ComfyUI_OpenClip.lib import image_io
from ComfyUI_OpenClip.nodes.aov_reader import OpenClipAOVReader
from ComfyUI_OpenClip.nodes.aov_writer import OpenClipAOVWriter
from ComfyUI_OpenClip.nodes.exr_sequence_reader import ExrSequenceReader
from ComfyUI_OpenClip.nodes.exr_sequence_writer import OpenClipExrSequenceWriter


def _image(n=3, h=8, w=8, scale=1.0):
    # Values outside 0..1 on purpose: EXR must not clamp.
    return torch.linspace(-0.5, 2.5, n * h * w * 3).reshape(n, h, w, 3) * scale


def _write(folder, name, image=None, **overrides):
    kwargs = dict(IMAGE=_image() if image is None else image, folder=str(folder), name=name)
    kwargs.update(overrides)
    return dict(zip(OpenClipExrSequenceWriter.RETURN_NAMES, OpenClipExrSequenceWriter().execute(**kwargs)))


def _read(sequence_path, **overrides):
    kwargs = dict(sequence_path=sequence_path, layer="", start_frame=-1, end_frame=-1,
                  frame_cap=-1, fps=24.0, colour_space="")
    kwargs.update(overrides)
    return dict(zip(ExrSequenceReader.RETURN_NAMES, ExrSequenceReader().execute(**kwargs)["result"]))


def _attrs(path) -> dict:
    inp = oiio.ImageInput.open(str(path))
    try:
        return {a.name: a.value for a in inp.spec().extra_attribs}
    finally:
        inp.close()


def _frames(folder):
    return sorted(os.listdir(folder))


def test_round_trip_through_exr_sequence_reader(tmp_path):
    image = _image()
    mask = torch.rand(3, 8, 8)
    out = _write(tmp_path, "plate", image, MASK=mask, exr_bit_depth="float (32-bit)")

    assert _frames(tmp_path) == ["plate.0001.exr", "plate.0002.exr", "plate.0003.exr"]
    assert out == {
        "sequence_path": str(tmp_path / "plate.####.exr"),
        "first_frame_path": str(tmp_path / "plate.0001.exr"),
        "folder": str(tmp_path),
        "name": "plate",
        "version_name": "",
    }
    back = _read(out["sequence_path"])
    assert torch.equal(back["IMAGE"], image)  # float, unclamped
    assert torch.equal(back["MASK"], mask)
    info = back["clip_info"]
    assert (info["clip_path"], info["clip_name"], info["start_frame"]) == (str(tmp_path), "plate", 1)
    assert info["colour_space"] == "Rec.709 video"
    assert info["start_timecode"] == "00:00:00:00"
    assert info["bit_depth"] == "float" and info["compression"] == "ZIP"


def test_per_frame_header_metadata(tmp_path):
    _write(tmp_path, "plate", fps=23.976, start_frame=1001, start_timecode="01:00:00:00", colour_space="ACEScg")
    assert image_io.describe_frame(str(tmp_path / "plate.1001.exr")).startswith("EXR half")
    for i, frame in enumerate(("plate.1001.exr", "plate.1002.exr", "plate.1003.exr")):
        attrs = _attrs(tmp_path / frame)
        assert tuple(attrs["FramesPerSecond"]) == (24000, 1001)
        assert attrs[image_io.COLOUR_SPACE_BACKUP_KEY] == "ACEScg"
        assert image_io.frame_timecode(str(tmp_path / frame)) is not None
    back = _read(str(tmp_path / "plate.####.exr"), fps=23.976)
    assert back["clip_info"]["start_timecode"] == "01:00:00:00"
    third = _read(str(tmp_path / "plate.####.exr"), fps=23.976, start_frame=1003)
    assert third["clip_info"]["start_timecode"] == "01:00:00:02"


def test_clip_info_carry_over(tmp_path):
    src = tmp_path / "src"
    _write(src, "plate", fps=25.0, start_frame=1001, start_timecode="10:00:00:00", colour_space="ACEScg",
           frame_padding=6, exr_bit_depth="float (32-bit)", exr_compression="PIZ")
    attrs = _attrs(src / "plate.001001.exr")
    first = _read(str(src / "plate.######.exr"), fps=25.0)

    out_dir = tmp_path / "out"
    out = _write(out_dir, "plate_clean", first["IMAGE"], clip_info=first["clip_info"])
    assert out["first_frame_path"] == str(out_dir / "plate_clean.001001.exr")
    back = _read(out["sequence_path"], fps=25.0)
    info = back["clip_info"]
    assert (info["start_frame"], info["start_timecode"], info["colour_space"]) == (1001, "10:00:00:00", "ACEScg")
    assert (info["frame_padding"], info["bit_depth"], info["compression"]) == (6, "float", "PIZ")
    assert tuple(_attrs(out_dir / "plate_clean.001001.exr")["FramesPerSecond"]) == (25, 1)
    assert "FramesPerSecond" in attrs

    # Empty folder/name take clip_info's: writes next to the source, under its name.
    with pytest.raises(ValueError, match="SEQUENCE ALREADY EXISTS"):
        _write("", "", first["IMAGE"], clip_info=first["clip_info"])

    # A typed value overrides clip_info.
    out = _write(out_dir, "typed", first["IMAGE"], clip_info=first["clip_info"], start_frame=1, frame_padding=4)
    assert out["first_frame_path"] == str(out_dir / "typed.0001.exr")


def test_folder_from_openclip_reader_clip_info_is_the_clip_files_folder(tmp_path):
    info = {"clip_path": str(tmp_path / "shot.clip"), "clip_name": "shot"}
    out = _write("", "", clip_info=info)
    assert out["folder"] == str(tmp_path) and out["name"] == "shot"


@pytest.mark.parametrize("name, v1, second, v2", [
    ("shot_v01", "v01", "shot_v02", "v02"),
    ("shot.v001", "v001", "shot.v002", "v002"),
    ("shot_v1_comp", "v1", "shot_v2_comp", "v2"),
    ("v009", "v009", "v010", "v010"),
])
def test_version_next(tmp_path, name, v1, second, v2):
    a = _write(tmp_path, name)  # nothing on disk yet: the name's own token
    b = _write(tmp_path, name)  # same name again: 'next' moves on
    assert (a["name"], a["version_name"]) == (name, v1)
    assert (b["name"], b["version_name"]) == (second, v2)
    assert os.path.exists(b["first_frame_path"])


@pytest.mark.parametrize("name", ["shotv01", "shot_v01x", "shot-v01", "shot_V01"])
def test_names_without_a_delimited_version_token_are_not_versioned(tmp_path, name):
    assert _write(tmp_path, name)["version_name"] == ""


def test_version_next_keeps_padding_and_ignores_other_names(tmp_path):
    _write(tmp_path, "shot_v003")
    _write(tmp_path, "other_v009")          # different name: not counted
    _write(tmp_path, "shot_v005_comp")      # different tail: not counted
    out = _write(tmp_path, "shot_v001")     # name's own token is below the highest on disk
    assert out["version_name"] == "v004" and out["name"] == "shot_v004"


def test_explicit_version_replaces_token(tmp_path):
    out = _write(tmp_path, "shot_v01", version_name="v07")
    assert (out["name"], out["version_name"]) == ("shot_v07", "v07")
    assert _frames(tmp_path)[0] == "shot_v07.0001.exr"


def test_explicit_version_must_be_v_digits(tmp_path):
    with pytest.raises(ValueError, match="v<digits>"):
        _write(tmp_path, "shot_v01", version_name="final")
    assert _frames(tmp_path) == []


def test_version_collision_refused_then_overwrite_deletes_old_frames_first(tmp_path):
    _write(tmp_path, "shot_v01", _image(n=5))
    with pytest.raises(ValueError, match="VERSION ALREADY EXISTS"):
        _write(tmp_path, "shot_v01", version_name="v01")
    assert len(_frames(tmp_path)) == 5  # refused: nothing touched

    out = _write(tmp_path, "shot_v01", _image(n=2, scale=2.0), version_name="v01", overwrite=True)
    assert out["name"] == "shot_v01"
    assert _frames(tmp_path) == ["shot_v01.0001.exr", "shot_v01.0002.exr"]  # no stale 3-5
    assert torch.allclose(_read(out["sequence_path"])["IMAGE"], _image(n=2, scale=2.0), atol=1e-2)


def test_no_version_name_collision_and_overwrite(tmp_path):
    _write(tmp_path, "plate", _image(n=4))
    (tmp_path / "plate.notes.exr").write_text("not a frame")
    (tmp_path / "plateX.0001.exr").write_text("another sequence")
    with pytest.raises(ValueError, match="SEQUENCE ALREADY EXISTS"):
        _write(tmp_path, "plate")
    _write(tmp_path, "plate", _image(n=1), overwrite=True)
    assert _frames(tmp_path) == ["plate.0001.exr", "plate.notes.exr", "plateX.0001.exr"]


def test_no_token_ignores_version_name(tmp_path, capsys):
    out = _write(tmp_path, "plate_final", version_name="v03")
    assert (out["name"], out["version_name"]) == ("plate_final", "")
    assert "ignored" in capsys.readouterr().out


def test_glob_characters_in_name_do_not_match_other_sequences(tmp_path):
    _write(tmp_path, "shotA")
    _write(tmp_path, "shot[A]", overwrite=True)  # nothing named 'shot[A]' yet: no collision
    _write(tmp_path, "shot*", _image(n=1))
    _write(tmp_path, "shot*", _image(n=1), overwrite=True)
    assert "shotA.0003.exr" in _frames(tmp_path)


@pytest.mark.parametrize("bad", [{"name": "../escape"}, {"name": "a/b"}, {"name": ".."},
                                 {"name": "a\\b"}, {"version_name": "../v1"}])
def test_bad_names_rejected_before_disk(tmp_path, bad):
    folder = tmp_path / "new"
    kwargs = {"name": "shot_v01", **bad}
    with pytest.raises(ValueError, match="plain name"):
        _write(folder, **kwargs)
    assert not folder.exists()


@pytest.mark.parametrize("overrides, match", [
    ({"fps": 17.0}, "fps|frame rate|supported"),
    ({"start_timecode": "bogus"}, "start_timecode"),
    ({"folder": "relative/dir"}, "absolute"),
])
def test_other_rejections_before_disk(tmp_path, overrides, match):
    folder = tmp_path / "new"
    kwargs = {"folder": folder, "name": "shot", **overrides}
    with pytest.raises(ValueError, match=match):
        _write(**kwargs)
    assert not folder.exists()


def test_bad_aov_chain_refused_before_overwrite_deletes(tmp_path):
    _write(tmp_path, "plate", _image(n=2))
    chain = [("depth", None, torch.zeros(2, 8, 8)), ("depth", None, torch.zeros(2, 8, 8))]
    with pytest.raises(ValueError, match="duplicate AOV name"):
        _write(tmp_path, "plate", _image(n=2), overwrite=True, aov_chain=chain)
    assert _frames(tmp_path) == ["plate.0001.exr", "plate.0002.exr"]


@pytest.mark.parametrize("layout", ["Multichannel", "Separate Files"])
def test_aov_chain_round_trip(tmp_path, layout):
    depth = torch.linspace(0, 100, 3 * 8 * 8).reshape(3, 8, 8)
    (chain,) = OpenClipAOVWriter().execute(name="depth", matte=depth)
    out = _write(tmp_path, "plate_v001", aov_chain=chain, aov_layout=layout, exr_bit_depth="float (32-bit)")
    back = _read(out["sequence_path"])
    assert "depth" in back["clip_info"]["aovs"]
    image, matte = OpenClipAOVReader().execute(aov_handle=back["aov_handle"], part_name="depth")
    assert image is None and torch.equal(matte, depth)
    if layout == "Separate Files":
        assert "plate_v001_AOV_depth.0001.exr" in _frames(tmp_path)
        # Sibling AOV files don't count as versions or collisions of the beauty.
        assert _write(tmp_path, "plate_v001")["version_name"] == "v002"
