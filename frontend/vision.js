const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);

async function refresh() {
  try {
    const response = await fetch('/api/vision/status');
    if (!response.ok) throw new Error('Status unavailable');
    const status = await response.json();
    $('#camera-state').textContent = status.error || (status.camera === 'connected' ? 'Camera connected · detections updating' : 'Starting camera and model…');
    $('#fps').textContent = status.fps ? status.fps.toFixed(1) : '—';
    $('#inference').textContent = status.inference_ms ? `${status.inference_ms} ms` : '—';
    $('#age').textContent = status.frame_age_ms == null ? '—' : `${status.frame_age_ms} ms`;
    $('#count').textContent = status.detections.length;
    $('#detections').innerHTML = status.detections.length ? status.detections.map((item) => `<div class="detection"><span>${escapeHTML(item.label)}</span><b>${Math.round(item.confidence * 100)}%</b></div>`).join('') : `<p class="muted">${status.error ? 'No detections — see camera status.' : 'No objects above threshold.'}</p>`;
    $('#error').hidden = !status.error;
    $('#error').textContent = status.error || '';
  } catch {
    $('#camera-state').textContent = 'Reconnecting to local server…';
  }
}

refresh();
setInterval(refresh, 750);
