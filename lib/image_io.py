from __future__ import annotations

import os
import re
from typing import Optional

import numpy as np
import OpenImageIO as oiio
import torch

MAX_FRAMES = 100_000

# Matches a printf-style frame specifier such as %04d or %d.
_FRAME_TOKEN_RE = re.compile(r'%\d*d')

_OIIO_BIT_DEPTHS: dict[str, oiio.TypeDesc] = {
    "half (16-bit)": oiio.HALF,
    "float (32-bit)": oiio.FLOAT,
}

_OIIO_COMPRESSIONS: dict[str, str] = {
    "ZIP": "zip",
    "PIZ": "piz",
    "DWAB": "dwab",
}

# Attributes set explicitly by writer format options; never overwritten by carried metadata.
_SKIP_METADATA_KEYS: frozenset[str] = frozenset({
    "compression",
    "openexr:chunkCount",
    "openexr:maxSamplesPerPixel",
    "openexr:roundingMode",
    "openexr:dwaCompressionLevel",
    "openexr:multiView",
})


def read_sequence(
    path_pattern: str,
    start_frame: int,
    end_frame: int,
    load_alpha: bool,
) -> tuple[torch.Tensor, torch.Tensor, dict]:
    """Read a frame sequence. Returns (IMAGE [N,H,W,3], MASK [N,H,W], metadata).

    metadata is extracted from the first frame's OIIO extra_attribs.
    """
    n_frames = end_frame - start_frame + 1
    if n_frames <= 0:
        raise ValueError(f"start_frame ({start_frame}) must be <= end_frame ({end_frame})")
    if n_frames > MAX_FRAMES:
        raise RuntimeError(f"Frame count {n_frames} exceeds MAX_FRAMES={MAX_FRAMES}")

    images: list[torch.Tensor] = []
    masks: list[torch.Tensor] = []
    metadata: dict = {}

    for i in range(n_frames):
        frame_num = start_frame + i
        filepath = path_pattern % frame_num if _FRAME_TOKEN_RE.search(path_pattern) else path_pattern
        image, mask, frame_meta = _read_frame(filepath, load_alpha)
        images.append(image)
        masks.append(mask)
        if i == 0:
            metadata = frame_meta

    return torch.stack(images), torch.stack(masks), metadata


def write_sequence(
    path_pattern: str,
    images: torch.Tensor,
    masks: Optional[torch.Tensor],
    start_frame: int,
    file_format: str,
    exr_bit_depth: str = "half (16-bit)",
    exr_compression: str = "ZIP",
    metadata: Optional[dict] = None,
) -> None:
    """Write a frame sequence to disk.

    When metadata is provided it is written into each EXR header, with writer
    format settings (compression, etc.) applied last so they always win.
    """
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
        _write_frame(filepath, images[i], mask, file_format, exr_bit_depth, exr_compression, metadata)


# --- private helpers ---


def _extract_frame_metadata(spec) -> dict:
    result = {}
    for attr in spec.extra_attribs:
        try:
            result[attr.name] = attr.value
        except Exception:
            pass
    return result


def _read_frame(filepath: str, load_alpha: bool) -> tuple[torch.Tensor, torch.Tensor, dict]:
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        raise FileNotFoundError(f"OIIO could not open: {filepath}")

    spec = inp.spec()
    pixels = inp.read_image(oiio.FLOAT)
    metadata = _extract_frame_metadata(spec)
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

    return image, mask, metadata


def _write_frame(
    filepath: str,
    image: torch.Tensor,
    mask: Optional[torch.Tensor],
    file_format: str,
    exr_bit_depth: str,
    exr_compression: str,
    metadata: Optional[dict] = None,
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
        if metadata:
            for key, value in metadata.items():
                if key not in _SKIP_METADATA_KEYS:
                    try:
                        spec[key] = value
                    except Exception:
                        pass
        # Writer format settings are applied last so they always override carried metadata.
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
