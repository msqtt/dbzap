// Request builder
import { apiFetch, getToken } from './api.js';
import { showResponse } from './response.js';
import { introspect, unwrapType } from './graphql.js';

let _current = null;
let _gqlSchema = null;
let _openapiSpec = null;
let _pgMode = 'offset'; // 'offset' | 'cursor'
let _tableColumns = []; // column names for filter dropdown

async function ensureOpenApiSpec() {
  if (_openapiSpec) return _openapiSpec;
  try {
    const { resp } = await apiFetch('/openapi.json');
    if (resp.ok) _openapiSpec = await resp.json();
  } catch { /* ignore */ }
  return _openapiSpec;
}

function resolveRef(schema, spec) {
  if (!schema?.$ref || !spec?.components?.schemas) return schema;
  const name = schema.$ref.split('/').pop();
  return spec.components.schemas[name] || schema;
}

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

  // Determine if this is a list endpoint (GET with no path params, not noQuery)
  const isListGet = endpoint.type === 'rest' && endpoint.method === 'GET' && !endpoint.path.includes('{') && !endpoint.noQuery;

  // Query params for REST list
  const querySection = document.getElementById('query-params-section');
  const queryContainer = document.getElementById('query-params');
  const cursorContainer = document.getElementById('cursor-params');
  queryContainer.innerHTML = '';
  cursorContainer.innerHTML = '';

  if (isListGet) {
    querySection.classList.remove('hidden');
    _pgMode = 'offset';
    renderOffsetParams(queryContainer);
    renderCursorParams(cursorContainer);
    updatePgModeButtons();

    // Extract column names from OpenAPI for filter dropdown
    _tableColumns = extractColumnNames(endpoint);
    renderFilterSection();
  } else {
    querySection.classList.add('hidden');
    document.getElementById('filter-section').classList.add('hidden');
    _tableColumns = [];
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
      bodyEditor.value = generateBodyTemplate(endpoint);
    }
  } else {
    bodySection.classList.add('hidden');
  }

  // Reset response
  document.getElementById('response-panel').classList.add('hidden');

  // Response format
  renderResponseFormat(endpoint);

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

// ---------------------------------------------------------------------------
// Pagination mode
// ---------------------------------------------------------------------------

function renderOffsetParams(container) {
  container.appendChild(makeParamRow('page', 'qp-page', '1'));
  container.appendChild(makeParamRow('page_size', 'qp-page-size', '20'));
}

function renderCursorParams(container) {
  container.appendChild(makeParamRow('limit', 'qp-limit', '20'));
  container.appendChild(makeParamRow('starting_after', 'qp-starting-after', ''));
  container.appendChild(makeParamRow('ending_before', 'qp-ending-before', ''));
}

function updatePgModeButtons() {
  const offsetBtn = document.getElementById('pg-mode-offset');
  const cursorBtn = document.getElementById('pg-mode-cursor');
  offsetBtn.classList.toggle('active', _pgMode === 'offset');
  cursorBtn.classList.toggle('active', _pgMode === 'cursor');
  document.getElementById('query-params').classList.toggle('hidden', _pgMode !== 'offset');
  document.getElementById('cursor-params').classList.toggle('hidden', _pgMode !== 'cursor');
}

// ---------------------------------------------------------------------------
// Filter section
// ---------------------------------------------------------------------------

const _FILTER_OPS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'like', 'in', 'is'];

function renderFilterSection() {
  const section = document.getElementById('filter-section');
  const rows = document.getElementById('filter-rows');
  rows.innerHTML = '';
  if (_tableColumns.length === 0) {
    section.classList.add('hidden');
    return;
  }
  section.classList.remove('hidden');
  document.getElementById('filter-or').value = '';
  document.getElementById('filter-options').classList.add('hidden');
}

function addFilterRow(field = '', op = 'eq', value = '') {
  const rows = document.getElementById('filter-rows');
  const row = document.createElement('div');
  row.className = 'filter-row';

  // Field select/input
  const fieldInput = document.createElement('input');
  fieldInput.className = 'filter-field';
  fieldInput.type = 'text';
  fieldInput.placeholder = 'field';
  fieldInput.value = field;
  fieldInput.setAttribute('list', `filter-fields-${rows.children.length}`);
  const datalist = document.createElement('datalist');
  datalist.id = `filter-fields-${rows.children.length}`;
  for (const col of _tableColumns) {
    const opt = document.createElement('option');
    opt.value = col;
    datalist.appendChild(opt);
  }
  row.appendChild(fieldInput);
  row.appendChild(datalist);

  // Op select
  const opSelect = document.createElement('select');
  opSelect.className = 'filter-op';
  for (const o of _FILTER_OPS) {
    const opt = document.createElement('option');
    opt.value = o;
    opt.textContent = o;
    if (o === op) opt.selected = true;
    opSelect.appendChild(opt);
  }
  row.appendChild(opSelect);

  // Value input
  const valInput = document.createElement('input');
  valInput.className = 'filter-value';
  valInput.type = 'text';
  valInput.placeholder = 'value';
  valInput.value = value;
  row.appendChild(valInput);

  // Remove button
  const rmBtn = document.createElement('button');
  rmBtn.className = 'filter-remove';
  rmBtn.type = 'button';
  rmBtn.textContent = '\u00d7';
  rmBtn.addEventListener('click', () => {
    row.remove();
    if (rows.children.length === 0) {
      document.getElementById('filter-options').classList.add('hidden');
    }
  });
  row.appendChild(rmBtn);

  rows.appendChild(row);
  document.getElementById('filter-options').classList.remove('hidden');
}

