// SafeNisha 3D city map engine.
// Renders the baked Bengaluru model (see tools/build_data.py) with three.js and
// draws whatever the host app hands it: routes, endpoints, hazard reports,
// optional risk cells and the user's position. It computes no risk itself.

import * as THREE from './vendor/three.module.min.js';
import { MapControls } from './vendor/addons/controls/MapControls.js';
import { EffectComposer } from './vendor/addons/postprocessing/EffectComposer.js';
import { RenderPass } from './vendor/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from './vendor/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from './vendor/addons/postprocessing/OutputPass.js';
import { ShaderPass } from './vendor/addons/postprocessing/ShaderPass.js';
import { Line2 } from './vendor/addons/lines/Line2.js';
import { LineGeometry } from './vendor/addons/lines/LineGeometry.js';
import { LineMaterial } from './vendor/addons/lines/LineMaterial.js';
import earcut from './vendor/earcut.js';
import { fetchTile, buildTile, LOD_SIZES } from './tiles.js';

// ---------------------------------------------------------------------------
// Palette (from the SafeNisha map style frame)
// ---------------------------------------------------------------------------
export const PALETTE = {
  duskNavy: '#0A0F1F',
  safeTeal: '#3EE6B0',
  cautionCoral: '#FF5A4F',
  lamp: '#FFC56B',
  mist: '#E5E7EB',
  horizon: '#1B2648',
  zenith: '#060A16',
  fabric: '#28324F',
  roof: '#3B4560',
  wallLit: '#2A3350',
  wallDark: '#161D33',
  wallBase: '#0D1324',
  water: '#12405A',
  green: '#12322C',
  user: '#22D3EE',
};

// index = road class code written by tools/build_data.py (widths live in tiles.js)
const ROAD_COLORS = ['#3D4A70', '#3A466A', '#364264', '#303B5B', '#2A3552', '#222C47', '#1C253D', '#27424C', '#463F66', '#4A47AE', '#12405A'];

// lodPx: a building is drawn only while it spans at least this many CSS pixels
export const QUALITY = {
  lite: { label: 'Lite', pixelRatio: 1, bloom: false, msaa: 0, radius: 2200, lodPx: 4 },
  low: { label: 'Low detail', pixelRatio: 1, bloom: true, msaa: 0, radius: 3000, lodPx: 3 },
  medium: { label: 'Medium detail', pixelRatio: 1.5, bloom: true, msaa: 4, radius: 4200, lodPx: 2 },
  high: { label: 'High detail', pixelRatio: 2, bloom: true, msaa: 4, radius: 6000, lodPx: 1.5 },
};

const FOG_GLSL = /* glsl */ `
  uniform vec3 uFogColor;
  uniform float uFogRange;
  vec3 applyFog(vec3 c, vec3 wp) {
    float d = distance(wp, cameraPosition) / uFogRange;
    return mix(c, uFogColor, clamp(1.0 - exp(-d * d), 0.0, 1.0));
  }
`;

// One NaN or infinite pixel from any shader gets smeared by the bloom blur into
// a large black rectangle. This pass runs before the bloom and zeroes such pixels.
// isnan() catches it where the GPU keeps NaN; min(max(c, 0), cap) catches it
// where min/max follow IEEE minNum/maxNum (D3D), which map NaN to the bound.
const SanitizeShader = {
  name: 'SanitizeShader',
  uniforms: { tDiffuse: { value: null } },
  vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `uniform sampler2D tDiffuse; varying vec2 vUv;
    void main(){
      vec4 c = texture2D(tDiffuse, vUv);
      if (any(isnan(c)) || any(isinf(c))) c = vec4(0.0, 0.0, 0.0, 1.0);
      gl_FragColor = vec4(min(max(c.rgb, vec3(0.0)), vec3(32.0)), min(max(c.a, 0.0), 1.0));
    }`,
};

const col = (hex) => new THREE.Color(hex);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

const pad4 = (n) => (n + 3) & ~3;
const fetchBinary = fetchTile;

// ---------------------------------------------------------------------------
export class CityMap {
  constructor({ canvas, overlay, dataUrl = './data/', quality = 'auto', onEvent = () => {}, onStatus = () => {} }) {
    this.canvas = canvas;
    this.overlay = overlay;
    this.dataUrl = dataUrl.endsWith('/') ? dataUrl : dataUrl + '/';
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.reducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    this.qualityName = quality;
    this.quality = QUALITY.medium;
    this.chunks = new Map();          // key -> { state, group, meshes[] }
    this.buildQueue = [];
    this.inflight = 0;
    this.labelsOn = true;
    this.lampsOn = true;
    this.dirty = true;
    this.state = {};
    this.routeObjs = [];
    this.markers = new Map();         // id -> { el, pos:Vector3, kind }
    this.hazardGroup = new THREE.Group();
    this.routeGroup = new THREE.Group();
    this.riskGroup = new THREE.Group();
    this.beaconGroup = new THREE.Group();
    this.fly = null;
    this.pickMode = null;
    this._v = new THREE.Vector3();
    this._v2 = new THREE.Vector3();
    this._lastLabelUpdate = 0;
    this._lastChunkUpdate = 0;
    this._moved = true;
    this.forceRender = false;         // benchmark: draw every frame
    this.workers = [];
    this.jobs = new Map();
    this._jobSeq = 0;
    this.stats = { tileMs: [], fetchMs: [], attachMs: [], renderTimes: [], triangles: 0, drawCalls: 0, workers: 0, t0: performance.now() };
  }

  // ---- coordinates --------------------------------------------------------
  toLocal(lat, lon) {
    const m = this.manifest;
    return { x: (lon - m.origin.lon) * m.m_per_deg.lon, y: (lat - m.origin.lat) * m.m_per_deg.lat };
  }
  toLatLon(x, y) {
    const m = this.manifest;
    return { lat: m.origin.lat + y / m.m_per_deg.lat, lon: m.origin.lon + x / m.m_per_deg.lon };
  }
  world(lat, lon, h = 0) {
    const p = this.toLocal(lat, lon);
    return new THREE.Vector3(p.x, h, -p.y);
  }
  inExtent(lat, lon) {
    const p = this.toLocal(lat, lon);
    const e = this.manifest.extent;
    return p.x >= e[0] && p.x <= e[2] && p.y >= e[1] && p.y <= e[3];
  }

