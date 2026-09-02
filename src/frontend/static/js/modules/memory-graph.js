/**
 * Visualizador interactivo de la Red Cognitiva / Grafo de Memoria para el HUD de J.A.R.V.I.S.
 * Renderiza nodos conceptuales, procedimentales y episódicos como una constelación neuronal interactiva.
 */

const NODE_COLORS = {
    concept: '#00d2ff',    // Cyan - Conceptos y entidades
    fact: '#00ffaa',       // Menta - Hechos y datos objetivos
    preference: '#ff77aa', // Rosa - Preferencias de usuario
    episode: '#b388ff',    // Púrpura - Memoria episódica
    skill: '#ffaa00',      // Ámbar - Procedimientos y habilidades
    default: '#00d2ff',
};

export class MemoryGraphRenderer {
    constructor({ canvas, countEl, infoEl, refreshBtn }) {
        this.canvas = canvas;
        this.ctx = canvas?.getContext('2d');
        this.countEl = countEl;
        this.infoEl = infoEl;
        this.refreshBtn = refreshBtn;

        this.nodes = [];
        this.links = [];
        this.hoveredNode = null;
        this.selectedNode = null;
        this.animFrameId = null;
        this.running = false;
        this.width = 280;
        this.height = 180;

        if (this.canvas && this.ctx) {
            this._setupCanvas();
            this._bindEvents();
            this.refresh();
        }
    }

    _setupCanvas() {
        const rect = this.canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        this.width = rect.width > 50 ? rect.width : 280;
        this.height = rect.height > 50 ? rect.height : 180;

        this.canvas.width = this.width * dpr;
        this.canvas.height = this.height * dpr;
        this.ctx.scale(dpr, dpr);
    }

    _bindEvents() {
        this.canvas.addEventListener('mousemove', (e) => this._onMouseMove(e));
        this.canvas.addEventListener('mouseleave', () => this._onMouseLeave());
        this.canvas.addEventListener('click', (e) => this._onClick(e));

        if (this.refreshBtn) {
            this.refreshBtn.addEventListener('click', () => {
                this.refreshBtn.classList.add('rotating');
                this.refresh().finally(() => {
                    setTimeout(() => this.refreshBtn.classList.remove('rotating'), 600);
                });
            });
        }

        window.addEventListener('resize', () => this._setupCanvas());
    }

    async refresh() {
        try {
            const res = await fetch('/api/memory/graph?limit=40');
            if (!res.ok) return;
            const data = await res.json();
            if (data.ok) {
                this._buildGraph(data.nodes || [], data.links || []);
                if (this.countEl) {
                    const count = data.count || 0;
                    this.countEl.textContent = `${count} ${count === 1 ? 'nodo cognitivo' : 'nodos cognitivos'}`;
                }
            }
        } catch (_err) {
            // Fails gracefully in offline or restricted environments
        }
    }

    _buildGraph(rawNodes, rawLinks) {
        const cx = this.width / 2;
        const cy = this.height / 2;

        if (rawNodes.length === 0) {
            // Placeholder demonstration nodes when memory is fresh
            this.nodes = [
                { id: 'p1', title: 'JARVIS', type: 'concept', x: cx, y: cy, vx: 0, vy: 0, radius: 6, color: '#00d2ff', content: 'Asistente IA central' },
                { id: 'p2', title: 'Usuario', type: 'concept', x: cx - 50, y: cy - 30, vx: 0, vy: 0, radius: 5, color: '#00ffaa', content: 'Administrador del sistema' },
                { id: 'p3', title: 'Spotify', type: 'skill', x: cx + 55, y: cy + 30, vx: 0, vy: 0, radius: 5, color: '#ffaa00', content: 'Control de reproducción musical' },
                { id: 'p4', title: 'Rutinas', type: 'skill', x: cx - 40, y: cy + 45, vx: 0, vy: 0, radius: 4, color: '#b388ff', content: 'Automatizaciones aprendidas' },
            ];
            this.links = [
                { sourceId: 'p1', targetId: 'p2', relation: 'administrado_por' },
                { sourceId: 'p1', targetId: 'p3', relation: 'controla' },
                { sourceId: 'p1', targetId: 'p4', relation: 'ejecuta' },
            ];
            this._startLoop();
            return;
        }

        const titleToId = new Map();
        this.nodes = rawNodes.map((n, idx) => {
            titleToId.set(n.title.toLowerCase(), n.id);
            const angle = (idx / rawNodes.length) * Math.PI * 2;
            const dist = 35 + Math.random() * (Math.min(this.width, this.height) / 2 - 45);
            return {
                id: n.id,
                title: n.title,
                type: n.type || 'concept',
                content: n.content || '',
                tags: n.tags || [],
                x: cx + Math.cos(angle) * dist,
                y: cy + Math.sin(angle) * dist,
                vx: (Math.random() - 0.5) * 0.4,
                vy: (Math.random() - 0.5) * 0.4,
                radius: n.type === 'skill' ? 5 : (n.title.length < 8 ? 5.5 : 4.5),
                color: NODE_COLORS[n.type] || NODE_COLORS.default,
            };
        });

        this.links = [];
        for (const l of rawLinks) {
            const sId = titleToId.get(String(l.source).toLowerCase());
            const tId = titleToId.get(String(l.target).toLowerCase());
            if (sId && tId && sId !== tId) {
                this.links.push({
                    sourceId: sId,
                    targetId: tId,
                    relation: l.relation || 'relacionado',
                });
            }
        }

        this._startLoop();
    }

