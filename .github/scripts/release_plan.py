"""Release policy C for this repo: classify a tag and write the release notes.

Used by .github/workflows/release.yml (plan) and promote.yml (promote);
tested by test_release_plan.py next to it. Standard library only.
Rules and how to cut a release: docs/ci.md.

  plan:    tag must be vX.Y.Z and equal pyproject.toml's version; compared with
           the highest earlier vX.Y.Z tag, a patch bump is a normal release,
           a minor/major bump (or the first release) is a pre-release. A
           "[pre-release]" marker in the annotated tag message turns a patch
           into a pre-release; nothing turns a minor/major into a normal one.
  gate:    before promote: a patch release passes as it is; a minor/major
           release needs the maintainer run's result file
           (openclip-test-<tag>.json, uploaded to the release) for exactly
           this tag and commit, full tier, PASS, real-clip tests > 0. Writes
           the verification line taken from that file.
  promote: rewrite the notes of an existing release to "FULLY TESTED",
           adding the given verification lines.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
MARKER_RE = re.compile(r"\[pre-release\]", re.IGNORECASE)
START = "<!-- verification:start -->"
END = "<!-- verification:end -->"
STATUS_RE = re.compile(r"^\*\*Status: .*\*\*$", re.MULTILINE)


class ReleaseError(Exception):
    """The tag can't be released as it is; the message says why."""


@dataclass
class Plan:
    tag: str
    previous: str | None
    kind: str  # "patch", "minor", "major" or "initial"
    prerelease: bool
    reasons: list[str] = field(default_factory=list)


def parse_tag(tag: str) -> tuple[int, int, int] | None:
    m = TAG_RE.match(tag)
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def previous_tag(tag: str, tags: list[str]) -> str | None:
    """Highest vX.Y.Z tag below `tag`; tags of any other shape are ignored."""
    current = parse_tag(tag)
    lower = [(v, t) for t in tags if (v := parse_tag(t)) is not None and v < current]
    return max(lower)[1] if lower else None


def split_message(message: str) -> tuple[str, str, bool]:
    """Annotated tag message -> (subject, body, has_pre_release_marker).

    A PGP/SSH signature block is dropped; the marker is removed wherever it
    appears, and a line left empty by that is dropped.
    """
    text = re.split(r"^-----BEGIN (?:PGP|SSH) SIGNATURE-----", message, flags=re.MULTILINE)[0]
    marker = bool(MARKER_RE.search(text))
    lines = []
    for line in text.splitlines():
        cleaned = MARKER_RE.sub("", line).rstrip()
        if cleaned.strip() or not MARKER_RE.search(line):
            lines.append(cleaned)
    text = "\n".join(lines).strip()
    subject, _, body = text.partition("\n")
    return subject.strip(), body.strip(), marker


def bump_kind(tag: str, tags: list[str]) -> tuple[str, str | None]:
    """-> (kind, previous tag): "patch", "minor", "major", or "initial" when there is no
    earlier vX.Y.Z tag."""
    current = parse_tag(tag)
    if current is None:
        raise ReleaseError(f"tag {tag!r} is not vMAJOR.MINOR.PATCH (e.g. v1.2.3)")
    prev = previous_tag(tag, tags)
    if prev is None:
        return "initial", None
    p = parse_tag(prev)
    if current[0] != p[0]:
        return "major", prev
    if current[1] != p[1]:
        return "minor", prev
    return "patch", prev


def classify(tag: str, version: str, tags: list[str], message: str = "") -> Plan:
    if parse_tag(tag) is None:
        raise ReleaseError(f"tag {tag!r} is not vMAJOR.MINOR.PATCH (e.g. v1.2.3)")
    if tag != f"v{version}":
        raise ReleaseError(f"tag {tag} does not match pyproject.toml version {version!r} (expected v{version})")
    kind, prev = bump_kind(tag, tags)
    _, _, marker = split_message(message)
    if prev is None:
        return Plan(tag, None, "initial", True, ["no earlier vX.Y.Z tag: first release is a pre-release"])
    if kind == "patch":
        if marker:
            return Plan(tag, prev, kind, True, [f"patch bump from {prev}", "[pre-release] in the tag message"])
        return Plan(tag, prev, kind, False, [f"patch bump from {prev}"])
    return Plan(tag, prev, kind, True, [f"{kind} bump from {prev}: always a pre-release until promoted"])


