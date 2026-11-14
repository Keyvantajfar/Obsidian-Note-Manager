// Shared UI components: toast, dialogs, menu, badges, diff, DataTable (virtualised), Tree, split pane, folder pickers.
import { h, icon, mount, clear, api, store, throttleFrame, fmt, baseName } from './core.js';

// ---------------------------------------------------------------- toasts
export function toast(message, kind = 'info', ms) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const t = h('div', { class: `toast ${kind}`, role: kind === 'err' ? 'alert' : 'status' },
    h('div', null, message), h('button', { class: 'x', 'aria-label': 'Dismiss', onclick: () => t.remove() }, '✕'));
  host.append(t);
  while (host.children.length > 5) host.firstChild.remove();
  setTimeout(() => t.remove(), ms ?? (kind === 'err' ? 9000 : kind === 'warn' ? 6500 : 4200));
}
export const toastError = (e) => toast(e?.message || String(e), 'err');

// ---------------------------------------------------------------- dialogs
let lastFocus = null;
export function dialog({ title, description, body, actions = [{ label: 'Close', value: null }], size = '', dismissible = true, focus } = {}) {
  lastFocus = document.activeElement;
  let done; const result = new Promise((r) => (done = r));
  const scrim = h('div', { class: 'scrim' });
  const btns = actions.map((a) => {
    const b = h('button', { class: `btn ${a.kind || ''}`, disabled: !!a.disabled, type: 'button' }, a.icon ? icon(a.icon) : null, a.label);
    b.addEventListener('click', async () => {
      if (a.onClick) { const r = await a.onClick(close); if (r === false) return; }
      if (!a.keepOpen) close(a.value);
    });
    return b;
  });
  const modal = h('div', { class: `modal ${size}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title || 'Dialog' },
    title ? h('header', null, h('h2', null, title), description ? h('p', null, description) : null) : null,
    h('div', { class: 'body' }, body), h('footer', null, btns));
  scrim.append(modal);
  function close(v = null) {
    if (!scrim.isConnected) return;
    scrim.remove(); document.removeEventListener('keydown', onKey, true);
    if (lastFocus && lastFocus.isConnected) lastFocus.focus?.();
    done(v);
  }
  function onKey(e) {
    if (!scrim.isConnected || scrim.nextSibling) return;                 // only the top-most dialog reacts
    if (e.key === 'Escape' && dismissible) { e.stopPropagation(); close(null); }
    else if (e.key === 'Tab') {
      const f = [...modal.querySelectorAll('button:not(:disabled), input:not(:disabled), select, textarea, [tabindex="0"]')].filter((x) => x.offsetParent);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    } else if (e.key === 'Enter' && !e.shiftKey && !(e.target instanceof HTMLTextAreaElement) && !(e.target instanceof HTMLButtonElement)) {
      const p = btns[actions.findIndex((a) => a.kind === 'primary')];
      if (p && !p.disabled) { e.preventDefault(); p.click(); }
    }
  }
  document.addEventListener('keydown', onKey, true);
  if (dismissible) scrim.addEventListener('mousedown', (e) => { if (e.target === scrim) close(null); });
  document.body.append(scrim);
  setTimeout(() => (focus ? (typeof focus === 'string' ? modal.querySelector(focus) : focus) : modal.querySelector('input, textarea, select') || btns.find((b) => b.classList.contains('primary')) || btns[0])?.focus(), 0);
  return { el: modal, close, result, buttons: btns };
}
export async function confirmDialog({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false }) {
  const d = dialog({ title, body: h('p', { style: { whiteSpace: 'pre-wrap' } }, message), actions: [
    { label: cancelLabel, value: false }, { label: confirmLabel, value: true, kind: danger ? 'danger' : 'primary' }] });
  return (await d.result) === true;
}
export async function promptDialog({ title, description, value = '', placeholder = '', label = '', validate, confirmLabel = 'OK', multiline = false }) {
  const input = multiline ? h('textarea', { rows: 4, value, placeholder }) : h('input', { type: 'text', value, placeholder });
  const err = h('div', { class: 'muted', style: { color: 'var(--rose)', minHeight: '1.2em', fontSize: 'var(--fs-sm)' } });
  const body = h('div', { class: 'stack' }, h('label', { class: 'field' }, label, input), err);
  const d = dialog({ title, description, body, actions: [{ label: 'Cancel', value: null }, {
    label: confirmLabel, kind: 'primary', value: 'ok', onClick: () => {
      const v = input.value; const m = validate ? validate(v) : null;
      if (m) { err.textContent = m; return false; }
      d.value = v;
    } }] });
  const r = await d.result;
  return r === 'ok' ? d.value : null;
}

// ---------------------------------------------------------------- menu
export function menu(anchor, items) {
  document.querySelectorAll('.menu').forEach((m) => m.remove());
  const r = anchor instanceof Element ? anchor.getBoundingClientRect() : { left: anchor.x, bottom: anchor.y, top: anchor.y, right: anchor.x };
  const m = h('div', { class: 'menu', role: 'menu' }, items.map((it) => it === '-' ? h('hr') :
    h('button', { role: 'menuitem', disabled: !!it.disabled, 'aria-checked': it.checked == null ? null : String(!!it.checked), onclick: () => { close(); it.onClick?.(); } },
      it.icon ? icon(it.icon) : null, it.label, it.hint ? h('span', { class: 'hint' }, it.hint) : null)));
  document.body.append(m);
  const w = m.offsetWidth, hgt = m.offsetHeight;
  m.style.left = Math.max(6, Math.min(r.left, innerWidth - w - 6)) + 'px';
  m.style.top = (r.bottom + hgt + 8 > innerHeight ? Math.max(6, r.top - hgt - 4) : r.bottom + 4) + 'px';
  const close = () => { m.remove(); document.removeEventListener('mousedown', out, true); document.removeEventListener('keydown', key, true); };
  const out = (e) => { if (!m.contains(e.target)) close(); };
  const key = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); close(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); const b = [...m.querySelectorAll('button:not(:disabled)')]; const i = b.indexOf(document.activeElement);
      b[(i + (e.key === 'ArrowDown' ? 1 : -1) + b.length) % b.length]?.focus();
    }
  };
  setTimeout(() => { document.addEventListener('mousedown', out, true); document.addEventListener('keydown', key, true); m.querySelector('button:not(:disabled)')?.focus(); }, 0);
  return close;
}

// ---------------------------------------------------------------- small pieces
export const statusBadge = (s) => h('span', { class: `badge ${s}` }, s === 'proposed' ? 'To review' : s === 'accepted' ? 'Ready' : s === 'rejected' ? 'Rejected' : s);
export const sourceBadge = (s) => h('span', { class: `badge plain ${s}` }, s === 'ai' ? 'AI' : s === 'rule' ? 'Rule' : 'You');
export function confBadge(c, thr = 0.85) {
  if (c == null) return h('span', { class: 'faint' }, '–');
  return h('span', { class: `conf ${c >= thr ? 'hi' : c < 0.5 ? 'lo' : ''}`, title: `Confidence ${c.toFixed(2)}` }, h('i', { style: { '--v': Math.round(c * 100) + '%' } }), c.toFixed(2));
}
export function chips(values, { max = 6, cls = '' } = {}) {
  const vs = Array.isArray(values) ? values : values ? [values] : [];
  const el = h('span', { class: 'chips' }, vs.slice(0, max).map((v) => h('span', { class: `chip ${cls}`, title: v }, v)));
  if (vs.length > max) el.append(h('span', { class: 'chip', title: vs.slice(max).join(', ') }, `+${vs.length - max}`));
  return el;
}
export function diffView(lines) {
  return h('pre', { class: 'diff' }, (lines || []).map(([t, l]) => h('div', { class: t === '+' ? 'add' : t === '-' ? 'del' : 'ctx' }, l)));
}
export function emptyState({ title, text, actions }) {
  return h('div', { class: 'empty' }, h('h3', null, title), text ? h('p', null, text) : null, actions ? h('div', { class: 'row' }, actions) : null);
}
export function kv(obj) {
  return h('dl', { class: 'kv' }, Object.entries(obj).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v ?? '–')]));
}
export function button(label, { icon: ic, kind = '', size = '', onClick, title } = {}) {
  return h('button', { class: `btn ${kind} ${size}`, type: 'button', title, onclick: onClick }, ic ? icon(ic) : null, label);
}
export function iconButton(ic, title, onClick, { size = 'sm', kind = 'ghost' } = {}) {
  return h('button', { class: `btn icon ${kind} ${size}`, type: 'button', title, 'aria-label': title, onclick: onClick }, icon(ic));
}
/** Run an async action with a busy state on its button and error toast. */
export async function busy(btn, fn) {
  const was = btn?.disabled; if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { toastError(e); } finally { if (btn) btn.disabled = was ?? false; }
}

// ---------------------------------------------------------------- split pane (draggable)
export function splitPane(left, right, { key = 'split', width = 280, min = 160, max = 640 } = {}) {
  const s = store('split:' + key, width);
  const l = h('div', { class: 'pane fixed', style: { width: Math.max(min, Math.min(max, s.get())) + 'px' } }, left);
  const g = h('div', { class: 'gutter', role: 'separator', 'aria-orientation': 'vertical', tabindex: 0, title: 'Drag to resize' });
  const r = h('div', { class: 'pane fill' }, right);
  g.addEventListener('pointerdown', (e) => {
    g.setPointerCapture(e.pointerId); g.classList.add('drag');
    const x0 = e.clientX, w0 = l.offsetWidth;
    const mv = (ev) => { l.style.width = Math.max(min, Math.min(max, w0 + ev.clientX - x0)) + 'px'; };
    const up = () => { g.classList.remove('drag'); g.removeEventListener('pointermove', mv); g.removeEventListener('pointerup', up); s.set(l.offsetWidth); };
    g.addEventListener('pointermove', mv); g.addEventListener('pointerup', up);
  });
  g.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { l.style.width = Math.max(min, Math.min(max, l.offsetWidth + (e.key === 'ArrowRight' ? 24 : -24))) + 'px'; s.set(l.offsetWidth); }
  });
  return { el: h('div', { class: 'split' }, l, g, r), left: l, right: r };
}

// ---------------------------------------------------------------- DataTable: virtualised rows, keyboard + mouse selection
export class DataTable {
  /** columns: [{key,label,width:'1fr'|'120px',render(row,i)->Node|string,sort?(a,b),title?}] */
  constructor(o) {
    this.o = { rowHeight: 34, checkbox: false, ...o };
    this.rows = []; this.view = []; this.sel = new Set(); this.cur = null; this.anchor = null; this.sortKey = null; this.sortDir = 1;
    const cols = [...(this.o.checkbox ? ['34px'] : []), ...this.o.columns.map((c) => (c.width || '1fr').replace(/^(\d*\.?\d*)fr$/, 'minmax(90px,$1fr)'))];
    this.tpl = cols.join(' ');
    const minW = cols.reduce((a, c) => a + (parseInt((c.match(/minmax\((\d+)px/) || c.match(/^(\d+)px/) || [0, 90])[1], 10) || 90), 0);
    this.head = h('div', { class: 'dt-head', style: { gridTemplateColumns: this.tpl } },
      this.o.checkbox ? h('div', { class: 'cb' }, this.allBox = h('input', { type: 'checkbox', 'aria-label': 'Select all', onchange: () => (this.allBox.checked ? this.selectAll() : this.clearSel()) })) : null,
      this.o.columns.map((c) => h('div', { class: c.sort ? 'sortable' : '', title: c.title || c.label, onclick: c.sort ? () => this.sortBy(c.key) : null }, c.label)));
    this.body = h('div', { class: 'dt-body' });
    this.emptyEl = h('div', { class: 'dt-empty', hidden: true });
    this.el = h('div', { class: 'dt ' + (this.o.class || ''), tabindex: 0, role: 'grid', 'aria-label': this.o.label || 'Table' }, this.head, this.body, this.emptyEl);
    this.el.style.setProperty('--row-h', this.o.rowHeight + 'px'); this.el.style.setProperty('--dt-min', minW + 'px');
    this.render = throttleFrame(() => this._render());
    this.el.addEventListener('scroll', this.render, { passive: true });
    new ResizeObserver(this.render).observe(this.el);
    this.el.addEventListener('keydown', (e) => this._key(e));
    this.body.addEventListener('mousedown', (e) => this._click(e));
    this.body.addEventListener('dblclick', (e) => { const r = this._rowAt(e); if (r) this.o.onActivate?.(r.row); });
    this.body.addEventListener('contextmenu', (e) => { const r = this._rowAt(e); if (r && this.o.onContext) { e.preventDefault(); if (this.cur !== r.key) this.setCursor(r.key); this.o.onContext(r.row, e); } });
  }
  key(row) { return this.o.rowKey(row); }
  setEmpty(node) { clear(this.emptyEl).append(node || ''); }
  setRows(rows) {
    this.rows = rows; this._sort();
    const keys = new Set(this.view.map((r) => this.key(r)));
    for (const k of [...this.sel]) if (!keys.has(k)) this.sel.delete(k);
    if (this.cur != null && !keys.has(this.cur)) { this.cur = null; this.o.onCursor?.(null); }
    if (this.cur == null && this.view.length && this.o.autoCursor !== false) { this.cur = this.key(this.view[0]); this.o.onCursor?.(this.view[0]); }
    this._render(); this._syncAll();
  }
  sortBy(key) { this.sortDir = this.sortKey === key ? -this.sortDir : 1; this.sortKey = key; this._sort(); this._render(); }
  _sort() {
    const c = this.o.columns.find((x) => x.key === this.sortKey);
    this.view = c?.sort ? [...this.rows].sort((a, b) => c.sort(a, b) * this.sortDir) : this.rows;
    this.head.querySelectorAll('.sortable').forEach((el, i) => { const col = this.o.columns.filter((x) => x.sort)[i]; el.dataset.dir = col?.key === this.sortKey ? this.sortDir : ''; el.textContent = col.label + (col.key === this.sortKey ? (this.sortDir > 0 ? ' ▲' : ' ▼') : ''); });
  }
  get cursorRow() { return this.cur == null ? null : this.view.find((r) => this.key(r) === this.cur) || null; }
  selectedRows() { return this.view.filter((r) => this.sel.has(this.key(r))); }
  /** what a bulk action applies to: the checked rows, else the cursor row */
  targets() { const s = this.selectedRows(); if (s.length) return s; const c = this.cursorRow; return c ? [c] : []; }
  _idx(key) { return this.view.findIndex((r) => this.key(r) === key); }
  setCursor(key, { scroll = true, silent = false } = {}) {
    this.cur = key; const i = this._idx(key);
    if (scroll && i >= 0) this._reveal(i);
    this._render();
    if (!silent) this.o.onCursor?.(i >= 0 ? this.view[i] : null);
  }
  _reveal(i) {
    const rh = this.o.rowHeight, top = i * rh + 30, bot = top + rh, v = this.el;
    if (top - 30 < v.scrollTop) v.scrollTop = top - 30; else if (bot > v.scrollTop + v.clientHeight) v.scrollTop = bot - v.clientHeight;
  }
  toggle(key) { this.sel.has(key) ? this.sel.delete(key) : this.sel.add(key); this._changed(); }
  selectAll(keys) { (keys || this.view.map((r) => this.key(r))).forEach((k) => this.sel.add(k)); this._changed(); }
  clearSel() { if (this.sel.size) { this.sel.clear(); this._changed(); } }
  _changed() { this._render(); this._syncAll(); this.o.onSelect?.(this.sel); }
  _syncAll() { if (this.allBox) { this.allBox.checked = this.view.length > 0 && this.sel.size >= this.view.length; this.allBox.indeterminate = this.sel.size > 0 && this.sel.size < this.view.length; } }
  _rowAt(e) { const el = e.target.closest?.('.dt-row'); if (!el) return null; const i = +el.dataset.i; return { row: this.view[i], key: this.key(this.view[i]), i }; }
  _click(e) {
    const r = this._rowAt(e); if (!r) return;
    this.el.focus({ preventScroll: true });
    if (e.target.closest('.cb')) { this.toggle(r.key); this.anchor = r.key; if (this.cur !== r.key) this.setCursor(r.key, { scroll: false }); return; }
    if (e.target.closest('button, a, input, select')) { if (this.cur !== r.key) this.setCursor(r.key, { scroll: false }); return; }
    if (e.shiftKey && this.anchor != null) {
      const a = this._idx(this.anchor), b = r.i;
      this.sel.clear(); for (let i = Math.min(a, b); i <= Math.max(a, b); i++) this.sel.add(this.key(this.view[i]));
      this._changed();
    } else if (e.ctrlKey || e.metaKey) { this.toggle(r.key); this.anchor = r.key; }
    else { if (!this.o.checkbox) this.clearSel(); this.anchor = r.key; }
    this.setCursor(r.key, { scroll: false });
  }
  _key(e) {
    if (e.target !== this.el && e.target.closest?.('input, textarea, select')) return;
    const n = this.view.length; if (!n) return;
    let i = this._idx(this.cur); const rows = Math.max(1, Math.floor(this.el.clientHeight / this.o.rowHeight) - 1);
    const go = (ni) => {
      ni = Math.max(0, Math.min(n - 1, ni));
      if (e.shiftKey) { if (this.anchor == null) this.anchor = this.cur; const a = this._idx(this.anchor); this.sel.clear(); for (let k = Math.min(a, ni); k <= Math.max(a, ni); k++) this.sel.add(this.key(this.view[k])); this._changed(); }
      else this.anchor = this.key(this.view[ni]);
      this.setCursor(this.key(this.view[ni]));
    };
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); go(i + 1); break;
      case 'ArrowUp': e.preventDefault(); go(i - 1); break;
      case 'PageDown': e.preventDefault(); go(i + rows); break;
      case 'PageUp': e.preventDefault(); go(i - rows); break;
      case 'Home': e.preventDefault(); go(0); break;
      case 'End': e.preventDefault(); go(n - 1); break;
      case ' ': if (this.cur != null) { e.preventDefault(); this.toggle(this.cur); this.anchor = this.cur; if (i < n - 1) this.setCursor(this.key(this.view[i + 1])); } break;
      case 'Enter': if (this.cursorRow) { e.preventDefault(); this.o.onActivate?.(this.cursorRow); } break;
      case 'a': case 'A': if (e.ctrlKey || e.metaKey) { e.preventDefault(); this.selectAll(); } else this.o.onKey?.(e, this.cursorRow); break;
      case 'Escape': if (this.sel.size) { e.stopPropagation(); this.clearSel(); } break;
      default: this.o.onKey?.(e, this.cursorRow);
    }
  }
  _render() {
    const rh = this.o.rowHeight, n = this.view.length, v = this.el;
    this.body.style.height = n * rh + 'px';
    this.emptyEl.hidden = n > 0;
    const first = Math.max(0, Math.floor((v.scrollTop - 30) / rh) - 4), last = Math.min(n, Math.ceil((v.scrollTop + v.clientHeight) / rh) + 4);
    const frag = document.createDocumentFragment();
    for (let i = first; i < last; i++) {
      const row = this.view[i], k = this.key(row);
      const el = h('div', { class: 'dt-row' + (this.sel.has(k) ? ' sel' : '') + (this.cur === k ? ' cur' : '') + (this.o.rowClass?.(row) ? ' ' + this.o.rowClass(row) : ''),
        role: 'row', 'aria-selected': this.sel.has(k) ? 'true' : null, dataset: { i }, style: { top: i * rh + 'px', gridTemplateColumns: this.tpl } });
      if (this.o.checkbox) el.append(h('div', { class: 'cb' }, h('input', { type: 'checkbox', checked: this.sel.has(k), tabindex: -1, 'aria-label': 'Select row' })));
      for (const c of this.o.columns) {
        const val = c.render ? c.render(row, i) : row[c.key];
        el.append(h('div', { role: 'gridcell', title: typeof val === 'string' ? val : null }, val));
      }
      frag.append(el);
    }
    this.body.replaceChildren(frag);
  }
}

// ---------------------------------------------------------------- Tree (folders)
export class Tree {
  /** nodes: [{id,label,count?,children?:[...],dim?}] — the root node id '' is allowed (vault root). */
  constructor({ onSelect, onToggle, expanded, label = 'Folders', icon: ic = 'folder' } = {}) {
    this.onSelect = onSelect; this.onToggle = onToggle; this.expanded = new Set(expanded || []); this.cur = null; this.nodes = []; this.ic = ic;
    this.el = h('div', { class: 'tree', role: 'tree', tabindex: 0, 'aria-label': label });
    this.el.addEventListener('keydown', (e) => this._key(e));
  }
  setNodes(nodes) { this.nodes = nodes; this.render(); }
  expandAll(on = true) { const walk = (ns) => ns.forEach((n) => { if (n.children?.length) { on ? this.expanded.add(n.id) : this.expanded.delete(n.id); walk(n.children); } }); walk(this.nodes); this.render(); }
  select(id, { silent = false } = {}) {
    this.cur = id;
    // make sure ancestors are open
    const path = (ns, acc) => { for (const n of ns) { if (n.id === id) return acc; const r = n.children && path(n.children, [...acc, n.id]); if (r) return r; } return null; };
    (path(this.nodes, []) || []).forEach((a) => this.expanded.add(a));
    this.render();
    this.el.querySelector('.tn.cur')?.scrollIntoView({ block: 'nearest' });
    if (!silent) this.onSelect?.(id, this._find(id));
  }
  _find(id) { const w = (ns) => { for (const n of ns) { if (n.id === id) return n; const r = n.children && w(n.children); if (r) return r; } return null; }; return w(this.nodes); }
  _flat() { const out = []; const w = (ns, d) => ns.forEach((n) => { out.push([n, d]); if (n.children?.length && this.expanded.has(n.id)) w(n.children, d + 1); }); w(this.nodes, 0); return out; }
  render() {
    const frag = document.createDocumentFragment();
    for (const [n, d] of this._flat()) {
      const has = !!n.children?.length, open = this.expanded.has(n.id);
      const row = h('div', { class: 'tn' + (this.cur === n.id ? ' cur' : '') + (n.dim ? ' dim' : ''), role: 'treeitem', 'aria-level': d + 1, 'aria-expanded': has ? String(open) : null, 'aria-selected': String(this.cur === n.id),
        style: { paddingLeft: 4 + d * 16 + 'px' }, dataset: { id: n.id },
        onclick: () => this.select(n.id) },
        h('button', { class: 'tw' + (open ? ' open' : ''), tabindex: -1, 'aria-label': open ? 'Collapse' : 'Expand', style: { visibility: has ? 'visible' : 'hidden' },
          onclick: (e) => { e.stopPropagation(); open ? this.expanded.delete(n.id) : this.expanded.add(n.id); this.onToggle?.(n.id, !open); this.render(); } }, icon('right')),
        icon(n.icon || this.ic), h('span', { class: 'tl', title: n.title || n.label }, n.label),
        n.count != null ? h('span', { class: 'tc' }, fmt.n(n.count)) : null);
      frag.append(row);
    }
    this.el.replaceChildren(frag);
  }
  _key(e) {
    const flat = this._flat(); const i = flat.findIndex(([n]) => n.id === this.cur);
    const cur = flat[i]?.[0];
    if (e.key === 'ArrowDown') { e.preventDefault(); const nx = flat[Math.min(flat.length - 1, i + 1)]; nx && this.select(nx[0].id); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); const pv = flat[Math.max(0, i - 1)]; pv && this.select(pv[0].id); }
    else if (e.key === 'ArrowRight' && cur?.children?.length) { e.preventDefault(); if (!this.expanded.has(cur.id)) { this.expanded.add(cur.id); this.render(); } else this.select(cur.children[0].id); }
    else if (e.key === 'ArrowLeft' && cur) { e.preventDefault(); if (this.expanded.has(cur.id) && cur.children?.length) { this.expanded.delete(cur.id); this.render(); } else { const d = flat[i][1]; for (let k = i - 1; k >= 0; k--) if (flat[k][1] < d) { this.select(flat[k][0].id); break; } } }
  }
}
/** Build a tree from folder paths: [{path,count}] → nodes (with the vault root as ''). */
export function folderNodes(paths, { rootLabel = '(vault root)', rootCount = null, countOf = () => null, titleOf = () => null } = {}) {
  const byId = new Map(); const root = { id: '', label: rootLabel, count: rootCount, children: [], icon: 'home' };
  byId.set('', root);
  const ensure = (p) => {
    if (byId.has(p)) return byId.get(p);
    const parentPath = p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '';
    const n = { id: p, label: baseName(p), count: countOf(p), title: titleOf(p), children: [] };
    byId.set(p, n); ensure(parentPath).children.push(n); return n;
  };
  [...paths].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' })).forEach(ensure);
  return [root];
}

// ---------------------------------------------------------------- pickers
/** Pick a folder on this computer (the vault). Uses the native dialog when running in a pywebview window. */
export async function chooseFolder({ start = '', title = 'Open a vault folder', confirmLabel = 'Open this folder' } = {}) {
  let path = start, data = null, sel = null, hidden = false;
  const input = h('input', { type: 'text', spellcheck: false, 'aria-label': 'Folder path' });
  const list = h('div', { class: 'picker-list', role: 'listbox' });
  const info = h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)', minHeight: '1.3em', marginTop: '6px' } });
  async function load(p) {
    try { data = await api.get('/api/fs', { path: p, hidden }); } catch (e) { info.textContent = e.message; info.style.color = 'var(--rose)'; return; }
    path = data.path; input.value = path; sel = null; info.style.color = ''; render();
  }
  function render() {
    const rows = [];
    if (data.parent) rows.push(h('button', { onclick: () => load(data.parent) }, icon('up'), '..'));
    for (const d of data.dirs) rows.push(h('button', { role: 'option', 'aria-selected': String(sel === d.path), onclick: () => { sel = d.path; render(); }, ondblclick: () => load(d.path) },
      icon('folder'), d.name, d.is_vault ? h('span', { class: 'badge ok plain' }, 'Obsidian vault') : null));
    if (!data.dirs.length) rows.push(h('div', { class: 'muted', style: { padding: '12px' } }, 'No sub-folders here.'));
    mount(list, rows);
    info.textContent = `${fmt.plural(data.md_count, 'note')} directly in this folder` + (data.is_vault ? ' · looks like an Obsidian vault' : '');
  }
  const roots = h('span', { class: 'row' });
  const body = h('div', null,
    h('div', { class: 'picker-path' },
      h('button', { class: 'btn icon', title: 'Up one level', onclick: () => data?.parent && load(data.parent) }, icon('up')),
      h('button', { class: 'btn icon', title: 'Home folder', onclick: () => load(data?.home || '') }, icon('home')), input,
      h('button', { class: 'btn', onclick: () => load(input.value) }, 'Go')),
    list, info,
    h('div', { class: 'row', style: { marginTop: '8px' } },
      h('label', { class: 'check muted' }, h('input', { type: 'checkbox', onchange: (e) => { hidden = e.target.checked; load(path); } }), 'Show hidden folders'), roots,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn sm', onclick: async () => { const n = await promptDialog({ title: 'New folder', placeholder: 'Folder name', validate: (v) => (v.trim() ? null : 'Enter a name') }); if (n) { try { const r = await api.post('/api/fs/mkdir', { path, name: n.trim() }); await load(r.path); } catch (e) { toastError(e); } } } }, icon('plus'), 'New folder')));
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); load(input.value); } });
  const native = window.pywebview?.api?.pick_folder;
  const d = dialog({ title, size: 'wide', body, actions: [
    ...(native ? [{ label: 'Use system dialog', value: 'native', keepOpen: false }] : []),
    { label: 'Cancel', value: null }, { label: confirmLabel, kind: 'primary', value: 'ok' }] });
  await load(start);
  if (data) data.roots?.length > 1 && mount(roots, data.roots.map((r) => h('button', { class: 'btn sm', onclick: () => load(r) }, r)));
  const r = await d.result;
  if (r === 'native') return await window.pywebview.api.pick_folder();
  return r === 'ok' ? (sel || path) : null;
}

/** Choose a destination folder among known folders (planned first), or type a new one. descriptions: {path: text} */
export async function chooseDestination({ folders, current = '', title = 'Move to…', descriptions = {}, allowRoot = true, confirmLabel = 'Move here' }) {
  const input = h('input', { type: 'text', value: current, placeholder: 'Type a folder path, e.g. Research/AI', spellcheck: false });
  const list = h('div', { class: 'picker-list', style: { height: '260px' } });
  const hint = h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)', marginTop: '6px', minHeight: '1.3em' } });
  const all = [...(allowRoot ? [''] : []), ...folders];
  function draw() {
    const q = input.value.trim().toLowerCase();
    const shown = all.filter((p) => !q || p.toLowerCase().includes(q) || (descriptions[p] || '').toLowerCase().includes(q));
    mount(list, shown.map((p) => h('button', { 'aria-selected': String(p === input.value.trim()), onclick: () => { input.value = p; draw(); }, ondblclick: () => d.buttons.at(-1).click() },
      icon(p ? 'folder' : 'home'), h('span', { style: { flex: 'none' } }, p || '(vault root)'), descriptions[p] ? h('span', { class: 'muted', style: { overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 'var(--fs-sm)' } }, descriptions[p]) : null)));
    if (!shown.length) mount(list, h('div', { class: 'muted', style: { padding: '12px' } }, 'No match — the path you typed will be created.'));
    const v = input.value.trim().replace(/^\/+|\/+$/g, '');
    hint.textContent = v && !folders.includes(v) ? `“${v}” is a new folder.` : '';
  }
  input.addEventListener('input', draw);
  const d = dialog({ title, size: 'wide', body: h('div', null, h('label', { class: 'field' }, 'Folder', input), list, hint),
    actions: [{ label: 'Cancel', value: null }, { label: confirmLabel, kind: 'primary', value: 'ok' }] });
  draw();
  return (await d.result) === 'ok' ? input.value.trim().replace(/^\/+|\/+$/g, '') : null;
}
