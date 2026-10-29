from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Optional

import torch

from ..lib import image_io, openclip_xml, package_layout, timecode
from ..lib.openclip_xml import ClipFormat, ClipSpan, ClipVersion
from .reader import _resolve_read_plan, feed_start_timecode

# Root <clip version="..."> values accepted for a new clip. 6 is the default: Flame 2024
# imported the identical file once version="8" was changed to "6" (issue #4).
FORMAT_VERSIONS = ("5", "6", "7", "8", "9")
DEFAULT_FORMAT_VERSION = "6"
DEFAULT_COLOUR_SPACE = "Rec.709 video"
DEFAULT_FPS = 24.0
DEFAULT_START_FRAME = 1
DEFAULT_FRAME_PADDING = 4
DEFAULT_BIT_DEPTH = "half (16-bit)"
# clip_info["bit_depth"] (the source's first EXR header) -> exr_bit_depth option.
_BIT_DEPTHS = {"half": "half (16-bit)", "float": "float (32-bit)"}

_AUTO = "auto = from clip_info, else "


class OpenClipWriter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "IMAGE": ("IMAGE",),
                # Fields with an "auto" value take it from clip_info when connected (else the
                # stated default); anything else overrides clip_info. See nodes/CLAUDE.md.
                "clip_path": ("STRING", {"default": "", "tooltip": "Folder or .clip file. Empty: " + _AUTO + "required"}),
                "clip_name": ("STRING", {"default": "", "tooltip": "Empty: " + _AUTO + "required. Never trimmed."}),
                "clip_filename": ("STRING", {"default": "$(path)/$(clip_name).clip"}),
                # Never taken from clip_info: "next" is the safe default (Flame-style).
                "version_name": ("STRING", {"default": "next"}),
                "overwrite": ("BOOLEAN", {"default": False}),
                "include_version_in_filename": ("BOOLEAN", {"default": True}),
                "fps": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 120.0, "step": 0.001, "tooltip": "0: " + _AUTO + "24"}),
                "start_frame": ("INT", {"default": -1, "min": -1, "max": 999999, "tooltip": "-1: " + _AUTO + "1"}),
                "start_timecode": ("STRING", {"default": "", "tooltip": "HH:MM:SS:FF (;FF drop-frame). Empty: from clip_info, else the existing clip's, else 00:00:00:00"}),
                "frame_padding": ("INT", {"default": 0, "min": 0, "max": 9, "tooltip": "0: " + _AUTO + "4"}),
                "colour_space": ("STRING", {"default": "", "tooltip": "Empty: " + _AUTO + DEFAULT_COLOUR_SPACE}),
                "format_version": ("STRING", {"default": "", "tooltip": "Open Clip schema 5-9 for a new clip. Empty: " + _AUTO + DEFAULT_FORMAT_VERSION}),
                "file_format": (["EXR", "PNG"],),
                # "auto" first, so it is the default; saved values stay valid (a combo is one widget).
                "exr_bit_depth": (["auto", "half (16-bit)", "float (32-bit)"], {"tooltip": _AUTO + DEFAULT_BIT_DEPTH}),
                "exr_compression": (["ZIP", "PIZ", "DWAB"],),
                "aov_layout": (["Multichannel", "Separate Files"],),
                "publish": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "MASK": ("MASK",),
                "clip_info": ("CLIP_INFO",),
                "aov_chain": ("AOV_WRITE_CHAIN",),
            },
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
                "prompt": "PROMPT",
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("clip_path",)
    OUTPUT_NODE = True
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(
        self,
        IMAGE: torch.Tensor,
        clip_path: str = "",
        clip_name: str = "",
        clip_filename: str = "$(path)/$(clip_name).clip",
        version_name: str = "next",
        fps: float = 0.0,
        start_frame: int = -1,
        frame_padding: int = 0,
        file_format: str = "EXR",
        exr_bit_depth: str = "auto",
        exr_compression: str = "ZIP",
        aov_layout: str = "Multichannel",
        colour_space: str = "",
        layout: str = "Standard Flame",
        publish: bool = False,
        overwrite: bool = False,
        include_version_in_filename: bool = True,
        MASK: Optional[torch.Tensor] = None,
        clip_info: Optional[dict] = None,
        aov_chain: Optional[list] = None,
        format_version: str = "",
        start_timecode: str = "",
        extra_pnginfo: Optional[dict] = None,
        prompt=None,
    ):
        info = clip_info or {}
        _check_name_component(version_name, "version_name")

        # --- resolve every auto field (see INPUT_TYPES) ---
        clip_path_in = clip_path.strip() or info.get("clip_path", "")
        # Names are never stripped: Flame keeps leading/trailing spaces (issue #7). A
        # whitespace-only widget still counts as empty.
        clip_name_in = clip_name if clip_name.strip() else info.get("clip_name", "")
        fps = fps if fps > 0 else float(info.get("fps") or DEFAULT_FPS)
        start_frame = start_frame if start_frame >= 0 else int(info.get("start_frame", DEFAULT_START_FRAME))
        frame_padding = frame_padding if frame_padding > 0 else int(info.get("frame_padding") or DEFAULT_FRAME_PADDING)
        colour_space = colour_space.strip() or info.get("colour_space") or DEFAULT_COLOUR_SPACE
        if exr_bit_depth == "auto":
            exr_bit_depth = _BIT_DEPTHS.get(info.get("bit_depth") or "", DEFAULT_BIT_DEPTH)
        format_version_typed = format_version.strip()
        format_version = _resolve_format_version(format_version, info)
        metadata = info.get("metadata")

        output_dir, clip_name = _resolve_destination(clip_path_in, clip_name_in, clip_filename.strip())
        output_dir, clip_name = _same_file_as_source(output_dir, clip_name, info.get("clip_path", ""))

        # clip_file's location doesn't depend on version_name in any layout (see
        # package_layout.build), so this probe build only locates it to check existing
        # versions before "next" is resolved and before a collision can be detected.
        probe = package_layout.build(
            layout, output_dir, clip_name, version_name, file_format, frame_padding
        )
        existing = openclip_xml.parse(str(probe.clip_file)) if probe.clip_file.exists() else None

        if version_name == "next":
            version_name = openclip_xml.next_version(existing.versions if existing else {})

        version_exists = existing is not None and version_name in existing.versions
        if version_exists and not overwrite:
            raise ValueError(
                f"[OpenClipWriter] VERSION ALREADY EXISTS — refusing to write.\n"
                f"Version '{version_name}' already exists in '{probe.clip_file}'.\n"
                f"Fix by one of:\n"
                f"  - set version_name to 'next' to auto-pick the next free version\n"
                f"  - enable 'overwrite' to replace '{version_name}'\n"
                f"  - type a different version_name"
            )

        # Everything that can reject this write is checked before any directory or frame
        # touches the disk, so a refused run never leaves orphan frames behind.
        fps_label = openclip_xml.fps_label_from_float(fps)
        tc_rate = timecode.tc_rate(fps)
        existing_tc = _existing_timecode(existing, probe.clip_file) if existing is not None else None
        start_tc = _resolve_start_timecode(start_timecode, info, existing_tc, tc_rate)
        if existing is not None:
            _check_append_timing(existing, probe.clip_file, existing_tc, start_tc, start_frame, tc_rate)
        if existing is not None and format_version_typed and format_version != existing.schema_version:
            print(
                f"[OpenClipWriter] WARNING: format_version '{format_version}' ignored -- appending to "
                f"'{probe.clip_file}', which keeps its own schema version '{existing.schema_version}'"
            )
        n_frames, height, width = IMAGE.shape[0], IMAGE.shape[1], IMAGE.shape[2]
        fmt = ClipFormat(
            width=width,
            height=height,
            n_channels=4 if MASK is not None else 3,
            bit_depth=exr_bit_depth,
            fps=fps_label,
            colour_space=colour_space,
            file_format=file_format,
            compression=exr_compression,
        )
        if existing is not None:
            _raise_on_format_mismatch(probe.clip_file, fmt)

        paths = package_layout.build(
            layout, output_dir, clip_name, version_name, file_format, frame_padding,
            version_in_filename=include_version_in_filename,
        )
        package_layout.create_dirs(paths)

        if version_exists and overwrite:
            _delete_existing_frames(paths.media_dir, paths.frame_stem, file_format)
            if include_version_in_filename:
                # Versions written before 2026-10-27 used "<clip>.<version>" frame names.
                _delete_existing_frames(paths.media_dir, f"{clip_name}.{version_name}", file_format)

        tc_ticks, tc_drop = timecode.parse_tc(start_tc, tc_rate)
        abs_pattern = str(paths.media_dir / f"{paths.frame_stem}.%0{frame_padding}d.{file_format.lower()}")
        image_io.write_sequence(
            abs_pattern, IMAGE, MASK, start_frame, file_format, exr_bit_depth, exr_compression,
            metadata=metadata, aov_entries=aov_chain, aov_layout=aov_layout,
            colour_space=colour_space, start_timecode=(tc_ticks, tc_rate, tc_drop),
        )

        print(f"[OpenClipWriter] First frame written: {abs_pattern % start_frame}")
        print(f"[OpenClipWriter] Wrote {n_frames} {file_format} file(s) to {paths.media_dir}")

        sidecar_rel = _write_publish_sidecar(paths, extra_pnginfo) if publish else None
        new_version = ClipVersion(
            uid=version_name,
            name=version_name,
            spans=[ClipSpan(path=abs_pattern, start_frame=start_frame, duration=n_frames)],
            publish_path=sidecar_rel,
            start_timecode=start_tc,
        )

        if paths.clip_file.exists():
            xml_bytes = _merge_version(paths.clip_file, new_version, fmt)
        else:
            xml_bytes = openclip_xml.generate(
                clip_name, {version_name: new_version}, version_name, fmt,
                schema_version=format_version,
            )
        paths.clip_file.write_bytes(xml_bytes)
        print(f"[OpenClipWriter] Wrote clip file: {paths.clip_file}")

        return (str(paths.clip_file),)


