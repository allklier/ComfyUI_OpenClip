#!/usr/bin/env python
"""Acceptance run for the OpenClip nodes against a *live* ComfyUI server (HTTP API).

Run it on the machine hosting ComfyUI, with the ComfyUI python env (needs OpenImageIO + numpy):

    python run_acceptance.py --url http://127.0.0.1:8199 --work ~/oc_accept/work \
        --flame-clip /path/to/real.clip [--mode normal|noocio]

See acceptance/README.md for how the private server is started. Output is a PASS/FAIL table;
exit code is non-zero if anything failed.

Tests (numbering follows the acceptance checklist in the project notes):
  1  node pack loads in a real server (all nodes registered, no traceback in server log);
     --mode noocio additionally proves the pack loads and reads/writes with OCIO absent and
     that the colour transform nodes fail with a clear error instead of crashing the pack.
  4  Reader -> Writer 'next' twice (v001, v002), cache hit on re-queue, cache invalidation on
     re-render; AOV Writer chain -> Writer -> Reader -> AOV Reader round trip, both layouts.
  5  OpenClipExrSequenceReader on a Blender-style single-part multilayer sequence.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time
import urllib.request

import numpy as np
import OpenImageIO as oiio

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
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
    """Standard Flame layout nests the clip: <clip_path>/<name>/<name>.clip."""
    hits = glob.glob(os.path.join(folder, "**", f"{name}.clip"), recursive=True)
    return hits[0] if hits else os.path.join(folder, f"{name}.clip")


# ---------------------------------------------------------------- node-prompt builders
def reader(clip_path: str, frame_cap: int = -1, version: str = "current", start: int = -1, end: int = -1,
           path_from: str = "", path_to: str = "") -> dict:
    return {"class_type": "OpenClipReader", "inputs": {
        "clip_path": clip_path, "version": version, "start_frame": start, "end_frame": end,
        "path_from": path_from, "path_to": path_to, "frame_cap": frame_cap}}


def writer(image, clip_path, clip_name, version="next", overwrite=False, fmt="EXR", layout="Multichannel", **extra) -> dict:
    inputs = {
        "IMAGE": image, "clip_path": clip_path, "clip_name": clip_name,
        "clip_filename": "$(path)/$(clip_name).clip", "version_name": version, "overwrite": overwrite,
        "include_version_in_filename": False, "fps": 24.0, "start_frame": 1, "frame_padding": 4,
        "file_format": fmt, "exr_bit_depth": "half (16-bit)", "exr_compression": "ZIP",
        "aov_layout": layout, "publish": False}
    inputs.update(extra)
    return {"class_type": "OpenClipWriter", "inputs": inputs}


# ================================================================ tests
def test1_load(srv: Server, server_log: str, mode: str) -> None:
    info = srv.object_info()
    want = {"OpenClipReader", "OpenClipWriter", "OpenClipExrSequenceReader", "OpenClipAOVReader",
            "OpenClipAOVWriter", "OpenClipColourSpaceTransform", "OpenClipColourTransform"}
    missing = want - set(info)
    record(f"T1[{mode}] all 7 nodes registered in running server", not missing, f"missing={sorted(missing)}")
    log = open(server_log, errors="replace").read() if os.path.exists(server_log) else ""
    bad = [l for l in log.splitlines() if "OpenClip" in l and ("Traceback" in l or "IMPORT FAILED" in l or "Cannot import" in l)]
    record(f"T1[{mode}] no OpenClip import failure/traceback in server log", not bad, "; ".join(bad)[:300])


def test1_noocio(srv: Server, flame_clip: str, work: str) -> None:
    h = srv.run({"1": reader(flame_clip, frame_cap=2),
                 "2": writer(["1", 0], os.path.join(work, "noocio_out"), "noocio")})
    err = failed(h)
    record("T1[noocio] Reader->Writer works with OCIO unavailable", err is None, err or "")
    # colour space transform must fail cleanly (error in the run), not take the server down
    p = {"1": reader(flame_clip, frame_cap=1),
         "2": {"class_type": "OpenClipColourSpaceTransform", "inputs": {
             "IMAGE": ["1", 0], "ocio_config": os.path.join(os.path.dirname(os.path.abspath(__file__)), "base", "custom_nodes", "ComfyUI_OpenClip", "tests", "fixtures", "test_ocio.ocio"), "source": "ACEScg (already the working space)",
             "custom_source": "", "target_colour_space": "ACEScg (already the working space)",
             "custom_target_colour_space": ""}},
         "3": writer(["2", 0], os.path.join(work, "noocio_out2"), "noocio2")}
    h = srv.run(p)
    err = failed(h)
    record("T1[noocio] colour transform without OCIO errors cleanly (server survives)",
           err is not None and srv.object_info() is not None, (err or "NO ERROR RAISED")[:200])


def test4_round_trip(srv: Server, flame_clip: str, work: str) -> None:
    src = os.path.join(work, "src")
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(src)
    # a private copy of the real Flame clip + 6 frames of its current version, so we can
    # re-render frames without touching the original
    shutil.copy(flame_clip, os.path.join(src, "src.clip"))
    srcdir = os.path.dirname(flame_clip)
    for f in sorted(glob.glob(os.path.join(srcdir, "to_comfyui.v005.00000[1-6].exr"))):
        shutil.copy(f, src)
    clip = os.path.join(src, "src.clip")
    remap = dict(path_from=srcdir, path_to=src)  # Flame writes absolute media paths
    out = os.path.join(work, "rt")
    shutil.rmtree(out, ignore_errors=True)

    prompt = {"1": reader(clip, frame_cap=6, **remap), "2": writer(["1", 0], out, "rt")}
    prompt["2"]["inputs"]["CLIP_METADATA"] = ["1", 10]
    prompt["2"]["inputs"]["MASK"] = ["1", 1]
    prompt["2"]["inputs"]["colour_space"] = ["1", 12]
    prompt["2"]["inputs"]["fps"] = ["1", 13]
    h1 = srv.run(prompt)
    err = failed(h1)
    record("T4 Flame .clip -> Reader -> Writer(next) #1 executes", err is None, err or "")
    h2 = srv.run(prompt)  # identical prompt: everything should come from cache
    err2 = failed(h2)
    # nothing executed => ComfyUI reports every node as cached (or an empty 'executed' set)
    record("T4 identical re-queue is served from cache", err2 is None and cached_nodes(h2) >= {"1", "2"},
           f"cached={sorted(cached_nodes(h2))}")

    # writer is output node with fresh-run semantics; force a second *execution* of v002 by
    # changing something upstream-neutral (frame_cap 6 -> 5 changes the Reader, not the version rule)
    prompt["1"]["inputs"]["frame_cap"] = 5
    h3 = srv.run(prompt)
    err3 = failed(h3)
    versions_on_disk = sorted({os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(out, "**", "*.exr"), recursive=True)})
    rt_clip = find_clip(out, "rt")
    clip_xml = open(rt_clip, errors="replace").read() if os.path.exists(rt_clip) else ""
    ok = err3 is None and "v001" in clip_xml and "v002" in clip_xml and 'currentVersion="v002"' in clip_xml
    record("T4 second Writer run auto-allocates v002 (v001 kept, currentVersion=v002)", ok,
           f"err={err3} dirs={versions_on_disk} v001={'v001' in clip_xml} v002={'v002' in clip_xml}")

    # a re-render of a source frame must invalidate the Reader's cache
    prompt["1"]["inputs"]["frame_cap"] = 6
    srv.run(prompt)  # prime cache at this exact setting (writes v003, fine)
    hit = srv.run({"1": prompt["1"]})  # Reader alone (OUTPUT_NODE)
    first = sorted(glob.glob(os.path.join(src, "to_comfyui.v005.000001.exr")))[0]
    time.sleep(1.1)
    os.utime(first, None)
    miss = srv.run({"1": prompt["1"]})
    record("T4 Reader cache: hit when untouched, invalidated by re-render of first frame",
           "1" in cached_nodes(hit) and "1" not in cached_nodes(miss),
           f"untouched cached={'1' in cached_nodes(hit)} after-touch cached={'1' in cached_nodes(miss)}")

    # collision handling (explicit existing version without overwrite -> error, with overwrite -> ok)
    p = {"1": reader(clip, frame_cap=3, **remap), "2": writer(["1", 0], out, "rt", version="v001", fps=["1", 13])}
    err = failed(srv.run(p))
    record("T4 explicit existing version without overwrite is refused with the banner message",
           err is not None and "VERSION ALREADY EXISTS" in err, (err or "no error")[:160])
    p["2"]["inputs"]["overwrite"] = True
    err = failed(srv.run(p))
    n = len(glob.glob(os.path.join(out, "**", "v001", "*.exr"), recursive=True))
    record("T4 overwrite=True replaces v001 (3 frames now, stale frames deleted)", err is None and n == 3, f"err={err} frames={n}")


def test4_aov(srv: Server, flame_clip: str, work: str) -> None:
    src = os.path.join(work, "src", "src.clip")
    remap = dict(path_from=os.path.dirname(flame_clip), path_to=os.path.join(work, "src"))
    for layout in ("Multichannel", "Separate Files"):
        tag = layout.split()[0].lower()
        out = os.path.join(work, f"aov_{tag}")
        shutil.rmtree(out, ignore_errors=True)
        p = {
            "1": reader(src, frame_cap=4, **remap),
            "2": {"class_type": "ImageToMask", "inputs": {"image": ["1", 0], "channel": "red"}},
            "3": {"class_type": "OpenClipAOVWriter", "inputs": {"name": "beauty_copy", "image": ["1", 0]}},
            "4": {"class_type": "OpenClipAOVWriter", "inputs": {"name": "red_matte", "matte": ["2", 0], "aov_chain": ["3", 0]}},
            "5": writer(["1", 0], out, "aov", layout=layout, aov_chain=["4", 0]),
        }
        err = failed(srv.run(p))
        record(f"T4 AOV write chain -> Writer ({layout})", err is None, err or "")
        clip = find_clip(out, "aov")
        if layout == "Separate Files":
            # Siblings are written next to the beauty as <stem>_AOV_<name>.####.exr; check them on
            # disk, then fall through to the same Reader -> AOV Reader round trip as Multichannel
            # (the Reader's aov_handle points at the beauty file; siblings are found from it).
            orig = sorted(glob.glob(os.path.join(work, "src", "to_comfyui.v005.00000[1-4].exr")))
            sib_b = sorted(glob.glob(os.path.join(out, "**", "aov_AOV_beauty_copy.*.exr"), recursive=True))
            sib_m = sorted(glob.glob(os.path.join(out, "**", "aov_AOV_red_matte.*.exr"), recursive=True))
            okb = len(sib_b) == 4 and all(np.allclose(rgb(read_exr(a)), rgb(read_exr(b)), rtol=1e-3, atol=1e-3) for a, b in zip(orig, sib_b))
            okm = len(sib_m) == 4 and all(np.allclose(read_exr(a)["R"], read_exr(b)["R"], rtol=1e-3, atol=1e-3) for a, b in zip(orig, sib_m))
            record("T4 Separate Files: sibling AOV files on disk carry the right pixels", okb and okm, f"beauty_copy={len(sib_b)} red_matte={len(sib_m)}")
        # read back: probe + extract each AOV and re-write them as plain beauty to compare
        out2 = os.path.join(work, f"aov_{tag}_back")
        shutil.rmtree(out2, ignore_errors=True)
        q = {
            "1": reader(clip),
            "2": {"class_type": "OpenClipAOVReader", "inputs": {"aov_handle": ["1", 11], "part_name": "beauty_copy"}},
            "3": {"class_type": "OpenClipAOVReader", "inputs": {"aov_handle": ["1", 11], "part_name": "red_matte"}},
            "4": writer(["2", 0], out2, "back_beauty"),
            "5": {"class_type": "MaskToImage", "inputs": {"mask": ["3", 1]}},
            "6": writer(["5", 0], out2, "back_matte"),
            "7": {"class_type": "PreviewAny", "inputs": {"source": ["1", 15]}},
        }
        h = srv.run(q)
        err = failed(h)
        record(f"T4 Reader -> AOV Reader x2 -> Writer ({layout}) executes", err is None, err or "")
        if err:
            continue
        # compare pixels
        orig = sorted(glob.glob(os.path.join(work, "src", "to_comfyui.v005.00000[1-4].exr")))
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
        "2": {"class_type": "OpenClipAOVReader", "inputs": {"aov_handle": ["1", 9], "part_name": "ViewLayer.Normal"}},
        "3": writer(["1", 0], "%s" % out, "beauty", MASK=["1", 1]),
        "4": writer(["2", 0], "%s" % out, "normals"),
        "5": {"class_type": "PreviewAny", "inputs": {"source": ["1", 12]}},
    }
    h = srv.run(p)
    err = failed(h)
    record("T5 EXR Sequence Reader (Blender multilayer, layer=CamelMatte.Combined) + AOV Reader executes", err is None, err or "")
    if err:
        return
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
    clips = glob.glob(os.path.join(out, "**", "*.clip"), recursive=True)
    record("T5 clip_name/folder derived from the sequence usable by Writer", len(clips) >= 1, ",".join(os.path.basename(c) for c in clips))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8199")
    ap.add_argument("--work", required=True)
    ap.add_argument("--flame-clip", required=True)
    ap.add_argument("--server-log", default="")
    ap.add_argument("--mode", choices=["normal", "noocio"], default="normal")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    srv = Server(a.url)
    test1_load(srv, a.server_log, a.mode)
    if a.mode == "noocio":
        test1_noocio(srv, a.flame_clip, a.work)
    else:
        test4_round_trip(srv, a.flame_clip, a.work)
        test4_aov(srv, a.flame_clip, a.work)
        test5_blender(srv, a.work)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
