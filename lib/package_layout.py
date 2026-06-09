from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VALID_LAYOUTS = frozenset({"Standard Flame", "Flat"})


@dataclass
class PackagePaths:
    clip_file: Path
    sidecar_file: Path
    media_dir: Path
    path_pattern_token: str  # relative to clip_file's directory, used in XML


def build(
    layout: str,
    output_dir: str,
    clip_name: str,
    version_name: str,
    file_format: str,
    frame_padding: int,
) -> PackagePaths:
    if layout not in VALID_LAYOUTS:
        raise ValueError(f"Unknown layout '{layout}'. Valid: {sorted(VALID_LAYOUTS)}")
    if frame_padding < 1 or frame_padding > 9:
        raise ValueError(f"frame_padding must be 1–9, got {frame_padding}")

    root = Path(output_dir)
    ext = file_format.lower()
    pad = f"%0{frame_padding}d"

    if layout == "Standard Flame":
        clip_dir = root / clip_name
        media_dir = clip_dir / "versions" / version_name
        return PackagePaths(
            clip_file=clip_dir / f"{clip_name}.clip",
            sidecar_file=media_dir / f"{clip_name}.{version_name}.comfy.json",
            media_dir=media_dir,
            path_pattern_token=f"versions/{version_name}/{clip_name}.{pad}.{ext}",
        )

    # Flat
    return PackagePaths(
        clip_file=root / f"{clip_name}.clip",
        sidecar_file=root / f"{clip_name}.{version_name}.comfy.json",
        media_dir=root,
        path_pattern_token=f"{clip_name}.{pad}.{ext}",
    )


def create_dirs(paths: PackagePaths) -> None:
    paths.clip_file.parent.mkdir(parents=True, exist_ok=True)
    paths.media_dir.mkdir(parents=True, exist_ok=True)
