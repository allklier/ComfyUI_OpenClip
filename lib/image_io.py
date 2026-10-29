from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np
import OpenImageIO as oiio
import torch

from . import timecode as _tc

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
    # A part's identity is set by the writer (AOV part names). Flame's single-part EXRs carry
    # name="rgb"; copied onto every part it made multi-part writes fail ("Each part should have
    # a unique name") and renamed Separate Files siblings so they couldn't be found (2.0).
    "name",
    "oiio:subimagename",
    "oiio:subimages",
})

CHANNELS_BEAUTY: tuple[str, ...] = ("R", "G", "B")
CHANNELS_ALPHA: tuple[str, ...] = ("A",)

AOV_LAYOUTS: frozenset[str] = frozenset({"Multichannel", "Separate Files"})

# OIIO 3.x's EXR plugin treats "oiio:ColorSpace" as an enum tied to its internal OCIO
# built-in-config registry, not a free string: recognized names get silently renamed to
# their canonical form (e.g. "ACEScg" -> "lin_ap1_scene") and unrecognized ones (camera
# log profile names, show-specific LUT names) are silently DROPPED on write entirely.
# Verified against the installed OIIO 3.1.15 by round-tripping through actual EXR files.
# So carried-through colour-space identity is also stashed verbatim under this
# project-namespaced key, which OIIO does not special-case. Readers should prefer this
# key and fall back to "oiio:ColorSpace" for files this project didn't write itself.
COLOUR_SPACE_KEY = "oiio:ColorSpace"
COLOUR_SPACE_BACKUP_KEY = "openclip:ColorSpace"
# Flame writes its colour tag into its EXRs under this key (issue #7).
AUTODESK_COLOUR_SPACE_KEY = "autodeskColorSpace"
TIMECODE_KEY = "smpte:TimeCode"


def colour_space_from_metadata(metadata: dict) -> str:
    """Colour tag from EXR header metadata: autodeskColorSpace, then openclip:ColorSpace,
    then oiio:ColorSpace ("" if none). The .clip XML's own tag comes before all of these;
    that lookup is the Reader's."""
    for key in (AUTODESK_COLOUR_SPACE_KEY, COLOUR_SPACE_BACKUP_KEY, COLOUR_SPACE_KEY):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def describe_frame(filepath: str) -> str:
    """Header-only one-liner for reports, e.g. 'EXR half PIZ' or 'PNG 8-bit'."""
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        return ""
    try:
        spec = inp.spec()
        fmt = {"openexr": "EXR", "png": "PNG"}.get(inp.format_name(), inp.format_name().upper())
        depth = {"half": "half", "float": "float", "uint8": "8-bit", "uint16": "16-bit"}.get(str(spec.format), str(spec.format))
        parts = [fmt, depth]
        if fmt == "EXR":
            compression = spec.get_string_attribute("compression", "")
            if compression:
                parts.append(compression.upper())
        return " ".join(parts)
    finally:
        inp.close()

def frame_timecode(filepath: str) -> Optional[int]:
    """Header-only: a frame's smpte:TimeCode time-and-flags word, or None when it has none or
    can't be opened. Verified with OIIO 3.1.15: spec.getattribute("smpte:TimeCode") gives
    (word, user_data), or None when the attribute is absent."""
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        oiio.geterror()  # clear OIIO's pending error; a missing frame just means no timecode
        return None
    try:
        value = inp.spec().getattribute(TIMECODE_KEY)
    finally:
        inp.close()
    return int(value[0]) if value else None

# EXR part-name attribute, tried in this order (see AOV Channel Handling below).
_PART_NAME_ATTRS: tuple[str, ...] = ("name", "oiio:subimagename")


def frame_path(path_pattern: str, frame_num: int) -> str:
    """Resolve a printf-style sequence pattern to a single frame's file path."""
    return path_pattern % frame_num if _FRAME_TOKEN_RE.search(path_pattern) else path_pattern


