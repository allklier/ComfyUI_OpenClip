"""2.0: issue #7 fixes (start timecode, colour lookup, untrimmed names, exact-file append) and
the clip_info interface (Reader/EXR Sequence Reader outputs, Writer auto fields, Clip Info node).

Most tests use a small synthetic Flame-style clip (`_flame_clip`), modelled on a real Flame
2027 export (tests' repro clip testclips/clip_1/alpha_test.clip): schema 9, 24000/1001 edit
rate, range-notation path with 6-digit padding, XML colourSpace, EXRs carrying
autodeskColorSpace and smpte:TimeCode. The test against the real clip skips when the
gitignored testclips/ folder is absent.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import OpenImageIO as oiio
import pytest
import torch
from lxml import etree

from ComfyUI_OpenClip.lib import image_io, openclip_xml, timecode
from ComfyUI_OpenClip.nodes.clip_info import OpenClipClipInfo
from ComfyUI_OpenClip.nodes.exr_sequence_reader import ExrSequenceReader
from ComfyUI_OpenClip.nodes.reader import OpenClipReader
from ComfyUI_OpenClip.nodes.writer import OpenClipWriter

from .conftest import named_result

REPO = Path(__file__).resolve().parent.parent
REAL_CLIP_DIR = REPO / "testclips" / "clip_1"

FLAME_CLIP = """<?xml version="1.0" encoding="UTF-8"?>
<clip type="clip" version="9">
\t<name type="string">{name}</name>
\t<userData type="dict"><appName type="binary">AutodeskÂ® FlameÂ®</appName></userData>
\t<tracks>
\t\t<track uid="t0">
\t\t\t<trackType>video</trackType>
\t\t\t<editRate type="rate"><numerator>24000</numerator><denominator>1001</denominator></editRate>
\t\t\t<feeds currentVersion="v0">
{feeds}
\t\t\t</feeds>
\t\t</track>
\t</tracks>
\t<versions currentVersion="v0">
\t\t<version uid="v0"><name>v0</name></version>
\t</versions>
</clip>
"""

FLAME_FEED = """\t\t\t\t<feed vuid="{vuid}" uid="u{vuid}">
\t\t\t\t\t<storageFormat type="format">
\t\t\t\t\t\t<type>video</type>
\t\t\t\t\t\t<channelsDepth type="uint">16</channelsDepth>
{colour}\t\t\t\t\t\t<height type="uint">8</height>
\t\t\t\t\t\t<nbChannels type="uint">3</nbChannels>
\t\t\t\t\t\t<width type="uint">8</width>
\t\t\t\t\t</storageFormat>
\t\t\t\t\t<sampleRate><numerator>24000</numerator><denominator>1001</denominator></sampleRate>
{tc}\t\t\t\t\t<spans><span><path encoding="pattern">{stem}_{vuid}.[{first:06d}-{last:06d}].exr</path></span></spans>
\t\t\t\t</feed>"""


def _flame_clip(tmp_path, name="woods walk ", ticks=86400, start=1, n=3, xml_colour="Rec.709 video",
                exr_meta=None, with_tc=True, exr_tc=True) -> Path:
    """A Flame-style clip '<name>.clip' (name kept verbatim, spaces included) with one version v0."""
    stem = name.strip().replace(" ", "_")
    exr_meta = {"autodeskColorSpace": "Rec.709 video"} if exr_meta is None else exr_meta
    image_io.write_sequence(str(tmp_path / f"{stem}_v0.%06d.exr"), torch.rand(n, 8, 8, 3), None, start, "EXR",
                            metadata=exr_meta, start_timecode=(ticks, 24, False) if exr_tc else None)
    colour = f"\t\t\t\t\t\t<colourSpace type=\"string\">{xml_colour}</colourSpace>\n" if xml_colour else ""
    tc = (f"\t\t\t\t\t<startTimecode type=\"time\"><rate>24</rate><nbTicks>{ticks}</nbTicks>"
          f"<dropMode>NDF</dropMode></startTimecode>\n") if with_tc else ""
    feed = FLAME_FEED.format(vuid="v0", colour=colour, tc=tc, stem=stem, first=start, last=start + n - 1)
    clip = tmp_path / f"{name}.clip"
    clip.write_text(FLAME_CLIP.format(name=name, feeds=feed), encoding="utf-8")
    return clip


def _read(clip, **over):
    kw = dict(clip_path=str(clip), version="current", start_frame=-1, end_frame=-1, path_from="", path_to="")
    kw.update(over)
    return named_result(OpenClipReader, OpenClipReader().execute(**kw)["result"])


def _write(n=3, image=None, **kw):
    kw.setdefault("file_format", "EXR")
    kw.setdefault("exr_bit_depth", "half (16-bit)")
    kw.setdefault("exr_compression", "ZIP")
    return OpenClipWriter().execute(IMAGE=image if image is not None else torch.rand(n, 8, 8, 3), **kw)[0]


def _feeds(clip):
    return etree.parse(str(clip)).getroot().find("tracks/track/feeds").findall("feed")


def _ticks(feed):
    return int(feed.findtext("startTimecode/nbTicks"))


def _exr_timecode(path) -> str:
    """smpte:TimeCode of one EXR as OIIO prints it (HH:MM:SS:FF)."""
    inp = oiio.ImageInput.open(str(path))
    try:
        value = inp.spec().getattribute("smpte:TimeCode")
    finally:
        inp.close()
    if value is None:
        return ""
    v = value[0]
    d = lambda shift, bits: (v >> shift) & ((1 << bits) - 1)
    return f"{d(28, 2)}{d(24, 4)}:{d(20, 3)}{d(16, 4)}:{d(12, 3)}{d(8, 4)}:{d(4, 2)}{d(0, 4)}"


def _snapshot(root: Path):
    return sorted((str(p), p.stat().st_size, p.read_bytes() if p.suffix == ".clip" else b"") for p in root.rglob("*") if p.is_file())


# --- lib/timecode -----------------------------------------------------------------------------


@pytest.mark.parametrize("ticks, rate, text", [
    (0, 24, "00:00:00:00"),
    (86400, 24, "01:00:00:00"),
    (927893, 24, "10:44:22:05"),  # the real Flame repro clip
    (90000, 25, "01:00:00:00"),
    (108000, 30, "01:00:00:00"),
])
def test_timecode_format_and_parse_non_drop(ticks, rate, text):
    assert timecode.format_tc(ticks, rate) == text
    assert timecode.parse_tc(text, rate) == (ticks, False)


@pytest.mark.parametrize("ticks, rate, text", [
    (1800, 30, "00:01:00;02"),    # frames 00 and 01 of minute 1 are skipped
    (17982, 30, "00:10:00;00"),   # every 10th minute keeps them
    (107892, 30, "01:00:00;00"),
    (3600, 60, "00:01:00;04"),
])
def test_timecode_drop_frame(ticks, rate, text):
    assert timecode.format_tc(ticks, rate, drop=True) == text
    assert timecode.parse_tc(text, rate) == (ticks, True)


def test_timecode_drop_frame_round_trips_every_tick():
    for t in range(0, 40000, 7):
        assert timecode.parse_tc(timecode.format_tc(t, 30, True), 30) == (t, True)


@pytest.mark.parametrize("text, rate", [
    ("1:00:00", 24), ("01:00:00:24", 24), ("01:60:00:00", 24), ("aa:bb:cc:dd", 24),
    ("00:01:00;00", 30),   # skipped label in drop-frame
    ("01:00:00;00", 24),   # drop-frame only exists at 30/60
])
def test_timecode_rejects_bad_text(text, rate):
    with pytest.raises(ValueError):
        timecode.parse_tc(text, rate)


def test_smpte_bcd_matches_flames_own_exr_value():
    # testclips/clip_1/testclip_1.000002.exr (Flame 2027) carries (272900614, 0) = 10:44:22:06.
    assert timecode.smpte_bcd(927894, 24) == (272900614, 0)
    assert timecode.smpte_bcd(1800, 30, drop=True)[0] & (1 << 6)  # SMPTE 12M drop-frame flag
    assert timecode.smpte_bcd(0, 50) is None  # frames field can't hold 40-49


@pytest.mark.parametrize("text, rate", [("01:00:00:00", 24), ("10:44:22:06", 24), ("23:59:59:24", 25),
                                         ("01:00:00;00", 30), ("00:01:00;02", 30), ("00:00:00:29", 30)])
def test_smpte_bcd_decodes_what_it_encodes(text, rate):
    ticks, drop = timecode.parse_tc(text, rate)
    assert timecode.from_smpte_bcd(timecode.smpte_bcd(ticks, rate, drop)[0], rate) == text


def test_smpte_bcd_decode_of_flames_own_exr_value_and_refusals():
    assert timecode.from_smpte_bcd(272900614, 24) == "10:44:22:06"
    assert timecode.from_smpte_bcd(16777216, 24) == "01:00:00:00"  # Autodesk's examples, frame 86400
    with pytest.raises(ValueError):
        timecode.from_smpte_bcd(16777216, 50)  # nothing is read above 30
    with pytest.raises(ValueError):
        timecode.from_smpte_bcd(0x25, 24)  # frame 25 doesn't exist at 24


# --- openclip_xml ---------------------------------------------------------------------------------


def test_parse_reads_feed_start_timecode_and_colour(tmp_path):
    parsed = openclip_xml.parse(str(_flame_clip(tmp_path)))
    v0 = parsed.versions["v0"]
    assert (v0.start_timecode, v0.colour_space) == ("01:00:00:00", "Rec.709 video")


def test_parse_without_start_timecode_is_empty(tmp_path):
    parsed = openclip_xml.parse(str(_flame_clip(tmp_path, with_tc=False, xml_colour="")))
    assert (parsed.versions["v0"].start_timecode, parsed.versions["v0"].colour_space) == ("", "")


def test_parse_keeps_leading_and_trailing_spaces_in_the_clip_name(tmp_path):
    assert openclip_xml.parse(str(_flame_clip(tmp_path, name=" woods walk "))).clip_name == " woods walk "


def test_parse_whitespace_only_name_falls_back_to_the_file_stem(tmp_path):
    clip = _flame_clip(tmp_path, name="x")
    clip.write_text(clip.read_text().replace("<name type=\"string\">x</name>", "<name type=\"string\">  </name>"))
    assert openclip_xml.parse(str(clip)).clip_name == "x"


@pytest.mark.parametrize("tc, ticks, drop", [("", 0, "NDF"), ("01:00:00;00", 107892, "DF")])
def test_generate_writes_the_versions_start_timecode(tc, ticks, drop):
    v = openclip_xml.ClipVersion(uid="v1", name="v1", spans=[openclip_xml.ClipSpan("a.%04d.exr", 1, 1)],
                                 start_timecode=tc)
    root = etree.fromstring(openclip_xml.generate("a", {"v1": v}, "v1", openclip_xml.ClipFormat(fps="29.97")))
    feed = root.find("tracks/track/feeds/feed")
    assert (feed.findtext("startTimecode/rate"), _ticks(feed), feed.findtext("startTimecode/dropMode")) == ("30", ticks, drop)


# --- issue #7 regressions (each fails on 1.1.1) -----------------------------------------------------


def test_issue7_new_clip_from_reader_keeps_the_start_timecode(tmp_path):
    info = _read(_flame_clip(tmp_path))["clip_info"]
    out = _write(clip_info=info, clip_path=str(tmp_path / "out"))
    (feed,) = _feeds(out)
    assert _ticks(feed) == 86400  # was 0 in 1.1.1


def test_issue7_append_to_flame_clip_keeps_the_start_timecode_on_the_new_version(tmp_path):
    clip = _flame_clip(tmp_path)
    out = _write(clip_info=_read(clip)["clip_info"])
    assert Path(out) == clip
    assert [(_f.get("vuid"), _ticks(_f)) for _f in _feeds(clip)] == [("v0", 86400), ("v1", 86400)]


def test_issue7_append_without_clip_info_takes_the_existing_clips_timecode(tmp_path):
    clip = _flame_clip(tmp_path, name="shot")
    _write(clip_path=str(tmp_path), clip_name="shot", fps=23.976)
    assert [_ticks(f) for f in _feeds(clip)] == [86400, 86400]


def test_issue7_written_exrs_carry_a_per_frame_timecode_counting_from_the_start(tmp_path):
    info = _read(_flame_clip(tmp_path))["clip_info"]
    assert "smpte:TimeCode" in info["metadata"]  # the source's frame-1 value, carried...
    out = _write(clip_info=info, clip_path=str(tmp_path / "out"))
    frames = sorted((Path(out).parent / "woods walk ").glob("*.exr"))
    # ...but replaced by one value per frame, not copied onto every frame.
    assert [_exr_timecode(f) for f in frames] == ["01:00:00:00", "01:00:00:01", "01:00:00:02"]


def test_reader_takes_the_exr_timecode_when_the_xml_has_none(tmp_path):
    """Decision 2026-10-29: no <startTimecode> -> the feed's first frame's EXR smpte:TimeCode."""
    r = _read(_flame_clip(tmp_path, with_tc=False))
    assert r["clip_info"]["start_timecode"] == "01:00:00:00"
    assert "start TC 01:00:00:00" in r["clip_report"]


def test_reader_timecode_is_the_first_frame_reads_also_for_a_window_exr_only(tmp_path):
    """Issue #8: clip_info's start timecode belongs to its start_frame. Frame 2 of a clip starting at
    01:00:00:00 carries 01:00:00:01 in its own header; read from there, that is the start."""
    clip = _flame_clip(tmp_path, with_tc=False)
    info = _read(clip, start_frame=2)["clip_info"]
    assert (info["start_frame"], info["start_timecode"]) == (2, "01:00:00:01")


def test_reader_timecode_is_the_first_frame_reads_also_for_a_window_xml(tmp_path):
    clip = _flame_clip(tmp_path, n=9)
    assert _read(clip)["clip_info"]["start_timecode"] == "01:00:00:00"  # the whole clip: its start
    assert _read(clip, start_frame=3, end_frame=6)["clip_info"]["start_timecode"] == "01:00:00:02"
    assert _read(clip, frame_cap=4)["clip_info"]["start_timecode"] == "01:00:00:00"  # cap only shortens
    # The report names the same timecode as clip_info.
    assert "start TC 01:00:00:02" in _read(clip, start_frame=3)["clip_report"]


def test_reader_window_timecode_in_drop_frame_crosses_the_dropped_frames(tmp_path):
    from ComfyUI_OpenClip.nodes.reader import feed_start_timecode
    from ComfyUI_OpenClip.lib.openclip_xml import ClipSpan, ClipVersion
    v = ClipVersion(uid="v0", name="v0", spans=[ClipSpan(path="x.%d.exr", start_frame=1, duration=9)],
                    start_timecode="00:00:59;28")
    assert [feed_start_timecode(v, "x.%d.exr", 29.97, f) for f in (1, 2, 3, 4)] == \
        ["00:00:59;28", "00:00:59;29", "00:01:00;02", "00:01:00;03"]
    assert feed_start_timecode(v, "x.%d.exr", 29.97) == "00:00:59;28"  # no frame: the clip's start (Writer)


def test_window_read_written_back_to_the_source_clip_is_refused(tmp_path):
    """The default wiring appends to the source clip. A window starting 2 frames in has another
    start timecode than the clip's other versions (Beak's "No Media" in the timeline), so the
    append rule refuses it and nothing is written; a new clip_name works."""
    clip = _flame_clip(tmp_path, n=9)
    info = _read(clip, start_frame=3, end_frame=6)["clip_info"]
    before = clip.read_bytes()
    with pytest.raises(ValueError, match="START TIMECODE MISMATCH"):
        _write(n=4, clip_info=info)
    assert clip.read_bytes() == before
    out = _write(n=4, clip_info=info, clip_path=str(tmp_path / "out"), clip_filename="$(path)/$(clip_name)_win.clip")
    assert _ticks(_feeds(out)[-1]) == 86402


def test_reader_lists_versions_in_the_files_order(tmp_path):
    """Issue #8: Flame numbers v0, v02, v1 in creation order; sorting the names scrambled them."""
    from ComfyUI_OpenClip.tests.flame_fixtures import FlameLike, build
    clip = build(FlameLike(id="m", name="mix", versions=("v0", "v02", "v1")), tmp_path)
    r = _read(clip)
    assert r["clip_info"]["versions"] == ["v0", "v02", "v1"]
    report = r["clip_report"]
    assert report.index("  v0\n") < report.index("  v02\n") < report.index("  v1 ")


