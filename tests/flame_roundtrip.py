"""Reader -> Writer round trips the way an artist wires them (clip_info connected, every Writer
field on auto), checked with tests/flame_compare. Shared by the synthetic Flame-like suite and
the real-clip fidelity suite, so both run exactly the same checks."""
from __future__ import annotations

from pathlib import Path

from ComfyUI_OpenClip.nodes.reader import OpenClipReader
from ComfyUI_OpenClip.nodes.writer import OpenClipWriter

from .conftest import named_result
from .flame_compare import (append_diffs, current_vuid, exr_attrs, expected_feed, exr_frame_diffs, feed_fields,
                            load, new_clip_diffs, source_media_pattern)


def read(clip, frame_cap: int = -1, start_frame: int = -1, end_frame: int = -1) -> dict:
    r = OpenClipReader().execute(clip_path=str(clip), version="current", start_frame=start_frame,
                                 end_frame=end_frame, path_from="", path_to="", frame_cap=frame_cap)
    return named_result(OpenClipReader, r["result"])


def write(r: dict, source_fields: dict, clip_path: str = "") -> str:
    """Writer with clip_info wired and every field on auto, bit depth and compression included
    (clip_info carries them, so the source's must come through without being set here); MASK is
    wired when the source has alpha."""
    return OpenClipWriter().execute(
        IMAGE=r["IMAGE"], MASK=r["MASK"] if source_fields["channels"] == 4 else None,
        clip_info=r["clip_info"], clip_path=clip_path, file_format="EXR",
    )[0]


def _source_exr(clip) -> tuple[str, str | None, int | None, int | None]:
    """(autodeskColorSpace, media pattern, first frame, smpte:TimeCode word) of the source feed's
    first frame."""
    pattern = source_media_pattern(clip)
    first = feed_fields(clip)["first"]
    if pattern is None or first is None:
        return "", None, None, None
    attrs = exr_attrs(pattern % first)
    tc = attrs.get("smpte:TimeCode")
    return attrs.get("autodeskColorSpace", ""), pattern, first, (tc[0] if tc is not None else None)


def new_clip_check(src_clip, out_dir, frame_cap: int = -1, start_frame: int = -1,
                   end_frame: int = -1) -> tuple[list[str], str]:
    """Read src_clip's current version (all of it, or the window start_frame..end_frame), write it
    as a new clip into out_dir, compare."""
    src = feed_fields(src_clip)
    exr_colour, pattern, src_first, tc_word = _source_exr(src_clip)
    r = read(src_clip, frame_cap, start_frame, end_frame)
    written = write(r, src, clip_path=str(out_dir))
    first, count = r["info.start_frame"], r["frame_count"]
    diffs = new_clip_diffs(src_clip, written, first=first, count=count, exr_colour=exr_colour, exr_tc_word=tc_word)
    want_colour = expected_feed(src, first=first, count=count, exr_colour=exr_colour)["colour"]
    diffs += exr_frame_diffs(written, current_vuid(load(written)), expected_colour=want_colour,
                             source_pattern=pattern, source_first=first if src_first is not None else None)
    return diffs, written


def append_check(src_clip, times: int = 1, frame_cap: int = -1) -> tuple[list[str], list[str]]:
    """Read the current version and append the result to the same .clip, `times` times (each
    later round reads the version the previous one added). Returns (diffs, new vuids)."""
    original = Path(src_clip).read_bytes()
    src_vuid = current_vuid(load(original))
    src = feed_fields(load(original), src_vuid)
    exr_colour, pattern, _, tc_word = _source_exr(src_clip)
    diffs, new_vuids = [], []
    for _ in range(times):
        r = read(src_clip, frame_cap)
        out = write(r, src)
        if Path(out) != Path(src_clip):
            diffs.append(f"append wrote {out!r}, not the source {str(src_clip)!r}")
            return diffs, new_vuids
        new_vuids.append(current_vuid(load(out)))
    first, count = r["info.start_frame"], r["frame_count"]
    diffs += append_diffs(original, src_clip, new_vuids, source_vuid=src_vuid, first=first, count=count,
                          exr_colour=exr_colour, exr_tc_word=tc_word)
    want_colour = expected_feed(src, first=first, count=count, exr_colour=exr_colour)["colour"]
    for v in new_vuids:
        diffs += exr_frame_diffs(src_clip, v, expected_colour=want_colour,
                                 source_pattern=pattern, source_first=first if pattern else None)
    return diffs, new_vuids