def read_sequence(
    path_pattern: str,
    start_frame: int,
    end_frame: int,
    load_alpha: bool,
    layer: str = "",
) -> tuple[torch.Tensor, torch.Tensor, dict]:
    """Read a frame sequence's beauty/alpha. Returns (IMAGE [N,H,W,3], MASK [N,H,W], metadata).

    `layer` selects a dot-named channel group in subimage 0 as the beauty (e.g.
    "ViewLayer.Combined" reads ViewLayer.Combined.R/G/B/A, Blender's multilayer EXR
    convention). Blank means the plain R/G/B/A channels.

    Always reads subimage 0 only -- AOVs are a separate, generic, part-name-keyed
    mechanism (probe_aov_names()/read_named_part() below), not something this function
    (or OpenClipReader itself) knows the names of. metadata is extracted from the first
    frame's OIIO extra_attribs.
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
        filepath = frame_path(path_pattern, frame_num)
        image, mask, frame_meta = _read_beauty_frame(filepath, load_alpha, layer)
        if images and image.shape != images[0].shape:
            raise RuntimeError(
                f"Frame size mismatch: {filepath} is {image.shape[1]}x{image.shape[0]} (WxH) but the "
                f"sequence started at {images[0].shape[1]}x{images[0].shape[0]}; all frames must match."
            )
        images.append(image)
        masks.append(mask)
        if i == 0:
            metadata = frame_meta

    return torch.stack(images), torch.stack(masks), metadata


def probe_aov_names(path_pattern: str, frame_num: int) -> list[str]:
    """AOV names present in one representative frame: dot-named channel layers in
    subimage 0, plus named EXR parts beyond subimage 0 (beauty).

    Header-only -- seeks each subimage and reads its `name` attribute, never calls
    read_image(), so this is cheap even when a part (e.g. Cryptomatte) is large. A part
    with no name attribute at all is skipped, since there's nothing to type into
    OpenClipAOVReader's part_name for it.
    """
    filepath = frame_path(path_pattern, frame_num)
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        raise FileNotFoundError(f"OIIO could not open: {filepath}")
    # Dot-named channel groups in subimage 0 first (single-part multilayer EXRs, e.g.
    # Blender/Nuke: "ViewLayer.Normal.X" -> "ViewLayer.Normal"), then named parts.
    names: list[str] = _channel_layers(inp.spec().channelnames)
    try:
        subimage = 1
        while inp.seek_subimage(subimage, 0):
            name = _part_name(inp.spec())
            if name:
                names.append(name)
            subimage += 1
    finally:
        inp.close()
    # "Separate Files" AOVs live in sibling '<stem>_AOV_<name>.####.ext' files next to the
    # beauty; recover each one's real name from its own part `name` attribute.
    for sibling in _aov_sibling_files(path_pattern, frame_num):
        sib = oiio.ImageInput.open(sibling)
        if not sib:
            continue
        try:
            name = _part_name(sib.spec())
        finally:
            sib.close()
        if name and name not in names:
            names.append(name)
    return names


def read_named_part(
    path_pattern: str,
    start_frame: int,
    end_frame: int,
    part_name: str,
) -> tuple[Optional[torch.Tensor], Optional[torch.Tensor]]:
    """Reads one named EXR part across a frame range. Returns (image, matte).

    `image` is [N,H,W,C] (whatever channel count that part actually has) or None if the
    part is single-channel (see below). `matte` is [N,H,W] -- the "R" channel if the part
    has one among >=2 channels, or the sole channel if the part is single-channel -- or
    None if the part was never found in any frame of the range.

    Whole-sequence, not per-frame: found in at least one frame of the range means the AOV
    counts as present, and any individual frame that happens to lack it is zero-filled
    just for that frame (this is the same rule read_sequence_with_aovs used to apply,
    carried over unchanged).
    """
    n_frames = end_frame - start_frame + 1
    if n_frames <= 0:
        raise ValueError(f"start_frame ({start_frame}) must be <= end_frame ({end_frame})")
    if n_frames > MAX_FRAMES:
        raise RuntimeError(f"Frame count {n_frames} exceeds MAX_FRAMES={MAX_FRAMES}")

    image_frames: list[Optional[np.ndarray]] = []
    matte_frames: list[Optional[np.ndarray]] = []
    shapes: list[tuple[int, int]] = []
    found = False
    has_image = False  # any frame had >=2 channels (so `image` is meaningful at all)

    for i in range(n_frames):
        frame_num = start_frame + i
        filepath = frame_path(path_pattern, frame_num)
        pixels, channelnames, hw = _read_named_part_frame(filepath, part_name)
        if pixels is None:
            sibling = frame_path(_aov_sibling_pattern(path_pattern, part_name), frame_num)
            if os.path.exists(sibling):
                pixels, channelnames, _sib_hw = _read_named_part_frame(sibling, part_name)
        shapes.append(hw)
        if pixels is None:
            image_frames.append(None)
            matte_frames.append(None)
            continue
        found = True
        image_arr, matte_arr = _split_image_matte(pixels, channelnames)
        if image_arr is not None:
            has_image = True
        image_frames.append(image_arr)
        matte_frames.append(matte_arr)

    if not found:
        return None, None

    # Zero-fill any individual frame that lacked the part, at that frame's own H,W and
    # whatever channel width the part actually has elsewhere in the range.
    image_width = next((a.shape[-1] for a in image_frames if a is not None), None)
    for i in range(n_frames):
        h, w = shapes[i]
        if has_image and image_frames[i] is None:
            image_frames[i] = np.zeros((h, w, image_width), dtype=np.float32)
        if matte_frames[i] is None:
            matte_frames[i] = np.zeros((h, w), dtype=np.float32)

    image_out = torch.from_numpy(np.stack(image_frames)) if has_image else None
    matte_out = torch.from_numpy(np.stack(matte_frames))
    return image_out, matte_out


def write_sequence(
    path_pattern: str,
    images: torch.Tensor,
    masks: Optional[torch.Tensor],
    start_frame: int,
    file_format: str,
    exr_bit_depth: str = "half (16-bit)",
    exr_compression: str = "ZIP",
    metadata: Optional[dict] = None,
    aov_entries: Optional[list[tuple[str, Optional[torch.Tensor], Optional[torch.Tensor]]]] = None,
    aov_layout: str = "Multichannel",
    colour_space: Optional[str] = None,
    start_timecode: Optional[tuple[int, int, bool]] = None,
) -> None:
    """Write a frame sequence to disk, plus optional named AOV parts.

    `colour_space`, when given, is written as oiio:ColorSpace and openclip:ColorSpace (and
    replaces a carried autodeskColorSpace), overriding the carried metadata's values.
    `start_timecode` = (ticks, rate, drop): every EXR frame gets its own smpte:TimeCode,
    counting up one per frame from it; a carried smpte:TimeCode is dropped. Rates above 30
    get none (see timecode.smpte_bcd).

    `aov_entries`: list of (name, image, matte) -- exactly one of image ([N,H,W,C]) or
    matte ([N,H,W]) should be set per entry (the caller node validates this; here, image
    wins if somehow both are given). Written as real multi-part EXR subimages, each
    carrying its own `name` attribute -- matching how a real multi-part EXR (e.g. a
    Flame render with AOVs imported from another DCC) actually looks, not this project's
    old single-part-multichannel convention.

    `aov_layout`: "Multichannel" appends each AOV as its own named subimage in the same
    file as beauty. "Separate Files" writes each AOV to its own sibling
    '<stem>_AOV_<name>.####.ext' file instead, one subimage, same `name` attribute set.
    AOVs require file_format="EXR".
    """
    n_frames = images.shape[0]
    if n_frames > MAX_FRAMES:
        raise RuntimeError(f"Frame count {n_frames} exceeds MAX_FRAMES={MAX_FRAMES}")
    if file_format == "EXR" and exr_bit_depth not in _OIIO_BIT_DEPTHS:
        raise ValueError(f"Unknown exr_bit_depth '{exr_bit_depth}'. Valid: {list(_OIIO_BIT_DEPTHS)}")
    if file_format == "EXR" and exr_compression not in _OIIO_COMPRESSIONS:
        raise ValueError(f"Unknown exr_compression '{exr_compression}'. Valid: {list(_OIIO_COMPRESSIONS)}")

    aov_entries = aov_entries or []
    if aov_entries:
        if file_format != "EXR":
            raise ValueError(f"AOV output requires file_format='EXR', got '{file_format}'")
        if aov_layout not in AOV_LAYOUTS:
            raise ValueError(f"Unknown aov_layout '{aov_layout}'. Valid: {sorted(AOV_LAYOUTS)}")
        names_seen = set()
        for name, _image, _matte in aov_entries:
            if name in names_seen:
                raise ValueError(f"duplicate AOV name '{name}' -- each entry in the chain must be unique")
            names_seen.add(name)

    if n_frames > 1 and not _FRAME_TOKEN_RE.search(path_pattern):
        raise ValueError(f"path_pattern has no frame token but {n_frames} frames were given: {path_pattern}")

    if colour_space is not None or start_timecode is not None:
        metadata = dict(metadata or {})
        if colour_space is not None:
            metadata[COLOUR_SPACE_KEY] = colour_space
            if AUTODESK_COLOUR_SPACE_KEY in metadata:
                metadata[AUTODESK_COLOUR_SPACE_KEY] = colour_space
        if start_timecode is not None:
            # The carried value is the source's first frame; ours is written per frame.
            metadata.pop(TIMECODE_KEY, None)

    for i in range(n_frames):
        frame_num = start_frame + i
        filepath = frame_path(path_pattern, frame_num)
        mask = masks[i] if masks is not None else None
        typed = _timecode_attrs(start_timecode, i)
        frame_aovs = [(name, _select_frame(image, i), _select_frame(matte, i)) for name, image, matte in aov_entries]
        inline_aovs = frame_aovs if aov_layout == "Multichannel" else None
        _write_frame(filepath, images[i], mask, file_format, exr_bit_depth, exr_compression, metadata,
                     aov_parts=inline_aovs, typed_attrs=typed)

        if aov_entries and aov_layout == "Separate Files":
            for name, image, matte in frame_aovs:
                aov_filepath = frame_path(_aov_sibling_pattern(path_pattern, name), frame_num)
                pixels, names = _aov_pixels_and_names(image, matte)
                _write_exr(aov_filepath, pixels, names, exr_bit_depth, exr_compression, metadata,
                           part_name=name, typed_attrs=typed)


def _timecode_attrs(start_timecode: Optional[tuple[int, int, bool]], offset: int) -> list:
    """[(key, TypeDesc, value)] for frame `offset` of a sequence starting at start_timecode."""
    if start_timecode is None:
        return []
    ticks, rate, drop = start_timecode
    bcd = _tc.smpte_bcd(ticks + offset, rate, drop)
    return [] if bcd is None else [(TIMECODE_KEY, oiio.TypeTimeCode, bcd)]


def _apply_typed_attrs(spec, typed_attrs: Optional[list]) -> None:
    for key, typedesc, value in typed_attrs or ():
        spec.attribute(key, typedesc, value)


# --- private helpers ---


def _part_name(spec) -> str:
    for attr in _PART_NAME_ATTRS:
        value = spec.get_string_attribute(attr, "")
        if value:
            return value
    return ""


def _select_frame(tensor: Optional[torch.Tensor], i: int) -> Optional[torch.Tensor]:
    return None if tensor is None else tensor[i]


def _aov_pixels_and_names(image: Optional[torch.Tensor], matte: Optional[torch.Tensor]) -> tuple[np.ndarray, tuple[str, ...]]:
    """(pixels, channel names) for one AOV entry's one frame -- image wins if both given."""
    if image is not None:
        return image.numpy(), tuple(CHANNELS_BEAUTY[: image.shape[-1]]) if image.shape[-1] <= 3 else tuple(
            f"ch{i}" for i in range(image.shape[-1])
        )
    if matte is not None:
        return _as_hwc(matte.numpy()), ("R",)
    raise ValueError("AOV entry has neither image nor matte")


