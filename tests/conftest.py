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
# True multi-part EXR, generic R/G/B channel names per part, identity carried only by
# each part's `name` attribute -- the real-world convention (confirmed against an actual
# Flame render whose AOVs were imported from another DCC). See lib/CLAUDE.md "AOV Channel
# Handling". There is no dotted-channel or separate-sibling-file convention to fixture
# for any more -- those were this project's own synthetic-fixture inventions, never
# something a real render produced, and were dropped 2026-09-23 along with the old
# fixed-three-AOV Reader/Writer interface.


def _make_aov_pixels() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed=99)
    return {
        "beauty": rng.random((IMAGE_SIZE, IMAGE_SIZE, 4), dtype=np.float32),
        "normal": rng.random((IMAGE_SIZE, IMAGE_SIZE, 3), dtype=np.float32) * 2 - 1,
        "normal_world": rng.random((IMAGE_SIZE, IMAGE_SIZE, 3), dtype=np.float32) * 2 - 1,
        "depth": rng.random((IMAGE_SIZE, IMAGE_SIZE, 1), dtype=np.float32) * 100,
    }


@pytest.fixture(scope="session")
def aov_pixels() -> dict[str, np.ndarray]:
    """The exact pixel arrays baked into aov_multipart_fixtures, for tests to assert against."""
    return _make_aov_pixels()


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
    `name` attribute. Depth is a replicated scalar across R,G,B (also confirmed
    empirically against a real file). Includes a Cryptomatte-like extra part (also
    generic R,G,B, indistinguishable from a real AOV by channel name alone) that a probe
    lists but nothing decodes unless a test explicitly asks for it by name.

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


# Channel layout of a real Blender 5.2 multilayer render (CGTestPlates/InteriorCamel),
# single part, in the alphabetical order Blender writes them.
BLENDER_CHANNELS: tuple[str, ...] = (
    "CamelMatte.Combined.A", "CamelMatte.Combined.B", "CamelMatte.Combined.G", "CamelMatte.Combined.R",
    "ViewLayer.Combined.A", "ViewLayer.Combined.B", "ViewLayer.Combined.G", "ViewLayer.Combined.R",
    "ViewLayer.Depth.Z",
    "ViewLayer.Noisy Image.A", "ViewLayer.Noisy Image.B", "ViewLayer.Noisy Image.G", "ViewLayer.Noisy Image.R",
    "ViewLayer.Normal.X", "ViewLayer.Normal.Y", "ViewLayer.Normal.Z",
)


def blender_frame_pixels(frame: int) -> dict[str, np.ndarray]:
    """Distinct, frame-dependent values per channel, keyed by channel name."""
    rng = np.random.default_rng(seed=100 + frame)
    return {n: rng.random((IMAGE_SIZE, IMAGE_SIZE)).astype(np.float32) - 0.5 for n in BLENDER_CHANNELS}


@pytest.fixture(scope="session")
def blender_multilayer_fixtures(tmp_path_factory) -> Path:
    """Three frames (1-3) of a Blender-style single-part multilayer EXR, written with the
    '####' naming Blender uses: blender_multilayer_fixtures / "render.0001.exr" etc.
    Tagged lin_ap1_scene, as Blender 5.2 tags ACEScg renders."""
    import OpenImageIO as oiio

    out = tmp_path_factory.mktemp("blender_multilayer_fixtures")
    for frame in (1, 2, 3):
        px = blender_frame_pixels(frame)
        spec = oiio.ImageSpec(IMAGE_SIZE, IMAGE_SIZE, len(BLENDER_CHANNELS), oiio.FLOAT)
        spec.channelnames = BLENDER_CHANNELS
        spec.attribute("oiio:ColorSpace", "lin_ap1_scene")
        path = str(out / f"render.{frame:04d}.exr")
        o = oiio.ImageOutput.create(path)
        assert o and o.open(path, spec), f"OIIO open failed: {path}"
        assert o.write_image(np.stack([px[n] for n in BLENDER_CHANNELS], axis=-1)), f"write failed: {path}"
        o.close()
    return out


def pytest_addoption(parser):
    parser.addoption(
        "--tier", choices=("smoke", "full"), default="smoke",
        help="smoke (default; what CI runs): unit tests + synthetic Flame-like cases. "
             "full: also the real-Flame-clip tests (marker flame_clips; tests/flame_clips.md).",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: heavier large-image/long-sequence tests; deselect with -m 'not slow'")
    config.addinivalue_line(
        "markers",
        "flame_clips: needs real Flame/Autodesk clips (gitignored, not redistributable); runs only with "
        "--tier full, and skips there when the clips are absent",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--tier") == "full":
        return
    skip = pytest.mark.skip(reason="real Flame clips: full tier only (pytest --tier full)")
    for item in items:
        if "flame_clips" in item.keywords:
            item.add_marker(skip)


def named_result(node_cls, result) -> dict:
    """A node's result tuple as {output name: value}, so tests don't depend on slot order.
    Also flattens clip_info's keys in as 'info.<key>' for brevity."""
    out = dict(zip(node_cls.RETURN_NAMES, result))
    for k, v in (out.get("clip_info") or {}).items():
        out[f"info.{k}"] = v
    return out
