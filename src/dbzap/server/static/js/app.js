// App bootstrap, tab routing, login flow
import { login, isLoggedIn, clearToken, getUsername, tokenExpiresAt } from './api.js';
import { loadEndpoints, render as renderSidebar, onEndpointSelect } from './sidebar.js';
import { loadEndpoint, initRequestBuilder } from './request.js';
import { initResponseViewer } from './response.js';
import { initDashboard, startPolling, stopPolling } from './dashboard.js';
import { updateHealthDot } from './health.js';

// ---- Theme ----
const THEME_KEY = 'dbzap-theme';
const THEMES = ['system', 'light', 'dark'];
const THEME_ICONS = { system: '\u25C9', light: '\u2600', dark: '\u263E' };

function getStoredTheme() {
  return localStorage.getItem(THEME_KEY) || 'system';
}

function effectiveTheme(pref) {
  if (pref === 'system') return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  return pref;
}

function applyTheme(pref) {
  document.documentElement.setAttribute('data-theme', effectiveTheme(pref));
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = THEME_ICONS[pref] || THEME_ICONS.system;
}

function initTheme() {
  const pref = getStoredTheme();
  applyTheme(pref);

  const btn = document.getElementById('theme-btn');
  if (btn) {
    btn.addEventListener('click', () => {
      const current = getStoredTheme();
      const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
    });
  }

  // React to OS preference changes when in system mode
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (getStoredTheme() === 'system') applyTheme('system');
  });
}

// ---- Credential pre-fill ----
async function preFillCredentials() {
  try {
    const resp = await fetch('/explorer/config');
    if (!resp.ok) return;
    const cfg = await resp.json();
    // Only username is pre-filled. The server intentionally does not return
    // the password (P0-1 / spec 08): /explorer/config is anonymously
    // reachable, so echoing the configured password would leak the admin
    // credential to every visitor. The user types the password each login.
    if (cfg.username) document.getElementById('username').value = cfg.username;
  } catch {
    // Silently ignore — login form stays functional
  }
}

// Exported so dashboard.js can call it
export function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.getElementById('view-explorer').classList.toggle('hidden', name !== 'explorer');
  document.getElementById('view-dashboard').classList.toggle('hidden', name !== 'dashboard');
  if (name === 'dashboard') startPolling();
  else stopPolling();
}

async function init() {
  initTheme();

  if (isLoggedIn()) {
    showApp();
  } else {
    showLogin();
    preFillCredentials();
  }
}

function showLogin() {
  document.getElementById('login-overlay').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
}

async function showApp() {
  document.getElementById('login-overlay').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  document.getElementById('current-user').textContent = getUsername() ?? '';

  // Token expiry countdown
  scheduleExpiryWarning();

  // Health dot
  updateHealthDot();
  setInterval(updateHealthDot, 30000);

  // Sidebar
  onEndpointSelect(endpoint => {
    loadEndpoint(endpoint);
    // scroll explorer main to top
    document.querySelector('.explorer-main')?.scrollTo(0, 0);
  });
  await loadEndpoints();

  // Search
  document.getElementById('endpoint-search').addEventListener('input', e => {
    renderSidebar(e.target.value);
  });

  // Tab switching
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Logout
  document.getElementById('logout-btn').addEventListener('click', () => {
    clearToken();
    stopPolling();
    showLogin();
    preFillCredentials();
  });

  // Init sub-modules
  initRequestBuilder();
  initResponseViewer();
  initDashboard();
}

function scheduleExpiryWarning() {
  const expiry = tokenExpiresAt();
  if (!expiry) return;
  const msLeft = expiry.getTime() - Date.now();
  const warnAt = msLeft - 60_000; // 1 min before expiry
  if (warnAt > 0) {
    setTimeout(() => {
      const el = document.getElementById('token-expiry');
      // Show overlay again would be too disruptive; show a banner instead
      showExpiryBanner(expiry);
    }, warnAt);
  }
  setTimeout(() => {
    clearToken();
    showLogin();
  }, msLeft);
}

function showExpiryBanner(expiry) {
  const el = document.getElementById('token-expiry');
  el.classList.remove('hidden');
  el.textContent = `Token expires at ${expiry.toLocaleTimeString()} — save your work.`;
  document.getElementById('login-overlay').classList.remove('hidden');
  document.getElementById('login-overlay').style.background = 'rgba(15,17,23,.85)';
}

// Login form
document.getElementById('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = document.getElementById('login-btn');
  const err = document.getElementById('login-error');
  btn.disabled = true;
  btn.textContent = 'Signing in…';
  err.classList.add('hidden');
  try {
    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    await login(username, password);
    await showApp();
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Sign in';
  }
});

init();
