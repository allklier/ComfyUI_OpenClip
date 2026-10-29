# CI and releases

This repo has four GitHub Actions workflows in `.github/workflows/`. They are
not part of the node pack (`.comfyignore` keeps `.github/` out of the
registry package).

| Workflow | Runs on | What it does | Can fail the PR / release |
|---|---|---|---|
| `tests.yml` | every pull request, every branch push, called by `release.yml` | `pytest` on Python 3.10 and 3.13 (Linux), plus the tests of the release script | yes |
| `security-scan.yml` | every pull request; weekly (Mon 05:17 UTC); by hand; called by `release.yml` | the shared scanner from [allklier/security-scans](https://github.com/allklier/security-scans) | PR: only a **new** finding at high or above, or a tool error. Weekly/manual/release: only a tool error |
| `release.yml` | push of a tag `vX.Y.Z`; by hand = **dry run** | checks the tag, runs tests + scan, creates the GitHub release | yes |
| `promote.yml` | by hand only | turns a release into a normal, FULLY TESTED one | — |

Every workflow starts with `permissions: contents: read`. Only the job that
creates or edits a release gets `contents: write`. Third-party actions are
pinned by full commit SHA (the tag is in a comment); checkouts don't keep
the token (`persist-credentials: false`). Nothing uses `pull_request_target`,
and input values (tag names, the tag message, the promote text) reach shell
scripts only through environment variables.

## Tests (`tests.yml`)

- Installs from PyPI: `requirements.txt` (OpenImageIO, OpenColorIO, lxml),
  numpy, pytest, and CPU-only torch from PyTorch's CPU index (keeps the
  install small; no GPU on the runner anyway). Versions are not pinned, so CI
  also catches a new upstream release that breaks us. `pip list` in the log
  shows what was actually installed.
- Python 3.10 is the floor in `pyproject.toml`; 3.13 is the newest Python
  ComfyUI recommends. Local development uses 3.12.
- No ComfyUI is installed: the tests call the node classes directly
  (`tests/CLAUDE.md`). The checkout goes into a folder named
  `ComfyUI_OpenClip` because the tests import the repo under that package name.
- CI runs the **smoke tier** (`pytest` with no options = `--tier smoke`): unit
  tests plus the synthetic Flame-like boundary cases (`tests/test_flame_like.py`).
  Tests marked `flame_clips` (real Flame/Autodesk clips, gitignored, not
  redistributable) are skipped with "full tier only"; with `--tier full` they
  also skip per clip when the clips are absent, so the workflow needs no change.
- Not covered on CI: the full tier, a live ComfyUI server, the frontend JS, Flame
  itself. See "Test tiers and the maintainer run" below.

## Security scan (`security-scan.yml`)

Calls `scan.yml` in `allklier/security-scans`, pinned by commit SHA (the
same SHA in `uses:` and `scanner-ref`; to update the scanner, bump both).
Accepted risks live in `.security-scans.toml` in this repo, each with a reason
and an expiry date. Reports are kept as a workflow artifact
(`security-scan-pr` / `security-scan-full`, 30 days) and shown in the job
summary. No upload to GitHub code scanning (the scanner offers it as an
optional extra job; left out to keep this minimal).

## Release policy C (`release.yml`)

Push a tag `vX.Y.Z` and the workflow:

1. **plan** — fails unless the tag is exactly `v` + the `version` in
   `pyproject.toml` at that tag. Finds the previous tag (the highest
   `vX.Y.Z` tag below this one; older odd tags such as `v0.95` or `v.09`
   are ignored) and classifies:

   | Bump vs previous tag | Release type |
   |---|---|
   | patch (`1.1.0` → `1.1.1`) | normal release |
   | patch with `[pre-release]` in the annotated tag message | pre-release |
   | minor (`1.1.x` → `1.2.0`) or major (`1.x` → `2.0.0`) | pre-release, always |
   | no earlier `vX.Y.Z` tag | pre-release |

   The override only goes one way: a patch can be held back as a
   pre-release, but nothing makes a minor or major bump a normal release
   straight away. That only happens through **promote**, after it has been
   checked by hand.