// ---------------------------------------------------------------------------
// OpenAPI helpers
// ---------------------------------------------------------------------------

function extractColumnNames(endpoint) {
  if (!_openapiSpec || !endpoint.op) return [];
  const schemas = _openapiSpec.components?.schemas || {};
  // Try to find the response model to get column names
  const resp200 = endpoint.op.responses?.['200'];
  const schema = resp200?.content?.['application/json']?.schema;
  if (!schema) return [];

  // For list endpoints, the schema has items/data array -> resolve item schema
  const resolved = resolveRef(schema, _openapiSpec);
  let itemSchema = null;

  if (resolved.properties) {
    // Check for pagination model (has 'items' or 'data' array)
    const dataProp = resolved.properties.data || resolved.properties.items;
    if (dataProp?.items) {
      itemSchema = resolveRef(dataProp.items, _openapiSpec);
    }
    // Check for allOf
    if (!itemSchema && resolved.allOf) {
      for (const sub of resolved.allOf) {
        const r = resolveRef(sub, _openapiSpec);
        const dp = r.properties?.data || r.properties?.items;
        if (dp?.items) {
          itemSchema = resolveRef(dp.items, _openapiSpec);
          break;
        }
      }
    }
    // Single item response (GET by PK) - properties are the columns
    if (!itemSchema && resolved.properties.id !== undefined) {
      itemSchema = resolved;
    }
  }

  if (itemSchema?.properties) {
    return Object.keys(itemSchema.properties);
  }
  return [];
}

function generateBodyTemplate(endpoint) {
  if (!_openapiSpec || !endpoint.op) return '{}';
  const reqBody = endpoint.op.requestBody;
  if (!reqBody) return '{}';
  const schema = reqBody.content?.['application/json']?.schema;
  if (!schema) return '{}';
  const schemas = _openapiSpec.components?.schemas || {};
  const val = schemaToExample(schema, schemas);
  return JSON.stringify(val, null, 2);
}

// ---------------------------------------------------------------------------
// Response format
// ---------------------------------------------------------------------------

async function renderResponseFormat(endpoint) {
  const section = document.getElementById('response-format-section');
  const pre = document.getElementById('response-format-body');
  section.classList.add('hidden');

  let format = null;

  if (endpoint.type === 'rest' && endpoint.op) {
    const spec = await ensureOpenApiSpec();
    const schemas = spec?.components?.schemas || {};
    // Check all response codes (200, 201)
    for (const code of ['200', '201']) {
      format = extractRestResponseFormat(endpoint.op, schemas, code);
      if (format) break;
    }
  } else if (endpoint.type === 'graphql' && endpoint.gqlField) {
    format = await extractGqlResponseFormat(endpoint.gqlField);
  }

  if (format) {
    pre.textContent = format;
    section.classList.remove('hidden');
  }
}

function extractRestResponseFormat(op, schemas, code = '200') {
  const resp = op.responses?.[code];
  if (!resp) return null;
  const schema = resp.content?.['application/json']?.schema;
  if (!schema) return null;
  const val = schemaToExample(schema, schemas);
  return JSON.stringify(val, null, 2);
}

function schemaToExample(schema, schemas = {}, depth = 0) {
  if (depth > 4) return '...';
  if (schema.$ref) schema = resolveRef(schema, { components: { schemas } });
  if (schema.example !== undefined) return schema.example;

  if (schema.type === 'object' && schema.properties) {
    const obj = {};
    for (const [key, val] of Object.entries(schema.properties)) {
      obj[key] = schemaToExample(val, schemas, depth + 1);
    }
    return obj;
  }
  if (schema.type === 'array' && schema.items) {
    return [schemaToExample(schema.items, schemas, depth + 1)];
  }
  if (schema.allOf && schema.allOf.length > 0) {
    const merged = {};
    for (const sub of schema.allOf) {
      const resolved = sub.$ref ? resolveRef(sub, { components: { schemas } }) : sub;
      const val = schemaToExample(resolved, schemas, depth + 1);
      if (val && typeof val === 'object' && !Array.isArray(val)) {
        Object.assign(merged, val);
      }
    }
    return merged;
  }
  const typeMap = { integer: 0, number: 0.0, string: 'string', boolean: true };
  if (typeMap[schema.type] !== undefined) return typeMap[schema.type];
  return null;
}

