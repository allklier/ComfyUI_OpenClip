"""Field-by-field comparison of a written .clip against the Flame clip it came from.

Used by the synthetic Flame-like suite (test_flame_like.py, smoke tier) and the real-clip
fidelity suite (test_flame_clips.py, full tier). Expectations are derived from the *source*
XML and EXRs, never from this project's own writer, so a bug shared by our Reader and Writer
(issue #7's lost timecode, colour tag, trimmed name) shows up as a difference.

Independent of lib/: the timecode label/BCD code and the frame-rate table here are separate
implementations on purpose (an oracle that reuses lib/timecode.py would agree with its bugs).
"""
from __future__ import annotations

import os
import re
from fractions import Fraction
from pathlib import Path
from typing import Optional

from lxml import etree

# Frame-rate labels this project writes as text (<editRate>23.976</editRate>); Flame writes
# numerator/denominator. Both compare as exact fractions.
RATE_LABELS = {
    "23.976": Fraction(24000, 1001), "24": Fraction(24), "25": Fraction(25),
    "29.97": Fraction(30000, 1001), "30": Fraction(30), "48": Fraction(48), "50": Fraction(50),
    "59.94": Fraction(60000, 1001), "60": Fraction(60),
}
WRITER_DEFAULT_COLOUR = "Rec.709 video"   # the Writer's documented default (nodes/CLAUDE.md)
WRITER_DEFAULT_RATE = Fraction(24)
_RANGE_RE = re.compile(r"\[(\d+)-(\d+)\]")
_DROP = {30: 2, 60: 4}


# --- timecode oracle (independent of lib/timecode.py) ---------------------------------------


def tc_label(ticks: int, rate: int, drop: bool) -> tuple[int, int, int, int]:
    """Real frame count since 00:00:00:00 -> (h, m, s, f) label. Drop-frame walks minute by
    minute: every minute not divisible by 10 starts its labels at frame 2 (4 at 60)."""
    if not drop:
        s, f = divmod(ticks, rate)
        return s // 3600, (s // 60) % 60, s % 60, f
    d = _DROP[rate]
    minute, rem = 0, ticks
    while True:
        skip = d if minute % 10 else 0
        if rem < rate * 60 - skip:
            break
        rem -= rate * 60 - skip
        minute += 1
    s, f = divmod(rem + (d if minute % 10 else 0), rate)
    return minute // 60, minute % 60, s, f


def tc_text(ticks: int, rate: int, drop: bool) -> str:
    h, m, s, f = tc_label(ticks, rate, drop)
    return f"{h:02d}:{m:02d}:{s:02d}{';' if drop else ':'}{f:02d}"


def tc_ticks(text: str, rate: int) -> int:
    """'HH:MM:SS:FF' / 'HH:MM:SS;FF' -> real frame count, by searching the oracle (slow but
    independent; fine for the handful of values the tests use)."""
    h, m, s, f = (int(x) for x in re.split(r"[:;]", text))
    drop = ";" in text
    if not drop:
        return ((h * 60 + m) * 60 + s) * rate + f
    d = _DROP[rate]
    minutes = h * 60 + m
    ticks = sum(rate * 60 - (d if mm % 10 else 0) for mm in range(minutes))
    return ticks + s * rate + f - (d if minutes % 10 else 0)


