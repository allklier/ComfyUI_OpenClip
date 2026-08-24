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
                "clip_filename": ("STRING", {"default": "$(path)/$(clip_name).clip"}),
                "version_name": ("STRING", {"default": "next"}),
                "overwrite": ("BOOLEAN", {"default": False}),
                "include_version_in_filename": ("BOOLEAN", {"default": False}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0, "step": 0.001}),
                "start_frame": ("INT", {"default": 1001, "min": 0, "max": 999999}),
                "frame_padding": ("INT", {"default": 4, "min": 1, "max": 9}),
                "file_format": (["EXR", "PNG"],),
                "exr_bit_depth": (["half (16-bit)", "float (32-bit)"],),
                "exr_compression": (["ZIP", "PIZ", "DWAB"],),
                "aov_layout": (["Multichannel", "Separate Files"],),
                "publish": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "MASK": ("MASK",),
                "CLIP_METADATA": ("CLIP_METADATA",),
                "NORMAL": ("IMAGE",),
                "NORMAL_WORLD": ("IMAGE",),
                "DEPTH": ("MASK",),
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
        fps: float,
        start_frame: int,
        frame_padding: int,
        file_format: str,
        exr_bit_depth: str,
        exr_compression: str,
        aov_layout: str = "Multichannel",
        colour_space: str = "Rec.1886 Rec.709 - Display",
        layout: str = "Standard Flame",
        publish: bool = False,
        overwrite: bool = False,
        include_version_in_filename: bool = False,
        MASK: Optional[torch.Tensor] = None,
        CLIP_METADATA: Optional[dict] = None,
        NORMAL: Optional[torch.Tensor] = None,
        NORMAL_WORLD: Optional[torch.Tensor] = None,
        DEPTH: Optional[torch.Tensor] = None,
        extra_pnginfo: Optional[dict] = None,
        prompt=None,
    ):
        output_dir, clip_name = _resolve_destination(
            clip_path.strip(), clip_name.strip(), clip_filename.strip()
        )

        # clip_file's location doesn't depend on version_name in either layout (see
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

        paths = package_layout.build(
            layout, output_dir, clip_name, version_name, file_format, frame_padding
        )
        package_layout.create_dirs(paths)

        frame_stem = f"{clip_name}.{version_name}" if include_version_in_filename else clip_name

        if version_exists and overwrite:
            _delete_existing_frames(paths.media_dir, frame_stem, file_format)

        abs_pattern = str(paths.media_dir / f"{frame_stem}.%0{frame_padding}d.{file_format.lower()}")
        aovs = {"normal": NORMAL, "normal_world": NORMAL_WORLD, "depth": DEPTH}
        image_io.write_sequence(
            abs_pattern, IMAGE, MASK, start_frame, file_format, exr_bit_depth, exr_compression,
            metadata=CLIP_METADATA, aovs=aovs, aov_layout=aov_layout,
        )

        n_frames, height, width = IMAGE.shape[0], IMAGE.shape[1], IMAGE.shape[2]
        print(f"[OpenClipWriter] First frame written: {abs_pattern % start_frame}")
        print(f"[OpenClipWriter] Wrote {n_frames} {file_format} file(s) to {paths.media_dir}")
        n_channels = 4 if MASK is not None else 3
        fps_label = openclip_xml.fps_label_from_float(fps)
        fmt = ClipFormat(
            width=width,
            height=height,
            n_channels=n_channels,
            bit_depth=exr_bit_depth,
            fps=fps_label,
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
        print(f"[OpenClipWriter] Wrote clip file: {paths.clip_file}")

        return (str(paths.clip_file),)


def _merge_version(
    clip_file: Path,
    clip_name: str,
    version_name: str,
    new_version: ClipVersion,
    fmt: ClipFormat,
) -> bytes:
    existing = openclip_xml.parse(str(clip_file))
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


def _delete_existing_frames(media_dir: Path, frame_stem: str, file_format: str) -> None:
    """Remove this version's previously rendered frames before rewriting (overwrite=True).

    Deletes before writing rather than just letting new frames land on top, so a shorter
    re-render doesn't leave stale extra frames from the old render behind.
    """
    pattern = f"{frame_stem}.*.{file_format.lower()}"
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
            raise ValueError("clip_path is required when clip_filename contains $(path)")
        expanded = expanded.replace("$(path)", folder)

    if "$(clip_name)" in expanded:
        if not clip_name_in:
            raise ValueError("clip_name is required when clip_filename contains $(clip_name)")
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
