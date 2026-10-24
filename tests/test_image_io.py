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
    # No matte requested -- defaults to fully opaque (VFX convention: absent
    # alpha means "counts everywhere", not "counts nowhere").
    assert torch.all(masks == 1.0)


def test_read_exr_rgb_no_alpha_channel_defaults_to_opaque(image_fixtures):
    # The actual real-world case: load_alpha=True but the source file simply
    # has no alpha channel to load (e.g. a generated background plate).
    pattern = str(image_fixtures / "test_rgb.%04d.exr")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=True)
    assert torch.all(masks == 1.0)


def test_read_exr_rgba_with_alpha(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.exr")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=True)
    assert images.shape == (1, 64, 64, 3)
    assert masks.shape == (1, 64, 64)
    assert torch.any(masks > 0.0)


def test_read_exr_rgba_alpha_ignored(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.exr")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert torch.all(masks == 1.0)


def test_read_png_rgb(image_fixtures):
    pattern = str(image_fixtures / "test_rgb.%04d.png")
    images, masks, _ = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert images.shape == (1, 64, 64, 3)
    assert torch.all(masks == 1.0)


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


def test_probe_aov_names(aov_multipart_fixtures):
    # Header-only listing, including a Cryptomatte-like part nothing asks for --
    # probing must never pixel-decode it, just read its `name` attribute.
    pattern = str(aov_multipart_fixtures / "aov_multipart.%04d.exr")
    names = image_io.probe_aov_names(pattern, 0)
    assert set(names) == {"depth", "normals", "cryptomatte"}


def test_probe_aov_names_beauty_only_is_empty(image_fixtures):
    pattern = str(image_fixtures / "test_rgb.%04d.exr")
    assert image_io.probe_aov_names(pattern, 0) == []


def test_read_named_part_multipart_real_world_convention(aov_multipart_fixtures, aov_pixels):
    # Regression test for the real-world convention: a true multi-part EXR where every
    # part just uses generic R,G,B channel names and the AOV is identified only by the
    # EXR part `name` attribute (confirmed against an actual Flame render whose AOVs were
    # imported from another DCC). See lib/CLAUDE.md "AOV Channel Handling".
    pattern = str(aov_multipart_fixtures / "aov_multipart.%04d.exr")

    # depth: replicated scalar across R,G,B -> single effective value, but still 3
    # channels on disk, so image is populated and matte = "R".
    d_image, d_matte = image_io.read_named_part(pattern, 0, 0, "depth")
    assert torch.allclose(d_matte[0], torch.from_numpy(aov_pixels["depth"][:, :, 0]), atol=1e-5)

    n_image, n_matte = image_io.read_named_part(pattern, 0, 0, "normals")
    assert torch.allclose(n_image[0], torch.from_numpy(aov_pixels["normal"]), atol=1e-5)
    assert torch.allclose(n_matte[0], torch.from_numpy(aov_pixels["normal"][:, :, 0]), atol=1e-5)

    # Nothing asked for cryptomatte or a nonexistent name -- both absent.
    x_image, x_matte = image_io.read_named_part(pattern, 0, 0, "nonexistent")
    assert x_image is None and x_matte is None


def test_read_named_part_single_channel_is_matte_only(tmp_path, write_multipart_exr):
    # A genuinely single-channel part (not a replicated scalar) has no meaningful
    # `image` at all.
    pixels = np.random.default_rng(1).random((64, 64, 1)).astype(np.float32)
    write_multipart_exr(tmp_path / "single.0000.exr", [
        (np.random.default_rng(2).random((64, 64, 4)).astype(np.float32), ("R", "G", "B", "A"), "beauty"),
        (pixels, ("Y",), "luma"),
    ])
    image, matte = image_io.read_named_part(str(tmp_path / "single.%04d.exr"), 0, 0, "luma")
    assert image is None
    assert torch.allclose(matte[0], torch.from_numpy(pixels[:, :, 0]), atol=1e-5)


def test_read_named_part_absent_in_whole_range_returns_none(aov_multipart_fixtures):
    pattern = str(aov_multipart_fixtures / "aov_multipart.%04d.exr")
    image, matte = image_io.read_named_part(pattern, 0, 0, "does_not_exist")
    assert image is None
    assert matte is None


def test_read_named_part_zero_fills_frames_individually_missing(tmp_path, write_multipart_exr):
    # Present in frame 0 and 2 but not 1 -- whole-sequence signal (found), per-frame
    # zero-fill for the one that lacks it.
    beauty = np.random.default_rng(3).random((64, 64, 4)).astype(np.float32)
    present = np.full((64, 64, 3), 0.5, dtype=np.float32)
    for fn, has_it in [(0, True), (1, False), (2, True)]:
        parts = [(beauty, ("R", "G", "B", "A"), "beauty")]
        if has_it:
            parts.append((present, ("R", "G", "B"), "myaov"))
        write_multipart_exr(tmp_path / f"seq.{fn:04d}.exr", parts)

    image, matte = image_io.read_named_part(str(tmp_path / "seq.%04d.exr"), 0, 2, "myaov")
    assert image is not None  # found overall
    assert torch.allclose(image[0], torch.from_numpy(present), atol=1e-5)
    assert torch.all(image[1] == 0.0)  # zero-filled, not skipped
    assert torch.allclose(image[2], torch.from_numpy(present), atol=1e-5)


def test_write_aov_multichannel_round_trip(tmp_path):
    pattern = str(tmp_path / "shot.%04d.exr")
    image = _make_image()
    normal = torch.rand(1, 64, 64, 3) * 2 - 1
    depth = torch.rand(1, 64, 64) * 100
    image_io.write_sequence(
        pattern, image, None, 1, "EXR", "float (32-bit)", "ZIP",
        aov_entries=[("normal", normal, None), ("depth", None, depth)],
        aov_layout="Multichannel",
    )
    assert not list(tmp_path.glob("shot_AOV_*"))  # true multi-part, one file, no siblings
    names = image_io.probe_aov_names(pattern, 1)
    assert set(names) == {"normal", "depth"}
    n_image, _n_matte = image_io.read_named_part(pattern, 1, 1, "normal")
    assert torch.allclose(n_image, normal, atol=1e-4)
    _d_image, d_matte = image_io.read_named_part(pattern, 1, 1, "depth")
    assert torch.allclose(d_matte, depth, atol=1e-4)


def test_write_aov_separate_files_round_trip(tmp_path):
    pattern = str(tmp_path / "shot.%04d.exr")
    image = _make_image()
    normal = torch.rand(1, 64, 64, 3) * 2 - 1
    depth = torch.rand(1, 64, 64) * 100
    image_io.write_sequence(
        pattern, image, None, 1, "EXR", "float (32-bit)", "ZIP",
        aov_entries=[("normal", normal, None), ("depth", None, depth)],
        aov_layout="Separate Files",
    )
    assert (tmp_path / "shot_AOV_normal.0001.exr").exists()
    assert (tmp_path / "shot_AOV_depth.0001.exr").exists()
    # the beauty file itself carries none, but the probe finds the sibling files
    assert image_io.probe_aov_names(pattern, 1) == ["depth", "normal"]

    # readable through the *beauty* pattern (what a Reader's aov_handle holds) ...
    n_image, _n_matte = image_io.read_named_part(pattern, 1, 1, "normal")
    assert torch.allclose(n_image, normal, atol=1e-4)
    _d_image, d_matte = image_io.read_named_part(pattern, 1, 1, "depth")
    assert torch.allclose(d_matte, depth, atol=1e-3)
    # ... and through the sibling pattern directly, as before
    n_image, _ = image_io.read_named_part(str(tmp_path / "shot_AOV_normal.%04d.exr"), 1, 1, "normal")
    assert torch.allclose(n_image, normal, atol=1e-4)
    assert image_io.read_named_part(pattern, 1, 1, "nope") == (None, None)


def test_write_aov_png_raises(tmp_path):
    pattern = str(tmp_path / "shot.%04d.png")
    with pytest.raises(ValueError, match="EXR"):
        image_io.write_sequence(
            pattern, _make_image(), None, 1, "PNG", "half (16-bit)", "ZIP",
            aov_entries=[("depth", None, torch.rand(1, 64, 64))],
        )


def test_write_aov_invalid_layout_raises(tmp_path):
    pattern = str(tmp_path / "shot.%04d.exr")
    with pytest.raises(ValueError, match="aov_layout"):
        image_io.write_sequence(
            pattern, _make_image(), None, 1, "EXR", "half (16-bit)", "ZIP",
            aov_entries=[("depth", None, torch.rand(1, 64, 64))], aov_layout="Bogus",
        )


def test_write_aov_duplicate_name_raises(tmp_path):
    pattern = str(tmp_path / "shot.%04d.exr")
    with pytest.raises(ValueError, match="duplicate"):
        image_io.write_sequence(
            pattern, _make_image(), None, 1, "EXR", "half (16-bit)", "ZIP",
            aov_entries=[("depth", None, torch.zeros(1, 64, 64)), ("depth", None, torch.ones(1, 64, 64))],
        )


@pytest.mark.parametrize("path_pattern,name,expected", [
    ("/a/b/shot.%04d.exr", "normal", "/a/b/shot_AOV_normal.%04d.exr"),
    ("/a/b/shot.%06d.exr", "depth", "/a/b/shot_AOV_depth.%06d.exr"),
    ("/a/b/shot.exr", "normal", "/a/b/shot_AOV_normal.exr"),  # static path, no frame token
    ("/a/b/shot.%04d.exr", "my aov!", "/a/b/shot_AOV_my_aov_.%04d.exr"),  # sanitized
])
def test_aov_sibling_pattern(path_pattern, name, expected):
    assert image_io._aov_sibling_pattern(path_pattern, name) == expected


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
    _images, _masks, metadata = image_io.read_sequence(pattern, 1, 1, load_alpha=False)
    assert metadata[image_io.COLOUR_SPACE_BACKUP_KEY] == colour_space_value


# --- single-part multilayer (Blender/Nuke dot-named channels) ---


def test_probe_aov_names_lists_dot_named_layers(blender_multilayer_fixtures):
    names = image_io.probe_aov_names(str(blender_multilayer_fixtures / "render.%04d.exr"), 1)
    assert set(names) == {"CamelMatte.Combined", "ViewLayer.Combined", "ViewLayer.Depth",
                          "ViewLayer.Noisy Image", "ViewLayer.Normal"}


def test_read_sequence_layer_reads_prefixed_beauty_and_alpha(blender_multilayer_fixtures):
    from .conftest import blender_frame_pixels

    pattern = str(blender_multilayer_fixtures / "render.%04d.exr")
    images, masks, _meta = image_io.read_sequence(pattern, 1, 2, load_alpha=True, layer="CamelMatte.Combined")
    for i, frame in enumerate((1, 2)):
        px = blender_frame_pixels(frame)
        want = np.stack([px[f"CamelMatte.Combined.{c}"] for c in "RGB"], axis=-1)
        assert torch.allclose(images[i], torch.from_numpy(want), atol=1e-6)
        assert torch.allclose(masks[i], torch.from_numpy(px["CamelMatte.Combined.A"]), atol=1e-6)


def test_read_sequence_missing_layer_names_the_available_layers(blender_multilayer_fixtures):
    pattern = str(blender_multilayer_fixtures / "render.%04d.exr")
    with pytest.raises(RuntimeError, match="layers in this file: .*ViewLayer.Combined"):
        image_io.read_sequence(pattern, 1, 1, load_alpha=True)


def test_read_named_part_dot_named_layer(blender_multilayer_fixtures):
    from .conftest import blender_frame_pixels

    pattern = str(blender_multilayer_fixtures / "render.%04d.exr")
    n_image, n_matte = image_io.read_named_part(pattern, 1, 3, "ViewLayer.Normal")
    d_image, d_matte = image_io.read_named_part(pattern, 1, 3, "ViewLayer.Depth")
    for i, frame in enumerate((1, 2, 3)):
        px = blender_frame_pixels(frame)
        want = np.stack([px[f"ViewLayer.Normal.{c}"] for c in "XYZ"], axis=-1)
        assert torch.allclose(n_image[i], torch.from_numpy(want), atol=1e-6)
        assert torch.allclose(n_matte[i], torch.from_numpy(px["ViewLayer.Normal.X"]), atol=1e-6)
        assert torch.allclose(d_matte[i], torch.from_numpy(px["ViewLayer.Depth.Z"]), atol=1e-6)
    assert d_image is None  # single channel -> matte only
    # A layer name must match whole: "ViewLayer" alone is not a layer of its own channels.
    assert image_io.read_named_part(pattern, 1, 1, "ViewLayer") == (None, None)
