from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Optional

import torch

from ..lib import colour_transform as _ct

_FLAME_GLOB = "/opt/Autodesk/colour_mgmt/configs/flame_configs/*/aces2.0_config/config.ocio"


def _pick_most_recent_flame_config(candidates: list[str]) -> str:
    """Return the path with the highest Flame version number, or '' if empty."""
    if not candidates:
        return ""

    def _version_key(p: str) -> tuple[int, ...]:
        ver = Path(p).parent.parent.name
        try:
            return tuple(int(x) for x in ver.split("."))
        except ValueError:
            return (0,)

    return max(candidates, key=_version_key)


def _default_ocio_config() -> str:
    """Return the best available OCIO config path:
    1. $OCIO environment variable if set
    2. Most recent Flame install under /opt/Autodesk/colour_mgmt/
    3. Empty string (user must fill in manually)
    """
    env_val = os.environ.get("OCIO", "").strip()
    if env_val:
        return env_val
    return _pick_most_recent_flame_config(glob.glob(_FLAME_GLOB))


class OpenClipColourTransform:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "IMAGE": ("IMAGE",),
                "ocio_config": ("STRING", {"default": _default_ocio_config()}),
                "input_colour_space": ("STRING", {"default": ""}),
            },
            "optional": {
                "MASK": ("MASK",),
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
        input_colour_space: str,
        MASK: Optional[torch.Tensor] = None,
    ) -> tuple:
        ocio_config = ocio_config.strip()
        input_colour_space = input_colour_space.strip()
        if not ocio_config:
            raise ValueError("ocio_config path is required")
        if not input_colour_space:
            raise ValueError("input_colour_space is required")

        output = _ct.apply_colour_transform(IMAGE, ocio_config, input_colour_space)
        out_mask = MASK if MASK is not None else torch.zeros(
            IMAGE.shape[0], IMAGE.shape[1], IMAGE.shape[2]
        )
        return (output, out_mask)
