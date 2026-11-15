// App shell: boot, routing, rail, status strip (with the proposals meter), command bar, shortcuts, theme, quit.
import { $, h, mount, clear, icon, api, bus, connectEvents, debounce, store, fmt, isTyping } from './core.js';
import { toast, toastError, dialog, confirmDialog, menu, button, emptyState } from './ui.js';
import { renderHome } from './home.js';
import { TABS } from './tabs/index.js';

const root = document.getElementById('app');
const S = { app: null, st: null, inst: {}, views: {}, tabBtns: {}, active: null, log: [], shell: null, ref: {}, shellKey: '' };
const theme = store('theme', 'system');
const MODES = [
  ['manual', 'Manual', 'Nothing runs on its own: you start AI runs and accept every change.'],
  ['assist', 'Assist', 'New and changed notes are tagged in the background; you still accept every change.'],
  ['auto', 'Auto', 'Confident proposals are accepted for you (and applied if auto-apply is on in Settings).'],
];

// ---------------------------------------------------------------- context handed to every tab
export const ctx = {
  get st() { return S.st; }, get app() { return S.app; }, api, bus, toast, toastError,
  goto, refreshStatus, openHelp, log: S.log,
  async act(btn, fn) { const was = btn?.disabled; if (btn) btn.disabled = true; try { return await fn(); } catch (e) { toastError(e); } finally { if (btn) btn.disabled = was ?? false; } },
  setBadge(id, n, cls = '') { const b = S.tabBtns[id]?.querySelector('.dot'); if (!b) return; b.hidden = !n; b.textContent = n > 99 ? '99+' : n; b.className = 'dot ' + cls; },
};

// ---------------------------------------------------------------- theme
function applyTheme() {
  const t = theme.get();
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
function cycleTheme() {
  const order = ['system', 'light', 'dark']; theme.set(order[(order.indexOf(theme.get()) + 1) % 3]); applyTheme();
  toast(`Theme: ${theme.get()}`, 'info', 1400);
  if (S.ref.themeBtn) mount(S.ref.themeBtn, icon(themeIcon()), h('span', { class: 'label' }, 'Theme'));
}
const themeIcon = () => (theme.get() === 'light' ? 'sun' : theme.get() === 'dark' ? 'moon' : 'sliders');

// ---------------------------------------------------------------- status
let statusBusy = false;
export async function refreshStatus() {
  if (statusBusy) return; statusBusy = true;
  try { S.st = await api.get('/api/status'); paintStatus(); } catch (e) { /* server gone: the connection banner handles it */ } finally { statusBusy = false; }
  return S.st;
}
const refreshStatusSoon = debounce(refreshStatus, 120);

function paintStatus() {
  const st = S.st, r = S.ref;
  if (!st?.open || !r.name) return;
  r.name.textContent = st.vault.name; r.name.title = st.vault.path;
  r.ws.firstChild.nextSibling.textContent = S.app.workflows[st.workflow].title;
  r.model.textContent = st.offline ? 'Works offline' : st.model; r.model.title = st.offline ? 'This workspace never calls a model' : `Model: ${st.model}`;
  r.mode.hidden = st.offline;
  r.mode.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === st.mode)));
  const j = st.jobs; r.job.hidden = !j.active;
  if (j.active) {
    r.job.className = 'job-pill' + (st.paused ? ' paused' : '');
    r.jobText.textContent = `${st.paused ? 'Paused' : 'Running'} ${fmt.n(j.finished)}/${fmt.n(j.total)}` + (!st.paused && j.rate ? ` · ${fmt.rate(j.rate)}` : '');
    r.jobBar.style.width = (j.total ? (100 * j.finished) / j.total : 0) + '%';
    r.pauseBtn.replaceChildren(icon(st.paused ? 'play' : 'pause')); r.pauseBtn.title = st.paused ? 'Resume (P)' : 'Pause (P)';
  }
  const review = st.stage.proposed || 0, ready = st.stage.accepted || 0;
  r.reviewN.textContent = fmt.n(review); r.readyN.textContent = fmt.n(ready);
  r.ready.disabled = !ready; r.review.disabled = !S.st.tabs.includes('stage');
  r.review.title = review ? `${fmt.plural(review, 'change')} waiting for your review` : 'Nothing waiting for review';
  r.ready.title = ready ? `Apply ${fmt.plural(ready, 'accepted change')}…` : 'Accept changes first, then apply them here';
  ctx.setBadge('stage', review, '');
  ctx.setBadge('jobs', j.active, 'run');
  document.title = (review ? `(${review}) ` : '') + st.vault.name + ' — Vault Manager';
}

