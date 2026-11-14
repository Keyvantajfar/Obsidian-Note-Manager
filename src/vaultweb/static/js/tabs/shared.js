// Helpers shared by several tabs: header schema, header/move/rename editors, change preview, apply + undo dialogs.
import { h, api, bus, fmt, baseName } from '../core.js';
import { dialog, promptDialog, confirmDialog, chooseDestination, diffView, statusBadge, sourceBadge, confBadge, chips, toast, toastError, button, kv } from '../ui.js';

// ---------------------------------------------------------------- schema + folders (cached, dropped on change)
let schemaP = null, foldersP = null;
bus.on('srv:settings', () => { schemaP = null; });
bus.on('srv:scan', () => { foldersP = null; });
bus.on('srv:taxonomy', () => { foldersP = null; });
bus.on('srv:vault', () => { schemaP = null; foldersP = null; });
export const schema = () => (schemaP ||= api.get('/api/settings').then((r) => r.settings.header_schema || []).catch((e) => { schemaP = null; throw e; }));
export const folderInfo = () => (foldersP ||= api.get('/api/folders').catch((e) => { foldersP = null; throw e; }));

/** All folders worth offering as a destination (planned first, then existing). */
export async function destinations() {
  const f = await folderInfo();
  const planned = f.planned.map((p) => p.path);
  const real = f.real.map((r) => r.path).filter((p) => !planned.includes(p));
  return { folders: [...planned, ...real], descriptions: Object.fromEntries(f.planned.filter((p) => p.description).map((p) => [p.path, p.description])) };
}

export const valuesText = (v) => (Array.isArray(v) ? v.join(', ') : v ?? '');
export const splitList = (s) => s.split(',').map((x) => x.trim()).filter(Boolean);

// ---------------------------------------------------------------- manual header edit
/** Edit header fields of one or many notes. `current` = {key:[values]} of the single note, if any. Returns true when staged. */
export async function editHeader({ paths, current = null, title }) {
  const sch = await schema();
  const many = paths.length > 1;
  const inputs = new Map();
  const rows = sch.map((f) => {
    const have = current ? Object.entries(current).find(([k]) => k.toLowerCase() === f.key.toLowerCase())?.[1] : null;
    const input = f.allowed?.length && !f.kind.includes('list') && f.kind !== 'csv'
      ? h('select', null, h('option', { value: '' }, many ? '(leave as is)' : '(empty)'), f.allowed.map((a) => h('option', { value: a, selected: have?.[0] === a }, a)))
      : h('input', { type: 'text', value: many ? '' : valuesText(have), placeholder: many ? '(leave as is)' : f.kind.includes('list') || f.kind === 'csv' ? 'A, B, C' : '', spellcheck: false });
    inputs.set(f.key, input);
    return h('label', { class: 'field' }, `${f.label || f.key}${f.source !== 'llm' && f.source !== 'none' ? `  · ${f.source}` : ''}`, input);
  });
  const note = h('p', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, many ? 'Only fields you fill in are changed, on every selected note.' : 'Clearing a field removes its value on apply.');
  const d = dialog({ title: title || (many ? `Edit header of ${paths.length} notes` : 'Edit header'), size: 'wide', body: h('div', { class: 'stack' }, rows, note), actions: [
    { label: 'Cancel', value: null }, { label: 'Stage change', kind: 'primary', value: 'ok' }] });
  if ((await d.result) !== 'ok') return false;
  const fields = {};
  for (const f of sch) {
    const raw = inputs.get(f.key).value.trim();
    const was = current ? valuesText(Object.entries(current).find(([k]) => k.toLowerCase() === f.key.toLowerCase())?.[1]) : null;
    if (many ? !raw : raw === (was ?? '')) continue;
    fields[f.key] = f.kind.includes('list') || f.kind === 'csv' ? splitList(raw) : raw;
  }
  if (!Object.keys(fields).length) { toast('Nothing changed.', 'info', 1500); return false; }
  const r = await api.post('/api/note/header', { paths, fields, overwrite: Object.keys(fields) });
  toast(r.staged ? `Header change staged on ${fmt.plural(r.staged, 'note')} — review it in Stage.` : 'Nothing to stage.', r.staged ? 'ok' : 'info');
  return r.staged > 0;
}

