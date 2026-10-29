"""Tests for release_plan.py (run in CI by tests.yml: pytest .github/scripts)."""
from __future__ import annotations

import json
import re

import pytest

from release_plan import ReleaseError, classify, gate, main, previous_tag, promote, render_notes, split_message

# The repo's real tags on 2026-10-28, odd early names included.
REAL_TAGS = ["v.081", "v.09", "v0.8", "v0.91", "v0.92", "v0.93", "v0.94", "v0.95", "v1.0.0", "v1.0.1", "v1.1.0"]
ANNOTATED = "Flame fixes\n\nFixes the thing.\nAnd another.\n"


def test_previous_tag_ignores_non_semver_and_picks_highest_lower():
    assert previous_tag("v1.1.1", REAL_TAGS) == "v1.1.0"
    assert previous_tag("v1.0.2", REAL_TAGS + ["v1.1.1"]) == "v1.0.1"  # hotfix on an older line
    assert previous_tag("v1.0.0", REAL_TAGS) is None  # v0.9x tags aren't vX.Y.Z


def test_patch_bump_is_normal_release():
    plan = classify("v1.1.1", "1.1.1", REAL_TAGS + ["v1.1.1"], ANNOTATED)
    assert (plan.kind, plan.prerelease, plan.previous) == ("patch", False, "v1.1.0")


@pytest.mark.parametrize("tag,kind", [("v1.2.0", "minor"), ("v2.0.0", "major"), ("v1.3.5", "minor")])
def test_minor_and_major_bumps_are_pre_releases(tag, kind):
    plan = classify(tag, tag[1:], REAL_TAGS, "")
    assert (plan.kind, plan.prerelease) == (kind, True)


def test_marker_forces_patch_to_pre_release():
    plan = classify("v1.1.1", "1.1.1", REAL_TAGS, "Risky fix [pre-release]\n\nbody")
    assert (plan.kind, plan.prerelease) == ("patch", True)


def test_nothing_forces_minor_to_normal_release():
    # There is no "full release" marker; a minor bump stays a pre-release whatever the message says.
    plan = classify("v1.2.0", "1.2.0", REAL_TAGS, "[release] [full] not a pre-release please")
    assert plan.prerelease is True


def test_first_release_is_pre_release():
    plan = classify("v0.1.0", "0.1.0", [], "")
    assert (plan.kind, plan.prerelease, plan.previous) == ("initial", True, None)


@pytest.mark.parametrize("tag,version", [("v1.1.1", "1.1.2"), ("v1.1.1", "1.1.1rc1"), ("1.1.1", "1.1.1")])
def test_tag_must_match_pyproject_version(tag, version):
    with pytest.raises(ReleaseError):
        classify(tag, version, REAL_TAGS, "")


@pytest.mark.parametrize("tag", ["v1.2", "v1.2.3-rc1", "v1.2.3.4", "vX.Y.Z"])
def test_tag_shape_is_strict(tag):
    with pytest.raises(ReleaseError):
        classify(tag, tag[1:], REAL_TAGS, "")


def test_split_message_drops_marker_and_signature():
    msg = "Subject line\n\n[pre-release]\nBody text [PRE-RELEASE] here\n-----BEGIN PGP SIGNATURE-----\nabc\n"
    assert split_message(msg) == ("Subject line", "Body text  here", True)
    assert split_message("") == ("", "", False)


def test_notes_from_tag_message():
    plan = classify("v1.1.1", "1.1.1", REAL_TAGS, ANNOTATED)
    title, notes = render_notes(plan, ANNOTATED, ["ignored"], "✔ https://run", "https://cmp")
    assert title == "v1.1.1: Flame fixes"
    assert "- CI (unit tests, security scan): ✔ https://run" in notes
    assert "- Acceptance (live ComfyUI): – not run" in notes
    assert "**Status: LIGHTLY TESTED**" in notes
    assert "Fixes the thing.\nAnd another." in notes and "ignored" not in notes
    assert "**Full changelog**: https://cmp" in notes


def test_notes_fall_back_to_commit_subjects_for_lightweight_tag():
    plan = classify("v1.1.1", "1.1.1", REAL_TAGS, "")
    title, notes = render_notes(plan, "", ["Fix A", "Fix B"], "✔ x", None)
    assert title == "v1.1.1"
    assert "- Fix A\n- Fix B" in notes and "Full changelog" not in notes


