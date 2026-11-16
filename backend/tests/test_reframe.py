import re

import pytest

from app.services import reframe
from app.services.reframe import (
    Sample,
    choose_layout,
    crop_size,
    crop_x_expression,
    plan_track_keyframes,
    simplify_keyframes,
    smooth_path,
)


def evaluate(expr: str, t: float) -> float:
    """Evaluate the FFmpeg expression subset we generate (lt, gte, + - * /)."""
    py = re.sub(r"\blt\(t,([\d.]+)\)", lambda m: f"(1 if t < {m.group(1)} else 0)", expr)
    py = re.sub(r"\bgte\(t,([\d.]+)\)", lambda m: f"(1 if t >= {m.group(1)} else 0)", py)
    return eval(py, {"t": t})  # noqa: S307 - test helper on generated arithmetic only


@pytest.mark.parametrize(
    ("src", "expected"),
    [((1920, 1080), (608, 1080)), ((1280, 720), (404, 720)), ((640, 360), (202, 360)),
     ((1080, 1920), (1080, 1920)), ((720, 1280), (720, 1280)), ((1080, 1080), (608, 1080))],
)
def test_crop_size(src, expected):
    assert crop_size(*src) == expected


def test_smooth_path_holds_still_inside_dead_zone():
    jitter = [500, 510, 495, 505, 498, 502, 507, 493]
    assert len(set(smooth_path(jitter, window=3, dead_zone=40))) == 1


def test_smooth_path_follows_real_movement():
    moving = [500] * 5 + [900] * 10
    path = smooth_path(moving, window=3, dead_zone=40)
    assert path[0] == 500
    assert path[-1] == pytest.approx(900 - 40)  # follows, lagging by the dead zone


def test_keyframes_jump_at_scene_cut_and_stay_in_bounds():
    samples = [Sample(i * 0.25, 300.0) for i in range(8)]
    samples += [Sample(2.0 + i * 0.25, 1700.0, cut=(i == 0)) for i in range(8)]
    kf = plan_track_keyframes(samples, src_width=1920, crop_width=608)
    expr = crop_x_expression(kf)
    assert evaluate(expr, 0.5) == pytest.approx(0)       # face at 300: clamped to left edge
    assert evaluate(expr, 1.99) == pytest.approx(0)      # held until the cut...
    assert evaluate(expr, 2.0) == pytest.approx(1312)    # ...then jumps (1920-608 = max)
    assert all(0 <= x <= 1312 and x % 2 == 0 for _, x in kf)


def test_missing_detections_hold_position():
    samples = [Sample(0, None), Sample(0.25, 960.0), Sample(0.5, None), Sample(0.75, None)]
    kf = plan_track_keyframes(samples, src_width=1920, crop_width=608)
    assert {x for _, x in kf} == {656}  # 960 - 304, also used before the first detection


def test_simplify_keyframes_drops_collinear_points():
    kf = [(0.0, 0), (1.0, 100), (2.0, 200), (3.0, 202), (4.0, 202)]
    assert simplify_keyframes(kf) == ((0.0, 0), (2.0, 200), (4.0, 202))


def test_crop_expression_interpolates_linearly():
    expr = crop_x_expression(((0.0, 100), (2.0, 300), (4.0, 300)))
    assert evaluate(expr, 0) == 100
    assert evaluate(expr, 1) == pytest.approx(200)
    assert evaluate(expr, 3) == 300
    assert evaluate(expr, 99) == 300
    assert crop_x_expression(((0.0, 42),)) == "42"
    assert crop_x_expression(()) == "0"


def test_choose_layout():
    faces = [Sample(i, 500.0) for i in range(6)] + [Sample(6 + i, None) for i in range(4)]
    assert choose_layout(faces) == ("track", 0.6)
    assert choose_layout([Sample(0, None)] * 10) == ("fit", 0.0)
    assert choose_layout([]) == ("fit", 0.0)


def test_pick_face_prefers_large_confident_face_near_previous():
    import numpy as np

    def face(x, size, score=0.95):
        return [x, 50, size, size, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, score]

    # Two similar faces (podcast): the bigger wins at first...
    podcast = np.array([face(100, 80), face(380, 70)], dtype=np.float32)
    assert reframe._pick_face(podcast, 4.0, None) == pytest.approx(560)
    # ...but once the camera is on the other one, it stays there (no ping-pong).
    assert reframe._pick_face(podcast, 4.0, previous_x=1660) == pytest.approx(1660)
    # A face 4x larger elsewhere still takes over.
    dominant = np.array([face(100, 160), face(380, 40)], dtype=np.float32)
    assert reframe._pick_face(dominant, 4.0, previous_x=1600) == pytest.approx(720)
    assert reframe._pick_face(None, 4.0, None) is None


def test_analyze_clip_without_faces_uses_fit_layout(sample_video):
    plan = reframe.analyze_clip(sample_video, 0, 3)  # FFmpeg test pattern: no faces
    assert plan.layout == "fit"
    assert (plan.src_width, plan.src_height) == (320, 240)
    assert plan.face_ratio == 0.0


def test_model_checksum_is_verified():
    reframe._verified_model_path.cache_clear()
    assert reframe._verified_model_path().endswith(".onnx")