def _as_hwc(arr: np.ndarray) -> np.ndarray:
    """Promote a (H,W) single-channel array to (H,W,1)."""
    return arr[:, :, None] if arr.ndim == 2 else arr


def _aov_sibling_files(path_pattern: str, frame_num: int) -> list[str]:
    """Existing sibling AOV files ('<stem>_AOV_<anything>.####.ext') for one frame."""
    probe = frame_path(_aov_sibling_pattern(path_pattern, "ZZZ"), frame_num)
    return sorted(glob.glob(glob.escape(probe).replace("_AOV_ZZZ", "_AOV_*")))


def _aov_sibling_pattern(path_pattern: str, name: str) -> str:
    """Insert an AOV name into a beauty path pattern's stem, as a filesystem-safe suffix.

    'shot.%04d.exr' -> 'shot_AOV_depth.%04d.exr' (frame-token form)
    'shot.exr'      -> 'shot_AOV_depth.exr'      (static single-image form, see
                        openclip_xml.py's Flame encoding="file" case)
    """
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    suffix = f"_AOV_{safe_name}"
    m = _FRAME_TOKEN_RE.search(path_pattern)
    if m:
        head = path_pattern[:m.start()]
        sep = "."
        if head.endswith("."):
            head = head[:-1]
        else:
            sep = ""
        return f"{head}{suffix}{sep}{path_pattern[m.start():]}"
    p = Path(path_pattern)
    return str(p.with_name(f"{p.stem}{suffix}{p.suffix}"))


