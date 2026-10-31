from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Optional

import torch

from ..lib import image_io, openclip_xml, timecode
from .writer import (
    COMPRESSIONS,
    DEFAULT_BIT_DEPTH,
    DEFAULT_COLOUR_SPACE,
    DEFAULT_COMPRESSION,
    DEFAULT_FPS,
    DEFAULT_FRAME_PADDING,
    DEFAULT_START_FRAME,
    _BIT_DEPTHS,
)

# A version token in a sequence name: 'v' + digits, delimited by '_' or '.' or the name's
# start/end ('shot_v01', 'shot.v001', 'v1_shot'). The rightmost one is the version.
_VERSION_TOKEN_RE = re.compile(r"(?<![^._])v(\d+)(?![^._])")
_VERSION_NAME_RE = re.compile(r"^v\d+$")

_AUTO = "auto = from clip_info, else "


class OpenClipExrSequenceWriter:
    """Writes a plain EXR sequence '<folder>/<name>.####.exr', no .clip file.

    Counterpart of OpenClipExrSequenceReader. Fields with an "auto" value take it from
    clip_info when connected, else a default; anything else overrides clip_info (same rule
    as OpenClipWriter). A name with a version token ('shot_v01') is versioned: version_name
    "next" scans the folder for the same name at other version numbers. See nodes/CLAUDE.md.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "IMAGE": ("IMAGE",),
                "folder": ("STRING", {"default": "", "tooltip": "Absolute folder. Empty: " + _AUTO + "required"}),
                "name": ("STRING", {"default": "", "tooltip": "Sequence name before '.####.exr'. Empty: " + _AUTO + "required. A v<digits> token (shot_v01) makes it versioned."}),
                # Only used when name has a version token; never taken from clip_info.
                "version_name": ("STRING", {"default": "next", "tooltip": "Versioned names only: 'next' or v<digits>"}),
                "overwrite": ("BOOLEAN", {"default": False}),
                "fps": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 120.0, "step": 0.001, "tooltip": "0: " + _AUTO + "24"}),
                "start_frame": ("INT", {"default": -1, "min": -1, "max": 999999, "tooltip": "-1: " + _AUTO + "1"}),
                "start_timecode": ("STRING", {"default": "", "tooltip": "HH:MM:SS:FF (;FF drop-frame). Empty: " + _AUTO + "00:00:00:00"}),
                "frame_padding": ("INT", {"default": 0, "min": 0, "max": 9, "tooltip": "0: " + _AUTO + "4"}),
                "colour_space": ("STRING", {"default": "", "tooltip": "Empty: " + _AUTO + DEFAULT_COLOUR_SPACE}),
                "exr_bit_depth": (["auto", "half (16-bit)", "float (32-bit)"], {"tooltip": _AUTO + DEFAULT_BIT_DEPTH}),
                "exr_compression": (["auto", *COMPRESSIONS], {"tooltip": _AUTO + DEFAULT_COMPRESSION}),
                "aov_layout": (["Multichannel", "Separate Files"],),
            },
            "optional": {
                "MASK": ("MASK",),
                "clip_info": ("CLIP_INFO",),
                "aov_chain": ("AOV_WRITE_CHAIN",),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("sequence_path", "first_frame_path", "folder", "name", "version_name")
    OUTPUT_NODE = True
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(
        self,
        IMAGE: torch.Tensor,
        folder: str = "",
        name: str = "",
        version_name: str = "next",
        overwrite: bool = False,
        fps: float = 0.0,
        start_frame: int = -1,
        start_timecode: str = "",
        frame_padding: int = 0,
        colour_space: str = "",
        exr_bit_depth: str = "auto",
        exr_compression: str = "auto",
        aov_layout: str = "Multichannel",
        MASK: Optional[torch.Tensor] = None,
        clip_info: Optional[dict] = None,
        aov_chain: Optional[list] = None,
    ):
        info = clip_info or {}

        # --- resolve every auto field (see INPUT_TYPES) ---
        folder = folder.strip() or _folder_of(info.get("clip_path", ""))
        name = name if name.strip() else info.get("clip_name", "")  # never stripped, as the Writer
        fps = fps if fps > 0 else float(info.get("fps") or DEFAULT_FPS)
        start_frame = start_frame if start_frame >= 0 else int(info.get("start_frame", DEFAULT_START_FRAME))
        frame_padding = frame_padding if frame_padding > 0 else int(info.get("frame_padding") or DEFAULT_FRAME_PADDING)
        colour_space = colour_space.strip() or info.get("colour_space") or DEFAULT_COLOUR_SPACE
        if exr_bit_depth == "auto":
            exr_bit_depth = _BIT_DEPTHS.get(info.get("bit_depth") or "", DEFAULT_BIT_DEPTH)
        if exr_compression == "auto":
            source = (info.get("compression") or "").upper()
            exr_compression = source if source in COMPRESSIONS else DEFAULT_COMPRESSION

        # --- everything that can reject this write, before anything touches the disk ---
        if not folder:
            raise ValueError("[OpenClipExrSequenceWriter] folder is required (type it, or connect clip_info)")
        if not os.path.isabs(folder):
            raise ValueError(f"[OpenClipExrSequenceWriter] folder must be an absolute path, got '{folder}'")
        if not name.strip():
            raise ValueError("[OpenClipExrSequenceWriter] name is required (type it, or connect clip_info)")
        _check_plain_name(name, "name")
        _check_plain_name(version_name, "version_name")
        fps_label = openclip_xml.fps_label_from_float(fps)
        tc_rate = timecode.tc_rate(fps)
        start_tc = _resolve_start_timecode(start_timecode, info, tc_rate)
        _check_aov_chain(aov_chain, aov_layout)
        if IMAGE.shape[0] > image_io.MAX_FRAMES:
            raise RuntimeError(f"Frame count {IMAGE.shape[0]} exceeds MAX_FRAMES={image_io.MAX_FRAMES}")

        stem, version = _resolve_stem(folder, name, version_name)
        existing = _existing_frames(Path(folder), stem)
        if existing and not overwrite:
            raise ValueError(_collision_message(folder, stem, version, len(existing)))

        Path(folder).mkdir(parents=True, exist_ok=True)
        if existing:
            _delete_existing_frames(Path(folder), stem)

        tc_ticks, tc_drop = timecode.parse_tc(start_tc, tc_rate)
        abs_pattern = os.path.join(folder, f"{stem}.%0{frame_padding}d.exr")
        image_io.write_sequence(
            abs_pattern, IMAGE, MASK, start_frame, "EXR", exr_bit_depth, exr_compression,
            metadata=info.get("metadata"), aov_entries=aov_chain, aov_layout=aov_layout,
            colour_space=colour_space, start_timecode=(tc_ticks, tc_rate, tc_drop),
            fps_rational=openclip_xml.fps_to_rational(fps_label),
        )
        first_frame = image_io.frame_path(abs_pattern, start_frame)
        print(f"[OpenClipExrSequenceWriter] Wrote {IMAGE.shape[0]} EXR file(s), first: {first_frame}")

        sequence_path = os.path.join(folder, f"{stem}.{'#' * frame_padding}.exr")
        return (sequence_path, first_frame, folder, stem, version)


def _folder_of(clip_path: str) -> str:
    """clip_info's clip_path is a .clip file (OpenClipReader) or a folder (EXR Sequence Reader)."""
    if clip_path.lower().endswith(".clip"):
        return str(Path(clip_path).parent)
    return clip_path