def _resolve_format_version(widget: str, info: dict) -> str:
    """Widget value (validated) > clip_info's schema version > 6."""
    value = widget.strip()
    if value:
        if value not in FORMAT_VERSIONS:
            raise ValueError(
                f"[OpenClipWriter] format_version '{value}' is not supported. Valid: {list(FORMAT_VERSIONS)}"
            )
        return value
    from_info = str(info.get("format_version") or "")
    if from_info in FORMAT_VERSIONS:
        return from_info
    if from_info:
        print(f"[OpenClipWriter] WARNING: clip_info format_version '{from_info}' is not writable; using {DEFAULT_FORMAT_VERSION}")
    return DEFAULT_FORMAT_VERSION


def _same_file_as_source(output_dir: str, clip_name: str, source: str) -> tuple[str, str]:
    """When the destination is the very .clip clip_info was read from, use that file's own
    folder and name verbatim, so the write appends to exactly it (issue #7)."""
    if not source.lower().endswith(".clip"):
        return output_dir, clip_name
    candidate = Path(output_dir) / f"{clip_name}.clip"
    try:
        if os.path.samefile(candidate, source):
            src = Path(source)
            return str(src.parent), src.name[: -len(".clip")]
    except OSError:
        pass  # either file missing: nothing to match
    return output_dir, clip_name