async function extractGqlResponseFormat(gqlField) {
  if (!_gqlSchema) {
    _gqlSchema = await introspect();
  }
  if (!_gqlSchema) return null;

  const typeName = unwrapType(gqlField.type);
  const gqlType = (_gqlSchema.types || []).find(t => t.name === typeName);
  if (!gqlType || gqlType.kind !== 'OBJECT' || !gqlType.fields) return null;

  const obj = {};
  for (const f of gqlType.fields) {
    const ft = unwrapType(f.type);
    obj[f.name] = ft;
  }
  return JSON.stringify(obj, null, 2);
}

// ---------------------------------------------------------------------------
// Init & Send
// ---------------------------------------------------------------------------

function updateSendState(pathParams) {
  const send = document.getElementById('send-btn');
  const allFilled = pathParams.every(p => {
    const el = document.getElementById(`path-param-${p}`);
    return el && el.value.trim() !== '';
  });
  send.disabled = !allFilled;
}

function buildQueryString() {
  const parts = [];
  const isListGet = _current?.type === 'rest' && _current.method === 'GET' && !_current.path.includes('{') && !_current.noQuery;

  if (!isListGet) return '';

  if (_pgMode === 'offset') {
    const page = document.getElementById('qp-page')?.value || '1';
    const pageSize = document.getElementById('qp-page-size')?.value || '20';
    parts.push(`page=${encodeURIComponent(page)}`, `page_size=${encodeURIComponent(pageSize)}`);
  } else {
    const limit = document.getElementById('qp-limit')?.value || '20';
    parts.push(`limit=${encodeURIComponent(limit)}`);
    const sa = document.getElementById('qp-starting-after')?.value;
    if (sa) parts.push(`starting_after=${encodeURIComponent(sa)}`);
    const eb = document.getElementById('qp-ending-before')?.value;
    if (eb) parts.push(`ending_before=${encodeURIComponent(eb)}`);
  }

  // Filters
  const filterRows = document.querySelectorAll('#filter-rows .filter-row');
  for (const row of filterRows) {
    const field = row.querySelector('.filter-field')?.value?.trim();
    const op = row.querySelector('.filter-op')?.value;
    const value = row.querySelector('.filter-value')?.value?.trim();
    if (field && op && value !== undefined && value !== '') {
      parts.push(`${encodeURIComponent(field)}[${op}]=${encodeURIComponent(value)}`);
    }
  }

  // _or
  const orVal = document.getElementById('filter-or')?.value?.trim();
  if (orVal) parts.push(`_or=${encodeURIComponent(orVal)}`);

  return parts.length > 0 ? `?${parts.join('&')}` : '';
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

  // Pagination mode toggle
  document.getElementById('pg-mode-offset').addEventListener('click', () => {
    _pgMode = 'offset';
    updatePgModeButtons();
  });
  document.getElementById('pg-mode-cursor').addEventListener('click', () => {
    _pgMode = 'cursor';
    updatePgModeButtons();
  });

  // Add filter
  document.getElementById('add-filter-btn').addEventListener('click', () => addFilterRow());

  document.getElementById('send-btn').addEventListener('click', sendRequest);
}

async function sendRequest() {
  if (!_current) return;
  const btn = document.getElementById('send-btn');
  btn.disabled = true;
  btn.textContent = 'Sending\u2026';

  try {
    let url = _current.path;
    // Substitute path params
    const pathParams = (_current.path.match(/\{([^}]+)\}/g) || []).map(p => p.slice(1, -1));
    for (const p of pathParams) {
      const val = document.getElementById(`path-param-${p}`)?.value ?? '';
      url = url.replace(`{${p}}`, encodeURIComponent(val));
    }
    // Query string
    url += buildQueryString();

    let bodyStr;
    if (!document.getElementById('body-section').classList.contains('hidden')) {
      const rawBody = document.getElementById('body-editor').value.trim();
      const err = document.getElementById('body-error');
      if (rawBody) {
        try { JSON.parse(rawBody); err.classList.add('hidden'); }
        catch { err.textContent = 'Invalid JSON'; err.classList.remove('hidden'); return; }
      }
      bodyStr = rawBody || undefined;
    }

    const method = _current.type === 'graphql' ? 'POST' : _current.method;
    const { resp, elapsed } = await apiFetch(url, { method, body: bodyStr });
    await showResponse(resp, elapsed);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Send';
  }
}
