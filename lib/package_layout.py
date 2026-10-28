from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VALID_LAYOUTS = frozenset({"Standard Flame", "Flat"})


@dataclass
class PackagePaths:
    clip_file: Path
    sidecar_file: Path
    media_dir: Path
    frame_stem: str          # frame filename before the frame number, e.g. "shot" or "shot_v001"
    path_pattern_token: str  # relative to clip_file's directory, used in XML


def build(
    layout: str,
    output_dir: str,
    clip_name: str,
    version_name: str,
    file_format: str,
    frame_padding: int,
    version_in_filename: bool = False,
) -> PackagePaths:
    """Resolve where a clip's .clip file, frames and sidecar go.

    Standard Flame puts <clip_name>.clip in output_dir next to a <clip_name>/ media folder:
    one <version>/ subfolder per version when version_in_filename is off, or every
    version's frames side by side as <clip_name>_<version>.####.ext when it is on (the
    layout Flame itself writes). A clip already on disk in the pre-2026-10-27 layout
    (<clip_name>/<clip_name>.clip + versions/<version>/) keeps that layout, so it can
    still be appended to.
    """
    if layout not in VALID_LAYOUTS:
        raise ValueError(f"Unknown layout '{layout}'. Valid: {sorted(VALID_LAYOUTS)}")
    if frame_padding < 1 or frame_padding > 9:
        raise ValueError(f"frame_padding must be 1–9, got {frame_padding}")

    root = Path(output_dir)
    ext = file_format.lower()
    pad = f"%0{frame_padding}d"
    stem = f"{clip_name}_{version_name}" if version_in_filename else clip_name

    if layout == "Flat":
        clip_dir, media_dir = root, root
    elif _is_legacy_standard(root, clip_name):
        clip_dir = root / clip_name
        media_dir = clip_dir / "versions" / version_name
    elif version_in_filename:
        clip_dir, media_dir = root, root / clip_name
    else:
        clip_dir, media_dir = root, root / clip_name / version_name

    rel_dir = media_dir.relative_to(clip_dir).as_posix()
    return PackagePaths(
        clip_file=clip_dir / f"{clip_name}.clip",
        sidecar_file=media_dir / f"{clip_name}.{version_name}.comfy.json",
        media_dir=media_dir,
        frame_stem=stem,
        path_pattern_token=f"{stem}.{pad}.{ext}" if rel_dir == "." else f"{rel_dir}/{stem}.{pad}.{ext}",
    )


def _is_legacy_standard(root: Path, clip_name: str) -> bool:
    # Backwards compatibility only; drop once no clips in the old layout are in use.
    return not (root / f"{clip_name}.clip").exists() and (root / clip_name / f"{clip_name}.clip").exists()


def create_dirs(paths: PackagePaths) -> None:
    paths.clip_file.parent.mkdir(parents=True, exist_ok=True)
    paths.media_dir.mkdir(parents=True, exist_ok=True)
