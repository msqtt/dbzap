// Service status card
import { apiFetch } from './api.js';

export async function updateHealthDot() {
  const dot = document.getElementById('health-dot');
  try {
    const { resp } = await apiFetch('/healthz');
    dot.className = 'status-dot ' + (resp.ok ? 'ok' : 'error');
  } catch {
    dot.className = 'status-dot error';
  }
}

export async function renderStatusCards(container) {
  container.innerHTML = '';
  try {
    const [liveRes, readyRes] = await Promise.all([
      apiFetch('/healthz'),
      apiFetch('/healthz/ready'),
    ]);
    const live  = liveRes.resp.ok  ? await liveRes.resp.json()  : null;
    const ready = readyRes.resp.ok ? await readyRes.resp.json() : null;

    container.appendChild(makeCard('Uptime', live
      ? formatUptime(live.uptime_seconds)
      : '—', '', live ? 'ok' : 'err'));

    container.appendChild(makeCard('Liveness',
      liveRes.resp.ok ? 'OK' : 'DOWN', '',
      liveRes.resp.ok ? 'ok' : 'err'));

    container.appendChild(makeCard('Readiness',
      readyRes.resp.ok ? 'OK' : 'DOWN', '',
      readyRes.resp.ok ? 'ok' : 'err'));

    // Try detail endpoint for extra info
    try {
      const { resp: detailResp } = await apiFetch('/healthz/detail');
      if (detailResp.ok) {
        const detail = await detailResp.json();
        container.appendChild(makeCard('API Mode', detail.api_mode ?? '—'));
        container.appendChild(makeCard('Tables', String(detail.introspection?.table_count ?? '—')));
      }
    } catch {}
  } catch {
    container.appendChild(makeCard('Status', 'Unavailable', '', 'err'));
  }
}

function makeCard(label, value, sub = '', cls = '') {
  const card = document.createElement('div');
  card.className = 'dash-card';
  card.innerHTML = `
    <div class="dash-card-label">${label}</div>
    <div class="dash-card-value ${cls}">${value}</div>
    ${sub ? `<div class="dash-card-sub">${sub}</div>` : ''}
  `;
  return card;
}

function formatUptime(s) {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}
