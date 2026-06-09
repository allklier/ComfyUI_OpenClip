from __future__ import annotations

from typing import Optional

import numpy as np
import OpenImageIO as oiio
import torch

MAX_FRAMES = 100_000

_OIIO_BIT_DEPTHS: dict[str, oiio.TypeDesc] = {
    "half (16-bit)": oiio.HALF,
    "float (32-bit)": oiio.FLOAT,
}

_OIIO_COMPRESSIONS: dict[str, str] = {
    "ZIP": "zip",
    "PIZ": "piz",
    "DWAB": "dwab",
}


def read_sequence(
    path_pattern: str,
    start_frame: int,
    end_frame: int,
    load_alpha: bool,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Read a frame sequence. Returns (IMAGE [N,H,W,3], MASK [N,H,W])."""
    n_frames = end_frame - start_frame + 1
    if n_frames <= 0:
        raise ValueError(f"start_frame ({start_frame}) must be <= end_frame ({end_frame})")
    if n_frames > MAX_FRAMES:
        raise RuntimeError(f"Frame count {n_frames} exceeds MAX_FRAMES={MAX_FRAMES}")

    images: list[torch.Tensor] = []
    masks: list[torch.Tensor] = []

    for i in range(n_frames):
        frame_num = start_frame + i
        filepath = path_pattern % frame_num
        image, mask = _read_frame(filepath, load_alpha)
        images.append(image)
        masks.append(mask)

    return torch.stack(images), torch.stack(masks)


def write_sequence(
    path_pattern: str,
    images: torch.Tensor,
    masks: Optional[torch.Tensor],
    start_frame: int,
    file_format: str,
    exr_bit_depth: str = "half (16-bit)",
    exr_compression: str = "ZIP",
) -> None:
    """Write a frame sequence to disk."""
    n_frames = images.shape[0]
    if n_frames > MAX_FRAMES:
        raise RuntimeError(f"Frame count {n_frames} exceeds MAX_FRAMES={MAX_FRAMES}")
    if file_format == "EXR" and exr_bit_depth not in _OIIO_BIT_DEPTHS:
        raise ValueError(f"Unknown exr_bit_depth '{exr_bit_depth}'. Valid: {list(_OIIO_BIT_DEPTHS)}")
    if file_format == "EXR" and exr_compression not in _OIIO_COMPRESSIONS:
        raise ValueError(f"Unknown exr_compression '{exr_compression}'. Valid: {list(_OIIO_COMPRESSIONS)}")

    for i in range(n_frames):
        frame_num = start_frame + i
        filepath = path_pattern % frame_num
        mask = masks[i] if masks is not None else None
        _write_frame(filepath, images[i], mask, file_format, exr_bit_depth, exr_compression)


# --- private helpers ---


def _read_frame(filepath: str, load_alpha: bool) -> tuple[torch.Tensor, torch.Tensor]:
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        raise FileNotFoundError(f"OIIO could not open: {filepath}")

    spec = inp.spec()
    pixels = inp.read_image(oiio.FLOAT)
    inp.close()

    if pixels is None:
        raise RuntimeError(f"OIIO read_image returned None for: {filepath}")

    pixels = np.asarray(pixels, dtype=np.float32)
    h, w = pixels.shape[:2]

    if load_alpha and spec.nchannels >= 4:
        image = torch.from_numpy(pixels[:, :, :3])
        mask = torch.from_numpy(pixels[:, :, 3])
    else:
        image = torch.from_numpy(pixels[:, :, :3])
        mask = torch.zeros(h, w, dtype=torch.float32)

    return image, mask


def _write_frame(
    filepath: str,
    image: torch.Tensor,
    mask: Optional[torch.Tensor],
    file_format: str,
    exr_bit_depth: str,
    exr_compression: str,
) -> None:
    if mask is not None:
        pixels = torch.cat([image, mask.unsqueeze(-1)], dim=-1).numpy()
        nchannels = 4
    else:
        pixels = image.numpy()
        nchannels = 3

    height, width = pixels.shape[:2]

    if file_format == "EXR":
        dtype = _OIIO_BIT_DEPTHS[exr_bit_depth]
        spec = oiio.ImageSpec(width, height, nchannels, dtype)
        spec["compression"] = _OIIO_COMPRESSIONS[exr_compression]
        pixels_write = pixels.astype(np.float32)
    else:
        spec = oiio.ImageSpec(width, height, nchannels, oiio.UINT8)
        pixels_write = (np.clip(pixels, 0.0, 1.0) * 255).astype(np.uint8)

    out = oiio.ImageOutput.create(filepath)
    if not out:
        raise RuntimeError(f"OIIO could not create output for: {filepath}")
    if not out.open(filepath, spec):
        raise RuntimeError(f"OIIO failed to open for writing: {filepath}")
    if not out.write_image(pixels_write):
        out.close()
        raise RuntimeError(f"OIIO write_image failed for: {filepath}")

    out.close()