def _resolve_start_timecode(widget: str, info: dict, existing_tc: Optional[str], rate: int) -> str:
    """Widget > clip_info > the existing clip's current feed (when appending) > 00:00:00:00."""
    for what, value in (("start_timecode", widget.strip()), ("clip_info start_timecode", info.get("start_timecode") or "")):
        if value:
            try:
                return timecode.normalize(value, rate)
            except ValueError as e:
                raise ValueError(f"[OpenClipWriter] {what}: {e}") from None
    return existing_tc or timecode.ZERO


def _existing_timecode(existing, clip_file: Path) -> str:
    """The existing clip's start timecode, as OpenClipReader reports it: the current feed's
    <startTimecode>, else its first frame's EXR smpte:TimeCode, else 00:00:00:00. Using the
    Reader's own lookup keeps a Reader -> Writer append to such a clip consistent."""
    current = existing.versions.get(existing.current_version)
    if current is None:
        return timecode.ZERO
    if current.start_timecode:
        return current.start_timecode
    try:
        # Same path resolution (incl. auto-remap) as the Reader, so it finds the same frame.
        _c, _p, version, abs_pattern, _s, _e = _resolve_read_plan(str(clip_file), current.name, -1, -1, "", "")
        num, den = openclip_xml.fps_to_rational(openclip_xml.read_format(str(clip_file)).fps)
        return feed_start_timecode(version, abs_pattern, num / den) or timecode.ZERO
    except (OSError, ValueError):
        return timecode.ZERO


