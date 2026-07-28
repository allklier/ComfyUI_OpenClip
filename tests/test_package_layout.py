from __future__ import annotations

import pytest

from ComfyUI_OpenClip.lib import package_layout


def test_standard_flame_paths(tmp_path):
    paths = package_layout.build("Standard Flame", str(tmp_path), "myshot", "v001", "EXR", 4)
    assert paths.clip_file == tmp_path / "myshot" / "myshot.clip"
    assert paths.sidecar_file == tmp_path / "myshot" / "versions" / "v001" / "myshot.v001.comfy.json"
    assert paths.media_dir == tmp_path / "myshot" / "versions" / "v001"
    assert paths.path_pattern_token == "versions/v001/myshot.%04d.exr"


def test_flat_paths(tmp_path):
    paths = package_layout.build("Flat", str(tmp_path), "myshot", "v001", "EXR", 4)
    assert paths.clip_file == tmp_path / "myshot.clip"
    assert paths.sidecar_file == tmp_path / "myshot.v001.comfy.json"
    assert paths.media_dir == tmp_path
    assert paths.path_pattern_token == "myshot.%04d.exr"


def test_standard_flame_no_versions_subdir_in_flat_token(tmp_path):
    paths = package_layout.build("Flat", str(tmp_path), "clip", "v002", "PNG", 4)
    assert "versions" not in paths.path_pattern_token


def test_standard_flame_creates_dirs(tmp_path):
    paths = package_layout.build("Standard Flame", str(tmp_path), "myshot", "v001", "EXR", 4)
    package_layout.create_dirs(paths)
    assert paths.clip_file.parent.exists()
    assert paths.media_dir.exists()


def test_flat_creates_dirs(tmp_path):
    paths = package_layout.build("Flat", str(tmp_path), "myshot", "v001", "EXR", 4)
    package_layout.create_dirs(paths)
    assert paths.clip_file.parent.exists()
    assert paths.media_dir.exists()


def test_path_token_relative_to_clip_file(tmp_path):
    paths = package_layout.build("Standard Flame", str(tmp_path), "clip", "v001", "EXR", 4)
    # The token must not be an absolute path
    assert not paths.path_pattern_token.startswith("/")
    # Resolving token from clip_file's directory must land in media_dir
    resolved = (paths.clip_file.parent / (paths.path_pattern_token % 1001)).resolve()
    expected = (paths.media_dir / "clip.1001.exr").resolve()
    assert resolved == expected


def test_unknown_layout_raises(tmp_path):
    with pytest.raises(ValueError, match="Unknown layout"):
        package_layout.build("Nonexistent", str(tmp_path), "clip", "v001", "EXR", 4)


def test_frame_padding_reflected_in_token(tmp_path):
    paths = package_layout.build("Standard Flame", str(tmp_path), "clip", "v001", "EXR", 6)
    assert "%06d" in paths.path_pattern_token


def test_png_extension_in_token(tmp_path):
    paths = package_layout.build("Flat", str(tmp_path), "clip", "v001", "PNG", 4)
    assert paths.path_pattern_token.endswith(".png")
