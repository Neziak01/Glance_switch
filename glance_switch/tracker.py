"""Webcam capture + MediaPipe face landmarks on a background thread."""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import features
from .config import data_dir, resource_dir
from .gaze_model import GazeModel
from .smoothing import PointFilter

log = logging.getLogger(__name__)

MODEL_NAME = "face_landmarker.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_model() -> Path:
    """Return the face landmarker model, downloading it on first run if needed."""
    for candidate in (resource_dir() / "models" / MODEL_NAME, data_dir() / MODEL_NAME):
        if candidate.is_file() and _sha256(candidate) == MODEL_SHA256:
            return candidate
    target = data_dir() / MODEL_NAME
    log.info("downloading face model to %s", target)
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(MODEL_URL, timeout=60) as resp:
        tmp.write_bytes(resp.read())
    if _sha256(tmp) != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("downloaded face model failed its checksum")
    tmp.replace(target)
    return target


@dataclass
class Sample:
    t: float
    features: Optional[np.ndarray]  # None when no face is visible
    gaze: Optional[tuple[float, float]] = None  # smoothed, desktop pixels


class Tracker:
    """Runs the camera loop. Read `latest` from any thread."""

    def __init__(self, camera_index: int = 0, on_status: Callable[[str], None] | None = None):
        self.camera_index = camera_index
        self.on_status = on_status or (lambda msg: None)
        self.model: Optional[GazeModel] = None
        self.latest: Optional[Sample] = None
        self._filter = PointFilter()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._restart = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- control -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
        self._restart.set()

    def set_camera(self, index: int) -> None:
        self.camera_index = index
        self._restart.set()

    def set_model(self, model: Optional[GazeModel]) -> None:
        with self._lock:
            self.model = model
            self._filter.reset()

    def get_latest(self) -> Optional[Sample]:
        with self._lock:
            return self.latest

    # --- loop --------------------------------------------------------------
    def _run(self) -> None:
        try:
            landmarker = self._create_landmarker()
        except Exception as exc:  # noqa: BLE001 - surface any init failure to the user
            log.exception("face model failed to load")
            self.on_status(f"Face model failed to load: {exc}")
            return
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    with self._lock:
                        self.latest = None
                    self._restart.clear()
                    self._stop.wait(0.2)
                    continue
                self._capture_loop(landmarker)
        finally:
            landmarker.close()

    def _create_landmarker(self):
        import mediapipe as mp
        from mediapipe.tasks.python import vision

        options = vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(ensure_model())),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_facial_transformation_matrixes=True,
        )
        return vision.FaceLandmarker.create_from_options(options)

    def _open_camera(self):
        import cv2

        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.camera_index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        return cap

    def _capture_loop(self, landmarker) -> None:
        import cv2
        import mediapipe as mp

        self._restart.clear()
        cap = self._open_camera()
        if cap is None:
            self.on_status(f"Camera {self.camera_index} unavailable, retrying")
            self._stop.wait(2.0)
            return
        self.on_status("Tracking")
        last_ts = 0
        failures = 0
        try:
            while not self._stop.is_set() and not self._restart.is_set():
                ok, frame = cap.read()
                if not ok:
                    failures += 1
                    if failures > 30:
                        self.on_status("Camera stopped delivering frames, reopening")
                        return
                    time.sleep(0.03)
                    continue
                failures = 0
                now = time.monotonic()
                ts = max(int(now * 1000), last_ts + 1)
                last_ts = ts
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect_for_video(image, ts)
                self._publish(result, now, frame.shape[1] / frame.shape[0])
        finally:
            cap.release()

    def _publish(self, result, now: float, aspect: float) -> None:
        vec = None
        if result.face_landmarks and result.facial_transformation_matrixes:
            vec = features.extract(
                result.face_landmarks[0], result.facial_transformation_matrixes[0], aspect
            )
        with self._lock:
            gaze = None
            if vec is not None and self.model is not None:
                gx, gy = self.model.predict(vec)
                gaze = self._filter(gx, gy, now)
            elif vec is None:
                self._filter.reset()
            self.latest = Sample(t=now, features=vec, gaze=gaze)
