const video = document.querySelector('#camera');
const canvas = document.createElement('canvas');
const context = canvas.getContext('2d', { alpha: false });
const startButton = document.querySelector('#start-button');
const stopButton = document.querySelector('#stop-button');
const cameraStage = document.querySelector('#camera-stage');
const toast = document.querySelector('#toast');
let stream = null;
let socket = null;
let frameTimer = null;
let frameInFlight = false;
let sessionStarted = null;
let clockTimer = null;
let toastTimer = null;
let spokenEventKeys = new Set();

function setConnection(connected, label = connected ? 'Connected' : 'Offline') {
  const node = document.querySelector('#connection-label');
  node.textContent = label;
  node.parentElement.classList.toggle('connected', connected);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 6000);
}

function setRisk(risk, score) {
  const badge = document.querySelector('#risk-badge');
  const ring = document.querySelector('#score-ring');
  const scoreLabel = document.querySelector('#score');
  badge.className = `risk-badge risk-${risk.toLowerCase()}`;
  badge.textContent = risk.toUpperCase();
  ring.style.setProperty('--score', String(score));
  ring.style.background = `conic-gradient(${risk === 'High' ? 'var(--coral)' : risk === 'Elevated' ? 'var(--amber)' : 'var(--green)'} ${score}%, #344239 0)`;
  scoreLabel.textContent = risk === 'Calibrating' ? '--' : String(score);
  document.querySelector('#risk-copy').textContent = {
    Calibrating: 'Looking for a face to establish your baseline.',
    Low: 'No elevated fatigue indicators in the current window.',
    Elevated: 'Some fatigue indicators are present. Stay attentive.',
    High: 'High fatigue indicators. Stop safely and rest.',
  }[risk] || 'Waiting for camera measurements.';
}

function formatTime(timestamp) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? '--:--' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function renderEvents(events = []) {
  const list = document.querySelector('#event-list');
  document.querySelector('#event-count').textContent = `${events.length} EVENT${events.length === 1 ? '' : 'S'}`;
  if (!events.length) {
    list.innerHTML = '<li class="empty-event">No alerts this session</li>';
    return;
  }
  list.replaceChildren(...events.slice(0, 9).map((event) => {
    const item = document.createElement('li');
    item.className = `event-item ${event.risk.toLowerCase()}`;
    const copy = document.createElement('div');
    copy.className = 'event-copy';
    const title = document.createElement('strong');
    title.textContent = `${event.risk} fatigue · ${event.score}`;
    const message = document.createElement('p');
    message.textContent = event.message;
    const time = document.createElement('time');
    time.textContent = formatTime(event.timestamp);
    copy.append(title, message, time);
    item.append(copy);
    return item;
  }));
}

function updateDashboard(state) {
  setRisk(state.risk || 'Calibrating', state.score || 0);
  document.querySelector('#perclos').textContent = `${Math.round((state.perclos || 0) * 100)}%`;
  document.querySelector('#yawns').textContent = state.yawns ?? 0;
  document.querySelector('#head-droop').textContent = `${Math.round((state.head_droop || 0) * 100)}%`;
  document.querySelector('#long-closures').textContent = state.long_closures ?? 0;
  document.querySelector('#blink-rate').textContent = state.blink_rate ?? 0;
  document.querySelector('#ear-value').textContent = state.ear == null ? '--' : Number(state.ear).toFixed(2);
  document.querySelector('#mar-value').textContent = state.mar == null ? '--' : Number(state.mar).toFixed(2);
  document.querySelector('#pitch-value').textContent = state.pitch == null ? '--' : `${Number(state.pitch).toFixed(0)}°`;
  document.querySelector('#yaw-value').textContent = state.yaw == null ? '--' : `${Number(state.yaw).toFixed(0)}°`;
  document.querySelector('#roll-value').textContent = state.roll == null ? '--' : `${Number(state.roll).toFixed(0)}°`;
  document.querySelector('#calibration-label').textContent = state.calibrated
    ? 'Baseline established · monitoring'
    : state.face_detected ? 'Calibrating · keep a neutral gaze' : 'Center your face in the frame';
  const face = document.querySelector('#face-indicator');
  face.textContent = state.face_detected ? 'FACE DETECTED' : 'FACE NOT DETECTED';
  face.classList.toggle('detected', Boolean(state.face_detected));
  renderEvents(state.events || []);
  (state.alerts || []).forEach((event) => speakAlert(event));
}

