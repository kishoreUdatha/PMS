/* MyGuest service worker.
 *
 * Makes the app installable and lets it open without a network, and does
 * nothing clever beyond that.
 *
 * What it caches: the app's own static files (the built JS, CSS, fonts and
 * icons). What it NEVER caches: anything under /api, /flows or /book. Those
 * are live room status, bookings and money. A stale cached copy of a folio
 * balance or a room's status is worse than an honest "you are offline",
 * because somebody would act on it.
 *
 * Navigations are network-first. The app shell is served from cache only
 * when the network is down, so a deploy is picked up on the next load.
 */
const CACHE = 'myguest-shell-v1'
const SHELL = ['/', '/manifest.webmanifest', '/favicon.svg', '/icon-192.png', '/icon-512.png']
const NEVER = [/^\/api\//, /^\/flows\//, /^\/book\//]

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return
  if (NEVER.some((re) => re.test(url.pathname))) return

  if (req.mode === 'navigate') {
    // Network first: the newest app, or the cached shell when offline.
    event.respondWith(fetch(req).catch(() => caches.match('/')))
    return
  }
  // Static assets: Vite fingerprints them, so a cached copy is never stale.
  if (/\.(js|css|woff2?|png|svg|webp|ico)$/.test(url.pathname)) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone()
          caches.open(CACHE).then((c) => c.put(req, copy))
        }
        return res
      })),
    )
  }
})