2. **tests** and **security-scan** — the two workflows above, run against the tag.
3. **publish** — only if all of the above passed: creates the GitHub release
   with `gh release create --verify-tag`.

Tags like `v1.2.0-rc1` don't match the strict format and fail in plan.

### Release notes

- **Title**: `<tag>: <first line of the tag message>`, or just the tag for a
  lightweight tag.
- **Verification** block, at the top:

  ```
  ### Verification
  - CI (unit tests, security scan): ✔ <link to the release run>
  - Acceptance (live ComfyUI): – not run
  - Flame 2027: – not tested

  **Status: LIGHTLY TESTED**
  ```

  The ✔ is written only because the release is created after tests and
  scan passed. The scan in a release runs in full mode, which fails only on a
  tool error; open findings are in that run's summary, they don't block.
- **Changes**: the rest of the annotated tag message (after the first line;
  the `[pre-release]` marker and any signature removed). For a lightweight
  tag, or an empty message, the commit subjects since the previous tag
  instead. Then a "Full changelog" compare link.

### How to cut a release

1. On `master`, set `version` in `pyproject.toml` (e.g. `1.1.1`), commit, push.
2. Optional dry run: Actions → **release** → Run workflow, `tag` = `v1.1.1`,
   and paste the tag message you plan to use into `tag-message`. The log and
   the job summary show the classification and the notes; nothing is
   created. (If the tag already exists on GitHub, its real message and its
   `pyproject.toml` are used instead.)
3. Tag and push. Use an annotated tag; its message is the changelog:

   ```sh
   git tag -a v1.1.1 -m "Security fixes" -m "- Writer checks clip names
   - lxml >= 6.1"
   # to hold a patch back as a pre-release, put [pre-release] anywhere in the message
   git push origin v1.1.1
   ```

4. Watch Actions → **release**. If it fails, nothing was created: fix,
   delete the tag (`git push origin :refs/tags/v1.1.1`, `git tag -d v1.1.1`), tag again.
5. The ComfyUI registry is separate: run `comfy node publish` yourself.

### Promote (`promote.yml`)

After testing by hand: Actions → **promote** → Run workflow, `tag` = the
release, optional `verification` = one line to add for the Flame check
(`docs/FLAME_TEST.md`), e.g. `Flame 2027: ✔ 2026-11-02`. It first runs the
**gate** (below): a minor or major release needs the maintainer run's result
file on the release, and the line about that run is taken from the file. Then it
clears the pre-release flag, adds the line(s) to the Verification list and sets
`**Status: FULLY TESTED**`. It refuses releases whose notes have no
Verification block written by `release.yml` (older, hand-written releases).
The "– not run" lines stay as they are; the added lines record what was done.

## Test tiers and the maintainer run

| Tier | What | Where |
|---|---|---|
| smoke | unit tests + synthetic Flame-like cases | `tests.yml`, every push/PR/release (≈4 s) |
| full | smoke + real-clip fidelity (`--tier full`) + live ComfyUI acceptance, normal and without OCIO | `scripts/maintainer_test.sh`, on the ComfyUI test machine |
| manual | 5 steps in Flame | `docs/FLAME_TEST.md` |

Details and the reasons: `tests/CLAUDE.md` "Tiers".

### Maintainer run

```sh
# from a checkout on the development machine; runs on the test host over ssh
FLAME_CLIP=<a real Flame .clip on that host> \
OPENCLIP_FLAME_CLIPS=<more .clip files or folders there, ':'-separated> \
scripts/maintainer_test.sh v2.0.0 --host <test host>
# or on the test host itself, from a checkout there
scripts/maintainer_test.sh v2.0.0
```

