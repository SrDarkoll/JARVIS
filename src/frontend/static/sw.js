const CACHE_NAME = 'jarvis-v6';
const ASSETS = [
    '/',
    '/static/css/style.css?v=1.5.0',
    '/static/js/main.js?v=1.5.0',
    '/media/favicon.svg'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll(ASSETS);
        }).catch(err => console.log('SW Install error', err))
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(names => Promise.all(
            names
                .filter(name => name.startsWith('jarvis-') && name !== CACHE_NAME)
                .map(name => caches.delete(name))
        ))
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
