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


# --- AOV fixtures ---
#
# Single-part EXR, dotted <layer>.<channel> naming per EXR_REQUIREMENTS.md's convention
# table, matching ComfyUI_Harmonize/tests/generate_synthetic_exr.py exactly.

AOV_MULTICHANNEL_NAMES = ("R", "G", "B", "A", "N.X", "N.Y", "N.Z", "Nw.X", "Nw.Y", "Nw.Z", "Z")


def _make_aov_pixels() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed=99)
    return {
        "beauty": rng.random((IMAGE_SIZE, IMAGE_SIZE, 4), dtype=np.float32),
        "normal": rng.random((IMAGE_SIZE, IMAGE_SIZE, 3), dtype=np.float32) * 2 - 1,
        "normal_world": rng.random((IMAGE_SIZE, IMAGE_SIZE, 3), dtype=np.float32) * 2 - 1,
        "depth": rng.random((IMAGE_SIZE, IMAGE_SIZE, 1), dtype=np.float32) * 100,
    }


def _write_named_exr(path: Path, pixels: np.ndarray, channelnames: tuple) -> None:
    import OpenImageIO as oiio

    spec = oiio.ImageSpec(IMAGE_SIZE, IMAGE_SIZE, pixels.shape[-1], oiio.FLOAT)
    spec.channelnames = channelnames
    spec["compression"] = "zip"
    out = oiio.ImageOutput.create(str(path))
    assert out, f"OIIO could not create: {path}"
    assert out.open(str(path), spec), f"OIIO open failed: {path}"
    assert out.write_image(pixels.astype(np.float32)), f"OIIO write_image failed: {path}"
    out.close()


@pytest.fixture(scope="session")
def aov_pixels() -> dict[str, np.ndarray]:
    """The exact pixel arrays baked into aov_fixtures, for tests to assert against."""
    return _make_aov_pixels()


@pytest.fixture(scope="session")
def aov_fixtures(tmp_path_factory, aov_pixels) -> Path:
    """Both AOV layouts EXR_REQUIREMENTS.md describes, plus a beauty-only file with no AOVs.

    Pattern: aov_fixtures / "aov_multi.%04d.exr"       -> single multichannel file, frame 0
             aov_fixtures / "aov_sep.%04d.exr"          -> beauty half of the separate-file set
             aov_fixtures / "aov_sep_AOV_Normals.%04d.exr" / "..._NormalsWorld..." / "..._Depth..."
             aov_fixtures / "aov_none.%04d.exr"         -> beauty only, no AOV channels/siblings
    """
    out = tmp_path_factory.mktemp("aov_fixtures")
    p = aov_pixels

    multi = np.concatenate([p["beauty"], p["normal"], p["normal_world"], p["depth"]], axis=-1)
    _write_named_exr(out / "aov_multi.0000.exr", multi, AOV_MULTICHANNEL_NAMES)

    _write_named_exr(out / "aov_sep.0000.exr", p["beauty"], ("R", "G", "B", "A"))
    _write_named_exr(out / "aov_sep_AOV_Normals.0000.exr", p["normal"], ("N.X", "N.Y", "N.Z"))
    _write_named_exr(out / "aov_sep_AOV_NormalsWorld.0000.exr", p["normal_world"], ("Nw.X", "Nw.Y", "Nw.Z"))
    _write_named_exr(out / "aov_sep_AOV_Depth.0000.exr", p["depth"], ("Z",))

    _write_named_exr(out / "aov_none.0000.exr", p["beauty"], ("R", "G", "B", "A"))

    return out


def _write_multipart_exr(path: Path, parts: list[tuple[np.ndarray, tuple, str]]) -> None:
    """Write a true multi-part EXR: one subimage per (pixels, channelnames, part_name).

    Mirrors a real Flame multi-layer export whose AOVs were imported from another DCC
    (Cinema 4D) — see lib/CLAUDE.md "AOV Channel Handling".
    """
    import OpenImageIO as oiio

    specs = []
    for pixels, channelnames, name in parts:
        spec = oiio.ImageSpec(IMAGE_SIZE, IMAGE_SIZE, pixels.shape[-1], oiio.FLOAT)
        spec.channelnames = channelnames
        spec.attribute("name", name)
        spec["compression"] = "zip"
        specs.append(spec)

    out = oiio.ImageOutput.create(str(path))
    assert out, f"OIIO could not create: {path}"
    assert out.open(str(path), specs), f"OIIO multi-part open failed: {path}"
    for i, (pixels, _channelnames, _name) in enumerate(parts):
        if i > 0:
            assert out.open(str(path), specs[i], "AppendSubimage"), f"OIIO AppendSubimage failed: {path} part {i}"
        assert out.write_image(pixels.astype(np.float32)), f"OIIO write_image failed: {path} part {i}"
    out.close()


@pytest.fixture(scope="session")
def write_multipart_exr():
    """Callable fixture exposing _write_multipart_exr for tests needing a custom part layout."""
    return _write_multipart_exr


@pytest.fixture(scope="session")
def aov_multipart_fixtures(tmp_path_factory, aov_pixels) -> Path:
    """A true multi-part EXR matching the real-world part-name convention confirmed
    against an actual Flame render (AOVs imported from Cinema 4D): every part uses
    generic R,G,B channel names, and the AOV's identity is carried only by the EXR part
    `name` attribute — not the dotted layer.channel convention aov_fixtures covers. Depth
    is a replicated scalar across R,G,B (also confirmed empirically against a real file).
    Includes a Cryptomatte-like extra part (also generic R,G,B, indistinguishable from a
    real AOV by channel name alone) that no default part-name target matches.

    Pattern: aov_multipart_fixtures / "aov_multipart.%04d.exr"
    """
    out = tmp_path_factory.mktemp("aov_multipart_fixtures")
    p = aov_pixels
    depth_replicated = np.repeat(p["depth"][:, :, 0:1], 3, axis=-1)
    crypto = np.random.default_rng(seed=7).random((IMAGE_SIZE, IMAGE_SIZE, 3)).astype(np.float32)
    _write_multipart_exr(out / "aov_multipart.0000.exr", [
        (p["beauty"], ("R", "G", "B", "A"), "beauty"),
        (depth_replicated, ("R", "G", "B"), "depth"),
        (p["normal"], ("R", "G", "B"), "normals"),
        (crypto, ("R", "G", "B"), "cryptomatte"),
    ])
    return out