def _check_plain_name(value: str, what: str) -> None:
    """Same rule as OpenClipWriter: a wired name must not be able to leave its folder."""
    if "/" in value or "\\" in value or value.strip() in (".", ".."):
        raise ValueError(
            f"[OpenClipExrSequenceWriter] {what} '{value}' must be a plain name: no '/', '\\', '.' or '..'. "
            f"To write somewhere else, change folder."
        )


def _resolve_start_timecode(widget: str, info: dict, rate: int) -> str:
    """Widget > clip_info > 00:00:00:00 (as OpenClipWriter for a new clip)."""
    for what, value in (("start_timecode", widget.strip()), ("clip_info start_timecode", info.get("start_timecode") or "")):
        if value:
            try:
                return timecode.normalize(value, rate)
            except ValueError as e:
                raise ValueError(f"[OpenClipExrSequenceWriter] {what}: {e}") from None
    return timecode.ZERO


def _check_aov_chain(aov_chain: Optional[list], aov_layout: str) -> None:
    """write_sequence checks these too, but only after an overwrite has deleted old frames."""
    if not aov_chain:
        return
    if aov_layout not in image_io.AOV_LAYOUTS:
        raise ValueError(f"Unknown aov_layout '{aov_layout}'. Valid: {sorted(image_io.AOV_LAYOUTS)}")
    names = [entry[0] for entry in aov_chain]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"duplicate AOV name '{duplicates[0]}' -- each entry in the chain must be unique")