// ---------------------------------------------------------------- shell
function shellKey() { return S.st.workflow + '|' + S.st.tabs.join(','); }

function buildShell() {
  const st = S.st; S.inst = {}; S.views = {}; S.tabBtns = {}; S.active = null; S.ref = {};
  const r = S.ref;
  const tabs = st.tabs.map((id, i) => {
    const def = TABS[id];
    return (S.tabBtns[id] = h('button', { class: 'tab', role: 'tab', id: 'tab-' + id, 'aria-selected': 'false', 'aria-controls': 'view-' + id, title: `${def.title} (${i + 1})`, onclick: () => goto(id) },
      icon(def.icon), h('span', { class: 'label' }, def.title), h('span', { class: 'k' }, String(i + 1)), h('span', { class: 'dot', hidden: true })));
  });
  r.themeBtn = h('button', { class: 'tab', onclick: cycleTheme, title: 'Switch theme' }, icon(themeIcon()), h('span', { class: 'label' }, 'Theme'));
  const rail = h('nav', { class: 'rail', 'aria-label': 'Sections' },
    h('div', { class: 'brand' }, h('img', { src: '/static/icon.png', alt: '' }), h('b', null, 'Vault Manager')),
    h('div', { class: 'tabs', role: 'tablist', 'aria-orientation': 'vertical' }, tabs),
    h('div', { class: 'rail-foot' },
      h('button', { class: 'tab', onclick: () => showHome(), title: 'Vaults and workspaces' }, icon('home'), h('span', { class: 'label' }, 'Home')),
      r.themeBtn,
      h('button', { class: 'tab', onclick: openHelp, title: 'Help and shortcuts (?)' }, icon('help'), h('span', { class: 'label' }, 'Help')),
      h('button', { class: 'tab', onclick: quit, title: 'Quit (Q)' }, icon('power'), h('span', { class: 'label' }, 'Quit'))));

  r.name = h('button', { class: 'vault-name btn ghost', style: { height: 'auto', padding: '2px 6px' }, onclick: (e) => menu(e.currentTarget, [
    { label: 'Rescan vault', icon: 'refresh', onClick: () => api.post('/api/scan').then(() => toast('Vault rescanned', 'ok', 1800)).catch(toastError) },
    { label: 'Open another vault…', icon: 'folder', onClick: () => showHome() }]) });
  r.ws = h('button', { class: 'ws-chip', title: 'Switch workspace', onclick: (e) => menu(e.currentTarget, Object.entries(S.app.workflows).map(([id, w]) => ({
    label: w.title, checked: id === S.st.workflow, hint: w.needs_llm ? '' : 'offline', onClick: () => switchWorkflow(id) }))) }, icon('grid'), h('span', { class: 'full' }, ''));
  r.model = h('span', { class: 'model' });
  r.mode = h('div', { class: 'seg mode', role: 'group', 'aria-label': 'Automation level' }, MODES.map(([v, label, tip]) =>
    h('button', { dataset: { v }, title: tip, 'aria-pressed': 'false', onclick: () => setMode(v) }, label)));
  r.jobText = h('span'); r.jobBar = h('i');
  r.pauseBtn = h('button', { class: 'btn icon sm ghost', onclick: () => api.post('/api/jobs-all/toggle').then(refreshStatus).catch(toastError) }, icon('pause'));
  r.job = h('span', { class: 'row', style: { gap: '2px' }, hidden: true },
    h('button', { class: 'job-pill', onclick: () => st.tabs.includes('jobs') && goto('jobs'), title: 'Open jobs' }, r.jobText, h('span', { class: 'bar' }, r.jobBar)), r.pauseBtn);
  r.reviewN = h('b'); r.readyN = h('b');
  r.review = h('button', { class: 'review', onclick: () => goto('stage') }, h('span', { class: 'tick' }), r.reviewN, 'to review');
  r.ready = h('button', { class: 'ready', onclick: () => { goto('stage'); setTimeout(() => bus.emit('stage:apply'), 0); } }, h('span', { class: 'tick' }), r.readyN, 'ready · Apply');
  const status = h('header', { class: 'status' }, r.name, r.ws, h('span', { class: 'spacer' }), r.model, r.mode, r.job, h('div', { class: 'meter', role: 'group', 'aria-label': 'Proposed changes' }, r.review, r.ready));

  const main = h('main', { class: 'main' });
  for (const id of st.tabs) { S.views[id] = h('section', { class: 'view', id: 'view-' + id, role: 'tabpanel', 'aria-labelledby': 'tab-' + id, hidden: true }); main.append(S.views[id]); }

  r.cmdInput = h('input', { type: 'text', placeholder: 'Tell the AI what to do, add a rule like  tag:ML -> move:Research/AI , or type / for commands', spellcheck: false, 'aria-label': 'Command', autocomplete: 'off' });
  const cmd = h('form', { class: 'cmd', onsubmit: (e) => { e.preventDefault(); runCommand(); } }, h('span', { class: 'prompt' }, '›'), r.cmdInput,
    h('kbd', { title: 'Press / to focus the command line' }, '/'));
  const hist = store('cmdhist', []); let hi = -1;
  r.cmdInput.addEventListener('keydown', (e) => {
    const list = hist.get();
    if (e.key === 'ArrowUp' && list.length) { e.preventDefault(); hi = Math.min(list.length - 1, hi + 1); r.cmdInput.value = list[hi]; }
    else if (e.key === 'ArrowDown') { e.preventDefault(); hi = Math.max(-1, hi - 1); r.cmdInput.value = hi < 0 ? '' : list[hi]; }
    else if (e.key === 'Escape') { r.cmdInput.blur(); }
  });
  S.cmdHist = { get: hist.get, push(t) { hist.set([t, ...hist.get().filter((x) => x !== t)].slice(0, 40)); hi = -1; } };

  S.shell = h('div', { class: 'shell' }); S.shell.append(rail, status, main, cmd);
  mount(root, S.shell); S.shellKey = shellKey();
  paintStatus();
}

