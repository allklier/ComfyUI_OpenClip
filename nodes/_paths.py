from __future__ import annotations

from pathlib import Path


def resolve_clip_path(clip_path: str) -> str:
    """Return an absolute path for a .clip file.

    Absolute paths are returned unchanged.
    Relative paths are resolved against ComfyUI's input directory when
    folder_paths is available, or against cwd in test context.
    """
    if Path(clip_path).is_absolute():
        return clip_path

    try:
        import folder_paths
        return str(Path(folder_paths.get_input_directory()) / clip_path)
    except ImportError:
        return clip_path
