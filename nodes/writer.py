from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import torch

from ..lib import image_io, openclip_xml, package_layout
from ..lib.openclip_xml import ClipFormat, ClipSpan, ClipVersion


class OpenClipWriter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "IMAGE": ("IMAGE",),
                "clip_path": ("STRING", {"default": ""}),
                "clip_name": ("STRING", {"default": ""}),
                "clip_filename": ("STRING", {"default": "$path/$clip_name.clip"}),
                "version_name": ("STRING", {"default": "v001"}),
                "fps": (openclip_xml.FPS_OPTIONS, {"default": "24"}),
                "start_frame": ("INT", {"default": 1001, "min": 0, "max": 999999}),
                "frame_padding": ("INT", {"default": 4, "min": 1, "max": 9}),
                "file_format": (["EXR", "PNG"],),
                "exr_bit_depth": (["half (16-bit)", "float (32-bit)"],),
                "exr_compression": (["ZIP", "PIZ", "DWAB"],),
                "publish": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "MASK": ("MASK",),
                "CLIP_METADATA": ("CLIP_METADATA",),
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
        clip_path: str,
        clip_name: str,
        clip_filename: str,
        version_name: str,
        fps: str,
        start_frame: int,
        frame_padding: int,
        file_format: str,
        exr_bit_depth: str,
        exr_compression: str,
        colour_space: str = "Rec.1886 Rec.709 - Display",
        layout: str = "Standard Flame",
        publish: bool = False,
        MASK: Optional[torch.Tensor] = None,
        CLIP_METADATA: Optional[dict] = None,
        extra_pnginfo: Optional[dict] = None,
        prompt=None,
    ):
        output_dir, clip_name = _resolve_destination(
            clip_path.strip(), clip_name.strip(), clip_filename.strip()
        )

        paths = package_layout.build(
            layout, output_dir, clip_name, version_name, file_format, frame_padding
        )
        package_layout.create_dirs(paths)

        abs_pattern = str(paths.media_dir / f"{clip_name}.%0{frame_padding}d.{file_format.lower()}")
        image_io.write_sequence(
            abs_pattern, IMAGE, MASK, start_frame, file_format, exr_bit_depth, exr_compression,
            metadata=CLIP_METADATA,
        )

        n_frames, height, width = IMAGE.shape[0], IMAGE.shape[1], IMAGE.shape[2]
        n_channels = 4 if MASK is not None else 3
        fmt = ClipFormat(
            width=width,
            height=height,
            n_channels=n_channels,
            bit_depth=exr_bit_depth,
            fps=fps,
            colour_space=colour_space,
            file_format=file_format,
            compression=exr_compression,
        )

        sidecar_rel = _write_publish_sidecar(paths, extra_pnginfo) if publish else None
        new_version = ClipVersion(
            uid=version_name,
            name=version_name,
            spans=[ClipSpan(path=abs_pattern, start_frame=start_frame, duration=n_frames)],
            publish_path=sidecar_rel,
        )

        if paths.clip_file.exists():
            xml_bytes = _merge_version(paths.clip_file, clip_name, version_name, new_version, fmt)
        else:
            xml_bytes = openclip_xml.generate(clip_name, {version_name: new_version}, version_name, fmt)
        paths.clip_file.write_bytes(xml_bytes)

        return (str(paths.clip_file),)


def _merge_version(
    clip_file: Path,
    clip_name: str,
    version_name: str,
    new_version: ClipVersion,
    fmt: ClipFormat,
) -> bytes:
    existing = openclip_xml.parse(str(clip_file))
    if version_name in existing.versions:
        raise ValueError(
            f"Version '{version_name}' already exists in '{clip_file.name}'. "
            f"Use a different version name."
        )
    existing_fmt = openclip_xml.read_format(str(clip_file))
    mismatches = _format_mismatches(existing_fmt, fmt)
    if mismatches:
        detail = ", ".join(f"{k}: existing={old} new={nw}" for k, old, nw in mismatches)
        raise ValueError(f"Format mismatch with '{clip_file.name}': {detail}")
    merged = {**existing.versions, version_name: new_version}
    return openclip_xml.generate(clip_name, merged, version_name, fmt)


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


def _resolve_destination(clip_path_in: str, clip_name_in: str, clip_filename: str) -> tuple[str, str]:
    """Expand $path/$clip_name tokens in clip_filename, then split into (output_dir, clip_name).

    $path  → clip_path_in, normalised to a folder (parent dir if a .clip file was supplied).
    $clip_name → clip_name_in.

    The expanded result is normalised (resolves ..) and split on the last component.
    The caller can use $path/../sibling/$clip_name to navigate relative to the source clip.
    """
    if not clip_filename:
        raise ValueError("clip_filename is required")

    folder = _normalize_to_folder(clip_path_in)
    expanded = clip_filename

    if "$path" in expanded:
        if not folder:
            raise ValueError("clip_path is required when clip_filename contains $path")
        expanded = expanded.replace("$path", folder)

    if "$clip_name" in expanded:
        if not clip_name_in:
            raise ValueError("clip_name is required when clip_filename contains $clip_name")
        expanded = expanded.replace("$clip_name", clip_name_in)

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
