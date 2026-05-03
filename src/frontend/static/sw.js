const CACHE_NAME = 'jarvis-v2';
const ASSETS = [
    '/',
    '/static/css/style.css',
    '/static/js/main.js',
    '/media/favicon.svg'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll(ASSETS);
        }).catch(err => console.log('SW Install error', err))
    );
});

self.addEventListener('fetch', event => {
    // Para las peticiones a nuestra API, siempre ir por red.
    if (event.request.url.includes('/api/')) {
        return;
    }
    
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
