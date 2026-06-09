from __future__ import annotations

from pathlib import Path
import sys
import types

from ComfyOpenClip.nodes._paths import resolve_clip_path
from ComfyOpenClip.nodes.reader import _auto_remap, _remap_prefix


def test_absolute_path_returned_unchanged(tmp_path):
    p = str(tmp_path / "clip.clip")
    assert resolve_clip_path(p) == p


def test_relative_path_resolved_against_input_dir(tmp_path, monkeypatch):
    # Inject a fake folder_paths module so the test does not need ComfyUI
    fake = types.ModuleType("folder_paths")
    fake.get_input_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", fake)

    result = resolve_clip_path("myshot/myshot.clip")
    assert result == str(tmp_path / "myshot" / "myshot.clip")


def test_relative_path_passthrough_without_folder_paths(monkeypatch):
    monkeypatch.delitem(sys.modules, "folder_paths", raising=False)
    result = resolve_clip_path("myshot/myshot.clip")
    assert result == "myshot/myshot.clip"


# --- _remap_prefix ---


def test_remap_replaces_matching_prefix():
    result = _remap_prefix(
        "/Volumes/LocalJobs/shots/clip.%06d.exr",
        "/Volumes/LocalJobs",
        "/mnt/LocalJobs",
    )
    assert result == "/mnt/LocalJobs/shots/clip.%06d.exr"


def test_remap_no_match_returns_unchanged():
    result = _remap_prefix(
        "/other/path/clip.%06d.exr",
        "/Volumes/LocalJobs",
        "/mnt/LocalJobs",
    )
    assert result == "/other/path/clip.%06d.exr"


def test_remap_empty_from_returns_unchanged():
    result = _remap_prefix("/some/path.exr", "", "/mnt/LocalJobs")
    assert result == "/some/path.exr"


def test_remap_empty_to_returns_unchanged():
    result = _remap_prefix("/some/path.exr", "/Volumes", "")
    assert result == "/some/path.exr"


# --- _auto_remap ---


def test_auto_remap_detects_media_dir_beside_clip(tmp_path):
    # Simulate: clip was authored with /fake/origin/project/media as the media dir.
    # The whole package was copied to tmp_path; the media dir is now tmp_path/media.
    clip_path = str(tmp_path / "shot.clip")
    (tmp_path / "media").mkdir()

    path_from, path_to = _auto_remap(clip_path, "/fake/origin/project/media/frame.%04d.exr")

    assert path_from == "/fake/origin/project"
    assert path_to == str(tmp_path)


def test_auto_remap_prefers_longest_suffix_match(tmp_path):
    # Both 'files' and 'shot/files' exist under tmp_path.
    # The embedded path ends with 'shot/files/', so 'shot/files' is the more
    # specific match and should win over just 'files'.
    clip_path = str(tmp_path / "shot.clip")
    (tmp_path / "files").mkdir()
    (tmp_path / "shot" / "files").mkdir(parents=True)

    path_from, path_to = _auto_remap(clip_path, "/fake/root/shot/files/frame.%04d.exr")

    assert path_from == "/fake/root"
    assert path_to == str(tmp_path)


def test_auto_remap_returns_none_when_no_match(tmp_path):
    clip_path = str(tmp_path / "shot.clip")
    # No subdirectories created — nothing to match against.
    result = _auto_remap(clip_path, "/fake/origin/media/frame.%04d.exr")
    assert result is None


def test_auto_remap_skips_relative_paths(tmp_path):
    clip_path = str(tmp_path / "shot.clip")
    # Relative paths need no remap.
    result = _auto_remap(clip_path, "media/frame.%04d.exr")
    assert result is None
