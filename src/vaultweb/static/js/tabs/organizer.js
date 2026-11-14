// Organizer: plan the folder tree, describe each folder, route notes into it, adopt AI-proposed structures.
import { h, mount, api, fmt, baseName, dirName, store } from '../core.js';
import { Tree, folderNodes, splitPane, emptyState, button, iconButton, menu, toast, toastError, dialog, promptDialog, confirmDialog, kv } from '../ui.js';

export function createOrganizer(ctx) {
  let plan = { folders: [], frozen: false, suggestions: [], unassigned: 0, attention: [], real: [] }, notes = [], sel = '';
  const tree = new Tree({ label: 'Planned folders', expanded: [''], onSelect: (id) => { sel = id; drawDetail(); } });
  const bar = h('div', { class: 'toolbar' });
  const detail = h('div', { class: 'inspector' });
  const post = async (path, body) => { try { const r = await api.post(path, body || {}); if (r.folders) plan = { ...plan, ...r }; return r; } catch (e) { toastError(e); } };
  const cur = () => plan.folders.find((f) => f.path === sel);

  function buildBar() {
    const off = ctx.st?.offline;
    mount(bar,
      h('b', { style: { fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-lg)' } }, 'Folder plan'),
      h('span', { class: 'muted' }, `${plan.folders.length} planned · ${fmt.n(plan.unassigned.length)} notes not routed`),
      h('span', { class: 'grow' }),
      button('Add folder', { icon: 'plus', size: 'sm', onClick: () => addFolder(sel) }),
      button('Import current folders', { icon: 'folder', size: 'sm', onClick: async () => { const r = await post('/api/plan/import-current'); if (r) { toast(r.added ? `Added ${fmt.plural(r.added, 'folder')} to the plan.` : 'Every existing folder is already planned.', 'info', 2000); draw(); } } }),
      off ? null : button('Propose structure…', { icon: 'sparkle', size: 'sm', onClick: proposeDialog }),
      off ? null : button('Route with AI', { icon: 'sparkle', size: 'sm', kind: 'primary', onClick: async () => { const r = await post('/api/plan/route'); if (r?.job) toast(`Routing ${fmt.plural(r.job.total, 'note')}…`, 'info', 2000); else if (r) toast(r.message || 'Nothing to route.', 'info'); } }),
      button(plan.frozen ? 'Unfreeze plan' : 'Freeze plan', { icon: plan.frozen ? 'unlock' : 'lock', size: 'sm', title: 'A frozen plan is never changed by the AI', onClick: async () => { await post('/api/plan/freeze'); draw(); } }));
  }
  async function addFolder(parent = '') {
    const p = await promptDialog({ title: 'Add a planned folder', label: 'Folder path (use / for sub-folders)', value: parent ? parent + '/' : '', placeholder: 'Research/AI', validate: (v) => (v.trim().replace(/\/+$/, '') ? null : 'Enter a path.'), confirmLabel: 'Add' });
    if (p == null) return;
    const r = await post('/api/plan/folder', { path: p.trim().replace(/^\/+|\/+$/g, '') }); if (r) { sel = p.trim().replace(/^\/+|\/+$/g, ''); draw(); }
  }
  async function renameFolder(f) {
    const n = await promptDialog({ title: `Rename “${f.path}”`, description: 'Notes already routed here follow the new name.', label: 'New path', value: f.path, validate: (v) => (v.trim() ? null : 'Enter a path.'), confirmLabel: 'Rename' });
    if (n == null || n.trim() === f.path) return;
    const r = await post('/api/plan/rename', { old: f.path, new: n.trim() }); if (r) { sel = n.trim().replace(/^\/+|\/+$/g, ''); draw(); }
  }
  async function removeFolder(f) {
    const kids = plan.folders.filter((x) => x.path.startsWith(f.path + '/')).length;
    const ok = await confirmDialog({ title: `Remove “${f.path}” from the plan?`, message: (kids ? `${kids} sub-folder(s) go with it. ` : '') + 'Notes are not touched; staged moves into it are dropped. Real folders on disk stay.', confirmLabel: 'Remove', danger: true });
    if (!ok) return;
    await post('/api/plan/remove', { path: f.path, with_children: true }); sel = dirName(f.path); draw();
  }

  function proposeDialog() {
    let folders = [], note = '';
    const prefs = h('textarea', { rows: 3, placeholder: 'e.g. “Max two levels. Keep a separate Journal folder. No more than 12 folders.”' });
    const out = h('div', { class: 'stack' });
    const fb = h('input', { type: 'text', placeholder: 'Refine: “merge Maths and Stats”, “split Research by year”…' });
    const paint = () => mount(out, note ? h('p', { class: 'muted' }, note) : null,
      folders.length ? h('div', { class: 'stack' }, folders.map((f, i) => h('div', { class: 'row', style: { borderBottom: '1px solid var(--line)', padding: '4px 0' } },
        h('div', { class: 'grow' }, h('b', null, f.path), h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, f.description || '')), iconButton('x', 'Drop this folder', () => { folders.splice(i, 1); paint(); })))) : null,
      folders.length ? h('div', { class: 'row' }, fb, button('Refine', { onClick: () => run(fb.value) })) : null);
    async function run(feedback = '') {
      mount(out, h('div', { class: 'row' }, h('span', { class: 'spin' }), 'Asking the model…'));
      try { const r = await api.post('/api/plan/propose', { preferences: prefs.value, feedback }); folders = r.folders; note = r.note || ''; fb.value = ''; paint(); }
      catch (e) { toastError(e); mount(out, h('p', { style: { color: 'var(--rose)' } }, e.message)); }
    }
    const adopt = (replace) => async () => {
      if (!folders.length) return false;
      try { await api.post('/api/plan/adopt', { folders, replace }); toast(`Plan updated with ${fmt.plural(folders.length, 'folder')}. Route notes next.`, 'ok'); } catch (e) { toastError(e); return false; }
    };
    dialog({ title: 'Propose a folder structure', size: 'wide', description: 'The model reads your vault and suggests folders. Nothing changes until you adopt it, and notes only move after you apply staged moves.',
      body: h('div', { class: 'stack' }, h('label', { class: 'field' }, 'Your preferences (optional)', prefs), button('Propose', { icon: 'sparkle', onClick: () => run() }), out),
      actions: [{ label: 'Close', value: null }, { label: 'Merge into plan', onClick: adopt(false), value: 'm' }, { label: 'Replace plan', kind: 'primary', onClick: adopt(true), value: 'r' }] });
  }

  function drawTree() {
    const paths = plan.folders.map((f) => f.path), by = Object.fromEntries(plan.folders.map((f) => [f.path, f]));
    const nodes = folderNodes(paths, { rootLabel: '(vault root)', rootCount: null, countOf: (p) => by[p]?.count ?? 0, titleOf: (p) => by[p]?.description || '' });
    const mark = (ns) => ns.forEach((n) => { if (n.id && !by[n.id]) n.dim = true; mark(n.children || []); }); mark(nodes);
    tree.setNodes(nodes); tree.cur = sel; tree.render();
  }
  function drawDetail() {
    const f = cur(), kids = [];
    const sug = plan.suggestions.length ? h('div', { class: 'ins-sec' }, h('h3', null, `AI suggests ${fmt.plural(plan.suggestions.length, 'new folder')}`),
      h('div', { class: 'stack' }, plan.suggestions.map((s) => h('div', { class: 'row', style: { alignItems: 'flex-start' } }, h('div', { class: 'grow' }, h('b', null, s.path), h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, `${fmt.plural(s.notes?.length ?? s.notes, 'note')} · ${s.reason || ''}`)),
        button('Accept', { size: 'sm', icon: 'check', onClick: async () => { const r = await post('/api/plan/suggestion', { path: s.path, action: 'accept' }); if (r) { toast(`Folder added; ${fmt.plural(r.staged, 'move')} staged.`, 'ok'); draw(); } } }),
        button('Dismiss', { size: 'sm', onClick: async () => { await post('/api/plan/suggestion', { path: s.path, action: 'dismiss' }); draw(); } }))))) : null;
    const attn = plan.attention.length ? h('div', { class: 'ins-sec' }, h('h3', null, `Needs attention (${plan.attention.length})`), h('div', { class: 'stack' }, plan.attention.slice(0, 8).map((a) => h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, h('b', { style: { color: 'var(--text)' } }, baseName(a.rel)), ' — ', a.why)))) : null;
    if (!f) {
      mount(detail, h('div', { class: 'ins-head' }, h('h2', { style: { fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-xl)' } }, sel === '' ? 'Vault root' : sel || 'Plan'),
        h('p', { class: 'muted' }, plan.folders.length ? 'Pick a planned folder on the left to describe it and see what is routed there.' : 'No folders planned yet. Import your current folders, add some by hand, or ask the AI to propose a structure.')), sug, attn);
      return;
    }
    const desc = h('textarea', { rows: 4, value: f.description || '', placeholder: 'What belongs here? The AI reads this when routing notes.', onblur: async () => { if (desc.value !== (f.description || '')) { const r = await post('/api/plan/describe', { path: f.path, description: desc.value }); if (r) toast('Description saved.', 'ok', 1200); } } });
    const inside = notes.filter((n) => n.eff_folder === f.path);
    mount(detail,
      h('div', { class: 'ins-head' }, h('h2', { style: { fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-xl)' } }, baseName(f.path)), h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, f.path)),
      h('div', { class: 'row wrap', style: { padding: '0 14px 10px' } }, button('Sub-folder', { size: 'sm', icon: 'plus', onClick: () => addFolder(f.path) }), button('Rename', { size: 'sm', icon: 'edit', onClick: () => renameFolder(f) }), button('Remove', { size: 'sm', icon: 'trash', onClick: () => removeFolder(f) })),
      h('div', { class: 'ins-sec' }, h('h3', null, 'Description for the AI'), desc,
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.llm, onchange: async (e) => { await post('/api/plan/describe', { path: f.path, llm: e.target.checked }); } }), 'The AI may route notes into this folder')),
      h('div', { class: 'ins-sec' }, h('h3', null, `${fmt.plural(inside.length, 'note')} here`), inside.length ? h('div', { class: 'stack' }, inside.slice(0, 15).map((n) => h('div', null, n.title, n.eff_folder !== n.folder ? h('span', { class: 'badge warn', style: { marginLeft: '8px' } }, 'move staged') : null)),
        inside.length > 15 ? h('span', { class: 'faint' }, `…and ${inside.length - 15} more`) : null) : h('p', { class: 'muted' }, 'Nothing routed here yet.'),
        ctx.st?.offline ? null : button('Re-route these with AI', { size: 'sm', onClick: async () => { if (!inside.length) return; const r = await post('/api/plan/route', { rels: inside.map((n) => n.rel) }); if (r?.job) toast('Re-routing…', 'info', 1500); } })),
      sug, attn);
  }
  function draw() { buildBar(); drawTree(); drawDetail(); }
  async function refresh() {
    try { [plan, notes] = await Promise.all([api.get('/api/plan'), api.get('/api/notes').then((r) => r.notes)]); } catch (e) { return toastError(e); }
    if (sel && sel !== '' && !cur()) sel = '';
    draw();
  }
  const sp = splitPane(h('div', null, tree.el), detail, { key: 'org-tree', width: 300, max: 520 });
  const wrap = h('div', { style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 } }, bar, sp.el);
  return { el: wrap, refresh };
}
