// Response viewer
const MAX_DISPLAY = 1024 * 1024; // 1 MB
let _fullBody = '';

export async function showResponse(resp, elapsed) {
  const panel = document.getElementById('response-panel');
  panel.classList.remove('hidden');

  // Status badge
  const badge = document.getElementById('resp-status-badge');
  badge.textContent = resp.status;
  badge.className = 'status-badge';
  if (resp.status < 300) badge.classList.add('s2xx');
  else if (resp.status < 500) badge.classList.add('s4xx');
  else badge.classList.add('s5xx');

  // Timing
  document.getElementById('resp-time').textContent = `${elapsed} ms`;

  // Headers
  const headersSection = document.getElementById('resp-headers-section');
  const headerLines = [];
  for (const [k, v] of resp.headers.entries()) {
    headerLines.push(`${k}: ${v}`);
  }
  headersSection.textContent = headerLines.join('\n');

  // Body
  const raw = await resp.text();
  _fullBody = raw;
  const bodyEl = document.getElementById('resp-body');
  const truncated = document.getElementById('resp-truncated');

  let display = raw;
  try { display = JSON.stringify(JSON.parse(raw), null, 2); } catch {}

  if (display.length > MAX_DISPLAY) {
    bodyEl.textContent = display.slice(0, MAX_DISPLAY);
    truncated.classList.remove('hidden');
  } else {
    bodyEl.textContent = display;
    truncated.classList.add('hidden');
  }
}

export function initResponseViewer() {
  document.getElementById('toggle-resp-headers').addEventListener('click', () => {
    document.getElementById('resp-headers-section').classList.toggle('hidden');
  });
  document.getElementById('show-full-resp').addEventListener('click', () => {
    document.getElementById('resp-body').textContent = _fullBody;
    document.getElementById('resp-truncated').classList.add('hidden');
  });
}
