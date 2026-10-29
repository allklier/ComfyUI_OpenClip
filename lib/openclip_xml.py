from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from lxml import etree

from . import timecode

MAX_VERSIONS = 1000
MAX_SPANS = 100

# Matches Flame's range notation inside a path: file.[000001-000100].exr
_RANGE_RE = re.compile(r'\[(\d+)-(\d+)\]')
# Matches a printf-style frame specifier: %04d, %06d, etc.
_PRINTF_RE = re.compile(r'%0(\d+)d')

# Rational frame-rate table: label → (numerator, denominator)
FPS_OPTIONS = ["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60"]
_FPS_TABLE: dict[str, tuple[int, int]] = {
    "23.976": (24000, 1001),
    "24":     (24, 1),
    "25":     (25, 1),
    "29.97":  (30000, 1001),
    "30":     (30, 1),
    "48":     (48, 1),
    "50":     (50, 1),
    "59.94":  (60000, 1001),
    "60":     (60, 1),
}

_BIT_DEPTH_INT: dict[str, int] = {
    "half (16-bit)":  16,
    "float (32-bit)": 32,
}
_BIT_DEPTH_LABEL: dict[int, str] = {v: k for k, v in _BIT_DEPTH_INT.items()}


@dataclass
class ClipSpan:
    path: str
    start_frame: int
    duration: int


@dataclass
class ClipVersion:
    uid: str
    name: str
    spans: list[ClipSpan] = field(default_factory=list)
    publish_path: Optional[str] = None  # relative to .clip file; written into version userData
    # "HH:MM:SS:FF" from <feed><startTimecode> ("" when the feed has none); written back on
    # generate/append, "" writing 00:00:00:00.
    start_timecode: str = ""
    colour_space: str = ""  # <feed><storageFormat><colourSpace>, "" when absent (parse only)


@dataclass
class ClipFormat:
    """Image format metadata written into <storageFormat>, <editRate>, etc."""
    width: int = 0
    height: int = 0
    n_channels: int = 3
    bit_depth: str = "half (16-bit)"
    fps: str = "24"
    colour_space: str = "Rec.1886 Rec.709 - Display"
    file_format: str = "EXR"    # written as <fileFormat> in storageFormat
    compression: str = "ZIP"    # written as <compression> in storageFormat (EXR only)


@dataclass
class ParsedClip:
    schema_version: str
    clip_name: str
    current_version: str
    versions: dict[str, ClipVersion]


def parse(clip_path: str) -> ParsedClip:
    path = Path(clip_path)
    if not path.exists():
        raise FileNotFoundError(f"Clip file not found: {clip_path}")

    root = _load(path).getroot()

    schema_version = root.get("version", "8")

    # Most of Autodesk's own example clips have no root <name>; fall back to the file stem.
    # Never stripped: Flame keeps leading/trailing spaces in clip names (issue #7).
    name_el = root.find("name")
    clip_name = (name_el.text or "") if name_el is not None else ""

    return _parse_clip(root, clip_name if clip_name.strip() else path.stem, schema_version, clip_path)


def resolve_version(clip: ParsedClip, version: str) -> ClipVersion:
    name = clip.current_version if version == "current" else version
    if name not in clip.versions:
        available = list(clip.versions.keys())
        raise ValueError(f"Version '{name}' not found in clip. Available: {available}")
    return clip.versions[name]


_VERSION_NUM_RE = re.compile(r'^v(\d+)$')


def next_version(existing_versions: dict) -> str:
    """Compute the next sequential version name (v001, v002, ...) given a clip's existing versions.

    Only names matching Flame's v<digits> convention count toward the sequence;
    other names are ignored for numbering. Padding width matches the highest
    matching version found (e.g. v009 -> v010); defaults to 'v001' (3-digit)
    when there are no matching versions yet.
    """
    numbered = []
    for name in existing_versions:
        m = _VERSION_NUM_RE.match(name)
        if m:
            numbered.append((int(m.group(1)), len(m.group(1))))
    if not numbered:
        return "v001"
    max_num, padding = max(numbered, key=lambda t: t[0])
    return f"v{max_num + 1:0{padding}d}"


def fps_to_rational(fps: str) -> tuple[int, int]:
    """Return (numerator, denominator) for a frame-rate label, defaulting to 24/1."""
    return _FPS_TABLE.get(fps, (24, 1))


_FPS_MATCH_TOLERANCE = 1e-3