def _extract_named_channels(pixels: np.ndarray, channelnames, names: tuple[str, ...]) -> Optional[np.ndarray]:
    """Pull named channels from an already-decoded (H,W,C) array, in `names` order.

    Returns None if any requested name is absent -- a channel group is all-or-nothing,
    never partially filled.
    """
    try:
        indices = [channelnames.index(n) for n in names]
    except ValueError:
        return None
    return pixels[:, :, indices]


def _split_image_matte(pixels: np.ndarray, channelnames: tuple[str, ...]) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """(image, matte) for one decoded part: 1 channel -> matte only, image is None
    (nothing meaningfully 3-channel to hand back); >=2 channels -> image is all of
    them, matte is the "R" channel if present, else channel 0 -- looked up by name,
    never assumed to be first, per this project's channel-by-name discipline.
    """
    if pixels.shape[-1] == 1:
        return None, pixels[:, :, 0]
    if "R" in channelnames:
        matte = pixels[:, :, channelnames.index("R")]
    else:
        matte = pixels[:, :, 0]
    return pixels, matte


def _extract_frame_metadata(spec) -> dict:
    result = {}
    for attr in spec.extra_attribs:
        try:
            result[attr.name] = attr.value
        except Exception:
            pass
    return result


def _read_beauty_frame(filepath: str, load_alpha: bool, layer: str = "") -> tuple[torch.Tensor, torch.Tensor, dict]:
    """Reads subimage 0 (beauty + alpha) only. AOVs are read separately, by name,
    via read_named_part() -- this function no longer knows any AOV names at all.
    `layer` prefixes the channel names (see read_sequence).
    """
    prefix = f"{layer}." if layer else ""
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        raise FileNotFoundError(f"OIIO could not open: {filepath}")
    try:
        spec0 = inp.spec()
        pixels0 = inp.read_image(oiio.FLOAT)
        if pixels0 is None:
            raise RuntimeError(f"{filepath}: OIIO read_image returned None")
        pixels0 = np.asarray(pixels0, dtype=np.float32)
        metadata = _extract_frame_metadata(spec0)
        h, w = pixels0.shape[:2]
        channelnames0 = spec0.channelnames

        beauty = _extract_named_channels(pixels0, channelnames0, tuple(prefix + c for c in CHANNELS_BEAUTY))
        if beauty is None:
            layers = _channel_layers(channelnames0)
            hint = f"; layers in this file: {layers}" if layers else ""
            raise RuntimeError(f"{filepath}: missing required {prefix}R/G/B channel(s){hint}; found channels {channelnames0}")
        image = torch.from_numpy(beauty)

        if load_alpha:
            alpha = _extract_named_channels(pixels0, channelnames0, tuple(prefix + c for c in CHANNELS_ALPHA))
            # No alpha channel means no matte, not "nothing here" -- standard VFX
            # convention (Nuke, Flame, After Effects) treats an absent alpha as
            # fully opaque, so a plate with no matte still counts everywhere.
            mask = torch.from_numpy(alpha[:, :, 0]) if alpha is not None else torch.ones(h, w, dtype=torch.float32)
        else:
            mask = torch.ones(h, w, dtype=torch.float32)
    finally:
        inp.close()

    return image, mask, metadata


