// Dashboard: polling loop, HTTP overview, per-endpoint table, DB health, sparklines
import { apiFetch } from './api.js';
import { parsePrometheus, estimatePercentile } from './metrics-parser.js';
import { drawSparkline, RingBuffer } from './sparkline.js';
import { renderStatusCards } from './health.js';
import { selectEndpointById } from './sidebar.js';
import { switchTab } from './app.js';

const POLL_MS = 5000;
const RETRY_BACKOFF = [5000, 10000, 30000];

let _timer = null;
let _paused = false;
let _retryIdx = 0;
let _pollInFlight = false;
let _prevTotals = null; // for req/rate delta

const _sparkReqRate   = new RingBuffer(60);
const _sparkErrRate   = new RingBuffer(60);
const _sparkP95       = new RingBuffer(60);
const _sparkPool      = new RingBuffer(60);

// Sort state
let _sortCol = 'requests';
let _sortAsc = false;
let _tableFilter = '';

export function initDashboard() {
  document.getElementById('pause-btn').addEventListener('click', togglePause);
  document.getElementById('endpoint-table-search').addEventListener('input', e => {
    _tableFilter = e.target.value.toLowerCase();
    // re-render with cached data (handled on next poll; immediate re-render if data present)
    if (_lastMetrics) renderEndpointTable(_lastMetrics);
  });
  document.querySelectorAll('#endpoint-table th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (_sortCol === col) _sortAsc = !_sortAsc;
      else { _sortCol = col; _sortAsc = false; }
      document.querySelectorAll('#endpoint-table th').forEach(h => {
        h.classList.remove('sort-asc', 'sort-desc');
      });
      th.classList.add(_sortAsc ? 'sort-asc' : 'sort-desc');
      if (_lastMetrics) renderEndpointTable(_lastMetrics);
    });
  });
  // Visibility API — pause when hidden
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) pausePolling();
    else if (!_paused) resumePolling();
  });
}

let _lastMetrics = null;

export function startPolling() {
  poll();
  _timer = setInterval(() => { if (!_paused && !_pollInFlight) poll(); }, POLL_MS);
}

export function stopPolling() {
  clearInterval(_timer);
  _timer = null;
}

function togglePause() {
  _paused = !_paused;
  document.getElementById('pause-btn').textContent = _paused ? 'Resume' : 'Pause';
  if (!_paused) poll();
}

function pausePolling() { _paused = true; document.getElementById('pause-btn').textContent = 'Resume'; }
function resumePolling() { _paused = false; document.getElementById('pause-btn').textContent = 'Pause'; poll(); }

async function poll() {
  if (_pollInFlight) return;
  _pollInFlight = true;
  try {
    const { resp } = await apiFetch('/metrics');
    if (!resp.ok) throw new Error('non-ok');
    const text = await resp.text();
    const parsed = parsePrometheus(text);
    _lastMetrics = parsed;
    _retryIdx = 0;
    document.getElementById('metrics-error-banner').classList.add('hidden');
    await renderDashboard(parsed);
  } catch {
    const delay = RETRY_BACKOFF[Math.min(_retryIdx++, RETRY_BACKOFF.length - 1)];
    document.getElementById('metrics-error-banner').classList.remove('hidden');
    clearInterval(_timer);
    _timer = setTimeout(() => {
      _timer = setInterval(() => { if (!_paused && !_pollInFlight) poll(); }, POLL_MS);
      poll();
    }, delay);
  } finally {
    _pollInFlight = false;
  }
}

async function renderDashboard(m) {
  await renderStatusCards(document.getElementById('status-cards'));
  renderHttpCards(m);
  renderEndpointTable(m);
  renderDbHealth(m);
  renderSparklines(m);
}