def test_reader_xml_timecode_wins_over_the_exr(tmp_path):
    clip = _flame_clip(tmp_path, ticks=86400)
    text = clip.read_text(encoding="utf-8").replace("<nbTicks>86400</nbTicks>", "<nbTicks>172800</nbTicks>")
    clip.write_text(text, encoding="utf-8")
    assert _read(clip)["clip_info"]["start_timecode"] == "02:00:00:00"


def test_reader_without_any_timecode_reports_none(tmp_path):
    r = _read(_flame_clip(tmp_path, with_tc=False, exr_tc=False))
    assert r["clip_info"]["start_timecode"] == "" and "start TC (none)" in r["clip_report"]


def test_issue7_reader_colour_comes_from_the_clip_xml(tmp_path):
    assert _read(_flame_clip(tmp_path))["info.colour_space"] == "Rec.709 video"  # was '' in 1.1.1


def test_issue7_reader_to_writer_keeps_the_colour_tag(tmp_path):
    out = _write(clip_info=_read(_flame_clip(tmp_path))["clip_info"], clip_path=str(tmp_path / "out"))
    assert [e.text for e in etree.parse(out).iter("colourSpace")] == ["Rec.709 video"]


def test_issue7_trailing_space_name_appends_to_the_source_clip(tmp_path):
    clip = _flame_clip(tmp_path, name="woods walk ")
    out = _write(clip_info=_read(clip)["clip_info"])
    assert out == str(clip)
    assert not (tmp_path / "woods walk.clip").exists()  # 1.1.1 created this sibling
    assert [f.get("vuid") for f in _feeds(clip)] == ["v0", "v1"]