async function runCommand() {
  const input = S.ref.cmdInput, text = input.value.trim(); if (!text) return;
  input.disabled = true;
  try {
    const r = await api.post('/api/command', { text });
    S.cmdHist.push(text); input.value = '';
    if (r.message) toast(r.message, r.level === 'error' ? 'err' : 'info');
    if (r.goto && S.st.tabs.includes(r.goto)) goto(r.goto);
    if (r.action === 'help') openHelp();
    else if (r.action === 'scan') { await api.post('/api/scan'); toast('Vault rescanned', 'ok', 1800); }
    else if (r.action === 'apply' || r.action === 'undo') setTimeout(() => bus.emit('stage:' + r.action), 0);
  } catch (e) { toastError(e); } finally { input.disabled = false; input.focus(); refreshStatus(); }
}

async function setMode(v) {
  try { await api.put('/api/settings', { auto_level: v }); await refreshStatus(); toast(`Automation: ${MODES.find((m) => m[0] === v)[1]}`, 'info', 1600); } catch (e) { toastError(e); }
}
async function switchWorkflow(id) {
  if (id === S.st.workflow) return;
  try { await api.post('/api/workflow', { id }); await refreshStatus(); showWork(S.app.workflows[id].landing); } catch (e) { toastError(e); }
}

