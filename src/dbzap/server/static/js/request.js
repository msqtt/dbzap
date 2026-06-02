// Request builder
import { apiFetch, getToken } from './api.js';
import { showResponse } from './response.js';

let _current = null;

export function loadEndpoint(endpoint) {
  _current = endpoint;
  const empty = document.getElementById('request-empty');
  const builder = document.getElementById('request-builder');
  empty.classList.add('hidden');
  builder.classList.remove('hidden');

  // Method badge
  const badge = document.getElementById('req-method-badge');
  const m = endpoint.type === 'graphql' ? 'GQL' : endpoint.method;
  badge.textContent = m;
  badge.className = `method-badge method-tag ${m}`;

  // Path display
  document.getElementById('req-path-display').textContent = endpoint.path;

  // Path params
  const pathSection = document.getElementById('path-params-section');
  const pathContainer = document.getElementById('path-params');
  const pathParams = endpoint.type === 'rest'
    ? (endpoint.path.match(/\{([^}]+)\}/g) || []).map(p => p.slice(1, -1))
    : [];
  pathContainer.innerHTML = '';
  if (pathParams.length > 0) {
    pathSection.classList.remove('hidden');
    for (const p of pathParams) {
      pathContainer.appendChild(makeParamRow(p, `path-param-${p}`, '', true));
    }
  } else {
    pathSection.classList.add('hidden');
  }

  // Query params for REST list/get
  const querySection = document.getElementById('query-params-section');
  const queryContainer = document.getElementById('query-params');
  queryContainer.innerHTML = '';
  const isListGet = endpoint.type === 'rest' && endpoint.method === 'GET' && !endpoint.path.includes('{');
  if (isListGet) {
    querySection.classList.remove('hidden');
    queryContainer.appendChild(makeParamRow('offset', 'qp-offset', '0'));
    queryContainer.appendChild(makeParamRow('limit', 'qp-limit', '20'));
  } else {
    querySection.classList.add('hidden');
  }

  // Headers
  const headersEditor = document.getElementById('headers-editor');
  headersEditor.innerHTML = '';
  const token = getToken();
  if (token) {
    headersEditor.appendChild(makeParamRow('Authorization', 'hdr-auth', `Bearer ${token}`, false, true));
  }
  headersEditor.appendChild(makeParamRow('Content-Type', 'hdr-ct', 'application/json', false, true));

  // Body
  const bodySection = document.getElementById('body-section');
  const needsBody = endpoint.type === 'graphql' ||
    (endpoint.type === 'rest' && ['POST','PUT','PATCH'].includes(endpoint.method));
  if (needsBody) {
    bodySection.classList.remove('hidden');
    const bodyEditor = document.getElementById('body-editor');
    if (endpoint.type === 'graphql') {
      bodyEditor.value = JSON.stringify({ query: `{ ${endpoint.path} }` }, null, 2);
    } else {
      bodyEditor.value = '{}';
    }
  } else {
    bodySection.classList.add('hidden');
  }

  // Reset response
  document.getElementById('response-panel').classList.add('hidden');

  // Update send button state
  updateSendState(pathParams);
}

function makeParamRow(label, id, defaultVal, required = false, readOnly = false) {
  const row = document.createElement('div');
  row.className = 'param-row';
  const lbl = document.createElement('label');
  lbl.setAttribute('for', id);
  lbl.textContent = label + (required ? ' *' : '');
  const inp = document.createElement('input');
  inp.id = id;
  inp.type = 'text';
  inp.value = defaultVal;
  if (readOnly) inp.readOnly = true;
  if (required) inp.addEventListener('input', () => updateSendState([]));
  row.appendChild(lbl);
  row.appendChild(inp);
  return row;
}

function updateSendState(pathParams) {
  const send = document.getElementById('send-btn');
  const allFilled = pathParams.every(p => {
    const el = document.getElementById(`path-param-${p}`);
    return el && el.value.trim() !== '';
  });
  send.disabled = !allFilled;
}

export function initRequestBuilder() {
  document.getElementById('format-body-btn').addEventListener('click', () => {
    const ta = document.getElementById('body-editor');
    const err = document.getElementById('body-error');
    try {
      ta.value = JSON.stringify(JSON.parse(ta.value), null, 2);
      err.classList.add('hidden');
    } catch {
      err.textContent = 'Invalid JSON';
      err.classList.remove('hidden');
    }
  });

  document.getElementById('send-btn').addEventListener('click', sendRequest);
}

async function sendRequest() {
  if (!_current) return;
  const btn = document.getElementById('send-btn');
  btn.disabled = true;
  btn.textContent = 'Sending…';

  try {
    let url = _current.path;
    // Substitute path params
    const pathParams = (_current.path.match(/\{([^}]+)\}/g) || []).map(p => p.slice(1, -1));
    for (const p of pathParams) {
      const val = document.getElementById(`path-param-${p}`)?.value ?? '';
      url = url.replace(`{${p}}`, encodeURIComponent(val));
    }
    // Query params
    const qpOffset = document.getElementById('qp-offset');
    const qpLimit  = document.getElementById('qp-limit');
    if (qpOffset && qpLimit) {
      url += `?offset=${qpOffset.value}&limit=${qpLimit.value}`;
    }

    let bodyStr;
    if (!document.getElementById('body-section').classList.contains('hidden')) {
      const rawBody = document.getElementById('body-editor').value.trim();
      const err = document.getElementById('body-error');
      try { JSON.parse(rawBody); err.classList.add('hidden'); }
      catch { err.textContent = 'Invalid JSON'; err.classList.remove('hidden'); return; }
      bodyStr = rawBody;
    }

    const method = _current.type === 'graphql' ? 'POST' : _current.method;
    const { resp, elapsed } = await apiFetch(url, { method, body: bodyStr });
    await showResponse(resp, elapsed);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Send';
  }
}
