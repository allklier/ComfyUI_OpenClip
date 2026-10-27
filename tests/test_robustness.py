"""Robustness: corrupt / truncated / missing media, malformed .clip XML, inconsistent frames.

The contract being locked in is "fail loudly, and say which file" -- never return a
plausible-looking wrong tensor. Where current behaviour is weaker than that, the test is
an xfail(strict=True) so fixing the library flips it to a failure that forces removing
the marker.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch
from lxml import etree

from ComfyUI_OpenClip.lib import image_io, openclip_xml
from ComfyUI_OpenClip.lib.openclip_xml import ClipSpan, ClipVersion
from ComfyUI_OpenClip.nodes.aov_reader import OpenClipAOVReader
from ComfyUI_OpenClip.nodes.reader import OpenClipReader
from ComfyUI_OpenClip.nodes.writer import OpenClipWriter

# --- helpers ---


def _seq(tmp_path: Path, ext: str = "exr", n: int = 3, size: int = 16):
    pattern = str(tmp_path / f"f.%04d.{ext}")
    image_io.write_sequence(pattern, torch.rand(n, size, size, 3), None, 1, ext.upper())
    return pattern


def _reader_kwargs(clip_path: str, **over):
    kw = dict(clip_path=clip_path, version="current", start_frame=-1, end_frame=-1, path_from="", path_to="")
    kw.update(over)
    return kw


def _clip_for(tmp_path: Path, n: int = 3, size: int = 16):
    """A real clip + frames on disk; returns (clip_file, frame_dir)."""
    media = tmp_path / "pkg" / "versions" / "v001"
    media.mkdir(parents=True)
    image_io.write_sequence(str(media / "c.%04d.exr"), torch.rand(n, size, size, 3), None, 1001, "EXR")
    versions = {"v001": ClipVersion(uid="v001", name="v001", spans=[
        ClipSpan(path="versions/v001/c.%04d.exr", start_frame=1001, duration=n)])}
    clip_file = tmp_path / "pkg" / "c.clip"
    clip_file.write_bytes(openclip_xml.generate("c", versions, "v001"))
    return clip_file, media


# --- corrupt EXR / PNG frames -------------------------------------------------


@pytest.mark.parametrize("ext", ["exr", "png"])
@pytest.mark.parametrize("label, payload", [
    ("zero_byte", lambda good: b""),
    ("garbage", lambda good: b"this is not an image file" * 8),
    ("header_only_stub", lambda good: good[:12]),
])
def test_unopenable_frame_mid_sequence_raises_naming_the_file(tmp_path, ext, label, payload):
    pattern = _seq(tmp_path, ext)
    bad = Path(pattern % 2)
    bad.write_bytes(payload(bad.read_bytes()))
    with pytest.raises(FileNotFoundError, match=bad.name):
        image_io.read_sequence(pattern, 1, 3, load_alpha=True)


@pytest.mark.parametrize("ext", ["exr", "png"])
def test_truncated_body_raises_instead_of_returning_partial_pixels(tmp_path, ext):
    pattern = _seq(tmp_path, ext, size=64)
    bad = Path(pattern % 2)
    data = bad.read_bytes()
    bad.write_bytes(data[: len(data) // 2])
    with pytest.raises(RuntimeError, match=bad.name):
        image_io.read_sequence(pattern, 1, 3, load_alpha=True)


def test_corrupt_frame_outside_requested_range_is_never_touched(tmp_path):
    pattern = _seq(tmp_path)
    Path(pattern % 3).write_bytes(b"")
    images, _m, _meta = image_io.read_sequence(pattern, 1, 2, load_alpha=True)
    assert images.shape[0] == 2


def test_corrupt_frame_fails_probe_and_named_part_reads(tmp_path):
    pattern = _seq(tmp_path)
    Path(pattern % 2).write_bytes(b"")
    with pytest.raises(FileNotFoundError):
        image_io.probe_aov_names(pattern, 2)
    with pytest.raises(FileNotFoundError):
        image_io.read_named_part(pattern, 1, 3, "depth")


def test_missing_frame_mid_sequence_raises_naming_the_frame(tmp_path):
    pattern = _seq(tmp_path)
    Path(pattern % 2).unlink()
    with pytest.raises(FileNotFoundError, match=r"f\.0002\.exr"):
        image_io.read_sequence(pattern, 1, 3, load_alpha=True)


def test_reader_node_surfaces_a_corrupt_frame(tmp_path):
    clip_file, media = _clip_for(tmp_path)
    (media / "c.1002.exr").write_bytes(b"")
    with pytest.raises(FileNotFoundError, match=r"c\.1002\.exr"):
        OpenClipReader().execute(**_reader_kwargs(str(clip_file)))


def test_reader_is_changed_survives_a_corrupt_first_frame(tmp_path):
    # IS_CHANGED only stats files, so a corrupt frame must not make it raise (a raise would
    # become NaN and defeat caching for the whole downstream graph).
    clip_file, media = _clip_for(tmp_path)
    (media / "c.1001.exr").write_bytes(b"")
    assert isinstance(OpenClipReader.IS_CHANGED(**_reader_kwargs(str(clip_file))), str)


def test_aov_reader_node_surfaces_a_corrupt_frame(tmp_path):
    pattern = _seq(tmp_path)
    Path(pattern % 2).write_bytes(b"")
    handle = {"abs_pattern": pattern, "start_frame": 1, "end_frame": 3, "clip_path": pattern}
    with pytest.raises(FileNotFoundError, match=r"f\.0002\.exr"):
        OpenClipAOVReader().execute(handle, "depth")


# --- inconsistent frames within one sequence ----------------------------------


def test_mismatched_resolution_mid_sequence_raises(tmp_path):
    pattern = str(tmp_path / "g.%04d.exr")
    for frame, size in ((1, 16), (2, 16), (3, 32)):
        image_io.write_sequence(pattern, torch.rand(1, size, size, 3), None, frame, "EXR")
    with pytest.raises(RuntimeError):
        image_io.read_sequence(pattern, 1, 3, load_alpha=True)


def test_mismatched_resolution_error_names_the_offending_frame(tmp_path):
    pattern = str(tmp_path / "g.%04d.exr")
    for frame, size in ((1, 16), (2, 16), (3, 32)):
        image_io.write_sequence(pattern, torch.rand(1, size, size, 3), None, frame, "EXR")
    with pytest.raises(RuntimeError, match=r"g\.0003\.exr"):
        image_io.read_sequence(pattern, 1, 3, load_alpha=True)


def test_alpha_present_in_only_some_frames_is_opaque_where_absent(tmp_path):
    pattern = str(tmp_path / "h.%04d.exr")
    mask = torch.full((1, 16, 16), 0.25)
    image_io.write_sequence(pattern, torch.rand(1, 16, 16, 3), mask, 1, "EXR")
    image_io.write_sequence(pattern, torch.rand(1, 16, 16, 3), None, 2, "EXR")
    _img, masks, _meta = image_io.read_sequence(pattern, 1, 2, load_alpha=True)
    assert torch.allclose(masks[0], torch.full((16, 16), 0.25))
    assert torch.all(masks[1] == 1.0)


def test_channels_are_addressed_by_name_not_position(tmp_path):
    import OpenImageIO as oiio

    px = np.zeros((8, 8, 4), np.float32)
    px[..., 0], px[..., 1], px[..., 2], px[..., 3] = 0.9, 0.3, 0.2, 0.1  # written as A,B,G,R
    spec = oiio.ImageSpec(8, 8, 4, oiio.FLOAT)
    spec.channelnames = ("A", "B", "G", "R")
    path = str(tmp_path / "odd.0001.exr")
    out = oiio.ImageOutput.create(path)
    assert out.open(path, spec) and out.write_image(px)
    out.close()
    img, mask, _ = image_io.read_sequence(str(tmp_path / "odd.%04d.exr"), 1, 1, load_alpha=True)
    assert img[0, 0, 0].tolist() == pytest.approx([0.1, 0.2, 0.3])
    assert float(mask[0, 0, 0]) == pytest.approx(0.9)


def test_two_channel_exr_raises_with_found_channels_listed(tmp_path):
    import OpenImageIO as oiio

    spec = oiio.ImageSpec(8, 8, 2, oiio.FLOAT)
    spec.channelnames = ("R", "G")
    path = str(tmp_path / "two.exr")
    out = oiio.ImageOutput.create(path)
    assert out.open(path, spec) and out.write_image(np.zeros((8, 8, 2), np.float32))
    out.close()
    with pytest.raises(RuntimeError, match=r"missing required R/G/B.*\('R', 'G'\)"):
        image_io.read_sequence(path, 1, 1, load_alpha=True)


def test_layer_missing_from_one_frame_raises(blender_multilayer_fixtures, tmp_path):
    import shutil

    for f in (1, 2, 3):
        shutil.copy(blender_multilayer_fixtures / f"render.{f:04d}.exr", tmp_path / f"render.{f:04d}.exr")
    # frame 2 replaced by a plain RGB file: no dotted layers at all
    (tmp_path / "render.0002.exr").unlink()
    image_io.write_sequence(str(tmp_path / "render.%04d.exr"), torch.rand(1, 64, 64, 3), None, 2, "EXR")
    with pytest.raises(RuntimeError, match=r"CamelMatte\.Combined\.R"):
        image_io.read_sequence(str(tmp_path / "render.%04d.exr"), 1, 3, load_alpha=True, layer="CamelMatte.Combined")


def test_named_part_zero_filled_at_the_frames_own_size_when_frame_lacks_it(tmp_path, write_multipart_exr, aov_pixels):
    pattern = str(tmp_path / "m.%04d.exr")
    parts = [(aov_pixels["beauty"], ("R", "G", "B", "A"), "beauty"), (aov_pixels["normal"], ("R", "G", "B"), "normals")]
    write_multipart_exr(tmp_path / "m.0001.exr", parts)
    write_multipart_exr(tmp_path / "m.0002.exr", parts[:1])  # lacks 'normals'
    image, matte = image_io.read_named_part(pattern, 1, 2, "normals")
    assert image.shape == (2, 64, 64, 3) and matte.shape == (2, 64, 64)
    assert torch.all(image[1] == 0) and torch.all(matte[1] == 0)


# --- malformed .clip XML ------------------------------------------------------


@pytest.mark.parametrize("label, text", [
    ("empty_file", ""),
    ("not_xml", "hello, I am not xml"),
    ("binary", b"\x00\xff\x00"),
])
def test_unparseable_clip_raises_value_error_naming_the_file(tmp_path, label, text):
    # A ValueError naming the file (chained from lxml's XMLSyntaxError), not a raw lxml traceback.
    p = tmp_path / "bad.clip"
    p.write_bytes(text if isinstance(text, bytes) else text.encode())
    with pytest.raises(ValueError, match=r"bad\.clip is not valid XML"):
        openclip_xml.parse(str(p))
    with pytest.raises(ValueError, match=r"bad\.clip is not valid XML"):
        openclip_xml.read_format(str(p))


def test_truncated_clip_raises_value_error(tmp_path, v8_single_clip):
    p = tmp_path / "trunc.clip"
    text = v8_single_clip.read_text()
    p.write_text(text[: len(text) // 2])
    with pytest.raises(ValueError, match="not valid XML") as exc:
        openclip_xml.parse(str(p))
    assert isinstance(exc.value.__cause__, etree.XMLSyntaxError)


@pytest.mark.parametrize("xml, match", [
    # No root <name> is fine (falls back to the file stem), so these now fail one level deeper.
    ("<foo/>", "Missing <tracks>"),
    ("<clip version='8'><tracks/></clip>", "Missing <track>"),
    ("<clip version='8'><name>x</name></clip>", "Missing <tracks>"),
    ("<clip version='8'><name>x</name><tracks/></clip>", "Missing <track>"),
    ("<clip version='8'><name>x</name><tracks><track/></tracks></clip>", "Missing <feeds>"),
    ("<clip version='8'><name>x</name><tracks><track><feeds><feed/></feeds></track></tracks></clip>", "missing 'vuid'"),
    ("<clip version='8'><name>x</name><tracks><track><feeds><feed vuid='v1'/></feeds></track></tracks></clip>", "Missing <spans>"),
    ("<clip version='8'><name>x</name><tracks><track><feeds><feed vuid='v1'><spans><span/></spans></feed></feeds></track></tracks></clip>", "missing <path>"),
    ("<clip version='8'><name>x</name><tracks><track><feeds><feed vuid='v1'><spans><span><path>a.[0010-0001].exr</path></span></spans></feed></feeds></track></tracks></clip>", "range end"),
])
def test_structurally_invalid_clip_raises_value_error(tmp_path, xml, match):
    p = tmp_path / "bad.clip"
    p.write_text(xml)
    with pytest.raises(ValueError, match=match):
        openclip_xml.parse(str(p))


def test_read_format_without_storage_format_returns_unknowns(tmp_path):
    # Autodesk's example clips often have no <storageFormat>; unknown fields stay 0/"" so the
    # writer's format-mismatch check skips them rather than the read failing.
    p = tmp_path / "nofmt.clip"
    p.write_text("<clip version='8'><name>x</name></clip>")
    fmt = openclip_xml.read_format(str(p))
    assert (fmt.width, fmt.height, fmt.file_format, fmt.fps) == (0, 0, "", "24")


def test_clip_with_no_feeds_has_no_versions_and_current_cannot_resolve(tmp_path):
    p = tmp_path / "nofeeds.clip"
    p.write_text("<clip version='8'><name>x</name><tracks><track><feeds/></track></tracks></clip>")
    parsed = openclip_xml.parse(str(p))
    assert parsed.versions == {}
    with pytest.raises(ValueError, match="not found"):
        openclip_xml.resolve_version(parsed, "current")


def test_missing_clip_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        openclip_xml.parse(str(tmp_path / "nope.clip"))
    with pytest.raises(FileNotFoundError):
        openclip_xml.read_format(str(tmp_path / "nope.clip"))


def test_unknown_edit_rate_label_silently_falls_back_to_24(tmp_path):
    # Characterisation, not endorsement: an unrecognised <editRate> (e.g. 12 fps) is read as 24.
    p = tmp_path / "rate.clip"
    p.write_text(
        "<clip version='8'><name>x</name><tracks><track><editRate type='rate'>12</editRate>"
        "<feeds><feed vuid='v1'><storageFormat type='format'/></feed></feeds></track></tracks></clip>"
    )
    assert openclip_xml.read_format(str(p)).fps == "24"


# --- Reader node on broken packages ------------------------------------------


def test_reader_empty_clip_path_raises(tmp_path):
    with pytest.raises(ValueError, match="clip_path is required"):
        OpenClipReader().execute(**_reader_kwargs("   "))


def test_reader_nonexistent_clip_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        OpenClipReader().execute(**_reader_kwargs(str(tmp_path / "nope.clip")))


def test_reader_unknown_version_lists_available(tmp_path):
    clip_file, _ = _clip_for(tmp_path)
    with pytest.raises(ValueError, match=r"v999.*Available.*v001"):
        OpenClipReader().execute(**_reader_kwargs(str(clip_file), version="v999"))


def test_reader_clip_with_media_deleted_raises_file_not_found(tmp_path):
    clip_file, media = _clip_for(tmp_path)
    for f in media.glob("*.exr"):
        f.unlink()
    with pytest.raises(FileNotFoundError):
        OpenClipReader().execute(**_reader_kwargs(str(clip_file)))


def test_reader_start_after_end_raises(tmp_path):
    clip_file, _ = _clip_for(tmp_path)
    with pytest.raises(ValueError, match="start_frame"):
        OpenClipReader().execute(**_reader_kwargs(str(clip_file), start_frame=1003, end_frame=1001))


def test_reader_range_beyond_the_media_raises_naming_the_first_missing_frame(tmp_path):
    clip_file, _ = _clip_for(tmp_path, n=3)  # frames 1001-1003
    with pytest.raises(FileNotFoundError, match=r"c\.1004\.exr"):
        OpenClipReader().execute(**_reader_kwargs(str(clip_file), end_frame=1010))


def test_reader_zero_span_version_raises(tmp_path):
    p = tmp_path / "nospan.clip"
    p.write_text(
        "<clip version='8'><name>x</name><tracks><track><feeds currentVersion='v1'>"
        "<feed vuid='v1'><spans/></feed></feeds></track></tracks></clip>"
    )
    with pytest.raises(ValueError, match="no spans"):
        OpenClipReader().execute(**_reader_kwargs(str(p)))


# --- write-side failure modes --------------------------------------------------


def test_write_to_unwritable_location_raises_runtime_error():
    with pytest.raises(RuntimeError, match="failed to open for writing"):
        image_io.write_sequence("/proc/nope/f.%04d.exr", torch.rand(1, 4, 4, 3), None, 1, "EXR")


def test_write_rejects_unknown_bit_depth_and_compression(tmp_path):
    pattern = str(tmp_path / "w.%04d.exr")
    with pytest.raises(ValueError, match="exr_bit_depth"):
        image_io.write_sequence(pattern, torch.rand(1, 4, 4, 3), None, 1, "EXR", exr_bit_depth="bogus")
    with pytest.raises(ValueError, match="exr_compression"):
        image_io.write_sequence(pattern, torch.rand(1, 4, 4, 3), None, 1, "EXR", exr_compression="bogus")
    assert list(tmp_path.iterdir()) == []


def test_write_mask_shorter_than_images_raises(tmp_path):
    with pytest.raises((IndexError, ValueError)):
        image_io.write_sequence(str(tmp_path / "m.%04d.exr"), torch.rand(3, 4, 4, 3), torch.rand(2, 4, 4), 1, "EXR")


def test_write_static_path_without_frame_token(tmp_path):
    image_io.write_sequence(str(tmp_path / "still.exr"), torch.rand(1, 4, 4, 3), None, 1, "EXR")
    assert (tmp_path / "still.exr").exists()


def _writer_kwargs(tmp_path, **over):
    kw = dict(clip_path=str(tmp_path), clip_name="s", clip_filename="$(path)/$(clip_name).clip",
              version_name="next", fps=24.0, start_frame=1, frame_padding=4, file_format="EXR",
              exr_bit_depth="half (16-bit)", exr_compression="ZIP")
    kw.update(over)
    return kw


def test_writer_unsupported_fps_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="does not match any supported rate"):
        OpenClipWriter().execute(IMAGE=torch.rand(2, 8, 8, 3), **_writer_kwargs(tmp_path, fps=26.5))


def test_writer_unsupported_fps_leaves_nothing_on_disk(tmp_path):
    with pytest.raises(ValueError):
        OpenClipWriter().execute(IMAGE=torch.rand(2, 8, 8, 3), **_writer_kwargs(tmp_path, fps=26.5))
    assert list(tmp_path.rglob("*.exr")) == []


def test_writer_resolution_mismatch_against_existing_clip_raises(tmp_path):
    OpenClipWriter().execute(IMAGE=torch.rand(1, 16, 16, 3), **_writer_kwargs(tmp_path))
    with pytest.raises(ValueError, match=r"Format mismatch.*width"):
        OpenClipWriter().execute(IMAGE=torch.rand(1, 32, 32, 3), **_writer_kwargs(tmp_path))


def test_writer_file_format_mismatch_against_existing_clip_raises(tmp_path):
    OpenClipWriter().execute(IMAGE=torch.rand(1, 16, 16, 3), **_writer_kwargs(tmp_path))
    with pytest.raises(ValueError, match="file_format"):
        OpenClipWriter().execute(IMAGE=torch.rand(1, 16, 16, 3), **_writer_kwargs(tmp_path, file_format="PNG"))


def test_writer_corrupt_existing_clip_raises_rather_than_clobbering(tmp_path):
    OpenClipWriter().execute(IMAGE=torch.rand(1, 8, 8, 3), **_writer_kwargs(tmp_path))
    clip = tmp_path / "s.clip"
    clip.write_text("<<<not xml")
    with pytest.raises(ValueError, match="not valid XML"):
        OpenClipWriter().execute(IMAGE=torch.rand(1, 8, 8, 3), **_writer_kwargs(tmp_path))
    assert clip.read_text() == "<<<not xml"


def test_writer_format_mismatch_leaves_no_new_frames_behind(tmp_path):
    OpenClipWriter().execute(IMAGE=torch.rand(1, 16, 16, 3), **_writer_kwargs(tmp_path))
    before = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    with pytest.raises(ValueError, match="Format mismatch"):
        OpenClipWriter().execute(IMAGE=torch.rand(1, 32, 32, 3), **_writer_kwargs(tmp_path))
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == before
    assert not (tmp_path / "s" / "v002").exists()


def test_write_multiple_frames_to_token_less_path_raises(tmp_path):
    with pytest.raises(ValueError, match="no frame token"):
        image_io.write_sequence(str(tmp_path / "still.exr"), torch.rand(2, 4, 4, 3), None, 1, "EXR")
