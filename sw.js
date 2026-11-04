// Braiton Picks - service worker rahisi.
// Inapitisha kila request moja kwa moja kwenye mtandao (data ya picks lazima iwe mpya).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