  // ---- setup --------------------------------------------------------------
  async init() {
    this.onStatus('Reading the map index', 0.05);
    this.manifest = await (await fetch(this.dataUrl + 'manifest.json')).json();
    const m = this.manifest;
    this.chunkIndex = new Map(m.chunks.map((c) => [`${c.cx}_${c.cy}`, c]));

    const renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: false, powerPreference: 'high-performance' });
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.setClearColor(col(PALETTE.horizon), 1);
    this.renderer = renderer;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(38, 1, 5, 200000);

    this.shared = {
      uFogColor: { value: col(PALETTE.horizon) },
      uFogRange: { value: 6000 },
      uDetail: { value: new THREE.Vector3(0, 0, 1) },
      uTime: { value: 0 },
    };

    this._buildSky();
    this._buildGround();
    this._buildMaterials();
    this.scene.add(this.riskGroup, this.hazardGroup, this.beaconGroup, this.routeGroup);

    const controls = new MapControls(this.camera, this.canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.screenSpacePanning = false;
    controls.zoomToCursor = true;
    controls.minDistance = 140;
    controls.maxDistance = 70000;
    controls.maxPolarAngle = THREE.MathUtils.degToRad(80);
    controls.rotateSpeed = 0.55;
    controls.zoomSpeed = 1.1;
    controls.addEventListener('change', () => { this._moved = true; this.dirty = true; });
    controls.addEventListener('start', () => { this.fly = null; this.onEvent({ type: 'interact' }); });
    this.controls = controls;

    this.setQuality(this.qualityName);
    this._bindPointer();
    this._bindKeys();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.canvas.parentElement || this.canvas);
    this.resize();

    const start = m.presets[0] || { x: 0, y: 0, d: 2000, h: 0, p: 55 };
    this.setView({ x: start.x, y: start.y, distance: start.d, heading: start.h, pitch: start.p });

    this._startWorkers();
    renderer.info.autoReset = false;
    // Buildings start loading straight away. Lakes, parks and the far-view
    // picture arrive in the background; a small far-view picture comes first
    // and the sharper one is fetched once the view around the camera is built.
    fetchBinary(this.dataUrl + m.base.file, m.tile_encoding).then((buf) => { this._buildBase(buf); this.dirty = true; })
      .catch((err) => console.warn('lakes and parks failed to load', err));
    this._loadFabric(m.fabric.lo || m.fabric.file, 1);
    this.onStatus('Raising buildings from their footprints', 0.3);
    this._loop = this._loop.bind(this);
    this._lastT = performance.now();
    requestAnimationFrame(this._loop);

    // resolve once the first ring of tiles around the camera is built
    await new Promise((resolve) => {
      const t0 = performance.now();
      const check = () => {
        const wanted = [...this.chunks.values()];
        const done = wanted.filter((c) => c.state === 'ready' || c.state === 'error').length;
        this.onStatus('Raising buildings from their footprints', 0.3 + 0.7 * (wanted.length ? done / wanted.length : 0));
        if ((wanted.length && done >= Math.min(wanted.length, 3)) || performance.now() - t0 > 12000) resolve();
        else setTimeout(check, 120);
      };
      check();
    });
    this.stats.readyAt = performance.now();
    this.onEvent({ type: 'ready' });
  }

  _buildSky() {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
    this.skyMat = new THREE.ShaderMaterial({
      depthTest: false, depthWrite: false,
      uniforms: { uHorizon: { value: 0.5 }, uA: { value: col(PALETTE.horizon) }, uB: { value: col(PALETTE.zenith) } },
      vertexShader: `varying vec2 vP; void main(){ vP = position.xy * 0.5 + 0.5; gl_Position = vec4(position.xy, 1.0, 1.0); }`,
      fragmentShader: `uniform float uHorizon; uniform vec3 uA; uniform vec3 uB; varying vec2 vP;
        void main(){ float t = smoothstep(0.0, 0.55, vP.y - uHorizon); gl_FragColor = vec4(mix(uA, uB, t), 1.0); }`,
    });
    const sky = new THREE.Mesh(geo, this.skyMat);
    sky.frustumCulled = false;
    sky.renderOrder = -10;
    this.scene.add(sky);
  }

  _buildGround() {
    const [x0, y0, x1, y1] = this.manifest.extent;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([x0, 0, -y0, x1, 0, -y0, x1, 0, -y1, x0, 0, -y1]), 3));
    geo.setAttribute('uv', new THREE.BufferAttribute(new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]), 2));
    geo.setIndex([0, 1, 2, 0, 2, 3]);
    this.groundMat = new THREE.ShaderMaterial({
      uniforms: {
        ...this.shared,
        uFabric: { value: null }, uHasFabric: { value: 0 },
        uGround: { value: col(PALETTE.duskNavy) }, uTint: { value: col(PALETTE.fabric) },
      },
      vertexShader: `varying vec2 vUv; varying vec3 vW;
        void main(){ vec4 wp = modelMatrix * vec4(position, 1.0); vW = wp.xyz; vUv = uv; gl_Position = projectionMatrix * viewMatrix * wp; }`,
      fragmentShader: `${FOG_GLSL}
        uniform sampler2D uFabric; uniform float uHasFabric; uniform vec3 uGround; uniform vec3 uTint; uniform vec3 uDetail;
        varying vec2 vUv; varying vec3 vW;
        void main(){
          float l = uHasFabric > 0.5 ? texture2D(uFabric, vUv).r : 0.0;
          float r = max(uDetail.z, 1.0);
          float far = smoothstep(r * 0.80, r, distance(vW.xz, uDetail.xy));
          vec3 c = mix(uGround, uTint, l * far);
          gl_FragColor = vec4(applyFog(c, vW), 1.0);
        }`,
    });
    const ground = new THREE.Mesh(geo, this.groundMat);
    ground.renderOrder = 0;
    ground.frustumCulled = false;
    this.ground = ground;
    this.scene.add(ground);

    // plain apron so the model does not end at a hard edge
    const apronMat = new THREE.ShaderMaterial({
      depthTest: false, depthWrite: false,
      uniforms: { ...this.shared, uGround: { value: col(PALETTE.duskNavy) } },
      vertexShader: `varying vec3 vW; void main(){ vec4 wp = modelMatrix * vec4(position, 1.0); vW = wp.xyz; gl_Position = projectionMatrix * viewMatrix * wp; }`,
      fragmentShader: `${FOG_GLSL} uniform vec3 uGround; varying vec3 vW; void main(){ gl_FragColor = vec4(applyFog(uGround, vW), 1.0); }`,
    });
    const apron = new THREE.Mesh(new THREE.PlaneGeometry(600000, 600000).rotateX(-Math.PI / 2), apronMat);
    apron.position.y = -1.5;
    apron.renderOrder = -1;
    this.scene.add(apron);
  }

  _buildMaterials() {
    const flat = (extraUniforms, vertex, fragment) => new THREE.ShaderMaterial({
      depthTest: false, depthWrite: false,
      uniforms: { ...this.shared, ...extraUniforms },
      vertexShader: vertex, fragmentShader: fragment,
    });
    this.baseMat = flat(
      { uWater: { value: col(PALETTE.water) }, uGreen: { value: col(PALETTE.green) } },
      `attribute float kind; varying float vKind; varying vec3 vW;
       void main(){ vKind = kind; vec4 wp = modelMatrix * vec4(position, 1.0); vW = wp.xyz; gl_Position = projectionMatrix * viewMatrix * wp; }`,
      `${FOG_GLSL} uniform vec3 uWater; uniform vec3 uGreen; varying float vKind; varying vec3 vW;
       void main(){ gl_FragColor = vec4(applyFog(vKind < 0.5 ? uWater : uGreen, vW), 1.0); }`,
    );
    this.roadMat = flat(
      { uRoad: { value: ROAD_COLORS.map(col) } },
      `attribute float cls; varying float vCls; varying vec3 vW;
       void main(){ vCls = cls; vec4 wp = modelMatrix * vec4(position, 1.0); vW = wp.xyz; gl_Position = projectionMatrix * viewMatrix * wp; }`,
      `${FOG_GLSL} uniform vec3 uRoad[11]; varying float vCls; varying vec3 vW;
       void main(){ gl_FragColor = vec4(applyFog(uRoad[int(vCls + 0.5)], vW), 1.0); }`,
    );
    this.buildingMat = new THREE.ShaderMaterial({
      uniforms: {
        ...this.shared,
        uRoof: { value: col(PALETTE.roof) }, uWallLit: { value: col(PALETTE.wallLit) },
        uWallDark: { value: col(PALETTE.wallDark) }, uBase: { value: col(PALETTE.wallBase) },
        uLight: { value: new THREE.Vector3(-0.55, 0.45, -0.7).normalize() },
      },
      vertexShader: `attribute float tone; uniform vec3 uDetail; varying vec3 vW; varying float vTone;
        void main(){
          vec4 wp = modelMatrix * vec4(position, 1.0);
          float r = max(uDetail.z, 1.0);
          wp.y *= 1.0 - smoothstep(r * 0.80, r, distance(wp.xz, uDetail.xy));
          vW = wp.xyz; vTone = tone;
          gl_Position = projectionMatrix * viewMatrix * wp;
        }`,
      fragmentShader: `${FOG_GLSL}
        uniform vec3 uRoof; uniform vec3 uWallLit; uniform vec3 uWallDark; uniform vec3 uBase; uniform vec3 uLight;
        varying vec3 vW; varying float vTone;
        void main(){
          vec3 nc = cross(dFdx(vW), dFdy(vW));
          float nl = length(nc);
          vec3 n = nl > 1e-12 ? nc / nl : vec3(0.0, 1.0, 0.0);
          float roof = smoothstep(0.55, 0.9, n.y);
          float lit = clamp(dot(n, uLight) * 0.5 + 0.5, 0.0, 1.0);
          vec3 wall = mix(uWallDark, uWallLit, lit);
          wall = mix(uBase, wall, 0.4 + 0.6 * smoothstep(0.0, 16.0, vW.y));
          vec3 c = mix(wall, uRoof, roof) * (0.86 + 0.28 * vTone);
          gl_FragColor = vec4(applyFog(c, vW), 1.0);
        }`,
    });
    this.lampMat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { ...this.shared, uColor: { value: col(PALETTE.lamp) }, uPx: { value: 600 } },
      vertexShader: `uniform float uPx; uniform float uFogRange; varying float vFade;
        void main(){
          vec4 wp = modelMatrix * vec4(position, 1.0);
          vec4 mv = viewMatrix * wp;
          gl_Position = projectionMatrix * mv;
          gl_PointSize = clamp(9.0 * uPx / max(-mv.z, 1.0), 2.0, 26.0);
          float d = -mv.z / uFogRange;
          vFade = exp(-d * d * 1.6);
        }`,
      fragmentShader: `uniform vec3 uColor; varying float vFade;
        void main(){
          float r = length(gl_PointCoord - 0.5) * 2.0;
          float a = 1.0 - smoothstep(0.0, 1.0, r);
          a = a * a * 0.55 + (1.0 - smoothstep(0.0, 0.38, r)) * 0.9;
          gl_FragColor = vec4(uColor * a * vFade, 1.0);
        }`,
    });
  }

  _buildBase(buf) {
    const dv = new DataView(buf);
    const nPoly = dv.getUint32(4, true), nRings = dv.getUint32(8, true), nPts = dv.getUint32(12, true);
    let off = 16;
    const kinds = new Uint8Array(buf, off, nPoly); off += pad4(nPoly);
    const ringsPer = new Uint16Array(buf, off, nPoly); off += pad4(nPoly * 2);
    const ptsPer = new Uint32Array(buf, off, nRings); off += nRings * 4;
    const xy = new Int16Array(buf, off, nPts * 2);
    const q = this.manifest.base_q;
    const pos = new Float32Array(nPts * 3);
    const kind = new Uint8Array(nPts);
    const idx = [];
    let ring = 0, p = 0;
    const flat = [];
    for (let i = 0; i < nPoly; i++) {
      flat.length = 0;
      const holes = [];
      const first = p;
      for (let r = 0; r < ringsPer[i]; r++, ring++) {
        if (r > 0) holes.push(flat.length / 2);
        let x = 0, y = 0;
        for (let k = 0; k < ptsPer[ring]; k++, p++) {
          x += xy[p * 2]; y += xy[p * 2 + 1];
          flat.push(x, y);
          pos[p * 3] = x * q; pos[p * 3 + 1] = 0; pos[p * 3 + 2] = -y * q;
          kind[p] = kinds[i];
        }
      }
      const tris = earcut(flat, holes.length ? holes : null, 2);
      for (let t = 0; t < tris.length; t++) idx.push(first + tris[t]);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('kind', new THREE.BufferAttribute(kind, 1));
    geo.setIndex(new THREE.BufferAttribute(new Uint32Array(idx), 1));
    const mat = this.baseMat;
    mat.side = THREE.DoubleSide;
    const mesh = new THREE.Mesh(geo, mat);
    mesh.frustumCulled = false;
    mesh.renderOrder = 1;
    this.scene.add(mesh);
  }

  _loadFabric(file, level) {
    return new THREE.TextureLoader().loadAsync(this.dataUrl + file).then((tex) => {
      if ((this._fabricLevel || 0) > level) { tex.dispose(); return; }   // a sharper one already arrived
      tex.colorSpace = THREE.NoColorSpace;
      tex.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
      tex.generateMipmaps = true;
      tex.minFilter = THREE.LinearMipmapLinearFilter;
      const old = this.groundMat.uniforms.uFabric.value;
      this.groundMat.uniforms.uFabric.value = tex;
      this.groundMat.uniforms.uHasFabric.value = 1;
      this._fabricLevel = level;
      if (old) old.dispose();
      this.dirty = true;
    }).catch((err) => console.warn('far-view picture failed to load', file, err));
  }

  // ---- worker pool ----------------------------------------------------------
  // Tiles are downloaded, unpacked and meshed on background threads so the page
  // never freezes while new areas stream in. If workers cannot start (old
  // browser, strict host), the same code runs on the main thread instead.
  _startWorkers() {
    const n = clamp((navigator.hardwareConcurrency || 4) - 1, 1, 4);
    try {
      for (let i = 0; i < n; i++) {
        const w = new Worker(new URL('./tile-worker.js', import.meta.url), { type: 'module' });
        w.pending = 0;
        w.onmessage = (e) => this._onWorkerMessage(w, e.data);
        w.onerror = (e) => { e.preventDefault?.(); this._stopWorkers(`tile worker failed: ${e.message || 'could not start'}`); };
        this.workers.push(w);
      }
    } catch (err) {
      this._stopWorkers(`tile workers unavailable: ${err.message}`);
    }
    this.stats.workers = this.workers.length;
  }

  _stopWorkers(reason) {
    if (!this.workers.length) return;
    console.warn(`${reason}. Processing map tiles on the main thread.`);
    for (const w of this.workers) w.terminate();
    this.workers = [];
    this.stats.workers = 0;
    const jobs = [...this.jobs.values()];
    this.jobs.clear();
    for (const job of jobs) { this.inflight--; if (this.chunks.get(job.key) === job.ch) this._fetchOnMain(job.key, job.ch, job.url); }
  }

  _onWorkerMessage(w, msg) {
    const job = this.jobs.get(msg.id);
    if (!job) return;
    this.jobs.delete(msg.id);
    w.pending--;
    this.inflight--;
    if (this.chunks.get(job.key) !== job.ch) return;      // dropped while loading
    if (!msg.ok) { job.ch.state = 'error'; console.warn('map tile failed', job.key, msg.error); return; }
    this._record(this.stats.tileMs, msg.out.ms);
    this._record(this.stats.fetchMs, msg.out.fetchMs);
    job.ch.built = msg.out;
    job.ch.state = 'parsed';
    this.buildQueue.push(job.key);
    this.dirty = true;
  }

  _fetchOnMain(key, ch, url) {
    this.inflight++;
    fetchBinary(url, this.manifest.tile_encoding).then((buf) => {
      this.inflight--;
      if (this.chunks.get(key) !== ch) return;
      ch.buf = buf; ch.state = 'parsed';
      this.buildQueue.push(key);
      this.dirty = true;
    }).catch((err) => { this.inflight--; ch.state = 'error'; console.warn('map tile failed', key, err); });
  }

  workerBusy() { return this.jobs.size; }

  _record(arr, v, max = 400) { arr.push(v); if (arr.length > max) arr.shift(); }

  gpuName() {
    try {
      const gl = this.renderer.getContext();
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
    } catch { return 'unknown'; }
  }

  // ---- quality / size -----------------------------------------------------
  setQuality(name) {
    this.qualityName = name;
    let key = name;
    if (name === 'auto' || !QUALITY[name]) {
      const small = Math.min(window.innerWidth, window.innerHeight) < 600 || /Mobi|Android/i.test(navigator.userAgent);
      key = small ? 'low' : (navigator.hardwareConcurrency || 8) <= 4 ? 'low' : 'medium';
    }
    this.qualityKey = key;
    this.quality = QUALITY[key];
    this._buildComposer();
    this.resize();
    this._moved = true;
    this.dirty = true;
    this.onEvent({ type: 'quality', quality: key, auto: name === 'auto' });
  }

  _buildComposer() {
    const q = this.quality;
    if (this.composer) { this.composer.dispose(); this.composer.renderTarget1.dispose(); this.composer.renderTarget2.dispose(); }
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    const target = new THREE.WebGLRenderTarget(Math.max(1, size.x), Math.max(1, size.y), { type: THREE.HalfFloatType, samples: q.msaa });
    const composer = new EffectComposer(this.renderer, target);
    composer.addPass(new RenderPass(this.scene, this.camera));
    if (q.bloom) {
      composer.addPass(new ShaderPass(SanitizeShader));
      this.bloom = new UnrealBloomPass(new THREE.Vector2(size.x, size.y), 0.62, 0.55, 0.2);
      composer.addPass(this.bloom);
    } else this.bloom = null;
    composer.addPass(new OutputPass());
    this.composer = composer;
  }

  resize() {
    const host = this.canvas.parentElement || this.canvas;
    const w = Math.max(1, host.clientWidth), h = Math.max(1, host.clientHeight);
    const pr = Math.min(window.devicePixelRatio || 1, this.quality.pixelRatio);
    this.width = w; this.height = h;
    this.renderer.setPixelRatio(pr);
    this.renderer.setSize(w, h, false);
    if (this.composer) { this.composer.setPixelRatio(pr); this.composer.setSize(w, h); }
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.lampMat.uniforms.uPx.value = (h * pr) / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)));
    for (const r of this.routeObjs) for (const l of r.lines) l.material.resolution.set(w * pr, h * pr);
    this._moved = true;
    this.dirty = true;
  }

  // ---- camera -------------------------------------------------------------
  getView() {
    const t = this.controls.target;
    const off = this._v.copy(this.camera.position).sub(t);
    const distance = off.length();
    const pitch = THREE.MathUtils.radToDeg(Math.acos(clamp(off.y / distance, -1, 1)));
    const heading = (THREE.MathUtils.radToDeg(-Math.atan2(off.x, off.z)) + 360) % 360;
    return { x: t.x, y: -t.z, distance, heading, pitch, ...this.toLatLon(t.x, -t.z), eye: this.camera.position.y };
  }

  setView({ x, y, lat, lon, distance, heading, pitch }) {
    const cur = this.controls ? this.getView() : { x: 0, y: 0, distance: 2000, heading: 0, pitch: 55 };
    if (lat != null && lon != null) ({ x, y } = this.toLocal(lat, lon));
    x = x ?? cur.x; y = y ?? cur.y;
    const e = this.manifest.extent;
    x = clamp(x, e[0], e[2]); y = clamp(y, e[1], e[3]);
    distance = clamp(distance ?? cur.distance, this.controls.minDistance, this.controls.maxDistance);
    heading = heading ?? cur.heading;
    pitch = clamp(pitch ?? cur.pitch, 0.5, 80);
    const phi = THREE.MathUtils.degToRad(pitch), theta = THREE.MathUtils.degToRad(-heading);
    this.controls.target.set(x, 0, -y);
    this.camera.position.set(
      x + distance * Math.sin(phi) * Math.sin(theta),
      distance * Math.cos(phi),
      -y + distance * Math.sin(phi) * Math.cos(theta),
    );
    this.camera.lookAt(this.controls.target);
    this._moved = true;
    this.dirty = true;
  }

  flyTo(view, ms = 1700) {
    const from = this.getView();
    const to = { ...from, ...view };
    if (view.lat != null && view.lon != null) Object.assign(to, this.toLocal(view.lat, view.lon));
    let dh = ((to.heading - from.heading + 540) % 360) - 180;
    to.heading = from.heading + dh;
    if (this.reducedMotion || ms <= 0) { this.setView(to); return; }
    this.fly = { from, to, t0: performance.now(), ms };
    this.dirty = true;
  }

  // Frame a set of points inside the part of the view the HUD leaves free.
  fitPoints(points, { pitch = 58, pad = 1.12 } = {}) {
    if (!points.length) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const p of points) { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); }
    const r = Math.max(110, Math.hypot(x1 - x0, y1 - y0) / 2);
    const ins = this.insets || { top: 0, right: 0, bottom: 0, left: 0 };
    const w = this.width, h = this.height;
    let fw = w - ins.left - ins.right, fh = h - ins.top - ins.bottom, left = ins.left, right = ins.right;
    if (fw < 220) { fw = w; left = right = 0; }
    fh = Math.max(fh, 160);
    const tan = Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    const distance = clamp((2 * r * pad * h) / (2 * tan * Math.min(fw, fh)), 380, 60000);
    const ppm = h / (2 * distance * tan);                 // pixels per metre at the target
    const heading = this.getView().heading, hr = THREE.MathUtils.degToRad(heading), pr = THREE.MathUtils.degToRad(pitch);
    const dx = (left - right) / 2, dy = (ins.top - ins.bottom) / 2;
    const east = Math.cos(hr) * (-dx / ppm) + Math.sin(hr) * (dy / (ppm * Math.cos(pr)));
    const north = -Math.sin(hr) * (-dx / ppm) + Math.cos(hr) * (dy / (ppm * Math.cos(pr)));
    this.flyTo({ x: (x0 + x1) / 2 + east, y: (y0 + y1) / 2 + north, distance, pitch });
  }

  // ---- data tiles ---------------------------------------------------------
  _updateChunks() {
    const m = this.manifest, v = this.getView();
    const q = this.quality;
    let R = v.distance > 14000 ? 0 : clamp(v.distance * 2.3, 1500, q.radius);
    // look-ahead: when tilted, most of what is visible lies beyond the target
    const fwd = this._v.set(-Math.sin(THREE.MathUtils.degToRad(-v.heading)), 0, -Math.cos(THREE.MathUtils.degToRad(-v.heading)));
    const ahead = R * 0.32 * Math.sin(THREE.MathUtils.degToRad(v.pitch));
    const cx = v.x + fwd.x * ahead, cy = v.y - fwd.z * ahead;
    this.shared.uDetail.value.set(cx, -cy, R);
    this.detail = { x: cx, y: cy, r: R };
    this.shared.uFogRange.value = Math.max(2600, v.distance * 3.4);

    const want = new Set();
    if (R > 0) {
      const c = m.chunk;
      const ix0 = Math.floor((cx - R - m.extent[0]) / c), ix1 = Math.floor((cx + R - m.extent[0]) / c);
      const iy0 = Math.floor((cy - R - m.extent[1]) / c), iy1 = Math.floor((cy + R - m.extent[1]) / c);
      for (let iy = iy0; iy <= iy1; iy++) for (let ix = ix0; ix <= ix1; ix++) {
        const key = `${ix}_${iy}`;
        if (!this.chunkIndex.has(key)) continue;
        const bx = m.extent[0] + ix * c, by = m.extent[1] + iy * c;
        const dx = Math.max(bx - cx, 0, cx - (bx + c)), dy = Math.max(by - cy, 0, cy - (by + c));
        if (dx * dx + dy * dy <= R * R) want.add(key);
      }
    }
    const keep = R + 2400;
    for (const [key, ch] of this.chunks) {
      if (want.has(key)) continue;
      const [ix, iy] = key.split('_').map(Number);
      const mx = m.extent[0] + (ix + 0.5) * m.chunk, my = m.extent[1] + (iy + 0.5) * m.chunk;
      if (R === 0 || Math.hypot(mx - cx, my - cy) > keep + m.chunk) this._dropChunk(key, ch);
    }
    const missing = [...want].filter((k) => !this.chunks.has(k)).map((k) => {
      const [ix, iy] = k.split('_').map(Number);
      const mx = m.extent[0] + (ix + 0.5) * m.chunk, my = m.extent[1] + (iy + 0.5) * m.chunk;
      return { k, ix, iy, d: Math.hypot(mx - v.x, my - v.y) };
    }).sort((a, b) => a.d - b.d);
    const maxInflight = Math.max(4, this.workers.length * 2);
    for (const it of missing) {
      if (this.inflight >= maxInflight) break;
      this._loadChunk(it.k, it.ix, it.iy);
    }
    this.pendingTiles = missing.length;
  }

  _loadChunk(key, ix, iy) {
    const ch = { state: 'loading', group: null, ix, iy };
    this.chunks.set(key, ch);
    const url = new URL(`c_${ix}_${iy}.${this.manifest.tile_ext || 'bin'}`, new URL(this.dataUrl, location.href)).href;
    if (!this.workers.length) { this._fetchOnMain(key, ch, url); return; }
    const w = this.workers.reduce((a, b) => (b.pending < a.pending ? b : a));
    const id = ++this._jobSeq;
    this.jobs.set(id, { key, ch, url });
    w.pending++;
    this.inflight++;
    const m = this.manifest;
    w.postMessage({ id, url, encoding: m.tile_encoding, opts: { chunk: m.chunk, sub: m.sub, q: m.q } });
  }

  _dropChunk(key, ch) {
    if (ch.group) {
      this.scene.remove(ch.group);
      ch.group.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
    }
    this.chunks.delete(key);
    this.dirty = true;
  }

  // Turns a processed tile into meshes. Cheap: the arrays were built elsewhere.
  _buildChunk(key) {
    const ch = this.chunks.get(key);
    if (!ch || ch.state !== 'parsed') return;
    const m = this.manifest, Q = m.q;
    const t0 = performance.now();
    if (!ch.built) {                                   // main-thread fallback
      const out = buildTile(ch.buf, { chunk: m.chunk, sub: m.sub, q: Q });
      this._record(this.stats.tileMs, out.ms);
      ch.built = out;
      ch.buf = null;
    }
    const t = ch.built;
    const group = new THREE.Group();
    const ox = m.extent[0] + (ch.ix + 0.5) * m.chunk, oy = m.extent[1] + (ch.iy + 0.5) * m.chunk;
    group.position.set(ox, 0, -oy);
    group.updateMatrix();
    group.matrixAutoUpdate = false;

    // buildings: one mesh per 500 m block, largest buildings first
    ch.buildings = [];
    for (const blk of t.blocks) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(blk.pos, 3));
      geo.setAttribute('tone', new THREE.BufferAttribute(blk.tone, 1, true));
      geo.setIndex(new THREE.BufferAttribute(blk.idx, 1));
      geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(blk.cx / Q, 0, -blk.cy / Q), 640 / Q);
      const mesh = new THREE.Mesh(geo, this.buildingMat);
      mesh.scale.setScalar(Q);
      mesh.renderOrder = 10;
      mesh.updateMatrix();
      mesh.matrixAutoUpdate = false;
      mesh.userData = { x: ox + blk.cx, y: oy + blk.cy, lod: blk.lod };
      group.add(mesh);
      ch.buildings.push(mesh);
    }

    // roads: ribbons, widest classes drawn last
    if (t.roads) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(t.roads.pos, 3));
      geo.setAttribute('cls', new THREE.BufferAttribute(t.roads.cls, 1));
      geo.setIndex(new THREE.BufferAttribute(t.roads.idx, 1));
      geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), m.chunk * 1.6);
      this.roadMat.side = THREE.DoubleSide;
      const mesh = new THREE.Mesh(geo, this.roadMat);
      mesh.renderOrder = 3;
      group.add(mesh);
    }

    // mapped street lamps
    if (t.lamps) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(t.lamps, 3));
      geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), m.chunk * 1.2);
      const points = new THREE.Points(geo, this.lampMat);
      points.renderOrder = 30;
      points.visible = this.lampsOn;
      points.userData.lamps = true;
      group.add(points);
    }

    ch.built = null;
    ch.group = group;
    ch.state = 'ready';
    this.scene.add(group);
    if (!this.stats.firstTileAt) this.stats.firstTileAt = performance.now();
    this._record(this.stats.attachMs, performance.now() - t0);
    this._lodDirty = true;
    this.dirty = true;
  }

  // Per 500 m block: hide it when it lies wholly outside the detail radius
  // (its buildings are flattened into the ground picture there anyway), and
  // otherwise draw only the buildings big enough to see from this distance.
  _updateLod() {
    const d = this.detail, cam = this.camera.position;
    if (!d) return;
    const focal = this.height / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)));
    const px = this.quality.lodPx || 2, L = LOD_SIZES.length;
    for (const ch of this.chunks.values()) {
      if (!ch.buildings) continue;
      for (const mesh of ch.buildings) {
        const u = mesh.userData;
        if (Math.hypot(u.x - d.x, u.y - d.y) - 354 > d.r) { mesh.visible = false; continue; }
        mesh.visible = true;
        const dist = Math.max(0, Math.hypot(u.x - cam.x, cam.y, -u.y - cam.z) - 360);
        const minSize = (dist * px) / focal;
        let j = 0;
        while (j + 1 < L && LOD_SIZES[j + 1] <= minSize) j++;
        mesh.geometry.setDrawRange(0, u.lod[j]);
      }
    }
  }

  setLamps(on) {
    this.lampsOn = on;
    for (const ch of this.chunks.values()) if (ch.group) ch.group.traverse((o) => { if (o.userData.lamps) o.visible = on; });
    this.dirty = true;
  }

  // ---- host state ---------------------------------------------------------
  setState(next) {
    const prev = this.state;
    this.state = { ...prev, ...next };
    const s = this.state;
    const changed = (k) => k in next && JSON.stringify(next[k]) !== JSON.stringify(prev[k]);
    if (changed('routes') || changed('selected_route')) this._syncRoutes();
    if (changed('start') || changed('destination')) this._syncEndpoints();
    if (changed('hazards')) this._syncHazards();
    if (changed('risk_cells')) this._syncRisk();
    if (changed('user_location')) this._syncUser();
    if ('pick_mode' in next) this.setPickMode(s.pick_mode || null);
    const fitKey = JSON.stringify([s.routes?.map((r) => [r.coords?.[0], r.coords?.length, r.coords?.[r.coords.length - 1]]), s.start, s.destination]);
    if ((changed('routes') || changed('start') || changed('destination')) && s.fit !== 'none' && fitKey !== this._fitKey) {
      this._fitKey = fitKey;
      this.fitToContent();
    }
    this.dirty = true;
  }

  fitToContent() {
    const s = this.state, pts = [];
    for (const r of s.routes || []) for (const c of r.coords || []) pts.push(this.toLocal(c[0], c[1]));
    for (const k of ['start', 'destination']) if (s[k]) pts.push(this.toLocal(s[k].lat, s[k].lon));
    if (pts.length === 1) this.flyTo({ x: pts[0].x, y: pts[0].y, distance: Math.min(this.getView().distance, 1400) });
    else if (pts.length > 1) this.fitPoints(pts);
  }

  routeStyle(route, index, total) {
    const role = route.role || (index === 0 ? 'lower_risk' : 'shortest');
    const coral = role === 'shortest' && total > 1;
    return {
      role,
      color: route.color || (coral ? PALETTE.cautionCoral : PALETTE.safeTeal),
      dashed: coral,
      width: coral ? 4.5 : 5.5,
    };
  }

  _syncRoutes() {
    for (const r of this.routeObjs) {
      for (const l of r.lines) { this.routeGroup.remove(l); l.material.dispose(); }
      r.geometry.dispose();
      if (r.beads) { this.routeGroup.remove(r.beads); r.beads.geometry.dispose(); r.beads.material.dispose(); }
    }
    this.routeObjs = [];
    const routes = (this.state.routes || []).filter((r) => r.coords && r.coords.length > 1);
    const pr = this.renderer.getPixelRatio();
    // draw coral first so the teal line stays on top where they overlap
    const ordered = routes.map((r, i) => ({ r, i, st: this.routeStyle(r, i, routes.length) })).sort((a, b) => (a.st.dashed ? 0 : 1) - (b.st.dashed ? 0 : 1));
    for (const { r, st } of ordered) {
      const flat = [];
      const pts = [];
      for (const c of r.coords) {
        const p = this.toLocal(c[0], c[1]);
        if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
        const last = pts[pts.length - 1];
        if (last && Math.abs(last.x - p.x) < 0.05 && Math.abs(last.y - p.y) < 0.05) continue;
        flat.push(p.x, 2.2, -p.y); pts.push(p);
      }
      if (pts.length < 2) continue;
      const geometry = new LineGeometry();
      geometry.setPositions(flat);
      const selected = this.state.selected_route == null || this.state.selected_route === r.id;
      const mk = (opts) => {
        const mat = new LineMaterial({
          color: col(st.color), linewidth: st.width * (selected ? 1 : 0.7), worldUnits: false,
          dashed: st.dashed, dashSize: 16, gapSize: 10, dashScale: 1, ...opts,
        });
        mat.resolution.set(this.width * pr, this.height * pr);
        const line = new Line2(geometry, mat);
        line.computeLineDistances();
        line.frustumCulled = false;
        return line;
      };
      const solid = mk({ transparent: !selected, opacity: selected ? 1 : 0.55, depthTest: true });
      solid.renderOrder = 40;
      const ghost = mk({ transparent: true, opacity: selected ? 0.3 : 0.14, depthTest: false, depthWrite: false });
      ghost.renderOrder = 39;
      this.routeGroup.add(ghost, solid);
      const obj = { id: r.id, lines: [solid, ghost], geometry, style: st, pts };
      if (!st.dashed) obj.beads = this._makeBeads(pts, st.color);
      this.routeObjs.push(obj);
    }
  }

  // small pulses that travel along the preferred route to show direction
  _makeBeads(pts, color) {
    const spacing = 14, pos = [], along = [];
    let carry = 0, total = 0;
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i], seg = Math.hypot(b.x - a.x, b.y - a.y);
      for (let d = carry; d < seg; d += spacing) {
        const t = d / seg;
        pos.push(a.x + (b.x - a.x) * t, 3.2, -(a.y + (b.y - a.y) * t));
        along.push(total + d);
      }
      carry = (carry - seg) % spacing;
      if (carry < 0) carry += spacing;
      total += seg;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pos), 3));
    geo.setAttribute('along', new THREE.BufferAttribute(new Float32Array(along), 1));
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uTime: this.shared.uTime, uColor: { value: col(color) }, uPx: this.lampMat.uniforms.uPx, uStill: { value: this.reducedMotion ? 1 : 0 } },
      vertexShader: `attribute float along; uniform float uTime; uniform float uPx; uniform float uStill; varying float vA;
        void main(){
          vec4 mv = viewMatrix * modelMatrix * vec4(position, 1.0);
          gl_Position = projectionMatrix * mv;
          float ph = fract(along / 168.0 - uTime * 0.22);
          vA = uStill > 0.5 ? 0.35 : smoothstep(0.72, 1.0, ph) * (1.0 - smoothstep(0.985, 1.0, ph)) + 0.12;
          gl_PointSize = clamp(7.0 * uPx / max(-mv.z, 1.0), 2.0, 15.0) * (0.6 + 0.4 * vA);
        }`,
      fragmentShader: `uniform vec3 uColor; varying float vA;
        void main(){ float r = length(gl_PointCoord - 0.5) * 2.0; float a = 1.0 - smoothstep(0.1, 1.0, r);
          gl_FragColor = vec4(mix(uColor, vec3(1.0), 0.45) * a * vA, 1.0); }`,
    });
    const points = new THREE.Points(geo, mat);
    points.frustumCulled = false;
    points.renderOrder = 41;
    this.routeGroup.add(points);
    return points;
  }

  _marker(id, kind, pos, html, title) {
    let m = this.markers.get(id);
    if (!m) {
      const el = document.createElement('div');
      el.className = `marker ${kind}`;
      this.overlay.appendChild(el);
      m = { el, kind };
      this.markers.set(id, m);
    }
    m.el.className = `marker ${kind}`;
    m.el.innerHTML = html;
    if (title) m.el.title = title; else m.el.removeAttribute('title');
    m.pos = pos;
    m.seen = true;
    return m;
  }

  _sweepMarkers(prefix) {
    for (const [id, m] of this.markers) {
      if (!id.startsWith(prefix)) continue;
      if (!m.seen) { m.el.remove(); this.markers.delete(id); }
      m.seen = false;
    }
  }

  _beam(pos, color, height = 70, radius = 2.2) {
    const g = new THREE.Group();
    const beamMat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      uniforms: { uColor: { value: col(color) } },
      vertexShader: `varying float vY; void main(){ vY = uv.y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `uniform vec3 uColor; varying float vY; void main(){ float a = pow(clamp(1.0 - vY, 0.0, 1.0), 2.2); gl_FragColor = vec4(uColor * a * 0.9, 1.0); }`,
    });
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(radius * 0.35, radius, height, 20, 1, true), beamMat);
    beam.position.y = height / 2;
    beam.renderOrder = 45;
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(radius * 3.2, radius * 4.4, 40).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: col(color), transparent: true, opacity: 0.9, depthTest: false, depthWrite: false }),
    );
    ring.position.y = 0.6;
    ring.renderOrder = 38;
    g.add(beam, ring);
    g.position.copy(pos);
    return g;
  }

  _syncEndpoints() {
    this.beaconGroup.clear();
    const s = this.state;
    const defs = [['start', 'A', 'Start'], ['destination', 'B', 'Destination']];
    for (const [key, letter, word] of defs) {
      const p = s[key];
      if (!p || p.lat == null) continue;
      const pos = this.world(p.lat, p.lon);
      this.beaconGroup.add(this._beam(pos, PALETTE.mist));
      const label = p.label ? `${word}: ${p.label}` : word;
      this._marker(`end:${key}`, `endpoint ${key}`, pos.clone().setY(74),
        `<span class="pin">${letter}</span><span class="txt">${escapeHtml(label)}</span>`);
    }
    this._sweepMarkers('end:');
  }

  _syncHazards() {
    this.hazardGroup.clear();
    const list = (this.state.hazards || []).filter((h) => h && h.status !== 'resolved');
    for (const h of list) {
      const lat = h.latitude ?? h.lat, lon = h.longitude ?? h.lon;
      if (lat == null || lon == null) continue;
      const confirmed = h.status === 'confirmed';
      const pos = this.world(lat, lon);
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(confirmed ? 0 : 9, 13, 40).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: col(PALETTE.cautionCoral), transparent: true, opacity: confirmed ? 0.55 : 0.8, depthTest: false, depthWrite: false }),
      );
      ring.position.copy(pos).setY(0.8);
      ring.renderOrder = 37;
      ring.userData.pulse = confirmed;
      this.hazardGroup.add(ring);
      const name = HAZARD_LABEL[h.hazard_type] || 'Hazard report';
      const state = confirmed ? 'Confirmed report' : 'Unverified report';
      this._marker(`haz:${h.id ?? `${lat},${lon}`}`, `hazard ${confirmed ? 'confirmed' : 'unverified'}`, pos.clone().setY(16),
        `${HAZARD_ICON[h.hazard_type] || HAZARD_ICON.other_infrastructure}<span class="txt">${escapeHtml(name)}<small>${state}</small></span>`,
        `${name} (${state.toLowerCase()})${h.description ? ': ' + h.description : ''}`).el.dataset.id = h.id ?? '';
    }
    this._sweepMarkers('haz:');
  }

  _syncRisk() {
    this.riskGroup.clear();
    const cells = this.state.risk_cells || [];
    if (!cells.length) return;
    // Translucent columns. They write depth so columns behind do not stack up into a white-out.
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: true,
      uniforms: { uColor: { value: col(PALETTE.cautionCoral) } },
      vertexShader: `attribute float value; varying float vY; varying float vV; varying vec3 vN;
        void main(){ vY = position.y + 0.5; vV = value; vN = normal;
          gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0); }`,
      fragmentShader: `uniform vec3 uColor; varying float vY; varying float vV; varying vec3 vN;
        void main(){
          float top = step(0.5, vN.y);
          float side = 0.16 + 0.42 * pow(clamp(vY, 0.0, 1.0), 1.6);
          float shade = 0.72 + 0.28 * abs(vN.x);
          float a = mix(side, 0.78, top) * (0.55 + 0.45 * vV);
          gl_FragColor = vec4(uColor * mix(shade, 1.0, top), a);
        }`,
    });
    const geo = new THREE.BoxGeometry(1, 1, 1);
    const mesh = new THREE.InstancedMesh(geo, mat, cells.length);
    const values = new Float32Array(cells.length);
    const mtx = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3();
    cells.forEach((c, i) => {
      const v = clamp(Number(c.value) || 0, 0, 1), size = c.size_m || 60, h = 12 + v * 260;
      const p = this.world(c.lat, c.lon, h / 2);
      sc.set(size, h, size);
      mesh.setMatrixAt(i, mtx.compose(p, q, sc));
      values[i] = v;
    });
    geo.setAttribute('value', new THREE.InstancedBufferAttribute(values, 1));
    mesh.frustumCulled = false;
    mesh.renderOrder = 36;
    this.riskGroup.add(mesh);
  }

  _syncUser() {
    const u = this.state.user_location;
    if (this.userObj) { this.scene.remove(this.userObj); this.userObj = null; }
    if (u && u.lat != null) {
      const pos = this.world(u.lat, u.lon);
      const g = new THREE.Group();
      const acc = clamp(u.accuracy_m || 12, 8, 400);
      const disc = new THREE.Mesh(new THREE.CircleGeometry(acc, 48).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: col(PALETTE.user), transparent: true, opacity: 0.16, depthTest: false, depthWrite: false }));
      disc.position.y = 0.5; disc.renderOrder = 35;
      g.add(disc);
      g.position.copy(pos);
      this.scene.add(g);
      this.userObj = g;
      this._marker('user:me', 'user', pos.clone().setY(2), '<span class="dot"></span>', 'Your position');
    }
    this._sweepMarkers('user:');
  }

  setPickMode(mode) {
    this.pickMode = mode;
    this.canvas.style.cursor = mode ? 'crosshair' : '';
  }

  // ---- pointer ------------------------------------------------------------
  _groundPoint(clientX, clientY, withBuildings) {
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    const ray = this._ray || (this._ray = new THREE.Raycaster());
    ray.setFromCamera(ndc, this.camera);
    let hit = null;
    const d = ray.ray.direction, o = ray.ray.origin;
    const tGround = d.y < -1e-5 ? -o.y / d.y : Infinity;
    if (withBuildings) {
      const meshes = [];
      for (const ch of this.chunks.values()) if (ch.buildings) for (const mb of ch.buildings) if (mb.visible) meshes.push(mb);
      ray.far = tGround;                       // a building is always hit before the ground behind it
      const hits = ray.intersectObjects(meshes, false);
      ray.far = Infinity;
      if (hits.length) hit = hits[0].point;
    }
    if (!hit && Number.isFinite(tGround)) hit = new THREE.Vector3(o.x + d.x * tGround, 0, o.z + d.z * tGround);
    if (!hit) return null;
    return { x: hit.x, y: -hit.z, ...this.toLatLon(hit.x, -hit.z) };
  }

  _bindPointer() {
    let down = null;
    this.canvas.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, t: performance.now(), button: e.button }; });
    this.canvas.addEventListener('pointerup', (e) => {
      if (!down || down.button !== 0) { down = null; return; }
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
      const quick = performance.now() - down.t < 600;
      down = null;
      if (moved > 6 || !quick) return;
      const p = this._groundPoint(e.clientX, e.clientY, true);
      if (!p) return;
      const e2 = this.manifest.extent;
      const inside = p.x >= e2[0] && p.x <= e2[2] && p.y >= e2[1] && p.y <= e2[3];
      this.onEvent({ type: 'click', lat: +p.lat.toFixed(6), lon: +p.lon.toFixed(6), mode: this.pickMode, inside });
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (e.pointerType === 'touch') return;
      const now = performance.now();
      if (now - (this._lastHover || 0) < 60) return;
      this._lastHover = now;
      const p = this._groundPoint(e.clientX, e.clientY, false);
      if (p) this.onEvent({ type: 'hover', lat: p.lat, lon: p.lon });
    });
    this.overlay.addEventListener('click', (e) => {
      const el = e.target.closest('.marker.hazard');
      if (el) this.onEvent({ type: 'hazard_click', id: el.dataset.id === '' ? null : (isNaN(+el.dataset.id) ? el.dataset.id : +el.dataset.id) });
    });
  }

  _bindKeys() {
    this.keys = new Set();
    this.canvas.addEventListener('keydown', (e) => {
      const k = e.key.toLowerCase();
      if (['w', 'a', 's', 'd', 'q', 'e', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright', '+', '=', '-'].includes(k)) { this.keys.add(k); e.preventDefault(); this.dirty = true; }
    });
    this.canvas.addEventListener('keyup', (e) => this.keys.delete(e.key.toLowerCase()));
    this.canvas.addEventListener('blur', () => this.keys.clear());
  }

  _applyKeys(dt) {
    if (!this.keys.size) return false;
    const v = this.getView(), k = this.keys;
    const step = v.distance * 0.9 * dt;
    const h = THREE.MathUtils.degToRad(v.heading);
    let f = 0, r = 0, turn = 0, zoom = 0;
    if (k.has('w') || k.has('arrowup')) f += 1;
    if (k.has('s') || k.has('arrowdown')) f -= 1;
    if (k.has('d') || k.has('arrowright')) r += 1;
    if (k.has('a') || k.has('arrowleft')) r -= 1;
    if (k.has('q')) turn -= 1;
    if (k.has('e')) turn += 1;
    if (k.has('+') || k.has('=')) zoom -= 1;
    if (k.has('-')) zoom += 1;
    this.fly = null;
    this.setView({
      x: v.x + (Math.sin(h) * f + Math.cos(h) * r) * step,
      y: v.y + (Math.cos(h) * f - Math.sin(h) * r) * step,
      heading: v.heading + turn * 60 * dt,
      distance: v.distance * (1 + zoom * 1.2 * dt),
    });
    return true;
  }

  // ---- labels + markers ---------------------------------------------------
  setLabels(on) { this.labelsOn = on; this._moved = true; this.dirty = true; }

  _updateLabels(view) {
    const pool = this._labelPool || (this._labelPool = new Map());
    const shown = new Set();
    if (this.labelsOn) {
      const m = this.manifest, cam = this.camera, w = this.width, h = this.height, d = view.distance;
      const fogR = this.shared.uFogRange.value * 1.25;
      const cand = [];
      const rank = { poi: 0, area: 1, rail: 2, water: 3, park: 4, metro: 5, hood: 6 };
      for (let i = 0; i < m.labels.length; i++) {
        const l = m.labels[i], k = l.k;
        if (k === 'hood' && d > 6500) continue;
        if (k === 'poi' && d > 17000) continue;
        if (k === 'rail' && d > 9000) continue;
        if (k === 'metro' && d > 4200) continue;
        if ((k === 'water' || k === 'park') && (d > 15000 || l.a < d / 260)) continue;
        if (k === 'area' && d < 900) continue;
        const p = this._v.set(l.x, 0, -l.y);
        const dist = p.distanceTo(cam.position);
        if (dist > fogR && d < 12000) continue;
        p.project(cam);
        if (p.z > 1 || p.z < -1) continue;
        const sx = (p.x * 0.5 + 0.5) * w, sy = (-p.y * 0.5 + 0.5) * h;
        if (sx < 0 || sx > w || sy < 56 || sy > h - 30) continue;
        cand.push({ i, l, sx, sy, key: rank[k] * 1e7 + dist });
      }
      cand.sort((a, b) => a.key - b.key);
      const boxes = [];
      const max = w < 640 ? 22 : 64;
      for (const c of cand) {
        if (shown.size >= max) break;
        const big = c.l.k === 'area';
        const bw = c.l.n.length * (big ? 8.2 : 6.6) + 26, bh = 20;
        const x0 = c.sx - (c.l.k === 'poi' || c.l.k === 'rail' || c.l.k === 'metro' ? 8 : bw / 2), y0 = c.sy - bh / 2;
        if (x0 < 6 || x0 + bw > w - 6) continue;
        let clash = false;
        for (const bx of boxes) if (x0 < bx[2] && x0 + bw > bx[0] && y0 < bx[3] && y0 + bh > bx[1]) { clash = true; break; }
        if (clash) continue;
        boxes.push([x0 - 6, y0 - 4, x0 + bw + 6, y0 + bh + 4]);
        shown.add(c.i);
        let el = pool.get(c.i);
        if (!el) {
          el = document.createElement('div');
          el.className = `lbl ${c.l.k}`;
          el.textContent = c.l.n;
          this.overlay.appendChild(el);
          pool.set(c.i, el);
          requestAnimationFrame(() => el.classList.add('on'));
        }
        el.style.transform = `translate3d(${Math.round(x0)}px, ${Math.round(c.sy)}px, 0) translateY(-50%)`;
      }
    }
    for (const [i, el] of pool) if (!shown.has(i)) { el.remove(); pool.delete(i); }
  }

  _updateMarkers() {
    const cam = this.camera, w = this.width, h = this.height;
    for (const m of this.markers.values()) {
      const p = this._v.copy(m.pos).project(cam);
      const vis = p.z < 1 && p.z > -1 && p.x > -1.2 && p.x < 1.2 && p.y > -1.2 && p.y < 1.2;
      m.el.style.display = vis ? '' : 'none';
      if (vis) m.el.style.transform = `translate3d(${((p.x * 0.5 + 0.5) * w).toFixed(1)}px, ${((-p.y * 0.5 + 0.5) * h).toFixed(1)}px, 0)`;
    }
  }

  nearestPlace(x, y) {
    let best = null, bd = Infinity;
    for (const l of this.manifest.labels) {
      if (l.k !== 'area' && l.k !== 'hood') continue;
      const d = (l.x - x) ** 2 + (l.y - y) ** 2;
      const wd = l.k === 'area' ? d * 0.45 : d;
      if (wd < bd) { bd = wd; best = l; }
    }
    return best && bd < 2500 ** 2 ? best.n : null;
  }

  // ---- frame --------------------------------------------------------------
  _loop(now) {
    requestAnimationFrame(this._loop);
    const dt = Math.min(0.1, (now - this._lastT) / 1000);
    this._lastT = now;

    if (this.fly) {
      const f = this.fly, t = clamp((now - f.t0) / f.ms, 0, 1), k = ease(t);
      const lerp = (a, b) => a + (b - a) * k;
      // zoom out a little mid-flight on long hops
      const hop = Math.hypot(f.to.x - f.from.x, f.to.y - f.from.y);
      const lift = Math.sin(Math.PI * t) * clamp(hop * 0.35 - Math.max(f.from.distance, f.to.distance) * 0.5, 0, 9000);
      this.setView({
        x: lerp(f.from.x, f.to.x), y: lerp(f.from.y, f.to.y),
        distance: Math.exp(lerp(Math.log(f.from.distance), Math.log(f.to.distance))) + lift,
        heading: lerp(f.from.heading, f.to.heading), pitch: lerp(f.from.pitch, f.to.pitch),
      });
      if (t >= 1) this.fly = null;
    } else {
      this._applyKeys(dt);
      this.controls.update();
    }

    // if a gesture ever leaves the camera at NaN, go back to the last good view
    const cp = this.camera.position, ct = this.controls.target;
    if (!Number.isFinite(cp.x + cp.y + cp.z + ct.x + ct.y + ct.z)) {
      this.fly = null;
      const g = this._goodView || { x: 0, y: 0, distance: 2000, heading: 0, pitch: 55 };
      this.setView({ x: g.x, y: g.y, distance: g.distance, heading: g.heading, pitch: g.pitch });
    }

    // keep the target on the map
    const e = this.manifest.extent, tg = this.controls.target;
    const cx = clamp(tg.x, e[0], e[2]), cz = clamp(tg.z, -e[3], -e[1]);
    if (cx !== tg.x || cz !== tg.z) { this.camera.position.x += cx - tg.x; this.camera.position.z += cz - tg.z; tg.x = cx; tg.z = cz; }
    if (this.camera.position.y < 25) this.camera.position.y = 25;

    if (this._moved || now - this._lastChunkUpdate > 400) {
      this._updateChunks();
      this._lastChunkUpdate = now;
    }
    if (this.buildQueue.length) {
      // a few tiles per frame: each one is uploaded to the GPU on the next draw
      const t0 = performance.now();
      let n = 0;
      do { this._buildChunk(this.buildQueue.shift()); n++; } while (this.buildQueue.length && n < 4 && performance.now() - t0 < 8);
      this.dirty = true;
    }
    // sharper far-view picture once the view around the camera is complete
    if (!this._fabricHiAsked && this.stats.readyAt && this.manifest.fabric.lo && !this.inflight && !this.buildQueue.length && !this.pendingTiles) {
      this._fabricHiAsked = true;
      this._loadFabric(this.manifest.fabric.file, 2);
    }

    // Nothing moved: only the route pulses and hazard rings animate, and those
    // are drawn at 30 frames a second to spare phone batteries.
    const animating = !this.reducedMotion && (this.routeObjs.some((r) => r.beads) || this.hazardGroup.children.length > 0);
    const needed = this.dirty || this.fly || this._moved || this.forceRender;
    if (!needed && (!animating || now - (this._lastRender || 0) < 32)) return;
    this._lastRender = now;

    const view = this.getView();
    if (Number.isFinite(view.x + view.y + view.distance + view.heading + view.pitch)) this._goodView = view;
    const cam = this.camera;
    cam.near = clamp(view.distance * 0.03, 4, 600);
    cam.far = Math.max(90000, view.distance * 8);
    cam.updateProjectionMatrix();
    cam.updateMatrixWorld();

    // horizon line for the sky gradient
    const hd = THREE.MathUtils.degToRad(view.heading);
    const far = this._v2.set(cam.position.x + Math.sin(hd) * 4e6, 0, cam.position.z - Math.cos(hd) * 4e6).project(cam);
    const horizon = far.y * 0.5 + 0.5;
    this.skyMat.uniforms.uHorizon.value = !Number.isFinite(horizon) || far.z > 1 || far.z < -1 ? 2 : horizon;

    this.shared.uTime.value = now / 1000;
    const pulse = 1 + 0.12 * Math.sin(now / 420);
    for (const ring of this.hazardGroup.children) if (ring.userData.pulse && !this.reducedMotion) ring.scale.set(pulse, 1, pulse);
    for (const r of this.routeObjs) if (r.style.dashed) for (const l of r.lines) l.material.dashScale = clamp(1400 / view.distance, 0.12, 2.2);

    if (this._moved || this._lodDirty) { this._updateLod(); this._lodDirty = false; }
    const info = this.renderer.info;
    info.reset();
    this.composer.render();
    this.stats.triangles = info.render.triangles;
    this.stats.drawCalls = info.render.calls;
    this._record(this.stats.renderTimes, now, 240);
    if (this._moved && now - this._lastLabelUpdate > 90) {
      this._updateLabels(view);
      this._lastLabelUpdate = now;
      this._labelsStale = false;
    } else if (this._moved) this._labelsStale = true;
    else if (this._labelsStale) { this._updateLabels(view); this._labelsStale = false; }
    this._updateMarkers();
    if (this._moved) this.onEvent({ type: 'view', view, place: this.nearestPlace(view.x, view.y), tiles: this.pendingTiles || 0 });
    this._moved = false;
    this.dirty = this.buildQueue.length > 0 || this.inflight > 0;

    // step detail down once if a device cannot keep up on "auto"
    if (this.qualityName === 'auto') {
      const loading = this.buildQueue.length > 0 || this.inflight > 0;      // tile builds make frames slow on any device
      this._slow = loading ? (this._slow || 0) : (this._slow || 0) * 0.95 + (dt > 0.055 ? 1 : 0);
      if (this._slow > 14 && this.qualityKey !== 'lite' && !this._stepped) {
        this._stepped = true;
        const order = ['high', 'medium', 'low', 'lite'];
        const nextKey = order[Math.min(order.indexOf(this.qualityKey) + 1, order.length - 1)];
        this.qualityKey = nextKey; this.quality = QUALITY[nextKey];
        this._buildComposer(); this.resize();
        this.onEvent({ type: 'quality', quality: nextKey, auto: true });
        setTimeout(() => { this._stepped = false; this._slow = 0; }, 6000);
      }
    }
  }
}

const HAZARD_LABEL = {
  broken_streetlight: 'Broken streetlight',
  dark_path: 'Dark path',
  blocked_footpath: 'Blocked footpath',
  other_infrastructure: 'Other infrastructure',
};

const svg = (d) => `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const HAZARD_ICON = {
  broken_streetlight: svg('<path d="M9 21h6M10 17h4M8 3l8 8M7.5 8.5A5 5 0 0 0 10 14v3M16.5 11.5A5 5 0 0 0 12 5"/>'),
  dark_path: svg('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>'),
  blocked_footpath: svg('<path d="M3 20h18M6 20V9h12v11M4 9h16M9 9V5h6v4M9 13l6 4M15 13l-6 4"/>'),
  other_infrastructure: svg('<path d="M12 3l10 18H2L12 3zM12 10v5M12 18h.01"/>'),
};

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export { HAZARD_LABEL };
