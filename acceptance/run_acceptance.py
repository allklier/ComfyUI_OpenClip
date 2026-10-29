#!/usr/bin/env python
"""Acceptance run for the OpenClip nodes against a *live* ComfyUI server (HTTP API), 2.0 interface.

Run it on the machine hosting ComfyUI, with the ComfyUI python env (needs OpenImageIO, numpy,
lxml, torch -- all present where the pack runs):

    python run_acceptance.py --url http://127.0.0.1:8199 --work ~/oc_accept/work \
        --pack ~/oc_accept/base/custom_nodes/ComfyUI_OpenClip \
        [--flame-clip /path/to/real.clip] [--server-log ~/oc_accept/server.log] \
        [--mode normal|noocio] [--json result.json]

See acceptance/README.md for how the private server is started (scripts/maintainer_test.sh does
all of it). Output is a PASS/FAIL table; exit code is non-zero if anything failed. --json also
writes the results as a list of {name, ok, detail}.

--flame-clip is any Flame .clip with an EXR sequence; it is only read (its current version's first
frames are copied into --work). Without it, a synthetic Flame-like clip is built (tests/flame_fixtures.py).

Tests:
  T1  pack loads in a real server: all 8 nodes registered, no import traceback in the server log;
      --mode noocio: loads, reads and writes with PyOpenColorIO blocked, colour nodes fail cleanly.
  T4  Reader -> Writer (clip_info wired) 'next' twice, cache hit on re-queue, cache invalidation on
      re-render, version collision/overwrite; AOV Writer chain -> Writer -> Reader -> AOV Reader,
      both layouts.
  T5  OpenClipExrSequenceReader on a Blender-style single-part multilayer sequence.
  T6  2.0 / issue #7, live: start timecode kept on a new clip and on append (and its two refusal
      cases), colour tag kept, a trailing-space name appends to the exact source file, the
      reader's clip_report in the node's ui output, OpenClipClipInfo outputs.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

import numpy as np
import OpenImageIO as oiio

RESULTS: list[tuple[str, bool, str]] = []
NODES = {"OpenClipReader", "OpenClipWriter", "OpenClipExrSequenceReader", "OpenClipAOVReader",
         "OpenClipAOVWriter", "OpenClipColourSpaceTransform", "OpenClipColourTransform", "OpenClipClipInfo"}

# Reader / EXR Sequence Reader output slots (2.0)
IMAGE, MASK, CLIP_INFO, AOV_HANDLE, FRAME_COUNT, WIDTH, HEIGHT, FPS, CLIP_REPORT = range(9)
# OpenClipClipInfo output slots
CI_START_TIMECODE, CI_COLOUR_SPACE, CI_BIT_DEPTH = 4, 6, 10

fc = ff = None  # tests/flame_compare and tests/flame_fixtures of the pack under test (see main)


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)


# ---------------------------------------------------------------- API plumbing
class Server:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def _req(self, path: str, data: dict | None = None):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # ComfyUI puts validation errors in the body
            return {"_http_error": e.code, **json.loads(e.read() or b"{}")}

    def object_info(self) -> dict:
        return self._req("/object_info")

    def run(self, prompt: dict, timeout: float = 300) -> dict:
        """Queue a prompt, wait for it, return the history entry (or an error dict)."""
        r = self._req("/prompt", {"prompt": prompt})
        if "prompt_id" not in r:
            return {"error": r}
        pid = r["prompt_id"]
        t0 = time.time()
        while time.time() - t0 < timeout:
            h = self._req(f"/history/{pid}")
            if pid in h and h[pid].get("status", {}).get("completed") is not None:
                return h[pid]
            time.sleep(0.3)
        return {"error": "timeout"}


def failed(h: dict) -> str | None:
    if "error" in h:
        return json.dumps(h["error"])[:600]
    st = h.get("status", {})
    if st.get("status_str") != "success":
        for ev, payload in st.get("messages", []):
            if ev == "execution_error":
                return f"{payload.get('node_type')}: {payload.get('exception_message', '').strip()[:500]}"
        return "status " + str(st.get("status_str"))
    return None


def cached_nodes(h: dict) -> set[str]:
    for ev, payload in h.get("status", {}).get("messages", []):
        if ev == "execution_cached":
            return set(payload.get("nodes", []))
    return set()


# ---------------------------------------------------------------- pixel helpers
def read_exr(path: str) -> dict[str, np.ndarray]:
    inp = oiio.ImageInput.open(path)
    spec = inp.spec()
    px = inp.read_image(oiio.FLOAT)
    inp.close()
    return {n: px[:, :, i] for i, n in enumerate(spec.channelnames)}


def write_blender_style(folder: str, frames=(1, 2, 3), size=48) -> dict[int, dict[str, np.ndarray]]:
    channels = (
        "CamelMatte.Combined.A", "CamelMatte.Combined.B", "CamelMatte.Combined.G", "CamelMatte.Combined.R",
        "ViewLayer.Combined.A", "ViewLayer.Combined.B", "ViewLayer.Combined.G", "ViewLayer.Combined.R",
        "ViewLayer.Depth.Z",
        "ViewLayer.Normal.X", "ViewLayer.Normal.Y", "ViewLayer.Normal.Z",
    )
    os.makedirs(folder, exist_ok=True)
    out = {}
    for f in frames:
        rng = np.random.default_rng(100 + f)
        px = {n: (rng.random((size, size)).astype(np.float32) - 0.5) for n in channels}
        out[f] = px
        spec = oiio.ImageSpec(size, size, len(channels), oiio.FLOAT)
        spec.channelnames = channels
        spec.attribute("oiio:ColorSpace", "lin_ap1_scene")
        path = os.path.join(folder, f"render.{f:04d}.exr")
        o = oiio.ImageOutput.create(path)
        assert o.open(path, spec) and o.write_image(np.stack([px[n] for n in channels], axis=-1))
        o.close()
    return out


def rgb(chs: dict[str, np.ndarray], prefix: str = "") -> np.ndarray:
    return np.stack([chs[f"{prefix}{c}"] for c in "RGB"], axis=-1)


def find_clip(folder: str, name: str) -> str:
    hits = glob.glob(os.path.join(folder, "**", f"{name}.clip"), recursive=True)
    return hits[0] if hits else os.path.join(folder, f"{name}.clip")


# ---------------------------------------------------------------- node-prompt builders
def reader(clip_path: str, frame_cap: int = -1, version: str = "current", start: int = -1, end: int = -1,
           path_from: str = "", path_to: str = "") -> dict:
    return {"class_type": "OpenClipReader", "inputs": {
        "clip_path": clip_path, "version": version, "start_frame": start, "end_frame": end,
        "path_from": path_from, "path_to": path_to, "frame_cap": frame_cap}}


def writer(image, clip_path="", clip_name="", version="next", overwrite=False, fmt="EXR", layout="Multichannel",
           clip_info=None, **extra) -> dict:
    """2.0 Writer: every auto field left on auto unless given in **extra."""
    inputs = {
        "IMAGE": image, "clip_path": clip_path, "clip_name": clip_name,
        "clip_filename": "$(path)/$(clip_name).clip", "version_name": version, "overwrite": overwrite,
        "include_version_in_filename": True, "fps": 0.0, "start_frame": -1, "start_timecode": "",
        "frame_padding": 0, "colour_space": "", "format_version": "",
        "file_format": fmt, "exr_bit_depth": "auto", "exr_compression": "ZIP",
        "aov_layout": layout, "publish": False}
    if clip_info is not None:
        inputs["clip_info"] = clip_info
    inputs.update(extra)
    return {"class_type": "OpenClipWriter", "inputs": inputs}


def preview(source) -> dict:
    return {"class_type": "PreviewAny", "inputs": {"source": source}}


# ---------------------------------------------------------------- sources
def prepare_source(flame_clip: str | None, work: str, n: int = 6) -> tuple[str, str, str]:
    """A private copy of the source clip and the first n frames of its current version in
    <work>/src, so frames can be re-rendered without touching the original. Returns
    (clip copy, path_from, path_to) for the Reader's remap."""
    src = os.path.join(work, "src")
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(src)
    if not flame_clip:
        case = ff.FlameLike("acceptance", name="acc_src", fps="24", tc="01:00:00:00", n=n, padding=6,
                            colour="Rec.709 video", channels=4)
        clip = str(ff.build(case, __import__("pathlib").Path(src)))
        return clip, "", ""
    pattern = fc.source_media_pattern(flame_clip)
    first = fc.feed_fields(flame_clip)["first"]
    for f in range(first, first + n):
        shutil.copy(pattern % f, src)
    clip = os.path.join(src, "src.clip")
    shutil.copy(flame_clip, clip)
    return clip, os.path.dirname(pattern), src


