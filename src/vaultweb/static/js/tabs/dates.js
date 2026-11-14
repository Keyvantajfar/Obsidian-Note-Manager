// Dates: find missing / broken / copy-time dates and fix them from file timestamps. Works offline.
import { h, mount, api, fmt, store } from '../core.js';
import { DataTable, emptyState, button, toast, toastError, promptDialog, statusBadge } from '../ui.js';

const LABEL = { missing: 'Missing', unreadable: 'Unreadable', format: 'Wrong format', later: 'Later than file', earlier: 'Earlier than file', ok: 'OK' };
const TONE = { missing: 'err', unreadable: 'err', format: 'warn', later: 'warn', earlier: 'plain', ok: 'ok' };

export function createDates(ctx) {
  const opt = store('dates:opt', { source: '', tolerance: 1, flag_earlier: false, filter: 'problems' });
  let data = { rows: [], counts: {}, sources: [], source_help: {}, spec: null };
  const o = () => opt.get(), set = (p) => { opt.set({ ...o(), ...p }); refresh(); };
  const short = (v) => (v ? v.replace('T', ' ').slice(0, 16) : '–');
  const table = new DataTable({
    label: 'Dates', checkbox: true, rowKey: (r) => r.rel, rowHeight: 36,
    columns: [
      { key: 'status', label: 'Status', width: '140px', sort: (a, b) => a.status.localeCompare(b.status), render: (r) => h('span', { class: 'badge ' + TONE[r.status] }, LABEL[r.status]) },
      { key: 'rel', label: 'Note', width: '1.4fr', sort: (a, b) => a.rel.localeCompare(b.rel), render: (r) => h('span', { title: r.rel }, r.rel.replace(/\.md$/, '')) },
      { key: 'current', label: 'Now in header', width: '160px', render: (r) => (r.current ? h('span', { class: r.status === 'ok' ? '' : 'muted' }, r.current) : h('span', { class: 'faint' }, '–')) },
      { key: 'file', label: 'File date', width: '140px', render: (r) => h('span', { class: 'muted' }, short(r.file_date)) },
      { key: 'proposed', label: 'Proposed', width: '150px', render: (r) => (r.proposed ? h('b', { style: { fontWeight: 600 } }, r.proposed) : h('span', { class: 'faint' }, '–')) },
      { key: 'delta', label: 'Off by', width: '90px', sort: (a, b) => (a.delta_days ?? 0) - (b.delta_days ?? 0), render: (r) => (r.delta_days != null ? h('span', { class: 'muted' }, Math.abs(r.delta_days) < 1 ? '<1 d' : `${Math.round(r.delta_days)} d`) : '') },
    ],
    onActivate: (r) => setByHand(r),
    onKey: (e, r) => { if (e.key.toLowerCase() === 'f') fixSelected(); },
  });
  const bar = h('div', { class: 'toolbar' }), chips = h('div', { class: 'toolbar', style: { gap: '6px' } });

  async function fixSelected() {
    const t = table.targets().filter((r) => r.status !== 'ok'); if (!t.length) return toast('Nothing to fix in the selection.', 'info', 1500);
    try { const r = await api.post('/api/dates/fix', { rels: t.map((x) => x.rel), source: data.source, tolerance: o().tolerance, flag_earlier: o().flag_earlier }); toast(`Staged ${fmt.plural(r.staged, 'date fix', 'date fixes')}. Review them in Stage.`, 'ok'); } catch (e) { toastError(e); }
  }
  async function fixAll() {
    try { const r = await api.post('/api/dates/fix', { filter: 'problems', source: data.source, tolerance: o().tolerance, flag_earlier: o().flag_earlier }); toast(`Staged ${fmt.plural(r.staged, 'date fix', 'date fixes')}. Review them in Stage.`, r.staged ? 'ok' : 'info'); } catch (e) { toastError(e); }
  }
  async function setByHand(r) {
    const v = await promptDialog({ title: `Set date · ${r.rel}`, label: 'Date', value: r.current || r.proposed || '', description: 'e.g. 2024-09-16 or 2024-09-16T17:18', confirmLabel: 'Stage date' });
    if (v == null) return;
    try { await api.post('/api/dates/set', { rel: r.rel, text: v }); toast('Date staged.', 'ok', 1500); } catch (e) { toastError(e); }
  }
  function build() {
    const c = data.counts, problems = ['missing', 'unreadable', 'format', 'later', 'earlier'].reduce((a, k) => a + (c[k] || 0), 0);
    const src = h('select', { 'aria-label': 'Date source', onchange: (e) => set({ source: e.target.value }), title: data.source_help[data.source] || '' }, data.sources.map((s) => h('option', { value: s, selected: s === data.source }, { earliest: 'Earliest of created / modified', mtime: 'Last modified', birth_or_mtime: 'Created', name_or_earliest: 'From file name, else earliest' }[s] || s)));
    mount(chips, ['all', 'problems', 'missing', 'unreadable', 'format', 'later', 'earlier', 'ok'].map((k) => {
      const n = k === 'all' ? data.rows.length : k === 'problems' ? problems : c[k] || 0;
      return h('button', { class: 'btn sm' + (o().filter === k ? ' primary' : ''), onclick: () => { opt.set({ ...o(), filter: k }); draw(); } }, (k === 'all' ? 'All' : k === 'problems' ? 'Problems' : LABEL[k]) + ` ${n}`);
    }));
    mount(bar, h('label', { class: 'row', style: { gap: '6px' } }, h('span', { class: 'muted' }, 'Date from'), src),
      h('label', { class: 'row', style: { gap: '6px' }, title: 'Dates within this many days of the file date count as fine' }, h('span', { class: 'muted' }, 'Tolerance'), h('input', { type: 'number', min: 0, step: 0.5, value: o().tolerance, style: { width: '66px' }, onchange: (e) => set({ tolerance: +e.target.value || 0 }) }), h('span', { class: 'muted' }, 'days')),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: o().flag_earlier, onchange: (e) => set({ flag_earlier: e.target.checked }) }), 'Flag dates earlier than the file'),
      h('span', { class: 'grow' }), button('Fix selected', { size: 'sm', onClick: fixSelected }), button(`Fix all ${problems} problems`, { size: 'sm', kind: 'primary', icon: 'check', onClick: fixAll }));
    bar.lastChild.disabled = !problems;
  }
  function draw() {
    const f = o().filter, p = (r) => r.status !== 'ok';
    table.setEmpty(emptyState({ title: data.spec ? 'No notes in this view' : 'No date field', text: data.spec ? 'Pick another filter above.' : 'The header schema has no date field. Add one in Settings.' }));
    table.setRows(data.rows.filter((r) => f === 'all' || (f === 'problems' ? p(r) : r.status === f)));
    build();
  }
  async function refresh() {
    try { data = await api.get('/api/dates', { source: o().source, tolerance: o().tolerance, flag_earlier: o().flag_earlier }); } catch (e) { return toastError(e); }
    draw();
  }
  const el = h('div', { style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 } }, bar, chips, table.el);
  return { el, refresh, activate: () => table.el.focus({ preventScroll: true }) };
}
