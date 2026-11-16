"""Plan the 9:16 crop for a clip: follow the speaker's face, or fit the whole frame.

1. Sample ~4 frames/s of the clip and detect faces with OpenCV's YuNet model.
2. Detect hard scene cuts (colour histogram change) so the camera jumps instead of
   sliding across a cut.
3. Per scene: fill gaps, smooth the face position and add a dead zone, so the virtual
   camera moves calmly and only when the subject really moves.
4. Express the crop's x position as an FFmpeg expression of time (linear between
   keyframes), applied in the same FFmpeg pass that renders the clip.

If faces are found in too few samples (screen recordings, slides, b-roll), the clip uses
the "fit" layout instead: the full frame over a blurred, zoomed copy of itself.
"""

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np

from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

SAMPLE_FPS = 4.0
DETECT_WIDTH = 480         # frames are downscaled to this width for detection
MIN_FACE_RATIO = 0.35      # below this share of samples with a face -> "fit" layout
CUT_THRESHOLD = 0.45       # Bhattacharyya distance between colour histograms
SMOOTH_SECONDS = 1.0       # moving-average window
DEAD_ZONE = 0.06           # fraction of source width the face may move before panning

Layout = Literal["track", "fit"]


@dataclass(frozen=True)
class Sample:
    t: float                  # seconds from clip start
    face_x: float | None      # face centre, source pixels
    cut: bool = False         # a hard scene cut happens at this sample


@dataclass(frozen=True)
class CropPlan:
    layout: Layout
    src_width: int
    src_height: int
    crop_width: int
    crop_height: int
    keyframes: tuple[tuple[float, int], ...]  # (t, crop x); used by "track"
    face_ratio: float


# --- Pure planning logic -----------------------------------------------------------------


def crop_size(src_width: int, src_height: int) -> tuple[int, int]:
    """Largest even-sized 9:16 crop that fits the source."""
    height = src_height - src_height % 2
    width = min(src_width, round(height * 9 / 16))
    width -= width % 2
    if width == src_width:  # source is already 9:16 or narrower
        height = min(height, round(width * 16 / 9))
        height -= height % 2
    return width, height


def split_scenes(samples: list[Sample]) -> list[list[Sample]]:
    scenes: list[list[Sample]] = []
    for s in samples:
        if not scenes or s.cut:
            scenes.append([])
        scenes[-1].append(s)
    return scenes


def _fill_gaps(values: list[float | None], default: float) -> list[float]:
    """Carry the last known value forward; before the first detection use the first one."""
    known = next((v for v in values if v is not None), default)
    out: list[float] = []
    for v in values:
        known = v if v is not None else known
        out.append(known)
    first = next((v for v in values if v is not None), default)
    for i, v in enumerate(values):
        if v is not None:
            break
        out[i] = first
    return out


