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
5. **AOV pass-through**, when present in the source EXR (depth, normals, etc.) — expose them without requiring a second *load of the beauty plate*. As of the 2026-09-23 redesign this is satisfied generically rather than per-AOV: the Reader hands back a lazy `AOV_HANDLE` (locates the file, decodes nothing) plus a probe of the AOV names actually present, and a separate `OpenClipAOVReader` node decodes one named part per instance, on demand — a plain beauty clip pays nothing extra, and an unrequested AOV (e.g. Cryptomatte) is never pixel-decoded. See "AOV channel convention" below.
6. **No new EXR/Open Clip codec work should be duplicated in ComfyUI_Harmonize** — ComfyUI_OpenClip is the single source of truth for that capability across both projects.
7. **Performance**: needs to sustain fp32 4K sequences within a 48GB VRAM budget when combined with the Harmonize node graph in the same ComfyUI process (see the hardware target in `ComfyUI_Harmonize/CLAUDE.md`) — flag if a buffering strategy needs coordination between the two node packs to hit that.
8. **A frame-count cap independent of the clip's native frame numbering.** `start_frame`/`end_frame` are currently absolute frame numbers matching each clip's own numbering convention — Flame typically starts sequences at 1001, but not always: confirmed on real akflame5 material where one clip in a shot's own paired plates started at frame 1 and the other at frame 1001. This makes "give me the first N frames" something the caller has to compute per clip today (resolve the native start, then add N) — brittle, and a wrong result here is a silently-valid read of the wrong range, not an error. Needed: a separate `frame_cap` (or similarly named) parameter, a frame *count* rather than an absolute frame number, applied on top of whatever start actually resolves — so `start_frame=-1, end_frame=-1` (native full range) plus `frame_cap=105` reads a clip's own first 105 frames regardless of whether its native numbering starts at 0, 1, or 1001. Found needed 2026-09-23 pairing a 105-frame real FG plate (`restaurant.clip`, native start frame 1) against a 121-frame generated BG plate (`restaurant_bg.clip`, native start frame 1001) for a Harmonize T0 test case.

9. **Plain EXR sequences without an Open Clip wrapper** (added 2026-09-28). Blender (and Nuke) renders arrive as bare `name.####.exr` sequences with no `.clip`. A separate reader node must read them with the same outputs as `OpenClipReader`, so it can stand in for it anywhere, including feeding `OpenClipAOVReader`. Built as `OpenClipExrSequenceReader`: the frame range comes from disk; fps and colour space come from widgets, since Blender writes no frame rate into the EXR and a blank colour space falls back to the header.

## AOV channel convention

**Two conventions are supported: named parts, and dot-named channel layers in a single part (the second added 2026-09-28).** A lookup by name tries named parts first, then a dot-named layer in subimage 0 (next section).

**Single-part, dot-named layers: confirmed against a real Blender 5.2 render (2026-09-28).** Blender's "OpenEXR MultiLayer" output writes one part whose channels are `<view layer>.<pass>.<channel>` (e.g. `ViewLayer.Combined.R`, `ViewLayer.Normal.X`, `ViewLayer.Depth.Z`, `CamelMatte.Combined.A`), with no plain `R/G/B` at all. Each distinct prefix (`ViewLayer.Normal`) counts as an AOV name: `probe_aov_names` lists it, and `read_named_part` returns its channels with the prefix stripped (`X,Y,Z`), so the `(image, matte)` rules below apply unchanged. The beauty is picked by prefix too (the reader's `layer` widget). Blender tags an ACEScg render `lin_ap1_scene`, which resolves to `ACEScg` in the production OCIO config.


**True multi-part EXR, part-name-keyed — confirmed against real material (the only
convention supported from 2026-09-23 until Blender's was added).** A real Flame render (beauty + depth + normals
+ Cryptomatte, AOVs imported from another DCC, Cinema 4D, verified with `oiiotool
--info -v -a`) showed every AOV lands in its own EXR subimage/part, and every part uses
plain generic `R`,`G`,`B` channel names — there is no dotted `N.X`/`Z` layer-channel
naming anywhere in *that* file. The AOV's identity is carried entirely by the EXR
part's own `name` attribute (`"depth"`, `"normals"`, `"cryptomatte"`, ...). An earlier
version of this doc specified a dotted `<layer>.<channel>` single-part convention
instead (matching `ComfyUI_Harmonize/tests/generate_synthetic_exr.py`, which produced
it) — at the time that was this project's own synthetic-fixture invention, not yet seen in a real
render (Blender's real multilayer output, above, later turned out to use it), and was dropped (not kept alongside the real convention) once AOVs
were confirmed to have had no production usage worth preserving compatibility for.

`ComfyUI_OpenClip/lib/image_io.py` exposes this generically, not as three hardcoded
AOV names:

- **`probe_aov_names(path_pattern, frame_num)`** — header-only listing of every dot-named
  layer in subimage 0, then every named part beyond subimage 0 (beauty), for one representative frame. `OpenClipReader`'s
  `available_aovs` output and in-node display are built on this.
- **`read_named_part(path_pattern, start_frame, end_frame, part_name)`** — decodes one
  named part across a frame range, lazily: nothing is pixel-decoded until called, and
  only for the requested name, so an unrequested part (Cryptomatte, in particular —
  its channels are *also* just `R,G,B`, indistinguishable from a real AOV by channel
  name alone) never pays the potentially-large decode cost. Returns `(image, matte)`:
  a single-channel part populates `matte` only; a >=1-channel part populates both,
  `matte` being the channel named `"R"` if present, else channel 0. Depth's data was
  found to be a replicated scalar across `R,G,B` (3 channels on disk, `image` and
  `matte` both populated, `matte` just equals every channel).

At the node level, `OpenClipReader.aov_handle` (a lazy locator, no pixel data) feeds
one `OpenClipAOVReader` instance per AOV actually wanted, each with its own `part_name`
`STRING` widget typed by hand off the probe list (no dropdown is possible — ComfyUI
gives a node no way to write another node's widget value). **This repo does not try to
determine whether an ambiguous normals-like part is camera-space or world-space** —
there is no way to know from the file alone when the data didn't originate in Flame —
so that labelling is left entirely to the consuming project (Harmonize) to declare via
its own node/toggle, not something `OpenClipAOVReader` infers or exposes as a
separately-named output.

Write side is symmetric: `OpenClipAOVWriter` daisy-chains named `(image, matte)`
entries into an `AOV_WRITE_CHAIN`, which `OpenClipWriter.aov_chain` accepts and writes
as real named subimages (`aov_layout="Multichannel"`, matching the real-world
convention above) or sibling files (`aov_layout="Separate Files"`, named after the AOV
itself, not a fixed suffix table).

### Channels MUST be addressed by name, never by index

OpenEXR does not preserve channel write order, and OIIO reorders on read. Verified with OIIO 3.1.15:

```
written:   R, G, B, A, N.X, N.Y, N.Z, Z
read back: R, G, B, A, Z, N.X, N.Y, N.Z     (spec.z_channel == 4)
```

Pixel data is permuted to match the names, so the file is correct — but a reader that assumes "normals are at indices 4–6 because that is where I wrote them" gets **depth** in the X slot and two-thirds of the normal in the rest. This fails silently and produces a plausible-looking wrong lighting fit. `ComfyUI_OpenClip/lib/image_io.py` currently reads flat RGB/RGBA by index and has no channel-name awareness, so this is new work, not a tweak.

## Out of scope for this doc

Anything about the harmonize node graph itself, spherical-harmonic math, or shadow projection — see `ComfyUI_Harmonize/docs/`.
