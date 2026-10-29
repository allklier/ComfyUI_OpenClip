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
    FlameLike("tc_drop_5994", fps="59.94", tc="00:10:00;00"),          # no EXR timecode above 30
    FlameLike("tc_pal", fps="25", tc="10:00:00:00"),
    FlameLike("tc_numbered_86400", fps="24", tc="01:00:00:00", start=86400, padding=8),
    FlameLike("tc_none", tc=None),                                     # no <startTimecode> at all
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