def _read_named_part_frame(filepath: str, part_name: str) -> tuple[Optional[np.ndarray], tuple[str, ...], tuple[int, int]]:
    """Finds `part_name` among this frame's subimages (0 included) and decodes it.

    Returns (pixels, channelnames, (h, w)) -- pixels is None if not found in this frame,
    but (h, w) is always the beauty frame's size (subimage 0), for zero-fill sizing even
    on a miss.
    """
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        raise FileNotFoundError(f"OIIO could not open: {filepath}")
    try:
        # ImageInput.open() positions at subimage 0 already; only subimages 1+ need
        # an explicit seek_subimage().
        spec = inp.spec()
        h, w = spec.height, spec.width
        subimage = 0
        while spec is not None:
            name = _part_name(spec)
            if name == part_name:
                pixels = inp.read_image(oiio.FLOAT)
                if pixels is None:
                    return None, (), (h, w)
                return np.asarray(pixels, dtype=np.float32), tuple(spec.channelnames), (h, w)
            subimage += 1
            spec = inp.spec() if inp.seek_subimage(subimage, 0) else None
    finally:
        inp.close()
    # No part carries that name: try it as a dot-named channel layer in subimage 0
    # (single-part multilayer EXR). Reopened rather than seeking back, so subimage 0 is
    # unambiguously what gets decoded.
    return _read_channel_layer_frame(filepath, part_name, (h, w))


