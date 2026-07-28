from __future__ import annotations

from ..lib import openclip_xml
from ._paths import resolve_clip_path


class OpenClipVersionSelector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "clip_path": ("STRING", {"default": ""}),
                "selected_version": ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("clip_path", "selected_version", "current_version", "available_versions")
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(self, clip_path: str, selected_version: str):
        clip_path = resolve_clip_path(clip_path.strip())
        if not clip_path:
            raise ValueError("clip_path is required")

        parsed = openclip_xml.parse(clip_path)

        if not selected_version:
            selected_version = parsed.current_version
        elif selected_version not in parsed.versions:
            available = sorted(parsed.versions.keys())
            raise ValueError(
                f"Version '{selected_version}' not found in clip. "
                f"Available: {available}"
            )

        available_versions = _format_available_versions(parsed.versions.keys(), parsed.current_version)
        return clip_path, selected_version, parsed.current_version, available_versions


def _format_available_versions(version_names, current_version: str) -> str:
    return "\n".join(
        f"{name}  (current)" if name == current_version else name
        for name in sorted(version_names)
    )
