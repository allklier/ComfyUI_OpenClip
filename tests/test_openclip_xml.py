from __future__ import annotations

import pytest
from lxml import etree

from ComfyUI_OpenClip.lib import openclip_xml
from ComfyUI_OpenClip.lib.openclip_xml import ClipSpan, ClipVersion, ParsedClip


# --- parse tests ---


def test_parse_v8_single_version(v8_single_clip):
    clip = openclip_xml.parse(str(v8_single_clip))
    assert clip.schema_version == "8"
    assert clip.clip_name == "test_clip"
    assert clip.current_version == "v001"
    assert list(clip.versions.keys()) == ["v001"]
    span = clip.versions["v001"].spans[0]
    assert span.path == "media/test_clip.%06d.exr"  # 6-digit padding from [001001-001004]
    assert span.start_frame == 1001
    assert span.duration == 4


def test_parse_v8_multi_version(v8_multi_clip):
    clip = openclip_xml.parse(str(v8_multi_clip))
    assert clip.schema_version == "8"
    assert clip.current_version == "v002"
    assert set(clip.versions.keys()) == {"v001", "v002", "v003"}


def test_parse_v9_single_version(v9_single_clip):
    clip = openclip_xml.parse(str(v9_single_clip))
    assert clip.schema_version == "9"
    assert clip.clip_name == "test_clip_v9"
    assert clip.current_version == "v001"
    assert "v001" in clip.versions
    span = clip.versions["v001"].spans[0]
    assert span.start_frame == 1001
    assert span.duration == 4


def test_parse_static_image_span(tmp_path):
    # Flame writes a single still (no sequence) with encoding="file" and no
    # [NNNN-NNNN] range in the path — confirmed from a real exported clip.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<clip type="clip" version="7">
  <name type="string">still_shot</name>
  <tracks>
    <track uid="abc">
      <feeds currentVersion="v0">
        <feed vuid="v0" uid="def">
          <storageFormat type="format"><type>video</type></storageFormat>
          <spans>
            <span>
              <path encoding="file">media/still_shot.png</path>
            </span>
          </spans>
        </feed>
      </feeds>
    </track>
  </tracks>
  <versions currentVersion="v0">
    <version uid="v0"><name>v0</name></version>
  </versions>