function renderHttpCards(m) {
  const container = document.getElementById('http-cards');
  container.innerHTML = '';

  // Total requests across all labels
  const reqCounter = m.counters['http_requests_total'] ?? {};
  let totalReqs = 0, totalErrors = 0;
  for (const [key, val] of Object.entries(reqCounter)) {
    totalReqs += val;
    if (key.includes('status="4') || key.includes('status="5')) totalErrors += val;
  }

  // Request rate (delta since last poll)
  let reqRate = 0;
  if (_prevTotals !== null) {
    reqRate = Math.max(0, (totalReqs - _prevTotals.reqs) / (POLL_MS / 1000));
  }
  _prevTotals = { reqs: totalReqs };
  _sparkReqRate.push(reqRate);

  const errRate = totalReqs > 0 ? (totalErrors / totalReqs) * 100 : 0;
  _sparkErrRate.push(errRate);

  const inProgress = m.gauges['http_requests_in_progress'] ?? 0;

  // p95 latency — aggregate across all paths
  const durHist = m.histograms['http_request_duration_seconds'] ?? {};
  let p95 = 0;
  if (Object.keys(durHist).length > 0) {
    const merged = { buckets: [], count: 0, sum: 0 };
    for (const h of Object.values(durHist)) {
      merged.count += h.count;
      merged.sum += h.sum;
      for (const [le, cnt] of h.buckets) {
        const existing = merged.buckets.find(b => b[0] === le);
        if (existing) existing[1] += cnt;
        else merged.buckets.push([le, cnt]);
      }
    }
    p95 = estimatePercentile(merged.buckets, 0.95);
  }
  _sparkP95.push(p95);

  const errCls = errRate < 1 ? 'ok' : errRate < 5 ? 'warn' : 'err';
  const prevRate = _sparkReqRate.data.at(-2);
  const trendCls = prevRate == null ? '' : reqRate > prevRate ? 'trend-up' : reqRate < prevRate ? 'trend-down' : '';

  container.appendChild(makeCard('Request Rate', `${reqRate.toFixed(2)} req/s`, '', trendCls));
  container.appendChild(makeCard('Active Requests', String(inProgress)));
  container.appendChild(makeCard('Error Rate', `${errRate.toFixed(1)}%`, '', errCls));
  container.appendChild(makeCard('p95 Latency', `${p95.toFixed(1)} ms`));
}

