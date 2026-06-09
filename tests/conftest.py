"""Pytest fixtures shared across all test modules."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
IMAGE_SIZE = 64  # width and height of generated test images


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def v8_single_clip(fixture_dir) -> Path:
    return fixture_dir / "v8_single_version.clip"


@pytest.fixture(scope="session")
def v8_multi_clip(fixture_dir) -> Path:
    return fixture_dir / "v8_multi_version.clip"


@pytest.fixture(scope="session")
def v9_single_clip(fixture_dir) -> Path:
    return fixture_dir / "v9_single_version.clip"


@pytest.fixture(scope="session")
def image_fixtures(tmp_path_factory) -> Path:
    """Generate small single-frame EXR and PNG test images.

    Files are written with a %04d frame number (frame 0) so callers can
    use the pattern form expected by read_sequence().
    Pattern: image_fixtures / "test_rgb.%04d.exr"   → frame 0 = test_rgb.0000.exr
    """
    import OpenImageIO as oiio

    out = tmp_path_factory.mktemp("image_fixtures")
    _write_exr(out / "test_rgb.0000.exr", nchannels=3)
    _write_exr(out / "test_rgba.0000.exr", nchannels=4)
    _write_png(out / "test_rgb.0000.png", nchannels=3)
    _write_png(out / "test_rgba.0000.png", nchannels=4)
    return out


def _write_exr(path: Path, nchannels: int) -> None:
    import OpenImageIO as oiio

    pixels = _make_pixels(nchannels)
    spec = oiio.ImageSpec(IMAGE_SIZE, IMAGE_SIZE, nchannels, oiio.HALF)
    spec["compression"] = "zip"
    out = oiio.ImageOutput.create(str(path))
    assert out, f"OIIO could not create: {path}"
    assert out.open(str(path), spec), f"OIIO open failed: {path}"
    assert out.write_image(pixels), f"OIIO write_image failed: {path}"
    out.close()


def _write_png(path: Path, nchannels: int) -> None:
    import OpenImageIO as oiio

    pixels = (_make_pixels(nchannels) * 255).astype(np.uint8)
    spec = oiio.ImageSpec(IMAGE_SIZE, IMAGE_SIZE, nchannels, oiio.UINT8)
    out = oiio.ImageOutput.create(str(path))
    assert out, f"OIIO could not create: {path}"
    assert out.open(str(path), spec), f"OIIO open failed: {path}"
    assert out.write_image(pixels), f"OIIO write_image failed: {path}"
    out.close()


def _make_pixels(nchannels: int) -> np.ndarray:
    rng = np.random.default_rng(seed=42)
    pixels = rng.random((IMAGE_SIZE, IMAGE_SIZE, nchannels), dtype=np.float32)
    if nchannels == 4:
        pixels[:, :, 3] = 0.8  # non-trivial alpha so tests can assert on it
    return pixels.astype(np.float16) if nchannels <= 4 else pixels