def _read_channel_layer_frame(filepath: str, layer: str, hw: tuple[int, int]) -> tuple[Optional[np.ndarray], tuple[str, ...], tuple[int, int]]:
    """Channels named '<layer>.<c>' in subimage 0, returned with the prefix stripped
    (so 'ViewLayer.Normal.X' comes back as 'X'), in the file's channel order."""
    inp = oiio.ImageInput.open(str(filepath))
    if not inp:
        raise FileNotFoundError(f"OIIO could not open: {filepath}")
    try:
        channelnames = tuple(inp.spec().channelnames)
        prefix = layer + "."
        indices = [i for i, n in enumerate(channelnames) if n.startswith(prefix) and "." not in n[len(prefix):]]
        if not indices:
            return None, (), hw
        pixels = inp.read_image(oiio.FLOAT)
        if pixels is None:
            return None, (), hw
        pixels = np.asarray(pixels, dtype=np.float32)
        return pixels[:, :, indices], tuple(channelnames[i][len(prefix):] for i in indices), hw
    finally:
        inp.close()


def _channel_layers(channelnames) -> list[str]:
    """Distinct dot-named layer prefixes, in channel order ('a.b.X' -> 'a.b')."""
    layers: list[str] = []
    for n in channelnames:
        if "." in n:
            layer = n.rsplit(".", 1)[0]
            if layer not in layers:
                layers.append(layer)
    return layers


def _write_exr(
    filepath: str,
    pixels: np.ndarray,
    names: tuple[str, ...],
    exr_bit_depth: str,
    exr_compression: str,
    metadata: Optional[dict],
    part_name: Optional[str] = None,
    typed_attrs: Optional[list] = None,
) -> None:
    height, width, nchannels = pixels.shape
    dtype = _OIIO_BIT_DEPTHS[exr_bit_depth]
    spec = oiio.ImageSpec(width, height, nchannels, dtype)
    spec.channelnames = tuple(names)
    if part_name:
        spec.attribute("name", part_name)
    if metadata:
        for key, value in metadata.items():
            if key not in _SKIP_METADATA_KEYS:
                try:
                    spec[key] = value
                except Exception:
                    pass
        if COLOUR_SPACE_KEY in metadata:
            # Verbatim backup — see COLOUR_SPACE_BACKUP_KEY's module-level comment.
            try:
                spec.attribute(COLOUR_SPACE_BACKUP_KEY, oiio.TypeString, str(metadata[COLOUR_SPACE_KEY]))
            except Exception:
                pass
    _apply_typed_attrs(spec, typed_attrs)
    # Writer format settings are applied last so they always override carried metadata.
    spec["compression"] = _OIIO_COMPRESSIONS[exr_compression]
    pixels_write = np.ascontiguousarray(pixels, dtype=np.float32)
    _write_image_file(filepath, spec, pixels_write)


def _write_image_file(filepath: str, spec, pixels_write: np.ndarray) -> None:
    out = oiio.ImageOutput.create(filepath)
    if not out:
        raise RuntimeError(f"OIIO could not create output for: {filepath}")
    if not out.open(filepath, spec):
        raise RuntimeError(f"OIIO failed to open for writing: {filepath}")
    if not out.write_image(pixels_write):
        out.close()
        err = out.geterror()
        raise RuntimeError(f"OIIO write_image failed for: {filepath} - {err}")
    out.close()