def fps_label_from_float(value: float, tol: float = _FPS_MATCH_TOLERANCE) -> str:
    """Match a numeric fps (e.g. from a node graph FLOAT input) to its FPS_OPTIONS label.

    NTSC rates are exact rationals (24000/1001, not 23.976), so this compares
    against each label's true rational value rather than a parsed decimal.
    Raises ValueError if no supported rate is within tolerance.
    """
    best_label, best_diff = None, None
    for label in FPS_OPTIONS:
        num, den = _FPS_TABLE[label]
        diff = abs((num / den) - value)
        if best_diff is None or diff < best_diff:
            best_label, best_diff = label, diff
    if not best_diff <= tol:  # written this way round so NaN is rejected too
        raise ValueError(f"fps {value} does not match any supported rate: {FPS_OPTIONS}")
    return best_label


def read_format(clip_path: str) -> ClipFormat:
    """Extract ClipFormat from the storageFormat and editRate of an existing clip."""
    path = Path(clip_path)
    if not path.exists():
        raise FileNotFoundError(f"Clip file not found: {clip_path}")
    root = _load(path).getroot()
    # Same track the reader reads, so a multi-track clip's audio/AOV tracks never supply the format.
    track = _select_track(root, clip_path) if root.find("tracks") is not None else root

    def _t(el: etree._Element, tag: str, default: str = "") -> str:
        child = el.find(tag)
        return (child.text or default).strip() if child is not None else default

    # The current version's feed: Flame keeps versions of different sizes in one clip, so the
    # first feed is not "the clip's format" (was track.find(".//storageFormat"), fixed 2026-10-29).
    feeds_el = track.find("feeds")
    feed = None
    if feeds_el is not None:
        vers_el = root.find("versions")
        cur = feeds_el.get("currentVersion") or (vers_el.get("currentVersion") if vers_el is not None else None)
        feed = next((f for f in feeds_el.findall("feed") if f.get("vuid") == cur), None)
    # Many Autodesk example clips carry no <storageFormat> at all; unknown fields stay at
    # 0/"" so the writer's mismatch check skips them.
    storage = (feed if feed is not None else track).find(".//storageFormat")
    has_storage = storage is not None
    if not has_storage:
        storage = etree.Element("storageFormat")
    depth_int = int(_t(storage, "channelsDepth") or "16")
    bit_depth = _BIT_DEPTH_LABEL.get(depth_int, "half (16-bit)")
    file_format = _t(storage, "fileFormat", "exr" if has_storage else "").upper()
    compression = _t(storage, "compression", "ZIP" if has_storage else "").upper()
    colour_space = _t(storage, "colourSpace")
    width = int(_t(storage, "width") or "0")
    height = int(_t(storage, "height") or "0")
    n_channels = int(_t(storage, "nbChannels") or "3")

    fps = "24"
    er = track.find(".//editRate")
    if er is not None:
        if er.text and er.text.strip():
            candidate = er.text.strip()
            fps = candidate if candidate in FPS_OPTIONS else "24"
        else:
            num_el, den_el = er.find("numerator"), er.find("denominator")
            if num_el is not None and den_el is not None:
                num, den = int(num_el.text or "24"), int(den_el.text or "1")
                fps = next((k for k, v in _FPS_TABLE.items() if v == (num, den)), "24")

    return ClipFormat(
        width=width, height=height, n_channels=n_channels,
        bit_depth=bit_depth, fps=fps, colour_space=colour_space,
        file_format=file_format, compression=compression,
    )


