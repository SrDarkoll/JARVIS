/**
 * Módulo de Gestión de Widgets para JARVIS.
 * Extrae y renderiza componentes visuales embebidos en el texto de la IA.
 *
 * ═══════════════════════════════════════════════════════════════════════════════
 * PROTOCOLO WIDGET — Contrato Backend → Frontend
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Los bloques <WIDGET> son JSON embebido en la respuesta del LLM. El frontend
 * los detecta, renderiza visualmente, y los REMUEVE del texto antes de enviarlo
 * al TTS (para que Piper no intente leer JSON).
 *
 * FORMATO:
 *   <WIDGET>{"type": "tipo", "data": {...}}</WIDGET>
 *
 *   El bloque puede tener campos adicionales además de "type" y "data".
 *   Todos los campos se pasan al renderer del tipo correspondiente.
 *
 * TIPOS SOPORTADOS:
 *
 *   spotify   → Renderiza tarjeta de reproducción
 *               Campos: type="spotify", track, artist, image
 *
 *   weather   → Renderiza widget de clima
 *               Campos: type="weather", temp, desc, city (opcional)
 *
 *   nba       → Renderiza resultados deportivos
 *               Campos: type="nba", data.games o data.partidos (array)
 *
 *   generic   → Fallback: renderiza JSON completo formateado
 *
 * FLUJO:
 *   1. LLM devuelve respuesta con <WIDGET>...</WIDGET>
 *   2. main.js detecta el bloque, lo extrae → limpia texto para TTS
 *   3. WidgetManager.processResponse() parsea y renderiza
 *   4. TTS recibe texto SIN el bloque JSON (limpieza en brain_utils.py)
 *
 * IMPORTANTE: Mantén el regex /<WIDGET>[\s\S]*?<\/WIDGET>/ sincronizado con
 *            _limpiar_metadatos_voz() en core/brain/brain_utils.py
 */

function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
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
                console.warn('[WIDGETS] Error parseando widget:', e);
            }
        }

        return cleanText.trim();
    }

    renderWidget(data) {
        if (!this.container) return;

        const card = document.createElement('div');
        card.className = `widget-card tech-glass fade-in-up type-${data.type || 'generic'}`;
        
        let html = `<div class="widget-header">${escapeHtml((data.title || 'MÓDULO').toUpperCase())}</div>`;
        html += '<div class="widget-content">';

        switch (data.type) {
            case 'spotify':
                html += `
                    <div class="spotify-widget">
                        <img src="${escapeHtml(data.image || '')}" class="track-art" alt="Album art">
                        <div class="track-info">
                            <span class="track-name">${escapeHtml(data.track || 'Desbloqueado')}</span>
                            <span class="artist-name">${escapeHtml(data.artist || 'Artista Desconocido')}</span>
                        </div>
                    </div>`;
                break;
            case 'weather':
                html += `
                    <div class="weather-widget">
                        <span class="weather-temp">${escapeHtml(data.temp || '--')}°C</span>
                        <span class="weather-desc">${escapeHtml(data.desc || 'Escaneando...')}</span>
                    </div>`;
                break;
            case 'nba':
                html += '<div class="nba-widget">';
                (data.data.games || data.data.partidos || []).forEach(g => {
                    html += `<div class="nba-game">
                        <span class="nba-team">${escapeHtml(g.home || g.local || 'Local')}</span>
                        <span class="nba-score">${escapeHtml(g.score || g.marcador || g.time || g.horario || '—')}</span>
                        <span class="nba-team">${escapeHtml(g.away || g.visitante || 'Visitante')}</span>
                    </div>`;
                });
                html += '</div>';
                break;
            default:
                html += `<pre class="generic-data">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`;
        }

        html += '</div>';
        card.innerHTML = html;

        if (this.activeTimeout) clearTimeout(this.activeTimeout);
        this.container.innerHTML = '';
        this.container.appendChild(card);

        this.activeTimeout = setTimeout(() => {
            card.classList.add('fade-out');
            setTimeout(() => card.remove(), 1000);
        }, 15000);
    }
}
