// Jobs: live list of AI runs with pause / resume / cancel / retry and a per-job log.
import { h, mount, api, fmt, store, debounce } from '../core.js';
import { splitPane, emptyState, button, iconButton, toast, toastError, kv } from '../ui.js';

export function createJobs(ctx) {
  let jobs = [], sel = null, detail = null, logFrom = 0, logLines = [];
  const list = h('div', { class: 'job-list' });
  const side = h('div', { class: 'inspector' });
  const bar = h('div', { class: 'toolbar' });
  const post = async (path, body) => { try { return await api.post(path, body || {}); } catch (e) { toastError(e); } };

  const stateBadge = (j) => h('span', { class: 'badge ' + (j.state === 'running' ? 'ai' : j.state === 'paused' ? 'warn' : j.state === 'cancelled' ? 'plain' : j.errors ? 'err' : 'ok') }, j.state);
  function paintBar() {
    const act = jobs.some((j) => j.active);
    mount(bar, h('span', { class: 'muted' }, act ? `${jobs.filter((j) => j.active).length} running` : 'Idle'), h('span', { class: 'grow' }),
      button(ctx.st?.paused ? 'Resume all' : 'Pause all', { size: 'sm', icon: ctx.st?.paused ? 'play' : 'pause', onClick: () => post('/api/jobs-all/toggle'), }),
      button('Stop all', { size: 'sm', icon: 'stop', onClick: () => post('/api/jobs-all/cancel') }), button('Clear finished', { size: 'sm', onClick: () => post('/api/jobs-all/clear') }));
    bar.children[2].disabled = !act; bar.children[3].disabled = !act;
  }
  function drawList() {
    if (!jobs.length) return mount(list, emptyState({ title: 'No jobs yet', text: 'AI runs show up here with live progress. Start one from the Tagger, the Organizer, or the command line.' }));
    mount(list, jobs.map((j) => {
      const pct = j.total ? Math.round((100 * j.finished) / j.total) : 0;
      return h('button', { class: 'job' + (sel === j.id ? ' cur' : ''), onclick: () => select(j.id) },
        h('div', { class: 'row' }, h('b', { class: 'grow', style: { fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, `#${j.id} ${j.title || j.kind}`), stateBadge(j)),
        h('div', { class: 'meterbar' }, h('i', { style: { width: pct + '%' } })),
        h('div', { class: 'row muted', style: { fontSize: 'var(--fs-sm)' } }, h('span', null, `${fmt.n(j.finished)}/${fmt.n(j.total)}`), j.errors ? h('span', { style: { color: 'var(--rose)' } }, `${j.errors} failed`) : null, j.skipped ? h('span', null, `${j.skipped} skipped`) : null,
          h('span', { class: 'grow' }), j.active && j.rate ? h('span', null, fmt.rate(j.rate)) : null, j.tokens ? h('span', null, `${fmt.n(j.tokens)} tok`) : null));
    }));
  }
  async function select(id) { sel = id; logFrom = 0; logLines = []; drawList(); await loadDetail(); }
  async function loadDetail() {
    if (sel == null) return mount(side, emptyState({ title: 'Pick a job', text: 'Its log and failed items show here.' }));
    try { detail = await api.get(`/api/jobs/${sel}`, { log_from: logFrom }); } catch (e) { sel = null; return mount(side, emptyState({ title: 'Job not found', text: e.message })); }
    logLines.push(...detail.log); logFrom = detail.log_len;
    const j = detail, fails = (j.items || []).filter((i) => i.state === 'error');
    const logEl = h('pre', { class: 'diff', style: { maxHeight: '38vh', overflow: 'auto' } }, logLines.slice(-400).map((l) => h('div', { class: 'ctx' }, String(l))));
    mount(side, h('div', { class: 'ins-head' }, h('h2', { style: { fontFamily: 'var(--font-serif)', fontSize: 'var(--fs-xl)' } }, `#${j.id} ${j.title || j.kind}`)),
      h('div', { class: 'row wrap', style: { padding: '0 14px 10px' } },
        j.active ? (j.state === 'paused' ? button('Resume', { size: 'sm', icon: 'play', onClick: () => post(`/api/jobs/${j.id}/resume`) }) : button('Pause', { size: 'sm', icon: 'pause', onClick: () => post(`/api/jobs/${j.id}/pause`) })) : null,
        j.active ? button('Cancel', { size: 'sm', icon: 'stop', onClick: () => post(`/api/jobs/${j.id}/cancel`) }) : null,
        j.errors ? button(`Retry ${j.errors} failed`, { size: 'sm', icon: 'refresh', onClick: async () => { const r = await post(`/api/jobs/${j.id}/retry`); if (r?.job) select(r.job.id); } }) : null,
        j.active ? button(`Workers: ${j.workers}`, { size: 'sm', onClick: async () => { const n = prompt('Parallel workers', j.workers); if (n) { await post(`/api/jobs/${j.id}/workers`, { n }); } } }) : null),
      h('div', { class: 'ins-sec' }, kv({ State: j.state, Progress: `${fmt.n(j.finished)} of ${fmt.n(j.total)}`, Done: j.done, Failed: j.errors, Skipped: j.skipped, Tokens: fmt.n(j.tokens), Rate: fmt.rate(j.rate) })),
      fails.length ? h('div', { class: 'ins-sec' }, h('h3', null, 'Failed'), h('div', { class: 'stack' }, fails.slice(0, 30).map((i) => h('div', null, h('b', null, i.key), h('div', { class: 'muted', style: { fontSize: 'var(--fs-sm)' } }, i.message))))) : null,
      h('div', { class: 'ins-sec' }, h('h3', null, 'Log'), logEl));
    logEl.scrollTop = logEl.scrollHeight;
  }
  async function refresh() {
    try { jobs = (await api.get('/api/jobs')).jobs; } catch (e) { return toastError(e); }
    if (sel == null && jobs.length) sel = jobs[0].id;
    paintBar(); drawList();
    if (sel != null && !jobs.some((j) => j.id === sel)) sel = null;
    loadDetail();
  }
  const left = h('div', { style: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 } }, bar, h('div', { style: { overflow: 'auto', flex: 1 } }, list));
  const sp = splitPane(left, side, { key: 'jobs-side', width: 520, min: 300, max: 1000 });
  sp.left.classList.replace('fixed', 'fill'); sp.right.classList.replace('fill', 'fixed');
  sp.left.style.cssText += ';flex:1 1 auto;display:flex;flex-direction:column;overflow:hidden';
  sp.right.style.cssText += ';flex:none;width:400px;background:var(--panel);border-left:1px solid var(--line)';
  return { el: sp.el, refresh };
}