def generate(
    clip_name: str,
    versions: dict[str, ClipVersion],
    current_version: str,
    fmt: Optional[ClipFormat] = None,
    schema_version: str = "6",
) -> bytes:
    """Build a brand-new clip. Appending to an existing one goes through append_version()."""
    if fmt is None:
        fmt = ClipFormat()

    now = _now()

    root = etree.Element("clip", type="clip", version=schema_version)
    etree.SubElement(root, "name", type="string").text = clip_name

    root_ud = etree.SubElement(root, "userData", type="dict")
    etree.SubElement(root_ud, "appName", type="string").text = "ComfyUI"

    tracks = etree.SubElement(root, "tracks")
    track = etree.SubElement(tracks, "track", uid="v0")
    etree.SubElement(track, "trackType").text = "video"
    etree.SubElement(track, "editRate", type="rate").text = fmt.fps

    feeds = etree.SubElement(track, "feeds", currentVersion=current_version)
    for i, (uid, version) in enumerate(versions.items()):
        if i >= MAX_VERSIONS:
            raise RuntimeError(f"Version count exceeds MAX_VERSIONS={MAX_VERSIONS}")
        feeds.append(_build_feed(uid, version, fmt))

    _append_versions_block(root, versions, current_version, now)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def append_version(clip_path: str, version: ClipVersion, fmt: ClipFormat) -> bytes:
    """Add (or replace, when its uid already exists) one version in an existing clip, in place.

    Only the version's <feed> (in the track the reader reads) and its top-level <version>
    entry are touched; everything else -- the root schema version, track names, Flame's
    userData, other tracks, elements this module knows nothing about -- is kept as-is.
    A replaced entry keeps its position. Whitespace is re-indented on output.
    """
    path = Path(clip_path)
    root = _load(path, etree.XMLParser(remove_blank_text=True)).getroot()
    uid = version.uid

    feeds = _require(_select_track(root, clip_path), "feeds")
    _put(feeds, "feed", "vuid", uid, _build_feed(uid, version, fmt))
    feeds.set("currentVersion", uid)

    vers_el = root.find("versions")
    if vers_el is None:
        # List the clip's existing feeds too, as bare <version uid=".."/> entries the way
        # Autodesk's One_Track_Two_Versions.clip does, so they aren't left without one.
        vers_el = etree.SubElement(root, "versions")
        for feed in feeds.findall("feed"):
            if feed.get("vuid") != uid:
                etree.SubElement(vers_el, "version", uid=feed.get("vuid"))
    old = next((v for v in vers_el.findall("version") if v.get("uid") == uid), None)
    number = vers_el.index(old) + 1 if old is not None else len(vers_el.findall("version")) + 1
    _put(vers_el, "version", "uid", uid, _build_version(uid, version, _now(), number))
    vers_el.set("currentVersion", uid)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# --- private helpers ---


def _new_uid() -> str:
    return uuid.uuid4().hex


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %I:%M:%S %p")


def _load(path: Path, parser: Optional[etree.XMLParser] = None) -> etree._ElementTree:
    try:
        return etree.parse(str(path), parser)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"{path} is not valid XML: {e}") from e


def _select_track(root: etree._Element, clip_path: str) -> etree._Element:
    """The track the reader reads: the first video track, else the first track."""
    tracks_el = root.find("tracks")
    if tracks_el is None and root.find("handler") is not None:
        raise ValueError(
            f"{clip_path}: pattern-based clips (<handler>/ScanPattern, no <tracks>) are not supported"
        )
    tracks = _require(root, "tracks").findall("track")
    if not tracks:
        raise ValueError("Missing <track> inside <tracks>")
    for track in tracks:
        if (track.findtext("trackType") or "").strip().lower() == "video":
            return track
    return tracks[0]


def _put(parent: etree._Element, tag: str, key: str, value: str, new_el: etree._Element) -> None:
    """Replace parent's <tag key=value> child in place, or append new_el if there is none."""
    for i, child in enumerate(parent):
        if child.tag == tag and child.get(key) == value:
            parent[i] = new_el
            return
    parent.append(new_el)


