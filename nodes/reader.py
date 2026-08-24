from __future__ import annotations

import os
from pathlib import Path

from ..lib import image_io, openclip_xml
from ._paths import resolve_clip_path

# Guard against probing excessively broad path prefixes (e.g. root '/').
_AUTO_REMAP_MIN_PREFIX_PARTS = 2


def _resolve_read_plan(clip_path: str, version: str, start_frame: int, end_frame: int, path_from: str, path_to: str):
    """Resolve clip_path/version/frame range into an absolute frame pattern.

    Shared by execute() and IS_CHANGED() so both agree on which frame files
    a given set of inputs actually reads.
    """
    clip_path = resolve_clip_path(clip_path.strip())
    if not clip_path:
        raise ValueError("clip_path is required")

    parsed = openclip_xml.parse(clip_path)
    clip_version = openclip_xml.resolve_version(parsed, version)

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

    return clip_path, parsed, clip_version, abs_pattern, resolved_start, resolved_end


# unique_id -> the files the last successful execute() actually read, so IS_CHANGED can
# still fingerprint them when clip_path arrives as a link. See IS_CHANGED for why this
# indirection is necessary at all. Process-local and lost on restart, which costs one
# extra read after a restart and nothing else.
_LAST_READ_PLAN: dict[str, tuple[str, str, int]] = {}


