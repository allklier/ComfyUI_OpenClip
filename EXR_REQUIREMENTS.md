# Requirements for OpenClip EXR I/O — for the Harmonize project

> **Two byte-identical copies of this file exist:** `ComfyUI_Harmonize/docs/openclip-exr-requirements.md` (canonical) and `ComfyUI_OpenClip/EXR_REQUIREMENTS.md`. Edit the canonical one, then `cp` it over — verbatim, no rewording for the destination. The text below deliberately names both repos explicitly and avoids relative links so that a straight copy reads correctly from either location; keep it that way, and `diff` will always prove whether they are in sync.
>
> It specifies what ComfyUI_OpenClip's Read/Write Open Clip nodes must support in order for ComfyUI_Harmonize to consume them. It does not describe anything to build in ComfyUI_Harmonize.

## Context

Harmonize runs as a round trip out of Autodesk Flame: a Flame comp is written to an Open Clip, read into ComfyUI, processed by the harmonize node graph, and written back out to an Open Clip that Flame reads back in. Harmonize does not implement its own image I/O — it depends entirely on ComfyUI_OpenClip's `OpenClipReader` / `OpenClipWriter` nodes.

## Requirements

1. **Lossless round trip at fp16/fp32.** No implicit clamping to 0..1 anywhere in the read or write path — several existing third-party ComfyUI image nodes clamp on the (often correct) assumption that `IMAGE` means display-referred 0..1 sRGB; `OpenClipReader`/`OpenClipWriter` must be an explicit exception to that assumption.
2. **ACEScg and arbitrary OCIO color spaces**, including camera log profiles. Read must preserve (or expose) the source's color-space identity; write must round-trip that identity without baking in an unrequested transform.
3. **Color-space/transfer-function metadata must be queryable downstream** — e.g. as a string output alongside the image — so consuming nodes (like Harmonize's `SHFitLighting`) can verify their linear scene-referred assumption at runtime rather than trusting undocumented convention.
4. **Sequence/batch support** matching Flame's Open Clip frame-range conventions, mapped onto ComfyUI's `IMAGE` batch dimension in frame order.
5. **AOV pass-through**, when present in the source EXR (depth, normals, etc.) — expose them as separate outputs/channels rather than requiring a second load.
6. **No new EXR/Open Clip codec work should be duplicated in ComfyUI_Harmonize** — ComfyUI_OpenClip is the single source of truth for that capability across both projects.
7. **Performance**: needs to sustain fp32 4K sequences within a 48GB VRAM budget when combined with the Harmonize node graph in the same ComfyUI process (see the hardware target in `ComfyUI_Harmonize/CLAUDE.md`) — flag if a buffering strategy needs coordination between the two node packs to hit that.

## AOV channel convention

Reference fixtures matching this convention are produced by `ComfyUI_Harmonize/tests/generate_synthetic_exr.py` — build the reader against those.

Single-part EXR, dotted `<layer>.<channel>` naming (matching the `CarInterior_AOV_Cryptomatte.exr` sample on akflame5):

| Layer | Channels | Meaning |
|---|---|---|
| beauty | `R`, `G`, `B`, `A` | linear scene-referred plate; `A` is coverage |
| `N` | `N.X`, `N.Y`, `N.Z` | camera-space normals: +X right, +Y up, +Z toward viewer |
| `Nw` | `Nw.X`, `Nw.Y`, `Nw.Z` | world-space normals |
| `Z` | `Z` | depth along the view axis; a far value where nothing is hit |

Both a single multi-channel file and a per-AOV file set (`<name>_AOV_Normals.####.exr`, `<name>_AOV_Depth.####.exr`) are generated, since real material on the deployment box uses the latter while requirement #5 above implies the former. The reader should handle both.

**Whether Flame writes single-part dotted or true multi-part EXR is still unconfirmed** — pending a check in Flame 2027.1. If it is multi-part, this section grows a second layout; it does not change the channel names.

### Channels MUST be addressed by name, never by index

OpenEXR does not preserve channel write order, and OIIO reorders on read. Verified with OIIO 3.1.15:

```
written:   R, G, B, A, N.X, N.Y, N.Z, Z
read back: R, G, B, A, Z, N.X, N.Y, N.Z     (spec.z_channel == 4)
```

Pixel data is permuted to match the names, so the file is correct — but a reader that assumes "normals are at indices 4–6 because that is where I wrote them" gets **depth** in the X slot and two-thirds of the normal in the rest. This fails silently and produces a plausible-looking wrong lighting fit. `ComfyUI_OpenClip/lib/image_io.py` currently reads flat RGB/RGBA by index and has no channel-name awareness, so this is new work, not a tweak.

## Out of scope for this doc

Anything about the harmonize node graph itself, spherical-harmonic math, or shadow projection — see `ComfyUI_Harmonize/docs/`.
