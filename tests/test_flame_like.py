"""Smoke tier: synthetic Flame-like boundary cases (runs on CI).

Each case is a small Flame-shaped clip built by tests/flame_fixtures.py. For each one the
Reader -> Writer round trip is run twice, the way an artist wires it (clip_info connected,
Writer on auto): into a new clip, and appended to the source clip. The written XML is compared
field by field with the source (tests/flame_compare.py: start timecode rate/ticks/dropMode,
colourSpace, editRate/sampleRate as exact fractions, <name>, path padding/range, version
numbering, every original element untouched on append) and every written EXR's smpte:TimeCode
and colour keys are checked against the source frames.

The same checks run on real Flame clips in the full tier (test_flame_clips.py). What the
synthetic cases cannot prove is that Flame itself writes these shapes (drop-frame and the
non-ASCII name are assumed, not seen) -- that is what the real clips are for.
"""
from __future__ import annotations

import pytest
from lxml import etree

from .flame_compare import bcd_decode, bcd_encode, load, tc_label, tc_text, tc_ticks
from .flame_fixtures import FlameLike, build
from .flame_roundtrip import append_check, new_clip_check

CASES = [
    # timecode / rate
    FlameLike("tc_0100", tc="01:00:00:00"),
    FlameLike("tc_104422_v01", tc="10:44:22:05", colour="Rec.1886 Rec.709 - Display", versions=("v01",),
              next_version="v02"),
    FlameLike("tc_drop_2997", fps="29.97", tc="01:00:00;00"),
    FlameLike("tc_drop_2997_odd_minute", fps="29.97", tc="00:01:00;02"),
    FlameLike("tc_drop_minute_edge", fps="29.97", tc="00:00:59;28", n=6),  # a window crosses the dropped frames
    FlameLike("tc_drop_5994", fps="59.94", tc="00:10:00;00"),          # no EXR timecode above 30
    FlameLike("tc_pal", fps="25", tc="10:00:00:00"),
    FlameLike("tc_numbered_86400", fps="24", tc="01:00:00:00", start=86400, padding=8),
    FlameLike("tc_none", tc=None),                                     # no <startTimecode> at all
    FlameLike("tc_exr_only", tc="01:00:00:00", xml_tc=False),            # timecode only in the EXRs (Autodesk)
    FlameLike("tc_exr_only_numbered", fps="24", tc="01:00:00:00", xml_tc=False, start=86400, padding=8),
    FlameLike("tc_exr_only_drop_2997", fps="29.97", tc="01:00:00;00", xml_tc=False),
    FlameLike("rate_23976_decimal", rate_xml="decimal", tc="01:00:00:00"),
    FlameLike("rate_25_decimal_v6", schema="6", fps="25", rate_xml="decimal", tc="00:00:10:00"),
    # frame numbering / padding
    FlameLike("start_0", fps="24", start=0, padding=4),
    FlameLike("start_1", fps="24", start=1, padding=4),
    FlameLike("start_1001", fps="24", start=1001, padding=4),
    FlameLike("pad_6", padding=6),
    # names
    FlameLike("name_trailing_space", name="woods walk ", tc="01:00:00:00"),
    FlameLike("name_leading_space_non_ascii", name=" lead café", tc="01:00:00:00"),
    # versions
    FlameLike("versions_v0_v1", versions=("v0", "v1"), next_version="v2"),
    FlameLike("versions_v001", versions=("v001", "v002", "v003"), next_version="v004"),
    FlameLike("no_versions_block", versions_block=False),
    # colour tags
    FlameLike("colour_acescg", colour="ACEScg"),
    FlameLike("colour_rec709_video", colour="Rec.709 video"),
    FlameLike("colour_rec1886", colour="Rec.1886 Rec.709 - Display"),
    FlameLike("colour_raw", colour="Raw"),
    FlameLike("colour_matte", colour="Matte"),
    FlameLike("colour_missing", colour=None, exr_colour=None),
    FlameLike("colour_exr_only", colour=None, exr_colour="ACEScg"),
    FlameLike("aces_alpha_float", colour="ACEScg", channels=4, depth=32),
    # structure
    FlameLike("audio_after_video", audio="after"),
    FlameLike("audio_before_video", audio="before"),
    FlameLike("schema_5", schema="5"),
    FlameLike("schema_6", schema="6"),
    FlameLike("schema_7", schema="7"),
    FlameLike("schema_8", schema="8"),
    FlameLike("schema_9", schema="9"),
]
IDS = [c.id for c in CASES]


