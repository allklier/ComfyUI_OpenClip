"""Full tier: fidelity against real Flame clips (marker flame_clips; `pytest --tier full`).

Data-driven. Every .clip found in these places is a case; nothing about a clip is hard-coded,
expectations come from its own XML and EXRs (tests/flame_compare.py), so any real clip works:

  tests/fixtures/flame/<folder>/*.clip   the planned set, see tests/flame_clips.md (gitignored)
  testclips/clip_1/alpha_test.clip       the issue-#7 repro clip, media path rewritten (gitignored)
  tests/fixtures/autodesk_examples/      Autodesk's examples, where readable (gitignored)
  $OPENCLIP_FLAME_CLIPS                  extra .clip files or folders, os.pathsep-separated
                                         (e.g. a show's clips on the maintainer machine)

Each case is copied into a temp folder before anything is written (the source clip and its media
are only read). Per case: Reader -> Writer into a new clip, and appended to the copy; the same
field-by-field and per-frame checks as the synthetic smoke suite (test_flame_like.py), plus
pixels equal to the source frames. $OPENCLIP_FLAME_FRAME_CAP (default 2) limits frames per read.
"""
from __future__ import annotations

import os
import re
import shutil
from fractions import Fraction
from pathlib import Path

import pytest
from lxml import etree

from .flame_compare import feed_fields, load, next_flame_version, video_track
from .flame_roundtrip import append_check, new_clip_check

pytestmark = pytest.mark.flame_clips

REPO = Path(__file__).resolve().parent.parent
FLAME_DIR = Path(__file__).parent / "fixtures" / "flame"
ALPHA_TEST = REPO / "testclips" / "clip_1" / "alpha_test.clip"
AUTODESK_DIR = Path(__file__).parent / "fixtures" / "autodesk_examples"
FRAME_CAP = int(os.environ.get("OPENCLIP_FLAME_FRAME_CAP", "2"))

# The planned real clips (tests/flame_clips.md): folder -> (what it must show, check).
PLANNED = {
    "tc_0100": ("23.976 at 01:00:00:00",
                lambda f, r: f["edit_rate"] == Fraction(24000, 1001) and f["tc"][:2] == (24, 86400)),
    "tc_drop": ("29.97 drop-frame", lambda f, r: f["edit_rate"] == Fraction(30000, 1001) and f["tc"][2] != "NDF"),
    "tc_pal": ("25 fps at 10:00:00:00", lambda f, r: f["edit_rate"] == Fraction(25) and f["tc"][:2] == (25, 900000)),
    "tc_numbered": ("frame numbers taken from the timecode", lambda f, r: f["first"] == f["tc"][1]),
    "name_trailing": ("<name> with a trailing space", lambda f, r: f["name"].endswith(" ")),
    "name_leading": ("<name> with a leading space and non-ASCII", lambda f, r: f["name"].startswith(" ")
                     and not f["name"].isascii()),
    "versions": ("two or more Flame versions", lambda f, r: len(video_track(r).findall("feeds/feed")) >= 2),
    "aces_alpha": ("ACEScg, 32-bit float, alpha", lambda f, r: "ACES" in (f["colour"] or "") and f["channels"] == 4
                   and f["depth"] == 32),
    "audio": ("an audio track", lambda f, r: any((t.findtext("trackType") or "") == "audio"
                                                 for t in r.findall("tracks/track"))),
    "padding": ("frame padding other than 6", lambda f, r: f["padding"] not in (None, 6)),
    "matte": ("a matte clip (optional)", lambda f, r: True),
}


def _sources() -> list[tuple[str, Path, list[tuple[str, str]]]]:
    """(id, clip, [(regex, replacement) applied to <path> text in the temp copy])."""
    out = []
    if FLAME_DIR.is_dir():
        for clip in sorted(FLAME_DIR.glob("*/*.clip")):
            out.append((f"flame/{clip.parent.name}/{clip.name}", clip, []))
    if ALPHA_TEST.exists():
        out.append(("testclips/alpha_test.clip", ALPHA_TEST,
                    [(r"^.*/alpha_test_v01\.", f"{ALPHA_TEST.parent}/testclip_1.")]))
    if AUTODESK_DIR.is_dir():
        for clip in sorted(AUTODESK_DIR.glob("*.clip")):
            out.append((f"autodesk/{clip.name}", clip, []))
    for entry in filter(None, os.environ.get("OPENCLIP_FLAME_CLIPS", "").split(os.pathsep)):
        p = Path(entry)
        for clip in ([p] if p.suffix == ".clip" else sorted(p.glob("*.clip"))):
            out.append((f"env/{clip.name}", clip, []))
    return out


SOURCES = _sources()
_NONE = [pytest.param(None, marks=pytest.mark.skip(
    reason="no real Flame clips found (tests/fixtures/flame/, testclips/, autodesk_examples, $OPENCLIP_FLAME_CLIPS)"))]


