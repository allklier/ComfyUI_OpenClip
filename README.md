# ComfyOpenClip

OpenClip read/write nodes for ComfyUI, designed for VFX pipelines that use Autodesk Flame, but also other apps that handle OpenClips like Nuke and Baselight.

Frames rendered in ComfyUI are written as versioned OpenClip packages that Flame can import directly from its MediaHub. Clips exported from Flame can be read back into ComfyUI for processing. 

---

## Quick Start

The easiest way to use these nodes is to add an OpenClip Reader node at the beginning of the workflow. Point it at an existing .clip file rendered from Flame, leave version as 'current', and use the IMAGE output. (For a plain `name.####.exr` sequence with no `.clip`, such as a Blender render, use the OpenClip EXR Sequence Reader instead.)

Then at the end of the workflow add a OpenClip Writer node, connecting the finished image into the IMAGE input and the Reader's `clip_info` output into the Writer's `clip_info` input. That one connection carries the clip's path, name, start frame, start timecode, frame rate, colour space, schema version, frame padding and EXR header metadata. Leave the Writer's fields on their defaults ("auto") and the version on 'next': the result is added as a new version of the source clip, at the same timecode. To write a separate clip instead, add a suffix to the clip_filename pattern, e.g. `$(path)/$(clip_name)_updated.clip`.

The rest are options for more advanced workflows.

If a specific version should be read, rather than 'current', run the Reader once. The node then shows a report of the clip: format, start timecode, frame range, the versions and the AOVs (also on its `clip_report` output, which you can wire to a Preview Text node). Copy a version name into the 'version' input.

## Upgrading from 1.x to 2.0

2.0 changes the Reader and Writer connections, so saved workflows need re-wiring:

- **Reader** outputs are now `IMAGE`, `MASK`, `clip_info`, `aov_handle`, `frame_count`, `width`, `height`, `fps`, `clip_report`. Removed: `format_version`, `version_name`, `start_frame`, `clip_path`, `clip_name`, `metadata`, `colour_space`, `available_versions`, `available_aovs`. Their values are in `clip_info` (use the new OpenClip Clip Info node to get one as its own output); the version and AOV lists are in `clip_report`. `IMAGE` and `MASK` stay in the first two slots; the other outputs moved.
- **EXR Sequence Reader**: same outputs as the Reader.
- **Writer**: the `CLIP_METADATA`, `colour_space` and `format_version` connections are gone; connect `clip_info` instead. `fps`, `start_frame`, `frame_padding`, `colour_space` and `format_version` are now fields whose default is "auto" (from clip_info, else the old default), and there is a new `start_timecode` field. The order of the Writer's fields changed, so check them on old Writer nodes.
- **Behaviour**: the start timecode is now kept (it used to be written as 00:00:00:00, which broke appended versions on a Flame clip with a real timecode); the Reader's colour comes from the .clip; clip names keep their leading and trailing spaces; an empty Writer colour means `Rec.709 video`.

## Nodes

### OpenClip Writer
Writes an image sequence and generates a `.clip` XML manifest that Flame can import.

With the default `Standard Flame` layout, the `.clip` file is written into the folder given by the path, next to a folder of the same name that holds the frames:

```
include_version_in_filename off          include_version_in_filename on  (Flame-compatible)
<path>/                                  <path>/
  foo.clip                                 foo.clip
  foo/                                     foo/
    v001/foo.0001.exr …                      foo_v001.0001.exr …
    v002/foo.0001.exr …                      foo_v002.0001.exr …
```

**Turning `include_version_in_filename` on gives the same layout Flame itself uses for its Open Clip exports**, so ComfyUI versions sit alongside Flame's own in the same folder. With it off, each version gets its own subfolder.

Clips written by earlier releases (`foo/foo.clip` with a `versions/` subfolder) can still be read and appended to; new versions added to such a clip keep that older layout.

Various options can be set to configure the clip and the EXR file generation. In a round trip from Flame, wire the Reader's `clip_info` into the Writer and leave the fields on auto.

The write node has the following inputs:

- IMAGE: takes an image sequence (if the workflow generates video rather than image batch, use a 'Get Video Components' node to extract an image batch and accompanying parameters).
- MASK (optional): an alpha channel to go with the EXR files.
- clip_info (optional): from either reader. Fills every field left on "auto" (below) and carries the EXR header metadata.
- aov_chain (optional): AOVs to write alongside the beauty pass, built with OpenClip AOV Writer nodes.

And these settings. "Auto" means: taken from clip_info when it is connected, else the default shown. Any other value overrides clip_info.

