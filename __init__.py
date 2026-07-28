from __future__ import annotations

from .nodes.colour_transform import OpenClipColourTransform
from .nodes.reader import OpenClipReader
from .nodes.version_selector import OpenClipVersionSelector
from .nodes.writer import OpenClipWriter

NODE_CLASS_MAPPINGS = {
    "OpenClipColourTransform": OpenClipColourTransform,
    "OpenClipReader": OpenClipReader,
    "OpenClipWriter": OpenClipWriter,
    "OpenClipVersionSelector": OpenClipVersionSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "OpenClipColourTransform": "OpenClip Colour Transform",
    "OpenClipReader": "OpenClip Reader",
    "OpenClipWriter": "OpenClip Writer",
    "OpenClipVersionSelector": "OpenClip Version Selector",
}

WEB_DIRECTORY = "./web"