# ================================================================ tests
def test1_load(srv: Server, server_log: str, mode: str) -> None:
    info = srv.object_info()
    missing = NODES - set(info)
    record(f"T1[{mode}] all {len(NODES)} nodes registered in running server", not missing, f"missing={sorted(missing)}")
    log = open(server_log, errors="replace").read() if os.path.exists(server_log) else ""
    bad = [l for l in log.splitlines() if "OpenClip" in l and ("Traceback" in l or "IMPORT FAILED" in l or "Cannot import" in l)]
    record(f"T1[{mode}] no OpenClip import failure/traceback in server log", not bad, "; ".join(bad)[:300])
    ins = info.get("OpenClipWriter", {}).get("input", {})
    sockets = sorted(ins.get("optional", {}))
    record(f"T1[{mode}] Writer sockets are MASK, clip_info, aov_chain (2.0)", sockets == ["MASK", "aov_chain", "clip_info"],
           f"optional={sockets}")
    outs = info.get("OpenClipReader", {}).get("output_name", [])
    record(f"T1[{mode}] Reader has the 9 2.0 outputs", list(outs) == ["IMAGE", "MASK", "clip_info", "aov_handle",
           "frame_count", "width", "height", "fps", "clip_report"], f"{outs}")


def test1_noocio(srv: Server, clip: str, work: str, pack: str) -> None:
    h = srv.run({"1": reader(clip, frame_cap=2),
                 "2": writer(["1", IMAGE], os.path.join(work, "noocio_out"), "noocio", clip_info=["1", CLIP_INFO])})
    err = failed(h)
    record("T1[noocio] Reader->Writer works with OCIO unavailable", err is None, err or "")
    p = {"1": reader(clip, frame_cap=1),
         "2": {"class_type": "OpenClipColourSpaceTransform", "inputs": {
             "IMAGE": ["1", IMAGE], "ocio_config": os.path.join(pack, "tests", "fixtures", "test_ocio.ocio"),
             "source": "ACEScg (already the working space)", "custom_source": "",
             "target_colour_space": "ACEScg (already the working space)", "custom_target_colour_space": ""}},
         "3": writer(["2", 0], os.path.join(work, "noocio_out2"), "noocio2")}
    err = failed(srv.run(p))
    record("T1[noocio] colour transform without OCIO errors cleanly (server survives)",
           err is not None and srv.object_info() is not None, (err or "NO ERROR RAISED")[:200])


