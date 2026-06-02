// Token management and HTTP client
const TOKEN_KEY = 'dbzap_token';
const USER_KEY  = 'dbzap_user';

export function saveToken(token, username) {
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USER_KEY, username);
}

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
}

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function getUsername() {
  return sessionStorage.getItem(USER_KEY);
}

export function isLoggedIn() {
  return !!getToken();
}

// Parse JWT exp claim (no verification — server enforces that)
export function tokenExpiresAt() {
  const t = getToken();
  if (!t) return null;
  try {
    const payload = JSON.parse(atob(t.split('.')[1]));
    return payload.exp ? new Date(payload.exp * 1000) : null;
  } catch { return null; }
}

function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function apiFetch(path, { method = 'GET', body, headers = {}, signal } = {}) {
  const opts = {
    method,
    headers: { ...authHeaders(), ...headers },
    signal,
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = typeof body === 'string' ? body : JSON.stringify(body);
  }
  const t0 = performance.now();
  const resp = await fetch(path, opts);
  const elapsed = Math.round(performance.now() - t0);
  return { resp, elapsed };
}

export async function login(username, password) {
  const { resp } = await apiFetch('/auth/login', {
    method: 'POST',
    body: { username, password },
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Login failed');
  const data = await resp.json();
  saveToken(data.access_token, username);
  return data;
}
