// Tags: the vocabulary of one header field, near-duplicate groups, rename / merge / delete (staged).
import { h, mount, api, fmt, store, debounce } from '../core.js';
import { DataTable, emptyState, button, toast, toastError, promptDialog, confirmDialog } from '../ui.js';

export function createTags(ctx) {
  const st = store('tags:key', '');
  let data = { keys: [], key: '', values: [], groups: [] }, text = '';
  const table = new DataTable({
    label: 'Values', checkbox: true, rowKey: (v) => v.value, rowHeight: 34,
    columns: [
      { key: 'value', label: 'Value', width: '2fr', sort: (a, b) => a.value.localeCompare(b.value, undefined, { sensitivity: 'base' }) },
      { key: 'count', label: 'Notes', width: '90px', sort: (a, b) => a.count - b.count, render: (v) => h('span', { style: { fontVariantNumeric: 'tabular-nums' } }, v.count) },
    ],
    onActivate: () => rename(),
    onKey: (e) => { const k = e.key.toLowerCase(); if (k === 'r' || k === 'f2') rename(); else if (k === 'delete') del(); },
  });
  const bar = h('div', { class: 'toolbar' }), side = h('div', { class: 'inspector' });
  const vals = () => table.targets().map((v) => v.value);
  async function stageRename(olds, nw) {
    try { const r = await api.post('/api/tags/rename', { key: data.key, olds, new: nw }); toast(r.staged ? `Staged on ${fmt.plural(r.staged, 'note')} — review in Stage.` : 'No note uses that value.', r.staged ? 'ok' : 'info'); } catch (e) { toastError(e); }
  }
  async function rename() {
    const v = vals(); if (!v.length) return toast('Tick or select a value first.', 'info', 1500);
    const nw = await promptDialog({ title: v.length > 1 ? `Merge ${v.length} values into one` : `Rename “${v[0]}”`, label: 'New value', value: v[0], confirmLabel: v.length > 1 ? 'Stage merge' : 'Stage rename', validate: (x) => (x.trim() ? null : 'Enter a value.') });
    if (nw != null) stageRename(v, nw.trim());
  }
  async function del() {
    const v = vals(); if (!v.length) return;
    if (await confirmDialog({ title: `Remove ${v.length > 1 ? v.length + ' values' : `“${v[0]}”`} from every note?`, message: 'Staged for review — nothing is written until you apply.', confirmLabel: 'Stage removal', danger: true })) stageRename(v, null);
  }
  function build() {
    mount(bar, h('select', { 'aria-label': 'Header field', onchange: (e) => { st.set(e.target.value); refresh(); } }, data.keys.map((k) => h('option', { value: k, selected: k === data.key }, k))),
      h('input', { type: 'search', placeholder: 'Filter values…', class: 'grow', oninput: debounce((e) => { text = e.target.value.toLowerCase(); draw(); }, 100) }),
      h('span', { class: 'muted' }, `${fmt.plural(data.values.length, 'value')}`), button('Rename / merge', { size: 'sm', icon: 'edit', onClick: rename }), button('Remove', { size: 'sm', icon: 'trash', onClick: del }));
  }
  function draw() {
    table.setEmpty(emptyState({ title: 'No values', text: 'Notes have no values for this field yet.' }));
    table.setRows(data.values.filter((v) => !text || v.value.toLowerCase().includes(text)));
    const counts = new Map(data.values.map((v) => [v.value, v.count]));
    mount(side, h('div', { class: 'ins-head' }, h('h2', { style: { fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-xl)' } }, 'Possible duplicates'), h('p', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, 'Spelling, case and plural variants of the same idea.')),
      data.groups.length ? data.groups.map((g) => {
        const best = [...g].sort((a, b) => (counts.get(b) || 0) - (counts.get(a) || 0))[0];
        return h('div', { class: 'ins-sec' }, h('div', { class: 'chips' }, g.map((v) => h('span', { class: 'chip', title: `${counts.get(v) || 0} notes` }, `${v} · ${counts.get(v) || 0}`))),
          button(`Merge into “${best}”`, { size: 'sm', icon: 'check', onClick: () => stageRename(g.filter((x) => x !== best), best) }));
      }) : h('div', { class: 'ins-sec muted' }, 'No near-duplicates found.'));
    build();
  }
  async function refresh() { try { data = await api.get('/api/tags', { key: st.get() }); } catch (e) { return toastError(e); } draw(); }
  const left = h('div', { style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 } }, bar, table.el);
  const wrap = h('div', { class: 'split', style: { flex: 1 } }, h('div', { class: 'pane fill' }, left), h('div', { class: 'pane fixed', style: { width: '340px', borderLeft: '1px solid var(--line)', borderRight: 0 } }, side));
  return { el: wrap, refresh, activate: () => table.el.focus({ preventScroll: true }) };
}
