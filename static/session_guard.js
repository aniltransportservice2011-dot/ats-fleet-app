/* Keeps a screen that was left untouched for hours working. One script for the website and the phone app.

   A page left open overnight (or a laptop / phone that slept) can have a dead connection or an ended session; its soft-navigation clicks used to do nothing at
   all. After 30 minutes without a touch, the NEXT click / tap / key press first asks the server "am I still signed in?" (/session-ping):
     - the answer is no, or the server cannot be reached  -> the page reloads, which lands on the login screen or simply gets a fresh connection;
     - after 8 hours untouched, and nothing typed on the page that would be lost -> the page reloads so the data is fresh;
     - otherwise nothing happens and the click goes through as normal.
   Coming back to the tab / window after a long absence runs the same check straight away. */
(function () {
  'use strict';
  if (window.__sessionGuard) return;
  window.__sessionGuard = true;
  var QUIET_MS = 30 * 60 * 1000, STALE_MS = 8 * 60 * 60 * 1000;
  var last = Date.now(), dirty = false, checking = false;

  function reload() { try { window.location.reload(); } catch (e) { window.location.href = window.location.href; } }
  function loggedOut(resp) {
    if (!resp || !resp.ok) return true;
    try { if (resp.redirected && /\/login/.test(new URL(resp.url).pathname)) return true; } catch (e) {}
    return false;
  }
  function check(quiet) {
    if (checking) return;
    checking = true;
    var done = function () { checking = false; };
    if (quiet >= STALE_MS && !dirty) { reload(); return; }
    var ctl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) ctl.abort(); }, 10000);
    fetch('/session-ping', { credentials: 'same-origin', cache: 'no-store', signal: ctl ? ctl.signal : undefined }).then(function (r) {
      clearTimeout(timer); done();
      if (loggedOut(r)) reload();
    }).catch(function () { clearTimeout(timer); done(); reload(); });
  }
  function touched() {
    var now = Date.now(), quiet = now - last;
    last = now;
    if (quiet >= QUIET_MS) check(quiet);
  }
  ['pointerdown', 'keydown', 'touchstart'].forEach(function (ev) { document.addEventListener(ev, touched, true); });
  document.addEventListener('input', function (e) { if (e.target && e.target.closest && e.target.closest('form')) dirty = true; }, true);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible') return;
    var quiet = Date.now() - last;
    if (quiet >= QUIET_MS) { last = Date.now(); check(quiet); }
  });
  window.addEventListener('pageshow', function (e) { if (e.persisted) check(STALE_MS - 1); });     // restored from the back/forward cache
  window.addEventListener('online', function () { check(0); });
})();
