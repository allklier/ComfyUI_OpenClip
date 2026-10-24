from __future__ import annotations

from .nodes.aov_reader import OpenClipAOVReader
from .nodes.aov_writer import OpenClipAOVWriter
from .nodes.colour_space_transform import OpenClipColourSpaceTransform
from .nodes.colour_transform import OpenClipColourTransform
from .nodes.exr_sequence_reader import ExrSequenceReader
from .nodes.reader import OpenClipReader
from .nodes.writer import OpenClipWriter

NODE_CLASS_MAPPINGS = {
    "OpenClipAOVReader": OpenClipAOVReader,
    "OpenClipAOVWriter": OpenClipAOVWriter,
    "OpenClipColourSpaceTransform": OpenClipColourSpaceTransform,
    "OpenClipColourTransform": OpenClipColourTransform,
    "OpenClipExrSequenceReader": ExrSequenceReader,
    "OpenClipReader": OpenClipReader,
    "OpenClipWriter": OpenClipWriter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "OpenClipAOVReader": "OpenClip AOV Reader",
    "OpenClipAOVWriter": "OpenClip AOV Writer",
    "OpenClipColourSpaceTransform": "OpenClip Colour Space Transform",
    "OpenClipColourTransform": "OpenClip Colour Transform",
    "OpenClipExrSequenceReader": "OpenClip EXR Sequence Reader",
    "OpenClipReader": "OpenClip Reader",
    "OpenClipWriter": "OpenClip Writer",
}

WEB_DIRECTORY = "./web"
