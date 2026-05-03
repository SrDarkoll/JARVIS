/**
 * Motor de Renderizado del Reactor Arc v2.0 (Three.js)
 * Implementa una esfera de energía, anillos de confinamiento y partículas.
 */

export class ArcReactor {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;

        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);
        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            alpha: true,
            antialias: true
        });

        this.init();
        this.animate();
    }

    init() {
        const size = this.canvas.clientWidth || 300;
        this.renderer.setSize(size, size);
        this.camera.position.z = 5;

        // --- Núcleo de Energía (Esfera central) ---
        const coreGeom = new THREE.IcosahedronGeometry(1.2, 2);
        const coreMat = new THREE.MeshBasicMaterial({
            color: 0x00f2ff,
            wireframe: true,
            transparent: true,
            opacity: 0.8
        });
        this.core = new THREE.Mesh(coreGeom, coreMat);
        this.scene.add(this.core);

        // --- Anillos de Confinamiento ---
        this.rings = [];
        const ringConfigs = [
            { r: 1.8, w: 0.05, c: 0x00f2ff, speed: 0.01 },
            { r: 2.1, w: 0.03, c: 0x0077ff, speed: -0.015 },
            { r: 2.4, w: 0.02, c: 0x00f2ff, speed: 0.005 }
        ];

        ringConfigs.forEach(conf => {
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

    animate() {
        requestAnimationFrame(() => this.animate());

        if (this.core) {
            this.core.rotation.y += 0.005;
            this.core.rotation.z += 0.002;
            const s = 1 + Math.sin(Date.now() * 0.002) * 0.05;
            this.core.scale.set(s, s, s);
        }

        this.rings.forEach(r => {
            r.mesh.rotation.x += r.speed;
            r.mesh.rotation.y += r.speed * 1.2;
        });

        if (this.particles) {
            this.particles.rotation.y += 0.001;
        }

        this.renderer.render(this.scene, this.camera);
    }

    resize() {
        if (!this.canvas) return;
        const size = this.canvas.clientWidth || 300;
        this.renderer.setSize(size, size);
        this.camera.aspect = 1;
        this.camera.updateProjectionMatrix();
    }

    dispose() {
        if (this.core) {
            this.core.geometry.dispose();
            this.core.material.dispose();
        }
        this.rings.forEach(r => {
            r.mesh.geometry.dispose();
            r.mesh.material.dispose();
        });
        if (this.particles) {
            this.particles.geometry.dispose();
            this.particles.material.dispose();
        }
        if (this.renderer) {
            this.renderer.dispose();
        }
    }
}