def test_issue7_mismatch_error_suggests_a_new_clip_name(tmp_path):
    clip = _flame_clip(tmp_path)
    with pytest.raises(ValueError, match="fps: existing=23.976 new=24.*new clip_name"):
        _write(clip_info=_read(clip)["clip_info"], fps=24.0)


# --- append timing rules ----------------------------------------------------------------------------


@pytest.mark.parametrize("overwrite", [False, True])
def test_append_with_a_different_start_timecode_errors_and_writes_nothing(tmp_path, overwrite):
    clip = _flame_clip(tmp_path)
    info = _read(clip)["clip_info"]
    before = _snapshot(tmp_path)
    with pytest.raises(ValueError, match=r"(?s)START TIMECODE MISMATCH.*01:00:00:00.*02:00:00:00"):
        _write(clip_info=info, start_timecode="02:00:00:00", version_name="v0" if overwrite else "next",
               overwrite=overwrite)
    assert _snapshot(tmp_path) == before


def test_append_with_same_real_timecode_and_other_start_frame_only_warns(tmp_path, capsys):
    clip = _flame_clip(tmp_path)
    _write(clip_info=_read(clip)["clip_info"], start_frame=1001)
    assert "WARNING: start frame 1001 differs" in capsys.readouterr().out
    assert [f.get("vuid") for f in _feeds(clip)] == ["v0", "v1"]


