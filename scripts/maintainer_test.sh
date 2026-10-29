#!/usr/bin/env bash
# Full-tier test of one git ref on the ComfyUI test machine; writes a result file for promotion
# (tests/CLAUDE.md "Tiers", docs/ci.md "Maintainer run").
#
#   scripts/maintainer_test.sh <git-ref> [--host <ssh host>] [--out <dir>]
#       From a checkout. Exports <git-ref> with `git archive`. With --host, copies it there and
#       runs this same script (the copy inside the archive) over ssh, then fetches the result file.
#       Without --host, runs on this machine.
#   scripts/maintainer_test.sh --archive <pack.tar> --ref <name> --commit <sha> [--out <dir>]
#       What --host runs on the remote side (also usable by hand on the test machine).
#
# On the test machine (nothing outside $OC_ACCEPT is written; the shared ComfyUI is only read):
#   1. installs the ref into $OC_ACCEPT/base/custom_nodes/ComfyUI_OpenClip (the private base) and
#      unpacks a second copy for pytest in $OC_ACCEPT/mt/src/ComfyUI_OpenClip;
#   2. pytest --tier full: unit + synthetic + real-clip tests. Real clips: $OC_FLAME_CLIPS_DIR
#      (default $OC_ACCEPT/flame_clips, linked as tests/fixtures/flame), Autodesk's examples
#      ($AUTODESK_EXAMPLES, default /opt/Autodesk/openclip_examples, linked when present) and
#      $OPENCLIP_FLAME_CLIPS (passed through);
#   3. starts the private ComfyUI (acceptance/start_private_comfyui.sh: port 8199, --cpu), runs
#      acceptance/run_acceptance.py (--flame-clip "$FLAME_CLIP" if set, else a synthetic clip),
#      stops it, then the same with PyOpenColorIO blocked (noocio);
#   4. writes openclip-test-<ref>.json: ref, commit, version, per-tier counts, PASS/FAIL. No host
#      or user names.
# Environment: OC_ACCEPT (default ~/oc_accept), COMFY_PY (default the ComfyUI env's python),
# PYTEST_PKGS (default $OC_ACCEPT/pytest_pkgs, put on PYTHONPATH when pytest isn't in COMFY_PY),
# FLAME_CLIP, OC_FLAME_CLIPS_DIR, AUTODESK_EXAMPLES, OPENCLIP_FLAME_CLIPS (forwarded by --host).
set -uo pipefail
die() { echo "maintainer_test: $*" >&2; exit 2; }
usage() { sed -n '2,30p' "$0" >&2; exit 2; }

REF= HOST= OUT=$PWD ARCHIVE= COMMIT=
while [ $# -gt 0 ]; do
  case $1 in
    --host) HOST=$2; shift 2 ;; --out) OUT=$2; shift 2 ;;
    --archive) ARCHIVE=$2; shift 2 ;; --ref) REF=$2; shift 2 ;; --commit) COMMIT=$2; shift 2 ;;
    -h|--help) usage ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$REF" ] || die "one ref only"; REF=$1; shift ;;
  esac
done
[ -n "$REF" ] || usage
SAFE_REF=$(printf '%s' "$REF" | tr -c 'A-Za-z0-9._-' '-')
RESULT_NAME="openclip-test-$SAFE_REF.json"
mkdir -p "$OUT" && OUT="$(cd "$OUT" && pwd)" || die "can't create $OUT"

if [ -z "$ARCHIVE" ]; then
  SRC="$(cd "$(dirname "$0")/.." && pwd)"
  # Full SHA: promote.yml compares it with the tag's commit (docs/ci.md "Gating promote").
  COMMIT=$(git -C "$SRC" rev-parse "$REF^{commit}") || die "unknown ref $REF"
  TMP=$(mktemp -d)
  git -C "$SRC" archive --format=tar --prefix=ComfyUI_OpenClip/ "$REF" >"$TMP/pack.tar" || die "git archive failed"
  if [ -n "$HOST" ]; then
    trap 'rm -rf "$TMP"' EXIT
    echo "== $REF ($COMMIT) -> $HOST"
    ssh "$HOST" 'mkdir -p "${OC_ACCEPT:-$HOME/oc_accept}/mt"' || die "ssh $HOST failed"
    scp -q "$TMP/pack.tar" "$HOST:oc_accept/mt/pack.tar" || die "scp failed"
    FWD=""
    for v in OC_ACCEPT COMFY_PY PYTEST_PKGS FLAME_CLIP OC_FLAME_CLIPS_DIR AUTODESK_EXAMPLES OPENCLIP_FLAME_CLIPS; do
      [ -n "${!v:-}" ] && FWD+="$v=$(printf '%q' "${!v}") "
    done
    # The script inside the archive runs, so the remote side is the version under test.
    ssh "$HOST" "cd ~/oc_accept/mt && tar -xOf pack.tar ComfyUI_OpenClip/scripts/maintainer_test.sh >maintainer_test.sh &&
      ${FWD}bash maintainer_test.sh --archive ~/oc_accept/mt/pack.tar --ref $(printf '%q' "$REF") --commit $COMMIT --out ~/oc_accept/mt"
    RC=$?
    scp -q "$HOST:oc_accept/mt/$RESULT_NAME" "$OUT/" || die "no result file on $HOST (exit $RC)"
    echo "result: $OUT/$RESULT_NAME"
    exit $RC
  fi
  ARCHIVE="$TMP/pack.tar"
