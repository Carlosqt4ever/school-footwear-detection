const feed = document.getElementById('camera-feed');
const placeholder = document.getElementById('camera-placeholder');
const start = document.getElementById('start-camera');
const stop = document.getElementById('stop-camera');
const error = document.getElementById('camera-error');
let attached = false;
let busy = false;
let pollPending = false;
let failures = 0;
function showError(message) { error.textContent = message || ''; error.hidden = !message; }
function render(state) {
  start.disabled = state.running || busy;
  stop.disabled = !state.running || busy;
  document.getElementById('camera-status').textContent = state.running ? '● Camera active' : 'Stopped';
  document.getElementById('fps').textContent = `${state.fps} FPS`;
  const names = {valid: 'Valid footwear', not_valid: 'Invalid footwear'};
  document.getElementById('result-label').textContent = names[state.label] || state.label;
  const panel = document.querySelector('.result-panel');
  panel.className = `panel result-panel ${['valid','not_valid'].includes(state.label) ? state.label : 'uncertain'}`;
  document.getElementById('result-icon').textContent = state.label === 'valid' ? '✓' : state.label === 'not_valid' ? '!' : '—';
  document.getElementById('confidence').textContent = state.confidence ? `${state.confidence}% model confidence` : 'Waiting for a detection';
  document.getElementById('confidence-bar').style.width = `${state.confidence}%`;
  showError(state.error);
  if (state.running && state.fps > 0) { feed.hidden = false; placeholder.hidden = true; }
  if (state.running && !attached) {
    feed.src = `/video?t=${Date.now()}`;
    attached = true;
  }
  if (!state.running) {
    feed.removeAttribute('src');
    attached = false;
    feed.hidden = true;
    placeholder.hidden = false;
  }
}
feed.onload = () => { feed.hidden = false; placeholder.hidden = true; };
feed.onerror = () => { attached = false; feed.hidden = true; placeholder.hidden = false; };
async function camera(action) {
  busy = true;
  start.disabled = stop.disabled = true;
  try {
    const response = await fetch(`/api/camera/${action}`, {method:'POST', headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}});
    if (response.status === 401) { location.href = '/login'; return; }
    if (!response.ok) throw new Error('Unable to change camera state. Reload the page and try again.');
    busy = false;
    render(await response.json());
  } catch (err) { showError(err.message); }
  finally { busy = false; poll(); }
}
async function poll() {
  if (pollPending) return;
  pollPending = true;
  try {
    const response = await fetch('/api/status');
    if (response.status === 401) { location.href = '/login'; return; }
    if (!response.ok) throw new Error('Unable to read camera status.');
    render(await response.json());
    failures = 0;
  } catch (err) {
    failures++;
    if (failures >= 3) showError('Connection to the local app was lost. Check that the server is still running.');
  } finally { pollPending = false; }
}
start.addEventListener('click', () => camera('start'));
stop.addEventListener('click', () => camera('stop'));
poll();
setInterval(poll, 1200);