def test_append_at_zero_timecode_with_other_start_frame_errors_and_writes_nothing(tmp_path):
    clip = _flame_clip(tmp_path, ticks=0)
    info = _read(clip)["clip_info"]
    before = _snapshot(tmp_path)
    with pytest.raises(ValueError, match=r"(?s)START FRAME MISMATCH.*set start_frame to 1"):
        _write(clip_info=info, start_frame=1001)
    assert _snapshot(tmp_path) == before


def test_append_to_a_clip_without_xml_timecode_uses_its_exr_timecode(tmp_path):
    """Same lookup as the Reader (XML, else the feed's first frame's EXR smpte:TimeCode), so an
    append to such a clip lines up with what the Reader reports."""
    clip = _flame_clip(tmp_path, name="shot", with_tc=False)  # EXRs from 01:00:00:00
    with pytest.raises(ValueError, match=r"(?s)START TIMECODE MISMATCH.*starts at 01:00:00:00"):
        _write(clip_path=str(tmp_path), clip_name="shot", fps=23.976, start_timecode="00:00:00:00")
    _write(clip_path=str(tmp_path), clip_name="shot", fps=23.976)  # auto -> the EXR's 01:00:00:00
    _write(clip_info=_read(clip)["clip_info"])  # Reader -> Writer: the same timecode
    assert [_ticks(f) for f in _feeds(clip)[1:]] == [86400, 86400]


def test_append_to_a_clip_without_any_timecode_treats_it_as_zero(tmp_path):
    clip = _flame_clip(tmp_path, name="shot", with_tc=False, exr_tc=False)
    with pytest.raises(ValueError, match=r"(?s)START TIMECODE MISMATCH.*00:00:00:00"):
        _write(clip_path=str(tmp_path), clip_name="shot", fps=23.976, start_timecode="01:00:00:00")
    _write(clip_path=str(tmp_path), clip_name="shot", fps=23.976)  # auto -> 00:00:00:00, start 1 = 1
    assert [_ticks(f) for f in _feeds(clip)[1:]] == [0]