def test4_round_trip(srv: Server, clip: str, remap: dict, work: str) -> None:
    out = os.path.join(work, "rt")
    shutil.rmtree(out, ignore_errors=True)
    prompt = {"1": reader(clip, frame_cap=6, **remap),
              "2": writer(["1", IMAGE], out, "rt", clip_info=["1", CLIP_INFO], MASK=["1", MASK])}
    err = failed(srv.run(prompt))
    record("T4 Flame .clip -> Reader -> Writer(next, clip_info) #1 executes", err is None, err or "")
    h2 = srv.run(prompt)  # identical prompt: everything should come from cache
    err2 = failed(h2)
    record("T4 identical re-queue is served from cache", err2 is None and cached_nodes(h2) >= {"1", "2"},
           f"cached={sorted(cached_nodes(h2))}")

    # force a second *execution* via an upstream-neutral change (frame_cap 6 -> 5)
    prompt["1"]["inputs"]["frame_cap"] = 5
    err3 = failed(srv.run(prompt))
    rt_clip = find_clip(out, "rt")
    clip_xml = open(rt_clip, errors="replace").read() if os.path.exists(rt_clip) else ""
    ok = err3 is None and 'vuid="v001"' in clip_xml and 'vuid="v002"' in clip_xml and 'currentVersion="v002"' in clip_xml
    record("T4 second Writer run auto-allocates v002 (v001 kept, currentVersion=v002)", ok, f"err={err3}")

    # a re-render of a source frame must invalidate the Reader's cache
    prompt["1"]["inputs"]["frame_cap"] = 6
    srv.run(prompt)
    hit = srv.run({"1": prompt["1"]})  # Reader alone (OUTPUT_NODE)
    first = fc.source_media_pattern(clip, None)
    first = (first if not remap.get("path_from") else first.replace(remap["path_from"], remap["path_to"], 1)) \
        % fc.feed_fields(clip)["first"]
    time.sleep(1.1)
    os.utime(first, None)
    miss = srv.run({"1": prompt["1"]})
    record("T4 Reader cache: hit when untouched, invalidated by re-render of first frame",
           "1" in cached_nodes(hit) and "1" not in cached_nodes(miss),
           f"untouched cached={'1' in cached_nodes(hit)} after-touch cached={'1' in cached_nodes(miss)}")

    p = {"1": reader(clip, frame_cap=3, **remap),
         "2": writer(["1", IMAGE], out, "rt", version="v001", clip_info=["1", CLIP_INFO], MASK=["1", MASK])}
    err = failed(srv.run(p))
    record("T4 explicit existing version without overwrite is refused with the banner message",
           err is not None and "VERSION ALREADY EXISTS" in err, (err or "no error")[:160])
    p["2"]["inputs"]["overwrite"] = True
    err = failed(srv.run(p))
    n = len(glob.glob(os.path.join(out, "**", "rt_v001.*.exr"), recursive=True))
    record("T4 overwrite=True replaces v001 (3 frames now, stale frames deleted)", err is None and n == 3, f"err={err} frames={n}")