def _copy(src: Path, rewrites: list, dest: Path) -> Path:
    """Copy the .clip into dest, every <path> made absolute (relative paths resolve against the
    original's folder; an absolute path whose folder doesn't exist is moved to the clip's own
    folder, as when a Flame export was copied here with its media)."""
    root = etree.parse(str(src))
    for el in root.iter("path"):
        text = (el.text or "").strip()
        for pattern, repl in rewrites:
            text = re.sub(pattern, repl, text)
        p = Path(text)
        if not p.is_absolute():
            p = src.parent / p
        elif not p.parent.is_dir():
            p = src.parent / p.name
        el.text = str(p)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / src.name
    root.write(str(out), xml_declaration=True, encoding="UTF-8")
    return out


@pytest.fixture(params=SOURCES or _NONE, ids=[s[0] for s in SOURCES] or ["none"])
def real_clip(request, tmp_path):
    ident, src, rewrites = request.param
    try:
        clip = _copy(src, rewrites, tmp_path / "src")
        fields = feed_fields(clip)
    except (etree.XMLSyntaxError, ValueError, StopIteration, IndexError, AttributeError) as e:
        pytest.skip(f"{ident}: not a clip this project reads ({type(e).__name__}: {e})")
    from .flame_compare import source_media_pattern
    pattern = source_media_pattern(clip)
    if pattern is None:
        pytest.skip(f"{ident}: current version is not a frame sequence ({fields['path']})")
    if fields["ext"] not in ("exr", "png"):
        pytest.skip(f"{ident}: {fields['ext']} media is not read by this project")
    if not Path(pattern % fields["first"]).exists():
        pytest.skip(f"{ident}: media not found ({pattern % fields['first']})")
    return clip


def test_real_clip_to_new_clip_matches_field_by_field(real_clip, tmp_path):
    diffs, _ = new_clip_check(real_clip, tmp_path / "out", frame_cap=FRAME_CAP)
    assert diffs == []


def test_real_clip_append_matches_and_keeps_every_flame_element(real_clip):
    existing = [f.get("vuid") for f in video_track(load(real_clip)).findall("feeds/feed")]
    diffs, new = append_check(real_clip, frame_cap=FRAME_CAP)
    assert diffs == []
    assert new == [next_flame_version(existing)]


def test_real_clip_append_to_an_append(real_clip):
    diffs, new = append_check(real_clip, times=2, frame_cap=FRAME_CAP)
    assert diffs == [] and len(new) == 2


@pytest.mark.parametrize("folder", sorted(PLANNED))
def test_planned_clip_covers_what_it_is_for(folder):
    """A planned folder must hold one clip that really shows its case (catches a mislabelled
    export); missing folders skip, so the set can grow one clip at a time."""
    what, check = PLANNED[folder]
    clips = sorted((FLAME_DIR / folder).glob("*.clip"))
    if not clips:
        pytest.skip(f"tests/fixtures/flame/{folder}/ not provided ({what})")
    assert len(clips) == 1, f"tests/fixtures/flame/{folder}/ holds {len(clips)} .clip files, expected 1"
    root = load(clips[0])
    assert check(feed_fields(root), root), f"{clips[0].name} does not show: {what} ({feed_fields(root)})"


def test_real_clip_exr_timecode_when_the_xml_has_none(real_clip, tmp_path):
    """Known gap, pinned: when the source's XML has no <startTimecode> but its EXRs carry a
    smpte:TimeCode (all of Autodesk's examples: frames 86400.. at 01:00:00:00), the Reader's
    start_timecode is "" (it reads the XML only, by design) and the Writer writes the new frames
    from 00:00:00:00. Fails once fixed (strict), forcing its removal."""
    from .flame_compare import exr_attrs, source_media_pattern
    fields = feed_fields(real_clip)
    first_tc = exr_attrs(source_media_pattern(real_clip) % fields["first"]).get("smpte:TimeCode")
    if fields["tc"] is not None or first_tc is None:
        pytest.skip("source has an XML <startTimecode>, or no EXR timecode: not this case")
    from .flame_compare import exr_frame_diffs
    _, written = new_clip_check(real_clip, tmp_path / "out", frame_cap=FRAME_CAP)
    diffs = exr_frame_diffs(written, load(written).find("tracks/track/feeds").get("currentVersion"),
                            expected_colour=feed_fields(written)["colour"],
                            source_pattern=source_media_pattern(real_clip), source_first=fields["first"])
    if any("source frame has" in d for d in diffs):
        pytest.xfail("known gap: EXR timecode not carried when the .clip XML has no <startTimecode>")
    pytest.fail("the EXR timecode is carried now: the gap is fixed -- drop this test and source_tc=False "
                "in tests/flame_roundtrip.py")
