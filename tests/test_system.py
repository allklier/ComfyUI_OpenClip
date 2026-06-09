"""System tests: end-to-end read, write, and round-trip without ComfyUI running."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from ComfyOpenClip.lib import image_io, openclip_xml, package_layout
from ComfyOpenClip.lib.openclip_xml import ClipSpan, ClipVersion
from ComfyOpenClip.nodes.reader import OpenClipReader
from ComfyOpenClip.nodes.version_selector import OpenClipVersionSelector
from ComfyOpenClip.nodes.writer import OpenClipWriter


# --- helpers ---


def _write_seq(tmp_path: Path, n_frames: int = 4, with_alpha: bool = False):
    """Write a small EXR sequence and return (pattern, start_frame)."""
    pattern = str(tmp_path / "frame.%04d.exr")
    images = torch.rand(n_frames, 64, 64, 3)
    masks = torch.full((n_frames, 64, 64), 0.8) if with_alpha else None
    image_io.write_sequence(pattern, images, masks, 1001, "EXR", "half (16-bit)", "ZIP")
    return pattern, 1001


def _clip_with_seq(tmp_path: Path, clip_name: str, version: str, n_frames: int = 4):
    """Create a minimal package (frames + .clip file) and return the clip path."""
    seq_dir = tmp_path / clip_name / "versions" / version
    seq_dir.mkdir(parents=True)
    pattern = str(seq_dir / f"{clip_name}.%04d.exr")
    images = torch.rand(n_frames, 64, 64, 3)
    image_io.write_sequence(pattern, images, None, 1001, "EXR", "half (16-bit)", "ZIP")

    versions = {
        version: ClipVersion(
            uid=version, name=version,
            spans=[ClipSpan(
                path=f"versions/{version}/{clip_name}.%04d.exr",
                start_frame=1001, duration=n_frames,
            )],
        )
    }
    xml_bytes = openclip_xml.generate(clip_name, versions, version)
    clip_file = tmp_path / clip_name / f"{clip_name}.clip"
    clip_file.write_bytes(xml_bytes)
    return str(clip_file), images


# --- read tests ---


def test_read_v8_clip_end_to_end(tmp_path):
    clip_path, original = _clip_with_seq(tmp_path, "sh010", "v001")
    reader = OpenClipReader()
    images, masks, frame_count, width, height, clip_version = reader.execute(
        clip_path=clip_path, version="current",
        start_frame=-1, end_frame=-1, load_alpha=False,
        path_from="", path_to="",
    )
    assert images.shape == (4, 64, 64, 3)
    assert frame_count == 4
    assert width == 64
    assert height == 64
    assert clip_version == "8"


def test_read_explicit_version(tmp_path):
    clip_path, _ = _clip_with_seq(tmp_path, "sh010", "v001")
    reader = OpenClipReader()
    images, *_ = reader.execute(
        clip_path=clip_path, version="v001",
        start_frame=-1, end_frame=-1, load_alpha=False,
        path_from="", path_to="",
    )
    assert images.shape[0] == 4


def test_read_missing_version_raises(tmp_path):
    clip_path, _ = _clip_with_seq(tmp_path, "sh010", "v001")
    reader = OpenClipReader()
    with pytest.raises(ValueError, match="v999"):
        reader.execute(clip_path=clip_path, version="v999",
                       start_frame=-1, end_frame=-1, load_alpha=False,
                       path_from="", path_to="")


def test_read_auto_remap(tmp_path):
    # Simulate an airgap copy: write frames in the real location, but embed a
    # fake absolute path in the clip XML (as Flame would on the origin machine).
    clip_dir = tmp_path / "sh010"
    media_dir = clip_dir / "versions" / "v001"
    media_dir.mkdir(parents=True)

    real_pattern = str(media_dir / "sh010.%04d.exr")
    images_orig = torch.rand(4, 64, 64, 3)
    image_io.write_sequence(real_pattern, images_orig, None, 1001, "EXR", "half (16-bit)", "ZIP")

    # XML embeds a fake origin path; the relative path inside it maps to media_dir
    # relative to clip_dir, which auto_remap should discover.
    versions = {
        "v001": ClipVersion(
            uid="v001", name="v001",
            spans=[ClipSpan(
                path="/fake/origin/machine/sh010/versions/v001/sh010.%04d.exr",
                start_frame=1001, duration=4,
            )],
        )
    }
    xml_bytes = openclip_xml.generate("sh010", versions, "v001")
    clip_file = clip_dir / "sh010.clip"
    clip_file.write_bytes(xml_bytes)

    reader = OpenClipReader()
    images, _, frame_count, *_ = reader.execute(
        clip_path=str(clip_file), version="current",
        start_frame=-1, end_frame=-1, load_alpha=False,
        path_from="", path_to="",
    )
    assert frame_count == 4
    assert torch.allclose(images, images_orig, atol=1e-3)


# --- write tests ---


def test_write_standard_flame_layout(tmp_path):
    writer = OpenClipWriter()
    images = torch.rand(4, 64, 64, 3)
    (clip_path,) = writer.execute(
        IMAGE=images, output_dir=str(tmp_path), clip_name="myshot",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )
    clip_file = Path(clip_path)
    assert clip_file.exists()
    media_dir = tmp_path / "myshot" / "versions" / "v001"
    assert (media_dir / "myshot.1001.exr").exists()
    assert (media_dir / "myshot.1004.exr").exists()


def test_write_flat_layout(tmp_path):
    writer = OpenClipWriter()
    images = torch.rand(4, 64, 64, 3)
    (clip_path,) = writer.execute(
        IMAGE=images, output_dir=str(tmp_path), clip_name="myshot",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
        layout="Flat", publish=False,
    )
    assert Path(clip_path).exists()
    assert (tmp_path / "myshot.1001.exr").exists()


def test_write_png_sequence(tmp_path):
    writer = OpenClipWriter()
    images = torch.rand(4, 64, 64, 3)
    (clip_path,) = writer.execute(
        IMAGE=images, output_dir=str(tmp_path), clip_name="myshot",
        version_name="v001", fps="24", start_frame=1, frame_padding=4,
        file_format="PNG", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
        layout="Flat", publish=False,
    )
    assert (tmp_path / "myshot.0001.png").exists()


def test_write_publish_creates_sidecar(tmp_path):
    writer = OpenClipWriter()
    images = torch.rand(4, 64, 64, 3)
    workflow = {"nodes": [{"id": 1, "type": "KSampler"}]}
    (clip_path,) = writer.execute(
        IMAGE=images, output_dir=str(tmp_path), clip_name="myshot",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=True,
        extra_pnginfo={"workflow": workflow},
    )
    # sidecar lives in the version media dir, not beside the .clip
    sidecar = tmp_path / "myshot" / "versions" / "v001" / "myshot.v001.comfy.json"
    assert sidecar.exists()
    saved = json.loads(sidecar.read_text())
    assert saved == workflow

    from lxml import etree
    root = etree.fromstring(Path(clip_path).read_bytes())
    # comfyWorkflow must be inside the version's userData, not at root
    assert root.find("userMetadata") is None
    ver_ud = root.find("versions/version[@uid='v001']/userData")
    comfy_el = ver_ud.find("comfyWorkflow")
    assert comfy_el is not None
    assert comfy_el.get("path") == "versions/v001/myshot.v001.comfy.json"


def test_write_no_publish_no_sidecar(tmp_path):
    writer = OpenClipWriter()
    images = torch.rand(4, 64, 64, 3)
    writer.execute(
        IMAGE=images, output_dir=str(tmp_path), clip_name="myshot",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )
    assert not (tmp_path / "myshot" / "myshot.comfy.json").exists()


# --- round-trip tests ---


def test_round_trip_rgb(tmp_path):
    writer = OpenClipWriter()
    original = torch.rand(4, 64, 64, 3)
    (clip_path,) = writer.execute(
        IMAGE=original, output_dir=str(tmp_path), clip_name="rt",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="float (32-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )
    reader = OpenClipReader()
    images, _, frame_count, *_ = reader.execute(
        clip_path=clip_path, version="current",
        start_frame=-1, end_frame=-1, load_alpha=False,
        path_from="", path_to="",
    )
    assert frame_count == 4
    # float32 EXR should round-trip without loss
    assert torch.allclose(images, original, atol=1e-4)


def test_round_trip_rgba(tmp_path):
    writer = OpenClipWriter()
    original_img = torch.rand(4, 64, 64, 3)
    original_mask = torch.full((4, 64, 64), 0.75)
    (clip_path,) = writer.execute(
        IMAGE=original_img, MASK=original_mask,
        output_dir=str(tmp_path), clip_name="rt_rgba",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="float (32-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )
    reader = OpenClipReader()
    images, masks, *_ = reader.execute(
        clip_path=clip_path, version="current",
        start_frame=-1, end_frame=-1, load_alpha=True,
        path_from="", path_to="",
    )
    assert torch.allclose(images, original_img, atol=1e-4)
    assert torch.allclose(masks, original_mask, atol=1e-4)


def test_write_second_version_adds_to_clip(tmp_path):
    writer = OpenClipWriter()
    images_v1 = torch.zeros(4, 64, 64, 3)
    images_v2 = torch.ones(4, 64, 64, 3)

    (clip_v1,) = writer.execute(
        IMAGE=images_v1, output_dir=str(tmp_path), clip_name="mv",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="float (32-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )
    (clip_v2,) = writer.execute(
        IMAGE=images_v2, output_dir=str(tmp_path), clip_name="mv",
        version_name="v002", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="float (32-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )

    # Both versions must exist in the clip; currentVersion must be v002
    parsed = openclip_xml.parse(clip_v2)
    assert set(parsed.versions.keys()) == {"v001", "v002"}
    assert parsed.current_version == "v002"

    # Round-trip: read v002 back correctly
    reader = OpenClipReader()
    images, *_ = reader.execute(
        clip_path=clip_v2, version="v002",
        start_frame=-1, end_frame=-1, load_alpha=False,
        path_from="", path_to="",
    )
    assert torch.allclose(images, images_v2, atol=1e-4)


def test_write_duplicate_version_raises(tmp_path):
    writer = OpenClipWriter()
    images = torch.rand(4, 64, 64, 3)
    writer.execute(
        IMAGE=images, output_dir=str(tmp_path), clip_name="mv",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )
    with pytest.raises(ValueError, match="already exists"):
        writer.execute(
            IMAGE=images, output_dir=str(tmp_path), clip_name="mv",
            version_name="v001", fps="24", start_frame=1001, frame_padding=4,
            file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
            layout="Standard Flame", publish=False,
        )


def test_write_format_mismatch_raises(tmp_path):
    writer = OpenClipWriter()
    images_hd = torch.rand(4, 64, 64, 3)
    images_4k = torch.rand(4, 128, 128, 3)
    writer.execute(
        IMAGE=images_hd, output_dir=str(tmp_path), clip_name="mv",
        version_name="v001", fps="24", start_frame=1001, frame_padding=4,
        file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
        layout="Standard Flame", publish=False,
    )
    with pytest.raises(ValueError, match="mismatch"):
        writer.execute(
            IMAGE=images_4k, output_dir=str(tmp_path), clip_name="mv",
            version_name="v002", fps="24", start_frame=1001, frame_padding=4,
            file_format="EXR", exr_bit_depth="half (16-bit)", exr_compression="ZIP",
            layout="Standard Flame", publish=False,
        )


# --- EXR format variants ---


def test_exr_half_vs_float_both_readable(tmp_path):
    images = torch.rand(1, 64, 64, 3)
    for bit_depth in ("half (16-bit)", "float (32-bit)"):
        pattern = str(tmp_path / f"{bit_depth[:4]}.%04d.exr")
        image_io.write_sequence(pattern, images, None, 1, "EXR", bit_depth, "ZIP")
        read, _ = image_io.read_sequence(pattern, 1, 1, load_alpha=False)
        assert read.shape == (1, 64, 64, 3)


def test_exr_compression_variants_round_trip(tmp_path):
    images = torch.rand(1, 64, 64, 3)
    for compression in ("ZIP", "PIZ", "DWAB"):
        pattern = str(tmp_path / f"{compression}.%04d.exr")
        image_io.write_sequence(pattern, images, None, 1, "EXR", "half (16-bit)", compression)
        read, _ = image_io.read_sequence(pattern, 1, 1, load_alpha=False)
        assert read.shape == (1, 64, 64, 3)


# --- VersionSelector ---


def test_version_selector_returns_all_versions(v8_multi_clip):
    selector = OpenClipVersionSelector()
    clip_path, selected, current = selector.execute(
        clip_path=str(v8_multi_clip),
        selected_version="v003",
        latest_version="",
    )
    assert selected == "v003"
    assert current == "v002"


def test_version_selector_falls_back_to_current_when_empty(v8_multi_clip):
    selector = OpenClipVersionSelector()
    _, selected, current = selector.execute(
        clip_path=str(v8_multi_clip),
        selected_version="",
        latest_version="",
    )
    assert selected == current == "v002"


def test_version_selector_raises_on_unknown_version(v8_multi_clip):
    selector = OpenClipVersionSelector()
    with pytest.raises(ValueError, match="v999"):
        selector.execute(
            clip_path=str(v8_multi_clip),
            selected_version="v999",
            latest_version="",
        )