fi

# ------------------------------------------------------------------ on the test machine
OC_ACCEPT=${OC_ACCEPT:-$HOME/oc_accept}
COMFY_PY=${COMFY_PY:-/home/aiuser/opt2/anaconda3/envs/comfyui/bin/python}
PYTEST_PKGS=${PYTEST_PKGS:-$OC_ACCEPT/pytest_pkgs}
OC_FLAME_CLIPS_DIR=${OC_FLAME_CLIPS_DIR:-$OC_ACCEPT/flame_clips}
AUTODESK_EXAMPLES=${AUTODESK_EXAMPLES:-/opt/Autodesk/openclip_examples}
PORT=8199
MT="$OC_ACCEPT/mt"
LOGS="$MT/logs"
PACK="$OC_ACCEPT/base/custom_nodes/ComfyUI_OpenClip"
[ -x "$COMFY_PY" ] || die "no python at $COMFY_PY (set COMFY_PY)"
curl -s -o /dev/null "http://127.0.0.1:$PORT/" && die "something already listens on port $PORT"

echo "== installing $REF ($COMMIT)"
rm -rf "$MT/src" "$LOGS" && mkdir -p "$MT/src" "$LOGS" "$OC_ACCEPT/base/custom_nodes" || die "can't prepare $MT"
tar -xf "$ARCHIVE" -C "$MT/src" || die "can't unpack $ARCHIVE"
rm -rf "$PACK" && cp -a "$MT/src/ComfyUI_OpenClip" "$PACK" || die "can't install into $PACK"
TESTS="$MT/src/ComfyUI_OpenClip"
[ -d "$OC_FLAME_CLIPS_DIR" ] && ln -s "$OC_FLAME_CLIPS_DIR" "$TESTS/tests/fixtures/flame"
[ -d "$AUTODESK_EXAMPLES" ] && ln -s "$AUTODESK_EXAMPLES" "$TESTS/tests/fixtures/autodesk_examples"

PP=""
"$COMFY_PY" -c "import pytest" 2>/dev/null || PP=$PYTEST_PKGS
PYTHONPATH="$PP" "$COMFY_PY" -c "import pytest" 2>/dev/null ||
  die "pytest not importable; e.g. $COMFY_PY -m pip install --target $PYTEST_PKGS pytest"

echo "== pytest --tier full"
(cd "$TESTS" && PYTHONPATH="$PP" "$COMFY_PY" -m pytest -q -ra --tier full -p no:cacheprovider \
   --junitxml="$LOGS/junit-full.xml" >"$LOGS/pytest-full.log" 2>&1)
PYTEST_RC=$?
tail -3 "$LOGS/pytest-full.log"