def smooth_path(xs: list[float], *, window: int, dead_zone: float) -> list[float]:
    """Moving average, then hold still until the subject leaves a dead zone."""
    if not xs:
        return []
    half = max(0, window // 2)
    averaged = [float(np.mean(xs[max(0, i - half):i + half + 1])) for i in range(len(xs))]
    out, camera = [], averaged[0]
    for x in averaged:
        if abs(x - camera) > dead_zone:
            camera = x - dead_zone if x > camera else x + dead_zone
        out.append(camera)
    return out


def plan_track_keyframes(samples: list[Sample], *, src_width: int,
                         crop_width: int) -> tuple[tuple[float, int], ...]:
    """Crop x keyframes: smooth within each scene, jump at cuts."""
    max_x = src_width - crop_width
    window = max(1, round(SMOOTH_SECONDS * SAMPLE_FPS))
    keyframes: list[tuple[float, int]] = []
    for scene in split_scenes(samples):
        centres = _fill_gaps([s.face_x for s in scene], default=src_width / 2)
        path = smooth_path(centres, window=window, dead_zone=DEAD_ZONE * src_width)
        for s, cx in zip(scene, path, strict=True):
            x = int(round(min(max(cx - crop_width / 2, 0), max_x)))
            x -= x % 2
            if s.cut and keyframes:
                # Hold the old position until just before the cut, then jump.
                keyframes.append((round(s.t - 0.001, 3), keyframes[-1][1]))
            keyframes.append((round(s.t, 3), x))
    return simplify_keyframes(keyframes)


def simplify_keyframes(keyframes: list[tuple[float, int]],
                       tolerance: int = 4) -> tuple[tuple[float, int], ...]:
    """Drop keyframes that lie (within `tolerance` px) on the line between neighbours."""
    if len(keyframes) <= 2:
        return tuple(keyframes)
    kept = [keyframes[0]]
    for i in range(1, len(keyframes) - 1):
        (t0, x0), (t1, x1), (t2, x2) = kept[-1], keyframes[i], keyframes[i + 1]
        expected = x0 + (x2 - x0) * (t1 - t0) / (t2 - t0) if t2 > t0 else x1
        if abs(x1 - expected) > tolerance:
            kept.append(keyframes[i])
    kept.append(keyframes[-1])
    return tuple(kept)


def crop_x_expression(keyframes: tuple[tuple[float, int], ...]) -> str:
    """FFmpeg expression of t: linear interpolation between keyframes, flat at the ends.

    Built as a flat sum of gated segments (no deep nesting), e.g.
    lt(t,1)*100+gte(t,1)*lt(t,2)*(100+50*(t-1)/1)+gte(t,2)*150
    """
    if not keyframes:
        return "0"
    if len(keyframes) == 1:
        return str(keyframes[0][1])
    terms = [f"lt(t,{keyframes[0][0]})*{keyframes[0][1]}"]
    for (t0, x0), (t1, x1) in zip(keyframes, keyframes[1:], strict=False):
        if t1 <= t0:
            continue
        slope = (x1 - x0) / (t1 - t0)
        segment = f"{x0}" if x0 == x1 else f"({x0}+{slope:.4f}*(t-{t0}))"
        terms.append(f"gte(t,{t0})*lt(t,{t1})*{segment}")
    terms.append(f"gte(t,{keyframes[-1][0]})*{keyframes[-1][1]}")
    return "+".join(terms)


def choose_layout(samples: list[Sample]) -> tuple[Layout, float]:
    if not samples:
        return "fit", 0.0
    ratio = sum(s.face_x is not None for s in samples) / len(samples)
    return ("track" if ratio >= MIN_FACE_RATIO else "fit"), round(ratio, 3)


# --- Video analysis (OpenCV) -------------------------------------------------------------


@lru_cache(maxsize=1)
def _verified_model_path() -> str:
    data = MODEL_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ExternalServiceError(f"Face model checksum mismatch: {MODEL_PATH.name}")
    return str(MODEL_PATH)


def _pick_face(faces: np.ndarray | None, scale: float, previous_x: float | None) -> float | None:
    """Centre x of the most likely speaker: big, confident, and near the last position.

    The distance penalty grows quadratically (relative to 15% of the frame width), so with
    two similar faces (e.g. a two-person podcast) the camera stays put instead of bouncing,
    while a clearly bigger face elsewhere still wins.
    """
    if faces is None or len(faces) == 0:
        return None
    reach = 0.15 * DETECT_WIDTH * scale
    best, best_score = None, -1.0
    for f in faces:
        x, _, w, h = (float(v) for v in f[:4])
        centre = (x + w / 2) * scale
        score = w * h * float(f[-1])
        if previous_x is not None:
            score /= 1 + (abs(centre - previous_x) / reach) ** 2
        if score > best_score:
            best, best_score = centre, score
    return best


def analyze_clip(video: Path, start: float, end: float) -> CropPlan:
    """Sample the clip, detect faces and scene cuts, and plan the crop."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ExternalServiceError(f"OpenCV could not open {video.name}")
    try:
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        crop_w, crop_h = crop_size(src_w, src_h)
        if crop_w >= src_w:  # already vertical: nothing to track
            return CropPlan("fit", src_w, src_h, crop_w, crop_h, (), 0.0)

        scale = src_w / DETECT_WIDTH
        det_h = round(src_h / scale)
        detector = cv2.FaceDetectorYN.create(_verified_model_path(), "", (DETECT_WIDTH, det_h),
                                             0.7, 0.3, 5000)
        step = max(1, round(fps / SAMPLE_FPS))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        first_frame = int(round(cap.get(cv2.CAP_PROP_POS_FRAMES)))
        total = max(1, int((end - start) * fps))

        samples: list[Sample] = []
        previous_hist = None
        previous_x: float | None = None
        for i in range(total):
            if i % step:
                if not cap.grab():
                    break
                continue
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, (DETECT_WIDTH, det_h), interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            cut = previous_hist is not None and cv2.compareHist(
                previous_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > CUT_THRESHOLD
            previous_hist = hist
            _, faces = detector.detect(small)
            face_x = _pick_face(faces, scale, None if cut else previous_x)
            previous_x = face_x if face_x is not None else previous_x
            t = (first_frame + i) / fps - start
            samples.append(Sample(round(max(0.0, t), 3), face_x, cut))
    finally:
        cap.release()

    layout, ratio = choose_layout(samples)
    keyframes = (plan_track_keyframes(samples, src_width=src_w, crop_width=crop_w)
                 if layout == "track" else ())
    logger.info("Reframe %.1f-%.1fs: %d samples, faces in %.0f%%, layout=%s, %d keyframes",
                start, end, len(samples), ratio * 100, layout, len(keyframes))
    return CropPlan(layout, src_w, src_h, crop_w, crop_h, keyframes, ratio)