function speakAlert(event) {
  const key = `${event.risk}:${event.timestamp}`;
  if (spokenEventKeys.has(key)) return;
  spokenEventKeys.add(key);
  speakEnglish(event.message);
}

function speakEnglish(message) {
  if (!('speechSynthesis' in window)) {
    speakWithServer(message);
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(message);
  const voice = window.speechSynthesis.getVoices().find((item) => item.lang.toLowerCase().startsWith('en'));
  utterance.lang = voice?.lang || 'en-US';
  if (voice) utterance.voice = voice;
  utterance.rate = 0.92;
  utterance.onerror = (event) => {
    if (event.error !== 'canceled') speakWithServer(message);
  };
  window.speechSynthesis.speak(utterance);
}

function speakWithServer(message) {
  fetch('/voice', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: message }),
  })
    .then((response) => response.json())
    .then((result) => {
      if (result.status !== 'spoken') showToast('English voice playback is unavailable on this device.');
    })
    .catch(() => showToast('English voice playback is unavailable on this device.'));
}

function sendFrame() {
  if (frameInFlight || !socket || socket.readyState !== WebSocket.OPEN || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
  const scale = Math.min(480 / video.videoWidth, 360 / video.videoHeight, 1);
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const jpeg = canvas.toDataURL('image/jpeg', 0.64).split(',', 2)[1];
  try {
    frameInFlight = true;
    socket.send(JSON.stringify({ type: 'frame', image: jpeg }));
  } catch (error) {
    frameInFlight = false;
    throw error;
  }
}

async function startSession() {
  startButton.disabled = true;
  try {
    await fetch('/reset', { method: 'POST' });
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
    });
    video.srcObject = stream;
    await video.play();
    cameraStage.classList.add('active');
    document.querySelector('#camera-state').textContent = 'Camera active';
    spokenEventKeys = new Set();
    frameInFlight = false;
    setRisk('Calibrating', 0);

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${protocol}//${location.host}/ws`);
    socket.addEventListener('open', () => {
      setConnection(true);
      frameTimer = setInterval(sendFrame, 67);
      sendFrame();
      stopButton.disabled = false;
    });
    socket.addEventListener('message', (message) => {
      frameInFlight = false;
      const data = JSON.parse(message.data);
      if (data.type === 'state') updateDashboard(data);
      else if (data.type === 'error') showToast(data.message);
    });
    socket.addEventListener('close', () => {
      frameInFlight = false;
      setConnection(false);
      if (stream) stopSession(false);
    });
    socket.addEventListener('error', () => showToast('Could not connect to the local monitoring service.'));
    sessionStarted = Date.now();
    clockTimer = setInterval(updateSessionClock, 1000);
    updateSessionClock();
  } catch (error) {
    stopSession(false);
    showToast(error.name === 'NotAllowedError' ? 'Camera permission is required to monitor.' : error.message || 'Could not start camera.');
    startButton.disabled = false;
  }
}

function stopSession(closeSocket = true) {
  clearInterval(frameTimer);
  clearInterval(clockTimer);
  frameTimer = null;
  clockTimer = null;
  frameInFlight = false;
  if (closeSocket && socket && socket.readyState < WebSocket.CLOSING) socket.close();
  socket = null;
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  video.srcObject = null;
  cameraStage.classList.remove('active');
  document.querySelector('#camera-state').textContent = 'Camera paused';
  document.querySelector('#session-time').textContent = 'SESSION 00:00';
  setConnection(false);
  startButton.disabled = false;
  stopButton.disabled = true;
  sessionStarted = null;
}

function updateSessionClock() {
  if (!sessionStarted) return;
  const seconds = Math.floor((Date.now() - sessionStarted) / 1000);
  const minutes = String(Math.floor(seconds / 60)).padStart(2, '0');
  document.querySelector('#session-time').textContent = `SESSION ${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

async function checkHealth() {
  try {
    const response = await fetch('/health');
    const health = await response.json();
    if (!health.model_ready && health.model_error) {
      setConnection(false, 'Model missing');
      document.querySelector('#camera-state').textContent = 'Model file required';
      document.querySelector('#calibration-label').textContent = 'Add backend/face_landmarker.task to enable monitoring';
    }
  } catch {
    setConnection(false, 'Service offline');
  }
}

startButton.addEventListener('click', startSession);
stopButton.addEventListener('click', () => stopSession());
document.querySelector('#test-voice-button').addEventListener('click', () => {
  speakEnglish('Driver Guardian voice test. English alerts are enabled.');
});
window.addEventListener('beforeunload', () => stopSession());
checkHealth();