def bcd_encode(h: int, m: int, s: int, f: int, drop: bool) -> int:
    """SMPTE 12M time-and-flags word as OpenEXR stores it in smpte:TimeCode."""
    return ((f % 10) | (f // 10) << 4 | (1 << 6 if drop else 0) | (s % 10) << 8 | (s // 10) << 12
            | (m % 10) << 16 | (m // 10) << 20 | (h % 10) << 24 | (h // 10) << 28)


def bcd_decode(word: int) -> str:
    g = lambda shift, bits: (word >> shift) & ((1 << bits) - 1)
    drop = bool(word & (1 << 6))
    return (f"{g(28, 2)}{g(24, 4)}:{g(20, 3)}{g(16, 4)}:{g(12, 3)}{g(8, 4)}"
            f"{';' if drop else ':'}{g(4, 2)}{g(0, 4)}")


def exr_tc_field(word: Optional[int], rate: int) -> Optional[tuple[int, int, str]]:
    """A source frame's smpte:TimeCode word as a feed "tc" field (rate, ticks, dropMode), or None
    (no word, or a rate above 30, where no EXR timecode is read)."""
    if word is None or rate > 30:
        return None
    text = bcd_decode(word)
    return rate, tc_ticks(text, rate), "DF" if ";" in text else "NDF"


# --- reading the fields Flame uses ------------------------------------------------------------


def load(path_or_bytes) -> etree._Element:
    parser = etree.XMLParser(remove_blank_text=True)
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return etree.fromstring(path_or_bytes, parser)
    return etree.parse(str(path_or_bytes), parser).getroot()


def video_track(root: etree._Element) -> etree._Element:
    """The track Flame shows as the picture: first <trackType>video</trackType>, else first."""
    tracks = root.findall("tracks/track")
    return next((t for t in tracks if (t.findtext("trackType") or "").strip() == "video"), tracks[0])


def current_vuid(root: etree._Element) -> str:
    feeds = video_track(root).find("feeds")
    vers = root.find("versions")
    return (feeds.get("currentVersion") or (vers.get("currentVersion") if vers is not None else None)
            or feeds.find("feed").get("vuid"))


def _rate(el: Optional[etree._Element]) -> Optional[Fraction]:
    if el is None:
        return None
    num, den = el.findtext("numerator"), el.findtext("denominator")
    if num is not None and den is not None:
        return Fraction(int(num), int(den))
    text = (el.text or "").strip()
    return RATE_LABELS.get(text) if text else None


def feed_fields(clip, vuid: Optional[str] = None) -> dict:
    """What Flame uses from one feed of the picture track, as plain values."""
    root = clip if isinstance(clip, etree._Element) else load(clip)
    track = video_track(root)
    vuid = vuid or current_vuid(root)
    feed = next(f for f in track.findall("feeds/feed") if f.get("vuid") == vuid)
    tc = feed.find("startTimecode")
    path_el = feed.find("spans/span/path")
    if path_el is None:
        path_el = feed.find("spans/span/paths/path")
    path = (path_el.text or "").strip()
    m = _RANGE_RE.search(path)
    sf = feed.find("storageFormat")
    txt = lambda tag: (sf.findtext(tag) or "").strip() if sf is not None and sf.find(tag) is not None else None
    name_el = root.find("name")
    return {
        "schema": root.get("version"),
        "name": name_el.text if name_el is not None else None,
        "edit_rate": _rate(track.find("editRate")),
        "sample_rate": _rate(feed.find("sampleRate")),
        "tc": None if tc is None else (int(tc.findtext("rate")), int(tc.findtext("nbTicks")),
                                       (tc.findtext("dropMode") or "NDF").strip()),
        "colour": txt("colourSpace"),
        "width": int(txt("width")) if txt("width") else None,
        "height": int(txt("height")) if txt("height") else None,
        "channels": int(txt("nbChannels")) if txt("nbChannels") else None,
        "depth": int(txt("channelsDepth")) if txt("channelsDepth") else None,
        "path": path,
        "padding": len(m.group(1)) if m else None,
        "first": int(m.group(1)) if m else None,
        "last": int(m.group(2)) if m else None,
        "media_stem": Path(path[:m.start()] if m else path).name,
        "ext": path.rsplit(".", 1)[-1].lower(),
    }


def source_media_pattern(clip_path, vuid: Optional[str] = None) -> Optional[str]:
    """Absolute printf pattern of a feed's frames ('/x/a_v0.%06d.exr'), or None if no range."""
    f = feed_fields(clip_path, vuid)
    if f["padding"] is None:
        return None
    path = _RANGE_RE.sub(f"%0{f['padding']}d", f["path"])
    return path if os.path.isabs(path) else str(Path(clip_path).parent / path)


# --- expectations ----------------------------------------------------------------------------


def expected_feed(source: dict, *, first: int, count: int, exr_colour: str = "",
                  exr_tc_word: Optional[int] = None) -> dict:
    """What the Writer should write for a version made from `source` (feed_fields of the version
    read), Reader -> Writer with clip_info and every field on auto. `exr_tc_word`: the
    smpte:TimeCode of the source feed's first frame, the start timecode when the XML has none."""
    rate = source["edit_rate"] or source["sample_rate"] or WRITER_DEFAULT_RATE
    tc_rate = round(rate)
    tc = source["tc"] or exr_tc_field(exr_tc_word, tc_rate) or (tc_rate, 0, "NDF")
    return {
        "edit_rate": rate,
        "sample_rate": rate,
        "tc": tc,
        "colour": source["colour"] or exr_colour or WRITER_DEFAULT_COLOUR,
        "width": source["width"],
        "height": source["height"],
        "depth": source["depth"],  # the Writer's bit depth on auto (clip_info's)
        "padding": source["padding"],
        "first": first,
        "last": first + count - 1,
        "ext": "exr",
    }


def feed_diffs(expected: dict, written: dict, where: str) -> list[str]:
    out = []
    for key, want in expected.items():
        if want is None:  # the source doesn't say (e.g. Autodesk examples have no storageFormat)
            continue
        got = written.get(key)
        if got != want:
            out.append(f"{where}: {key} is {got!r}, source says {want!r}")
    return out


def new_clip_diffs(source_clip, written_clip, *, first: int, count: int, exr_colour: str = "",
                   source_vuid: Optional[str] = None, exr_tc_word: Optional[int] = None) -> list[str]:
    """A brand-new clip written from source_clip's version `source_vuid` (default current)."""
    src = feed_fields(source_clip, source_vuid)
    root = load(written_clip)
    vuid = current_vuid(root)
    out = feed_diffs(expected_feed(src, first=first, count=count, exr_colour=exr_colour, exr_tc_word=exr_tc_word),
                     feed_fields(root, vuid), f"new clip feed {vuid}")
    src_name = src["name"] if (src["name"] or "").strip() else Path(source_clip).stem
    want_schema = src["schema"] if src["schema"] in ("5", "6", "7", "8", "9") else "6"
    if root.findtext("name") != src_name:
        out.append(f"new clip: <name> is {root.findtext('name')!r}, source says {src_name!r}")
    if root.get("version") != want_schema:
        out.append(f"new clip: schema is {root.get('version')!r}, source says {want_schema!r}")
    if Path(written_clip).name != f"{src_name}.clip":
        out.append(f"new clip: file is {Path(written_clip).name!r}, expected {src_name + '.clip'!r}")
    media = feed_fields(root, vuid)["media_stem"]
    if media != f"{src_name}_{vuid}.":
        out.append(f"new clip: media named {media!r}, expected Flame's {src_name + '_' + vuid + '.'!r}")
    return out


def append_diffs(original: bytes, appended_clip, new_vuids: list[str], *, source_vuid: str,
                 first: int, count: int, exr_colour: str = "", exr_tc_word: Optional[int] = None) -> list[str]:
    """`appended_clip` after appending `new_vuids` (in order) to the clip whose bytes were
    `original`: every original element unchanged (only the two currentVersion attributes move),
    exactly the new feeds/versions added, and each new feed matching the source version."""
    orig = load(original)
    new = load(appended_clip)
    out = []
    o_track, n_track = video_track(orig), video_track(new)
    o_vuids = [f.get("vuid") for f in o_track.findall("feeds/feed")]
    n_vuids = [f.get("vuid") for f in n_track.findall("feeds/feed")]
    if n_vuids != o_vuids + new_vuids:
        out.append(f"append: feeds are {n_vuids}, expected {o_vuids + new_vuids}")
    o_vers, n_vers = orig.find("versions"), new.find("versions")
    o_uids = [v.get("uid") for v in o_vers.findall("version")] if o_vers is not None else o_vuids
    n_uids = [v.get("uid") for v in n_vers.findall("version")] if n_vers is not None else []
    if n_uids != o_uids + new_vuids:
        out.append(f"append: <versions> lists {n_uids}, expected {o_uids + new_vuids}")
    last = new_vuids[-1]
    for what, el in (("feeds", n_track.find("feeds")), ("versions", n_vers)):
        if el is not None and el.get("currentVersion") != last:
            out.append(f"append: <{what} currentVersion> is {el.get('currentVersion')!r}, expected {last!r}")
    for v in new_vuids:
        ver = next((x for x in (n_vers.findall("version") if n_vers is not None else []) if x.get("uid") == v), None)
        if ver is None or ver.findtext("name") != v:
            out.append(f"append: <version uid={v!r}> has no <name>{v}</name>")

    # Untouched: strip what the append may change, then the rest must be identical.
    stripped = load(etree.tostring(new))
    s_track = video_track(stripped)
    for f in s_track.findall("feeds/feed"):
        if f.get("vuid") in new_vuids:
            f.getparent().remove(f)
    s_vers = stripped.find("versions")
    if o_vers is None and s_vers is not None:
        stripped.remove(s_vers)
    elif s_vers is not None:
        for v in s_vers.findall("version"):
            if v.get("uid") in new_vuids:
                s_vers.remove(v)
    for o_el, s_el in ((o_track.find("feeds"), s_track.find("feeds")), (o_vers, stripped.find("versions"))):
        if o_el is None or s_el is None:
            continue
        if o_el.get("currentVersion") is None:
            s_el.attrib.pop("currentVersion", None)
        else:
            s_el.set("currentVersion", o_el.get("currentVersion"))
    diff = first_difference(orig, stripped)
    if diff:
        out.append(f"append changed an original element: {diff}")

    src = feed_fields(orig, source_vuid)
    want = expected_feed(src, first=first, count=count, exr_colour=exr_colour, exr_tc_word=exr_tc_word)
    want["edit_rate"] = src["edit_rate"]  # the track's own <editRate>, which an append leaves alone
    for v in new_vuids:
        out += feed_diffs(want, feed_fields(new, v), f"appended feed {v}")
    return out


def first_difference(a: etree._Element, b: etree._Element, path: str = "") -> Optional[str]:
    here = f"{path}/{a.tag}" + (f"[{a.get('vuid') or a.get('uid')}]" if a.get("vuid") or a.get("uid") else "")
    if a.tag != b.tag:
        return f"{here}: tag {a.tag!r} became {b.tag!r}"
    if dict(a.attrib) != dict(b.attrib):
        return f"{here}: attributes {dict(a.attrib)} became {dict(b.attrib)}"
    if (a.text or "").strip() != (b.text or "").strip():
        return f"{here}: text {a.text!r} became {b.text!r}"
    if len(a) != len(b):
        return f"{here}: {len(a)} children became {len(b)} ({[c.tag for c in a]} -> {[c.tag for c in b]})"
    for ca, cb in zip(a, b):
        d = first_difference(ca, cb, here)
        if d:
            return d
    return None


def next_flame_version(existing: list[str]) -> str:
    """Flame's numbering continued: v0 -> v1, v01 -> v02, v005 -> v006; v001 when none is numbered."""
    nums = [(int(m.group(1)), len(m.group(1))) for v in existing if (m := re.fullmatch(r"v(\d+)", v))]
    if not nums:
        return "v001"
    n, width = max(nums)
    return f"v{n + 1:0{width}d}"


# --- EXR headers -----------------------------------------------------------------------------


def exr_attrs(path) -> dict:
    import OpenImageIO as oiio
    inp = oiio.ImageInput.open(str(path))
    assert inp, f"can't open {path}"
    try:
        spec = inp.spec()
        out = {a.name: a.value for a in spec.extra_attribs}
        out["_channels"] = tuple(spec.channelnames)
        return out
    finally:
        inp.close()


def exr_pixels(path) -> dict:
    """{channel name: float32 array} of an EXR's first part."""
    import OpenImageIO as oiio
    inp = oiio.ImageInput.open(str(path))
    assert inp, f"can't open {path}"
    try:
        px = inp.read_image(oiio.FLOAT)
        return {n: px[:, :, k] for k, n in enumerate(inp.spec().channelnames)}
    finally:
        inp.close()


def pixel_diffs(written, source) -> list[str]:
    """R/G/B (and A when the written frame has it) must equal the source frame exactly: the
    Reader -> Writer path is lossless at the source's own bit depth (PIZ/ZIP)."""
    import numpy as np
    w, s = exr_pixels(written), exr_pixels(source)
    out = []
    for ch in ("R", "G", "B", "A"):
        if ch not in w:
            continue
        if ch not in s:
            if ch == "A" and np.all(w["A"] == 1.0):
                continue  # no alpha in the source: the Reader's opaque MASK
            out.append(f"{Path(written).name}: channel {ch} not in the source frame")
        elif not np.array_equal(w[ch], s[ch]):
            out.append(f"{Path(written).name}: channel {ch} differs from {Path(source).name} "
                       f"(max abs diff {float(np.max(np.abs(w[ch] - s[ch]))):.3g})")
    return out


def exr_frame_diffs(written_clip, vuid: str, *, expected_colour: str,
                    source_pattern: Optional[str] = None, source_first: Optional[int] = None) -> list[str]:
    """Per written frame: smpte:TimeCode = the feed's start timecode + offset (rates <= 30 only;
    the BCD frames field holds 0-39), autodeskColorSpace/openclip:ColorSpace = the colour tag; and,
    when the source frames are given, the same timecode as the source's frame at that offset
    (also when the source's XML has no <startTimecode> and only its EXRs carry one)."""
    root = load(written_clip)
    f = feed_fields(root, vuid)
    pattern = _RANGE_RE.sub(f"%0{f['padding']}d", f["path"])
    rate, ticks, mode = f["tc"]
    drop = mode == "DF"
    out = []
    for i, frame in enumerate(range(f["first"], f["last"] + 1)):
        path = pattern % frame
        attrs = exr_attrs(path)
        tc = attrs.get("smpte:TimeCode")
        if rate <= 30:
            want = tc_text(ticks + i, rate, drop)
            got = bcd_decode(tc[0]) if tc is not None else None
            if got != want:
                out.append(f"{Path(path).name}: smpte:TimeCode {got!r}, feed start + {i} is {want!r}")
            if source_pattern is not None:
                src_tc = exr_attrs(source_pattern % (source_first + i)).get("smpte:TimeCode")
                if src_tc is not None and tc is not None and src_tc[0] != tc[0]:
                    out.append(f"{Path(path).name}: smpte:TimeCode {bcd_decode(tc[0])!r}, source frame has "
                               f"{bcd_decode(src_tc[0])!r}")
        elif tc is not None:
            out.append(f"{Path(path).name}: smpte:TimeCode written at rate {rate} (can't be stored)")
        keys = ["openclip:ColorSpace"]
        if "autodeskColorSpace" in attrs or (
                source_pattern is not None and "autodeskColorSpace" in exr_attrs(source_pattern % source_first)):
            keys.append("autodeskColorSpace")  # Flame's own key: kept whenever the source had it
        if source_pattern is not None:
            out += pixel_diffs(path, source_pattern % (source_first + i))
        for key in keys:
            if attrs.get(key) != expected_colour:
                out.append(f"{Path(path).name}: {key} {attrs.get(key)!r}, expected {expected_colour!r}")
    return out