def test4_aov(srv: Server, clip: str, remap: dict, work: str) -> None:
    pattern = fc.source_media_pattern(clip)
    if remap.get("path_from"):
        pattern = pattern.replace(remap["path_from"], remap["path_to"], 1)
    first = fc.feed_fields(clip)["first"]
    orig = [pattern % f for f in range(first, first + 4)]
    for layout in ("Multichannel", "Separate Files"):
        tag = layout.split()[0].lower()
        out = os.path.join(work, f"aov_{tag}")
        shutil.rmtree(out, ignore_errors=True)
        p = {
            "1": reader(clip, frame_cap=4, **remap),
            "2": {"class_type": "ImageToMask", "inputs": {"image": ["1", IMAGE], "channel": "red"}},
            "3": {"class_type": "OpenClipAOVWriter", "inputs": {"name": "beauty_copy", "image": ["1", IMAGE]}},
            "4": {"class_type": "OpenClipAOVWriter", "inputs": {"name": "red_matte", "matte": ["2", 0], "aov_chain": ["3", 0]}},
            "5": writer(["1", IMAGE], out, "aov", layout=layout, aov_chain=["4", 0], clip_info=["1", CLIP_INFO]),
        }
        err = failed(srv.run(p))
        record(f"T4 AOV write chain -> Writer ({layout})", err is None, err or "")
        aov_clip = find_clip(out, "aov")
        if layout == "Separate Files":
            sib_b = sorted(glob.glob(os.path.join(out, "**", "*_AOV_beauty_copy.*.exr"), recursive=True))
            sib_m = sorted(glob.glob(os.path.join(out, "**", "*_AOV_red_matte.*.exr"), recursive=True))
            okb = len(sib_b) == 4 and all(np.allclose(rgb(read_exr(a)), rgb(read_exr(b)), rtol=1e-3, atol=1e-3) for a, b in zip(orig, sib_b))
            okm = len(sib_m) == 4 and all(np.allclose(read_exr(a)["R"], read_exr(b)["R"], rtol=1e-3, atol=1e-3) for a, b in zip(orig, sib_m))
            record("T4 Separate Files: sibling AOV files on disk carry the right pixels", okb and okm, f"beauty_copy={len(sib_b)} red_matte={len(sib_m)}")
        out2 = os.path.join(work, f"aov_{tag}_back")
        shutil.rmtree(out2, ignore_errors=True)
        q = {
            "1": reader(aov_clip),
            "2": {"class_type": "OpenClipAOVReader", "inputs": {"aov_handle": ["1", AOV_HANDLE], "part_name": "beauty_copy"}},
            "3": {"class_type": "OpenClipAOVReader", "inputs": {"aov_handle": ["1", AOV_HANDLE], "part_name": "red_matte"}},
            "4": writer(["2", 0], out2, "back_beauty"),
            "5": {"class_type": "MaskToImage", "inputs": {"mask": ["3", 1]}},
            "6": writer(["5", 0], out2, "back_matte"),
        }
        h = srv.run(q)
        err = failed(h)
        record(f"T4 Reader -> AOV Reader x2 -> Writer ({layout}) executes", err is None, err or "")
        if err:
            continue
        report = (h.get("outputs", {}).get("1", {}).get("clip_report") or [""])[0]
        record(f"T4 clip_report lists both AOVs ({layout})", "  beauty_copy" in report and "  red_matte" in report,
               report.split("AOVS")[-1].strip().replace("\n", ",")[:120])
        back_b = sorted(glob.glob(os.path.join(out2, "**", "back_beauty*.exr"), recursive=True))
        back_m = sorted(glob.glob(os.path.join(out2, "**", "back_matte*.exr"), recursive=True))
        okb = len(back_b) == 4 and all(
            np.allclose(rgb(read_exr(a)), rgb(read_exr(b)), rtol=1e-3, atol=1e-3) for a, b in zip(orig, back_b))
        record(f"T4 AOV 'beauty_copy' pixels survive full round trip ({layout})", okb, f"orig={len(orig)} back={len(back_b)}")
        okm = len(back_m) == 4 and all(
            np.allclose(read_exr(a)["R"], read_exr(b)["R"], rtol=1e-3, atol=1e-3) for a, b in zip(orig, back_m))
        record(f"T4 AOV 'red_matte' (single channel) pixels survive round trip ({layout})", okm, f"back={len(back_m)}")


