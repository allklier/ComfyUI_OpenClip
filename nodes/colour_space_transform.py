from __future__ import annotations

from typing import Optional

import torch

from ..lib import colour_transform as _ct
from .colour_transform import _default_ocio_config

# Friendly label -> real OCIO colour-space name, verified against the actual
# production config on akflame5 (/opt/Autodesk/colour_mgmt/configs/
# flame_configs/2026.0/aces2.0_config/config.ocio, 2026-08-19) -- not guessed.
# All but Canon, sRGB and BT.1886 are the config's already-linear
# "Linear <camera gamut>" entry, matching the plate state this node expects (see
# class docstring): a camera log curve should already have been decoded by the
# time an image reaches this node, only the native-gamut identity remains to
# reconcile. Canon has no such paired linear entry in this config -- only the
# log-encoded name exists, kept here as the best available option but unverified
# as a source for a plain scene-to-scene conversion; flag this if Canon footage
# is actually used. sRGB and BT.1886 are gamma-encoded scene-referred entries
# (Utility/Texture family) rather than "Linear ..." ones -- there is no separate
# already-linear entry for either in this config, so the encoded name is both
# the best and the correct choice (see the BT.1886 entry's own comment below).
# ACEScg listed first -- it's the pipeline's own working space (see the
# COLOUR PRIMARIES CONTRACT note in realplate_moduleA.json), the most common
# pick on both ends of this node.
ACESCG_LABEL = "ACEScg (already the working space)"
CAMERA_COLOUR_SPACES = {
    ACESCG_LABEL: "ACEScg",
    "ARRI LogC3 (Wide Gamut 3)": "Linear ARRI Wide Gamut 3",
    "ARRI LogC4 (Wide Gamut 4)": "Linear ARRI Wide Gamut 4",
    "RED Log3G10 (REDWideGamutRGB)": "Linear REDWideGamutRGB",
    "Sony S-Log3 (S-Gamut3)": "Linear S-Gamut3",
    "Sony S-Log3 (S-Gamut3.Cine)": "Linear S-Gamut3.Cine",
    "Canon Log2/Log3 (Cinema Gamut D55, unverified)": "CanonLog3 CinemaGamut D55",
    "Panasonic V-Log (V-Gamut)": "Linear V-Gamut",
    "Blackmagic Film (Wide Gamut Gen5)": "Linear BMD WideGamut Gen5",
    "DaVinci Wide Gamut": "Linear DaVinci WideGamut",
    "sRGB (Rec.709, e.g. AI-generated or stock stills)": "sRGB Encoded Rec.709 (sRGB)",
    # Verified against the same config, 2026-09-23: "Rec.1886 Rec.709 - Display" is
    # itself a display_colorspaces entry (from_display_reference, not
    # from_scene_reference) -- not the scene-referred family this dropdown is built
    # from, and Flame's declared clip colourSpace tag for a video-gamma export names
    # it that way regardless. "Gamma 2.4 Encoded Rec.709" is the scene-referred
    # counterpart (same Rec.709 primaries matrix as the sRGB entry above, pure 2.4
    # power curve matching BT.1886's reference EOTF exactly; config alias
    # `rec709_display` confirms it) -- config.getProcessor() resolves both names to
    # numerically identical output (checked at grey 0.0/0.18/0.5/0.9/1.0 via
    # PyOpenColorIO's CPU processor), so this entry is the correct source for a
    # clip declaring "Rec.1886 Rec.709 - Display" (e.g. a Flame broadcast-gamma
    # export), distinct from the sRGB curve just above it (measurably different:
    # 0.5 decodes to 0.1895 here vs 0.2140 for sRGB).
    "Rec.1886/BT.1886 (Rec.709, e.g. Flame broadcast-gamma export)": "Gamma 2.4 Encoded Rec.709",
}
CUSTOM_LABEL = "Custom..."
SAME_AS_INPUT_LABEL = "Same as input (via pipe)"