def _build_feed(uid: str, version: ClipVersion, fmt: ClipFormat) -> etree._Element:
    feed = etree.Element("feed", vuid=uid, uid=_new_uid())

    is_exr = fmt.file_format.upper() == "EXR"

    storage = etree.SubElement(feed, "storageFormat", type="format")
    etree.SubElement(storage, "type").text = "video"
    if is_exr and fmt.bit_depth in _BIT_DEPTH_INT:
        etree.SubElement(storage, "channelsDepth", type="uint").text = str(_BIT_DEPTH_INT[fmt.bit_depth])
        etree.SubElement(storage, "channelsEncoding", type="string").text = "Float"
    elif not is_exr:
        etree.SubElement(storage, "channelsDepth", type="uint").text = "8"
        etree.SubElement(storage, "channelsEncoding", type="string").text = "Integer"
    etree.SubElement(storage, "channelsEndianess", type="string").text = "Little Endian"
    if fmt.colour_space:
        etree.SubElement(storage, "colourSpace", type="string").text = fmt.colour_space
    if is_exr and fmt.compression:
        etree.SubElement(storage, "compression", type="string").text = fmt.compression
    etree.SubElement(storage, "fieldDominance", type="int").text = "2"
    etree.SubElement(storage, "fileFormat", type="string").text = fmt.file_format.lower()
    if fmt.height > 0:
        etree.SubElement(storage, "height", type="uint").text = str(fmt.height)
    etree.SubElement(storage, "nbChannels", type="uint").text = str(fmt.n_channels)
    pixel_layout = "RGBA" if fmt.n_channels == 4 else "RGB"
    etree.SubElement(storage, "pixelLayout", type="string").text = pixel_layout
    etree.SubElement(storage, "pixelRatio", type="float").text = "1"
    if fmt.width > 0:
        etree.SubElement(storage, "width", type="uint").text = str(fmt.width)

    # sampleRate — scalar, no type attribute (teens.clip pattern)
    etree.SubElement(feed, "sampleRate").text = fmt.fps

    fps_num, fps_den = fps_to_rational(fmt.fps)
    fps_int = timecode.tc_rate(fps_num / fps_den)
    ticks, drop = timecode.parse_tc(version.start_timecode or timecode.ZERO, fps_int)
    tc = etree.SubElement(feed, "startTimecode", type="time")
    etree.SubElement(tc, "rate").text = str(fps_int)
    etree.SubElement(tc, "nbTicks").text = str(ticks)
    # "DF" is assumed to be Flame's drop-frame spelling (only "NDF" seen in real clips).
    etree.SubElement(tc, "dropMode").text = "DF" if drop else "NDF"

    if version.spans:
        etree.SubElement(feed, "startFrame").text = str(version.spans[0].start_frame)

    spans_el = etree.SubElement(feed, "spans")
    for j, span in enumerate(version.spans):
        if j >= MAX_SPANS:
            raise RuntimeError(f"Span count exceeds MAX_SPANS={MAX_SPANS}")
        span_el = etree.SubElement(spans_el, "span")
        etree.SubElement(span_el, "duration").text = str(span.duration)
        etree.SubElement(span_el, "trackIndex").text = "0"
        range_path = _printf_to_range(span.path, span.start_frame, span.duration)
        etree.SubElement(span_el, "path", encoding="pattern").text = range_path

    return feed


def _append_versions_block(
    root: etree._Element,
    versions: dict[str, ClipVersion],
    current_version: str,
    now: str,
) -> None:
    vers_el = etree.SubElement(root, "versions", currentVersion=current_version)
    for i, (uid, version) in enumerate(versions.items(), start=1):
        vers_el.append(_build_version(uid, version, now, i))


def _build_version(uid: str, version: ClipVersion, now: str, number: int) -> etree._Element:
    ver_el = etree.Element("version", uid=uid)
    etree.SubElement(ver_el, "name").text = version.name
    etree.SubElement(ver_el, "creationDate").text = now
    ud = etree.SubElement(ver_el, "userData", type="dict")
    etree.SubElement(ud, "appName", type="string").text = "ComfyUI"
    etree.SubElement(ud, "versionNumber", type="uint64").text = str(number)
    if version.publish_path is not None:
        etree.SubElement(ud, "comfyWorkflow", path=version.publish_path)
    return ver_el


def _parse_clip(root: etree._Element, clip_name: str, schema_version: str, clip_path: str) -> ParsedClip:
    """Parse the clip body, shared by every schema version.

    Confirmed against real Flame-exported clips (schema versions 6, 7, 8,
    and 9): media always lives at tracks/track/feeds/feed, and the
    top-level <versions> block (metadata only: name, creationDate,
    batchSetup, etc.) is always a sibling of <tracks>, never nested inside
    <track>. There is no structural difference between schema versions.

    Reads the first video track (Autodesk's examples include audio and
    multi-layer tracks). A <feeds> without currentVersion (common in those
    examples) falls back to the top-level <versions currentVersion>, then to
    the first feed.
    """
    feeds_el = _require(_select_track(root, clip_path), "feeds")
    versions_el = root.find("versions")
    versions: dict[str, ClipVersion] = {}

    for i, feed in enumerate(feeds_el.findall("feed")):
        if i >= MAX_VERSIONS:
            raise RuntimeError(f"Version count exceeds MAX_VERSIONS={MAX_VERSIONS}")
        uid = feed.get("vuid") or ""
        if not uid:
            raise ValueError(f"Feed at index {i} is missing 'vuid' attribute")
        spans = _parse_spans(feed, uid)
        versions[uid] = ClipVersion(
            uid=uid, name=uid, spans=spans,
            start_timecode=_parse_start_timecode(feed),
            colour_space=(feed.findtext("storageFormat/colourSpace") or "").strip(),
        )

    current_version = (
        feeds_el.get("currentVersion")
        or (versions_el.get("currentVersion") if versions_el is not None else None)
        or next(iter(versions), "")
    )

    if versions_el is not None:
        for ver_el in versions_el.findall("version"):
            uid = ver_el.get("uid", "")
            if uid in versions:
                ud = ver_el.find("userData")
                if ud is not None:
                    cw = ud.find("comfyWorkflow")
                    if cw is not None:
                        versions[uid].publish_path = cw.get("path")

    return ParsedClip(
        schema_version=schema_version,
        clip_name=clip_name,
        current_version=current_version,
        versions=versions,
    )


