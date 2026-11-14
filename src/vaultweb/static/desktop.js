/* Desktop-window glue (classic script, loaded on every page; does nothing in a normal browser tab started with `serve`).
   - tells the app when the window goes away, so the program can exit
   - keeps the app informed that the window is alive (Chromium may be minimised)
   - opens external links in the real browser */
(function () {
  var cfg = window.__VM__ || {};
  if (!cfg.desktop) return;
  var bye = function () { try { navigator.sendBeacon('/desktop/bye?t=' + encodeURIComponent(cfg.token)); } catch (e) { /* ignore */ } };
  window.addEventListener('pagehide', bye);
  if (cfg.heartbeat) setInterval(function () { fetch('/healthz', { cache: 'no-store' }).catch(function () {}); }, 20000);
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    var u = a.getAttribute('href') || '';
    if (/^https?:/i.test(u) && a.origin !== location.origin) {
      e.preventDefault();
      fetch('/api/open-external', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-VaultMgr': '1' }, body: JSON.stringify({ url: u }) });
    }
  });
})();
