from __future__ import annotations

import os
import re
from pathlib import Path

from ..lib import image_io
from ._paths import resolve_clip_path

# '####' (any run of '#') in a sequence path, the Blender/Nuke way of writing a frame token.
_HASH_TOKEN_RE = re.compile(r"#+")


def _to_printf_pattern(sequence_path: str) -> str:
    """'shot.####.exr' -> 'shot.%04d.exr'; a '%04d'-style pattern passes through unchanged."""
    if image_io._FRAME_TOKEN_RE.search(sequence_path):
        return sequence_path
    tokens = _HASH_TOKEN_RE.findall(Path(sequence_path).name)
    if len(tokens) != 1:
        raise ValueError(f"sequence_path needs exactly one frame token ('####' or '%04d'), got '{sequence_path}'")
    head, _, tail = sequence_path.rpartition(tokens[0])
    return f"{head}%0{len(tokens[0])}d{tail}"


def _frames_on_disk(abs_pattern: str) -> list[int]:
    """Sorted frame numbers whose files match the pattern's filename on disk."""
    directory, name = os.path.split(abs_pattern)
    m = image_io._FRAME_TOKEN_RE.search(name)
    regex = re.compile(re.escape(name[:m.start()]) + r"(\d+)" + re.escape(name[m.end():]) + r"$")
    try:
        entries = os.listdir(directory or ".")
    except FileNotFoundError:
        raise FileNotFoundError(f"sequence directory does not exist: {directory}") from None
    return sorted(int(mm.group(1)) for e in entries if (mm := regex.match(e)))


def _resolve_read_plan(sequence_path: str, start_frame: int, end_frame: int):
    """Shared by execute() and IS_CHANGED(), same as OpenClipReader's."""
    sequence_path = sequence_path.strip()
    if not sequence_path:
        raise ValueError("sequence_path is required")
    abs_pattern = _to_printf_pattern(resolve_clip_path(sequence_path))
    if start_frame == -1 or end_frame == -1:
        frames = _frames_on_disk(abs_pattern)
        if not frames:
            raise FileNotFoundError(f"no frames on disk match {abs_pattern}")
    resolved_start = frames[0] if start_frame == -1 else start_frame
    resolved_end = frames[-1] if end_frame == -1 else end_frame
    return abs_pattern, resolved_start, resolved_end


_LAST_READ_PLAN: dict[str, tuple[str, int]] = {}


def _fingerprint(abs_pattern: str, resolved_start: int) -> str:
    frame_stat = os.stat(image_io.frame_path(abs_pattern, resolved_start))
    return f"{frame_stat.st_mtime_ns}:{frame_stat.st_size}"


class ExrSequenceReader:
    """Reads a plain EXR frame sequence (no .clip wrapper), e.g. a Blender or Nuke render.

    Outputs only what a plain sequence actually has -- no format_version, version_name
    or available_versions, since there's no notion of an Open Clip version here; a node
    that needs those (e.g. OpenClipWriter) supplies its own default rather than taking an
    empty placeholder from this reader. clip_path and clip_name are genuine derived
    values, not placeholders: clip_path is the sequence's folder and clip_name its
    filename stem, so wiring them into OpenClipWriter writes '<folder>/<stem>.clip'.
    fps and colour_space come from widgets, because Blender writes no frame rate into
    the EXR; a blank colour_space falls back to the file header.

    `layer` picks the beauty from a single-part multilayer EXR (Blender writes
    '<view layer>.<pass>.<channel>', e.g. 'CamelMatte.Combined'). Blank reads plain
    R/G/B/A. Every other layer is listed in available_aovs and read with
    OpenClipAOVReader by the same name (e.g. 'ViewLayer.Normal').
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # 'name.####.exr' or 'name.%04d.exr'; relative paths resolve against the input directory.
                "sequence_path": ("STRING", {"default": ""}),
                "layer": ("STRING", {"default": ""}),
                # -1 means the first/last frame found on disk
                "start_frame": ("INT", {"default": -1, "min": -1, "max": 999999}),
                "end_frame": ("INT", {"default": -1, "min": -1, "max": 999999}),
                "frame_cap": ("INT", {"default": -1, "min": -1, "max": 999999}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001}),
                "colour_space": ("STRING", {"default": ""}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT", "INT", "INT", "STRING", "STRING", "CLIP_METADATA", "AOV_HANDLE", "STRING", "FLOAT", "STRING")
    RETURN_NAMES = ("IMAGE", "MASK", "frame_count", "width", "height", "start_frame", "clip_path", "clip_name", "metadata", "aov_handle", "colour_space", "fps", "available_aovs")
    OUTPUT_NODE = True
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(
        self,
        sequence_path: str,
        layer: str,
        start_frame: int,
        end_frame: int,
        frame_cap: int,
        fps: float,
        colour_space: str,
        unique_id=None,
    ):
        abs_pattern, resolved_start, resolved_end = _resolve_read_plan(sequence_path, start_frame, end_frame)
        if frame_cap > -1:
            resolved_end = min(resolved_end, resolved_start + frame_cap - 1)
        if unique_id is not None:
            _LAST_READ_PLAN[str(unique_id)] = (abs_pattern, resolved_start)

        images, masks, metadata = image_io.read_sequence(
            abs_pattern, resolved_start, resolved_end, load_alpha=True, layer=layer.strip()
        )
        colour_space = colour_space.strip() or metadata.get(image_io.COLOUR_SPACE_BACKUP_KEY) or metadata.get(image_io.COLOUR_SPACE_KEY, "")

        frame_count, height, width = images.shape[0], images.shape[1], images.shape[2]
        folder, filename = os.path.split(abs_pattern)
        clip_name = filename[:image_io._FRAME_TOKEN_RE.search(filename).start()].rstrip("._")
        aov_handle = {"abs_pattern": abs_pattern, "start_frame": resolved_start, "end_frame": resolved_end, "clip_path": abs_pattern}
        available_aovs = "\n".join(image_io.probe_aov_names(abs_pattern, resolved_start))
        return {
            "ui": {"available_aovs": [available_aovs]},
            "result": (
                images, masks, frame_count, width, height, resolved_start, folder, clip_name,
                metadata, aov_handle, colour_space, float(fps), available_aovs,
            ),
        }

    # Same contract as OpenClipReader._UNFINGERPRINTABLE.
    _UNFINGERPRINTABLE = ""

    @classmethod
    def IS_CHANGED(cls, sequence_path=None, start_frame=None, end_frame=None, unique_id=None, **kwargs):
        """First frame's mtime/size. Must never raise; see OpenClipReader.IS_CHANGED for
        why (a raise defeats caching for the whole downstream graph) and for the
        linked-input fallback to what the last execute() read."""
        if isinstance(sequence_path, str) and all(isinstance(v, int) for v in (start_frame, end_frame)):
            try:
                abs_pattern, resolved_start, _e = _resolve_read_plan(sequence_path, start_frame, end_frame)
                return _fingerprint(abs_pattern, resolved_start)
            except Exception:
                return cls._UNFINGERPRINTABLE

        remembered = _LAST_READ_PLAN.get(str(unique_id)) if unique_id is not None else None
        if remembered is not None:
            try:
                return _fingerprint(*remembered)
            except Exception:
                return cls._UNFINGERPRINTABLE
        return cls._UNFINGERPRINTABLE