def render_notes(plan: Plan, message: str, commits: list[str], ci_line: str, compare_url: str | None) -> tuple[str, str]:
    """-> (title, markdown body). Changelog = tag message body, else commit subjects."""
    subject, body, _ = split_message(message)
    title = f"{plan.tag}: {subject}" if subject else plan.tag
    if body:
        changelog = body
    elif commits:
        changelog = "\n".join(f"- {c}" for c in commits)
    else:
        changelog = "(no changelog)"
    parts = [
        START,
        "### Verification",
        "",
        f"- CI (unit tests, security scan): {ci_line}",
        "- Acceptance (live ComfyUI): – not run",
        "- Flame 2027: – not tested",
        "",
        "**Status: LIGHTLY TESTED**",
        END,
        "",
        "### Changes",
        "",
        changelog,
    ]
    if compare_url:
        parts += ["", f"**Full changelog**: {compare_url}"]
    return title, "\n".join(parts) + "\n"


def result_file_name(tag: str) -> str:
    """What scripts/maintainer_test.sh writes for a run of `tag` (a vX.Y.Z tag needs no escaping)."""
    return f"openclip-test-{tag}.json"


def gate(tag: str, tags: list[str], tag_commit: str, result: dict | None) -> str:
    """Check that `tag` may be promoted; -> the verification line from the result file ("" for
    a patch). A patch release (only Z changed vs the previous vX.Y.Z tag) is exempt and promotes
    as before. Anything else needs the maintainer run's result file for exactly this tag and
    commit: tier full, overall PASS, at least one real-clip test passed."""
    kind, prev = bump_kind(tag, tags)
    if kind == "patch":
        return ""
    name = result_file_name(tag)
    what = f"{kind} release {tag}" + (f" (previous {prev})" if prev else "")
    if result is None:
        raise ReleaseError(f"{what} needs the maintainer run's result: run scripts/maintainer_test.sh {tag}, "
                           f"then gh release upload {tag} {name}")
    if not isinstance(result, dict):
        raise ReleaseError(f"{name}: not a JSON object")

    def get(*keys):
        value = result
        for k in keys:
            if not isinstance(value, dict) or k not in value:
                raise ReleaseError(f"{name}: missing {'.'.join(keys)}")
            value = value[k]
        return value

    problems = []
    if get("ref") != tag:
        problems.append(f"ref is {get('ref')!r}, not {tag!r} (run the maintainer test on the tag itself)")
    commit = get("commit")
    if not (isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{7,40}", commit)
            and tag_commit.lower().startswith(commit)):
        problems.append(f"commit is {commit!r}, the tag's commit is {tag_commit}")
    if get("tier") != "full":
        problems.append(f"tier is {get('tier')!r}, not 'full'")
    if get("overall") != "PASS":
        problems.append(f"overall is {get('overall')!r}, not 'PASS'")
    real = get("results", "pytest_full", "real_clip_tests", "passed")
    if not (isinstance(real, int) and real > 0):
        problems.append(f"results.pytest_full.real_clip_tests.passed is {real!r}: no real Flame clip was tested")
    if problems:
        raise ReleaseError(f"{name} does not allow promoting {tag}: " + "; ".join(problems))

    def count(*keys):
        value = get(*keys)
        return value if isinstance(value, int) else 0

    py = ("results", "pytest_full")
    acc, noocio = ("results", "acceptance_normal"), ("results", "acceptance_noocio")
    return (f"Full tier + live ComfyUI (maintainer run {result.get('timestamp', '?')}, {commit[:7]}): ✔ "
            f"pytest {count(*py, 'passed')} passed (real Flame clips {real}), "
            f"acceptance {count(*acc, 'passed')}/{count(*acc, 'passed') + count(*acc, 'failed')}, "
            f"without OCIO {count(*noocio, 'passed')}/{count(*noocio, 'passed') + count(*noocio, 'failed')}")


