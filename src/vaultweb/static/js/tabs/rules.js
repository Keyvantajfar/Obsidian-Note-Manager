// Rules (condition -> action, deterministic) and standing instructions (free text the AI follows).
import { h, mount, api, fmt, icon } from '../core.js';
import { emptyState, button, iconButton, toast, toastError, dialog, promptDialog, confirmDialog } from '../ui.js';

export function createRules(ctx) {
  let data = { rules: [], instructions: [], locks: [], syntax: '' };
  const post = async (p, b) => { try { const r = await api.post(p, b || {}); if (r.rules) data = { ...data, ...r }; return r; } catch (e) { toastError(e); } };

  async function matches(r) {
    try { const m = await api.get(`/api/rules/${r.id}/matches`); dialog({ title: `Matches · ${r.name || r.text}`, body: m.count ? h('div', { class: 'stack' }, m.notes.map((n) => h('div', null, n)), m.count > m.notes.length ? h('span', { class: 'faint' }, `…and ${m.count - m.notes.length} more`) : null) : h('p', { class: 'muted' }, 'No note matches this rule right now.') }); } catch (e) { toastError(e); }
  }
  async function editRule(r) {
    const t = await promptDialog({ title: 'Edit rule', label: 'Rule', value: r.text, multiline: true, confirmLabel: 'Save' });
    if (t == null) return;
    try { const x = await api.put(`/api/rules/${r.id}`, { text: t }); data = { ...data, ...x }; draw(); } catch (e) { toastError(e); }
  }
  async function addRule(text) {
    const r = await post('/api/rules', { text }); if (r) { toast(`Rule added — it matches ${fmt.plural(r.matches, 'note')}.`, 'ok', 2200); draw(); return true; }
  }
  const ruleInput = h('input', { type: 'text', class: 'grow', placeholder: 'tag:ML -> move:Research/AI      folder:Inbox & words>400 -> tag:Long', spellcheck: false });
  const nlInput = h('input', { type: 'text', class: 'grow', placeholder: 'Or describe it: “everything about calculus goes under University/Maths”', spellcheck: false });
  async function translate() {
    const text = nlInput.value.trim(); if (!text) return;
    try {
      const r = await api.post('/api/rules-translate', { text });
      const d = dialog({ title: 'Proposed rules', size: 'wide', description: r.explanation, body: r.rules.length ? h('div', { class: 'stack' }, r.rules.map((x) => h('code', { style: { display: 'block', padding: '6px 8px', background: 'var(--bg)', borderRadius: 'var(--r-sm)' } }, typeof x === 'string' ? x : x.text))) : h('p', { class: 'muted' }, 'The model did not suggest any rule.'),
        actions: [{ label: 'Cancel', value: null }, { label: 'Add as proposed', kind: 'primary', value: 'ok', disabled: !r.rules.length }] });
      if ((await d.result) === 'ok') { for (const x of r.rules) await post('/api/rules', { text: typeof x === 'string' ? x : x.text, proposed: true, enabled: false }); nlInput.value = ''; toast('Added as proposed — approve them below.', 'ok'); draw(); }
    } catch (e) { toastError(e); }
  }
  async function addInstruction() {
    const text = h('textarea', { rows: 3, placeholder: 'e.g. “Never tag anything as #Idea unless the note says so explicitly.”' });
    const scope = h('select', null, [['global', 'All notes'], ['folder', 'Notes in a folder'], ['tag', 'Notes with a tag']].map(([v, l]) => h('option', { value: v }, l)));
    const value = h('input', { type: 'text', placeholder: 'folder path or tag', disabled: true });
    scope.onchange = () => { value.disabled = scope.value === 'global'; };
    const task = h('select', null, [['both', 'Tagging and routing'], ['header', 'Tagging only'], ['route', 'Routing only']].map(([v, l]) => h('option', { value: v }, l)));
    const d = dialog({ title: 'New instruction', description: 'Standing advice sent to the model with every matching note.', size: 'wide', body: h('div', { class: 'stack' }, h('label', { class: 'field' }, 'Instruction', text), h('div', { class: 'row' }, h('label', { class: 'field grow' }, 'Applies to', scope), h('label', { class: 'field grow' }, 'Value', value), h('label', { class: 'field grow' }, 'For', task))),
      actions: [{ label: 'Cancel', value: null }, { label: 'Add', kind: 'primary', value: 'ok', onClick: () => (text.value.trim() ? true : false) }] });
    if ((await d.result) !== 'ok') return;
    const r = await post('/api/instructions', { text: text.value, scope: scope.value, value: value.value.trim(), task: task.value }); if (r) { if (r.job) toast('Re-running affected notes…', 'info'); draw(); }
  }
  const root = h('div', { class: 'rules-view' });
  function draw() {
    const rule = (r, i) => h('div', { class: 'rule' + (r.enabled ? '' : ' off') + (r.error ? ' bad' : ''), },
      h('div', { class: 'grow', style: { minWidth: 0 } }, h('div', { class: 'row', style: { gap: '8px' } }, r.proposed ? h('span', { class: 'badge warn' }, 'Proposed') : null, r.name ? h('b', null, r.name) : null),
        h('code', null, r.text), r.error ? h('div', { style: { color: 'var(--rose)', fontSize: 'var(--fs-sm)' } }, r.error) : r.target ? h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, `moves to ${r.target}/`) : null),
      h('div', { class: 'row', style: { gap: '2px', flex: 'none' } },
        r.proposed ? button('Approve', { size: 'sm', kind: 'primary', onClick: async () => { await post(`/api/rules/${r.id}/approve`); draw(); } }) : h('label', { class: 'check', title: 'Enabled' }, h('input', { type: 'checkbox', checked: r.enabled, onchange: async () => { await post(`/api/rules/${r.id}/toggle`); draw(); } })),
        iconButton('eye', 'Which notes match', () => matches(r)), iconButton('edit', 'Edit', () => editRule(r)),
        iconButton('up', 'Move up', async () => { await post(`/api/rules/${r.id}/move`, { delta: -1 }); draw(); }), iconButton('trash', 'Delete', async () => { await post(`/api/rules/${r.id}/delete`); draw(); })));
    const ins = (i) => h('div', { class: 'rule' + (i.enabled ? '' : ' off') }, h('div', { class: 'grow' }, h('div', { class: 'row', style: { gap: '6px' } }, h('span', { class: 'badge plain' }, i.scope_label), i.task !== 'both' ? h('span', { class: 'badge plain' }, i.task) : null), h('div', null, i.text)),
      h('div', { class: 'row', style: { gap: '2px', flex: 'none' } }, h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: i.enabled, onchange: async () => { await post(`/api/instructions/${i.id}/toggle`); draw(); } })),
        ctx.st?.offline ? null : iconButton('refresh', 'Re-run the AI on affected notes', async () => { const r = await post(`/api/instructions/${i.id}/rerun`); if (r?.job) toast('Re-running…', 'info', 1500); }), iconButton('trash', 'Delete', async () => { await post(`/api/instructions/${i.id}/delete`); draw(); })));
    mount(root,
      h('section', null, h('h2', null, 'Rules'), h('p', { class: 'muted' }, 'Exact, repeatable. A rule matches notes and stages a header or move change. They run on every scan and are never “guessed” by the AI.'),
        h('form', { class: 'row', onsubmit: async (e) => { e.preventDefault(); const t = ruleInput.value.trim(); if (t && (await addRule(t))) ruleInput.value = ''; } }, ruleInput, h('button', { class: 'btn primary', type: 'submit' }, 'Add rule')),
        ctx.st?.offline ? null : h('form', { class: 'row', style: { marginTop: '8px' }, onsubmit: (e) => { e.preventDefault(); translate(); } }, nlInput, h('button', { class: 'btn', type: 'submit' }, icon('sparkle'), 'Translate')),
        h('div', { class: 'rule-list' }, data.rules.length ? data.rules.map(rule) : h('p', { class: 'faint' }, 'No rules yet.')),
        data.syntax ? h('details', { style: { marginTop: '8px' } }, h('summary', { class: 'muted' }, 'Rule syntax'), h('pre', { class: 'diff', style: { padding: '8px 12px' } }, data.syntax)) : null),
      h('section', null, h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Instructions for the AI'), button('New instruction', { icon: 'plus', size: 'sm', onClick: addInstruction })),
        h('p', { class: 'muted' }, 'Free-text advice the model reads. Use these for judgement calls that a rule cannot express.'),
        h('div', { class: 'rule-list' }, data.instructions.length ? data.instructions.map(ins) : h('p', { class: 'faint' }, 'None yet.'))),
      data.locks.length ? h('section', null, h('h2', null, `Locked notes (${data.locks.length})`), h('p', { class: 'muted' }, 'The AI never proposes anything for these. Unlock them in the Explorer.'), h('div', { class: 'chips' }, data.locks.slice(0, 40).map((l) => h('span', { class: 'chip', title: l }, l)))) : null);
  }
  async function refresh() { try { data = await api.get('/api/rules'); } catch (e) { return toastError(e); } draw(); }
  return { el: h('div', { style: { flex: 1, overflow: 'auto' } }, root), refresh };
}