# target_colour_space's own dropdown: ACEScg and "same as input" first (the
# two most common destinations -- stay in the working space, or round-trip
# back to whatever gamut the plate arrived in), then the same curated camera
# list as `source` (covers a plain Rec.709 target too, via the sRGB entry),
# then the Custom... escape hatch.
TARGET_COLOUR_SPACES = (
    [ACESCG_LABEL, SAME_AS_INPUT_LABEL]
    + [label for label in CAMERA_COLOUR_SPACES if label != ACESCG_LABEL]
    + [CUSTOM_LABEL]
)


class OpenClipColourSpaceTransform:
    """Plain scene-to-scene OCIO colour-space conversion -- reconciles a
    plate's native gamut/encoding into a common working space (default
    ACEScg), with no Display/View or tone-mapping involved.

    Distinct from OpenClipColourTransform, which applies a Display/View
    transform for scene-to-display work (Flame's per-clip view-transform
    picker). See lib/colour_transform.py's apply_colourspace_transform()
    docstring for why that node is architecturally the wrong tool here.

    `source` offers common camera-footage gamuts by familiar name; pick
    Custom... and fill `custom_source` for anything not listed. Every listed
    option except Canon's is the config's already-linear entry, matching a
    plate that arrived via OpenClipReader (which decodes camera log curves
    on read, per this project's linear-on-read contract) -- Canon has no such
    paired entry in the config this was verified against.

    `target_colour_space` is a dropdown, not free text -- same failure mode
    as `source` (a mistyped OCIO name) was possible before this. Options:
    ACEScg; "Same as input (via pipe)", which reads the real space name from
    `same_as_input_colour_space` (wire OpenClipClipInfo's `colour_space`
    output into it -- note that value is the verbatim tag embedded in the
    source file/metadata, not cross-checked against this OCIO config, so an
    unresolvable name still surfaces as OCIO's own error at execute time);
    the same curated camera list as `source`; or Custom..., filling
    `custom_target_colour_space`.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "IMAGE": ("IMAGE",),
                "ocio_config": ("STRING", {"default": _default_ocio_config()}),
                "source": (list(CAMERA_COLOUR_SPACES) + [CUSTOM_LABEL],),
                "custom_source": ("STRING", {"default": "", "tooltip": "Used only when source is Custom..."}),
                "target_colour_space": (TARGET_COLOUR_SPACES,),
                "custom_target_colour_space": (
                    "STRING",
                    {"default": "", "tooltip": "Used only when target_colour_space is Custom..."},
                ),
            },
            "optional": {
                "MASK": ("MASK",),
                "same_as_input_colour_space": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Connect OpenClipClipInfo's colour_space output here. "
                            "Used only when target_colour_space is 'Same as input (via pipe)'."
                        ),
                    },
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("IMAGE", "MASK")
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(
        self,
        IMAGE: torch.Tensor,
        ocio_config: str,
        source: str,
        custom_source: str,
        target_colour_space: str,
        custom_target_colour_space: str = "",
        same_as_input_colour_space: Optional[str] = None,
        MASK: Optional[torch.Tensor] = None,
    ) -> tuple:
        ocio_config = ocio_config.strip()
        if not ocio_config:
            raise ValueError("ocio_config path is required")

        if source == CUSTOM_LABEL:
            input_space = custom_source.strip()
            if not input_space:
                raise ValueError("custom_source is required when source is Custom...")
        else:
            input_space = CAMERA_COLOUR_SPACES[source]

        if target_colour_space == CUSTOM_LABEL:
            output_space = custom_target_colour_space.strip()
            if not output_space:
                raise ValueError("custom_target_colour_space is required when target_colour_space is Custom...")
        elif target_colour_space == SAME_AS_INPUT_LABEL:
            output_space = (same_as_input_colour_space or "").strip()
            if not output_space:
                raise ValueError(
                    "same_as_input_colour_space must be connected when target_colour_space "
                    "is 'Same as input (via pipe)'"
                )
        else:
            output_space = CAMERA_COLOUR_SPACES[target_colour_space]

        output = _ct.apply_colourspace_transform(IMAGE, ocio_config, input_space, output_space)
        # No mask connected means no matte, not "nothing here" -- standard VFX
        # convention treats an absent matte as fully opaque.
        out_mask = MASK if MASK is not None else torch.ones(
            IMAGE.shape[0], IMAGE.shape[1], IMAGE.shape[2]
        )
        return (output, out_mask)
