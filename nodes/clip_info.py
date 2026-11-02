from __future__ import annotations

from ._clip_info import build_report


class OpenClipClipInfo:
    """Unpacks a reader's clip_info into one output per value, plus the same clip_report
    text the reader shows. For wiring single values into other nodes (e.g. colour_space
    into OpenClipColourSpaceTransform's 'Same as input (via pipe)'); OpenClipWriter takes
    clip_info whole and doesn't need this."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"clip_info": ("CLIP_INFO",)}}

    RETURN_TYPES = (
        "STRING", "STRING", "STRING", "INT", "STRING", "FLOAT", "STRING", "STRING", "INT",
        "CLIP_METADATA", "STRING", "STRING",
    )
    RETURN_NAMES = (
        "clip_path", "clip_name", "version_name", "start_frame", "start_timecode", "fps",
        "colour_space", "format_version", "frame_padding", "metadata", "bit_depth", "clip_report",
    )
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(self, clip_info: dict):
        i = clip_info
        return (
            i["clip_path"], i["clip_name"], i["version_name"], i["start_frame"], i["start_timecode"],
            i["fps"], i["colour_space"], i["format_version"], i["frame_padding"], i["metadata"],
            i.get("bit_depth", ""), build_report(i),
        )