def _write_multipart_exr(
    filepath: str,
    parts: list[tuple[np.ndarray, tuple[str, ...], Optional[str]]],
    exr_bit_depth: str,
    exr_compression: str,
    metadata: Optional[dict],
    typed_attrs: Optional[list] = None,
) -> None:
    """Writes a true multi-part EXR: `parts` is [(pixels, channelnames, part_name), ...],
    part_name None for the first (beauty) part. Verified against the installed OIIO
    3.1.15: open(filename, [spec, ...]) then write_image() for subimage 0, followed by
    open(filename, spec_i, "AppendSubimage") + write_image() per subsequent subimage.
    """
    dtype = _OIIO_BIT_DEPTHS[exr_bit_depth]
    specs = []
    for pixels, names, part_name in parts:
        height, width, nchannels = pixels.shape
        spec = oiio.ImageSpec(width, height, nchannels, dtype)
        spec.channelnames = tuple(names)
        if part_name:
            spec.attribute("name", part_name)
        if metadata:
            for key, value in metadata.items():
                if key not in _SKIP_METADATA_KEYS:
                    try:
                        spec[key] = value
                    except Exception:
                        pass
            if COLOUR_SPACE_KEY in metadata:
                try:
                    spec.attribute(COLOUR_SPACE_BACKUP_KEY, oiio.TypeString, str(metadata[COLOUR_SPACE_KEY]))
                except Exception:
                    pass
        _apply_typed_attrs(spec, typed_attrs)
        spec["compression"] = _OIIO_COMPRESSIONS[exr_compression]
        specs.append(spec)

    out = oiio.ImageOutput.create(filepath)
    if not out:
        raise RuntimeError(f"OIIO could not create output for: {filepath}")
    if not out.open(filepath, specs):
        raise RuntimeError(f"OIIO failed to open for multi-part writing: {filepath}: {out.geterror()}")

    for i, (pixels, _names, _part_name) in enumerate(parts):
        if i > 0:
            if not out.open(filepath, specs[i], "AppendSubimage"):
                out.close()
                raise RuntimeError(f"OIIO failed to append subimage {i} to: {filepath}: {out.geterror()}")
        pixels_write = np.ascontiguousarray(pixels, dtype=np.float32)
        if not out.write_image(pixels_write):
            err = out.geterror()
            out.close()
            raise RuntimeError(f"OIIO write_image failed for subimage {i} of: {filepath} - {err}")
    out.close()


def _write_frame(
    filepath: str,
    image: torch.Tensor,
    mask: Optional[torch.Tensor],
    file_format: str,
    exr_bit_depth: str,
    exr_compression: str,
    metadata: Optional[dict] = None,
    aov_parts: Optional[list[tuple[str, Optional[torch.Tensor], Optional[torch.Tensor]]]] = None,
    typed_attrs: Optional[list] = None,
) -> None:
    beauty_channels = [image.numpy()]
    beauty_names = list(CHANNELS_BEAUTY)
    if mask is not None:
        beauty_channels.append(mask.unsqueeze(-1).numpy())
        beauty_names.append(CHANNELS_ALPHA[0])
    beauty_pixels = np.concatenate(beauty_channels, axis=-1)

    if file_format != "EXR":
        height, width = beauty_pixels.shape[:2]
        spec = oiio.ImageSpec(width, height, beauty_pixels.shape[-1], oiio.UINT8)
        pixels_write = np.ascontiguousarray((np.clip(beauty_pixels, 0.0, 1.0) * 255).astype(np.uint8))
        _write_image_file(filepath, spec, pixels_write)
        return

    if not aov_parts:
        _write_exr(filepath, beauty_pixels, tuple(beauty_names), exr_bit_depth, exr_compression, metadata,
                   typed_attrs=typed_attrs)
        return

    parts: list[tuple[np.ndarray, tuple[str, ...], Optional[str]]] = [
        (beauty_pixels, tuple(beauty_names), None)
    ]
    for name, aov_image, aov_matte in aov_parts:
        pixels, names = _aov_pixels_and_names(aov_image, aov_matte)
        parts.append((pixels, names, name))
    _write_multipart_exr(filepath, parts, exr_bit_depth, exr_compression, metadata, typed_attrs=typed_attrs)