def test_invalid_start_timecode_raises_before_anything_touches_disk(tmp_path):
    with pytest.raises(ValueError, match="start_timecode"):
        _write(clip_path=str(tmp_path), clip_name="a", start_timecode="25:00")
    assert list(tmp_path.iterdir()) == []


def test_rates_above_30_get_the_xml_timecode_but_no_exr_timecode(tmp_path):
    out = _write(clip_path=str(tmp_path), clip_name="a", fps=50.0, start_timecode="01:00:00:10")
    (feed,) = _feeds(out)
    assert (feed.findtext("startTimecode/rate"), _ticks(feed)) == ("50", 180010)
    assert all(_exr_timecode(f) == "" for f in (tmp_path / "a").glob("*.exr"))


# --- Writer auto fields -------------------------------------------------------------------------------


def test_writer_defaults_without_clip_info(tmp_path):
    out = _write(clip_path=str(tmp_path), clip_name="a")
    root = etree.parse(out).getroot()
    feed = root.find("tracks/track/feeds/feed")
    assert root.get("version") == "6"
    assert feed.findtext("sampleRate") == "24" and _ticks(feed) == 0
    assert feed.findtext("storageFormat/colourSpace") == "Rec.709 video"
    assert feed.findtext("spans/span/path").endswith("/a/a_v001.[0001-0003].exr")  # start 1, padding 4


def test_writer_takes_every_auto_field_from_clip_info(tmp_path):
    info = _read(_flame_clip(tmp_path, start=5))["clip_info"]
    out = _write(clip_info=info, clip_path=str(tmp_path / "out"))
    root = etree.parse(out).getroot()
    feed = root.find("tracks/track/feeds/feed")
    assert Path(out).name == "woods walk .clip"
    assert root.get("version") == "9" and root.findtext("name") == "woods walk "
    assert feed.findtext("sampleRate") == "23.976" and _ticks(feed) == 86400
    assert feed.findtext("storageFormat/colourSpace") == "Rec.709 video"
    assert feed.findtext("spans/span/path").endswith("/woods walk /woods walk _v001.[000005-000007].exr")


def test_writer_fields_override_clip_info(tmp_path):
    info = _read(_flame_clip(tmp_path))["clip_info"]
    out = _write(clip_info=info, clip_path=str(tmp_path / "out"), clip_name="other", fps=25.0, start_frame=100,
                 start_timecode="02:00:00:00", frame_padding=3, colour_space="ACEScg", format_version="7")
    root = etree.parse(out).getroot()
    feed = root.find("tracks/track/feeds/feed")
    assert Path(out) == tmp_path / "out" / "other.clip"
    assert root.get("version") == "7" and feed.findtext("sampleRate") == "25"
    assert (feed.findtext("startTimecode/rate"), _ticks(feed)) == ("25", 180000)
    assert feed.findtext("storageFormat/colourSpace") == "ACEScg"
    assert feed.findtext("spans/span/path").endswith("/other/other_v001.[100-102].exr")
    frame = next((tmp_path / "out" / "other").glob("*.100.exr"))
    _, _, meta = image_io.read_sequence(str(frame), 0, 0, load_alpha=False)
    assert meta["openclip:ColorSpace"] == "ACEScg" and meta["autodeskColorSpace"] == "ACEScg"


def test_version_name_is_never_taken_from_clip_info(tmp_path):
    clip = _flame_clip(tmp_path)
    info = _read(clip)["clip_info"]
    assert info["version_name"] == "v0"
    _write(clip_info=info)  # "next": v1, not a collision with v0
    assert [f.get("vuid") for f in _feeds(clip)] == ["v0", "v1"]  # Flame's v0 -> v1: padding kept
    assert "version_name" not in [k for k, v in OpenClipWriter.INPUT_TYPES()["optional"].items()]


def test_clip_info_with_an_unwritable_schema_version_falls_back_to_6(tmp_path, capsys):
    out = _write(clip_info={"format_version": "4", "metadata": {}}, clip_path=str(tmp_path), clip_name="a")
    assert etree.parse(out).getroot().get("version") == "6"
    assert "not writable" in capsys.readouterr().out


def test_writer_sockets_and_auto_defaults():
    types = OpenClipWriter.INPUT_TYPES()
    assert list(types["optional"]) == ["MASK", "clip_info", "aov_chain"]
    req = types["required"]
    assert [k for k, v in req.items() if v[0] in ("IMAGE",)] == ["IMAGE"]
    assert {k: req[k][1]["default"] for k in ("clip_path", "clip_name", "start_frame", "start_timecode", "fps",
                                               "colour_space", "format_version", "frame_padding", "version_name")} == {
        "clip_path": "", "clip_name": "", "start_frame": -1, "start_timecode": "", "fps": 0.0,
        "colour_space": "", "format_version": "", "frame_padding": 0, "version_name": "next"}
    for gone in ("CLIP_METADATA", "colour_space", "format_version"):
        assert gone not in types["optional"]


def test_clip_name_and_path_still_required_without_clip_info(tmp_path):
    with pytest.raises(ValueError, match="clip_path is required"):
        _write(clip_name="a")
    with pytest.raises(ValueError, match="clip_name is required"):
        _write(clip_path=str(tmp_path))


# --- exact-file append ---------------------------------------------------------------------------------


