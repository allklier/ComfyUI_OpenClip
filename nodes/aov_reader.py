from __future__ import annotations

import os

from ..lib import image_io

# unique_id -> the (abs_pattern, start_frame) this node instance last read, so IS_CHANGED
# can still fingerprint when aov_handle arrives as None (it's a linked input -- same
# mechanism and same reason as OpenClipReader.IS_CHANGED, see there for the full story).
_LAST_READ_PLAN: dict[str, tuple[str, int]] = {}


def _fingerprint(abs_pattern: str, start_frame: int) -> str:
    frame_stat = os.stat(image_io.frame_path(abs_pattern, start_frame))
    return f"{frame_stat.st_mtime_ns}:{frame_stat.st_size}"


class OpenClipAOVReader:
    """Extracts one named EXR part from an OpenClipReader's aov_handle.

    Generic and lazy: the handle just locates the file(s); nothing is decoded until
    this node executes, and only the one part named `part_name` is decoded -- an
    unrequested part (e.g. a large Cryptomatte) is never touched. Add one instance per
    AOV you actually want; a clip with no AOVs costs zero of these.

    part_name is typed by hand from the AOVS section of the reader's clip_report (copy the
    name from there) -- ComfyUI has no way for one node to populate another's widget
    with a dropdown, so this is copy-and-retype.

    Output shape: a part with a single channel goes entirely to `matte`, `image` is
    None -- there's nothing meaningfully multi-channel to return. A part with >=2
    channels populates both: `image` is all of them, `matte` is the "R" channel if
    present, else channel 0 (looked up by name, never assumed positionally). Either
    output is None if `part_name` isn't found in any frame of the handle's range --
    a real absence, not a fabricated zero-filled placeholder.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "aov_handle": ("AOV_HANDLE",),
                "part_name": ("STRING", {"default": ""}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "matte")
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(self, aov_handle: dict, part_name: str, unique_id=None):
        part_name = part_name.strip()
        if not part_name:
            raise ValueError("part_name is required")

        abs_pattern = aov_handle["abs_pattern"]
        start_frame = aov_handle["start_frame"]
        end_frame = aov_handle["end_frame"]

        if unique_id is not None:
            _LAST_READ_PLAN[str(unique_id)] = (abs_pattern, start_frame)

        image, matte = image_io.read_named_part(abs_pattern, start_frame, end_frame, part_name)
        return (image, matte)

    # Same contract as OpenClipReader._UNFINGERPRINTABLE.
    _UNFINGERPRINTABLE = ""

    @classmethod
    def IS_CHANGED(cls, aov_handle=None, part_name=None, unique_id=None, **kwargs):
        """Same reasoning as OpenClipReader.IS_CHANGED: aov_handle is a linked input, so
        it arrives as None here (ComfyUI's mark_missing() substitutes None for every
        link-driven argument to IS_CHANGED). Must never raise -- see OpenClipReader for
        why a raise here would defeat caching for the entire downstream graph, not just
        this node.
        """
        if isinstance(aov_handle, dict):
            try:
                return _fingerprint(aov_handle["abs_pattern"], aov_handle["start_frame"])
            except Exception:
                return cls._UNFINGERPRINTABLE

        remembered = _LAST_READ_PLAN.get(str(unique_id)) if unique_id is not None else None
        if remembered is not None:
            try:
                return _fingerprint(*remembered)
            except Exception:
                return cls._UNFINGERPRINTABLE

        return cls._UNFINGERPRINTABLE
