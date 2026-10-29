"""CLIP_INFO: one dict carrying everything a reader knows about the clip it read, so a Writer
(or OpenClipClipInfo) takes it from one socket instead of eight.

Keys (all always present):
  clip_path       source .clip path; for OpenClipExrSequenceReader the sequence's folder
  clip_name       <name> from the .clip, never stripped; the filename stem for a sequence
  version_name    the version actually read ("" for a sequence)
  start_frame     first frame actually read
  start_timecode  "HH:MM:SS:FF" from the feed's <startTimecode>, else its first frame's EXR
                  smpte:TimeCode ("" when none)
  fps             exact frame rate (FLOAT)
  colour_space    see OpenClipReader for the lookup order ("" when none)
  format_version  Open Clip schema version ("" for a sequence)
  frame_padding   digits in the frame numbers
  bit_depth       "half"/"float" from the first frame's EXR header ("" for anything else, e.g. PNG)
  metadata        EXR header attributes of the first frame (what CLIP_METADATA carried)
For clip_report only:
  media_pattern, file_format ('EXR half PIZ'), width, height, frame_count,
  versions (list, [] for a sequence), current_version, aovs (list)
"""
from __future__ import annotations

import re

from ..lib import openclip_xml

_PADDING_RE = re.compile(r"%0?(\d*)d")


def padding_of(pattern: str) -> int:
    """Digits of the printf frame token in a media pattern (1 for '%d', 0 if none)."""
    m = _PADDING_RE.search(pattern)
    if not m:
        return 0
    return int(m.group(1)) if m.group(1) else 1


def bit_depth_of(file_format: str) -> str:
    """'half'/'float' from a describe_frame() text such as 'EXR half PIZ' (the first frame's
    header); "" for anything else (PNG, integer EXR)."""
    parts = file_format.split()
    return parts[1] if len(parts) > 1 and parts[0] == "EXR" and parts[1] in ("half", "float") else ""


def fps_text(fps: float) -> str:
    try:
        return openclip_xml.fps_label_from_float(fps)
    except ValueError:
        return f"{fps:g}"


def build_report(info: dict) -> str:
    is_clip = bool(info.get("format_version"))
    source = info["clip_path"] if is_clip else (info.get("media_pattern") or info["clip_path"])
    fmt_parts = []
    if is_clip:
        fmt_parts.append(f"Open Clip schema {info['format_version']}")
    if info.get("file_format"):
        fmt_parts.append(info["file_format"])
    fmt_parts.append(f"{info.get('width', 0)}×{info.get('height', 0)}")
    fmt_parts.append(f"{fps_text(info['fps'])} fps")

    start = info["start_frame"]
    count = info.get("frame_count", 0)
    end = start + count - 1
    tc = info.get("start_timecode") or "(none)"
    lines = [
        f"CLIP     {info['clip_name']}   ({source})",
        f"FORMAT   {' · '.join(fmt_parts)}",
        f"         colour: {info.get('colour_space') or '(none)'}",
        f"TIME     start TC {tc} · frames {start}–{end} ({count}) · padding {info.get('frame_padding', 0)}",
    ]

    if is_clip:
        versions = list(info.get("versions") or [])
        width = max((len(v) for v in versions), default=0)
        lines += ["", "VERSIONS"]
        for name in versions:
            tags = []
            if name == info.get("current_version"):
                tags.append("(current)")
            if name == info.get("version_name"):
                tags.append("← loaded")
            lines.append(f"  {name.ljust(width)}  {'  '.join(tags)}".rstrip() if tags else f"  {name}")

    lines += ["", "AOVS"]
    aovs = list(info.get("aovs") or [])
    lines += [f"  {a}" for a in aovs] if aovs else ["  (none)"]
    return "\n".join(lines)
