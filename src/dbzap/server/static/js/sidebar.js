// Sidebar: endpoint listing from OpenAPI + GraphQL introspection, search/filter
import { apiFetch } from './api.js';
import { introspect } from './graphql.js';

let _endpoints = [];   // [{id, method, path, type, group, gqlField}]
let _onSelect = null;

export function onEndpointSelect(fn) { _onSelect = fn; }

export async function loadEndpoints() {
  _endpoints = [];
  await Promise.allSettled([loadRest(), loadGraphQL()]);
  render();
}

async function loadRest() {
  try {
    const { resp } = await apiFetch('/openapi.json');
    if (!resp.ok) return;
    const spec = await resp.json();
    _endpoints.push({ id: 'GET:/openapi.json', method: 'GET', path: '/openapi.json', type: 'rest', group: 'Schema', op: null, noQuery: true });
    for (const [path, methods] of Object.entries(spec.paths || {})) {
      for (const [method, op] of Object.entries(methods)) {
        const m = method.toUpperCase();
        if (!['GET','POST','PUT','PATCH','DELETE'].includes(m)) continue;
        const parts = path.split('/').filter(Boolean);
        const group = parts[1] || 'other';
        _endpoints.push({ id: `${m}:${path}`, method: m, path, type: 'rest', group, op });
      }
    }
  } catch {}
}

async function loadGraphQL() {
  try {
    const schema = await introspect();
    if (!schema) return;
    const fields = [
      ...(schema.queryType?.fields ?? []).map(f => ({ ...f, kind: 'query' })),
      ...(schema.mutationType?.fields ?? []).map(f => ({ ...f, kind: 'mutation' })),
    ];
    for (const f of fields) {
      _endpoints.push({
        id: `GQL:${f.name}`,
        method: f.kind === 'query' ? 'QUERY' : 'MUTATION',
        path: f.name,
        type: 'graphql',
        group: 'GraphQL',
        gqlField: f,
      });
    }
  } catch {}
}

export function render(filter = '') {
  const list = document.getElementById('sidebar-content');
  if (!list) return;
  const q = filter.toLowerCase();
  const visible = _endpoints.filter(e =>
    e.path.toLowerCase().includes(q) || e.method.toLowerCase().includes(q)
  );

  const byGroup = {};
  for (const e of visible) {
    (byGroup[e.group] ??= []).push(e);
  }

  list.innerHTML = '';
  for (const [group, items] of Object.entries(byGroup)) {
    const label = document.createElement('div');
    label.className = 'sidebar-group-label';
    label.textContent = group;
    list.appendChild(label);
    for (const e of items) {
      const item = document.createElement('div');
      item.className = 'sidebar-item';
      item.dataset.id = e.id;
      const tag = document.createElement('span');
      const m = e.type === 'graphql' ? 'GQL' : e.method;
      tag.className = `method-tag ${m}`;
      tag.textContent = m;
      const name = document.createElement('span');
      name.textContent = e.path;
      item.appendChild(tag);
      item.appendChild(name);
      item.addEventListener('click', () => selectEndpoint(e));
      list.appendChild(item);
    }
  }
  if (visible.length === 0) {
    const msg = document.createElement('div');
    msg.className = 'empty-state';
    msg.textContent = 'No endpoints found.';
    list.appendChild(msg);
  }
}

export function selectEndpointById(id) {
  const e = _endpoints.find(x => x.id === id);
  if (e) selectEndpoint(e);
}

function selectEndpoint(e) {
  document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
  const el = document.querySelector(`.sidebar-item[data-id="${CSS.escape(e.id)}"]`);
  if (el) el.classList.add('active');
  _onSelect?.(e);
}