function renderEndpointTable(m) {
  const reqCounter = m.counters['http_requests_total'] ?? {};
  const durHist    = m.histograms['http_request_duration_seconds'] ?? {};

  // Build row data keyed by method+path
  const rows = {};
  for (const [key, val] of Object.entries(reqCounter)) {
    const method = extract(key, 'method');
    const path   = extract(key, 'path');
    const status = extract(key, 'status');
    const rk = `${method}||${path}`;
    rows[rk] ??= { path, method, requests: 0, errors: 0, p50: 0, p95: 0, p99: 0, avg: 0 };
    rows[rk].requests += val;
    if (status.startsWith('4') || status.startsWith('5')) rows[rk].errors += val;
  }
  for (const [key, h] of Object.entries(durHist)) {
    const method = extract(key, 'method');
    const path   = extract(key, 'path');
    const rk = `${method}||${path}`;
    if (!rows[rk]) continue;
    rows[rk].p50 = estimatePercentile(h.buckets, 0.50);
    rows[rk].p95 = estimatePercentile(h.buckets, 0.95);
    rows[rk].p99 = estimatePercentile(h.buckets, 0.99);
    rows[rk].avg = h.count > 0 ? (h.sum / h.count) * 1000 : 0;
  }

  let data = Object.values(rows).filter(r =>
    !_tableFilter || r.path.toLowerCase().includes(_tableFilter)
  );
  data.sort((a, b) => {
    const av = a[_sortCol] ?? 0, bv = b[_sortCol] ?? 0;
    return _sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
  });

  const tbody = document.getElementById('endpoint-table-body');
  const empty = document.getElementById('endpoint-table-empty');
  tbody.innerHTML = '';
  if (data.length === 0) { empty.classList.remove('hidden'); return; }
  empty.classList.add('hidden');
  for (const r of data) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${r.path}</td>
      <td><span class="method-tag ${r.method}">${r.method}</span></td>
      <td>${r.requests}</td>
      <td>${r.errors}</td>
      <td>${r.p50.toFixed(1)}</td>
      <td>${r.p95.toFixed(1)}</td>
      <td>${r.p99.toFixed(1)}</td>
      <td>${r.avg.toFixed(1)}</td>
    `;
    tr.addEventListener('click', () => {
      switchTab('explorer');
      selectEndpointById(`${r.method}:${r.path}`);
    });
    tbody.appendChild(tr);
  }
}

function renderDbHealth(m) {
  const container = document.getElementById('db-health-cards');
  container.innerHTML = '';

  const poolSize    = m.gauges['db_pool_size'] ?? 0;
  const checkedOut  = m.gauges['db_pool_checked_out'] ?? 0;
  const overflow    = m.gauges['db_pool_overflow'] ?? 0;
  const available   = Math.max(0, poolSize - checkedOut);
  const utilPct     = poolSize > 0 ? Math.round((checkedOut / poolSize) * 100) : 0;
  _sparkPool.push(utilPct);

  const poolCard = document.createElement('div');
  poolCard.className = 'dash-card';
  const barTotal = poolSize || 1;
  poolCard.innerHTML = `
    <div class="dash-card-label">Connection Pool</div>
    <div class="pool-bar-wrap">
      <div class="pool-bar">
        <div class="pool-bar-checked"  style="width:${(checkedOut/barTotal)*100}%"></div>
        <div class="pool-bar-overflow" style="width:${(overflow/barTotal)*100}%"></div>
        <div class="pool-bar-avail"    style="width:${(available/barTotal)*100}%"></div>
      </div>
      <div class="pool-legend">
        <span class="l-checked">Checked out: ${checkedOut}</span>
        <span class="l-overflow">Overflow: ${overflow}</span>
        <span class="l-avail">Available: ${available}</span>
      </div>
    </div>
  `;
  container.appendChild(poolCard);
  container.appendChild(makeCard('Pool Utilization', `${utilPct}%`));

  // Query latency per table
  const dbHist = m.histograms['db_query_duration_seconds'] ?? {};
  const dbRows = [];
  for (const [key, h] of Object.entries(dbHist)) {
    const table = extract(key, 'table');
    const op    = extract(key, 'operation');
    dbRows.push({ name: `${table} / ${op}`, p95: estimatePercentile(h.buckets, 0.95) });
  }

  const slowWrap = document.getElementById('slow-queries-wrap');
  slowWrap.innerHTML = '';
  if (dbRows.length > 0) {
    const top5 = dbRows.sort((a, b) => b.p95 - a.p95).slice(0, 5);
    const h3 = document.createElement('h3');
    h3.textContent = 'Top 5 Slow Queries (p95)';
    slowWrap.appendChild(h3);
    for (const r of top5) {
      const row = document.createElement('div');
      row.className = 'slow-query-row';
      row.innerHTML = `<span class="slow-query-name">${r.name}</span><span class="slow-query-lat">${r.p95.toFixed(1)} ms</span>`;
      slowWrap.appendChild(row);
    }
  }
}

function renderSparklines(m) {
  drawSparkline(document.getElementById('spark-req-rate'),  _sparkReqRate.data,  '#6366f1');
  drawSparkline(document.getElementById('spark-error-rate'), _sparkErrRate.data, '#ef4444');
  drawSparkline(document.getElementById('spark-p95'),        _sparkP95.data,     '#eab308');
  drawSparkline(document.getElementById('spark-pool'),       _sparkPool.data,    '#3b82f6');
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

// Extract label value from Prometheus label string like method="GET",path="/api/users"
function extract(labelStr, key) {
  const m = labelStr.match(new RegExp(`${key}="([^"]*)"`));
  return m ? m[1] : '';
}