def test5_blender(srv: Server, work: str) -> None:
    folder = os.path.join(work, "blender")
    shutil.rmtree(folder, ignore_errors=True)
    truth = write_blender_style(folder)
    out = os.path.join(work, "blender_out")
    shutil.rmtree(out, ignore_errors=True)
    seq = os.path.join(folder, "render.####.exr")
    p = {
        "1": {"class_type": "OpenClipExrSequenceReader", "inputs": {
            "sequence_path": seq, "layer": "CamelMatte.Combined", "start_frame": -1, "end_frame": -1,
            "frame_cap": -1, "fps": 24.0, "colour_space": ""}},
        "2": {"class_type": "OpenClipAOVReader", "inputs": {"aov_handle": ["1", AOV_HANDLE], "part_name": "ViewLayer.Normal"}},
        "3": writer(["1", IMAGE], out, "beauty", MASK=["1", MASK]),
        "4": writer(["2", 0], out, "normals"),
        "5": writer(["1", IMAGE], clip_info=["1", CLIP_INFO]),  # folder/stem from the sequence
    }
    h = srv.run(p)
    err = failed(h)
    record("T5 EXR Sequence Reader (Blender multilayer, layer=CamelMatte.Combined) + AOV Reader executes", err is None, err or "")
    if err:
        return
    report = (h.get("outputs", {}).get("1", {}).get("clip_report") or [""])[0]
    record("T5 EXR Sequence Reader clip_report in the node's ui output", report.startswith("CLIP     render")
           and "VERSIONS" not in report, report.splitlines()[0][:100] if report else "missing")
    beauty = sorted(glob.glob(os.path.join(out, "**", "beauty*.exr"), recursive=True))
    normals = sorted(glob.glob(os.path.join(out, "**", "normals*.exr"), recursive=True))
    record("T5 all 3 frames read and written", len(beauty) == 3 and len(normals) == 3, f"beauty={len(beauty)} normals={len(normals)}")
    ok = len(beauty) == 3
    for i, f in enumerate((1, 2, 3)):
        if i >= len(beauty):
            break
        b = read_exr(beauty[i])
        ok &= np.allclose(rgb(b), rgb(truth[f], "CamelMatte.Combined."), atol=2e-3, rtol=1e-3)
        ok &= np.allclose(b["A"], truth[f]["CamelMatte.Combined.A"], atol=2e-3, rtol=1e-3)
    record("T5 beauty RGB + alpha match the Blender layer exactly (within half-float)", bool(ok))
    ok = len(normals) == 3
    for i, f in enumerate((1, 2, 3)):
        if i >= len(normals):
            break
        n = read_exr(normals[i])
        want = np.stack([truth[f][f"ViewLayer.Normal.{c}"] for c in "XYZ"], axis=-1)
        ok &= np.allclose(rgb(n), want, atol=2e-3, rtol=1e-3)
    record("T5 AOV 'ViewLayer.Normal' X/Y/Z mapped by name (not position)", bool(ok))
    record("T5 clip_info from the sequence drives the Writer: <folder>/render.clip",
           os.path.exists(os.path.join(folder, "render.clip")), "")


