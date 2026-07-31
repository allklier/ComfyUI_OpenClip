from __future__ import annotations

import numpy as np
import torch

OUTPUT_COLOUR_SPACE = "Rec.1886 Rec.709 - Display"
OUTPUT_VIEW = "ACES 2.0 - SDR 100 nits (Rec.709)"


def apply_colour_transform(
    images: torch.Tensor,
    config_path: str,
    input_space: str,
    output_space: str = OUTPUT_COLOUR_SPACE,
    view: str = OUTPUT_VIEW,
) -> torch.Tensor:
    """Apply an OCIO Display/View transform to an IMAGE tensor in-place on a copy.

    images:       (N, H, W, 3) float32 tensor
    config_path:  path to an OCIO config file
    input_space:  source colour space name as registered in the config
    output_space: destination display colour space name (default: Rec.1886 Rec.709 - Display)
    view:         view transform name for that display (default: ACES 2.0 - SDR 100 nits (Rec.709));
                  this is what applies the ACES Output Transform (tone-mapping / gamut compression) -
                  a plain colour-space-to-colour-space conversion into a Display space skips it and
                  produces out-of-range (negative / >1.0) values on bright, saturated source pixels.

    Returns a new (N, H, W, 3) float32 tensor; the input is not modified.
    Raises ImportError if PyOpenColorIO (opencolorio) is not installed.
    """
    try:
        import PyOpenColorIO as ocio
    except ImportError as exc:
        raise ImportError(
            "PyOpenColorIO is required for colour transforms. "
            "Install it with: pip install opencolorio"
        ) from exc

    config = ocio.Config.CreateFromFile(config_path)
    transform = ocio.DisplayViewTransform()
    transform.setSrc(input_space)
    transform.setDisplay(output_space)
    transform.setView(view)
    processor = config.getProcessor(transform)
    cpu = processor.getDefaultCPUProcessor()

    # Independent float32 copy so the input tensor is never modified
    arr = images.detach().cpu().float().numpy().copy()
    N, H, W, C = arr.shape
    if C != 3:
        raise ValueError(f"Expected 3-channel IMAGE tensor, got {C} channels")

    flat = arr.reshape(N * H * W, C)  # view into arr; applyRGB works in-place
    cpu.applyRGB(flat)
    return torch.from_numpy(arr)