It exports the ref with `git archive`, installs it into the private ComfyUI base
(`~/oc_accept/base/custom_nodes/ComfyUI_OpenClip`; the shared ComfyUI install is only read),
runs `pytest --tier full`, then starts the private ComfyUI (`acceptance/start_private_comfyui.sh`,
port 8199, `--cpu`) and runs `acceptance/run_acceptance.py` normally and with OCIO blocked, and
writes `openclip-test-<ref>.json` (ref, commit (full SHA), version, per-tier counts incl. how many
real-clip tests ran, `overall` PASS/FAIL; no host or user names). For a release, run it with the
tag as `<ref>` and upload the file to the release (see "Gating promote" below). With `--host` the file is
copied back to `--out` (default: the current folder). Logs stay in `~/oc_accept/mt/logs/` on
the test host. Real clips there: `~/oc_accept/flame_clips/` (the planned set, `tests/flame_clips.md`),
`/opt/Autodesk/openclip_examples`, and `$OPENCLIP_FLAME_CLIPS`.

### Gating promote on the result file

Mirrors LTX Alpha Gen's `promote.sh`; the checks are `release_plan.py gate`
(tested in `.github/scripts/test_release_plan.py`), `promote.yml` only fetches
the inputs.

1. Run the maintainer test **on the tag itself** (`scripts/maintainer_test.sh v2.0.0 --host <test host>`),
   then attach the result to the release:

   ```sh
   gh release upload v2.0.0 openclip-test-v2.0.0.json
   ```

2. `promote.yml` downloads `openclip-test-<tag>.json` from the release assets and the gate
   refuses unless `ref` = the tag, `commit` = the tag's commit (the script writes the full SHA;
   an older file's short SHA of at least 7 characters is accepted as a prefix), `tier` = `full`,
   `overall` = `PASS`, and `results.pytest_full.real_clip_tests.passed` > 0 (so a run without
   any real clip can't promote). A missing or unreadable file refuses too, with the upload
   command in the message.
3. The Verification line about that run comes from the file:
   `Full tier + live ComfyUI (maintainer run <timestamp>, <commit>): ✔ pytest <n> passed (real Flame clips <n>), acceptance <n>/<n>, without OCIO <n>/<n>`.
   The free-text `verification` input stays for the Flame line, which no machine can produce.

**Exception: patch releases** (only Z of X.Y.Z changed against the previous vX.Y.Z tag, the
same classification `release.yml` uses, `[pre-release]` marker or not) are exempt and promote
as before, with no result file. The first release (no earlier tag) is not a patch and is gated.

The tag's commit is resolved through the same GitHub API calls `release.yml` already uses
(`git/ref/tags/<tag>`, and `git/tags/<sha>` → `.object.sha` for an annotated tag; checked
read-only against `v1.1.1` on 2026-10-29: it gives `caae91f`, the same as
`git rev-parse v1.1.1^{commit}`). `gh release download <tag> --pattern <file> --dir <dir>`
per `gh release download --help` (gh 2.45). No new action, no new permission
(the job already has `contents: write` to edit the release; reading assets needs only read).

## What is verified, and what isn't

| Part | Proven how |
|---|---|
| Tests and PR-mode security scan | ran green on the PR that added CI |
| Release classification and notes | `.github/scripts/test_release_plan.py` (runs in `tests.yml`), including the repo's real tag list |
| All workflows | `actionlint` 1.7.12 and `zizmor` 1.30.1 (`--offline`), the scanner's pinned versions. zizmor reports two low "self-repository" notes in `release.yml`; kept on purpose (comment in the file: the pinned actionlint rejects the suggested `$/` syntax) |
| Dry run, manual full scan | ran green on `master` after the merge (2026-10-28): the v1.1.1 dry run classified it as a patch (normal release) and printed the notes; the full scan found 0 high, 3 accepted risks, 2 low notes |
| Tag push, tests + scan from the release, publish | v1.1.1 (2026-10-28): the release run passed tests and scan through the two-level call (`release.yml` → `security-scan.yml` → the scanner) and created the release with the multi-line notes intact |
| Promote gate | `release_plan.py gate` unit tests (patch exempt; minor/major without, with a wrong and with a good result file); `actionlint` 1.7.12 and `zizmor` 1.30.1 on `promote.yml` (no new findings). The workflow itself has not run on GitHub |
| Promote, weekly schedule | not run yet. The schedule first fires on a Monday at 05:17 UTC; promote is first used when a release is tested in Flame |

Not decided by CI: whether GitHub moves the "Latest" badge when a
pre-release is promoted (not checked).