def test6_issue7(srv: Server, work: str) -> None:
    from pathlib import Path
    base = os.path.join(work, "issue7")
    shutil.rmtree(base, ignore_errors=True)
    case = ff.FlameLike("acc_tc", name="woods walk ", fps="23.976", tc="01:00:00:00", colour="Rec.709 video")
    clip = str(ff.build(case, Path(base, "src")))

    # reader ui output + clip info node
    h = srv.run({"1": reader(clip), "2": {"class_type": "OpenClipClipInfo", "inputs": {"clip_info": ["1", CLIP_INFO]}},
                 "3": preview(["2", CI_START_TIMECODE]), "4": preview(["2", CI_COLOUR_SPACE]),
                 "5": preview(["2", CI_BIT_DEPTH])})
    err = failed(h)
    outs = h.get("outputs", {})
    report = (outs.get("1", {}).get("clip_report") or [""])[0]
    record("T6 Reader's clip_report is in the node's ui output", err is None and report.startswith("CLIP     woods walk ")
           and "start TC 01:00:00:00" in report and "colour: Rec.709 video" in report, err or report.splitlines()[0:4].__repr__()[:200])
    tc_text = (outs.get("3", {}).get("text") or [""])[0]
    colour_text = (outs.get("4", {}).get("text") or [""])[0]
    record("T6 OpenClipClipInfo: start_timecode and colour_space outputs", (tc_text, colour_text) == ("01:00:00:00", "Rec.709 video"),
           f"{tc_text!r} {colour_text!r}")
    depth_text = (outs.get("5", {}).get("text") or [""])[0]
    record("T6 OpenClipClipInfo: bit_depth output", depth_text == "half", repr(depth_text))

    # new clip: timecode, colour, untrimmed name, all fields on auto
    new_dir = os.path.join(base, "new")
    err = failed(srv.run({"1": reader(clip), "2": writer(["1", IMAGE], new_dir, clip_info=["1", CLIP_INFO])}))
    written = os.path.join(new_dir, "woods walk .clip")
    diffs = ["not written"] if not os.path.exists(written) else (
        fc.new_clip_diffs(clip, written, first=1, count=3)
        + fc.exr_frame_diffs(written, "v001", expected_colour="Rec.709 video",
                             source_pattern=fc.source_media_pattern(clip), source_first=1))
    record("T6 new clip keeps start TC 01:00:00:00, colour tag, name, rate, padding (field by field)",
           err is None and not diffs, err or "; ".join(diffs)[:300])

    # append: exactly the source file, v0 -> v1, every Flame element kept
    original = open(clip, "rb").read()
    err = failed(srv.run({"1": reader(clip), "2": writer(["1", IMAGE], clip_info=["1", CLIP_INFO])}))
    diffs = fc.append_diffs(original, clip, ["v1"], source_vuid="v0", first=1, count=3) if err is None else []
    record("T6 append to the trailing-space source: same file, v1 at 01:00:00:00, original untouched",
           err is None and not diffs and not os.path.exists(os.path.join(base, "src", "woods walk.clip")),
           err or "; ".join(diffs)[:300])

    # refusals: different start TC on append; both 00:00:00:00 with another start frame
    before = open(clip, "rb").read()
    err = failed(srv.run({"1": reader(clip), "2": writer(["1", IMAGE], clip_info=["1", CLIP_INFO], start_timecode="02:00:00:00")}))
    record("T6 append with a different start TC is refused, clip unchanged",
           err is not None and "START TIMECODE MISMATCH" in err and open(clip, "rb").read() == before, (err or "no error")[:160])
    zero = str(ff.build(ff.FlameLike("acc_zero", name="zero", fps="24", tc="00:00:00:00"), Path(base, "src0")))
    before = open(zero, "rb").read()
    err = failed(srv.run({"1": reader(zero), "2": writer(["1", IMAGE], clip_info=["1", CLIP_INFO], start_frame=1001)}))
    record("T6 append at 00:00:00:00 with another start frame is refused, clip unchanged",
           err is not None and "START FRAME MISMATCH" in err and open(zero, "rb").read() == before, (err or "no error")[:160])
    err = failed(srv.run({"1": reader(clip), "2": writer(["1", IMAGE], clip_info=["1", CLIP_INFO], start_frame=1001)}))
    feeds = fc.video_track(fc.load(clip)).findall("feeds/feed")
    record("T6 append at the same real TC with another start frame only warns (v2 written)",
           err is None and [f.get("vuid") for f in feeds] == ["v0", "v1", "v2"], err or "")


