// Minimal PWA Service Worker for iOS / Desktop Add-to-Home-Screen standalone mode
const CACHE_NAME = 'videodownloader-shell-v1';
const SHELL_ASSETS = ['/', '/static/styles.css', '/static/app.js', '/static/icon.svg'];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) {
    return;
  }
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
