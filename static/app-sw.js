// Service worker for the /app/ section only (registered with scope:'/app/' in base_app.html) —
// its only real job is to satisfy Chrome/Android's install-prompt requirement. It deliberately
// does NOT cache any page or data response: every fleet number must always come straight from
// the server, same "always fresh" principle the rest of this app follows. Only the tiny static
// shell (icons) gets cached, purely so the installed app's icon/manifest resolve instantly.
const SHELL_CACHE = 'ats-app-shell-v1';
const SHELL_ASSETS = [
  '/static/app-icons/icon-192.png',
  '/static/app-icons/icon-512.png',
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(function (cache) { return cache.addAll(SHELL_ASSETS); })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.filter(function (n) { return n !== SHELL_CACHE; }).map(function (n) { return caches.delete(n); }));
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function (event) {
  var url = new URL(event.request.url);
  // Only ever serve the tiny shell assets from cache; everything else (every page, every bit of
  // real data) always goes to the network untouched.
  if (SHELL_ASSETS.some(function (a) { return url.pathname === a; })) {
    event.respondWith(caches.match(event.request).then(function (cached) { return cached || fetch(event.request); }));
  }
});
