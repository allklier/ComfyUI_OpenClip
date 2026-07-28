from __future__ import annotations

from pathlib import Path

import pytest
import torch

pytest.importorskip("PyOpenColorIO")

from ComfyUI_OpenClip.lib.colour_transform import apply_colour_transform, OUTPUT_COLOUR_SPACE
from ComfyUI_OpenClip.nodes.colour_transform import (
    OpenClipColourTransform,
    _default_ocio_config,
    _pick_most_recent_flame_config,
)

FIXTURE_CONFIG = str(Path(__file__).parent / "fixtures" / "test_ocio.ocio")


# --- config discovery ---


def test_pick_most_recent_empty_returns_empty():
    assert _pick_most_recent_flame_config([]) == ""


def test_pick_most_recent_single():
    candidates = ["/opt/Autodesk/colour_mgmt/flame_configs/2025.1/aces2.0_config/config.ocio"]
    assert _pick_most_recent_flame_config(candidates) == candidates[0]


def test_pick_most_recent_selects_highest_version():
    candidates = [
        "/opt/Autodesk/colour_mgmt/flame_configs/2024.1/aces2.0_config/config.ocio",
        "/opt/Autodesk/colour_mgmt/flame_configs/2025.2/aces2.0_config/config.ocio",
        "/opt/Autodesk/colour_mgmt/flame_configs/2025.10/aces2.0_config/config.ocio",
    ]
    result = _pick_most_recent_flame_config(candidates)
    assert "2025.10" in result


def test_pick_most_recent_version_sort_beats_lexicographic():
    # "2025.10" > "2025.9" numerically but "2025.9" > "2025.10" lexicographically
    candidates = [
        "/opt/Autodesk/colour_mgmt/flame_configs/2025.9/aces2.0_config/config.ocio",
        "/opt/Autodesk/colour_mgmt/flame_configs/2025.10/aces2.0_config/config.ocio",
    ]
    result = _pick_most_recent_flame_config(candidates)
    assert "2025.10" in result


def test_default_ocio_uses_env_var(monkeypatch):
    monkeypatch.setenv("OCIO", "/custom/config.ocio")
    assert _default_ocio_config() == "/custom/config.ocio"


def test_default_ocio_env_var_beats_flame_install(monkeypatch):
    monkeypatch.setenv("OCIO", "/custom/config.ocio")
    import ComfyUI_OpenClip.nodes.colour_transform as ct_mod
    monkeypatch.setattr(ct_mod.glob, "glob", lambda _: [
        "/opt/Autodesk/colour_mgmt/flame_configs/2025.1/aces2.0_config/config.ocio"
    ])
    assert _default_ocio_config() == "/custom/config.ocio"


# --- colour transform ---


def test_apply_identity_transform():
    images = torch.full((1, 4, 4, 3), 0.5)
    result = apply_colour_transform(images, FIXTURE_CONFIG, "linear")
    assert torch.allclose(result, images, atol=1e-5)


def test_apply_scale_transform():
    images = torch.full((2, 4, 4, 3), 0.25)
    result = apply_colour_transform(images, FIXTURE_CONFIG, "scaled_2x")
    assert torch.allclose(result, torch.full_like(result, 0.5), atol=1e-5)


def test_apply_transform_does_not_modify_input():
    images = torch.full((1, 4, 4, 3), 0.25)
    original = images.clone()
    apply_colour_transform(images, FIXTURE_CONFIG, "scaled_2x")
    assert torch.allclose(images, original)


def test_apply_transform_returns_float32():
    images = torch.rand(1, 4, 4, 3)
    result = apply_colour_transform(images, FIXTURE_CONFIG, "linear")
    assert result.dtype == torch.float32


def test_apply_transform_preserves_shape():
    images = torch.rand(3, 8, 16, 3)
    result = apply_colour_transform(images, FIXTURE_CONFIG, "linear")
    assert result.shape == images.shape


def test_apply_transform_wrong_channels_raises():
    images = torch.rand(1, 4, 4, 4)  # 4-channel (RGBA)
    with pytest.raises(ValueError, match="3-channel"):
        apply_colour_transform(images, FIXTURE_CONFIG, "linear")


# --- node ---


def test_node_mask_passthrough():
    node = OpenClipColourTransform()
    images = torch.full((1, 4, 4, 3), 0.25)
    mask = torch.ones(1, 4, 4)
    _, out_mask = node.execute(IMAGE=images, ocio_config=FIXTURE_CONFIG,
                               input_colour_space="scaled_2x", MASK=mask)
    assert torch.allclose(out_mask, mask)


def test_node_no_mask_returns_zeros():
    node = OpenClipColourTransform()
    images = torch.full((1, 4, 4, 3), 0.25)
    _, out_mask = node.execute(IMAGE=images, ocio_config=FIXTURE_CONFIG,
                               input_colour_space="linear")
    assert out_mask.shape == (1, 4, 4)
    assert out_mask.sum() == 0


def test_node_raises_on_empty_config():
    node = OpenClipColourTransform()
    with pytest.raises(ValueError, match="ocio_config"):
        node.execute(IMAGE=torch.rand(1, 4, 4, 3), ocio_config="",
                     input_colour_space="linear")


def test_node_raises_on_empty_space():
    node = OpenClipColourTransform()
    with pytest.raises(ValueError, match="input_colour_space"):
        node.execute(IMAGE=torch.rand(1, 4, 4, 3), ocio_config=FIXTURE_CONFIG,
                     input_colour_space="")


def test_node_output_constant():
    node = OpenClipColourTransform()
    images = torch.full((1, 4, 4, 3), 0.25)
    out_image, _ = node.execute(IMAGE=images, ocio_config=FIXTURE_CONFIG,
                                input_colour_space="scaled_2x")
    assert torch.allclose(out_image, torch.full_like(out_image, 0.5), atol=1e-5)