def _resolve_stem(folder: str, name: str, version_name: str) -> tuple[str, str]:
    """(file stem, version) for this write. A name without a version token is used as is
    and the version is "" (version_name is not used). With a token, 'next' is one past the
    highest version of the same name in the folder (keeping its padding; the name's own
    token if there is none yet), and a typed v<digits> replaces the token."""
    tokens = list(_VERSION_TOKEN_RE.finditer(name))
    if not tokens:
        if version_name != "next":
            print(f"[OpenClipExrSequenceWriter] WARNING: version_name '{version_name}' ignored -- "
                  f"name '{name}' has no version token (e.g. _v001)")
        return name, ""
    token = tokens[-1]
    head, tail = name[:token.start()], name[token.end():]
    if version_name == "next":
        found = _versions_on_disk(Path(folder), head, tail)
        version = openclip_xml.next_version(dict.fromkeys(found)) if found else f"v{token.group(1)}"
    elif _VERSION_NAME_RE.match(version_name):
        version = version_name
    else:
        raise ValueError(
            f"[OpenClipExrSequenceWriter] version_name '{version_name}' must be 'next' or v<digits> "
            f"(e.g. v003), since it replaces the version token in '{name}'"
        )
    return f"{head}{version}{tail}", version


def _versions_on_disk(folder: Path, head: str, tail: str) -> list[str]:
    """'v<digits>' of every '<head>v<digits><tail>.<frame>.exr' frame in folder."""
    regex = re.compile(re.escape(head) + r"(v\d+)" + re.escape(tail) + r"\.\d+\.exr$")
    try:
        entries = os.listdir(folder)
    except FileNotFoundError:
        return []
    return sorted({m.group(1) for e in entries if (m := regex.match(e))})


def _existing_frames(folder: Path, stem: str) -> list[Path]:
    """'<stem>.<digits>.exr' frames in folder. Glob-escaped, so * ? [ ] in a name can't match
    another sequence's frames; the digit check keeps 'stem.x.exr'-style files out."""
    regex = re.compile(re.escape(stem) + r"\.\d+\.exr$")
    return sorted(f for f in folder.glob(f"{glob.escape(stem)}.*.exr") if regex.match(f.name))


def _delete_existing_frames(folder: Path, stem: str) -> None:
    """Remove the old frames first (overwrite), so a shorter re-render leaves none behind."""
    frames = _existing_frames(folder, stem)
    for f in frames:
        f.unlink()
    print(f"[OpenClipExrSequenceWriter] Overwrite: deleted {len(frames)} existing frame file(s) of '{stem}' in {folder}")


def _collision_message(folder: str, stem: str, version: str, count: int) -> str:
    if version:
        return (
            f"[OpenClipExrSequenceWriter] VERSION ALREADY EXISTS — refusing to write.\n"
            f"Version '{version}' already has {count} frame(s) in '{folder}' ('{stem}.*.exr').\n"
            f"Fix by one of:\n"
            f"  - set version_name to 'next' to auto-pick the next free version\n"
            f"  - enable 'overwrite' to replace '{version}'\n"
            f"  - type a different version_name"
        )
    return (
        f"[OpenClipExrSequenceWriter] SEQUENCE ALREADY EXISTS — refusing to write.\n"
        f"'{stem}' already has {count} frame(s) in '{folder}' ('{stem}.*.exr').\n"
        f"Fix by one of:\n"
        f"  - enable 'overwrite' to replace it\n"
        f"  - type a different name, or add a version token (e.g. {stem}_v001) to version it"
    )
