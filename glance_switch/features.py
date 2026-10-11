"""Turn MediaPipe face landmarks into a compact gaze feature vector.

The vector mixes head pose (where the face points) with iris position inside
each eye (where the eyes point relative to the head). A per-user calibration
(see gaze_model.py) maps it to screen coordinates, so the exact scale and
sign conventions here do not matter as long as they are consistent.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

# MediaPipe Face Mesh indices (478-point model with irises).
# Eye A: landmarks around the subject's right eye (image left when unmirrored).
EYE_A = {"outer": 33, "inner": 133, "top": 159, "bottom": 145, "iris": 468}
# Eye B: subject's left eye.
EYE_B = {"outer": 263, "inner": 362, "top": 386, "bottom": 374, "iris": 473}

FEATURE_NAMES = (
    "yaw",
    "pitch",
    "roll",
    "head_x",
    "head_y",
    "head_z",
    "iris_a_h",
    "iris_a_v",
    "iris_b_h",
    "iris_b_v",
)
N_FEATURES = len(FEATURE_NAMES)


def head_angles(matrix: np.ndarray) -> tuple[float, float, float]:
    """Yaw, pitch and roll (radians) from a 4x4 facial transformation matrix."""
    r = np.asarray(matrix, dtype=float)[:3, :3]
    forward = r @ np.array([0.0, 0.0, 1.0])
    yaw = math.atan2(forward[0], forward[2])
    pitch = math.atan2(forward[1], math.hypot(forward[0], forward[2]))
    right = r @ np.array([1.0, 0.0, 0.0])
    roll = math.atan2(right[1], right[0])
    return yaw, pitch, roll


def _xy(landmarks: Sequence, idx: int) -> np.ndarray:
    p = landmarks[idx]
    return np.array([p.x, p.y], dtype=float)


def iris_offset(landmarks: Sequence, eye: dict, aspect: float) -> tuple[float, float]:
    """Iris position inside the eye, normalised by eye width.

    Returns (h, v): h is 0 at the outer corner and 1 at the inner corner,
    v is the signed distance from the eye's mid-line (positive = down).
    `aspect` is image width / height, so x and y share the same units.
    """
    scale = np.array([aspect, 1.0])
    outer = _xy(landmarks, eye["outer"]) * scale
    inner = _xy(landmarks, eye["inner"]) * scale
    top = _xy(landmarks, eye["top"]) * scale
    bottom = _xy(landmarks, eye["bottom"]) * scale
    iris = _xy(landmarks, eye["iris"]) * scale

    axis = inner - outer
    width = float(np.linalg.norm(axis))
    if width < 1e-6:
        return 0.5, 0.0
    unit = axis / width
    normal = np.array([-unit[1], unit[0]])
    h = float(np.dot(iris - outer, unit) / width)
    mid = (top + bottom) / 2.0
    v = float(np.dot(iris - mid, normal) / width)
    return h, v


def extract(
    landmarks: Sequence,
    matrix: Optional[np.ndarray],
    aspect: float = 4.0 / 3.0,
) -> Optional[np.ndarray]:
    """Build the feature vector, or None if the face data is unusable."""
    if landmarks is None or len(landmarks) < 478 or matrix is None:
        return None
    yaw, pitch, roll = head_angles(matrix)
    m = np.asarray(matrix, dtype=float)
    head_x, head_y, head_z = m[0, 3], m[1, 3], m[2, 3]
    a_h, a_v = iris_offset(landmarks, EYE_A, aspect)
    b_h, b_v = iris_offset(landmarks, EYE_B, aspect)
    vec = np.array(
        [yaw, pitch, roll, head_x, head_y, head_z, a_h, a_v, b_h, b_v], dtype=float
    )
    if not np.all(np.isfinite(vec)):
        return None
    return vec
