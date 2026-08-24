from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from ComfyUI_OpenClip.lib import image_io


# --- read tests ---


def test_read_exr_rgb(image_fixtures):
    pattern = str(image_fixtures / "test_rgb.%04d.exr")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert images.shape == (1, 64, 64, 3)
    assert masks.shape == (1, 64, 64)
    assert images.dtype == torch.float32
    assert torch.all(masks == 0.0)


def test_read_exr_rgba_with_alpha(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.exr")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=True)
    assert images.shape == (1, 64, 64, 3)
    assert masks.shape == (1, 64, 64)
    assert torch.any(masks > 0.0)


def test_read_exr_rgba_alpha_ignored(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.exr")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert torch.all(masks == 0.0)


def test_read_png_rgb(image_fixtures):
    pattern = str(image_fixtures / "test_rgb.%04d.png")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert images.shape == (1, 64, 64, 3)
    assert torch.all(masks == 0.0)


def test_read_png_rgba_with_alpha(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.png")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=True)
    assert torch.any(masks > 0.0)


def test_read_static_path_no_frame_token(image_fixtures):
    # A literal path with no %d token (Flame encoding="file" stills) is read
    # as-is, ignoring start/end frame numbers.
    static_path = str(image_fixtures / "test_rgb.0000.png")
    images, masks, _ = image_io.read_sequence(static_path, 1, 1, load_alpha=False)
    assert images.shape == (1, 64, 64, 3)


def test_read_missing_frame_raises(tmp_path):
    # OIIO returns None for an inaccessible/missing file; we raise FileNotFoundError
    pattern = str(tmp_path / "frame.%04d.exr")
    with pytest.raises(FileNotFoundError):
        image_io.read_sequence(pattern, 1001, 1001, load_alpha=False)


def test_read_invalid_range_raises(image_fixtures):
    path = str(image_fixtures / "test_rgb.exr")
    with pytest.raises(ValueError, match="start_frame"):
        image_io.read_sequence(path, 10, 5, load_alpha=False)


# --- write tests ---


def _make_image(h=64, w=64) -> torch.Tensor:
    return torch.rand(1, h, w, 3, dtype=torch.float32)


def _make_mask(h=64, w=64) -> torch.Tensor:
    return torch.full((1, h, w), 0.75, dtype=torch.float32)


def test_write_exr_half_zip(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    image_io.write_sequence(pattern, _make_image(), None, 1001, "EXR", "half (16-bit)", "ZIP")
    assert Path(pattern % 1001).exists()


def test_write_exr_half_piz(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    image_io.write_sequence(pattern, _make_image(), None, 1001, "EXR", "half (16-bit)", "PIZ")
    assert Path(pattern % 1001).exists()


def test_write_exr_half_dwab(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    image_io.write_sequence(pattern, _make_image(), None, 1001, "EXR", "half (16-bit)", "DWAB")
    assert Path(pattern % 1001).exists()


def test_write_exr_float(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    image_io.write_sequence(pattern, _make_image(), None, 1001, "EXR", "float (32-bit)", "ZIP")
    assert Path(pattern % 1001).exists()


def test_write_exr_rgba_alpha_preserved(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    image = _make_image()
    mask = _make_mask()
    image_io.write_sequence(pattern, image, mask, 1001, "EXR", "half (16-bit)", "ZIP")
    read_images, read_masks, _ = image_io.read_sequence(pattern, 1001, 1001, load_alpha=True)
    assert read_masks.shape == (1, 64, 64)
    assert torch.all(read_masks > 0.5)


def test_write_png_rgb(tmp_path):
    pattern = str(tmp_path / "frame.%04d.png")
    image_io.write_sequence(pattern, _make_image(), None, 1, "PNG", "half (16-bit)", "ZIP")
    assert Path(pattern % 1).exists()


def test_write_png_rgba(tmp_path):
    pattern = str(tmp_path / "frame.%04d.png")
    image_io.write_sequence(pattern, _make_image(), _make_mask(), 1, "PNG", "half (16-bit)", "ZIP")
    read_images, read_masks, _ = image_io.read_sequence(pattern, 1, 1, load_alpha=True)
    assert torch.any(read_masks > 0.5)


def test_frame_padding_default(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    image_io.write_sequence(pattern, _make_image(), None, 1, "EXR", "half (16-bit)", "ZIP")
    assert Path(tmp_path / "frame.0001.exr").exists()


def test_frame_padding_six_digits(tmp_path):
    pattern = str(tmp_path / "frame.%06d.exr")
    image_io.write_sequence(pattern, _make_image(), None, 1, "EXR", "half (16-bit)", "ZIP")
    assert Path(tmp_path / "frame.000001.exr").exists()


def test_write_invalid_compression_raises(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    with pytest.raises(ValueError, match="exr_compression"):
        image_io.write_sequence(pattern, _make_image(), None, 1, "EXR", "half (16-bit)", "BOGUS")


def test_write_invalid_bit_depth_raises(tmp_path):
    pattern = str(tmp_path / "frame.%04d.exr")
    with pytest.raises(ValueError, match="exr_bit_depth"):
        image_io.write_sequence(pattern, _make_image(), None, 1, "EXR", "bogus", "ZIP")


# --- AOV tests ---


def test_read_aov_multichannel_by_name(aov_fixtures, aov_pixels):
    pattern = str(aov_fixtures / "aov_multi.%04d.exr")
    images, masks, aovs, _ = image_io.read_sequence_with_aovs(pattern, 0, 0, load_alpha=True)
    assert torch.allclose(images[0], torch.from_numpy(aov_pixels["beauty"][:, :, :3]), atol=1e-5)
    assert torch.allclose(masks[0], torch.from_numpy(aov_pixels["beauty"][:, :, 3]), atol=1e-5)
    assert aovs["normal"].shape == (1, 64, 64, 3)
    assert aovs["normal_world"].shape == (1, 64, 64, 3)
    assert aovs["depth"].shape == (1, 64, 64)
    assert torch.allclose(aovs["normal"][0], torch.from_numpy(aov_pixels["normal"]), atol=1e-5)
    assert torch.allclose(aovs["normal_world"][0], torch.from_numpy(aov_pixels["normal_world"]), atol=1e-5)
    assert torch.allclose(aovs["depth"][0], torch.from_numpy(aov_pixels["depth"][:, :, 0]), atol=1e-5)


def test_read_aov_separate_files(aov_fixtures, aov_pixels):
    pattern = str(aov_fixtures / "aov_sep.%04d.exr")
    images, masks, aovs, _ = image_io.read_sequence_with_aovs(pattern, 0, 0, load_alpha=True)
    assert torch.allclose(aovs["normal"][0], torch.from_numpy(aov_pixels["normal"]), atol=1e-5)
    assert torch.allclose(aovs["normal_world"][0], torch.from_numpy(aov_pixels["normal_world"]), atol=1e-5)
    assert torch.allclose(aovs["depth"][0], torch.from_numpy(aov_pixels["depth"][:, :, 0]), atol=1e-5)


def test_read_aov_absent_returns_none(aov_fixtures):
    # A layer never found in any frame of the sequence is None, not a zero tensor --
    # so a consumer (e.g. ComfyUI_Harmonize's NormalsRouter/DepthRouter) can tell
    # "this AOV pass doesn't exist for this shot" apart from "it exists and is
    # legitimately all-zero."
    pattern = str(aov_fixtures / "aov_none.%04d.exr")
    images, masks, aovs, _ = image_io.read_sequence_with_aovs(pattern, 0, 0, load_alpha=True)
    assert aovs["normal"] is None
    assert aovs["normal_world"] is None
    assert aovs["depth"] is None


def test_read_aov_multipart_by_part_name(aov_multipart_fixtures, aov_pixels):
    # Regression test for the real-world convention: a true multi-part EXR where every
    # part just uses generic R,G,B channel names and the AOV is identified only by the
    # EXR part `name` attribute (confirmed against an actual Flame render whose AOVs were
    # imported from another DCC). Depth's replicated-scalar R==G==B reads as channel 0.
    # See lib/CLAUDE.md "AOV Channel Handling".
    pattern = str(aov_multipart_fixtures / "aov_multipart.%04d.exr")
    images, masks, aovs, _ = image_io.read_sequence_with_aovs(pattern, 0, 0, load_alpha=True)
    assert torch.allclose(images[0], torch.from_numpy(aov_pixels["beauty"][:, :, :3]), atol=1e-5)
    assert torch.allclose(masks[0], torch.from_numpy(aov_pixels["beauty"][:, :, 3]), atol=1e-5)
    assert torch.allclose(aovs["depth"][0], torch.from_numpy(aov_pixels["depth"][:, :, 0]), atol=1e-5)
    assert torch.allclose(aovs["normal_raw"][0], torch.from_numpy(aov_pixels["normal"]), atol=1e-5)
    # No dotted-channel AOVs and no part named "" (world-normal lookup is disabled by
    # default) -- both come back None rather than a guessed zero-fill.
    assert aovs["normal"] is None
    assert aovs["normal_world"] is None


def test_read_aov_multipart_part_name_override(write_multipart_exr, aov_pixels, tmp_path):
    # Non-default part names, all three overrides supplied explicitly -- proves the
    # override params actually drive lookup, not just the hardcoded defaults.
    depth_replicated = np.repeat(aov_pixels["depth"][:, :, 0:1], 3, axis=-1)
    write_multipart_exr(tmp_path / "custom.0000.exr", [
        (aov_pixels["beauty"], ("R", "G", "B", "A"), "beauty"),
        (depth_replicated, ("R", "G", "B"), "myDepth"),
        (aov_pixels["normal_world"], ("R", "G", "B"), "myWorldNormals"),
        (aov_pixels["normal"], ("R", "G", "B"), "myRawNormals"),
    ])
    pattern = str(tmp_path / "custom.%04d.exr")
    images, masks, aovs, _ = image_io.read_sequence_with_aovs(
        pattern, 0, 0, load_alpha=True,
        depth_part_name="myDepth", normal_world_part_name="myWorldNormals", normal_raw_part_name="myRawNormals",
    )
    assert torch.allclose(aovs["depth"][0], torch.from_numpy(aov_pixels["depth"][:, :, 0]), atol=1e-5)
    assert torch.allclose(aovs["normal_world"][0], torch.from_numpy(aov_pixels["normal_world"]), atol=1e-5)
    assert torch.allclose(aovs["normal_raw"][0], torch.from_numpy(aov_pixels["normal"]), atol=1e-5)


def test_read_sequence_ignores_aovs(aov_fixtures, aov_pixels):
    # read_sequence() (no AOVs) still works unchanged on an AOV-bearing file.
    pattern = str(aov_fixtures / "aov_multi.%04d.exr")
    images, masks, _meta = image_io.read_sequence(pattern, 0, 0, load_alpha=True)
    assert images.shape == (1, 64, 64, 3)
    assert torch.allclose(images[0], torch.from_numpy(aov_pixels["beauty"][:, :, :3]), atol=1e-5)


def test_write_aov_multichannel_round_trip(tmp_path):
    pattern = str(tmp_path / "shot.%04d.exr")
    image = _make_image()
    normal = torch.rand(1, 64, 64, 3) * 2 - 1
    normal_world = torch.rand(1, 64, 64, 3) * 2 - 1
    depth = torch.rand(1, 64, 64) * 100
    image_io.write_sequence(
        pattern, image, None, 1, "EXR", "float (32-bit)", "ZIP",
        aovs={"normal": normal, "normal_world": normal_world, "depth": depth},
        aov_layout="Multichannel",
    )
    assert not list(tmp_path.glob("shot_AOV_*"))  # single file, no siblings
    _images, _masks, aovs, _ = image_io.read_sequence_with_aovs(pattern, 1, 1, load_alpha=False)
    assert torch.allclose(aovs["normal"], normal, atol=1e-4)
    assert torch.allclose(aovs["normal_world"], normal_world, atol=1e-4)
    assert torch.allclose(aovs["depth"], depth, atol=1e-4)


def test_write_aov_separate_files_round_trip(tmp_path):
    pattern = str(tmp_path / "shot.%04d.exr")
    image = _make_image()
    normal = torch.rand(1, 64, 64, 3) * 2 - 1
    depth = torch.rand(1, 64, 64) * 100
    image_io.write_sequence(
        pattern, image, None, 1, "EXR", "float (32-bit)", "ZIP",
        aovs={"normal": normal, "depth": depth},
        aov_layout="Separate Files",
    )
    assert (tmp_path / "shot_AOV_Normals.0001.exr").exists()
    assert (tmp_path / "shot_AOV_Depth.0001.exr").exists()
    assert not (tmp_path / "shot_AOV_NormalsWorld.0001.exr").exists()  # not connected, not written
    _images, _masks, aovs, _ = image_io.read_sequence_with_aovs(pattern, 1, 1, load_alpha=False)
    assert torch.allclose(aovs["normal"], normal, atol=1e-4)
    assert torch.allclose(aovs["depth"], depth, atol=1e-4)
    assert aovs["normal_world"] is None  # never connected -> None on read back, not zero-filled


def test_write_aov_png_raises(tmp_path):
    pattern = str(tmp_path / "shot.%04d.png")
    with pytest.raises(ValueError, match="EXR"):
        image_io.write_sequence(
            pattern, _make_image(), None, 1, "PNG", "half (16-bit)", "ZIP",
            aovs={"depth": torch.rand(1, 64, 64)},
        )


def test_write_aov_invalid_layout_raises(tmp_path):
    pattern = str(tmp_path / "shot.%04d.exr")
    with pytest.raises(ValueError, match="aov_layout"):
        image_io.write_sequence(
            pattern, _make_image(), None, 1, "EXR", "half (16-bit)", "ZIP",
            aovs={"depth": torch.rand(1, 64, 64)}, aov_layout="Bogus",
        )


@pytest.mark.parametrize("path_pattern,suffix,expected", [
    ("/a/b/shot.%04d.exr", "_AOV_Normals", "/a/b/shot_AOV_Normals.%04d.exr"),
    ("/a/b/shot.%06d.exr", "_AOV_Depth", "/a/b/shot_AOV_Depth.%06d.exr"),
    ("/a/b/shot.exr", "_AOV_Normals", "/a/b/shot_AOV_Normals.exr"),  # static path, no frame token
])
def test_aov_sibling_pattern(path_pattern, suffix, expected):
    assert image_io._aov_sibling_pattern(path_pattern, suffix) == expected


# --- fp16/fp32 no-clamp regression tests (EXR_REQUIREMENTS.md requirement #1) ---


def _out_of_range_image() -> torch.Tensor:
    # Representative of an ACEScg specular highlight/negative-primary excursion.
    return torch.tensor([[[[2.5, -0.3, 1.8]]]], dtype=torch.float32).expand(1, 8, 8, 3).clone()


@pytest.mark.parametrize("bit_depth", ["half (16-bit)", "float (32-bit)"])
def test_exr_round_trip_does_not_clamp(tmp_path, bit_depth):
    pattern = str(tmp_path / f"hdr_{bit_depth[:4]}.%04d.exr")
    original = _out_of_range_image()
    image_io.write_sequence(pattern, original, None, 1, "EXR", bit_depth, "ZIP")
    read, _masks, _meta = image_io.read_sequence(pattern, 1, 1, load_alpha=False)
    # half (16-bit) has coarser precision than float32 but must not clip toward 0..1.
    tol = 1e-2 if bit_depth.startswith("half") else 1e-5
    assert torch.allclose(read, original, atol=tol)
    assert read.min() < 0.0
    assert read.max() > 1.0


def test_png_write_does_clamp_intentionally(tmp_path):
    # PNG is 8-bit display-referred; clamping here is correct, unlike EXR above.
    pattern = str(tmp_path / "hdr.%04d.png")
    original = _out_of_range_image()
    image_io.write_sequence(pattern, original, None, 1, "PNG", "half (16-bit)", "ZIP")
    read, _masks, _meta = image_io.read_sequence(pattern, 1, 1, load_alpha=False)
    assert read.min() >= 0.0
    assert read.max() <= 1.0
    assert not torch.allclose(read, original, atol=0.1)  # detail above 1.0 / below 0.0 is gone


# --- colour_space metadata tests (EXR_REQUIREMENTS.md requirement #3) ---


@pytest.mark.parametrize("colour_space_value", [
    "ACEScg",  # OIIO's built-in registry renames this on write via oiio:ColorSpace alone
    "Log3G10 RedWideGamutRGB",  # camera log profile; OIIO drops this on write via oiio:ColorSpace alone
])
def test_colour_space_metadata_round_trips_verbatim(tmp_path, colour_space_value):
    pattern = str(tmp_path / "cs.%04d.exr")
    image_io.write_sequence(
        pattern, _make_image(), None, 1, "EXR", "float (32-bit)", "ZIP",
        metadata={image_io.COLOUR_SPACE_KEY: colour_space_value},
    )
    _images, _masks, _meta_aov, metadata = image_io.read_sequence_with_aovs(pattern, 1, 1, load_alpha=False)
    assert metadata[image_io.COLOUR_SPACE_BACKUP_KEY] == colour_space_value