</clip>
"""
    clip_file = tmp_path / "still_shot.clip"
    clip_file.write_text(xml)
    parsed = openclip_xml.parse(str(clip_file))
    span = parsed.versions["v0"].spans[0]
    assert span.path == "media/still_shot.png"
    assert span.start_frame == 1
    assert span.duration == 1


def test_parse_missing_file():
    with pytest.raises(FileNotFoundError):
        openclip_xml.parse("/nonexistent/path/clip.clip")


def test_parse_range_path_basic():
    path, start, dur = openclip_xml._parse_range_path(
        "files/shot.[000001-000100].exr", "v001"
    )
    assert path == "files/shot.%06d.exr"
    assert start == 1
    assert dur == 100


def test_parse_range_path_nonzero_start():
    path, start, dur = openclip_xml._parse_range_path(
        "shot.[001001-001004].exr", "v001"
    )
    assert path == "shot.%06d.exr"
    assert start == 1001
    assert dur == 4


def test_parse_range_path_no_range_raises():
    with pytest.raises(ValueError, match="range notation"):
        openclip_xml._parse_range_path("shot.%04d.exr", "v001")


def test_parse_range_path_inverted_range_raises():
    with pytest.raises(ValueError, match="end.*<.*start"):
        openclip_xml._parse_range_path("shot.[000100-000001].exr", "v001")


# --- resolve_version tests ---


def test_current_version_fallback(v8_multi_clip):
    clip = openclip_xml.parse(str(v8_multi_clip))
    version = openclip_xml.resolve_version(clip, "current")
    assert version.uid == "v002"


def test_explicit_version_selection(v8_multi_clip):
    clip = openclip_xml.parse(str(v8_multi_clip))
    version = openclip_xml.resolve_version(clip, "v003")
    assert version.uid == "v003"


def test_missing_version_raises(v8_multi_clip):
    clip = openclip_xml.parse(str(v8_multi_clip))
    with pytest.raises(ValueError, match="v999"):
        openclip_xml.resolve_version(clip, "v999")


# --- next_version tests ---


def test_next_version_no_existing_returns_v001():
    assert openclip_xml.next_version({}) == "v001"


def test_next_version_increments_highest():
    assert openclip_xml.next_version({"v001": None, "v002": None}) == "v003"


def test_next_version_ignores_non_numbered_names():
    assert openclip_xml.next_version({"v001": None, "final": None}) == "v002"


def test_next_version_preserves_padding_width():
    assert openclip_xml.next_version({"v009": None}) == "v010"


# --- generate tests ---


def _make_clip(n_versions: int = 1) -> tuple[str, dict, str]:
    versions = {
        f"v{i:03d}": ClipVersion(
            uid=f"v{i:03d}",
            name=f"v{i:03d}",
            spans=[ClipSpan(path=f"media/v{i:03d}/clip.%04d.exr", start_frame=1001, duration=10)],
        )
        for i in range(1, n_versions + 1)
    }
    return "my_clip", versions, "v001"


def test_generate_v8_produces_valid_xml():
    clip_name, versions, current = _make_clip()
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    root = etree.fromstring(xml_bytes)
    assert root.get("version") == "8"
    assert root.find("name").text == "my_clip"


def test_generate_round_trips_through_parse(tmp_path):
    clip_name, versions, current = _make_clip()
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    clip_file = tmp_path / "my_clip.clip"
    clip_file.write_bytes(xml_bytes)
    parsed = openclip_xml.parse(str(clip_file))
    assert parsed.schema_version == "8"
    assert parsed.clip_name == "my_clip"
    assert parsed.current_version == "v001"
    span = parsed.versions["v001"].spans[0]
    assert span.path == "media/v001/clip.%04d.exr"
    assert span.start_frame == 1001
    assert span.duration == 10


def test_generate_publish_metadata_in_version():
    clip_name, versions, current = _make_clip()
    versions["v001"].publish_path = "versions/v001/my_clip.v001.comfy.json"
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    root = etree.fromstring(xml_bytes)
    # must be inside <versions>/<version>/<userData>, not at root level
    assert root.find("userMetadata") is None
    ver_ud = root.find("versions/version[@uid='v001']/userData")
    assert ver_ud is not None
    comfy = ver_ud.find("comfyWorkflow")
    assert comfy is not None
    assert comfy.get("path") == "versions/v001/my_clip.v001.comfy.json"


def test_generate_no_publish_comfy_element_absent():
    clip_name, versions, current = _make_clip()
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    root = etree.fromstring(xml_bytes)
    assert root.find("userMetadata") is None
    assert root.find(".//comfyWorkflow") is None


def test_generate_publish_round_trips_through_parse(tmp_path):
    clip_name, versions, current = _make_clip()
    versions["v001"].publish_path = "versions/v001/my_clip.v001.comfy.json"
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    clip_file = tmp_path / "my_clip.clip"
    clip_file.write_bytes(xml_bytes)
    parsed = openclip_xml.parse(str(clip_file))
    assert parsed.versions["v001"].publish_path == "versions/v001/my_clip.v001.comfy.json"


def test_generate_colour_space_written():
    from ComfyUI_OpenClip.lib.openclip_xml import ClipFormat
    clip_name, versions, current = _make_clip()
    fmt = ClipFormat(colour_space="ACEScg")
    xml_bytes = openclip_xml.generate(clip_name, versions, current, fmt)
    root = etree.fromstring(xml_bytes)
    cs = root.find(".//colourSpace")
    assert cs is not None
    assert cs.text == "ACEScg"


def test_generate_colour_space_default():
    clip_name, versions, current = _make_clip()
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    root = etree.fromstring(xml_bytes)
    cs = root.find(".//colourSpace")
    assert cs is not None
    assert cs.text == "Rec.1886 Rec.709 - Display"


def test_generate_colour_space_empty_omits_element():
    from ComfyUI_OpenClip.lib.openclip_xml import ClipFormat
    clip_name, versions, current = _make_clip()
    fmt = ClipFormat(colour_space="")
    xml_bytes = openclip_xml.generate(clip_name, versions, current, fmt)
    root = etree.fromstring(xml_bytes)
    assert root.find(".//colourSpace") is None


def test_generate_file_format_and_compression_in_storage_format():
    from ComfyUI_OpenClip.lib.openclip_xml import ClipFormat
    clip_name, versions, current = _make_clip()
    fmt = ClipFormat(file_format="EXR", compression="PIZ")
    xml_bytes = openclip_xml.generate(clip_name, versions, current, fmt)
    root = etree.fromstring(xml_bytes)
    assert root.find(".//fileFormat").text == "exr"
    assert root.find(".//compression").text == "PIZ"


def test_generate_png_omits_compression():
    from ComfyUI_OpenClip.lib.openclip_xml import ClipFormat
    clip_name, versions, current = _make_clip()
    fmt = ClipFormat(file_format="PNG", compression="ZIP")
    xml_bytes = openclip_xml.generate(clip_name, versions, current, fmt)
    root = etree.fromstring(xml_bytes)
    assert root.find(".//fileFormat").text == "png"
    assert root.find(".//compression") is None


def test_generate_start_frame_on_feed_and_duration_in_span():
    clip_name, versions, current = _make_clip()
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    root = etree.fromstring(xml_bytes)
    feed = root.find(".//feed")
    assert feed.find("startFrame").text == "1001"
    span = feed.find(".//span")
    assert span.find("duration").text == "10"
    assert span.find("trackIndex").text == "0"


def test_generate_edit_rate_is_scalar():
    from ComfyUI_OpenClip.lib.openclip_xml import ClipFormat
    clip_name, versions, current = _make_clip()
    fmt = ClipFormat(fps="25")
    xml_bytes = openclip_xml.generate(clip_name, versions, current, fmt)
    root = etree.fromstring(xml_bytes)
    er = root.find(".//editRate")
    assert er.text == "25"
    assert er.find("numerator") is None


# --- fps_label_from_float ---


def test_fps_label_from_float_exact_integer_rate():
    assert openclip_xml.fps_label_from_float(25.0) == "25"


def test_fps_label_from_float_matches_ntsc_decimal_approximation():
    # 23.976 is a decimal approximation of the exact rational 24000/1001;
    # it must still resolve to the "23.976" label, not fail or match "24".
    assert openclip_xml.fps_label_from_float(23.976) == "23.976"


def test_fps_label_from_float_matches_exact_rational():
    assert openclip_xml.fps_label_from_float(24000 / 1001) == "23.976"


def test_fps_label_from_float_out_of_tolerance_raises():
    with pytest.raises(ValueError, match="26.5"):
        openclip_xml.fps_label_from_float(26.5)


def test_generate_version_number_in_userData():
    clip_name, versions, current = _make_clip(n_versions=2)
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    root = etree.fromstring(xml_bytes)
    ver_nodes = root.findall(".//versions/version")
    assert ver_nodes[0].find("userData/versionNumber").text == "1"
    assert ver_nodes[1].find("userData/versionNumber").text == "2"


def test_round_trip_preserves_schema_version(tmp_path):
    clip_name, versions, current = _make_clip()
    xml_bytes = openclip_xml.generate(clip_name, versions, current)
    clip_file = tmp_path / "clip.clip"
    clip_file.write_bytes(xml_bytes)
    parsed = openclip_xml.parse(str(clip_file))
    assert parsed.schema_version == "8"
