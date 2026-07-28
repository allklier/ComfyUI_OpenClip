from __future__ import annotations

from pathlib import Path

from ..lib import image_io, openclip_xml
from ._paths import resolve_clip_path

# Guard against probing excessively broad path prefixes (e.g. root '/').
_AUTO_REMAP_MIN_PREFIX_PARTS = 2


class OpenClipReader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip_path": ("STRING", {"default": ""}),
                "version": ("STRING", {"default": "current"}),
                # -1 means use the clip's own start/end from the XML span
                "start_frame": ("INT", {"default": -1, "min": -1, "max": 999999}),
                "end_frame": ("INT", {"default": -1, "min": -1, "max": 999999}),
                "load_alpha": ("BOOLEAN", {"default": False}),
                # Optional manual path remap: replace a prefix in all media paths
                # from the XML. Leave blank to let the reader auto-detect the remap
                # by probing for the media directory relative to the clip file.
                "path_from": ("STRING", {"default": ""}),
                "path_to": ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT", "INT", "STRING", "INT", "STRING", "STRING", "CLIP_METADATA", "FLOAT")
    RETURN_NAMES = ("IMAGE", "MASK", "frame_count", "width", "height", "format_version", "start_frame", "clip_path", "clip_name", "metadata", "fps")
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(
        self,
        clip_path: str,
        version: str,
        start_frame: int,
        end_frame: int,
        load_alpha: bool,
        path_from: str,
        path_to: str,
    ):
        clip_path = resolve_clip_path(clip_path.strip())
        if not clip_path:
            raise ValueError("clip_path is required")

        parsed = openclip_xml.parse(clip_path)
        clip_version = openclip_xml.resolve_version(parsed, version)
        fps_num, fps_den = openclip_xml.fps_to_rational(openclip_xml.read_format(clip_path).fps)
        fps = fps_num / fps_den

        if not clip_version.spans:
            raise ValueError(f"Version '{version}' has no spans")

        span = clip_version.spans[0]
        abs_pattern = str(Path(clip_path).parent / span.path)

        path_from_s = path_from.strip()
        path_to_s = path_to.strip()
        if not path_from_s:
            detected = _auto_remap(clip_path, abs_pattern)
            if detected:
                path_from_s, path_to_s = detected

        abs_pattern = _remap_prefix(abs_pattern, path_from_s, path_to_s)

        resolved_start = span.start_frame if start_frame == -1 else start_frame
        resolved_end = (span.start_frame + span.duration - 1) if end_frame == -1 else end_frame

        images, masks, metadata = image_io.read_sequence(
            abs_pattern, resolved_start, resolved_end, load_alpha
        )

        frame_count, height, width = images.shape[0], images.shape[1], images.shape[2]
        return (
            images, masks, frame_count, width, height, parsed.schema_version,
            resolved_start, clip_path, parsed.clip_name, metadata, fps,
        )


def _remap_prefix(path: str, path_from: str, path_to: str) -> str:
    if not path_from or not path_to:
        return path
    if path.startswith(path_from):
        return path_to + path[len(path_from):]
    return path


def _auto_remap(clip_path: str, media_pattern: str) -> tuple[str, str] | None:
    """
    Detect the path_from / path_to remap automatically.

    Strips the filename and walks progressively shorter left-prefixes of the
    embedded media directory, checking each time whether the remaining suffix
    resolves to a real directory relative to the clip file. The longest-suffix
    (most specific) match wins, so a deep shared structure beats a shallow one.

    Returns (path_from, path_to) or None if no remap can be determined.
    """
    media_dir = Path(media_pattern).parent
    if not media_dir.is_absolute():
        return None

    clip_dir = Path(clip_path).parent
    parts = media_dir.parts  # e.g. ('/', 'Volumes', 'LocalJobs', 'project', 'files')

    # Iterate longest suffix first (smallest i = most path components in suffix
    # = most specific match). Stop before i reaches a trivially broad prefix
    # like the filesystem root.
    for i in range(_AUTO_REMAP_MIN_PREFIX_PARTS, len(parts)):
        suffix = Path(*parts[i:])
        candidate = clip_dir / suffix
        try:
            if candidate.is_dir():
                path_from = str(Path(*parts[:i]))
                return path_from, str(clip_dir)
        except OSError:
            continue

    return None