def test_promote_sets_fully_tested_and_adds_line():
    plan = classify("v1.2.0", "1.2.0", REAL_TAGS, ANNOTATED)
    _, notes = render_notes(plan, ANNOTATED, [], "✔ x", None)
    out = promote(notes, "Acceptance (live ComfyUI): ✔ akflame5,\n 2026-11-02")
    assert "**Status: FULLY TESTED**" in out and "LIGHTLY" not in out
    assert "- Flame 2027: – not tested\n- Acceptance (live ComfyUI): ✔ akflame5, 2026-11-02\n" in out
    assert out.endswith("Fixes the thing.\nAnd another.\n")
    assert promote(notes) .count("- ") == notes.count("- ")  # no line given: only the status changes


def test_promote_refuses_notes_without_block():
    with pytest.raises(ReleaseError):
        promote("hand-written notes\n")


def test_main_plan_writes_notes_and_outputs(tmp_path, monkeypatch):
    out = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    (tmp_path / "tags").write_text("\n".join(REAL_TAGS + ["v1.2.0"]))
    (tmp_path / "msg").write_text(ANNOTATED)
    rc = main(["plan", "--tag", "v1.2.0", "--version", "1.2.0", "--tags-file", str(tmp_path / "tags"),
               "--message-file", str(tmp_path / "msg"), "--ci-line", "✔ r",
               "--repo-url", "https://github.com/o/r", "--notes-out", str(tmp_path / "notes")])
    assert rc == 0
    assert "compare/v1.1.0...v1.2.0" in (tmp_path / "notes").read_text()
    outputs = out.read_text()
    assert "\ntrue\n" in outputs and "v1.2.0: Flame fixes" in outputs
    assert "**Status: LIGHTLY TESTED**" in outputs  # notes are a job output too


def test_main_previous(tmp_path, capsys):
    (tmp_path / "tags").write_text("\n".join(REAL_TAGS))
    assert main(["previous", "--tag", "v1.1.1", "--tags-file", str(tmp_path / "tags")]) == 0
    assert main(["previous", "--tag", "not-a-tag", "--tags-file", str(tmp_path / "tags")]) == 0
    assert capsys.readouterr().out == "v1.1.0\n\n"


def test_main_plan_mismatch_fails(tmp_path, capsys):
    (tmp_path / "tags").write_text("v1.1.0\n")
    rc = main(["plan", "--tag", "v1.1.1", "--version", "1.1.0", "--tags-file", str(tmp_path / "tags"),
               "--ci-line", "x", "--repo-url", "u", "--notes-out", str(tmp_path / "n")])
    assert rc == 1 and "does not match" in capsys.readouterr().out


# --- gate: promote needs the maintainer run's result file, except for a patch ---------------------------

COMMIT = "caae91f8ccccb0307fab257f4184548f5954217b"
TAGS_2 = REAL_TAGS + ["v1.1.1", "v2.0.0"]


def result(**over):
    """A result file as scripts/maintainer_test.sh writes it (field names copied from there)."""
    doc = {
        "schema": 1, "ref": "v2.0.0", "commit": COMMIT, "version": "2.0.0", "tier": "full",
        "results": {
            "pytest_full": {"passed": 776, "failed": 0, "skipped": 94, "xfailed": 0, "failed_tests": [],
                            "real_clip_tests": {"passed": 158, "skipped": 94, "xfailed": 0, "failed": 0},
                            "exit_code": 0},
            "acceptance_normal": {"passed": 34, "failed": 0, "failed_tests": [], "exit_code": 0},
            "acceptance_noocio": {"passed": 6, "failed": 0, "failed_tests": [], "exit_code": 0},
        },
        "acceptance_source": "real Flame clip", "environment": {},
        "timestamp": "2026-10-29T18:00:00Z", "overall": "PASS",
    }
    doc.update(over)
    return doc


def test_gate_patch_is_exempt_even_without_a_result_file():
    assert gate("v1.1.1", REAL_TAGS + ["v1.1.1"], COMMIT, None) == ""


@pytest.mark.parametrize("tag", ["v2.0.0", "v1.2.0"])
def test_gate_minor_or_major_without_result_file_refuses(tag):
    with pytest.raises(ReleaseError, match=rf"needs the maintainer run's result.*gh release upload {tag} "
                                           rf"openclip-test-{tag}\.json"):
        gate(tag, TAGS_2 + [tag], COMMIT, None)