def promote(body: str, *verifications: str) -> str:
    """Set the Status line to FULLY TESTED; add each non-empty verification as a bullet."""
    if START not in body or END not in body or not STATUS_RE.search(body):
        raise ReleaseError("release notes have no verification block written by release.yml")
    head, rest = body.split(START, 1)
    block, tail = rest.split(END, 1)
    for verification in verifications:
        verification = " ".join(verification.split())  # one line, no markup tricks across lines
        if not verification:
            continue
        lines = block.split("\n")
        last_bullet = max(i for i, line in enumerate(lines) if line.startswith("- "))
        lines.insert(last_bullet + 1, f"- {verification}")
        block = "\n".join(lines)
    block = STATUS_RE.sub("**Status: FULLY TESTED**", block)
    return head + START + block + END + tail


def _read(path: str | None) -> str:
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _set_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    delim = f"EOF_{os.urandom(8).hex()}"
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{name}<<{delim}\n{value}\n{delim}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--tag", required=True)
    p.add_argument("--version", required=True, help="pyproject.toml [project].version")
    p.add_argument("--tags-file", required=True, help="all tag names, one per line")
    p.add_argument("--message-file", help="annotated tag message (empty/absent for a lightweight tag)")
    p.add_argument("--commits-file", help="commit subjects since the previous tag, one per line")
    p.add_argument("--ci-line", required=True)
    p.add_argument("--repo-url", required=True, help="https://github.com/<owner>/<repo>")
    p.add_argument("--notes-out", required=True)
    r = sub.add_parser("previous", help="print the previous vX.Y.Z tag (empty if none)")
    r.add_argument("--tag", required=True)
    r.add_argument("--tags-file", required=True)
    g = sub.add_parser("gate", help="may this tag be promoted? writes the verification line")
    g.add_argument("--tag", required=True)
    g.add_argument("--tags-file", required=True, help="all tag names, one per line")
    g.add_argument("--tag-commit", required=True, help="full SHA of the commit the tag points to")
    g.add_argument("--result-file", required=True, help="openclip-test-<tag>.json; may be absent for a patch")
    g.add_argument("--line-out", required=True, help="the verification line ('' for a patch)")
    q = sub.add_parser("promote")
    q.add_argument("--notes-in", required=True)
    q.add_argument("--notes-out", required=True)
    q.add_argument("--verification", action="append", default=[], help="a line to add; may repeat")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "plan":
            message = _read(a.message_file)
            tags = [t.strip() for t in _read(a.tags_file).splitlines() if t.strip()]
            plan = classify(a.tag, a.version, tags, message)
            commits = [c.strip() for c in _read(a.commits_file).splitlines() if c.strip()]
            compare = f"{a.repo_url}/compare/{plan.previous}...{plan.tag}" if plan.previous else None
            title, notes = render_notes(plan, message, commits, a.ci_line, compare)
            with open(a.notes_out, "w", encoding="utf-8") as f:
                f.write(notes)
            print(f"tag:        {plan.tag}\nprevious:   {plan.previous or '(none)'}\nkind:       {plan.kind}")
            print(f"release as: {'PRE-RELEASE' if plan.prerelease else 'normal release'}")
            print("why:        " + "; ".join(plan.reasons))
            print(f"title:      {title}\n----- notes -----\n{notes}-----------------")
            _set_output("title", title)
            _set_output("prerelease", "true" if plan.prerelease else "false")
            _set_output("notes", notes)
        elif a.cmd == "previous":
            tags = _read(a.tags_file).split()
            print((previous_tag(a.tag, tags) if parse_tag(a.tag) else None) or "")
        elif a.cmd == "gate":
            tags = [t.strip() for t in _read(a.tags_file).splitlines() if t.strip()]
            result = None
            if os.path.exists(a.result_file):
                try:
                    result = json.loads(_read(a.result_file))
                except ValueError as e:
                    raise ReleaseError(f"{a.result_file}: not valid JSON ({e})") from None
            line = gate(a.tag, tags, a.tag_commit, result)
            with open(a.line_out, "w", encoding="utf-8") as f:
                f.write(line)
            print(line or f"{a.tag}: patch release, promoted without a result file")
        else:
            with open(a.notes_out, "w", encoding="utf-8") as f:
                f.write(promote(_read(a.notes_in), *a.verification))
    except ReleaseError as e:
        print(f"::error::{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
