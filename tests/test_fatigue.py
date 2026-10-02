import unittest

from backend.fatigue import FatigueEngine


class FatigueEngineTests(unittest.TestCase):
    def test_un_calibrated_session_stays_calibrating(self):
        engine = FatigueEngine()

        state = engine.update({"face_detected": False, "calibrated": False}, now=1.0)

        self.assertEqual(state["risk"], "Calibrating")
        self.assertEqual(state["score"], 0)

    def test_high_alert_obeys_cooldown(self):
        engine = FatigueEngine()
        vision = {
            "face_detected": True,
            "calibrated": True,
            "eyes_closed": True,
            "head_droop": True,
            "yawn_event": True,
        }

        first = engine.update(vision, now=10.0)
        engine.update(vision, now=10.2)
        engine.update(vision, now=10.4)
        second = engine.update(vision, now=11.0)

        self.assertEqual(first["risk"], "High")
        self.assertEqual(first["score"], 73)
        self.assertEqual(second["score"], 90)
        self.assertEqual(len(first["alerts"]), 1)
        self.assertEqual(len(second["alerts"]), 0)
        self.assertEqual(len(second["events"]), 1)


if __name__ == "__main__":
    unittest.main()