// ---------------------------------------------------------------- routing
export function goto(id) {
  if (!S.st?.open) return showHome();
  if (!S.shell || S.shellKey !== shellKey()) buildShell();
  if (!S.st.tabs.includes(id)) { toast(`“${TABS[id]?.title || id}” isn't part of the ${S.app.workflows[S.st.workflow].title} workspace.`, 'warn'); return; }
  if (S.active === id) return;
  if (S.active) { S.inst[S.active]?.deactivate?.(); S.views[S.active].hidden = true; S.tabBtns[S.active].setAttribute('aria-selected', 'false'); }
  S.active = id;
  if (!S.inst[id]) {
    try { S.inst[id] = TABS[id].create(ctx); S.views[id].append(S.inst[id].el); S.inst[id].stale = true; }
    catch (e) { console.error(e); S.views[id].append(emptyState({ title: 'This tab failed to load', text: String(e.message || e) })); S.inst[id] = {}; }
  }
  S.views[id].hidden = false; S.tabBtns[id].setAttribute('aria-selected', 'true');
  history.replaceState(null, '', '#/' + id);
  const inst = S.inst[id]; inst.activate?.();
  if (inst.stale) { inst.stale = false; inst.refresh?.(); }
}
function showWork(tab) {
  if (!S.shell || S.shellKey !== shellKey()) buildShell();
  const want = tab || location.hash.replace('#/', '');
  const id = S.st.tabs.includes(want) ? want : S.app.workflows[S.st.workflow].landing;
  S.active && S.views[S.active] && (S.views[S.active].hidden = true);
  S.active = null; goto(id);
}
function showHome() {
  S.shell = null; S.active = null; S.shellKey = '';
  history.replaceState(null, '', '#/home');
  document.title = 'Vault Manager';
  renderHome(root, { app: S.app, st: S.st, onEnter: async () => { await refreshStatus(); showWork(); }, onStatus: (st) => { S.st = st; } });
}

// server → tabs: refresh the active tab, mark the others stale
const refreshActive = debounce(() => { const i = S.inst[S.active]; if (i) { i.stale = false; i.refresh?.(); } }, 120);
function onServer(d) {
  if (d.event === 'hello' || d.event === 'bye') return;
  if (['stage', 'scan', 'job', 'applied', 'undone', 'settings', 'workflow', 'vault', 'taxonomy', 'rules', 'resync'].includes(d.event)) refreshStatusSoon();
  if (d.event === 'message') { S.log.unshift({ t: Date.now(), text: d.text }); S.log.length = Math.min(S.log.length, 300); toast(d.text, /fail|error|cannot|could not/i.test(d.text) ? 'warn' : 'info'); bus.emit('log'); }
  else if (d.event === 'applied') toast(`Applied ${fmt.plural(d.applied, 'change')}` + (d.failed ? ` — ${d.failed} failed` : '') + '. Undo is available in Stage.', d.failed ? 'warn' : 'ok');
  else if (d.event === 'undone') toast(`Undone: ${fmt.plural(d.restored + d.moved_back, 'note')} restored` + (d.conflicts ? `, ${d.conflicts} skipped (changed since)` : ''), d.conflicts ? 'warn' : 'ok');
  else if (d.event === 'job' && d.kind === 'finished') toast(`${d.title || 'Job'} finished` + (d.errors ? ` — ${fmt.plural(d.errors, 'error')}` : ''), d.errors ? 'warn' : 'ok');
  else if (d.event === 'confirm_needed') toast(`${fmt.plural(d.count, 'accepted change')} exceed the auto-apply cap. Review them in Stage.`, 'warn', 8000);
  else if (d.event === 'vault' && d.state === 'closed' && S.shell) showHome();
  if (!S.shell) return;
  for (const id of S.st?.tabs || []) {
    const def = TABS[id], inst = S.inst[id];
    if (!inst || !(def.watch || []).includes(d.event) && d.event !== 'resync') continue;
    if (id === S.active) refreshActive(); else inst.stale = true;
  }
}

