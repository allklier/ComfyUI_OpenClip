from __future__ import annotations

import numpy as np
import torch

OUTPUT_COLOUR_SPACE = "Rec.1886 Rec.709 - Display"


def apply_colour_transform(
    images: torch.Tensor,
    config_path: str,
    input_space: str,
    output_space: str = OUTPUT_COLOUR_SPACE,
) -> torch.Tensor:
    """Apply an OCIO colour transform to an IMAGE tensor in-place on a copy.

    images:      (N, H, W, 3) float32 tensor
    config_path: path to an OCIO config file
    input_space: source colour space name as registered in the config
    output_space: destination colour space name (default: Rec.1886 Rec.709 - Display)

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
    processor = config.getProcessor(input_space, output_space)
    cpu = processor.getDefaultCPUProcessor()

    # Independent float32 copy so the input tensor is never modified
    arr = images.detach().cpu().float().numpy().copy()
    N, H, W, C = arr.shape
    if C != 3:
        raise ValueError(f"Expected 3-channel IMAGE tensor, got {C} channels")

    flat = arr.reshape(N * H * W, C)  # view into arr; applyRGB works in-place
    cpu.applyRGB(flat)
    return torch.from_numpy(arr)
