// Tagger: the AI's header proposals, reviewed one by one or in bulk, with a before/after diff.
import { h, mount, api, bus, fmt, store } from '../core.js';
import { DataTable, splitPane, statusBadge, sourceBadge, confBadge, emptyState, button, iconButton, menu, toast, toastError, diffView, kv } from '../ui.js';
import { changeSummary, editChange, previewChange } from './shared.js';

export function createTagger(ctx) {
  const filt = store('tagger:filter', 'proposed');
  let data = { changes: [], counts: {}, threshold: 0.85 }, notes = [];
  const table = new DataTable({
    label: 'Header proposals', checkbox: true, rowHeight: 38, rowKey: (c) => c.id,
    columns: [
      { key: 'status', label: 'Status', width: '100px', render: (c) => statusBadge(c.status) },
      { key: 'title', label: 'Note', width: '1fr', sort: (a, b) => a.title.localeCompare(b.title), render: (c) => h('span', { title: c.note }, c.title) },
      { key: 'summary', label: 'Proposed header', width: '2.4fr', render: (c) => changeSummary(c) },
      { key: 'source', label: 'By', width: '62px', render: (c) => sourceBadge(c.source) },
      { key: 'confidence', label: 'Confidence', width: '110px', sort: (a, b) => (a.confidence ?? 2) - (b.confidence ?? 2), render: (c) => confBadge(c.confidence, data.threshold) },
    ],
    onCursor: (c) => showDiff(c), onSelect: () => paintBar(), onActivate: (c) => previewChange(c.id), onContext: (c, e) => rowMenu({ x: e.clientX, y: e.clientY }),
    onKey: (e, c) => { const k = e.key.toLowerCase(); if (!c) return; if (k === 'y' || k === 'a') setStatus('accepted'); else if (k === 'n' || k === 'r') setStatus('rejected'); else if (k === 'e') edit(c); else if (k === 'u') unstage(); },
  });
  const bar = h('div', { class: 'toolbar' });
  const side = h('div', { class: 'inspector' });
  const targets = () => table.targets();

  async function setStatus(status) { const t = targets(); if (!t.length) return; try { await api.post('/api/stage/status', { status, ids: t.map((c) => c.id) }); } catch (e) { toastError(e); } }
  async function unstage() { const t = targets(); if (!t.length) return; try { await api.post('/api/stage/remove', { ids: t.map((c) => c.id) }); } catch (e) { toastError(e); } }
  async function edit(c) { try { await editChange(c); } catch (e) { toastError(e); } }
  async function lock() { const t = targets(); if (!t.length) return; try { await api.post('/api/note/lock', { paths: t.map((c) => c.note), locked: !t[0].locked }); } catch (e) { toastError(e); } }
  function rowMenu(at) { menu(at, [{ label: 'Accept', icon: 'check', hint: 'Y', onClick: () => setStatus('accepted') }, { label: 'Reject', icon: 'x', hint: 'N', onClick: () => setStatus('rejected') }, { label: 'Edit…', icon: 'edit', hint: 'E', onClick: () => table.cursorRow && edit(table.cursorRow) }, '-',
    { label: 'Lock note against the AI', icon: 'lock', onClick: lock }, { label: 'Discard proposal', icon: 'trash', hint: 'U', onClick: unstage }]); }

  async function run({ fresh = false, onlyMissing = true } = {}) {
    try {
      const r = await api.post('/api/ai/run', { header: true, route: false, only_unstaged: onlyMissing && !fresh, fresh, title: fresh ? 'Re-tag all notes' : 'Tag notes without a proposal' });
      if (!r.job) toast(r.message || 'Nothing to tag — every note already has a proposal or a full header.', 'info'); else ctx.toast(`Tagging ${fmt.plural(r.job.total, 'note')}…`, 'info', 2000);
    } catch (e) { toastError(e); }
  }
  async function acceptAbove() { try { const r = await api.post('/api/stage/accept-all', { min_confidence: 'threshold', kind: 'header' }); toast(`Accepted ${fmt.plural(r.accepted, 'proposal')} at ≥ ${data.threshold}.`, 'ok', 2200); } catch (e) { toastError(e); } }
  async function acceptEvery() { try { const r = await api.post('/api/stage/accept-all', { kind: 'header' }); toast(`Accepted ${fmt.plural(r.accepted, 'proposal')}.`, 'ok', 2200); } catch (e) { toastError(e); } }

  let info;
  function buildBar() {
    info = h('span', { class: 'muted' });
    const off = ctx.st?.offline;
    mount(bar,
      h('div', { class: 'seg' }, [['proposed', 'To review'], ['accepted', 'Ready'], ['rejected', 'Rejected'], ['all', 'All']].map(([v, l]) => h('button', { 'aria-pressed': String(filt.get() === v), onclick: () => { filt.set(v); redraw(); } }, l))),
      info, h('span', { class: 'grow' }),
      off ? null : button('Run on missing', { icon: 'sparkle', kind: 'primary', size: 'sm', onClick: () => run() }),
      off ? null : button('Re-run all', { icon: 'refresh', size: 'sm', onClick: () => run({ fresh: true }) }),
      button('Accept', { size: 'sm', icon: 'check', onClick: () => setStatus('accepted') }), button('Reject', { size: 'sm', icon: 'x', onClick: () => setStatus('rejected') }),
      iconButton('more', 'More', (e) => menu(e.currentTarget, [{ label: `Accept all ≥ ${data.threshold}`, onClick: acceptAbove }, { label: 'Accept every proposal', onClick: acceptEvery }])));
    paintBar();
  }
  function paintBar() { if (!info) return; const c = data.counts || {}; info.textContent = `${fmt.n(visible().length)} shown` + (table.sel.size ? ` · ${table.sel.size} ticked` : '') + `  ·  ${fmt.n(c.proposed || 0)} to review`; }
  const visible = () => data.changes.filter((c) => c.kind === 'header' && c.exists && (filt.get() === 'all' || c.status === filt.get()));

  let seq = 0;
  async function showDiff(c) {
    if (!c) return mount(side, emptyState({ title: 'Pick a proposal', text: 'The before and after of the header shows here.' }));
    const my = ++seq;
    try {
      const p = await api.get('/api/stage/preview', { id: c.id }); if (my !== seq) return;
      mount(side, h('div', { class: 'ins-head' }, h('h2', { style: { fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-xl)' } }, c.title), h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, c.note)),
        h('div', { class: 'row wrap', style: { padding: '0 14px 10px' } }, statusBadge(c.status), sourceBadge(c.source), confBadge(c.confidence, data.threshold)),
        c.reason ? h('div', { class: 'ins-sec' }, h('h3', null, 'Why'), h('p', null, c.reason)) : null,
        h('div', { class: 'ins-sec' }, h('h3', null, 'Header change'), diffView(p.diff)),
        p.warnings?.length ? h('div', { class: 'ins-sec' }, h('h3', null, 'Warnings'), h('div', { class: 'stack' }, p.warnings.map((w) => h('div', { class: 'badge warn', style: { height: 'auto', padding: '4px 8px', whiteSpace: 'normal' } }, w)))) : null,
        h('div', { class: 'ins-sec row wrap' }, button('Accept', { icon: 'check', kind: 'primary', size: 'sm', onClick: () => setStatus('accepted') }), button('Reject', { icon: 'x', size: 'sm', onClick: () => setStatus('rejected') }), button('Edit…', { icon: 'edit', size: 'sm', onClick: () => edit(c) })));
    } catch (e) { if (my === seq) mount(side, emptyState({ title: 'No preview', text: e.message })); }
  }
  function redraw() {
    const rows = visible();
    const none = !data.changes.some((c) => c.kind === 'header');
    table.setEmpty(none ? emptyState({ title: 'No header proposals yet', text: ctx.st?.offline ? 'This workspace is offline. Edit headers by hand in the Explorer.' : 'Press “Run on missing” and the AI will propose parent, status, tags and date for notes that lack them.' })
      : emptyState({ title: 'Nothing in this view', text: 'Try another filter.' }));
    table.setRows(rows); buildBar();
    if (!rows.length) showDiff(null);
  }
  async function refresh() { try { data = await api.get('/api/stage', { kind: 'header' }); } catch (e) { return toastError(e); } redraw(); if (table.cursorRow) showDiff(table.cursorRow); }

  const left = h('div', { style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 } }, bar, table.el);
  const sp = splitPane(left, side, { key: 'tagger-side', width: 640, min: 360, max: 1400 });
  sp.left.classList.replace('fixed', 'fill'); sp.right.classList.replace('fill', 'fixed');
  sp.left.style.cssText += ';flex:1 1 auto;display:flex;flex-direction:column;overflow:hidden';
  sp.right.style.cssText += ';flex:none;width:380px;background:var(--panel);border-left:1px solid var(--line)';
  mount(side, emptyState({ title: 'Pick a proposal', text: 'The before and after of the header shows here.' }));
  return { el: sp.el, refresh, activate: () => table.el.focus({ preventScroll: true }) };
}
