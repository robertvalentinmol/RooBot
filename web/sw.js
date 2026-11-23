// RooBot: service worker mínimo. Permite instalar RooBot como app desde Chrome y, si el servidor no está
// en marcha (Termux cerrado), enseña cómo arrancarlo en vez de la página de error del navegador.
// No guarda nada en caché: la app siempre se sirve desde el servidor local.
const PAGINA = `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>RooBot</title></head>
<body style="background:#0d1420;color:#eaf0f8;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center;padding:24px;box-sizing:border-box">
<div><h1 style="font-size:22px;margin:0 0 12px">RooBot no está en marcha</h1>
<p style="color:#93a4bc;line-height:1.5;margin:0 0 20px">Abre Termux y escribe <b style="color:#eaf0f8">roobot</b>.<br>Con Termux:Boot se arranca solo al encender la consola.</p>
<button onclick="location.reload()" style="font-size:16px;padding:10px 20px;border-radius:8px;border:0;background:#3b82f6;color:#fff">Reintentar</button></div>
</body></html>`;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (e) => {
  if (e.request.mode !== 'navigate') return;          // el resto, directo al servidor
  e.respondWith(fetch(e.request).catch(() =>
    new Response(PAGINA, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});
