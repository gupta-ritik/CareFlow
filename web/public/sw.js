// CareFlow service worker: shows push alerts, opens the right page when one is tapped, and shows a clear
// "no connection" page instead of the browser's error when the phone is offline. It caches no patient data.
const OFFLINE_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareFlow is offline</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#eaf1f4;color:#0f2236;
font-family:system-ui,sans-serif;padding:16px}div{max-width:22rem;text-align:center}p{color:#56677a;line-height:1.5}
button{margin-top:8px;border:0;border-radius:12px;background:#0f2236;color:#fff;font-weight:600;padding:10px 16px}</style></head>
<body><div><h1>No connection</h1><p>CareFlow needs the internet to show live discharge data. Check Wi-Fi or mobile data and try again.</p>
<button onclick="location.reload()">Try again</button></div></body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return; // API calls and assets go straight to the network
  event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE_HTML, { headers: { "content-type": "text/html; charset=utf-8" } })));
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "CareFlow", {
      body: data.body || "",
      tag: data.tag,
      renotify: Boolean(data.tag),
      icon: "/icon-192.png",
      badge: "/badge-96.png",
      data: { url: data.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const w of windows) {
        if (new URL(w.url).origin === self.location.origin) {
          await w.focus();
          return w.navigate(target);
        }
      }
      return self.clients.openWindow(target);
    })(),
  );
});
