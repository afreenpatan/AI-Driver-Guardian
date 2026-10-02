from __future__ import annotations

import time
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision


RIGHT_EYE = [33, 160, 158, 133, 153, 144]
LEFT_EYE = [362, 385, 387, 263, 373, 380]
POSE_LANDMARKS = [1, 152, 33, 263, 61, 291]
POSE_MODEL_POINTS = np.array(
    [
        (0.0, 0.0, 0.0),
        (0.0, -63.6, -12.5),
        (-43.3, 32.7, -26.0),
        (43.3, 32.7, -26.0),
        (-28.9, -28.9, -24.1),
        (28.9, -28.9, -24.1),
    ],
    dtype=np.float64,
)


def eye_aspect_ratio(points: np.ndarray) -> float:
    vertical = np.linalg.norm(points[1] - points[5]) + np.linalg.norm(
        points[2] - points[4]
    )
    horizontal = 2.0 * np.linalg.norm(points[0] - points[3])
    return float(vertical / horizontal) if horizontal > 0 else 0.0


class VisionProcessor:
    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or Path(__file__).with_name("face_landmarker.task")
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"Face Landmarker model not found at {self.model_path}. "
                "See the project README to download it."
            )

        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(self.model_path)),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        self.reset()

    def reset(self) -> None:
        self.calibration_started: float | None = None
        self.ear_samples: deque[float] = deque(maxlen=100)
        self.pitch_samples: deque[float] = deque(maxlen=100)
        self.ear_baseline: float | None = None
        self.pitch_baseline: float | None = None
        self.yawn_started: float | None = None
        self.yawn_active = False
        self.droop_started: float | None = None
        self._last_metrics: dict[str, float | bool | None] = {
            "ear": None,
            "mar": None,
            "pitch": None,
            "yaw": None,
            "roll": None,
        }

    def close(self) -> None:
        self.landmarker.close()

    def process(self, jpeg_bytes: bytes) -> dict[str, float | bool | None]:
        image_array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode the camera frame as JPEG")

        height, width = frame.shape[:2]
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        result = self.landmarker.detect(mp_image)
        now = time.monotonic()

        if not result.face_landmarks:
            return {
                "face_detected": False,
                "calibrated": self.ear_baseline is not None,
                "ear": self._last_metrics["ear"],
                "mar": self._last_metrics["mar"],
                "pitch": self._last_metrics["pitch"],
                "yaw": self._last_metrics["yaw"],
                "roll": self._last_metrics["roll"],
                "eyes_closed": False,
                "yawn_event": False,
                "head_droop": False,
            }

        landmarks = result.face_landmarks[0]
        points = np.array([(item.x * width, item.y * height) for item in landmarks])
        ear = (eye_aspect_ratio(points[RIGHT_EYE]) + eye_aspect_ratio(points[LEFT_EYE])) / 2
        mouth_width = np.linalg.norm(points[78] - points[308])
        mar = float(np.linalg.norm(points[13] - points[14]) / mouth_width) if mouth_width else 0.0
        pitch, yaw, roll = self._head_pose(points, width, height)
        self._last_metrics = {"ear": ear, "mar": mar, "pitch": pitch, "yaw": yaw, "roll": roll}

        if self.ear_baseline is None:
            self._calibrate(ear, pitch, now)

        calibrated = self.ear_baseline is not None
        eyes_closed = calibrated and ear < self.ear_baseline * 0.75
        yawn_event = self._track_yawn(mar, now)
        head_droop = self._track_droop(pitch, now) if calibrated else False
        return {
            "face_detected": True,
            "calibrated": calibrated,
            "ear": ear,
            "mar": mar,
            "pitch": pitch,
            "yaw": yaw,
            "roll": roll,
            "eyes_closed": bool(eyes_closed),
            "yawn_event": yawn_event,
            "head_droop": head_droop,
        }

    def _calibrate(self, ear: float, pitch: float, now: float) -> None:
        if self.calibration_started is None:
            self.calibration_started = now
        self.ear_samples.append(ear)
        self.pitch_samples.append(pitch)
        elapsed = now - self.calibration_started
        if elapsed >= 3.0 and len(self.ear_samples) >= 20:
            self.ear_baseline = float(np.median(self.ear_samples))
            self.pitch_baseline = float(np.median(self.pitch_samples))

    def _track_yawn(self, mar: float, now: float) -> bool:
        if mar >= 0.6:
            if self.yawn_started is None:
                self.yawn_started = now
            if now - self.yawn_started >= 1.5 and not self.yawn_active:
                self.yawn_active = True
                return True
        else:
            self.yawn_started = None
            self.yawn_active = False
        return False

    def _track_droop(self, pitch: float, now: float) -> bool:
        is_drooping = self.pitch_baseline is not None and pitch < self.pitch_baseline - 15.0
        if is_drooping:
            if self.droop_started is None:
                self.droop_started = now
            return now - self.droop_started >= 1.0
        self.droop_started = None
        return False

    @staticmethod
    def _head_pose(points: np.ndarray, width: int, height: int) -> tuple[float, float, float]:
        focal_length = float(width)
        camera_matrix = np.array(
            [[focal_length, 0, width / 2], [0, focal_length, height / 2], [0, 0, 1]],
            dtype=np.float64,
        )
        success, rotation_vector, _ = cv2.solvePnP(
            POSE_MODEL_POINTS,
            points[POSE_LANDMARKS].astype(np.float64),
            camera_matrix,
            np.zeros((4, 1), dtype=np.float64),
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return 0.0, 0.0, 0.0
        rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
        projection = np.hstack((rotation_matrix, np.zeros((3, 1), dtype=np.float64)))
        angles = cv2.decomposeProjectionMatrix(projection)[6].flatten()
        pitch = float(angles[0])
        if pitch < -90.0:
            pitch += 180.0
        elif pitch > 90.0:
            pitch -= 180.0
        return pitch, float(angles[1]), float(angles[2])