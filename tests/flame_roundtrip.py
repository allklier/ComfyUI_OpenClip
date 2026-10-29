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

_DEPTH = {16: "half (16-bit)", 32: "float (32-bit)"}


def read(clip, frame_cap: int = -1) -> dict:
    r = OpenClipReader().execute(clip_path=str(clip), version="current", start_frame=-1, end_frame=-1,
                                 path_from="", path_to="", frame_cap=frame_cap)
    return named_result(OpenClipReader, r["result"])


def write(r: dict, source_fields: dict, clip_path: str = "") -> str:
    """Writer with clip_info wired. Bit depth isn't carried by clip_info (a widget), so it is set
    to the source's here, as an artist would; MASK is wired when the source has alpha."""
    return OpenClipWriter().execute(
        IMAGE=r["IMAGE"], MASK=r["MASK"] if source_fields["channels"] == 4 else None,
        clip_info=r["clip_info"], clip_path=clip_path, file_format="EXR",
        exr_bit_depth=_DEPTH.get(source_fields["depth"], "half (16-bit)"), exr_compression="PIZ",
    )[0]


def _source_exr_colour(clip) -> tuple[str, str | None, int | None]:
    pattern = source_media_pattern(clip)
    first = feed_fields(clip)["first"]
    if pattern is None or first is None:
        return "", None, None
    return exr_attrs(pattern % first).get("autodeskColorSpace", ""), pattern, first


def new_clip_check(src_clip, out_dir, frame_cap: int = -1) -> tuple[list[str], str]:
    """Read src_clip's current version, write it as a new clip into out_dir, compare."""
    src = feed_fields(src_clip)
    exr_colour, pattern, src_first = _source_exr_colour(src_clip)
    r = read(src_clip, frame_cap)
    written = write(r, src, clip_path=str(out_dir))
    first, count = r["info.start_frame"], r["frame_count"]
    diffs = new_clip_diffs(src_clip, written, first=first, count=count, exr_colour=exr_colour)
    want_colour = expected_feed(src, first=first, count=count, exr_colour=exr_colour)["colour"]
    diffs += exr_frame_diffs(written, current_vuid(load(written)), expected_colour=want_colour,
                             source_pattern=pattern, source_first=first if src_first is not None else None,
                             source_tc=src["tc"] is not None)
    return diffs, written


def append_check(src_clip, times: int = 1, frame_cap: int = -1) -> tuple[list[str], list[str]]:
    """Read the current version and append the result to the same .clip, `times` times (each
    later round reads the version the previous one added). Returns (diffs, new vuids)."""
    original = Path(src_clip).read_bytes()
    src_vuid = current_vuid(load(original))
    src = feed_fields(load(original), src_vuid)
    exr_colour, pattern, _ = _source_exr_colour(src_clip)
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
                          exr_colour=exr_colour)
    want_colour = expected_feed(src, first=first, count=count, exr_colour=exr_colour)["colour"]
    for v in new_vuids:
        diffs += exr_frame_diffs(src_clip, v, expected_colour=want_colour,
                                 source_pattern=pattern, source_first=first if pattern else None,
                                 source_tc=src["tc"] is not None)
    return diffs, new_vuids
