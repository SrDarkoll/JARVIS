import * as THREE from '../../vendor/three.module.min.js';

/**
 * Motor de Renderizado del Reactor Arc v2.0 (Three.js)
 * Implementa una esfera de energía, anillos de confinamiento y partículas.
 */

const STATE_COLORS = Object.freeze({
    idle: 0x00f2ff,
    listening: 0xffcc00,       // Golden amber during active listening
    transcribing: 0x00d2ff,    // High-tech cyan
    processing: 0x0077ff,      // Deep sapphire while reasoning
    confirmation: 0xffaa00,
    speaking: 0x00ffaa,        // Emerald green while responding
    success: 0x00ffaa,         // Emerald green on success
    error: 0xff3366,           // Crimson alert
    offline: 0x778899,
});

export class ArcReactor {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;

        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);
        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            alpha: true,
            antialias: true,
        });

        this.reducedMotion = false;
        if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
            const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
            this.reducedMotion = Boolean(mq.matches);
            mq.addEventListener('change', (e) => {
                this.reducedMotion = Boolean(e.matches);
            });
        }

        this.init();
        this.animate();
    }

    init() {
        this.resize();
        this.camera.position.z = 6.0;

        // --- Núcleo de Energía (Esfera central) ---
        const coreGeom = new THREE.IcosahedronGeometry(1.2, 2);
        const coreMat = new THREE.MeshBasicMaterial({
            color: 0x00f2ff,
            wireframe: true,
            transparent: true,
            opacity: 0.8,
        });
        this.core = new THREE.Mesh(coreGeom, coreMat);
        this.scene.add(this.core);

        // --- Anillos de Confinamiento ---
        this.rings = [];
        const ringConfigs = [
            { r: 1.8, w: 0.05, c: 0x00f2ff, speed: 0.01 },
            { r: 2.1, w: 0.03, c: 0x0077ff, speed: -0.015 },
            { r: 2.4, w: 0.02, c: 0x00f2ff, speed: 0.005 },
        ];

        ringConfigs.forEach((conf) => {
            const geom = new THREE.TorusGeometry(conf.r, conf.w, 16, 100);
            const mat = new THREE.MeshBasicMaterial({ color: conf.c, transparent: true, opacity: 0.6 });
            const ring = new THREE.Mesh(geom, mat);
            ring.rotation.x = Math.random() * Math.PI;
            ring.rotation.y = Math.random() * Math.PI;
            this.scene.add(ring);
            this.rings.push({ mesh: ring, speed: conf.speed });
        });

        // --- Sistema de Partículas ---
        const partGeom = new THREE.BufferGeometry();
        const partCount = 500;
        const pos = new Float32Array(partCount * 3);
        for (let i = 0; i < partCount * 3; i++) pos[i] = (Math.random() - 0.5) * 10;
        partGeom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        const partMat = new THREE.PointsMaterial({ color: 0x00f2ff, size: 0.02, transparent: true, opacity: 0.5 });
        this.particles = new THREE.Points(partGeom, partMat);
        this.scene.add(this.particles);
    }

    resize() {
        if (!this.canvas || !this.renderer || !this.camera) return false;
        const size = Math.max(96, Math.round(this.canvas.clientWidth || 300));
        this.camera.aspect = 1;
        this.camera.updateProjectionMatrix();
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        this.renderer.setSize(size, size, false);
        return true;
    }

    setState(state) {
        this.currentState = state;
        const hex = STATE_COLORS[state] || STATE_COLORS.idle;
        if (this.core && this.core.material) {
            this.core.material.color.setHex(hex);
        }
        if (this.particles && this.particles.material) {
            this.particles.material.color.setHex(hex);
        }
        if (this.rings) {
            this.rings.forEach((r, idx) => {
                if (r.mesh && r.mesh.material) {
                    r.mesh.material.color.setHex(idx === 1 && state === 'idle' ? 0x0077ff : hex);
                }
            });
        }
        const container = this.canvas?.parentElement;
        if (container) {
            container.dataset.state = state;
        }
    }

    animate() {
        requestAnimationFrame(() => this.animate());

        if (!this.reducedMotion) {
            const mult = this.currentState === 'processing' ? 2.5 : (this.currentState === 'listening' ? 1.4 : 1.0);
            if (this.core) {
                this.core.rotation.y += 0.005 * mult;
                this.core.rotation.z += 0.002 * mult;
                const pulseRate = this.currentState === 'listening' ? 0.006 : 0.002;
                const s = 1 + Math.sin(Date.now() * pulseRate) * (this.currentState === 'listening' ? 0.08 : 0.05);
                this.core.scale.set(s, s, s);
            }

            this.rings.forEach((r) => {
                if (r.mesh) {
                    r.mesh.rotation.x += r.speed * mult;
                    r.mesh.rotation.y += r.speed * 0.5 * mult;
                }
            });

            if (this.particles) {
                this.particles.rotation.y -= 0.001 * mult;
            }
        }

        this.renderer.render(this.scene, this.camera);
    }
}