@pytest.fixture(params=CASES, ids=IDS)
def case_clip(request, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    return request.param, build(request.param, src)


def test_new_clip_matches_the_source_field_by_field(case_clip, tmp_path):
    case, clip = case_clip
    diffs, written = new_clip_check(clip, tmp_path / "out")
    assert diffs == []
    assert load(written).find("tracks/track/feeds/feed").get("vuid") == "v001"  # a new clip starts at v001


def test_window_read_starts_at_its_own_first_frames_timecode(case_clip, tmp_path):
    """Issue #8 (Beak): a Reader window starting partway in (frames 3..) writes a clip whose start
    timecode, in the XML and in every EXR frame, is that frame's, not the source clip's start."""
    case, clip = case_clip
    diffs, written = new_clip_check(clip, tmp_path / "out", start_frame=case.start + 2)
    assert diffs == []


def test_append_matches_the_source_and_leaves_flames_elements_alone(case_clip):
    case, clip = case_clip
    diffs, new = append_check(clip)
    assert diffs == []
    assert new == [case.next_version]


@pytest.mark.parametrize("case", [c for c in CASES if c.id in (
    "tc_0100", "tc_drop_2997", "name_trailing_space", "versions_v0_v1", "no_versions_block", "audio_before_video")],
    ids=lambda c: c.id)
def test_append_to_an_append(case, tmp_path):
    clip = build(case, tmp_path)
    diffs, new = append_check(clip, times=2)
    assert diffs == []
    n = int(case.next_version[1:])
    assert new == [case.next_version, f"v{n + 1:0{len(case.next_version) - 1}d}"]


def test_comparison_helper_catches_a_lost_timecode(tmp_path):
    """The checks must fail on issue #7's first bug: a written feed at nbTicks 0."""
    case = FlameLike("guard", tc="01:00:00:00")
    clip = build(case, tmp_path / "src")
    _, written = new_clip_check(clip, tmp_path / "out")
    root = etree.parse(written)
    root.find(".//startTimecode/nbTicks").text = "0"
    root.write(written)
    from .flame_compare import new_clip_diffs
    diffs = new_clip_diffs(clip, written, first=1, count=3)
    assert any("tc is (24, 0, 'NDF'), source says (24, 86400, 'NDF')" in d for d in diffs)


def test_comparison_helper_catches_a_lost_exr_only_timecode_and_a_lost_bit_depth(tmp_path):
    """The checks must fail when the EXR-only timecode (no XML <startTimecode>) is written as
    00:00:00:00, and when a 32-bit source is written at 16 bits."""
    from .flame_compare import exr_attrs, new_clip_diffs
    case = FlameLike("guard_exr_tc", tc="01:00:00:00", xml_tc=False, depth=32)
    clip = build(case, tmp_path / "src")
    _, written = new_clip_check(clip, tmp_path / "out")
    root = etree.parse(written)
    root.find(".//startTimecode/nbTicks").text = "0"
    root.find(".//storageFormat/channelsDepth").text = "16"
    root.write(written)
    word = exr_attrs(tmp_path / "src" / "shot" / "shot_v0.000001.exr")["smpte:TimeCode"][0]
    diffs = new_clip_diffs(clip, written, first=1, count=3, exr_tc_word=word)
    assert any("tc is (24, 0, 'NDF'), source says (24, 86400, 'NDF')" in d for d in diffs)
    assert any("depth is 16, source says 32" in d for d in diffs)


def test_comparison_helper_catches_a_changed_original_element(tmp_path):
    from .flame_compare import append_diffs
    clip = build(FlameLike("guard2"), tmp_path)
    original = clip.read_bytes()
    append_check(clip)
    clip.write_text(clip.read_text(encoding="utf-8").replace("<appVersion type=\"string\">2027.2</appVersion>",
                                                             "<appVersion type=\"string\">x</appVersion>", 1),
                    encoding="utf-8")
    diffs = append_diffs(original, clip, ["v1"], source_vuid="v0", first=1, count=3)
    assert any("append changed an original element" in d and "appVersion" in d for d in diffs)


# --- the timecode oracle itself (independent of lib/timecode.py) ---------------------------------


@pytest.mark.parametrize("text, rate, ticks", [
    ("01:00:00:00", 24, 86400), ("10:44:22:05", 24, 927893), ("10:00:00:00", 25, 900000),
    ("01:00:00;00", 30, 107892), ("00:01:00;02", 30, 1800), ("00:10:00;00", 30, 17982),
    ("00:10:00;00", 60, 35964),
])
def test_oracle_matches_published_timecode_values(text, rate, ticks):
    drop = ";" in text
    assert tc_ticks(text, rate) == ticks
    assert tc_text(ticks, rate, drop) == text


def test_oracle_bcd_matches_flames_own_exr_value():
    # testclips/clip_1/testclip_1.000002.exr (Flame 2027): smpte:TimeCode (272900614, 0) = 10:44:22:06
    assert bcd_encode(*tc_label(927894, 24, False), False) == 272900614
    assert bcd_decode(272900614) == "10:44:22:06"


# --- bugs found by the first maintainer run on real clips (2026-10-29) -----------------------------


@pytest.mark.parametrize("layout", ["Multichannel", "Separate Files"])
def test_aovs_from_a_flame_source_round_trip(tmp_path, layout):
    """Flame's EXRs carry name="rgb". With clip_info wired (2.0 carries the header metadata
    always) that name was copied onto every part: Multichannel failed ("Each part should have a
    unique name"), Separate Files siblings were renamed and read back as None."""
    import torch
    from ComfyUI_OpenClip.nodes.aov_reader import OpenClipAOVReader
    from ComfyUI_OpenClip.nodes.aov_writer import OpenClipAOVWriter
    from ComfyUI_OpenClip.nodes.writer import OpenClipWriter
    from .flame_roundtrip import read
    r = read(build(FlameLike("aov_src", tc="01:00:00:00"), tmp_path / "src"))
    assert r["info.metadata"].get("name") == "rgb"
    matte = r["IMAGE"][..., 0]
    chain = OpenClipAOVWriter().execute(name="depth", matte=matte)[0]
    chain = OpenClipAOVWriter().execute(name="normals", image=r["IMAGE"], aov_chain=chain)[0]
    out = OpenClipWriter().execute(IMAGE=r["IMAGE"], clip_info=r["clip_info"], clip_path=str(tmp_path / "out"),
                                   aov_chain=chain, aov_layout=layout)[0]
    back = read(out)
    assert back["info.aovs"] == ["depth", "normals"]
    _img, m = OpenClipAOVReader().execute(aov_handle=back["aov_handle"], part_name="depth")
    img, _m = OpenClipAOVReader().execute(aov_handle=back["aov_handle"], part_name="normals")
    assert torch.allclose(m, matte, atol=1e-3) and torch.allclose(img, r["IMAGE"], atol=1e-3)


def _mixed_size_clip(tmp_path):
    """A Flame-like clip whose first version (v0) is 16x16 and current version (v1) 8x8."""
    clip = build(FlameLike("mixed", versions=("v0", "v1")), tmp_path)
    text = clip.read_text(encoding="utf-8")
    first_feed_end = text.index("</feed>")
    head = text[:first_feed_end].replace('<width type="uint">8</width>', '<width type="uint">16</width>')
    head = head.replace('<height type="uint">8</height>', '<height type="uint">16</height>')
    clip.write_text(head + text[first_feed_end:], encoding="utf-8")
    return clip


def test_append_compares_the_format_with_the_current_version(tmp_path):
    """Flame keeps versions of different sizes in one clip (a real clip on the share: v01-v05
    1920x1088, v06-v07 1280x720). The format check compared with the *first* feed, so appending
    at the current version's own size was refused. The Reader refuses such a clip since
    2026-10-29 (next test), so the Writer is driven directly here."""
    import torch
    from ComfyUI_OpenClip.nodes.writer import OpenClipWriter
    clip = _mixed_size_clip(tmp_path)
    kw = dict(clip_path=str(clip), clip_name="shot", fps=24000 / 1001, file_format="EXR")
    out = OpenClipWriter().execute(IMAGE=torch.rand(3, 8, 8, 3), **kw)[0]  # 8x8, the current version's size
    assert [f.get("vuid") for f in load(out).findall("tracks/track/feeds/feed")] == ["v0", "v1", "v2"]
    with pytest.raises(ValueError, match="width: existing=8 new=16"):
        OpenClipWriter().execute(IMAGE=torch.rand(3, 16, 16, 3), **kw)


@pytest.mark.parametrize("version", ["current", "v0"])
def test_reader_refuses_a_clip_whose_versions_differ_in_size(tmp_path, version):
    """User decision 2026-10-29: stricter than Flame. Whichever version is asked for, the Reader
    lists every version's size; XML only, and IS_CHANGED still never raises."""
    from ComfyUI_OpenClip.nodes.reader import OpenClipReader
    clip = _mixed_size_clip(tmp_path)
    args = dict(clip_path=str(clip), version=version, start_frame=-1, end_frame=-1, path_from="", path_to="")
    with pytest.raises(ValueError, match=r"VERSIONS OF DIFFERENT SIZES(.|\n)*v0  16x16\n  v1  8x8"):
        OpenClipReader().execute(**args)
    assert isinstance(OpenClipReader.IS_CHANGED(**args), str)


def test_reader_skips_versions_with_no_recorded_size(tmp_path):
    from .flame_roundtrip import read
    clip = build(FlameLike("nosize", versions=("v0", "v1")), tmp_path)
    text = clip.read_text(encoding="utf-8")
    first_feed_end = text.index("</feed>")
    head = text[:first_feed_end].replace('<width type="uint">8</width>', "")
    clip.write_text(head + text[first_feed_end:], encoding="utf-8")
    assert read(clip)["width"] == 8
