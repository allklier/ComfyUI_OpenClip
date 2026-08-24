from __future__ import annotations

from pathlib import Path

import pytest
import torch

pytest.importorskip("PyOpenColorIO")

from ComfyUI_OpenClip.lib.colour_transform import apply_colourspace_transform
from ComfyUI_OpenClip.nodes.colour_space_transform import (
    ACESCG_LABEL,
    CAMERA_COLOUR_SPACES,
    CUSTOM_LABEL,
    SAME_AS_INPUT_LABEL,
    TARGET_COLOUR_SPACES,
    OpenClipColourSpaceTransform,
)

FIXTURE_CONFIG = str(Path(__file__).parent / "fixtures" / "test_ocio.ocio")


# --- lib function: plain scene-to-scene, against the same fixture config
# test_colour_transform.py already uses ---


def test_scale_transform_matches_the_view_bypassed_result():
    # scaled_2x -> linear (the fixture's scene-reference space) should match
    # test_colour_transform.py's own Raw-view (no tone-mapping) result: 0.25 -> 0.5.
    images = torch.full((1, 4, 4, 3), 0.25)
    result = apply_colourspace_transform(images, FIXTURE_CONFIG, "scaled_2x", "linear")
    assert torch.allclose(result, torch.full_like(result, 0.5), atol=1e-5)


def test_identity_space_is_a_no_op():
    images = torch.full((1, 4, 4, 3), 0.37)
    result = apply_colourspace_transform(images, FIXTURE_CONFIG, "linear", "linear")
    assert torch.allclose(result, images, atol=1e-5)


def test_forward_then_reverse_round_trips():
    images = torch.rand(1, 4, 4, 3)
    forward = apply_colourspace_transform(images, FIXTURE_CONFIG, "scaled_2x", "linear")
    back = apply_colourspace_transform(forward, FIXTURE_CONFIG, "linear", "scaled_2x")
    assert torch.allclose(back, images, atol=1e-5)


def test_does_not_modify_input():
    images = torch.full((1, 4, 4, 3), 0.25)
    original = images.clone()
    apply_colourspace_transform(images, FIXTURE_CONFIG, "scaled_2x", "linear")
    assert torch.allclose(images, original)


def test_wrong_channels_raises():
    images = torch.rand(1, 4, 4, 4)
    with pytest.raises(ValueError, match="3-channel"):
        apply_colourspace_transform(images, FIXTURE_CONFIG, "linear", "linear")


# --- node: friendly camera labels resolve to real names, Custom escape hatch ---


def test_camera_labels_are_all_distinct_non_empty_strings():
    assert len(CAMERA_COLOUR_SPACES) >= 5
    real_names = list(CAMERA_COLOUR_SPACES.values())
    assert len(real_names) == len(set(real_names)), "duplicate real colour-space names"
    assert all(label and name for label, name in CAMERA_COLOUR_SPACES.items())


def test_acescg_is_first_in_both_dropdowns():
    assert list(CAMERA_COLOUR_SPACES)[0] == ACESCG_LABEL
    assert TARGET_COLOUR_SPACES[0] == ACESCG_LABEL
    assert TARGET_COLOUR_SPACES[1] == SAME_AS_INPUT_LABEL


def test_target_dropdown_has_no_duplicate_acescg_entry():
    # ACEScg appears once, pulled to the front -- not also inside the
    # trailing camera-list copy.
    assert TARGET_COLOUR_SPACES.count(ACESCG_LABEL) == 1


def test_node_resolves_a_camera_label(monkeypatch):
    # Swap in the fixture config's own colour spaces so this runs against a
    # config that actually has the resolved name, without touching the real
    # camera dict.
    import ComfyUI_OpenClip.nodes.colour_space_transform as cst_mod
    monkeypatch.setitem(cst_mod.CAMERA_COLOUR_SPACES, "Test Camera", "scaled_2x")

    node = OpenClipColourSpaceTransform()
    images = torch.full((1, 4, 4, 3), 0.25)
    out_image, _ = node.execute(
        IMAGE=images, ocio_config=FIXTURE_CONFIG, source="Test Camera",
        custom_source="", target_colour_space=CUSTOM_LABEL,
        custom_target_colour_space="linear",
    )
    assert torch.allclose(out_image, torch.full_like(out_image, 0.5), atol=1e-5)


def test_node_custom_source():
    node = OpenClipColourSpaceTransform()
    images = torch.full((1, 4, 4, 3), 0.25)
    out_image, _ = node.execute(
        IMAGE=images, ocio_config=FIXTURE_CONFIG, source=CUSTOM_LABEL,
        custom_source="scaled_2x", target_colour_space=CUSTOM_LABEL,
        custom_target_colour_space="linear",
    )
    assert torch.allclose(out_image, torch.full_like(out_image, 0.5), atol=1e-5)


def test_node_custom_without_value_raises():
    node = OpenClipColourSpaceTransform()
    with pytest.raises(ValueError, match="custom_source"):
        node.execute(
            IMAGE=torch.rand(1, 4, 4, 3), ocio_config=FIXTURE_CONFIG, source=CUSTOM_LABEL,
            custom_source="", target_colour_space=CUSTOM_LABEL,
            custom_target_colour_space="linear",
        )


def test_node_custom_target_without_value_raises():
    node = OpenClipColourSpaceTransform()
    with pytest.raises(ValueError, match="custom_target_colour_space"):
        node.execute(
            IMAGE=torch.rand(1, 4, 4, 3), ocio_config=FIXTURE_CONFIG, source=CUSTOM_LABEL,
            custom_source="linear", target_colour_space=CUSTOM_LABEL,
            custom_target_colour_space="",
        )


def test_node_same_as_input_pipe_resolves():
    node = OpenClipColourSpaceTransform()
    images = torch.full((1, 4, 4, 3), 0.25)
    out_image, _ = node.execute(
        IMAGE=images, ocio_config=FIXTURE_CONFIG, source=CUSTOM_LABEL,
        custom_source="scaled_2x", target_colour_space=SAME_AS_INPUT_LABEL,
        same_as_input_colour_space="linear",
    )
    assert torch.allclose(out_image, torch.full_like(out_image, 0.5), atol=1e-5)


def test_node_same_as_input_without_pipe_connected_raises():
    node = OpenClipColourSpaceTransform()
    with pytest.raises(ValueError, match="same_as_input_colour_space"):
        node.execute(
            IMAGE=torch.rand(1, 4, 4, 3), ocio_config=FIXTURE_CONFIG, source=CUSTOM_LABEL,
            custom_source="linear", target_colour_space=SAME_AS_INPUT_LABEL,
        )


def test_node_mask_passthrough():
    node = OpenClipColourSpaceTransform()
    images = torch.full((1, 4, 4, 3), 0.25)
    mask = torch.ones(1, 4, 4)
    _, out_mask = node.execute(
        IMAGE=images, ocio_config=FIXTURE_CONFIG, source=CUSTOM_LABEL,
        custom_source="linear", target_colour_space=CUSTOM_LABEL,
        custom_target_colour_space="linear", MASK=mask,
    )
    assert torch.allclose(out_mask, mask)


def test_node_raises_on_empty_config():
    node = OpenClipColourSpaceTransform()
    with pytest.raises(ValueError, match="ocio_config"):
        node.execute(
            IMAGE=torch.rand(1, 4, 4, 3), ocio_config="", source=CUSTOM_LABEL,
            custom_source="linear", target_colour_space=CUSTOM_LABEL,
            custom_target_colour_space="linear",
        )