def test_destination_that_is_the_source_file_by_another_spelling_appends_to_that_file(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    clip = _flame_clip(real, name="woods walk ")
    os.symlink(real, tmp_path / "link")
    info = _read(clip)["clip_info"]
    out = _write(clip_info=info, clip_path=str(tmp_path / "link"))
    assert out == str(clip)  # the source's own path, not the symlinked spelling
    assert [f.get("vuid") for f in _feeds(clip)] == ["v0", "v1"]


def test_names_with_spaces_typed_by_hand_are_not_trimmed(tmp_path):
    out = _write(clip_path=str(tmp_path), clip_name=" a b ")
    assert Path(out).name == " a b .clip"
    assert openclip_xml.parse(out).clip_name == " a b "


def test_name_rules_still_apply_to_clip_info_names(tmp_path):
    with pytest.raises(ValueError, match="plain name"):
        _write(clip_info={"clip_name": "../x", "clip_path": str(tmp_path), "metadata": {}})
    assert list(tmp_path.iterdir()) == []


# --- Reader clip_info, colour lookup, clip_report ----------------------------------------------------------


def test_reader_outputs():
    assert OpenClipReader.RETURN_NAMES == (
        "IMAGE", "MASK", "clip_info", "aov_handle", "frame_count", "width", "height", "fps", "clip_report")
    assert OpenClipReader.RETURN_TYPES[:4] == ("IMAGE", "MASK", "CLIP_INFO", "AOV_HANDLE")


def test_reader_clip_info_values(tmp_path):
    r = _read(_flame_clip(tmp_path))
    info = r["clip_info"]
    assert {k: info[k] for k in ("clip_name", "version_name", "start_frame", "start_timecode", "colour_space",
                                 "format_version", "frame_padding")} == {
        "clip_name": "woods walk ", "version_name": "v0", "start_frame": 1, "start_timecode": "01:00:00:00",
        "colour_space": "Rec.709 video", "format_version": "9", "frame_padding": 6}
    assert info["clip_path"] == str(tmp_path / "woods walk .clip")
    assert info["fps"] == pytest.approx(24000 / 1001) == r["fps"]
    assert info["metadata"]["autodeskColorSpace"] == "Rec.709 video"


@pytest.mark.parametrize("xml, meta, expected", [
    ("XML", {"autodeskColorSpace": "AD", "oiio:ColorSpace": "lin_ap1_scene"}, "XML"),
    ("", {"autodeskColorSpace": "AD", "oiio:ColorSpace": "lin_ap1_scene"}, "AD"),
    ("", {"oiio:ColorSpace": "lin_ap1_scene"}, "lin_ap1_scene"),  # read back via openclip:ColorSpace
    ("", {}, ""),
])
def test_reader_colour_lookup_order(tmp_path, xml, meta, expected):
    assert _read(_flame_clip(tmp_path, xml_colour=xml, exr_meta=meta))["info.colour_space"] == expected


@pytest.mark.parametrize("meta, expected", [
    ({"autodeskColorSpace": "AD", "openclip:ColorSpace": "OC", "oiio:ColorSpace": "OI"}, "AD"),
    ({"openclip:ColorSpace": "OC", "oiio:ColorSpace": "OI"}, "OC"),
    ({"oiio:ColorSpace": "OI"}, "OI"),
    ({"autodeskColorSpace": "  ", "oiio:ColorSpace": "OI"}, "OI"),
    ({}, ""),
])
def test_header_colour_lookup_order(meta, expected):
    # The EXR half of the lookup (the XML tag comes first; see the Reader test above).
    assert image_io.colour_space_from_metadata(meta) == expected


def test_clip_report_layout(tmp_path):
    clip = _flame_clip(tmp_path)
    _write(clip_info=_read(clip)["clip_info"])  # adds v1, now current
    r = OpenClipReader().execute(clip_path=str(clip), version="v0", start_frame=-1, end_frame=-1,
                                 path_from="", path_to="")
    report = r["result"][8]
    assert r["ui"] == {"clip_report": [report]}
    assert report == (
        f"CLIP     woods walk    ({clip})\n"
        "FORMAT   Open Clip schema 9 · EXR half ZIP · 8×8 · 23.976 fps\n"
        "         colour: Rec.709 video\n"
        "TIME     start TC 01:00:00:00 · frames 1–3 (3) · padding 6\n"
        "\n"
        "VERSIONS\n"
        "  v0  ← loaded\n"
        "  v1  (current)\n"
        "\n"
        "AOVS\n"
        "  (none)"
    )


def test_clip_report_lists_aovs_one_per_line(tmp_path):
    from ComfyUI_OpenClip.nodes.aov_writer import OpenClipAOVWriter
    chain = OpenClipAOVWriter().execute(name="depth", matte=torch.rand(2, 8, 8))[0]
    chain = OpenClipAOVWriter().execute(name="normals", image=torch.rand(2, 8, 8, 3), aov_chain=chain)[0]
    out = _write(n=2, clip_path=str(tmp_path), clip_name="a", aov_chain=chain)
    report = _read(out)["clip_report"]
    assert report.endswith("\n\nAOVS\n  depth\n  normals")
    assert "  v001  (current)  ← loaded" in report


# --- OpenClipClipInfo ---------------------------------------------------------------------------------------


def test_clip_info_node_unpacks_every_value(tmp_path):
    info = _read(_flame_clip(tmp_path))["clip_info"]
    out = dict(zip(OpenClipClipInfo.RETURN_NAMES, OpenClipClipInfo().execute(info)))
    assert out["clip_path"] == info["clip_path"] and out["clip_name"] == "woods walk "
    assert (out["version_name"], out["start_frame"], out["start_timecode"], out["colour_space"],
            out["format_version"], out["frame_padding"]) == ("v0", 1, "01:00:00:00", "Rec.709 video", "9", 6)
    assert out["fps"] == pytest.approx(24000 / 1001)
    assert out["metadata"] is info["metadata"]
    assert out["clip_report"].startswith("CLIP     woods walk ")
    assert dict(zip(OpenClipClipInfo.RETURN_NAMES, OpenClipClipInfo.RETURN_TYPES))["metadata"] == "CLIP_METADATA"


def test_clip_info_node_bit_depth_output_sits_before_clip_report(tmp_path):
    assert OpenClipClipInfo.RETURN_NAMES[-2:] == ("bit_depth", "clip_report")
    assert OpenClipClipInfo.RETURN_TYPES[-2:] == ("STRING", "STRING")
    info = _read(_flame_clip(tmp_path))["clip_info"]
    assert dict(zip(OpenClipClipInfo.RETURN_NAMES, OpenClipClipInfo().execute(info)))["bit_depth"] == "half"


# --- bit depth (2026-10-29) -------------------------------------------------------------------------------


@pytest.mark.parametrize("depth, fmt, expected", [("half (16-bit)", "EXR", "half"), ("float (32-bit)", "EXR", "float"),
                                                  ("half (16-bit)", "PNG", "")])
def test_reader_clip_info_bit_depth_from_the_first_frame(tmp_path, depth, fmt, expected):
    out = _write(clip_path=str(tmp_path), clip_name="a", exr_bit_depth=depth, file_format=fmt)
    r = _read(out)
    assert r["clip_info"]["bit_depth"] == expected
    assert r["clip_report"].count(expected or "PNG") == 1  # FORMAT line only, never repeated


def _written_depth(clip) -> str:
    feed = _feeds(clip)[-1]
    frame = Path(clip).parent / feed.findtext("spans/span/path")
    path = re.sub(r"\[(\d+)-\d+\]", lambda m: m.group(1), str(frame))
    return image_io.describe_frame(path).split()[1]


def test_writer_bit_depth_auto_takes_clip_info_else_half(tmp_path):
    assert OpenClipWriter.INPUT_TYPES()["required"]["exr_bit_depth"][0] == ["auto", "half (16-bit)", "float (32-bit)"]
    src = _write(clip_path=str(tmp_path / "src"), clip_name="a", exr_bit_depth="float (32-bit)")
    info = _read(src)["clip_info"]
    assert _written_depth(_write(clip_info=info, clip_path=str(tmp_path / "auto"), exr_bit_depth="auto")) == "float"
    assert _written_depth(_write(clip_info=info, clip_path=str(tmp_path / "typed"),
                                 exr_bit_depth="half (16-bit)")) == "half"  # an explicit choice overrides
    assert _written_depth(_write(clip_path=str(tmp_path / "none"), clip_name="b", exr_bit_depth="auto")) == "half"
    png = _read(_write(clip_path=str(tmp_path / "png"), clip_name="p", file_format="PNG"))["clip_info"]
    assert _written_depth(_write(clip_info=png, clip_path=str(tmp_path / "frompng"), exr_bit_depth="auto")) == "half"


def _first_frame_attrs(clip) -> dict:
    """Header attributes of the first frame of the clip's newest feed."""
    feed = _feeds(clip)[-1]
    frame = Path(clip).parent / feed.findtext("spans/span/path")
    inp = oiio.ImageInput.open(re.sub(r"\[(\d+)-\d+\]", lambda m: m.group(1), str(frame)))
    try:
        return {a.name: a.value for a in inp.spec().extra_attribs}
    finally:
        inp.close()


def test_writer_compression_auto_takes_clip_info_else_zip(tmp_path):
    assert OpenClipWriter.INPUT_TYPES()["required"]["exr_compression"][0] == ["auto", "ZIP", "PIZ", "DWAB"]
    src = _write(clip_path=str(tmp_path / "src"), clip_name="a", exr_compression="PIZ")
    info = _read(src)["clip_info"]
    assert info["compression"] == "PIZ"
    comp = lambda clip: _first_frame_attrs(clip)["compression"]  # noqa: E731
    assert comp(_write(clip_info=info, clip_path=str(tmp_path / "auto"), exr_compression="auto")) == "piz"
    assert comp(_write(clip_info=info, clip_path=str(tmp_path / "typed"), exr_compression="DWAB")) == "dwab"
    assert comp(_write(clip_path=str(tmp_path / "none"), clip_name="b", exr_compression="auto")) == "zip"
    other = dict(info, compression="ZIPS")  # a compression the Writer doesn't offer -> default
    assert comp(_write(clip_info=other, clip_path=str(tmp_path / "zips"), exr_compression="auto")) == "zip"
    png = _read(_write(clip_path=str(tmp_path / "png"), clip_name="p", file_format="PNG"))["clip_info"]
    assert png["compression"] == ""


def test_writer_puts_the_clips_frame_rate_in_every_frame_header(tmp_path):
    # As Flame does: the exact rate (24000/1001 for 23.976), replacing a carried value.
    src = _write(clip_path=str(tmp_path / "src"), clip_name="a", fps=23.976)
    assert tuple(_first_frame_attrs(src)["FramesPerSecond"]) == (24000, 1001)
    info = _read(src)["clip_info"]
    info["metadata"]["FramesPerSecond"] = (30, 1)
    out = _write(clip_info=info, clip_path=str(tmp_path / "out"), fps=25.0)
    assert tuple(_first_frame_attrs(out)["FramesPerSecond"]) == (25, 1)


def test_clip_info_colour_feeds_colour_space_transform_same_as_input(tmp_path):
    pytest.importorskip("PyOpenColorIO")
    from ComfyUI_OpenClip.nodes.colour_space_transform import (
        CUSTOM_LABEL, SAME_AS_INPUT_LABEL, OpenClipColourSpaceTransform)
    out = _write(n=1, clip_path=str(tmp_path), clip_name="a", colour_space="scaled_2x")
    info = _read(out)["clip_info"]
    colour = dict(zip(OpenClipClipInfo.RETURN_NAMES, OpenClipClipInfo().execute(info)))["colour_space"]
    assert colour == "scaled_2x"
    images = torch.full((1, 2, 2, 3), 0.25)
    result, _ = OpenClipColourSpaceTransform().execute(
        IMAGE=images, ocio_config=str(Path(__file__).parent / "fixtures" / "test_ocio.ocio"),
        source=CUSTOM_LABEL, custom_source="linear", target_colour_space=SAME_AS_INPUT_LABEL,
        custom_target_colour_space="", same_as_input_colour_space=colour)
    assert not torch.allclose(result, images)  # resolved to a real, different space


# --- EXR Sequence Reader -> Writer --------------------------------------------------------------------------


def test_exr_sequence_clip_info_drives_a_new_clip(tmp_path):
    image_io.write_sequence(str(tmp_path / "render.%05d.exr"), torch.rand(2, 8, 8, 3), None, 7, "EXR",
                            metadata={"oiio:ColorSpace": "lin_ap1_scene"})
    r = ExrSequenceReader().execute(sequence_path=str(tmp_path / "render.#####.exr"), layer="", start_frame=-1,
                                    end_frame=-1, frame_cap=-1, fps=25.0, colour_space="")["result"]
    info = r[2]
    assert (info["clip_path"], info["clip_name"], info["frame_padding"], info["start_timecode"], info["bit_depth"]) == (
        str(tmp_path), "render", 5, "", "half")
    out = _write(n=2, clip_info=info)
    feed = _feeds(out)[0]
    assert Path(out) == tmp_path / "render.clip"
    assert etree.parse(out).getroot().get("version") == "6"  # a sequence has no schema version
    assert feed.findtext("sampleRate") == "25" and _ticks(feed) == 0
    assert feed.findtext("storageFormat/colourSpace") == "lin_ap1_scene"
    assert feed.findtext("spans/span/path").endswith("/render/render_v001.[00007-00008].exr")


# --- the real Flame clip (testclips/, gitignored) -------------------------------------------------------------


@pytest.fixture
def real_flame_clip(tmp_path):
    src = REAL_CLIP_DIR / "alpha_test.clip"
    if not src.exists():
        pytest.skip("testclips/clip_1 not present (gitignored)")
    text = src.read_text(encoding="utf-8")
    text = re.sub(r"<path encoding=\"pattern\">[^<]*/alpha_test_v01\.",
                  f"<path encoding=\"pattern\">{REAL_CLIP_DIR}/testclip_1.", text)
    clip = tmp_path / "alpha_test.clip"
    clip.write_text(text, encoding="utf-8")
    return clip


@pytest.mark.flame_clips
def test_real_flame_clip_round_trip(real_flame_clip):
    flat = lambda el: etree.tostring(etree.fromstring(etree.tostring(el), etree.XMLParser(remove_blank_text=True)))
    original = flat(_feeds(real_flame_clip)[0])
    r = _read(real_flame_clip, frame_cap=2)
    info = r["clip_info"]
    assert (info["start_timecode"], info["colour_space"], info["frame_padding"], info["format_version"]) == (
        "10:44:22:05", "Rec.1886 Rec.709 - Display", 6, "9")
    assert "start TC 10:44:22:05 · frames 1–2 (2) · padding 6" in r["clip_report"]
    out = _write(image=r["IMAGE"][:, :8, :8], clip_info=info, clip_path=str(real_flame_clip.parent / "new"))
    (feed,) = _feeds(out)
    assert _ticks(feed) == 927893
    assert [_exr_timecode(f) for f in sorted((Path(out).parent / "alpha_test").glob("*.exr"))] == [
        "10:44:22:05", "10:44:22:06"]
    # Appending (full size, so the format check passes) keeps the original feed byte for byte.
    _write(image=r["IMAGE"], clip_info=info)
    feeds = _feeds(real_flame_clip)
    assert flat(feeds[0]) == original  # lxml re-indents on save; content is unchanged
    assert [(f.get("vuid"), _ticks(f)) for f in feeds] == [("v01", 927893), ("v02", 927893)]


def test_exr_sequence_start_timecode_from_the_first_frame_read(tmp_path):
    """A plain sequence has no clip start of its own; the Writer numbers the new clip from the
    first frame read, so that frame's smpte:TimeCode is its start timecode."""
    image_io.write_sequence(str(tmp_path / "plate.%04d.exr"), torch.rand(3, 8, 8, 3), None, 10, "EXR",
                            start_timecode=(86400, 24, False))
    kw = dict(sequence_path=str(tmp_path / "plate.####.exr"), layer="", end_frame=-1, frame_cap=-1, fps=24.0,
              colour_space="")
    info = ExrSequenceReader().execute(start_frame=-1, **kw)["result"][2]
    assert info["start_timecode"] == "01:00:00:00"
    assert ExrSequenceReader().execute(start_frame=11, **kw)["result"][2]["start_timecode"] == "01:00:00:01"
    feed = _feeds(_write(clip_info=info))[0]
    assert _ticks(feed) == 86400
