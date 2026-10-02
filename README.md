# AI Driver Guardian

A local webcam prototype that estimates fatigue indicators from facial landmarks and displays a live dashboard. The signals are estimates, not a diagnosis or a guarantee of driver safety.

## Requirements

- Python 3.10 or 3.11
- A webcam and a modern browser with English speech synthesis
- The MediaPipe Face Landmarker model asset

Download the official model into `backend/face_landmarker.task` from PowerShell in the project folder:

```powershell
Invoke-WebRequest `
  -Uri "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task" `
  -OutFile "backend/face_landmarker.task"
```

## Run

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Set-Location backend
python -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>, grant camera permission, and keep a neutral gaze during the 3-second baseline calibration. The frontend sends reduced-size JPEG frames to the local FastAPI process at up to about 15 frames per second, skipping frames while inference is busy to keep updates current. Frames are not saved or sent to a remote service.

If the PowerShell execution policy prevents environment activation, run `.venv\Scripts\python.exe -m pip install -r backend\requirements.txt`, then `.venv\Scripts\python.exe -m uvicorn main:app --reload` from `backend`.

## API

- `GET /health` reports service and model readiness.
- `GET /status` returns the latest score and window metrics.
- `GET /events` returns the alert history for the current session.
- `POST /reset` starts a new calibration session.
- `WS /ws` accepts JSON messages shaped as `{"type":"frame","image":"<base64 JPEG>"}` and returns state messages.

## Signals and limitations

EAR uses the supplied six-point eye contours and a per-session 75% baseline threshold. Yawns require MAR >= 0.6 for 1.5 seconds. Head droop is a relative pitch drop of 15 degrees sustained for one second. The 60-second score uses PERCLOS, yawns (normalized at three per window), head-droop time, and long closures (normalized at three per window), with weights 0.45, 0.25, 0.20, and 0.10. Blink count is shown as a diagnostic but is not part of that score.

Lighting, glasses, camera angle, and individual differences can affect landmark quality. Thresholds are prototype defaults and need real-world validation before any safety-critical use. English browser speech synthesis is preferred; the optional `pyttsx3` endpoint is a local-machine English fallback and depends on installed OS voices. Use **Test voice** in the dashboard to check playback.

## Tests

From the project folder, run:

```powershell
python -m unittest discover -s tests
```