def _check_append_timing(existing, clip_file: Path, ex_tc: str, start_tc: str, start_frame: int, rate: int) -> None:
    """Issue #7 rules for adding a version to an existing clip (also with overwrite on):
    - start timecodes differ -> error (Flame would place the versions apart in time);
    - same real timecode, different start frame -> warning only (timecode places it);
    - both 00:00:00:00 and different start frames -> error (nothing else aligns them)."""
    if ex_tc != start_tc:
        raise ValueError(
            f"[OpenClipWriter] START TIMECODE MISMATCH — refusing to write.\n"
            f"'{clip_file}' starts at {ex_tc} (current version '{existing.current_version}'), "
            f"this write at {start_tc}. Flame would place the two versions apart in time.\n"
            f"Fix by one of:\n"
            f"  - set start_timecode to {ex_tc}\n"
            f"  - set a new clip_name to write a separate clip"
        )
    current = existing.versions.get(existing.current_version)
    if current is None or not current.spans:
        return
    ex_start = current.spans[0].start_frame
    if ex_start == start_frame:
        return
    if timecode.parse_tc(start_tc, rate)[0] == 0:
        raise ValueError(
            f"[OpenClipWriter] START FRAME MISMATCH — refusing to write.\n"
            f"'{clip_file}' starts at frame {ex_start}, this write at {start_frame}, and both start "
            f"at timecode {start_tc}, so nothing would line the versions up.\n"
            f"Fix by one of:\n"
            f"  - set start_frame to {ex_start}\n"
            f"  - set start_timecode to the clip's real timecode\n"
            f"  - set a new clip_name to write a separate clip"
        )
    print(
        f"[OpenClipWriter] WARNING: start frame {start_frame} differs from '{clip_file.name}' "
        f"({ex_start}); both start at timecode {start_tc}, so the versions still line up."
    )


def _raise_on_format_mismatch(clip_file: Path, fmt: ClipFormat) -> None:
    mismatches = _format_mismatches(openclip_xml.read_format(str(clip_file)), fmt)
    if mismatches:
        detail = ", ".join(f"{k}: existing={old} new={nw}" for k, old, nw in mismatches)
        raise ValueError(
            f"Format mismatch with '{clip_file.name}': {detail}. "
            f"To keep this result, set a new clip_name to write a separate clip."
        )


def _merge_version(
    clip_file: Path,
    new_version: ClipVersion,
    fmt: ClipFormat,
) -> bytes:
    """Add new_version to an existing clip in place, keeping everything else in the file
    (schema version, Flame's track/version metadata, other tracks) -- see append_version."""
    _raise_on_format_mismatch(clip_file, fmt)
    return openclip_xml.append_version(str(clip_file), new_version, fmt)


def _format_mismatches(
    existing: ClipFormat, new: ClipFormat
) -> list[tuple[str, str, str]]:
    results = []
    if existing.width > 0 and new.width > 0 and existing.width != new.width:
        results.append(("width", str(existing.width), str(new.width)))
    if existing.height > 0 and new.height > 0 and existing.height != new.height:
        results.append(("height", str(existing.height), str(new.height)))
    if existing.fps and new.fps and existing.fps != new.fps:
        results.append(("fps", existing.fps, new.fps))
    if existing.file_format and new.file_format and existing.file_format != new.file_format:
        results.append(("file_format", existing.file_format, new.file_format))
    return results


