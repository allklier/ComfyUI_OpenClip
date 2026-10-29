"""Builds small Flame-like .clip files with tiny EXR frames, for the synthetic boundary suite.

Modelled on real Flame exports (the 2027 repro clip testclips/clip_1/alpha_test.clip and the
v6 corpus clip in test_writer_reader_gaps.FLAME_CLIP): tab-indented XML, root userData with
Flame's double-encoded "AutodeskÂ®", hex track/feed uids, a track <name>, full <storageFormat>,
feed <userData><recordTimecode>, <sampleRate>, <startTimecode>, absolute range-notation paths
named <name>_<version>.[first-last].exr, and a top-level <versions> block. The frames are written
with plain OIIO (not lib/image_io), carrying what Flame puts in its EXRs: PIZ, autodeskColorSpace,
framesPerSecond and a per-frame smpte:TimeCode (encoded by tests/flame_compare, not lib/timecode).
Nothing Autodesk-owned is copied: only the structure.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional

import numpy as np

from .flame_compare import RATE_LABELS, bcd_encode, tc_label, tc_ticks

SIZE = 8


@dataclass(frozen=True)
class FlameLike:
    id: str
    name: str = "shot"
    schema: str = "9"
    fps: str = "23.976"
    rate_xml: str = "rational"         # "rational" (Flame 2027), "decimal" (<editRate>23.976</editRate>), "none"
    tc: Optional[str] = "00:00:00:00"   # None: no <startTimecode> at all
    start: int = 1
    n: int = 3
    padding: int = 6
    colour: Optional[str] = "Rec.709 video"   # XML <colourSpace>; None: absent
    exr_colour: Optional[str] = "same"        # EXR autodeskColorSpace; "same" = colour, None = absent
    versions: tuple = ("v0",)                 # the last one is current
    versions_block: bool = True
    audio: Optional[str] = None               # "after" / "before" the video track
    channels: int = 3
    depth: int = 16
    next_version: str = "v1"                  # what Flame numbering continues with

    @property
    def rate(self) -> Fraction:
        return RATE_LABELS[self.fps]

    @property
    def tc_rate(self) -> int:
        return round(self.rate)

    @property
    def drop(self) -> bool:
        return self.tc is not None and ";" in self.tc

    @property
    def ticks(self) -> int:
        return tc_ticks(self.tc, self.tc_rate) if self.tc else 0

    @property
    def exr_colour_value(self) -> Optional[str]:
        return self.colour if self.exr_colour == "same" else self.exr_colour


def _rate_xml(tag: str, case: FlameLike, indent: str) -> str:
    if case.rate_xml == "none":
        return ""
    if case.rate_xml == "decimal":
        attr = ' type="rate"' if tag == "editRate" else ""
        return f"{indent}<{tag}{attr}>{case.fps}</{tag}>\n"
    attr = ' type="rate"' if tag == "editRate" else ""
    return (f"{indent}<{tag}{attr}>\n{indent}\t<numerator>{case.rate.numerator}</numerator>\n"
            f"{indent}\t<denominator>{case.rate.denominator}</denominator>\n{indent}</{tag}>\n")


def _time_xml(tag: str, case: FlameLike, indent: str, label: bool = False) -> str:
    h, m, s, f = tc_label(case.ticks, case.tc_rate, case.drop)
    lab = f' label="{h:02d}:{m:02d}:{s:02d}+{f:02d}"' if label else ""
    return (f"{indent}<{tag} type=\"time\"{lab}>\n{indent}\t<rate>{case.tc_rate}</rate>\n"
            f"{indent}\t<nbTicks>{case.ticks}</nbTicks>\n"
            f"{indent}\t<dropMode>{'DF' if case.drop else 'NDF'}</dropMode>\n{indent}</{tag}>\n")


def _write_frames(case: FlameLike, pattern: str, seed: int) -> None:
    import OpenImageIO as oiio

    rng = np.random.default_rng(seed)
    fmt = oiio.HALF if case.depth == 16 else oiio.FLOAT
    for i in range(case.n):
        frame = case.start + i
        spec = oiio.ImageSpec(SIZE, SIZE, case.channels, fmt)
        spec.channelnames = ("R", "G", "B", "A")[:case.channels]
        spec["compression"] = "piz"
        spec.attribute("framesPerSecond", oiio.TypeRational, (case.rate.numerator, case.rate.denominator))
        if case.exr_colour_value:
            spec.attribute("autodeskColorSpace", case.exr_colour_value)
        if case.tc is not None and case.tc_rate <= 30:
            word = bcd_encode(*tc_label(case.ticks + i, case.tc_rate, case.drop), case.drop)
            spec.attribute("smpte:TimeCode", oiio.TypeTimeCode, (word, 0))
        path = pattern % frame
        out = oiio.ImageOutput.create(path)
        assert out and out.open(path, spec), f"OIIO open failed: {path}"
        assert out.write_image(rng.random((SIZE, SIZE, case.channels), dtype=np.float32)), path
        out.close()


def build(case: FlameLike, folder: Path) -> Path:
    """Write '<folder>/<name>.clip' (name verbatim) plus frames for every version in
    '<folder>/<name>/'. Returns the .clip path."""
    media = folder / case.name
    media.mkdir(parents=True, exist_ok=True)
    feeds = []
    for k, vuid in enumerate(case.versions):
        pattern = str(media / f"{case.name}_{vuid}.%0{case.padding}d.exr")
        _write_frames(case, pattern, seed=k)
        first, last = case.start, case.start + case.n - 1
        rng = f"[{first:0{case.padding}d}-{last:0{case.padding}d}]"
        I = "\t" * 5
        colour = f'{I}\t<colourSpace type="string">{case.colour}</colourSpace>\n' if case.colour else ""
        tc = ""
        if case.tc is not None:
            tc = (f"{I}<userData type=\"dict\">\n" + _time_xml("recordTimecode", case, I + "\t", label=True)
                  + f"{I}</userData>\n")
        feeds.append(
            f"\t\t\t\t<feed vuid=\"{vuid}\" uid=\"{uuid.uuid4().hex}\">\n"
            f"{I}<storageFormat type=\"format\">\n"
            f"{I}\t<type>video</type>\n"
            f"{I}\t<channelsDepth type=\"uint\">{case.depth}</channelsDepth>\n"
            f"{I}\t<channelsEncoding type=\"string\">Float</channelsEncoding>\n"
            f"{I}\t<channelsEndianess type=\"string\">Little Endian</channelsEndianess>\n"
            f"{colour}"
            f"{I}\t<fieldDominance type=\"int\">2</fieldDominance>\n"
            f"{I}\t<height type=\"uint\">{SIZE}</height>\n"
            f"{I}\t<nbChannels type=\"uint\">{case.channels}</nbChannels>\n"
            f"{I}\t<pixelLayout type=\"string\">{'RGBA' if case.channels == 4 else 'RGB'}</pixelLayout>\n"
            f"{I}\t<pixelRatio type=\"float\">1</pixelRatio>\n"
            f"{I}\t<rowOrdering type=\"string\">down</rowOrdering>\n"
            f"{I}\t<width type=\"uint\">{SIZE}</width>\n"
            f"{I}</storageFormat>\n"
            f"{tc}"
            + _rate_xml("sampleRate", case, I)
            + (_time_xml("startTimecode", case, I) if case.tc is not None else "")
            + f"{I}<spans>\n{I}\t<span>\n"
            f"{I}\t\t<path encoding=\"pattern\">{media}/{case.name}_{vuid}.{rng}.exr</path>\n"
            f"{I}\t</span>\n{I}</spans>\n"
            f"\t\t\t\t</feed>\n"
        )
    current = case.versions[-1]
    video = (
        f"\t\t<track uid=\"{uuid.uuid4().hex}\">\n"
        f"\t\t\t<trackType>video</trackType>\n"
        + _rate_xml("editRate", case, "\t\t\t")
        + f"\t\t\t<name type=\"string\">{case.name}</name>\n"
        f"\t\t\t<feeds currentVersion=\"{current}\">\n" + "".join(feeds) + "\t\t\t</feeds>\n"
        f"\t\t</track>\n"
    )
    audio = (
        f"\t\t<track uid=\"{uuid.uuid4().hex}\">\n\t\t\t<trackType>audio</trackType>\n"
        f"\t\t\t<feeds><feed vuid=\"{case.versions[0]}\" uid=\"{uuid.uuid4().hex}\"><spans><span>"
        f"<path encoding=\"file\">{media}/{case.name}.wav</path></span></spans></feed></feeds>\n"
        f"\t\t</track>\n"
    )
    tracks = {"after": video + audio, "before": audio + video, None: video}[case.audio]
    versions = ""
    if case.versions_block:
        versions = f"\t<versions currentVersion=\"{current}\">\n"
        for k, vuid in enumerate(case.versions, start=1):
            versions += (
                f"\t\t<version uid=\"{vuid}\">\n\t\t\t<name>{vuid}</name>\n"
                f"\t\t\t<creationDate>2026/10/03 11:35:20 AM</creationDate>\n"
                f"\t\t\t<userData type=\"dict\">\n"
                f"\t\t\t\t<appName type=\"binary\">AutodeskÂ® FlameÂ®</appName>\n"
                f"\t\t\t\t<appVersion type=\"string\">2027.2</appVersion>\n"
                f"\t\t\t\t<module type=\"string\">Batch</module>\n"
                f"\t\t\t\t<versionNumber type=\"uint64\">{k}</versionNumber>\n"
                f"\t\t\t</userData>\n\t\t</version>\n"
            )
        versions += "\t</versions>\n"
    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        f"<clip type=\"clip\" version=\"{case.schema}\">\n"
        f"\t<name type=\"string\">{case.name}</name>\n"
        f"\t<sourceName type=\"string\">A001_C002_{case.id}</sourceName>\n"
        "\t<userData type=\"dict\">\n"
        "\t\t<appName type=\"binary\">AutodeskÂ® FlameÂ®</appName>\n"
        "\t\t<appVersion type=\"string\">2027.2</appVersion>\n"
        "\t\t<module type=\"string\">Batch</module>\n"
        "\t</userData>\n"
        f"\t<tracks>\n{tracks}\t</tracks>\n"
        f"{versions}"
        "</clip>\n"
    )
    clip = folder / f"{case.name}.clip"
    clip.write_text(xml, encoding="utf-8")
    return clip