- clip_path (auto: empty): where the OpenClip files will be stored. Required if clip_info isn't connected.
- clip_name (auto: empty): logical clip name. Required if clip_info isn't connected. Leading and trailing spaces are kept, as Flame keeps them.
- clip_filename: filename of the .clip file. This uses a pattern, referencing the other clip settings to construct the full filename. Default is: `$(path)/$(clip_name).clip`.
- version_name: the text string of the version. The default of 'next' will allocate the next version, or start with v001. 'next' keeps the clip's own numbering: a Flame clip at `v0` gets `v1`, ours at `v001` get `v002`. Never taken from clip_info.
- overwrite: whether existing versions are allowed to be overwritten. If the latest version is 'v003', and the version_name is also set to 'v003', the existing version 'v003' will be deleted and a new version will be written. If disabled, writing an existing version number results in an error.
- include_version_in_filename: on by default. Adds the version to the frame filenames (`foo_v003.0001.exr`) and puts all versions' frames in one folder — the layout Flame uses for its own Open Clip exports, which lets Flame add its own versions to the clip. Off: one subfolder per version, version not in the filename.
- fps (auto: 0, else 24): the frame rate to place into the clip metadata (must match a standard rate such as 23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60).
- start_frame (auto: -1, else 1): the number of the first written frame. 1 is how Flame numbers its own renders; use 1001 for Nuke or film pipelines.
- start_timecode (auto: empty, else the existing clip's when adding a version, else 00:00:00:00): `HH:MM:SS:FF`, or `HH:MM:SS;FF` for drop-frame. Written into the clip and, per frame, into the EXR files (up to 30 fps). Start timecode and start frame are separate: one is never worked out from the other.
- frame_padding (auto: 0, else 4): how many digits the frame numbers have.
- colour_space (auto: empty, else `Rec.709 video`): the colour space written into the .clip and the EXR headers. `Rec.709 video` is the name Flame used before 2026, which Flame 2026 and later still recognise (as an alias of `Rec.1886 Rec.709 - Display`), so the same tag works across Flame versions.
- format_version (auto: empty, else 6): the OpenClip schema version (5–9) of a new clip.
- file_format: choose between EXR and PNG (AOVs require EXR).
- exr_bit_depth, exr_compression: EXR specific.
- aov_layout: `Multichannel` writes each AOV as a named part inside the same EXR file; `Separate Files` writes one extra `<name>_AOV_<aov>.####.exr` file per AOV.
- publish: if selected, a copy of the JSON file of the current ComfyUI workflow will be copied next to the version's frames.

When adding a version to an existing clip, the Writer checks before writing anything, so a refused run leaves nothing on disk:

- the format (size, frame rate, file format) must match; if it doesn't, write a separate clip with a new clip_name;
- the start timecode must match the clip's current version (a clip without one counts as 00:00:00:00), also with overwrite on, because Flame would otherwise place the versions apart in time;
- a different start frame is only a warning when the clip has a real timecode, but an error when both are at 00:00:00:00, as nothing else would line the versions up.

When the destination is the very `.clip` file clip_info was read from, the version is added to exactly that file.

### OpenClip Reader
Reads any version from an existing `.clip` file and returns an IMAGE and MASK tensor batch. Also owns version browsing/selection — there is no separate selector node.

The reader node has the following inputs/settings:

- clip_path: the full filename of the .clip file to be read.
- version: the version to be read (defaults to 'current', based on which version is marked as current in .clip file)
- end_frame/start_frame: select a specific frame range to be read (-1 means the clip's own range)
- path_from/path_to: replace a path prefix in the media paths stored in the .clip (leave blank to auto-detect, e.g. when a clip made on another machine points at a different mount).
- frame_cap: read at most this many frames (-1 = no cap).

The node has the following outputs:

- IMAGE: the image batch read from the file.
- MASK: the alpha channel read from the file (fully opaque if the source has no alpha).
- clip_info: everything about the clip in one connection (path, name, version read, start frame, start timecode, frame rate, colour space, schema version, frame padding, EXR header metadata). Wire it into the Writer, or into an OpenClip Clip Info node to get single values.
- aov_handle: a lightweight handle to the clip's frames for the OpenClip AOV Reader (decodes nothing itself).
- frame_count: the number of frames read.
- width/height: the dimensions of the canvas.
- fps: frame rate indicated in the file.
- clip_report: the report also shown inside the node: clip, format and colour, start timecode and frame range, then one line per version and per AOV, so names can be copied.

The colour space is taken from the .clip (`colourSpace`), else from the EXR header (`autodeskColorSpace`, then the OIIO keys).

The Reader also takes an optional frame range and version. Queue the Reader alone to inspect a clip's versions and AOVs.

### OpenClip EXR Sequence Reader
Reads plain `name.####.exr` sequences with no `.clip` file, such as Blender renders (including Blender's single-part "OpenEXR MultiLayer" layout) or Nuke output. Has the same outputs as the OpenClip Reader, so the two can be swapped behind a switch node. Its clip_info has no version, schema version or start timecode; wired into a Writer, it writes `<folder>/<name>.clip`.

### OpenClip Clip Info
Takes a clip_info and outputs each value on its own socket (clip_path, clip_name, version_name, start_frame, start_timecode, fps, colour_space, format_version, frame_padding, metadata), plus the clip_report text.

### OpenClip AOV Reader
Takes an `aov_handle` from either reader and a `part_name`, and decodes only that one named part (e.g. depth, normals, a puzzle matte). Outputs `image` and `matte`; a single-channel AOV is returned as `matte` only. The AOVS section of the reader's report lists the names. Cryptomatte decoding is not supported. If the named part is not found in any frame, the outputs are `None`.

### OpenClip AOV Writer
Builds an `AOV_WRITE_CHAIN` of named AOV entries (`name`, `image` and/or `matte`). Chain several together via `aov_chain`, then wire the result into the OpenClip Writer's `aov_chain` input. AOVs can only be written to EXR, not PNG.

### OpenClip Colour Space Transform
Converts between OCIO colour spaces; the target can be 'Same as input (via pipe)', using the `colour_space` output of an OpenClip Clip Info node fed by the Reader's clip_info.

### OpenClip Colour Transform

Applies an OCIO display/view transform (including the ACES output transform, with a selectable `view`) to make log and camera spaces compatible with models that have been trained on BT1886/Rec709 footage.

Insert after the reader and before the rest of the workflow.

The config defaults to the `$OCIO` environment variable if set; otherwise, on Flame 2026 or later workstations, to the ACES 2.0 OCIO config that ships with Flame (`/opt/Autodesk/colour_mgmt/configs/flame_configs/<version>/aces2.0_config/config.ocio`).

### Flame 2025 and earlier (SynColor)

Both colour transform nodes use OCIO. Flame switched from its own SynColor colour management to OCIO in Flame 2026, so on older Flame workstations there's no default config to find. You have two options:

- **Do the colour conversion in Flame** before and after ComfyUI, and leave these nodes out of the workflow.
- **Use one of Autodesk's SynColor-emulation OCIO configs.** These ship with Flame 2026 and later in `/opt/Autodesk/colour_mgmt/configs/legacy_configs/` (e.g. `syncolor_aces1.1_config/config.ocio`). They are not part of this node pack; copy the folder from a Flame 2026+ install or obtain it from Autodesk, then point `ocio_config` (or `$OCIO`) at its `config.ocio`. With such a config:
  - Colour space names are the old SynColor names (e.g. `Rec.709 video`, `scene-linear Alexa WideGamut`). The Colour Space Transform's dropdown entries and the Colour Transform's display are translated automatically; anything typed by hand must use the old names. `Sony S-Log3 (S-Gamut3.Cine)` has no equivalent in these configs.
  - The default `view` (`ACES 2.0 - SDR 100 nits (Rec.709)`) doesn't exist; use e.g. `ACES 1.1 SDR-video (Rec.709 limited)` or `ACES 1.0 SDR-video`. A wrong view name produces an error listing the available ones.
  - These configs emulate the old SynColor transforms; an exact match to what a given pre-2026 Flame produced hasn't been verified.

---

## Screenshots

Several screenshots showing latest version of the nodes in action. There are two versions of the write node, one that is paired with the read node, the other that is independent on a brand new open clip file generated by a workflow.

![Reader Example](docs/screen_openclip_reader.png)
![Writer Example - new clip](docs/screen_openclip_writer.png)
![Writer Example - round trip](docs/screen_openclip_writer_roundtrip.png)
![Color Space Transform](docs/screen_openclip_colorspace.png)

---

## Requirements

| Package | Purpose | Required |
|---|---|---|
| `lxml` | XML parse and serialise | Always |
| `openimageio` | EXR and PNG I/O | Always |
| `opencolorio` | OCIO colour transforms | Colour transform nodes only |

**ComfyUI Manager** installs all dependencies automatically from `requirements.txt` when you install the node pack — no manual steps needed.

**Manual install:**
```bash
pip install -r requirements.txt
```

If you don't need the colour transform nodes, `opencolorio` can be skipped — the other nodes will load and run without it.

---

## Installation

**Via ComfyUI Manager / the ComfyUI Registry** (recommended): search for "OpenClip" in the Manager, or run `comfy node install comfy-openclip`.

**Manual:**
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/allklier/ComfyUI_OpenClip.git
```

Restart ComfyUI. The nodes appear under the **OpenClip** category.

---

## Notes

- New clips are written as OpenClip schema version 6 by default (or the source clip's version, when clip_info is connected), so that older Flame releases can import them; set the Writer's `format_version` (5–9) to change it. Adding a version to an existing clip keeps that clip's own schema version and all of Flame's metadata.
- Tested with Flame 2027: a clip written by the Writer opens, and Flame can add its own versions to it. Flame 2024 and 2025 have not been tested; schema 6 and the `Rec.709 video` tag were chosen for them, based on a Flame 2024.1 user's report and the names older Flame wrote.
- Pattern-based clips (`ScanPattern`) and clips referencing movie files (`.mov` etc.) can't be read; stereo clips read the left eye.
- Screenshots below predate 2.0 (clip_info) and the AOV and EXR Sequence Reader nodes.
- Frames are never clamped on the EXR path (half or float); PNG output is 8-bit and does clamp.
- The Colour Transform node's `view` input defaults to `ACES 2.0 - SDR 100 nits (Rec.709)` to match what Flame expects on import, but any view exposed by the OCIO config can be used instead.
- Flame users can point the OCIO config field at their project config, typically found at `/opt/Autodesk/colour_mgmt/configs/flame_configs/<version>/aces2.0_config/config.ocio`.
