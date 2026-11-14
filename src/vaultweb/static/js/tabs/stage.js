// Stage: everything waiting to be written. Review, edit, accept, apply, undo.
import { h, mount, api, bus, fmt, baseName, store } from '../core.js';
import { DataTable, statusBadge, sourceBadge, confBadge, emptyState, button, iconButton, menu, toast, toastError, dialog, confirmDialog } from '../ui.js';
import { changeSummary, kindLabel, previewChange, editChange, applyDialog, undoDialog } from './shared.js';

export function createStage(ctx) {
  const filt = store('stage:filter', { status: 'all', kind: 'all' });
  let data = { changes: [], counts: {}, threshold: 0.85 };
  let batches = [];
  const f = () => filt.get();

  const table = new DataTable({
    label: 'Staged changes', checkbox: true, rowHeight: 38, rowKey: (c) => c.id, autoCursor: true,
    columns: [
      { key: 'status', label: 'Status', width: '104px', render: (c) => statusBadge(c.status), sort: (a, b) => a.status.localeCompare(b.status) },
      { key: 'kind', label: 'Type', width: '70px', render: (c) => kindLabel(c.kind), sort: (a, b) => a.kind.localeCompare(b.kind) },
      { key: 'title', label: 'Note', width: '1.1fr', render: (c) => h('span', { title: c.note }, c.title, c.locked ? h('span', { class: 'faint' }, '  🔒') : null), sort: (a, b) => a.title.localeCompare(b.title) },
      { key: 'summary', label: 'Change', width: '2fr', render: (c) => changeSummary(c) },
      { key: 'source', label: 'By', width: '66px', render: (c) => sourceBadge(c.source), sort: (a, b) => a.source.localeCompare(b.source) },
      { key: 'confidence', label: 'Confidence', width: '110px', render: (c) => confBadge(c.confidence, data.threshold), sort: (a, b) => (a.confidence ?? 2) - (b.confidence ?? 2) },
    ],
    onActivate: (c) => preview(c), onContext: (c, e) => rowMenu(c, { x: e.clientX, y: e.clientY }),
    onSelect: () => paintBar(), onCursor: () => paintBar(),
    onKey: (e, c) => {
      if (!c) return;
      const k = e.key.toLowerCase();
      if (k === 'y' || k === 'a') setStatus('accepted'); else if (k === 'n' || k === 'r') setStatus('rejected'); else if (k === 'e') edit(c);
      else if (k === 'delete' || k === 'backspace') remove();
    },
  });
  const seg = (key, opts) => h('div', { class: 'seg' }, opts.map(([v, l]) => h('button', { 'aria-pressed': String(f()[key] === v), onclick: () => { filt.set({ ...f(), [key]: v }); redraw(); } }, l)));
  const bar = h('div', { class: 'toolbar' });
  const foot = h('div', { class: 'dt-foot' });
  const el = h('div', { class: 'view-in', style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 } }, bar, table.el, foot);

  const targets = () => table.targets();
  async function setStatus(status) {
    const t = targets(); if (!t.length) return;
    try { await api.post('/api/stage/status', { status, ids: t.map((c) => c.id) }); } catch (e) { toastError(e); }
  }
  async function remove() {
    const t = targets(); if (!t.length) return;
    try { await api.post('/api/stage/remove', { ids: t.map((c) => c.id) }); toast(`Removed ${fmt.plural(t.length, 'staged change')}.`, 'info', 1600); } catch (e) { toastError(e); }
  }
  const preview = (c) => previewChange(c.id, { onAction: () => {} });
  const edit = async (c) => { try { await editChange(c); } catch (e) { toastError(e); } };
  function rowMenu(c, at) {
    menu(at, [
      { label: 'Preview', icon: 'eye', onClick: () => preview(c) }, '-',
      { label: 'Accept', icon: 'check', hint: 'Y', onClick: () => setStatus('accepted') }, { label: 'Reject', icon: 'x', hint: 'N', onClick: () => setStatus('rejected') },
      { label: 'Edit…', icon: 'edit', hint: 'E', onClick: () => edit(c) }, '-',
      { label: 'Remove from stage', icon: 'trash', hint: 'Del', onClick: remove }]);
  }

  async function acceptAll(minConf) {
    try { const r = await api.post('/api/stage/accept-all', minConf === undefined ? {} : { min_confidence: minConf }); toast(`Accepted ${fmt.plural(r.accepted, 'change')}.`, 'ok', 2000); } catch (e) { toastError(e); }
  }
  async function history() {
    try { batches = (await api.get('/api/batches')).batches; } catch (e) { return toastError(e); }
    const body = batches.length ? h('div', { class: 'stack' }, batches.map((b) => h('div', { class: 'row', style: { padding: '6px 0', borderBottom: '1px solid var(--line)' } },
      h('div', { class: 'grow' }, h('b', null, b.label || 'apply'), h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, `${fmt.date(b.time)} · ${fmt.plural(b.ops, 'operation')}`)),
      b.undone ? h('span', { class: 'badge plain' }, 'Undone') : button('Undo', { size: 'sm', onClick: async () => { if (await undoDialog(b.id)) d.close(); } })))) : emptyState({ title: 'No applies yet', text: 'Applied batches show up here so you can undo them.' });
    const d = dialog({ title: 'Apply history', body, actions: [{ label: 'Close', value: null, kind: 'primary' }] });
  }

  let applyBtn, undoBtn, selInfo;
  function paintBar() {
    const c = data.counts || {}, t = targets().length, ready = c.accepted || 0;
    if (applyBtn) { applyBtn.disabled = !ready; applyBtn.lastChild.textContent = ready ? `Apply ${ready}…` : 'Apply…'; }
    if (selInfo) selInfo.textContent = table.sel.size ? `${table.sel.size} ticked` : '';
    foot.textContent = '';
    foot.append(h('span', null, `${fmt.n(c.proposed || 0)} to review`), h('span', null, `${fmt.n(c.accepted || 0)} ready`), h('span', null, `${fmt.n(c.rejected || 0)} rejected`),
      h('span', { class: 'grow' }), h('span', null, t ? `Y accept · N reject · E edit · Enter preview · Space tick` : ''));
  }
  function buildBar() {
    selInfo = h('span', { class: 'muted' });
    applyBtn = button('Apply…', { icon: 'check', kind: 'primary', onClick: () => doApply() });
    undoBtn = button('Undo', { icon: 'undo', onClick: () => doUndo() });
    mount(bar,
      seg('status', [['all', 'All'], ['proposed', 'To review'], ['accepted', 'Ready'], ['rejected', 'Rejected']]),
      seg('kind', [['all', 'Any'], ['header', 'Headers'], ['move', 'Moves']]),
      h('span', { class: 'grow' }), selInfo,
      button('Accept', { size: 'sm', onClick: () => setStatus('accepted') }), button('Reject', { size: 'sm', onClick: () => setStatus('rejected') }),
      button('Edit', { size: 'sm', onClick: () => table.cursorRow && edit(table.cursorRow) }), button('Remove', { size: 'sm', onClick: remove }),
      iconButton('more', 'More', (e) => menu(e.currentTarget, [
        { label: 'Accept everything to review', onClick: () => acceptAll() },
        { label: `Accept all ≥ ${data.threshold} confidence`, onClick: () => acceptAll('threshold') }, '-',
        { label: 'Clear rejected', onClick: async () => { try { const r = await api.post('/api/stage/clear-rejected'); toast(`Removed ${fmt.plural(r.removed, 'rejected change')}.`, 'info', 1600); } catch (er) { toastError(er); } } },
        { label: 'Apply history…', onClick: history }])),
      undoBtn, applyBtn);
    paintBar();
  }
  async function doApply() { await applyDialog(); }
  async function doUndo() { await undoDialog(); }

  function redraw() {
    const { status, kind } = f();
    const rows = data.changes.filter((c) => (status === 'all' || c.status === status) && (kind === 'all' || c.kind === kind) && c.exists);
    table.setEmpty(data.changes.length ? emptyState({ title: 'Nothing matches this filter', text: 'Switch the filter above to see other changes.' })
      : emptyState({ title: 'The stage is empty', text: 'Run the AI from the Tagger or edit a header by hand in the Explorer. Proposals land here first — nothing is written until you apply.' }));
    table.setRows(rows);
    buildBar();
  }
  async function refresh() {
    try { data = await api.get('/api/stage'); } catch (e) { return toastError(e); }
    redraw();
  }
  const offs = [bus.on('stage:apply', () => doApply()), bus.on('stage:undo', () => doUndo())];
  return { el, refresh, activate: () => table.el.focus({ preventScroll: true }), destroy: () => offs.forEach((o) => o()) };
}