// ---------------------------------------------------------------- help, quit, shortcuts
function openHelp() {
  const sc = [['1 … 9', 'Switch tab'], ['/', 'Focus the command line'], ['P', 'Pause or resume running jobs'], ['M', 'Cycle automation: manual → assist → auto'], ['?', 'This help'], ['Q', 'Quit'],
    ['↑ ↓ PgUp PgDn', 'Move in lists'], ['Space', 'Tick a row'], ['Shift+↑↓ / Ctrl+A', 'Extend / select all'], ['Enter', 'Open or edit the row'], ['Esc', 'Close a dialog or clear ticks']];
  const cmds = [['/mode manual|assist|auto', 'Automation level'], ['/run [tag|route|all]', 'Run the AI on notes that still miss something'], ['/apply   /undo', 'Open the Apply or Undo dialog'], ['/pause  /resume  /stop', 'Control running jobs'],
    ['/workers N   /batch N   /threshold X', 'Speed and confidence settings'], ['/freeze   /unfreeze', 'Lock the folder plan'], ['/goto tab', 'Jump to a tab'], ['/scan', 'Rescan the vault']];
  dialog({ title: 'Help', size: 'wide', body: h('div', { class: 'stack' },
    h('p', { class: 'muted' }, 'The AI only proposes. Nothing is written to your notes until you apply it in Stage, and every apply can be undone.'),
    h('h3', null, 'Shortcuts'), h('dl', { class: 'kv' }, sc.flatMap(([k, v]) => [h('dt', null, h('kbd', null, k)), h('dd', null, v)])),
    h('h3', null, 'Command line'), h('p', { class: 'muted' }, 'Plain text becomes a standing instruction for the AI. Prefix with @folder: or #tag: to scope it. Rules use  condition -> action  (see the Rules tab).'),
    h('dl', { class: 'kv' }, cmds.flatMap(([k, v]) => [h('dt', null, h('code', null, k)), h('dd', null, v)])),
    h('p', { class: 'faint' }, `Vault Manager ${S.app.version} · log file: ${S.app.log}`)), actions: [{ label: 'Close', kind: 'primary', value: null }] });
}
async function quit() {
  const ok = await confirmDialog({ title: 'Quit Vault Manager?', message: 'Staged changes are kept and will be here next time. Running AI jobs stop.', confirmLabel: 'Quit', danger: true });
  if (!ok) return;
  try { await api.post('/api/quit'); } catch { /* already gone */ }
  mount(root, h('div', { class: 'empty', style: { height: '100%' } }, h('img', { src: '/static/icon.png', width: 56, height: 56, alt: '', style: { borderRadius: '14px' } }),
    h('h3', null, 'Vault Manager has stopped'), h('p', null, window.__VM__?.desktop ? 'This window will close in a moment.' : 'You can close this browser tab.')));
  setTimeout(() => window.close(), 400);
}

document.addEventListener('keydown', (e) => {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || !S.shell) return;
  if (isTyping()) return;
  const k = e.key;
  if (/^[1-9]$/.test(k)) { const id = S.st.tabs[+k - 1]; if (id) { e.preventDefault(); goto(id); } }
  else if (k === '/' || k === ':') { e.preventDefault(); S.ref.cmdInput.focus(); if (k === '/') S.ref.cmdInput.value = '/'; }
  else if (k === '?') { e.preventDefault(); openHelp(); }
  else if (k === 'p' || k === 'P') { api.post('/api/jobs-all/toggle').then(refreshStatus).catch(toastError); }
  else if (k === 'm' || k === 'M') { const i = MODES.findIndex((m) => m[0] === S.st.mode); if (!S.st.offline) setMode(MODES[(i + 1) % 3][0]); }
  else if (k === 'q' || k === 'Q') quit();
});

// ---------------------------------------------------------------- connection banner & boot
let banner = null;
bus.on('conn', ({ up }) => {
  if (!up && !banner) { banner = h('div', { class: 'toast err', style: { position: 'fixed', top: '10px', left: '50%', transform: 'translateX(-50%)', zIndex: 90 } }, 'Lost connection to the app — trying to reconnect…'); document.body.append(banner); }
  if (up && banner) { banner.remove(); banner = null; refreshStatus(); }
});
bus.on('srv', onServer);
bus.on('srv:resync', () => { refreshStatus(); if (S.shell) for (const id of Object.keys(S.inst)) S.inst[id].stale = true, id === S.active && refreshActive(); });
bus.on('stage:open', () => goto('stage'));

async function boot() {
  applyTheme();
  try { S.app = await api.get('/api/app'); } catch (e) {
    mount(root, emptyState({ title: 'Cannot reach Vault Manager', text: 'The app is not running any more. Start it again and reload this page.' })); return;
  }
  S.st = S.app.status; connectEvents();
  if (S.st.open && location.hash !== '#/home') showWork(); else showHome();
}
boot();
export { S };
