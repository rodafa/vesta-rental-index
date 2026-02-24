/**
 * Vesta Dashboard — shared API client.
 * Wraps fetch() with JSON parsing, error handling, and helpers.
 */
const VestaAPI = (() => {
  const BASE = '/api';

  async function request(path, options = {}) {
    const url = path.startsWith('http') ? path : `${BASE}${path}`;
    const resp = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`API ${resp.status}: ${text.slice(0, 200)}`);
    }
    return resp.json();
  }

  function get(path) { return request(path); }

  function post(path, body) {
    return request(path, { method: 'POST', body: JSON.stringify(body) });
  }

  function put(path, body) {
    return request(path, { method: 'PUT', body: JSON.stringify(body) });
  }

  // Fetch a list endpoint, returns the items array.
  // django-ninja LimitOffsetPagination wraps results in { items: [], count: N }.
  async function list(path) {
    const data = await get(path);
    return data.items || data;
  }

  // Format helpers
  function $(amount) {
    if (amount == null) return '—';
    return '$' + Number(amount).toLocaleString('en-US', {
      minimumFractionDigits: 0, maximumFractionDigits: 0
    });
  }

  function $dec(amount) {
    if (amount == null) return '—';
    return '$' + Number(amount).toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function pct(value, decimals = 1) {
    if (value == null) return '—';
    return Number(value).toFixed(decimals) + '%';
  }

  function num(value) {
    if (value == null) return '—';
    return Number(value).toLocaleString('en-US');
  }

  function dateStr(d) {
    if (!d) return '—';
    return new Date(d + 'T00:00:00').toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric'
    });
  }

  function daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().split('T')[0];
  }

  function today() {
    return new Date().toISOString().split('T')[0];
  }

  // Show a toast notification
  function toast(message, type = 'success') {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 300);
    }, 3000);
  }

  // Set innerHTML and return the element for chaining
  function render(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
    return el;
  }

  return { get, post, put, list, $, $dec, pct, num, dateStr, daysAgo, today, toast, render };
})();
