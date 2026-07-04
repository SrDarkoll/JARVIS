/**
 * Widget renderer for assistant responses.
 *
 * The backend may embed visual payloads as:
 *   <WIDGET>{"type":"spotify","track":"...","artist":"..."}</WIDGET>
 *
 * Keep this regex in sync with _limpiar_metadatos_voz() in
 * core/brain/brain_utils.py so widget JSON is not spoken by TTS.
 */

function clearElement(element) {
    while (element.firstChild) {
        element.removeChild(element.firstChild);
    }
}

function createTextElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    element.textContent = text == null ? '' : String(text);
    return element;
}

function safeImageSrc(rawSrc) {
    const src = String(rawSrc || '').trim();
    if (!src) return '';
    if (/^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(src)) return src;

    try {
        const url = new URL(src, window.location.origin);
        if (url.protocol === 'http:' || url.protocol === 'https:' || url.protocol === 'blob:') {
            return url.href;
        }
    } catch (_) {
        return '';
    }
    return '';
}

export class WidgetManager {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.activeTimeout = null;
    }

    processResponse(text) {
        const widgetRegex = /<WIDGET>([\s\S]*?)<\/WIDGET>/g;
        let match;
        let cleanText = text;

        while ((match = widgetRegex.exec(text)) !== null) {
            try {
                const widgetData = JSON.parse(match[1]);
                this.renderWidget(widgetData);
                cleanText = cleanText.replace(match[0], '');
            } catch (e) {
                console.warn('[WIDGETS] Error parsing widget:', e);
            }
        }

        return cleanText.trim();
    }

    renderWidget(data) {
        if (!this.container) return;

        const card = document.createElement('div');
        card.className = `widget-card tech-glass fade-in-up type-${data.type || 'generic'}`;

        const header = createTextElement('div', 'widget-header', (data.title || 'MODULO').toUpperCase());
        const content = document.createElement('div');
        content.className = 'widget-content';

        switch (data.type) {
            case 'spotify':
                this.renderSpotifyWidget(content, data);
                break;
            case 'weather':
                this.renderWeatherWidget(content, data);
                break;
            case 'nba':
                this.renderNbaWidget(content, data);
                break;
            default:
                content.appendChild(createTextElement('pre', 'generic-data', JSON.stringify(data, null, 2)));
        }

        card.append(header, content);

        if (this.activeTimeout) clearTimeout(this.activeTimeout);
        clearElement(this.container);
        this.container.appendChild(card);

        this.activeTimeout = setTimeout(() => {
            card.classList.add('fade-out');
            setTimeout(() => card.remove(), 1000);
        }, 15000);
    }

    renderSpotifyWidget(content, data) {
        const wrapper = document.createElement('div');
        wrapper.className = 'spotify-widget';

        const image = document.createElement('img');
        image.className = 'track-art';
        image.alt = 'Album art';
        image.loading = 'lazy';
        image.referrerPolicy = 'no-referrer';
        image.src = safeImageSrc(data.image);

        const info = document.createElement('div');
        info.className = 'track-info';
        info.append(
            createTextElement('span', 'track-name', data.track || 'Desbloqueado'),
            createTextElement('span', 'artist-name', data.artist || 'Artista Desconocido'),
        );

        wrapper.append(image, info);
        content.appendChild(wrapper);
    }

    renderWeatherWidget(content, data) {
        const wrapper = document.createElement('div');
        wrapper.className = 'weather-widget';
        wrapper.append(
            createTextElement('span', 'weather-temp', `${data.temp || '--'} C`),
            createTextElement('span', 'weather-desc', data.desc || 'Escaneando...'),
        );
        content.appendChild(wrapper);
    }

    renderNbaWidget(content, data) {
        const wrapper = document.createElement('div');
        wrapper.className = 'nba-widget';
        const payload = data.data || {};
        const games = Array.isArray(payload.games)
            ? payload.games
            : (Array.isArray(payload.partidos) ? payload.partidos : []);

        if (games.length === 0) {
            wrapper.appendChild(createTextElement('div', 'nba-game', 'No hay partidos disponibles.'));
            content.appendChild(wrapper);
            return;
        }

        games.forEach((game) => {
            const row = document.createElement('div');
            row.className = 'nba-game';
            row.append(
                createTextElement('span', 'nba-team', game.home || game.local || 'Local'),
                createTextElement('span', 'nba-score', game.score || game.marcador || game.time || game.horario || '-'),
                createTextElement('span', 'nba-team', game.away || game.visitante || 'Visitante'),
            );
            wrapper.appendChild(row);
        });
        content.appendChild(wrapper);
    }
}