def _delete_existing_frames(media_dir: Path, frame_stem: str, file_format: str) -> None:
    """Remove this version's previously rendered frames before rewriting (overwrite=True).

    Deletes before writing rather than just letting new frames land on top, so a shorter
    re-render doesn't leave stale extra frames from the old render behind.
    """
    # Escaped, so a clip or version name containing * ? [ ] can't match another clip's frames.
    pattern = f"{glob.escape(frame_stem)}.*.{file_format.lower()}"
    deleted = 0
    for f in media_dir.glob(pattern):
        f.unlink()
        deleted += 1
    if deleted:
        print(f"[OpenClipWriter] Overwrite: deleted {deleted} existing frame file(s) matching '{pattern}' in {media_dir}")


def _resolve_destination(clip_path_in: str, clip_name_in: str, clip_filename: str) -> tuple[str, str]:
    """Expand $(path)/$(clip_name) tokens in clip_filename, then split into (output_dir, clip_name).

    $(path)      → clip_path_in, normalised to a folder (parent dir if a .clip file was supplied).
    $(clip_name) → clip_name_in.

    Tokens use explicit parentheses so adjacent literal text (e.g. $(clip_name)_clean)
    is never mistaken for part of the token name.

    The expanded result is normalised (resolves ..) and split on the last component.
    The caller can use $(path)/../sibling/$(clip_name) to navigate relative to the source clip.
    """
    if not clip_filename:
        raise ValueError("clip_filename is required")

    folder = _normalize_to_folder(clip_path_in)
    expanded = clip_filename

    if "$(path)" in expanded:
        if not folder:
            raise ValueError("clip_path is required when clip_filename contains $(path) (type it, or connect clip_info)")
        expanded = expanded.replace("$(path)", folder)

    if "$(clip_name)" in expanded:
        if not clip_name_in:
            raise ValueError("clip_name is required when clip_filename contains $(clip_name) (type it, or connect clip_info)")
        _check_name_component(clip_name_in, "clip_name")
        expanded = expanded.replace("$(clip_name)", clip_name_in)

    # Collapse any .. segments without requiring the path to exist yet.
    expanded = os.path.normpath(expanded)

    p = Path(expanded)
    if not p.parent or str(p.parent) == ".":
        raise ValueError(
            f"clip_filename must resolve to a path with a parent directory "
            f"(e.g. /output/myshot), got: '{expanded}'"
        )

    name = p.stem if p.suffix.lower() == ".clip" else p.name
    return str(p.parent), name


def _check_name_component(value: str, what: str) -> None:
    """Reject a clip_name/version_name that would leave its folder (a path separator, '.' or '..').

    Either can be wired from a Reader, i.e. come from someone else's .clip <name>, so it must
    not be able to redirect the write. Moving the destination on purpose is what clip_filename
    is for ($(path)/../elsewhere/$(clip_name)).
    """
    if "/" in value or "\\" in value or value.strip() in (".", ".."):
        raise ValueError(
            f"[OpenClipWriter] {what} '{value}' must be a plain name: no '/', '\\', '.' or '..'. "
            f"To write somewhere else, change clip_filename (e.g. $(path)/../other/$(clip_name))."
        )


def _normalize_to_folder(val: str) -> str:
    """Return val as a folder path. If val points to a .clip file, return its parent dir."""
    if not val:
        return ""
    p = Path(val)
    if p.suffix.lower() == ".clip":
        return str(p.parent)
    return val


def _write_publish_sidecar(
    paths: package_layout.PackagePaths,
    extra_pnginfo: Optional[dict],
) -> Optional[str]:
    workflow = (extra_pnginfo or {}).get("workflow")
    if workflow is None:
        return None
    paths.sidecar_file.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
    return str(paths.sidecar_file.relative_to(paths.clip_file.parent))
