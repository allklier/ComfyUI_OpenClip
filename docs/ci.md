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
- Tests that need Autodesk's example clips (gitignored, not redistributable)
  are skipped on CI.
- Not covered: a live ComfyUI server, the frontend JS, Flame itself. Those
  are the manual "Acceptance" and "Flame" lines in the release notes.

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

After testing by hand (acceptance run on a live ComfyUI, Flame): Actions →
**promote** → Run workflow, `tag` = the release, optional `verification` =
one line to add, e.g. `Acceptance (live ComfyUI): ✔ akflame5, 2026-11-02`.
It clears the pre-release flag, adds the line to the Verification list and
sets `**Status: FULLY TESTED**`. It refuses releases whose notes have no
Verification block written by `release.yml` (older, hand-written releases).
The "– not run" lines stay as they are; the added line records what was done.

## What is verified, and what isn't

| Part | Proven how |
|---|---|
| Tests and PR-mode security scan | ran green on the PR that added CI |
| Release classification and notes | `.github/scripts/test_release_plan.py` (runs in `tests.yml`), including the repo's real tag list |
| All workflows | `actionlint` 1.7.12 and `zizmor` 1.30.1 (`--offline`), the scanner's pinned versions. zizmor reports two low "self-repository" notes in `release.yml`; kept on purpose (comment in the file: the pinned actionlint rejects the suggested `$/` syntax) |
| Dry run, tag push, publish, promote, weekly schedule | not run yet. Manual runs (dry run, promote, scan) and the schedule only work once the workflow file is on `master`, the default branch; a tag push uses the workflow at the tagged commit, and no tag was pushed while building this. First real use is the first release after merge |

Not decided by CI: whether GitHub moves the "Latest" badge when a
pre-release is promoted (not checked).
