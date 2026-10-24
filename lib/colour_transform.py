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
    inverse: bool = False,
) -> torch.Tensor:
    """Apply an OCIO Display/View transform to an IMAGE tensor in-place on a copy.

    images:       (N, H, W, 3) float32 tensor
    config_path:  path to an OCIO config file
    input_space:  source colour space name as registered in the config (forward),
                  or the destination scene-referred colour space name (inverse)
    output_space: destination display colour space name (default: Rec.1886 Rec.709 - Display)
    view:         view transform name for that display (default: ACES 2.0 - SDR 100 nits (Rec.709));
                  this is what applies the ACES Output Transform (tone-mapping / gamut compression) -
                  a plain colour-space-to-colour-space conversion into a Display space skips it and
                  produces out-of-range (negative / >1.0) values on bright, saturated source pixels.
    inverse:      False (default) transforms input_space -> (output_space, view): scene-referred to
                  display-referred. True reverses it: the incoming image is assumed to already be in
                  (output_space, view) -- e.g. a display-referred plate such as an AI-generated
                  background -- and is transformed back into input_space, scene-referred. Values
                  already clipped to display range on the way out (e.g. blown highlights) are not
                  recoverable; this inverts the transform, not information lost before it ran.

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
    direction = ocio.TRANSFORM_DIR_INVERSE if inverse else ocio.TRANSFORM_DIR_FORWARD
    processor = config.getProcessor(transform, direction)
    return _apply_processor(images, processor)


def apply_colourspace_transform(
    images: torch.Tensor,
    config_path: str,
    input_space: str,
    output_space: str = "ACEScg",
) -> torch.Tensor:
    """Plain scene-to-scene OCIO colour-space conversion -- no Display, no View,
    no tone-mapping. For reconciling two already scene-referred plates (e.g.
    different camera-native gamuts, or an sRGB-curve-encoded utility space)
    into one common working space.

    This is deliberately a different OCIO call from apply_colour_transform()'s
    DisplayViewTransform, not a mode of it: a display colour space's own
    definition in an OCIO v2/ACES config is encoding-only (EOTF + primaries) --
    the actual tone-mapping/gamut compression lives on the View, which only
    gets applied via DisplayViewTransform. input_space/output_space here are
    both ordinary scene-referred colorspaces: config.getProcessor(src, dst) is
    correct and complete on its own, and running this through a View would
    incorrectly bolt tone-mapping onto what should be a pure gamut/encoding
    remap. See lib/CLAUDE.md "why this distinction matters" for the measured
    example that motivated apply_colour_transform() to avoid this call.

    images:       (N, H, W, 3) float32 tensor
    config_path:  path to an OCIO config file
    input_space:  source colour space name as registered in the config
    output_space: destination colour space name (default: ACEScg)

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
    return _apply_processor(images, processor)


def _apply_processor(images: torch.Tensor, processor) -> torch.Tensor:
    # Independent float32 copy so the input tensor is never modified
    arr = images.detach().cpu().float().numpy().copy()
    N, H, W, C = arr.shape
    if C != 3:
        raise ValueError(f"Expected 3-channel IMAGE tensor, got {C} channels")

    cpu = processor.getDefaultCPUProcessor()
    flat = arr.reshape(N * H * W, C)  # view into arr; applyRGB works in-place
    cpu.applyRGB(flat)
    return torch.from_numpy(arr)
