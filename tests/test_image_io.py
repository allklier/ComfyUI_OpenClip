from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from ComfyOpenClip.lib import image_io


# --- read tests ---


def test_read_exr_rgb(image_fixtures):
    pattern = str(image_fixtures / "test_rgb.%04d.exr")
    images, masks = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert images.shape == (1, 64, 64, 3)
    assert masks.shape == (1, 64, 64)
    assert images.dtype == torch.float32
    assert torch.all(masks == 0.0)


def test_read_exr_rgba_with_alpha(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.exr")
    images, masks = image_io.read_sequence(pattern, 0, 0, load_alpha=True)
    assert images.shape == (1, 64, 64, 3)
    assert masks.shape == (1, 64, 64)
    assert torch.any(masks > 0.0)


def test_read_exr_rgba_alpha_ignored(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.exr")
    images, masks = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert torch.all(masks == 0.0)


def test_read_png_rgb(image_fixtures):
    pattern = str(image_fixtures / "test_rgb.%04d.png")
    images, masks = image_io.read_sequence(pattern, 0, 0, load_alpha=False)
    assert images.shape == (1, 64, 64, 3)
    assert torch.all(masks == 0.0)


def test_read_png_rgba_with_alpha(image_fixtures):
    pattern = str(image_fixtures / "test_rgba.%04d.png")
    images, masks = image_io.read_sequence(pattern, 0, 0, load_alpha=True)
    assert torch.any(masks > 0.0)


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
    read_images, read_masks = image_io.read_sequence(pattern, 1001, 1001, load_alpha=True)
    assert read_masks.shape == (1, 64, 64)
    assert torch.all(read_masks > 0.5)


def test_write_png_rgb(tmp_path):
    pattern = str(tmp_path / "frame.%04d.png")
    image_io.write_sequence(pattern, _make_image(), None, 1, "PNG", "half (16-bit)", "ZIP")
    assert Path(pattern % 1).exists()


def test_write_png_rgba(tmp_path):
    pattern = str(tmp_path / "frame.%04d.png")
    image_io.write_sequence(pattern, _make_image(), _make_mask(), 1, "PNG", "half (16-bit)", "ZIP")
    read_images, read_masks = image_io.read_sequence(pattern, 1, 1, load_alpha=True)
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