export async function moveNotes({ paths, current = '' }) {
  const { folders, descriptions } = await destinations();
  const dest = await chooseDestination({ folders, descriptions, current, title: paths.length > 1 ? `Move ${paths.length} notes to…` : 'Move to…' });
  if (dest == null) return false;
  const r = await api.post('/api/note/move', { paths, folder: dest });
  toast(r.staged ? `Move staged: ${fmt.plural(r.staged, 'note')} → ${dest || '(root)'}/` : 'Already there — nothing to stage.', r.staged ? 'ok' : 'info');
  return r.staged > 0;
}

export async function renameNote(path, title) {
  const name = await promptDialog({ title: 'Rename note', label: 'New name (without .md)', value: title, validate: (v) => (!v.trim() ? 'Enter a name.' : /[\\/:*?"<>|]/.test(v) ? 'Avoid  \\ / : * ? " < > |' : null), confirmLabel: 'Stage rename' });
  if (name == null) return false;
  const r = await api.post('/api/note/rename', { path, name });
  toast(r.staged ? 'Rename staged.' : 'Name unchanged.', r.staged ? 'ok' : 'info');
  return r.staged > 0;
}

// ---------------------------------------------------------------- change presentation
export const kindLabel = (k) => (k === 'header' ? 'Header' : 'Move');
export function changeSummary(c) {
  if (c.kind === 'move') return h('span', null, '→ ', h('b', null, (c.data.folder || '(root)') + '/'), c.data.name ? h('span', { class: 'muted' }, `  as ${c.data.name}`) : null);
  const f = c.data.fields || {};
  return h('span', { class: 'chips' }, Object.entries(f).map(([k, v]) => h('span', { class: 'chip', title: `${k}: ${valuesText(v)}` }, h('span', { class: 'muted' }, k + ' '), valuesText(v))));
}

/** Show the before/after of one staged change, with accept / reject. */
export async function previewChange(id, { onAction } = {}) {
  let p;
  try { p = await api.get('/api/stage/preview', { id }); } catch (e) { toastError(e); return; }
  const c = p.change;
  const body = h('div', { class: 'stack' },
    h('div', { class: 'row wrap' }, statusBadge(c.status), sourceBadge(c.source), confBadge(c.confidence), c.locked ? h('span', { class: 'badge warn' }, 'Locked') : null),
    kv({ Note: c.note, Change: changeSummary(c), Reason: c.reason || null }),
    diffView(p.diff), p.warnings?.length ? h('div', { class: 'stack' }, p.warnings.map((w) => h('div', { class: 'badge warn', style: { height: 'auto', padding: '4px 8px', whiteSpace: 'normal' } }, w))) : null);
  const act = (status) => async () => { try { await api.post('/api/stage/status', { status, ids: [id] }); onAction?.(status); } catch (e) { toastError(e); return false; } };
  dialog({ title: `${kindLabel(c.kind)} change · ${c.title}`, size: 'wide', body, actions: [
    { label: 'Reject', onClick: act('rejected'), value: 'rejected' }, { label: 'Close', value: null }, { label: 'Accept', kind: 'primary', onClick: act('accepted'), value: 'accepted' }] });
}

/** Edit a staged change (becomes a manual change). */
export async function editChange(c) {
  if (c.kind === 'move') {
    const { folders, descriptions } = await destinations();
    const dest = await chooseDestination({ folders, descriptions, current: c.data.folder || '', title: `Destination for “${c.title}”` });
    if (dest == null) return false;
    const r = await api.post('/api/stage/edit', { id: c.id, folder: dest, name: c.data.name || '' });
    return !!r.change;
  }
  const sch = await schema();
  const inputs = new Map();
  const fields = c.data.fields || {};
  const rows = Object.keys(fields).map((k) => {
    const f = sch.find((x) => x.key.toLowerCase() === k.toLowerCase());
    const input = h('input', { type: 'text', value: valuesText(fields[k]), spellcheck: false });
    inputs.set(k, [input, f]);
    return h('label', { class: 'field' }, f?.label || k, input);
  });
  const d = dialog({ title: `Edit proposal · ${c.title}`, size: 'wide', description: 'Your edit replaces the AI’s proposal and counts as a manual change.', body: h('div', { class: 'stack' }, rows),
    actions: [{ label: 'Cancel', value: null }, { label: 'Save', kind: 'primary', value: 'ok' }] });
  if ((await d.result) !== 'ok') return false;
  const out = {};
  for (const [k, [input, f]] of inputs) { const raw = input.value.trim(); out[k] = f && (f.kind.includes('list') || f.kind === 'csv') ? splitList(raw) : raw; }
  await api.post('/api/stage/edit', { id: c.id, fields: out });
  return true;
}

// ---------------------------------------------------------------- apply / undo
/** Dry-run, then write the accepted changes. `ids` omitted = every accepted change. */
export async function applyDialog({ ids, label } = {}) {
  let plan;
  try { plan = await api.post('/api/apply/plan', ids ? { ids } : {}); } catch (e) { toastError(e); return false; }
  const cn = plan.counts || {};
  const items = plan.items.filter((i) => !i.noop);
  const runnable = items.filter((i) => !i.blocked);
  if (!plan.items.length) { toast('No accepted changes to apply. Accept some first.', 'info'); return false; }
  const list = h('div', { class: 'stack', style: { maxHeight: '46vh', overflow: 'auto' } }, plan.items.map((it) => h('details', { class: 'plan-item' + (it.blocked ? ' blocked' : '') },
    h('summary', null, h('span', { class: 'badge ' + (it.blocked ? 'err' : it.warned ? 'warn' : it.noop ? 'plain' : 'ok') }, it.blocked ? 'Blocked' : it.noop ? 'No change' : it.warned ? 'Warning' : 'OK'),
      h('span', { class: 'muted' }, kindLabel(it.kind)), h('b', { style: { fontWeight: 600 } }, baseName(it.note)), it.dst ? h('span', { class: 'muted' }, `→ ${it.dst}`) : null),
    it.issues.length ? h('ul', { class: 'issues' }, it.issues.map((i) => h('li', { class: i.level }, i.text))) : null, it.diff?.length ? diffView(it.diff) : null)));
  const body = h('div', { class: 'stack' },
    h('p', null, `${fmt.plural(runnable.length, 'change')} will be written` + (plan.mkdirs?.length ? `, creating ${fmt.plural(plan.mkdirs.length, 'folder')}` : '') + (items.length - runnable.length ? `; ${items.length - runnable.length} blocked and skipped` : '') + '.'),
    h('p', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, 'Originals are backed up first. You can undo this whole apply afterwards.'), list);
  const d = dialog({ title: 'Apply changes', size: 'xl', body, actions: [{ label: 'Cancel', value: null }, { label: `Apply ${runnable.length}`, kind: 'primary', value: 'ok', disabled: !runnable.length && !items.length }] });
  if ((await d.result) !== 'ok') return false;
  try {
    const r = await api.post('/api/apply', { ids, label: label || 'apply' });
    const res = r.result;
    if (res.failed.length) toast(`${res.applied} applied, ${res.failed.length} failed: ${res.failed[0][1]}`, 'warn', 9000);
    bus.emit('applied', res);
    return true;
  } catch (e) { toastError(e); return false; }
}

export async function undoDialog(batchId = null) {
  let list;
  try { list = (await api.get('/api/batches')).batches; } catch (e) { toastError(e); return false; }
  const b = batchId ? list.find((x) => x.id === batchId) : list.find((x) => !x.undone);
  if (!b) { toast('Nothing to undo.', 'info'); return false; }
  const ok = await confirmDialog({ title: 'Undo this apply?', message: `${b.label || 'apply'} — ${fmt.date(b.time)}\nNotes changed since are skipped, never overwritten.`, confirmLabel: 'Undo', danger: true });
  if (!ok) return false;
  try { await api.post('/api/undo', { batch_id: b.id }); return true; } catch (e) { toastError(e); return false; }
}
