// Core helpers: DOM builder, icons, API client, event bus, SSE connection, small utilities. No framework.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h('div', {class:'x', onclick(){...}, dataset:{a:1}, style:{color:'red'}}, 'text', childNode, [..]) */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') el.innerHTML = v;
    else if (k in el && k !== 'list' && k !== 'form') { try { el[k] = v; } catch { el.setAttribute(k, v); } }
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  return el;
}
export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
export function clear(el) { el.replaceChildren(); return el; }
export function mount(el, ...children) { el.replaceChildren(); return append(el, children); }

// ---------------------------------------------------------------- icons (24px grid, stroke)
const I = {
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  file: 'M6 3h8l4 4v14H6z M14 3v4h4',
  tag: 'M3 12V4h8l10 10-8 8z M7.5 8.5h.01',
  calendar: 'M4 6h16v14H4z M4 10h16 M8 3v4 M16 3v4',
  layers: 'M12 3l9 5-9 5-9-5z M3 13l9 5 9-5',
  list: 'M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01',
  hash: 'M5 9h15 M4 15h15 M10 4l-2 16 M16 4l-2 16',
  rules: 'M6 4v6a4 4 0 0 0 4 4h6 M6 4h.01 M18 14h.01 M6 20v-6 M6 20h.01',
  sliders: 'M4 7h10 M18 7h2 M4 17h2 M10 17h10 M14 4v6 M6 14v6',
  compass: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M15.5 8.5l-2 5-5 2 2-5z',
  sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z',
  tree: 'M5 4h5v5H5z M14 11h6v4h-6z M14 18h6v3h-6z M7.5 9v9h6.5 M7.5 13h6.5',
  play: 'M7 4l13 8-13 8z', pause: 'M7 4h4v16H7z M13 4h4v16h-4z', stop: 'M6 6h12v12H6z',
  check: 'M4 12l5 5L20 6', x: 'M5 5l14 14 M19 5L5 19',
  undo: 'M9 14L4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3',
  lock: 'M6 11h12v9H6z M8 11V8a4 4 0 0 1 8 0v3', unlock: 'M6 11h12v9H6z M8 11V8a4 4 0 0 1 7.5-2',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M20 20l-4-4',
  plus: 'M12 5v14 M5 12h14', edit: 'M4 20h4L19 9l-4-4L4 16z', trash: 'M5 7h14 M9 7V4h6v3 M7 7l1 13h8l1-13',
  right: 'M9 5l7 7-7 7', down: 'M5 9l7 7 7-7', up: 'M5 15l7-7 7 7', left: 'M15 5l-7 7 7 7',
  refresh: 'M20 11a8 8 0 1 0-2 6 M20 4v7h-7',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 11v5 M12 8h.01',
  warn: 'M12 3l10 18H2z M12 10v5 M12 18h.01',
  arrow: 'M5 12h14 M13 6l6 6-6 6', home: 'M4 11l8-7 8 7 M6 10v10h12V10',
  save: 'M5 4h11l3 3v13H5z M8 4v5h7V4 M8 20v-6h8v6', external: 'M14 4h6v6 M20 4l-9 9 M18 14v6H4V6h6',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5',
  moon: 'M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z', menu: 'M4 7h16 M4 12h16 M4 17h16',
  filter: 'M4 5h16l-6 8v6l-4-2v-4z', more: 'M5 12h.01 M12 12h.01 M19 12h.01',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7 M12 17h.01',
  power: 'M12 3v9 M6.5 6.5a8 8 0 1 0 11 0', copy: 'M8 8h11v12H8z M5 16V4h11', eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  up: 'M6 15l6-6 6 6', grid: 'M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z', move: 'M5 12h14 M13 6l6 6-6 6 M5 5v14',
};
export function icon(name, cls = '') {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'ico ' + cls);
  const p = document.createElementNS(NS, 'path'); p.setAttribute('d', I[name] || I.info);
  svg.append(p);
  return svg;
}

// ---------------------------------------------------------------- event bus
const listeners = new Map();
export const bus = {
  on(ev, fn) { (listeners.get(ev) || listeners.set(ev, new Set()).get(ev)).add(fn); return () => listeners.get(ev)?.delete(fn); },
  emit(ev, data) { for (const fn of [...(listeners.get(ev) || [])]) { try { fn(data); } catch (e) { console.error(ev, e); } } },
};

// ---------------------------------------------------------------- API client
export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}
async function req(method, path, { params, body } = {}) {
  let url = path;
  if (params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, v === true ? '1' : v === false ? '0' : v);
    const s = q.toString(); if (s) url += (path.includes('?') ? '&' : '?') + s;
  }
  const init = { method, headers: { 'X-VaultMgr': '1' } };
  if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, init); }
  catch { throw new ApiError('Cannot reach the app. If it was closed, start it again.', 0); }
  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch { data = { error: text.slice(0, 200) }; } }
  if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
  return data;
}
export const api = {
  get: (p, params) => req('GET', p, { params }),
  post: (p, body = {}) => req('POST', p, { body }),
  put: (p, body = {}) => req('PUT', p, { body }),
};

// ---------------------------------------------------------------- live events (SSE) with reconnect
let es = null, retry = 0, wasDown = false;
export function connectEvents() {
  if (es) return;
  const open = () => {
    es = new EventSource('/api/events');
    es.onopen = () => { retry = 0; if (wasDown) { wasDown = false; bus.emit('srv', { event: 'resync' }); bus.emit('srv:resync', {}); } bus.emit('conn', { up: true }); };
    es.onmessage = (m) => {
      let d; try { d = JSON.parse(m.data); } catch { return; }
      bus.emit('srv', d); bus.emit('srv:' + d.event, d);
    };
    es.onerror = () => {
      wasDown = true; bus.emit('conn', { up: false });
      es.close(); es = null;
      setTimeout(open, Math.min(8000, 700 * 2 ** retry++));
    };
  };
  open();
}

// ---------------------------------------------------------------- utilities
export function debounce(fn, ms = 150) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
export function throttleFrame(fn) { let q = false; return (...a) => { if (q) return; q = true; requestAnimationFrame(() => { q = false; fn(...a); }); }; }
export const fmt = {
  n: (x) => (x ?? 0).toLocaleString(),
  pct: (x) => `${Math.round((x ?? 0) * 100)}%`,
  conf: (x) => (x == null ? '' : x.toFixed(2)),
  bytes(b) { if (b < 1024) return b + ' B'; if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'; return (b / 1048576).toFixed(1) + ' MB'; },
  date(sec) { return sec ? new Date(sec * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : ''; },
  rate: (r) => (r ? r.toFixed(1) + '/s' : '–'),
  plural: (n, one, many) => `${(n ?? 0).toLocaleString()} ${n === 1 ? one : many || one + 's'}`,
};
export function store(key, fallback) {                    // per-viewer conveniences only; everything is try/catch'd
  return {
    get() { try { const v = localStorage.getItem('vm:' + key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; } },
    set(v) { try { localStorage.setItem('vm:' + key, JSON.stringify(v)); } catch { /* private window */ } },
  };
}
export function baseName(p) { return p.split('/').pop(); }
export function dirName(p) { const i = p.lastIndexOf('/'); return i < 0 ? '' : p.slice(0, i); }
export function isTyping(el = document.activeElement) {
  return !!el && (el.matches?.('input, textarea, select, [contenteditable=true]') || el.closest?.('.modal'));
}
