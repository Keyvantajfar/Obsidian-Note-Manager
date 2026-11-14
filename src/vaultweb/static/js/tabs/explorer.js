// Explorer: folders, notes, header editing, body editing, move / rename / lock, AI on a selection.
import { h, mount, clear, api, bus, fmt, baseName, dirName, debounce, store } from '../core.js';
import { DataTable, Tree, folderNodes, splitPane, chips, statusBadge, sourceBadge, emptyState, button, iconButton, menu, toast, toastError, dialog, confirmDialog, diffView, kv } from '../ui.js';
import { editHeader, moveNotes, renameNote, schema, valuesText } from './shared.js';

export function createExplorer(ctx) {
  let notes = [], folders = { real: [], planned: [], root_count: 0 }, sch = [];
  let folder = null;                                   // null = every note, '' = vault root, 'A/B' = that folder and below
  const q = { text: '', only: 'all' };
  const expanded = store('explorer:open', ['']);

  // ---- folder tree
  const tree = new Tree({ label: 'Folders', expanded: expanded.get(), onSelect: (id) => { folder = id; paint(); } });
  const allBtn = h('button', { class: 'tn all', onclick: () => { folder = null; tree.cur = null; tree.render(); paint(); } }, h('span', { class: 'tl' }, 'All notes'), h('span', { class: 'tc', id: 'ex-all' }));
  const treeBox = h('div', null, allBtn, tree.el);

  // ---- table
  const table = new DataTable({
    label: 'Notes', checkbox: true, rowHeight: 36, rowKey: (n) => n.rel,
    columns: [
      { key: 'title', label: 'Note', width: '1.3fr', sort: (a, b) => a.title.localeCompare(b.title), render: (n) => h('span', { title: n.rel }, n.locked ? h('span', { class: 'faint' }, '🔒 ') : null, n.title) },
      { key: 'header', label: 'Header', width: '2fr', render: (n) => headerCell(n) },
      { key: 'folder', label: 'Folder', width: '1fr', sort: (a, b) => a.eff_folder.localeCompare(b.eff_folder), render: folderCell },
      { key: 'staged', label: 'Staged', width: '120px', render: (n) => h('span', { class: 'row', style: { gap: '4px' } }, n.staged.map((c) => h('span', { class: `badge ${c.status}`, title: `${c.kind} · ${c.source} · ${c.status}` }, c.kind === 'move' ? 'move' : 'hdr'))) },
      { key: 'mtime', label: 'Modified', width: '110px', sort: (a, b) => a.mtime - b.mtime, render: (n) => h('span', { class: 'muted' }, new Date(n.mtime * 1000).toLocaleDateString([], { dateStyle: 'medium' })) },
    ],
    onCursor: (n) => showInspector(n), onSelect: () => paintBar(), onActivate: (n) => openNote(n),
    onContext: (n, e) => rowMenu({ x: e.clientX, y: e.clientY }),
    onKey: (e, n) => { const k = e.key.toLowerCase(); if (!n) return; if (k === 'e') actHeader(); else if (k === 'm') actMove(); else if (k === 't') actAI(); else if (k === 'l') actLock(); else if (k === 'f2') actRename(); },
  });
  function headerCell(n) {
    if (!n.has_fm) return h('span', { class: 'faint' }, 'no header');
    const vals = Object.entries(n.eff_values).filter(([k]) => !/^(date|created|modified)/i.test(k)).flatMap(([, v]) => v);
    const miss = n.missing?.length;
    return h('span', { class: 'row', style: { gap: '6px', minWidth: 0 } }, chips(vals, { max: 4 }), miss ? h('span', { class: 'badge warn', title: 'Missing: ' + n.missing.join(', ') }, `${miss} missing`) : null);
  }
  function folderCell(n) {
    const mv = n.staged.find((c) => c.kind === 'move' && c.status !== 'rejected');
    return n.eff_folder !== n.folder ? h('span', { title: `${n.folder || '(root)'} → ${n.eff_folder || '(root)'}` }, h('span', { class: 'faint' }, (n.folder || '/') + ' → '), n.eff_folder || '/') : h('span', { class: 'muted' }, n.folder || '/');
  }

  // ---- toolbar
  const search = h('input', { type: 'search', placeholder: 'Filter notes…  (title, tag, folder)', 'aria-label': 'Filter notes', class: 'grow', oninput: debounce((e) => { q.text = e.target.value.toLowerCase(); paint(); }, 120) });
  const onlySel = h('select', { 'aria-label': 'Show', onchange: (e) => { q.only = e.target.value; paint(); } },
    [['all', 'All notes'], ['missing', 'Missing header fields'], ['nofm', 'No header'], ['staged', 'With staged changes'], ['unstaged', 'Nothing staged'], ['locked', 'Locked']].map(([v, l]) => h('option', { value: v }, l)));
  const bar = h('div', { class: 'toolbar' });
  let info;
  function buildBar() {
    info = h('span', { class: 'muted' });
    const aiOff = ctx.st?.offline;
    mount(bar, search, onlySel, info,
      button('Edit header', { icon: 'edit', size: 'sm', onClick: () => actHeader() }), button('Move…', { icon: 'move', size: 'sm', onClick: () => actMove() }),
      aiOff ? null : button('Tag with AI', { icon: 'sparkle', size: 'sm', kind: 'primary', onClick: () => actAI() }),
      iconButton('more', 'More actions', (e) => rowMenu(e.currentTarget)));
  }
  function rowMenu(at) {
    const ai = !ctx.st?.offline;
    menu(at, [{ label: 'Edit header…', icon: 'edit', hint: 'E', onClick: actHeader }, { label: 'Move to folder…', icon: 'move', hint: 'M', onClick: actMove }, { label: 'Rename…', hint: 'F2', onClick: actRename },
      ai ? { label: 'Tag with AI', icon: 'sparkle', hint: 'T', onClick: actAI } : null, '-',
      { label: 'Lock / unlock (AI skips it)', icon: 'lock', hint: 'L', onClick: actLock }, { label: 'Discard staged changes', icon: 'undo', onClick: actUnstage }, '-',
      { label: 'Open note…', icon: 'file', onClick: () => table.cursorRow && openNote(table.cursorRow) }].filter(Boolean));
  }
  const paths = () => table.targets().map((n) => n.rel);
  const need = () => { const p = paths(); if (!p.length) toast('Select a note first.', 'info', 1500); return p; };
  async function actHeader() { const p = need(); if (!p.length) return; const n = p.length === 1 ? notes.find((x) => x.rel === p[0]) : null; try { await editHeader({ paths: p, current: n?.eff_values || null, title: n ? `Edit header · ${n.title}` : null }); } catch (e) { toastError(e); } }
  async function actMove() { const p = need(); if (!p.length) return; try { await moveNotes({ paths: p, current: notes.find((x) => x.rel === p[0])?.eff_folder || '' }); } catch (e) { toastError(e); } }
  async function actRename() { const n = table.cursorRow; if (!n) return; try { await renameNote(n.rel, n.title); } catch (e) { toastError(e); } }
  async function actLock() { const p = need(); if (!p.length) return; const locked = !notes.find((x) => x.rel === p[0])?.locked; try { await api.post('/api/note/lock', { paths: p, locked }); toast(locked ? 'Locked — the AI will skip it.' : 'Unlocked.', 'info', 1500); } catch (e) { toastError(e); } }
  async function actUnstage() { const p = need(); if (!p.length) return; try { await api.post('/api/note/unstage', { paths: p }); toast('Staged changes discarded.', 'info', 1500); } catch (e) { toastError(e); } }
  async function actAI() {
    const p = need(); if (!p.length) return;
    try { const r = await api.post('/api/ai/run', { rels: p, header: ctx.st.header_enabled, route: ctx.st.route_enabled, fresh: true, title: `${fmt.plural(p.length, 'note')} from Explorer` }); if (!r.job) toast(r.message || 'Nothing to run.', 'info'); } catch (e) { toastError(e); }
  }

  // ---- inspector
  const inspector = h('div', { class: 'inspector' }, emptyState({ title: 'No note selected', text: 'Pick a note to see its header and text.' }));
  let shown = null, loadSeq = 0;
  const showInspector = debounce(async (n) => {
    if (!n) { mount(inspector, emptyState({ title: 'No note selected', text: 'Pick a note to see its header and text.' })); shown = null; return; }
    const seq = ++loadSeq;
    try { const d = await api.get('/api/note', { path: n.rel }); if (seq === loadSeq) { shown = d; drawInspector(d); } } catch (e) { if (seq === loadSeq) mount(inspector, emptyState({ title: 'Cannot read this note', text: e.message })); }
  }, 60);
  function drawInspector(d) {
    const sv = (n) => n.staged || [];
    mount(inspector,
      h('div', { class: 'ins-head' }, h('h2', { style: { fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-xl)' } }, d.title), h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, d.rel + ' · ' + fmt.plural(d.words, 'word'))),
      h('div', { class: 'row wrap', style: { padding: '0 14px 8px' } }, button('Edit header', { size: 'sm', icon: 'edit', onClick: actHeader }), button('Edit text', { size: 'sm', icon: 'file', onClick: () => openNote(d) }), button('Open in editor', { size: 'sm', icon: 'external', onClick: () => externalEdit(d) })),
      h('div', { class: 'ins-sec' }, h('h3', null, 'Header'), d.header.length ? kv(Object.fromEntries(d.header.map((e) => [e.key, chips(e.values, { max: 12 })]))) : h('p', { class: 'muted' }, 'This note has no header yet.'),
        d.missing?.length ? h('p', { class: 'muted', style: { marginTop: '6px' } }, 'Missing: ', d.missing.join(', ')) : null),
      d.staged.length ? h('div', { class: 'ins-sec' }, h('h3', null, 'Staged'), h('div', { class: 'stack' }, d.staged.map((c) => h('div', { class: 'row' }, statusBadge(c.status), sourceBadge(c.source), h('span', null, c.kind === 'move' ? 'Move' : 'Header'), c.confidence != null ? h('span', { class: 'muted' }, c.confidence.toFixed(2)) : null)),
        button('Review in Stage', { size: 'sm', onClick: () => ctx.goto('stage') }))) : null,
      h('div', { class: 'ins-sec' }, h('h3', null, 'Text'), h('div', { class: 'note-body' }, (d.body || '').slice(0, 4000) || h('span', { class: 'faint' }, '(empty)'), (d.body || '').length > 4000 ? h('span', { class: 'faint' }, '\n…') : null)),
      d.backlinks.length ? h('div', { class: 'ins-sec' }, h('h3', null, `Linked from ${d.backlinks.length}`), h('div', { class: 'stack' }, d.backlinks.slice(0, 12).map((b) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); table.setCursor(b); } }, baseName(b).replace(/\.md$/, ''))))) : null);
  }
  async function externalEdit(d) { try { await api.post('/api/open-external', { path: d.rel }); } catch (e) { toastError(e); } }

  // ---- body editor
  async function openNote(n) {
    let d; try { d = await api.get('/api/note', { path: n.rel }); } catch (e) { return toastError(e); }
    const ta = h('textarea', { class: 'editor', spellcheck: false, rows: 24, value: d.text, style: { width: '100%', minHeight: '56vh', fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)', lineHeight: 1.6 } });
    const dlg = dialog({ title: d.title, description: d.rel, size: 'xl', dismissible: false, body: ta, actions: [
      { label: 'Cancel', value: null, onClick: async () => (ta.value === d.text) || confirmDialog({ title: 'Discard your edits?', message: 'Your changes to this note have not been saved.', confirmLabel: 'Discard', danger: true }) },
      { label: 'Save', kind: 'primary', value: 'ok', onClick: async () => {
        try { await api.put('/api/note', { path: d.rel, text: ta.value, base_hash: d.hash }); toast('Saved. You can undo it from Stage → history.', 'ok'); }
        catch (e) { toastError(e); return false; }
      } }] });
    ta.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); dlg.buttons[1].click(); } });
  }

  // ---- data
  function counts() {
    const c = new Map(); c.set('', notes.length);
    for (const n of notes) { let p = n.eff_folder; for (;;) { c.set(p, (c.get(p) || 0) + 1); if (!p) break; p = dirName(p); } }
    return c;
  }
  function drawTree() {
    const c = counts();
    const paths = new Set([...folders.real.map((f) => f.path), ...folders.planned.map((f) => f.path)]);
    const planned = new Set(folders.planned.map((f) => f.path)), desc = Object.fromEntries(folders.planned.map((f) => [f.path, f.description]));
    const nodes = folderNodes([...paths].filter(Boolean), { countOf: (p) => c.get(p) || 0, rootCount: c.get('') });
    const mark = (ns) => ns.forEach((n) => { if (n.id && !folders.real.some((f) => f.path === n.id) && planned.has(n.id)) n.dim = true; if (desc[n.id]) n.title = desc[n.id]; mark(n.children || []); });
    mark(nodes); tree.setNodes(nodes);
    document.getElementById('ex-all') && (document.getElementById('ex-all').textContent = notes.length);
    allBtn.classList.toggle('cur', folder === null); tree.cur = folder; tree.render();
  }
  const inFolder = (n) => folder === null || (folder === '' ? true : n.eff_folder === folder || n.eff_folder.startsWith(folder + '/'));
  function paint() {
    const rows = notes.filter((n) => {
      if (folder !== null && folder !== '' && !inFolder(n)) return false;
      if (folder === '' && n.eff_folder !== '') return false;
      if (q.text && !(`${n.title} ${n.eff_folder} ${Object.values(n.eff_values).flat().join(' ')}`.toLowerCase().includes(q.text))) return false;
      switch (q.only) { case 'missing': return n.missing?.length > 0; case 'nofm': return !n.has_fm; case 'staged': return n.staged.length > 0; case 'unstaged': return !n.staged.length; case 'locked': return n.locked; default: return true; }
    });
    table.setEmpty(emptyState({ title: notes.length ? 'No notes match' : 'This vault has no notes', text: notes.length ? 'Clear the filter or pick another folder.' : 'Add Markdown files to the vault folder and rescan.' }));
    table.setRows(rows);
    allBtn.classList.toggle('cur', folder === null);
    paintBar(rows.length);
  }
  function paintBar(n = table.view.length) { if (info) info.textContent = `${fmt.plural(n, 'note')}` + (table.sel.size ? ` · ${table.sel.size} ticked` : ''); }

  async function refresh() {
    try { [notes, folders, sch] = await Promise.all([api.get('/api/notes').then((r) => r.notes), api.get('/api/folders'), schema()]); } catch (e) { return toastError(e); }
    if (!bar.firstChild) buildBar();
    drawTree(); paint();
    if (shown && table.cursorRow?.rel === shown.rel) showInspector(table.cursorRow);
  }
  const mid = h('div', { style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 } }, bar, table.el);
  const sp = splitPane(treeBox, h('div', { class: 'split', style: { flex: 1 } }), { key: 'explorer-tree', width: 220, max: 420 });
  const inner = sp.right.firstChild;
  const sp2 = splitPane(mid, inspector, { key: 'explorer-ins', width: 640, min: 360, max: 1400 });
  inner.replaceWith(sp2.el); sp2.el.style.flex = '1';
  sp2.left.style.cssText += ';flex:1 1 auto;display:flex;flex-direction:column;overflow:hidden';
  sp2.right.style.cssText += ';flex:none;width:330px;background:var(--panel);border-left:1px solid var(--line)';
  sp2.left.classList.remove('fixed'); sp2.left.classList.add('fill');
  sp2.right.classList.remove('fill'); sp2.right.classList.add('fixed');
  return { el: sp.el, refresh, activate: () => table.el.focus({ preventScroll: true }) };
}