def _parse_start_timecode(feed: etree._Element) -> str:
    """<startTimecode><rate/><nbTicks/><dropMode/> -> 'HH:MM:SS:FF', or '' when absent."""
    tc = feed.find("startTimecode")
    if tc is None:
        return ""
    try:
        rate = int((tc.findtext("rate") or "").strip())
        ticks = int((tc.findtext("nbTicks") or "0").strip())
    except ValueError:
        return ""
    drop = (tc.findtext("dropMode") or "NDF").strip().upper() == "DF"
    try:
        return timecode.format_tc(ticks, rate, drop and rate in (30, 60))
    except ValueError:
        return ""


def _parse_spans(feed: etree._Element, version_uid: str) -> list[ClipSpan]:
    spans_el = _require(feed, "spans")
    spans: list[ClipSpan] = []

    for i, span in enumerate(spans_el.findall("span")):
        if i >= MAX_SPANS:
            raise RuntimeError(f"Span count exceeds MAX_SPANS={MAX_SPANS}")
        path_el = span.find("path")
        if path_el is None:
            # Stereo: <paths><path subFeedId="Left">…<path subFeedId="Right">…; read the first eye.
            path_el = span.find("paths/path")
        if path_el is None:
            raise ValueError(f"Span {i} in version '{version_uid}' is missing <path>")

        raw_path = (path_el.text or "").strip()
        start_el = span.find("startFrame")
        dur_el = span.find("duration")

        if start_el is not None and dur_el is not None:
            # Legacy: explicit startFrame + duration inside span, printf-style path
            spans.append(ClipSpan(
                path=raw_path,
                start_frame=int(start_el.text or 0),
                duration=int(dur_el.text or 0),
            ))
        elif _RANGE_RE.search(raw_path):
            # Standard: range encoded in path as file.[000001-000100].exr
            path, start_frame, duration = _parse_range_path(raw_path, version_uid)
            spans.append(ClipSpan(path=path, start_frame=start_frame, duration=duration))
        else:
            # Static single image (Flame encoding="file", e.g. a still or screenshot):
            # no sequence, so there is no frame range to parse.
            spans.append(ClipSpan(path=raw_path, start_frame=1, duration=1))

    return spans


def _parse_range_path(raw_path: str, version_uid: str) -> tuple[str, int, int]:
    """Convert a Flame range path like 'file.[000001-000100].exr'
    to (printf_pattern, start_frame, duration)."""
    m = _RANGE_RE.search(raw_path)
    if not m:
        raise ValueError(
            f"Span in version '{version_uid}': path '{raw_path}' has no range notation "
            f"[NNNN-NNNN] and no separate <startFrame>/<duration> elements"
        )
    start = int(m.group(1))
    end = int(m.group(2))
    if end < start:
        raise ValueError(
            f"Span in version '{version_uid}': range end {end} < start {start}"
        )
    padding = len(m.group(1))
    printf_path = raw_path[:m.start()] + f"%0{padding}d" + raw_path[m.end():]
    return printf_path, start, end - start + 1


def _printf_to_range(pattern: str, start_frame: int, duration: int) -> str:
    """Convert 'shot.%04d.exr' + start + dur to 'shot.[001001-001273].exr'."""
    m = _PRINTF_RE.search(pattern)
    if not m:
        return pattern  # already range notation or static path — pass through
    padding = int(m.group(1))
    end = start_frame + duration - 1
    range_str = f"[{start_frame:0{padding}d}-{end:0{padding}d}]"
    return pattern[:m.start()] + range_str + pattern[m.end():]


def _require(parent: etree._Element, tag: str) -> etree._Element:
    el = parent.find(tag)
    if el is None:
        raise ValueError(f"Missing <{tag}> inside <{parent.tag}>")
    return el