def _fingerprint(resolved_clip_path: str, abs_pattern: str, resolved_start: int) -> str:
    """mtime+size of the clip XML and the sequence's first frame."""
    clip_stat = os.stat(resolved_clip_path)
    frame_stat = os.stat(image_io.frame_path(abs_pattern, resolved_start))
    return f"{clip_stat.st_mtime_ns}:{clip_stat.st_size}:{frame_stat.st_mtime_ns}:{frame_stat.st_size}"


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
                # Optional manual path remap: replace a prefix in all media paths
                # from the XML. Leave blank to let the reader auto-detect the remap
                # by probing for the media directory relative to the clip file.
                "path_from": ("STRING", {"default": ""}),
                "path_to": ("STRING", {"default": ""}),
                # Part-name-keyed AOV convention: a multi-part EXR where every part uses
                # generic R/G/B channel names and the AOV is identified only by the EXR
                # part `name` attribute (confirmed against a real Flame render whose AOVs
                # were imported from another DCC) — see lib/CLAUDE.md "AOV Channel
                # Handling". Empty string disables lookup for that AOV. normal_raw_layer_name
                # carries a normals-like part whose camera/world space this repo does not
                # try to determine; a downstream node should apply that labelling.
                "depth_layer_name": ("STRING", {"default": image_io.DEFAULT_DEPTH_PART_NAME}),
                "normal_world_layer_name": ("STRING", {"default": image_io.DEFAULT_NORMAL_WORLD_PART_NAME}),
                "normal_raw_layer_name": ("STRING", {"default": image_io.DEFAULT_NORMAL_RAW_PART_NAME}),
            },
            # Needed by IS_CHANGED, which is handed UNIQUE_ID even though it is handed
            # nothing else useful when inputs are linked. Hidden inputs are a separate
            # dict, not positional widgets, so adding this cannot disturb the
            # widgets_values ordering of saved workflows.
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT", "INT", "STRING", "STRING", "INT", "STRING", "STRING", "CLIP_METADATA", "IMAGE", "IMAGE", "MASK", "IMAGE", "STRING", "FLOAT", "STRING")
    RETURN_NAMES = ("IMAGE", "MASK", "frame_count", "width", "height", "format_version", "version_name", "start_frame", "clip_path", "clip_name", "metadata", "NORMAL", "NORMAL_WORLD", "DEPTH", "NORMAL_RAW", "colour_space", "fps", "available_versions")
    OUTPUT_NODE = True
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(
        self,
        clip_path: str,
        version: str,
        start_frame: int,
        end_frame: int,
        path_from: str,
        path_to: str,
        depth_layer_name: str = image_io.DEFAULT_DEPTH_PART_NAME,
        normal_world_layer_name: str = image_io.DEFAULT_NORMAL_WORLD_PART_NAME,
        normal_raw_layer_name: str = image_io.DEFAULT_NORMAL_RAW_PART_NAME,
        unique_id=None,
    ):
        clip_path, parsed, clip_version, abs_pattern, resolved_start, resolved_end = _resolve_read_plan(
            clip_path, version, start_frame, end_frame, path_from, path_to
        )
        # Remember what this node instance actually read, so IS_CHANGED can stat these
        # files on the next queue even when it cannot resolve them from its own arguments.
        if unique_id is not None:
            _LAST_READ_PLAN[str(unique_id)] = (clip_path, abs_pattern, resolved_start)
        fps_num, fps_den = openclip_xml.fps_to_rational(openclip_xml.read_format(clip_path).fps)
        fps = fps_num / fps_den

        images, masks, aovs, metadata = image_io.read_sequence_with_aovs(
            abs_pattern, resolved_start, resolved_end, load_alpha=True,
            depth_part_name=depth_layer_name.strip(),
            normal_world_part_name=normal_world_layer_name.strip(),
            normal_raw_part_name=normal_raw_layer_name.strip(),
        )
        # Prefer the verbatim backup key over "oiio:ColorSpace" itself — OIIO's EXR plugin
        # silently renames/drops that key for names outside its built-in registry (e.g.
        # camera log profiles); see image_io.COLOUR_SPACE_BACKUP_KEY.
        colour_space = metadata.get(image_io.COLOUR_SPACE_BACKUP_KEY) or metadata.get(image_io.COLOUR_SPACE_KEY, "")

        frame_count, height, width = images.shape[0], images.shape[1], images.shape[2]
        available_versions = _format_available_versions(parsed.versions.keys(), parsed.current_version)
        return {
            "ui": {"available_versions": [available_versions]},
            "result": (
                images, masks, frame_count, width, height, parsed.schema_version,
                clip_version.name, resolved_start, clip_path, parsed.clip_name, metadata,
                aovs["normal"], aovs["normal_world"], aovs["depth"], aovs["normal_raw"],
                colour_space, fps, available_versions,
            ),
        }

    # Returned when the mtime/size fingerprint cannot be computed. Must be a stable
    # constant: see IS_CHANGED below for why anything else (including raising) is worse.
    _UNFINGERPRINTABLE = ""

    @classmethod
    def IS_CHANGED(
        cls, clip_path=None, version=None, start_frame=None, end_frame=None,
        path_from=None, path_to=None, unique_id=None, **kwargs,
    ):
        """Change signature: the media's mtime/size, whenever we can work out the media.

        ComfyUI already re-executes this node when its *inputs* change -- a linked
        value is part of the cache key via caching.py's ancestry walk. IS_CHANGED
        exists to add the one signal the graph cannot see: **the files on disk
        changed underneath an unchanged graph** (a re-render in Flame, a new clip
        version). So the contract is:

            inputs changed  -> ComfyUI handles it
            files changed   -> this function reports it
            neither         -> stable value, cache is reused

        The awkward part is knowing *which* files to stat. ComfyUI builds this
        function's arguments with get_input_data(..., execution_list=None), whose
        mark_missing() substitutes None for every input driven by a link, so a
        clip_path fed by a PrimitiveString arrives as None. There is no way to
        recover it -- dynprompt is None here too, so even a hidden PROMPT input
        would arrive empty.

        UNIQUE_ID *is* delivered, though, so when the arguments are unusable we fall
        back to the files the last successful execute() recorded for this node
        instance (_LAST_READ_PLAN). That covers the case that matters: nothing in the
        graph moved, but the artist rendered a new version. The only cost is the
        first queue after a restart, when the memory is empty and the node reads once.

        Must never raise. ComfyUI catches an IS_CHANGED exception and substitutes
        float("NaN"), which never compares equal to itself, so the node re-executes
        on every queue -- and since caching.py folds each node's signature into every
        descendant's, one raising IS_CHANGED here defeats the cache for the entire
        downstream graph. That was a real production bug (2026-08-24): clip_path
        arrived as None, .strip() raised AttributeError, and a Harmonize workflow
        re-ran Lotus and the whole Module A relight on every single queue.
        """
        # Preferred path: every input is a real constant, so resolve and stat directly.
        if (all(isinstance(v, str) for v in (clip_path, version, path_from, path_to))
                and all(isinstance(v, int) for v in (start_frame, end_frame))):
            try:
                resolved_clip_path, _p, _v, abs_pattern, resolved_start, _e = _resolve_read_plan(
                    clip_path, version, start_frame, end_frame, path_from, path_to
                )
                return _fingerprint(resolved_clip_path, abs_pattern, resolved_start)
            except Exception:
                # A genuinely broken path/version still has to fail loudly -- but in
                # execute(), with a real error message. Failing here would only
                # destroy caching silently.
                return cls._UNFINGERPRINTABLE

        # Fallback: stat whatever this node instance read last time.
        remembered = _LAST_READ_PLAN.get(str(unique_id)) if unique_id is not None else None
        if remembered is not None:
            try:
                return _fingerprint(*remembered)
            except Exception:
                return cls._UNFINGERPRINTABLE

        # Nothing to go on yet (first queue, or first after a restart).
        return cls._UNFINGERPRINTABLE


def _format_available_versions(version_names, current_version: str) -> str:
    return "\n".join(
        f"{name}  (current)" if name == current_version else name
        for name in sorted(version_names)
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
