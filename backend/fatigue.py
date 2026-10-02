from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Any


WINDOW_SECONDS = 60.0


class FatigueEngine:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.samples: deque[tuple[float, bool, bool]] = deque()
        self.yawn_times: deque[float] = deque()
        self.long_closure_times: deque[float] = deque()
        self.blink_times: deque[float] = deque()
        self.closed_since: float | None = None
        self.last_alert: dict[str, float] = {}
        self.events: deque[dict[str, str | float]] = deque(maxlen=100)
        self.latest: dict[str, Any] = {
            "score": 0,
            "risk": "Calibrating",
            "perclos": 0.0,
            "yawns": 0,
            "blink_rate": 0.0,
            "long_closures": 0,
            "head_droop": 0.0,
            "face_detected": False,
            "calibrated": False,
            "ear": None,
            "mar": None,
            "pitch": None,
            "yaw": None,
            "roll": None,
            "alerts": [],
        }

    def update(self, vision: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        if vision.get("face_detected"):
            self._add_sample(vision, now)
        self._prune(now)

        alerts: list[dict[str, str | float]] = []
        if not vision.get("calibrated"):
            risk = "Calibrating"
            score = 0
            features = self._features()
        else:
            features = self._features()
            score = round(
                100
                * (
                    0.45 * features["perclos"]
                    + 0.25 * min(1.0, features["yawns"] / 3.0)
                    + 0.20 * features["head_droop"]
                    + 0.10 * min(1.0, features["long_closures"] / 3.0)
                )
            )
            risk = "Low" if score < 35 else "Elevated" if score <= 65 else "High"
            alert = self._maybe_alert(risk, score, now)
            if alert:
                alerts.append(alert)

        self.latest = {
            "score": score,
            "risk": risk,
            **features,
            "face_detected": bool(vision.get("face_detected")),
            "calibrated": bool(vision.get("calibrated")),
            "ear": vision.get("ear"),
            "mar": vision.get("mar"),
            "pitch": vision.get("pitch"),
            "yaw": vision.get("yaw"),
            "roll": vision.get("roll"),
            "alerts": alerts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {**self.latest, "events": list(self.events)}

    def _add_sample(self, vision: dict[str, Any], now: float) -> None:
        closed = bool(vision.get("eyes_closed"))
        drooping = bool(vision.get("head_droop"))
        self.samples.append((now, closed, drooping))

        if closed and self.closed_since is None:
            self.closed_since = now
        elif not closed and self.closed_since is not None:
            duration = now - self.closed_since
            if duration >= 1.0:
                self.long_closure_times.append(self.closed_since)
            elif duration >= 0.08:
                self.blink_times.append(now)
            self.closed_since = None

        if vision.get("yawn_event"):
            self.yawn_times.append(now)

    def _prune(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()
        while self.yawn_times and self.yawn_times[0] < cutoff:
            self.yawn_times.popleft()
        while self.long_closure_times and self.long_closure_times[0] < cutoff:
            self.long_closure_times.popleft()
        while self.blink_times and self.blink_times[0] < cutoff:
            self.blink_times.popleft()

    def _features(self) -> dict[str, float | int]:
        if not self.samples:
            return {
                "perclos": 0.0,
                "yawns": len(self.yawn_times),
                "blink_rate": 0.0,
                "long_closures": len(self.long_closure_times),
                "head_droop": 0.0,
            }
        closed_count = sum(sample[1] for sample in self.samples)
        droop_count = sum(sample[2] for sample in self.samples)
        return {
            "perclos": closed_count / len(self.samples),
            "yawns": len(self.yawn_times),
            "blink_rate": float(len(self.blink_times)),
            "long_closures": len(self.long_closure_times),
            "head_droop": droop_count / len(self.samples),
        }

    def _maybe_alert(self, risk: str, score: int, now: float) -> dict[str, str | float] | None:
        if risk == "Low":
            return None
        cooldown = 15.0 if risk == "High" else 30.0
        if now - self.last_alert.get(risk, float("-inf")) < cooldown:
            return None
        self.last_alert[risk] = now
        event: dict[str, str | float] = {
            "risk": risk,
            "score": score,
            "message": f"{risk} fatigue indicators detected. Take a safe break when possible.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.events.appendleft(event)
        return event