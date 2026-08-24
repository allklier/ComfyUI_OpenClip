from __future__ import annotations

from .nodes.colour_space_transform import OpenClipColourSpaceTransform
from .nodes.colour_transform import OpenClipColourTransform
from .nodes.reader import OpenClipReader
from .nodes.writer import OpenClipWriter

NODE_CLASS_MAPPINGS = {
    "OpenClipColourSpaceTransform": OpenClipColourSpaceTransform,
    "OpenClipColourTransform": OpenClipColourTransform,
    "OpenClipReader": OpenClipReader,
    "OpenClipWriter": OpenClipWriter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "OpenClipColourSpaceTransform": "OpenClip Colour Space Transform",
    "OpenClipColourTransform": "OpenClip Colour Transform",
    "OpenClipReader": "OpenClip Reader",
    "OpenClipWriter": "OpenClip Writer",
}

WEB_DIRECTORY = "./web"
