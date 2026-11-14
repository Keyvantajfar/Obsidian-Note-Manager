// Settings: model connection (profiles), automation, safety, scanning, header fields, prompts.
import { h, mount, api, bus, fmt, store, debounce } from '../core.js';
import { button, iconButton, toast, toastError, dialog, confirmDialog, promptDialog, emptyState, kv } from '../ui.js';

const SECTIONS = [['model', 'Model'], ['auto', 'Automation'], ['safety', 'Safety'], ['scan', 'Scanning'], ['header', 'Header fields'], ['prompts', 'Prompts']];
const AUTO = [
  ['auto_level', 'Automation level', 'select', null, 'Manual: you start runs. Assist: new notes are tagged in the background. Auto: confident proposals are accepted too.'],
  ['auto_accept_threshold', 'Accept at confidence ≥', 'number', { min: 0, max: 1, step: 0.05 }, 'Used by Auto mode and “Accept all ≥”.'],
  ['rules_auto_accept', 'Accept rule results automatically', 'bool', null, 'Rules are exact, so their changes can skip review.'],
  ['auto_apply', 'Apply accepted changes automatically', 'bool', null, 'Only in Auto mode. Always undoable.'],
  ['auto_apply_cap', 'Auto-apply at most', 'number', { min: 1, step: 1 }, 'More than this waits for you in Stage.'],
  ['watch_interval', 'Check the disk every (seconds)', 'number', { min: 2, step: 1 }, 'How quickly edits made in other apps are noticed.'],
  ['workers', 'Parallel requests', 'number', { min: 1, max: 128, step: 1 }, 'Higher is faster if your server can take it.'],
  ['batch_size', 'Notes per request', 'number', { min: 1, max: 20, step: 1 }, '1 is the most reliable with small models.'],
  ['max_note_chars', 'Characters of each note sent', 'number', { min: 500, step: 500 }, ''],
  ['context_siblings', 'Neighbour notes as context', 'number', { min: 0, step: 1 }, ''],
  ['context_links', 'Linked notes as context', 'number', { min: 0, step: 1 }, ''],
  ['vocab_size', 'Known values offered to the model', 'number', { min: 0, step: 5 }, ''],
];
const SAFETY = [
  ['backup', 'Back up every file before changing it', 'bool', null, 'Backups live in .vaultmgr/ and power Undo.'],
  ['preserve_mtime', 'Keep each note’s modified time', 'bool', null, 'So sorting by date is not disturbed by header edits.'],
  ['prune_empty_dirs', 'Delete folders left empty after moves', 'bool', null, ''],
  ['collision_policy', 'If a name is already taken', 'select', null, 'Suffix: add “ (2)”. Skip: leave the note where it is. Nothing is ever overwritten.'],
];
const SCAN = [
  ['ignore_globs', 'Hide completely', 'list', null, 'One pattern per line, e.g. Templates/**  or  *.excalidraw.md. Dot-folders are always hidden.'],
  ['no_ai_globs', 'Visible, but never sent to the AI', 'list', null, 'e.g. Journal/**  — you can still edit them by hand.'],
  ['date_source', 'Where file dates come from', 'select', null, ''],
];
const LABELS = { auto_level: { manual: 'Manual', assist: 'Assist', auto: 'Auto' }, collision_policy: { suffix: 'Add a suffix', skip: 'Skip the note' },
  date_source: { earliest: 'Earliest of created / modified', mtime: 'Last modified', birth_or_mtime: 'Created (else modified)', name_or_earliest: 'File name date, else earliest' } };