    _startLoop() {
        if (this.running) return;
        this.running = true;
        const tick = () => {
            if (!this.running) return;
            this._update();
            this._render();
            this.animFrameId = requestAnimationFrame(tick);
        };
        this.animFrameId = requestAnimationFrame(tick);
    }

    stop() {
        this.running = false;
        if (this.animFrameId) {
            cancelAnimationFrame(this.animFrameId);
            this.animFrameId = null;
        }
    }

    _update() {
        const cx = this.width / 2;
        const cy = this.height / 2;

        // Force simulation & gentle drift
        for (let i = 0; i < this.nodes.length; i++) {
            const node = this.nodes[i];

            // Soft pull toward center
            const dx = cx - node.x;
            const dy = cy - node.y;
            node.vx += dx * 0.0008;
            node.vy += dy * 0.0008;

            // Repulsion between nodes
            for (let j = i + 1; j < this.nodes.length; j++) {
                const other = this.nodes[j];
                const nx = other.x - node.x;
                const ny = other.y - node.y;
                const distSq = nx * nx + ny * ny;
                if (distSq > 0 && distSq < 3600) {
                    const dist = Math.sqrt(distSq);
                    const force = (60 - dist) / 60 * 0.08;
                    const fx = (nx / dist) * force;
                    const fy = (ny / dist) * force;
                    node.vx -= fx;
                    node.vy -= fy;
                    other.vx += fx;
                    other.vy += fy;
                }
            }

            // Damping & movement
            node.vx *= 0.94;
            node.vy *= 0.94;
            node.x += node.vx;
            node.y += node.vy;

            // Constrain inside bounds with soft bounce
            const pad = 12;
            if (node.x < pad) { node.x = pad; node.vx = Math.abs(node.vx) * 0.8; }
            if (node.x > this.width - pad) { node.x = this.width - pad; node.vx = -Math.abs(node.vx) * 0.8; }
            if (node.y < pad) { node.y = pad; node.vy = Math.abs(node.vy) * 0.8; }
            if (node.y > this.height - pad) { node.y = this.height - pad; node.vy = -Math.abs(node.vy) * 0.8; }
        }
    }

    _render() {
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.width, this.height);

        const nodeMap = new Map(this.nodes.map(n => [n.id, n]));

        // Render synapses / links
        for (const link of this.links) {
            const s = nodeMap.get(link.sourceId);
            const t = nodeMap.get(link.targetId);
            if (!s || !t) continue;

            const isHighlighted = (this.hoveredNode && (this.hoveredNode.id === s.id || this.hoveredNode.id === t.id));
            ctx.beginPath();
            ctx.moveTo(s.x, s.y);
            ctx.lineTo(t.x, t.y);
            ctx.strokeStyle = isHighlighted ? 'rgba(0, 210, 255, 0.8)' : 'rgba(0, 210, 255, 0.15)';
            ctx.lineWidth = isHighlighted ? 1.5 : 0.8;
            ctx.stroke();
        }

        // Render nodes
        const now = Date.now() * 0.003;
        for (const node of this.nodes) {
            const isHovered = this.hoveredNode?.id === node.id;
            const pulse = Math.sin(now + node.x * 0.1) * 0.8;
            const r = (node.radius + (isHovered ? 2.5 : pulse));

            // Outer glow halo
            ctx.beginPath();
            ctx.arc(node.x, node.y, r * 2, 0, Math.PI * 2);
            ctx.fillStyle = isHovered ? 'rgba(0, 210, 255, 0.35)' : 'rgba(0, 210, 255, 0.08)';
            ctx.fill();

            // Core circle
            ctx.beginPath();
            ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
            ctx.fillStyle = node.color;
            ctx.shadowColor = node.color;
            ctx.shadowBlur = isHovered ? 12 : 5;
            ctx.fill();
            ctx.shadowBlur = 0;

            // Text label for hovered or key nodes
            if (isHovered || this.nodes.length <= 8) {
                ctx.fillStyle = isHovered ? '#ffffff' : 'rgba(200, 235, 255, 0.75)';
                ctx.font = isHovered ? 'bold 10px Rajdhani, sans-serif' : '9px Rajdhani, sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText(node.title, node.x, node.y - r - 3);
            }
        }
    }

    _onMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        let found = null;
        for (const n of this.nodes) {
            const dist = Math.hypot(n.x - mx, n.y - my);
            if (dist <= n.radius + 6) {
                found = n;
                break;
            }
        }

        this.hoveredNode = found;
        if (found) {
            this.canvas.style.cursor = 'pointer';
            if (this.infoEl) {
                this.infoEl.innerHTML = `<span class="memory-tag" style="color: ${found.color}">[${found.type.toUpperCase()}]</span> <strong>${found.title}</strong>${found.content ? `<span class="memory-snippet"> - ${found.content}</span>` : ''}`;
            }
        } else {
            this.canvas.style.cursor = 'default';
        }
    }

    _onMouseLeave() {
        this.hoveredNode = null;
        this.canvas.style.cursor = 'default';
        if (this.infoEl) {
            const count = this.nodes.length;
            this.infoEl.innerHTML = `<span class="memory-count-label">${count} ${count === 1 ? 'nodo cognitivo' : 'nodos cognitivos'}</span>`;
        }
    }

    _onClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        // Apply outward shockwave from click
        for (const n of this.nodes) {
            const dx = n.x - mx;
            const dy = n.y - my;
            const dist = Math.hypot(dx, dy) || 1;
            if (dist < 100) {
                const force = (100 - dist) / 100 * 2.5;
                n.vx += (dx / dist) * force;
                n.vy += (dy / dist) * force;
            }
        }
    }
}