def main() -> int:
    global fc, ff
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8199")
    ap.add_argument("--work", required=True)
    ap.add_argument("--pack", default=os.path.expanduser("~/oc_accept/base/custom_nodes/ComfyUI_OpenClip"),
                    help="the installed pack under test (its tests/ helpers are used)")
    ap.add_argument("--flame-clip", default="")
    ap.add_argument("--server-log", default="")
    ap.add_argument("--mode", choices=["normal", "noocio"], default="normal")
    ap.add_argument("--json", default="", help="also write the results to this JSON file")
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(a.pack)))
    pkg = os.path.basename(os.path.abspath(a.pack))
    fc = __import__(f"{pkg}.tests.flame_compare", fromlist=["x"])
    ff = __import__(f"{pkg}.tests.flame_fixtures", fromlist=["x"])
    os.makedirs(a.work, exist_ok=True)
    srv = Server(a.url)
    test1_load(srv, a.server_log, a.mode)
    clip, path_from, path_to = prepare_source(a.flame_clip, a.work)
    remap = dict(path_from=path_from, path_to=path_to)
    if a.mode == "noocio":
        test1_noocio(srv, a.flame_clip or clip, a.work, a.pack)
    else:
        test4_round_trip(srv, clip, remap, a.work)
        test4_aov(srv, clip, remap, a.work)
        test5_blender(srv, a.work)
        test6_issue7(srv, a.work)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    if a.json:
        with open(a.json, "w") as f:
            json.dump([{"name": n, "ok": ok, "detail": d} for n, ok, d in RESULTS], f, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