SERVER=
stop_server() {
  [ -n "$SERVER" ] && kill "$SERVER" 2>/dev/null && wait "$SERVER" 2>/dev/null
  SERVER=
}
trap 'stop_server; [ -n "${TMP:-}" ] && rm -rf "$TMP"' EXIT
run_acceptance() {  # <normal|noocio>
  local mode=$1 log="$OC_ACCEPT/server.log"
  [ "$mode" = noocio ] && log="$OC_ACCEPT/server_noocio.log"
  echo "== acceptance ($mode)"
  if [ "$mode" = noocio ]; then bash "$PACK/acceptance/start_private_comfyui.sh" noocio &
  else bash "$PACK/acceptance/start_private_comfyui.sh" & fi
  SERVER=$!
  for _ in $(seq 180); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/object_info" && break
    kill -0 "$SERVER" 2>/dev/null || break
    sleep 1
  done
  rm -rf "$OC_ACCEPT/work_mt_$mode"
  "$COMFY_PY" "$PACK/acceptance/run_acceptance.py" --url "http://127.0.0.1:$PORT" --work "$OC_ACCEPT/work_mt_$mode" \
    --pack "$PACK" --server-log "$log" --mode "$mode" --json "$LOGS/acceptance-$mode.json" \
    ${FLAME_CLIP:+--flame-clip "$FLAME_CLIP"} >"$LOGS/acceptance-$mode.log" 2>&1
  local rc=$?
  tail -1 "$LOGS/acceptance-$mode.log"
  stop_server
  cp "$log" "$LOGS/" 2>/dev/null
  return $rc
}
run_acceptance normal; ACC_RC=$?
run_acceptance noocio; NOOCIO_RC=$?

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$TESTS/pyproject.toml")
LOGS="$LOGS" REF="$REF" COMMIT="$COMMIT" VERSION="$VERSION" PYTEST_RC=$PYTEST_RC ACC_RC=$ACC_RC NOOCIO_RC=$NOOCIO_RC \
FLAME_CLIP_SET=${FLAME_CLIP:+1} RESULT="$OUT/$RESULT_NAME" "$COMFY_PY" -I - <<'EOF'
import datetime, json, os, platform, xml.etree.ElementTree as ET
e = os.environ

def junit(path):
    r = {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "failed_tests": [],
         "real_clip_tests": {"passed": 0, "skipped": 0, "xfailed": 0, "failed": 0}}
    try:
        cases = list(ET.parse(path).getroot().iter("testcase"))
    except (OSError, ET.ParseError) as x:
        r["error"] = "no readable junit file (%s)" % type(x).__name__
        return r
    for c in cases:
        name = "%s::%s" % (c.get("classname"), c.get("name"))
        sk = c.find("skipped")
        if c.find("failure") is not None or c.find("error") is not None:
            kind = "failed"; r["failed_tests"].append(name)
        elif sk is not None:
            kind = "xfailed" if sk.get("type") == "pytest.xfail" else "skipped"
        else:
            kind = "passed"
        r[kind] += 1
        if "test_flame_clips" in name or "autodesk" in name or "real_flame_clip" in name:
            r["real_clip_tests"][kind] += 1
    return r

def acceptance(mode, rc):
    try:
        rows = json.load(open(os.path.join(e["LOGS"], "acceptance-%s.json" % mode)))
    except (OSError, ValueError) as x:
        return {"passed": 0, "failed": 0, "exit_code": rc, "error": "no results (%s)" % type(x).__name__}
    bad = [r["name"] for r in rows if not r["ok"]]
    return {"passed": len(rows) - len(bad), "failed": len(bad), "failed_tests": bad, "exit_code": rc}

py = junit(os.path.join(e["LOGS"], "junit-full.xml")); py["exit_code"] = int(e["PYTEST_RC"])
res = {"pytest_full": py, "acceptance_normal": acceptance("normal", int(e["ACC_RC"])),
       "acceptance_noocio": acceptance("noocio", int(e["NOOCIO_RC"]))}
ok = all(r["exit_code"] == 0 and r["failed"] == 0 and r["passed"] > 0 and "error" not in r for r in res.values())
import OpenImageIO, sys
doc = {
    "schema": 1, "ref": e["REF"], "commit": e["COMMIT"], "version": e["VERSION"], "tier": "full",
    "results": res,
    "acceptance_source": "real Flame clip" if e.get("FLAME_CLIP_SET") else "synthetic Flame-like clip",
    "environment": {"os": platform.platform(terse=True), "python": sys.version.split()[0],
                    "openimageio": OpenImageIO.__version__},
    "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "overall": "PASS" if ok else "FAIL",
}
with open(e["RESULT"], "w") as f:
    json.dump(doc, f, indent=2); f.write("\n")
print("%s: %s (pytest %d passed/%d failed/%d skipped/%d xfailed, real-clip tests %d passed; "
      "acceptance %d/%d, noocio %d/%d)" % (e["RESULT"], doc["overall"], py["passed"], py["failed"], py["skipped"],
      py["xfailed"], py["real_clip_tests"]["passed"], res["acceptance_normal"]["passed"],
      res["acceptance_normal"]["passed"] + res["acceptance_normal"]["failed"], res["acceptance_noocio"]["passed"],
      res["acceptance_noocio"]["passed"] + res["acceptance_noocio"]["failed"]))
EOF
echo "logs: $LOGS"
grep -q '"overall": "PASS"' "$OUT/$RESULT_NAME" 2>/dev/null || exit 1
exit 0
