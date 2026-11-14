// Home: choose a vault, then a workspace.
import { h, mount, icon, api, fmt, baseName } from './core.js';
import { toastError, button, chooseFolder, emptyState } from './ui.js';

const SVG = (inner) => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', '0 0 70 48'); s.setAttribute('aria-hidden', 'true'); s.innerHTML = inner; return s; };
const GLYPH = {
  all: () => SVG('<rect x="14" y="4" width="42" height="12" rx="3" opacity=".45"/><rect x="10" y="17" width="50" height="12" rx="3" opacity=".7"/><rect x="6" y="30" width="58" height="14" rx="3"/><path d="M14 37h14M40 37h18"/>'),
  header: () => SVG('<rect x="12" y="3" width="46" height="42" rx="4"/><path d="M12 15h46"/><path d="M19 9h10M33 9h20" /><path d="M19 23h32M19 30h26M19 37h30" opacity=".5"/>'),
  organizer: () => SVG('<rect x="3" y="18" width="14" height="12" rx="3"/><path d="M17 24h8M25 10v28M25 10h8M25 24h8M25 38h8"/><rect x="33" y="5" width="30" height="10" rx="3"/><rect x="33" y="19" width="30" height="10" rx="3"/><rect x="33" y="33" width="30" height="10" rx="3"/>'),
  dates: () => SVG('<rect x="10" y="6" width="50" height="38" rx="4"/><path d="M10 17h50M23 3v6M47 3v6"/><path d="M20 26h.01M30 26h.01M40 26h.01M50 26h.01M20 35h.01M30 35h.01M40 35h.01" stroke-width="3.2"/>'),
};

export function renderHome(root, { app, st, onEnter, onStatus }) {
  let cur = st;
  const wrap = h('div', { class: 'home' });
  mount(root, wrap);

  async function open(call, label) {
    wrap.setAttribute('aria-busy', 'true');
    mount(vaultCol, h('div', { class: 'row muted' }, h('span', { class: 'spin' }), label));
    try { cur = await call(); onStatus(cur); app.recent = (await api.get('/api/app')).recent; }
    catch (e) { toastError(e); }
    wrap.removeAttribute('aria-busy'); paint();
  }
  const openPath = (p) => open(() => api.post('/api/vault/open', { path: p }), 'Reading your notes…');
  async function pickFolder() {
    const start = cur.open ? cur.vault.path.replace(/[\\/][^\\/]*$/, '') : app.recent[0]?.replace(/[\\/][^\\/]*$/, '') || '';
    const p = await chooseFolder({ start }); if (p) openPath(p);
  }

  const vaultCol = h('section', { class: 'home-sec', 'aria-label': 'Vault' });
  const wsCol = h('section', { class: 'home-sec', 'aria-label': 'Workspace' });

  function paintVault() {
    const items = [];
    if (cur.open) items.push(h('div', { class: 'vault-item', 'aria-current': 'true', style: { cursor: 'default' } }, icon('folder'),
      h('div', { class: 't grow' }, h('b', null, cur.vault.name), h('span', null, cur.vault.path)),
      h('span', { class: 'badge ok plain' }, `${fmt.n(cur.stats.notes)} notes`)));
    for (const p of app.recent.filter((r) => !cur.open || r !== cur.vault.path).slice(0, 6))
      items.push(h('button', { class: 'vault-item', onclick: () => openPath(p), title: p }, icon('folder'), h('div', { class: 't' }, h('b', null, baseName(p.replace(/\\/g, '/'))), h('span', null, p))));
    mount(vaultCol, h('h2', null, 'Vault'),
      items.length ? h('div', { class: 'vault-list' }, items) : h('p', { class: 'muted', style: { marginBottom: '10px' } }, 'Choose the folder that holds your Obsidian notes. Nothing is changed until you apply changes yourself.'),
      h('div', { class: 'row wrap', style: { marginTop: '14px' } },
        button(cur.open ? 'Open another folder…' : 'Open a folder…', { icon: 'folder', kind: 'primary', onClick: pickFolder }),
        button('Try the demo vault', { icon: 'sparkle', onClick: () => open(() => api.post('/api/vault/demo'), 'Preparing the demo vault…'), title: 'A messy sample vault with a built-in fake model — no AI server needed' }),
        cur.open ? button('Close vault', { kind: 'ghost', onClick: () => open(() => api.post('/api/vault/close'), 'Closing…') }) : null),
      cur.open && cur.mock ? h('p', { class: 'muted', style: { marginTop: '10px', fontSize: 'var(--fs-sm)' } }, 'A built-in fake model is answering, so you can try everything without an AI server.') : null,
      cur.scan_error ? h('p', { style: { color: 'var(--rose)', marginTop: '10px' } }, 'The scan hit a problem: ' + cur.scan_error) : null);
  }

  function paintWs() {
    const rows = Object.entries(app.workflows).map(([id, w]) => h('button', { class: 'ws', disabled: !cur.open, title: cur.open ? `Open the ${w.title} workspace` : 'Open a vault first',
      onclick: async () => { try { cur = await api.post('/api/workflow', { id }); onStatus(cur); onEnter(); } catch (e) { toastError(e); } } },
      h('div', { class: 'glyph' }, GLYPH[id]()),
      h('div', null, h('h3', null, w.title.charAt(0) + w.title.slice(1).toLowerCase()), h('p', null, w.description),
        h('div', { class: 'tags' }, w.needs_llm ? null : h('span', { class: 'offline' }, 'Works offline'), w.tabs.map((t) => h('span', { class: 'chip' }, app.tab_labels[t])))),
      h('span', { class: 'go' }, icon('arrow'))));
    mount(wsCol, h('h2', null, 'Workspace'), h('div', { class: 'ws-list' }, rows));
  }
  function paint() { paintVault(); paintWs(); }

  mount(wrap,
    h('header', { class: 'home-head' }, h('img', { src: '/static/icon.png', alt: '' }),
      h('div', null, h('h1', null, 'Vault Manager'),
        h('p', null, 'Tag your notes, repair their headers and tidy your folders. The AI proposes, you review, and every change can be undone.'))),
    h('div', { class: 'home-grid' }, vaultCol, wsCol),
    h('footer', { class: 'home-foot' }, h('span', null, `Version ${app.version}`), h('span', null, 'Your notes stay on this computer; only the text sent to your configured model leaves it.')));
  paint();
}
