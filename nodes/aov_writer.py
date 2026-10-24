from __future__ import annotations

from typing import Optional

import torch


class OpenClipAOVWriter:
    """Adds one named AOV to a write chain -- daisy-chain as many as you need, then
    feed the last one into OpenClipWriter's aov_chain input.

    Exactly one of `image`/`matte` must be connected: `image` ([B,H,W,C]) is written as
    that many channels; `matte` ([B,H,W]) is written as a single channel named "R" (so
    reading it back through OpenClipAOVReader lands it on the matte output again, per
    that node's single-channel rule). Providing both or neither raises -- this is an
    artist decision, not something to guess at.

    Each name in the chain must be unique; a duplicate raises here (pinpointing which
    node introduced it) as well as in OpenClipWriter (which also validates the whole
    chain, in case one was ever built by some other means).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "name": ("STRING", {"default": ""}),
            },
            "optional": {
                "image": ("IMAGE",),
                "matte": ("MASK",),
                "aov_chain": ("AOV_WRITE_CHAIN",),
            },
        }

    RETURN_TYPES = ("AOV_WRITE_CHAIN",)
    RETURN_NAMES = ("aov_chain",)
    FUNCTION = "execute"
    CATEGORY = "OpenClip"

    def execute(
        self,
        name: str,
        image: Optional[torch.Tensor] = None,
        matte: Optional[torch.Tensor] = None,
        aov_chain: Optional[list] = None,
    ):
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        if image is not None and matte is not None:
            raise ValueError(f"AOV '{name}': connect exactly one of image/matte, not both")
        if image is None and matte is None:
            raise ValueError(f"AOV '{name}': connect exactly one of image/matte")

        chain = list(aov_chain) if aov_chain else []
        if any(existing_name == name for existing_name, _image, _matte in chain):
            raise ValueError(f"duplicate AOV name '{name}' -- each entry in the chain must be unique")

        chain.append((name, image, matte))
        return (chain,)