def test_gate_first_release_is_not_a_patch():
    with pytest.raises(ReleaseError, match="initial release"):
        gate("v0.1.0", [], COMMIT, None)


def test_gate_good_result_gives_the_verification_line():
    line = gate("v2.0.0", TAGS_2, COMMIT, result())
    assert line == ("Full tier + live ComfyUI (maintainer run 2026-10-29T18:00:00Z, caae91f): ✔ pytest 776 passed "
                    "(real Flame clips 158), acceptance 34/34, without OCIO 6/6")


def test_gate_accepts_a_short_commit():
    assert gate("v2.0.0", TAGS_2, COMMIT, result(commit=COMMIT[:7]))


@pytest.mark.parametrize("over, why", [
    ({"ref": "feature/2.0-clip-info"}, "ref is 'feature/2.0-clip-info'"),
    ({"commit": "387d8c1"}, "commit is '387d8c1'"),
    ({"commit": "caae"}, "commit is 'caae'"),  # too short to identify a commit
    ({"tier": "smoke"}, "tier is 'smoke'"),
    ({"overall": "FAIL"}, "overall is 'FAIL'"),
])
def test_gate_refuses_a_result_for_something_else(over, why):
    with pytest.raises(ReleaseError, match=re.escape(why)):
        gate("v2.0.0", TAGS_2, COMMIT, result(**over))


def test_gate_refuses_a_run_without_real_clips():
    doc = result()
    doc["results"]["pytest_full"]["real_clip_tests"]["passed"] = 0
    with pytest.raises(ReleaseError, match="no real Flame clip was tested"):
        gate("v2.0.0", TAGS_2, COMMIT, doc)


def test_gate_refuses_a_file_with_missing_fields():
    doc = result()
    del doc["results"]["pytest_full"]["real_clip_tests"]
    with pytest.raises(ReleaseError, match="missing results.pytest_full.real_clip_tests"):
        gate("v2.0.0", TAGS_2, COMMIT, doc)


def test_promote_adds_several_lines_in_order():
    plan = classify("v2.0.0", "2.0.0", TAGS_2, ANNOTATED)
    _, notes = render_notes(plan, ANNOTATED, [], "✔ x", None)
    out = promote(notes, "Full tier: ✔ a", "", "Flame 2027: ✔ b")
    assert "- Flame 2027: – not tested\n- Full tier: ✔ a\n- Flame 2027: ✔ b\n\n**Status: FULLY TESTED**" in out


def test_main_gate(tmp_path, capsys):
    (tmp_path / "tags").write_text("\n".join(TAGS_2))
    args = ["gate", "--tag", "v2.0.0", "--tags-file", str(tmp_path / "tags"), "--tag-commit", COMMIT,
            "--result-file", str(tmp_path / "openclip-test-v2.0.0.json"), "--line-out", str(tmp_path / "line")]
    assert main(args) == 1 and "needs the maintainer run's result" in capsys.readouterr().out  # no file
    (tmp_path / "openclip-test-v2.0.0.json").write_text("{not json")
    assert main(args) == 1 and "not valid JSON" in capsys.readouterr().out
    (tmp_path / "openclip-test-v2.0.0.json").write_text(json.dumps(result()))
    assert main(args) == 0 and (tmp_path / "line").read_text().startswith("Full tier + live ComfyUI")
    patch = ["gate", "--tag", "v1.1.1", "--tags-file", str(tmp_path / "tags"), "--tag-commit", COMMIT,
             "--result-file", str(tmp_path / "absent.json"), "--line-out", str(tmp_path / "line")]
    assert main(patch) == 0 and (tmp_path / "line").read_text() == ""
    assert "patch release" in capsys.readouterr().out


def test_main_promote_with_two_lines(tmp_path):
    plan = classify("v2.0.0", "2.0.0", TAGS_2, ANNOTATED)
    _, notes = render_notes(plan, ANNOTATED, [], "✔ x", None)
    (tmp_path / "in").write_text(notes)
    assert main(["promote", "--notes-in", str(tmp_path / "in"), "--notes-out", str(tmp_path / "out"),
                 "--verification", "Full tier: ✔ a", "--verification", "Flame 2027: ✔ b"]) == 0
    assert "- Full tier: ✔ a\n- Flame 2027: ✔ b\n" in (tmp_path / "out").read_text()
