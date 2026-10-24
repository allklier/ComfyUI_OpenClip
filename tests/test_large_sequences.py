"""Large / long sequences: frame caps, numbering edge cases, big fp32 frames, ordering.

Default-run tests stay small enough for the suite to remain fast; the genuinely heavy
ones carry @pytest.mark.slow (registered in conftest.py) and can be skipped with
`pytest -m "not slow"`.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import torch

from ComfyUI_OpenClip.lib import image_io, openclip_xml
from ComfyUI_OpenClip.lib.openclip_xml import ClipSpan, ClipVersion
from ComfyUI_OpenClip.nodes.aov_reader import OpenClipAOVReader
from ComfyUI_OpenClip.nodes.aov_writer import OpenClipAOVWriter
from ComfyUI_OpenClip.nodes.exr_sequence_reader import ExrSequenceReader
from ComfyUI_OpenClip.nodes.reader import OpenClipReader
from ComfyUI_OpenClip.nodes.writer import OpenClipWriter


def _frame_coded_images(n: int, h: int = 8, w: int = 8) -> torch.Tensor:
    """Frame i is filled with the constant i/1000 (exact in fp32, easy to order-check)."""
    return torch.stack([torch.full((h, w, 3), i / 1000.0) for i in range(n)])


# --- MAX_FRAMES cap (no files needed: the check precedes any disk access) --------


def test_read_sequence_over_max_frames_raises_before_touching_disk():
    with pytest.raises(RuntimeError, match="MAX_FRAMES"):
        image_io.read_sequence("/nonexistent/f.%04d.exr", 1, image_io.MAX_FRAMES + 1, load_alpha=True)


def test_read_sequence_at_exactly_max_frames_passes_the_cap():
    # Exactly MAX_FRAMES is allowed: it proceeds to disk and fails there, not at the cap.
    with pytest.raises(FileNotFoundError):
        image_io.read_sequence("/nonexistent/f.%04d.exr", 1, image_io.MAX_FRAMES, load_alpha=True)


def test_read_named_part_over_max_frames_raises():
    with pytest.raises(RuntimeError, match="MAX_FRAMES"):
        image_io.read_named_part("/nonexistent/f.%04d.exr", 1, image_io.MAX_FRAMES + 1, "depth")


def test_write_sequence_over_max_frames_raises_before_writing(tmp_path):
    images = torch.zeros(image_io.MAX_FRAMES + 1, 1, 1, 3)
    with pytest.raises(RuntimeError, match="MAX_FRAMES"):
        image_io.write_sequence(str(tmp_path / "f.%04d.exr"), images, None, 1, "EXR")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reader", [image_io.read_sequence])
def test_empty_or_inverted_range_raises(reader):
    with pytest.raises(ValueError, match="start_frame"):
        reader("/nonexistent/f.%04d.exr", 5, 4, True)


def test_clip_xml_version_and_span_caps(tmp_path):
    versions = {f"v{i}": ClipVersion(uid=f"v{i}", name=f"v{i}", spans=[]) for i in range(openclip_xml.MAX_VERSIONS + 1)}
    with pytest.raises(RuntimeError, match="MAX_VERSIONS"):
        openclip_xml.generate("x", versions, "v0")


def test_clip_xml_span_cap_on_parse(tmp_path):
    spans = "".join(f"<span><path>a.[0001-0002].exr</path></span>" for _ in range(openclip_xml.MAX_SPANS + 1))
    p = tmp_path / "many.clip"
    p.write_text(
        "<clip version='8'><name>x</name><tracks><track><feeds currentVersion='v1'>"
        f"<feed vuid='v1'><spans>{spans}</spans></feed></feeds></track></tracks></clip>"
    )
    with pytest.raises(RuntimeError, match="MAX_SPANS"):
        openclip_xml.parse(str(p))


# --- long sequences (many tiny frames) ----------------------------------------------


def test_long_sequence_round_trips_in_order_with_exact_values(tmp_path):
    n = 240
    pattern = str(tmp_path / "long.%04d.exr")
    image_io.write_sequence(pattern, _frame_coded_images(n), None, 1001, "EXR", "float (32-bit)", "ZIP")
    images, masks, _ = image_io.read_sequence(pattern, 1001, 1001 + n - 1, load_alpha=True)
    assert images.shape == (n, 8, 8, 3) and masks.shape == (n, 8, 8)
    assert images.dtype == torch.float32
    assert torch.equal(images[:, 0, 0, 0], torch.arange(n, dtype=torch.float32) / 1000.0)
    assert len(list(tmp_path.glob("long.*.exr"))) == n


def test_frame_numbers_crossing_the_padding_width_stay_ordered(tmp_path):
    # %04d overflows at 10000: names grow to five digits rather than wrapping or colliding.
    pattern = str(tmp_path / "pad.%04d.exr")
    image_io.write_sequence(pattern, _frame_coded_images(5), None, 9998, "EXR", "float (32-bit)", "ZIP")
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "pad.10000.exr", "pad.10001.exr", "pad.10002.exr", "pad.9998.exr", "pad.9999.exr"]
    images, _m, _meta = image_io.read_sequence(pattern, 9998, 10002, load_alpha=True)
    assert torch.equal(images[:, 0, 0, 0], torch.arange(5, dtype=torch.float32) / 1000.0)


def test_reader_node_frame_cap_on_a_long_clip(tmp_path):
    n = 150
    media = tmp_path / "pkg" / "versions" / "v001"
    media.mkdir(parents=True)
    image_io.write_sequence(str(media / "c.%04d.exr"), _frame_coded_images(n), None, 1001, "EXR", "float (32-bit)", "ZIP")
    versions = {"v001": ClipVersion(uid="v001", name="v001", spans=[
        ClipSpan(path="versions/v001/c.%04d.exr", start_frame=1001, duration=n)])}
    clip = tmp_path / "pkg" / "c.clip"
    clip.write_bytes(openclip_xml.generate("c", versions, "v001"))

    common = dict(clip_path=str(clip), version="current", path_from="", path_to="")
    full = OpenClipReader().execute(start_frame=-1, end_frame=-1, **common)["result"]
    assert full[2] == n
    capped = OpenClipReader().execute(start_frame=1100, end_frame=-1, frame_cap=10, **common)["result"]
    assert capped[2] == 10 and capped[7] == 1100
    assert torch.equal(capped[0][:, 0, 0, 0], full[0][99:109, 0, 0, 0])


def test_exr_sequence_reader_long_sequence_with_gaps_discovery(tmp_path):
    # start/end -1 spans first..last frame on disk; a gap in between is a loud failure.
    pattern = str(tmp_path / "gap.%04d.exr")
    image_io.write_sequence(pattern, _frame_coded_images(60), None, 1, "EXR", "float (32-bit)", "ZIP")
    (tmp_path / "gap.0030.exr").unlink()
    kwargs = dict(sequence_path=str(tmp_path / "gap.####.exr"), layer="", start_frame=-1, end_frame=-1,
                  frame_cap=-1, fps=24.0, colour_space="")
    with pytest.raises(FileNotFoundError, match=r"gap\.0030\.exr"):
        ExrSequenceReader().execute(**kwargs)
    ok = ExrSequenceReader().execute(**{**kwargs, "end_frame": 29})["result"]
    assert ok[2] == 29


def test_aov_reader_over_a_long_multipart_sequence(tmp_path, write_multipart_exr):
    n = 60
    from .conftest import IMAGE_SIZE
    for i in range(n):
        beauty = np.full((IMAGE_SIZE, IMAGE_SIZE, 4), 0.5, np.float32)
        depth = np.full((IMAGE_SIZE, IMAGE_SIZE, 1), float(i), np.float32)
        write_multipart_exr(tmp_path / f"a.{i + 1:04d}.exr", [(beauty, ("R", "G", "B", "A"), "beauty"), (depth, ("Z",), "depth")])
    handle = {"abs_pattern": str(tmp_path / "a.%04d.exr"), "start_frame": 1, "end_frame": n, "clip_path": ""}
    image, matte = OpenClipAOVReader().execute(handle, "depth")
    assert image is None and matte.shape == (n, IMAGE_SIZE, IMAGE_SIZE)
    assert torch.equal(matte[:, 0, 0], torch.arange(n, dtype=torch.float32))


def test_writer_next_version_many_times_stays_sequential(tmp_path):
    kw = dict(clip_path=str(tmp_path), clip_name="s", clip_filename="$(path)/$(clip_name).clip", version_name="next",
              fps=24.0, start_frame=1, frame_padding=4, file_format="EXR", exr_bit_depth="half (16-bit)",
              exr_compression="ZIP")
    for _ in range(12):
        OpenClipWriter().execute(IMAGE=torch.rand(1, 8, 8, 3), **kw)
    parsed = openclip_xml.parse(str(tmp_path / "s" / "s.clip"))
    assert list(parsed.versions) == [f"v{i:03d}" for i in range(1, 13)]
    assert parsed.current_version == "v012"


# --- big frames -----------------------------------------------------------------------


def test_fp32_frames_with_alpha_round_trip_bit_exact_at_moderate_size(tmp_path):
    n, h, w = 3, 540, 960
    rng = np.random.default_rng(1)
    images = torch.from_numpy(rng.standard_normal((n, h, w, 3)).astype(np.float32) * 10)  # beyond 0..1, negative
    masks = torch.from_numpy(rng.random((n, h, w)).astype(np.float32))
    pattern = str(tmp_path / "big.%04d.exr")
    image_io.write_sequence(pattern, images, masks, 1, "EXR", "float (32-bit)", "ZIP")
    out_i, out_m, _ = image_io.read_sequence(pattern, 1, n, load_alpha=True)
    assert torch.equal(out_i, images) and torch.equal(out_m, masks)


@pytest.mark.parametrize("compression", ["ZIP", "PIZ", "DWAB"])
def test_all_compressions_survive_a_non_tile_aligned_odd_size(tmp_path, compression):
    h, w = 301, 517  # not multiples of any EXR block size
    # smooth content: DWAB is lossy and noisy random data is its worst case
    ramp = torch.linspace(0, 1, w).view(1, 1, w, 1).expand(2, h, w, 3).contiguous()
    images = ramp
    pattern = str(tmp_path / "odd.%04d.exr")
    image_io.write_sequence(pattern, images, None, 1, "EXR", "half (16-bit)", compression)
    out, _m, _meta = image_io.read_sequence(pattern, 1, 2, load_alpha=False)
    assert out.shape == (2, h, w, 3)
    tol = 2e-2 if compression == "DWAB" else 1e-3  # DWAB is lossy
    assert torch.allclose(out, images, atol=tol)


@pytest.mark.slow
def test_uhd_fp32_multi_frame_round_trip_with_aov_and_timing(tmp_path):
    n, h, w = 3, 2160, 3840
    rng = np.random.default_rng(2)
    images = torch.from_numpy(rng.random((n, h, w, 3), dtype=np.float32))
    depth = torch.from_numpy(rng.random((n, h, w), dtype=np.float32) * 100)
    chain = OpenClipAOVWriter().execute(name="depth", matte=depth)[0]
    t0 = time.perf_counter()
    result = OpenClipWriter().execute(
        IMAGE=images, clip_path=str(tmp_path), clip_name="uhd", clip_filename="$(path)/$(clip_name).clip",
        version_name="next", fps=24.0, start_frame=1001, frame_padding=4, file_format="EXR",
        exr_bit_depth="float (32-bit)", exr_compression="ZIP", aov_chain=chain)
    out = OpenClipReader().execute(clip_path=result[0], version="current", start_frame=-1, end_frame=-1,
                                   path_from="", path_to="")["result"]
    elapsed = time.perf_counter() - t0
    assert out[2:5] == (n, w, h)
    assert torch.equal(out[0], images)
    _img, matte = OpenClipAOVReader().execute(out[11], "depth")
    assert torch.equal(matte, depth)
    assert elapsed < 120, f"UHD fp32 3-frame round trip took {elapsed:.1f}s"