export function createSettings(ctx) {
  const tab = store('settings:tab', 'model');
  let S = null, P = null, selProfile = null, schemaDraft = null, promptDraft = null;
  const root = h('div', { class: 'settings-view' });

  const save = debounce(async (patch) => { try { S = await api.put('/api/settings', patch); toast('Saved.', 'ok', 900); } catch (e) { toastError(e); refresh(); } }, 350);
  function control([key, label, type, attrs, help]) {
    const val = S.settings[key];
    let input;
    if (type === 'bool') input = h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!val, onchange: (e) => save({ [key]: e.target.checked }) }), label);
    else if (type === 'select') {
      const opts = { auto_level: S.options.auto_levels, collision_policy: S.options.collision_policies, date_source: S.options.date_sources }[key];
      input = h('label', { class: 'field' }, label, h('select', { onchange: (e) => save({ [key]: e.target.value }) }, opts.map((o) => h('option', { value: o, selected: o === val }, LABELS[key]?.[o] || o))));
    } else if (type === 'list') input = h('label', { class: 'field wide' }, label, h('textarea', { rows: 3, spellcheck: false, value: (val || []).join('\n'), onchange: (e) => save({ [key]: e.target.value }) }));
    else input = h('label', { class: 'field' }, label, h('input', { type: 'number', ...attrs, value: val, onchange: (e) => save({ [key]: e.target.value }) }));
    return h('div', { class: type === 'list' ? 'wide' : '' }, input, help ? h('div', { class: 'help' }, help) : null);
  }
  const grid = (defs) => h('div', { class: 'form-grid' }, defs.map(control));

  // ---------- model profiles
  const field = (label, el, help) => h('label', { class: 'field' }, label, el, help ? h('span', { class: 'help' }, help) : null);
  function modelView() {
    const prof = P.profiles.find((p) => p.name === selProfile) || P.profiles.find((p) => p.name === P.active) || P.profiles[0];
    selProfile = prof.name;
    const f = {};
    const inp = (k, o = {}) => (f[k] = h('input', { type: o.type || 'text', value: prof[k] ?? '', spellcheck: false, placeholder: o.ph || '', step: o.step, list: o.list }));
    const sel = (k, opts) => (f[k] = h('select', null, opts.map((o) => h('option', { value: o, selected: prof[k] === o }, o))));
    const json = (k) => (f[k] = h('textarea', { rows: 2, spellcheck: false, value: JSON.stringify(prof[k] || {}, null, 0) }));
    const models = h('datalist', { id: 'models-dl' });
    const preset = h('select', { onchange: (e) => { const u = P.presets[e.target.value]?.base_url; if (u !== undefined && e.target.value) f.base_url.value = u; } }, h('option', { value: '' }, 'Choose a preset…'), Object.keys(P.presets).map((k) => h('option', { value: k }, k)));
    const out = h('div', { class: 'stack' });
    const gather = () => {
      const o = { name: f.name.value.trim() };
      for (const k of ['base_url', 'api_key_env', 'model']) o[k] = f[k].value.trim();
      for (const k of ['temperature', 'max_tokens', 'timeout', 'max_retries']) o[k] = Number(f[k].value);
      for (const k of ['json_mode', 'reasoning']) o[k] = f[k].value;
      for (const k of ['extra_body', 'extra_headers']) { try { o[k] = JSON.parse(f[k].value || '{}'); } catch { throw new Error(`${k} is not valid JSON`); } }
      if (f.api_key.value) o.api_key = f.api_key.value;
      return o;
    };
    const run = async (fn) => { try { return await fn(gather()); } catch (e) { toastError(e); } };
    const test = () => run(async (p) => {
      mount(out, h('div', { class: 'row' }, h('span', { class: 'spin' }), 'Testing…'));
      const r = await api.post('/api/profiles/test', { profile: p, original_name: prof.name });
      mount(out, r.lines.map((l) => h('div', { class: 'badge ' + (l.level === 'ok' ? 'ok' : l.level === 'error' ? 'err' : l.level === 'warn' ? 'warn' : 'plain'), style: { height: 'auto', padding: '5px 9px', whiteSpace: 'normal', justifySelf: 'start' } }, l.text)));
    });
    const list = () => run(async (p) => { const r = await api.post('/api/profiles/models', { profile: p, original_name: prof.name }); mount(models, r.models.map((m) => h('option', { value: m }))); toast(`${fmt.plural(r.models.length, 'model')} found — click the Model box to choose.`, 'ok'); });
    const saveP = (activate) => run(async (p) => { P = await api.put('/api/profiles', { profile: p, original_name: prof.name, activate }); selProfile = p.name; toast(activate ? `Now using ${p.name}.` : 'Profile saved.', 'ok'); draw(); });
    const delP = async () => { if (P.profiles.length < 2) return toast('Keep at least one profile.', 'warn'); if (await confirmDialog({ title: `Delete “${prof.name}”?`, message: 'The saved key goes with it.', confirmLabel: 'Delete', danger: true })) { P = await api.post('/api/profiles/delete', { name: prof.name }); selProfile = P.active; draw(); } };
    const newP = async () => { const n = await promptDialog({ title: 'New profile', label: 'Name', validate: (v) => (!v.trim() ? 'Enter a name.' : P.profiles.some((p) => p.name === v.trim()) ? 'That name exists.' : null) }); if (n == null) return; try { P = await api.put('/api/profiles', { profile: { name: n.trim(), base_url: 'http://localhost:8080/v1' } }); selProfile = n.trim(); draw(); } catch (e) { toastError(e); } };
    const imp = async () => { const p = await promptDialog({ title: 'Import an old config', label: 'Path to the old settings file', description: 'From the earlier command-line tool.' }); if (p) try { P = await api.post('/api/profiles/import', { path: p }); selProfile = P.active; draw(); toast('Imported.', 'ok'); } catch (e) { toastError(e); } };
    return h('div', { class: 'panel' },
      h('div', { class: 'row wrap' }, h('select', { 'aria-label': 'Profile', onchange: (e) => { selProfile = e.target.value; draw(); } }, P.profiles.map((p) => h('option', { value: p.name, selected: p.name === prof.name }, p.name + (p.name === P.active ? '  (in use)' : '')))),
        button('New', { size: 'sm', icon: 'plus', onClick: newP }), button('Import old config', { size: 'sm', onClick: imp }), h('span', { class: 'grow' }), button('Delete', { size: 'sm', icon: 'trash', onClick: delP })),
      h('div', { class: 'form-grid' },
        field('Profile name', inp('name')), field('Server preset', preset),
        h('div', { class: 'wide' }, field('Base URL', inp('base_url', { ph: 'http://localhost:8080/v1' }), 'Any OpenAI-compatible server: llama.cpp, Ollama, LM Studio, vLLM, OpenAI, OpenRouter…')),
        field('API key', inp('api_key', { type: 'password', ph: prof.api_key_set ? '•••• stored — leave blank to keep' : 'optional for local servers' }), prof.key_hint), field('…or the name of an environment variable', inp('api_key_env', { ph: 'OPENAI_API_KEY' }), 'Preferred: the key is never written to disk.'),
        field('Model', Object.assign(inp('model', { list: 'models-dl', ph: 'leave empty if the server has one model' }), {}), null), h('div', { style: { alignSelf: 'end' } }, button('List models from server', { size: 'sm', onClick: list }), models),
        field('Temperature', inp('temperature', { type: 'number', step: 0.1 })), field('Max reply tokens', inp('max_tokens', { type: 'number' })),
        field('Timeout (seconds)', inp('timeout', { type: 'number' })), field('Retries', inp('max_retries', { type: 'number' })),
        field('JSON mode', sel('json_mode', ['auto', 'on', 'off']), 'Asks the server for strict JSON. Turn off if replies come back empty.'), field('Thinking', sel('reasoning', ['auto', 'off', 'on']), '“off” asks reasoning models to skip hidden thinking.'),
        h('div', { class: 'wide' }, field('Extra request fields (JSON)', json('extra_body'), 'e.g. {"chat_template_kwargs": {"enable_thinking": false}}')), h('div', { class: 'wide' }, field('Extra headers (JSON)', json('extra_headers')))),
      h('div', { class: 'row wrap' }, button('Test connection', { icon: 'play', onClick: test }), h('span', { class: 'grow' }), button('Save', { onClick: () => saveP(false) }), button('Save & use', { kind: 'primary', icon: 'check', onClick: () => saveP(true) })), out);
  }

  // ---------- header fields
  function headerView() {
    schemaDraft ||= JSON.parse(JSON.stringify(S.settings.header_schema));
    const dirty = JSON.stringify(schemaDraft) !== JSON.stringify(S.settings.header_schema);
    const redraw = () => { draw(); };
    const move = (i, d) => { const j = i + d; if (j < 0 || j >= schemaDraft.length) return; [schemaDraft[i], schemaDraft[j]] = [schemaDraft[j], schemaDraft[i]]; redraw(); };
    const rows = schemaDraft.map((f, i) => h('div', { class: 'field-row' }, h('span', { class: 'faint' }, i + 1),
      h('div', { style: { minWidth: 0 } }, h('b', null, f.label || f.key), h('span', { class: 'muted' }, `  ${f.key} · ${f.kind} · ${f.source}`), f.allowed?.length ? h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, 'one of: ' + f.allowed.join(', ')) : null),
      h('div', { class: 'row', style: { gap: '2px' } }, iconButton('up', 'Move up', () => move(i, -1)), iconButton('edit', 'Edit', () => editField(i)), iconButton('trash', 'Remove', () => { schemaDraft.splice(i, 1); redraw(); }))));
    return h('div', { class: 'panel' }, h('p', { class: 'muted' }, 'The header is the block at the top of each note. These fields are what the Tagger fills in and what the Explorer shows. Changing them rescans the vault.'), h('div', { class: 'stack' }, rows),
      h('div', { class: 'row wrap' }, button('Add field', { icon: 'plus', size: 'sm', onClick: () => { schemaDraft.push({ key: '', kind: 'text', label: '', source: 'llm', instruction: '', required: true, quote: false, default: '', max_items: 10, min_items: 1, allowed: [], date_format: '%Y-%m-%dT%H:%M' }); editField(schemaDraft.length - 1, true); } }),
        button('Reset to defaults', { size: 'sm', onClick: async () => { if (await confirmDialog({ title: 'Reset the header fields?', message: 'Parent, status, tags and date return.', confirmLabel: 'Reset' })) { schemaDraft = JSON.parse(JSON.stringify(S.default_schema)); redraw(); } } }),
        h('span', { class: 'grow' }), dirty ? button('Discard', { size: 'sm', onClick: () => { schemaDraft = null; draw(); } }) : null,
        button('Save fields', { kind: 'primary', icon: 'check', onClick: async () => { try { S = await api.put('/api/settings/schema', { fields: schemaDraft }); schemaDraft = null; toast('Header fields saved; vault rescanned.', 'ok'); draw(); } catch (e) { toastError(e); } } })));
  }
  async function editField(i, isNew = false) {
    const f = schemaDraft[i], o = S.options;
    const t = (v, ph) => h('input', { type: 'text', value: v ?? '', placeholder: ph || '', spellcheck: false });
    const key = t(f.key, 'parent'), label = t(f.label, 'Parent MOC'), def = t(f.default), fmt_ = t(f.date_format), allowed = t((f.allowed || []).join(', '), 'Idea, Draft, Done');
    const kind = h('select', null, o.field_kinds.map((k) => h('option', { value: k, selected: k === f.kind }, k)));
    const source = h('select', null, o.field_sources.map((k) => h('option', { value: k, selected: k === f.source }, k)));
    const instr = h('textarea', { rows: 4, value: f.instruction || '', placeholder: 'What should the model put here?' });
    const quote = h('input', { type: 'checkbox', checked: f.quote }), req = h('input', { type: 'checkbox', checked: f.required });
    const mx = h('input', { type: 'number', value: f.max_items, min: 1 });
    const d = dialog({ title: isNew ? 'New header field' : `Field · ${f.key}`, size: 'wide', dismissible: false, body: h('div', { class: 'form-grid' },
      field('Key (as written in the note)', key), field('Label', label), field('Value format', kind, 'wikilink → [[Value]], hashtag → #Value, list → [a, b], csv → a, b, date → formatted time'), field('Filled by', source, 'llm: the model. created / modified: file time. static: the default. none: by hand only.'),
      h('div', { class: 'wide' }, field('Instruction for the model', instr)), field('Allowed values (blank = any)', allowed), field('Default (for “static”)', def), field('Date format', fmt_), field('Max values', mx),
      h('label', { class: 'check' }, quote, 'Wrap the value in quotes'), h('label', { class: 'check' }, req, 'Required (counts as “missing” if empty)')),
      actions: [{ label: 'Cancel', value: null, onClick: () => { if (isNew && !f.key) schemaDraft.splice(i, 1); } }, { label: 'Done', kind: 'primary', value: 'ok' }] });
    if ((await d.result) === 'ok') Object.assign(f, { key: key.value.trim(), label: label.value.trim(), kind: kind.value, source: source.value, instruction: instr.value, allowed: allowed.value.split(',').map((x) => x.trim()).filter(Boolean), default: def.value, date_format: fmt_.value, max_items: +mx.value || 10, quote: quote.checked, required: req.checked });
    else if (isNew && !f.key) schemaDraft.splice(i, 1);
    draw();
  }

  // ---------- prompts
  function promptsView() {
    promptDraft ||= { ...S.default_prompts, ...(S.settings.prompts || {}) };
    const names = { analyze_base: ['Tagging & routing prompt', 'Sent with every note. Placeholders are filled in automatically.'], structure_base: ['Folder-structure prompt', 'Used by “Propose structure”.'], rules_base: ['Rule-writing prompt', 'Used when translating plain English into rules.'] };
    return h('div', { class: 'panel' }, h('p', { class: 'muted' }, 'The instructions the model receives. Edit with care: the reply format is part of the prompt. Reset brings the built-in text back.'),
      Object.keys(S.default_prompts).map((k) => {
        const ta = h('textarea', { rows: 12, spellcheck: false, value: promptDraft[k], style: { fontFamily: 'var(--font-mono)', fontSize: 'var(--fs-sm)' }, oninput: () => { promptDraft[k] = ta.value; } });
        const custom = (S.settings.prompts || {})[k];
        return h('div', { class: 'stack' }, h('div', { class: 'row' }, h('b', { class: 'grow' }, names[k]?.[0] || k, custom ? h('span', { class: 'badge warn', style: { marginLeft: '8px' } }, 'customised') : null), button('Reset', { size: 'sm', onClick: () => { promptDraft[k] = S.default_prompts[k]; draw(); } })), h('div', { class: 'help' }, names[k]?.[1] || ''), ta);
      }), h('div', { class: 'row' }, h('span', { class: 'grow' }), button('Save prompts', { kind: 'primary', icon: 'check', onClick: async () => { try { S = await api.put('/api/settings', { prompts: promptDraft }); promptDraft = null; toast('Prompts saved.', 'ok'); draw(); } catch (e) { toastError(e); } } })));
  }

  function draw() {
    if (!S || !P) return;
    const body = { model: modelView, auto: () => h('div', { class: 'panel' }, grid(AUTO)), safety: () => h('div', { class: 'panel' }, grid(SAFETY)), scan: () => h('div', { class: 'panel' }, grid(SCAN)), header: headerView, prompts: promptsView }[tab.get()]();
    const scrollTop = root.parentElement?.scrollTop || 0;
    mount(root, h('div', { class: 'row wrap' }, h('h2', { class: 'grow' }, 'Settings'), h('div', { class: 'seg' }, SECTIONS.map(([k, l]) => h('button', { 'aria-pressed': String(tab.get() === k), onclick: () => { tab.set(k); draw(); } }, l)))), body);
    if (root.parentElement) root.parentElement.scrollTop = scrollTop;
  }
  async function refresh() {
    try { [S, P] = await Promise.all([api.get('/api/settings'), api.get('/api/profiles')]); } catch (e) { return toastError(e); }
    if (!P.profiles.some((p) => p.name === selProfile)) selProfile = P.active;
    draw();
  }
  return { el: h('div', { style: { flex: 1, overflow: 'auto' } }, root), refresh };
}
