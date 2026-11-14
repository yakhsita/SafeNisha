/* SafeNisha map engine: slippy tile map and exact backend route geometry. */
(function () {
  'use strict';
  const SN = (window.SN = window.SN || {});
  const NS = 'http://www.w3.org/2000/svg';
  const ZMIN = 2, ZMAX = 19, TS = 256;
  // Basemap: Esri Canvas (keyless). Native tiles stop at z16; z17 is drawn by scaling z16 tiles.
  const TILE_MAX = 16;
  const tileURL = (dark, z, x, y) =>
    `https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? 'Dark' : 'Light'}_Gray_Base/MapServer/tile/${z}/${y}/${x}`;
  const ATTRIBUTION = 'Tiles © Esri · HERE, Garmin, © OpenStreetMap contributors';

  const reduced = () => !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function proj(lat, lon, z) {
    const s = TS * 2 ** z, sn = Math.sin((lat * Math.PI) / 180);
    return [((lon + 180) / 360) * s, (0.5 - Math.log((1 + sn) / (1 - sn)) / (4 * Math.PI)) * s];
  }
  function unproj(x, y, z) {
    const s = TS * 2 ** z, n = Math.PI - (2 * Math.PI * y) / s;
    return [(180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))), (x / s) * 360 - 180];
  }
  function svg(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function rgb(hex) {
    hex = (hex || '#000000').replace('#', '').trim();
    if (hex.length === 3) hex = hex.replace(/./g, (c) => c + c);
    const n = parseInt(hex, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const rgba = (hex, a) => { const c = rgb(hex); return `rgba(${c[0]},${c[1]},${c[2]},${a})`; };
  function mix(h1, h2, t) {
    const a = rgb(h1), b = rgb(h2);
    return '#' + a.map((v, i) => Math.round(lerp(v, b[i], t)).toString(16).padStart(2, '0')).join('');
  }

  /* ---------- theme colours (read from CSS tokens) ---------- */
  const C = {};
  SN.refreshColors = function () {
    const cs = getComputedStyle(document.documentElement);
    ['safe', 'risk', 'warn', 'info', 'accent', 'text', 'muted', 'halo'].forEach((k) => { C[k] = cs.getPropertyValue('--' + k).trim(); });
    C.dark = document.documentElement.dataset.theme !== 'light';
  };
  SN.colors = C;

  function riskClass(r) { return r == null ? 'Data limited' : r < 0.3 ? 'Lower' : r < 0.6 ? 'Moderate' : 'Higher'; }
  /** Continuous green -> amber -> red scale shared by routes, strips and the atlas. */
  function riskColor(v) {
    const t = clamp((v - 0.15) / 0.6, 0, 1);
    return t < 0.5 ? mix(C.safe, C.warn, t * 2) : mix(C.warn, C.risk, (t - 0.5) * 2);
  }
  SN.util = { clamp, lerp, rgba, mix, esc, riskClass, riskColor, reduced };

  /* Routes are returned by Python; their geometry and metrics are never interpolated. */
  SN.computeRoutes = function(sc) {
    if(!sc.routes)return null;
    const s=sc.routes.shortest,l=sc.routes.lower_risk;
    return {
      short:{d:s.distance_m,r:s.risk_indicator,seg:s.segment_risk,path:s.path},
      low:{d:l.distance_m,r:l.risk_indicator,seg:l.segment_risk,path:l.path}
    };
  };

  // Same Gaussian field and weights used by Python's route scoring.
  SN.syntheticRiskAt=function(lat,lon,zones,definition={}){
    const weights=definition.weights||{historical_incidents:0.35,lighting_deficiency:0.30,isolation:0.10,uncertainty:0.05};
    const bandwidth=definition.bandwidth_fraction??0.24;
    const names=['historical_incidents','lighting_deficiency','isolation'];
    const values={historical_incidents:0,lighting_deficiency:0,isolation:0};
    const known=zones.some(z=>{const [w,s,e,n]=z.coverage_bounds||z.bounds;return lon>=w&&lon<=e&&lat>=s&&lat<=n;});
    if(known)zones.forEach(z=>{
      const [w,s,e,n]=z.bounds,sx=Math.max((e-w)*bandwidth,1e-8),sy=Math.max((n-s)*bandwidth,1e-8);
      const kernel=Math.exp(-0.5*(((lon-(w+e)/2)/sx)**2+((lat-(s+n)/2)/sy)**2));
      names.forEach(name=>values[name]+=kernel*z[name]);
    });
    return names.reduce((risk,name)=>risk+(weights[name]||0)*(known?Math.min(1,values[name]):1),(weights.uncertainty||0)*(definition.uncertainty??1));
  };

  /* ---------- shared tooltip ---------- */
  const tip = document.createElement('div');
  tip.className = 'map-tip';
  tip.setAttribute('role', 'tooltip');
  document.body.appendChild(tip);
  SN.tip = {
    show(e, html) {
      tip.innerHTML = html;
      tip.classList.add('on');
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let x = e.clientX + 14, y = e.clientY + 16;
      if (x + w > innerWidth - 8) x = e.clientX - w - 14;
      if (y + h > innerHeight - 8) y = e.clientY - h - 12;
      tip.style.transform = `translate(${Math.max(8, x)}px,${Math.max(8, y)}px)`;
    },
    hide() { tip.classList.remove('on'); },
  };

  const ICON = {
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    minus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>',
    fit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  };
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  class SNMap {
    constructor(host, opts = {}) {
      this.host = host;
      this.mode = opts.mode || 'planner';
      this.onPick = opts.onPick || null;
      this.onSelect = opts.onSelect || null;
      host.classList.add('map');
      if (this.mode === 'picker') host.classList.add('is-pick');
      host.tabIndex = 0;
      host.setAttribute('role', 'application');
      host.setAttribute('aria-label', (opts.label || 'Map') + '. Arrow keys pan, plus and minus zoom.');
      host.innerHTML =
        '<div class="map-world"><div class="map-tiles"></div><svg class="map-ov" aria-hidden="true"></svg></div>' +
        `<div class="map-ctrls glass"><button type="button" data-z="1" aria-label="Zoom in">${ICON.plus}</button>` +
        `<button type="button" data-z="-1" aria-label="Zoom out">${ICON.minus}</button>` +
        `<button type="button" data-fit aria-label="Fit map to routes">${ICON.fit}</button></div>` +
        '<div class="map-chip glass" hidden></div><div class="map-hint" aria-hidden="true"></div>' +
        '<div class="map-legend glass"></div><div class="map-attr"></div>';
      const q = (s) => host.querySelector(s);
      this.world = q('.map-world');
      this.tilesEl = q('.map-tiles');
      this.ov = q('.map-ov');
      this.chipEl = q('.map-chip');
      this.hintEl = q('.map-hint');
      this.legendEl = q('.map-legend');
      this.attrEl = q('.map-attr');
      this.M = { z: 16, cx: 0, cy: 0, ox: 0, oy: 0, w: 0, h: 0 };
      this.scene = {};
      this.layer = null;
      this.old = [];
      this.fitted = false;
      this.routeEls = null;
      this._wire();
      if (window.ResizeObserver) new ResizeObserver(() => this._resize()).observe(host);
      else addEventListener('resize', () => this._resize());
    }

    /* ----- view ----- */
    _resize() {
      const w = this.host.clientWidth, h = this.host.clientHeight;
      if (!w || !h) return;
      const M = this.M, changed = w !== M.w || h !== M.h;
      M.w = w; M.h = h;
      if (!this.fitted) return this.fit();
      if (changed) { this.fit(); }
    }
    _points() {
      const sc = this.scene, P = [], R = sc.routes;
      const withRoutes = this.mode === 'demo' || (sc.showRoutes !== false && sc.searched !== false && sc.state !== 'no_route');
      if (R && withRoutes) ['shortest', 'lower_risk'].forEach((k) => (R[k].path || []).forEach((p) => P.push(p)));
      if (sc.start) P.push([sc.start.lat, sc.start.lon]);
      if (sc.end) P.push([sc.end.lat, sc.end.lon]);
      if (!R && !sc.start && !sc.end && sc.currentLocation) P.push([sc.currentLocation.lat,sc.currentLocation.lon]);
      if (sc.atlas) P.push([sc.atlas.south, sc.atlas.west], [sc.atlas.north, sc.atlas.east]);
      if(this.mode==='picker'){(sc.hazards||[]).forEach(h=>P.push([h.lat,h.lon]));if(sc.pin)P.push([sc.pin.lat,sc.pin.lon]);}
      if (!P.length) P.push([12.9335, 77.6225]);
      return P;
    }
    centerOn(lat,lon) {
      const center=proj(lat,lon,16);
      this.fitted=true;
      this._setView(16,center[0],center[1]);
    }
    fit(smooth) {
      const M = this.M;
      if (!M.w) return;
      let la0 = 90, la1 = -90, lo0 = 180, lo1 = -180;
      this._points().forEach((p) => { la0 = Math.min(la0, p[0]); la1 = Math.max(la1, p[0]); lo0 = Math.min(lo0, p[1]); lo1 = Math.max(lo1, p[1]); });
      const pad = clamp(Math.min(M.w, M.h) * 0.12, 28, 80);
      let z = ZMAX;
      for (; z > ZMIN; z--) {
        const a = proj(la1, lo0, z), b = proj(la0, lo1, z);
        if (b[0] - a[0] <= M.w - 2 * pad && b[1] - a[1] <= M.h - 2 * pad) break;
      }
      const c = proj((la0 + la1) / 2, (lo0 + lo1) / 2, z);
      if (smooth && this.fitted && z === M.z && !reduced()) return this._glide(c[0], c[1]);
      this.fitted = true;
      this._setView(z, c[0], c[1]);
    }
    _setView(z, cx, cy) {
      const M = this.M;
      M.z = z; M.cx = cx; M.cy = cy; M.ox = Math.round(cx); M.oy = Math.round(cy);
      this._apply();
      this._placeLayers();
      this._tiles();
      this.draw();
    }
    _apply() {
      const M = this.M;
      this.world.style.transform = `translate3d(${M.w / 2 + M.ox - M.cx}px,${M.h / 2 + M.oy - M.cy}px,0)`;
    }
    _glide(tx, ty, dur = 380) {
      cancelAnimationFrame(this._anim);
      const M = this.M, fx = M.cx, fy = M.cy, t0 = performance.now();
      const step = (now) => {
        const p = Math.min(1, (now - t0) / dur), e = 1 - (1 - p) ** 3;
        M.cx = lerp(fx, tx, e); M.cy = lerp(fy, ty, e);
        this._apply(); this._queueTiles();
        if (p < 1) this._anim = requestAnimationFrame(step);
      };
      this._anim = requestAnimationFrame(step);
    }
    panBy(dx, dy) { if (reduced()) { this.M.cx += dx; this.M.cy += dy; this._apply(); this._tiles(); } else this._glide(this.M.cx + dx, this.M.cy + dy, 220); }

    zoomBy(dz, ax, ay) {
      const M = this.M;
      const nz = clamp(M.z + dz, ZMIN, ZMAX);
      if (nz === M.z || this._zooming || !M.w) return;
      dz = nz - M.z;
      if (ax == null) { ax = M.w / 2; ay = M.h / 2; }
      cancelAnimationFrame(this._anim);
      const s = 2 ** dz, wx = M.cx + ax - M.w / 2, wy = M.cy + ay - M.h / 2;
      const ncx = wx * s - (ax - M.w / 2), ncy = wy * s - (ay - M.h / 2);
      const base = `translate(${M.w / 2 + M.ox - M.cx}px,${M.h / 2 + M.oy - M.cy}px)`;
      const done = () => { this._zooming = false; this._setView(nz, ncx, ncy); };
      if (reduced()) return done();
      this._zooming = true;
      SN.tip.hide();
      const t0 = performance.now(), dur = 280;
      const step = (now) => {
        const p = Math.min(1, (now - t0) / dur), e = 1 - (1 - p) ** 3, k = s ** e;
        this.world.style.transform = `translate(${ax}px,${ay}px) scale(${k}) translate(${-ax}px,${-ay}px) ${base}`;
        if (p < 1) requestAnimationFrame(step); else done();
      };
      requestAnimationFrame(step);
    }

    /* ----- tiles (cross-faded layers, one per zoom/theme) ----- */
    _layerTransform(L) {
      const M = this.M, s = 2 ** (M.z - L.z);
      L.el.style.transform = `translate(${L.ox * s - M.ox}px,${L.oy * s - M.oy}px) scale(${s})`;
    }
    _placeLayers() {
      if (this.layer) this._layerTransform(this.layer);
      this.old.forEach((L) => this._layerTransform(L));
    }
    _clearLayers() {
      if (this.layer) this.layer.el.remove();
      this.old.forEach((L) => L.el.remove());
      this.layer = null; this.old = [];
    }
    _retire(L) {
      while (this.old.length > 1) this.old.shift().el.remove();
      this.old.push(L);
      const kill = () => {
        if (!L.el.isConnected) return;
        L.el.style.opacity = '0';
        setTimeout(() => { L.el.remove(); this.old = this.old.filter((x) => x !== L); }, 450);
      };
      L.kill = kill;
      setTimeout(kill, 1400);
    }
    _queueTiles() {
      if (this._tq) return;
      this._tq = requestAnimationFrame(() => { this._tq = 0; this._tiles(); });
    }
    _tiles() {
      const M = this.M, sc = this.scene;
      if (!M.w) return;
      if (sc.offline || navigator.onLine === false) {
        this._clearLayers();
        this._chip('Offline map · showing routes without tiles', true);
        this.attrEl.textContent = '';
        return;
      }
      const tz = Math.min(M.z, TILE_MAX), f = 2 ** (M.z - tz);
      const style = C.dark ? 'dark' : 'light', key = style + tz;
      let L = this.layer;
      if (!L || L.key !== key) {
        const el = document.createElement('div');
        el.className = 'tile-layer';
        this.tilesEl.appendChild(el);
        const NL = { key, z: tz, style, ox: M.ox / f, oy: M.oy / f, el, imgs: new Map(), loaded: 0 };
        if (L) this._retire(L);
        this.layer = L = NL;
        this._layerTransform(L);
        setTimeout(() => { if (this.layer === L && L.loaded === 0 && !this.scene.offline) this._chip('Offline map · showing routes without tiles', true); }, 4500);
      }
      if (this.chipEl.dataset.offline && L.loaded) this._chip('');
      this.attrEl.textContent = ATTRIBUTION;
      const max = 2 ** tz, cx = M.cx / f, cy = M.cy / f, hw = M.w / 2 / f, hh = M.h / 2 / f;
      const x0 = Math.floor((cx - hw) / TS) - 1, x1 = Math.floor((cx + hw) / TS) + 1;
      const y0 = Math.floor((cy - hh) / TS) - 1, y1 = Math.floor((cy + hh) / TS) + 1;
      const want = new Set();
      for (let tx = x0; tx <= x1; tx++) {
        for (let ty = y0; ty <= y1; ty++) {
          if (ty < 0 || ty >= max) continue;
          const k = tx + '/' + ty;
          want.add(k);
          if (L.imgs.has(k)) continue;
          const wx = ((tx % max) + max) % max, img = new Image();
          img.alt = ''; img.decoding = 'async'; img.draggable = false;
          img.style.left = tx * TS - L.ox + 'px';
          img.style.top = ty * TS - L.oy + 'px';
          img.onload = () => {
            img.classList.add('ok'); L.loaded++;
            if (this.layer === L && this.chipEl.dataset.offline) this._chip('');
            if (L.loaded >= 6 && this.layer === L) this.old.forEach((o) => o.kill && setTimeout(o.kill, 250));
          };
          img.onerror = () => img.remove();
          img.src = tileURL(style === 'dark', tz, wx, ty);
          L.el.appendChild(img);
          L.imgs.set(k, img);
        }
      }
      if (L.imgs.size > want.size * 2.5) {
        for (const [k, img] of L.imgs) if (!want.has(k)) { img.remove(); L.imgs.delete(k); }
      }
    }
    _chip(text, offline) {
      const c = this.chipEl;
      if (!text) { c.hidden = true; delete c.dataset.offline; return; }
      c.textContent = text; c.hidden = false;
      if (offline) c.dataset.offline = '1';
    }
    _hint() {
      this.hintEl.textContent = `Hold ${isMac ? '⌘' : 'Ctrl'} and scroll to zoom`;
      this.hintEl.classList.add('on');
      clearTimeout(this._ht);
      this._ht = setTimeout(() => this.hintEl.classList.remove('on'), 1300);
    }
    themeChanged() { this._tiles(); this.draw(); }

    /* ----- input ----- */
    _wire() {
      const host = this.host;
      let down = null, moved = false, samples = [];
      host.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || e.target.closest('button, a, input')) return;
        cancelAnimationFrame(this._anim);
        down = { x: e.clientX, y: e.clientY, cx: this.M.cx, cy: this.M.cy, id: e.pointerId };
        moved = false;
        samples = [[performance.now(), e.clientX, e.clientY]];
      });
      host.addEventListener('pointermove', (e) => {
        if (!down || e.pointerId !== down.id || this._zooming) return;
        const dx = e.clientX - down.x, dy = e.clientY - down.y;
        if (!moved && Math.hypot(dx, dy) > 4) {
          moved = true; host.classList.add('is-drag'); SN.tip.hide();
          try { host.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
        }
        if (moved) {
          this.M.cx = down.cx - dx; this.M.cy = down.cy - dy;
          this._apply(); this._queueTiles();
          samples.push([performance.now(), e.clientX, e.clientY]);
          if (samples.length > 8) samples.shift();
        }
      });
      const up = (e) => {
        if (!down || e.pointerId !== down.id) return;
        const wasMoved = moved;
        down = null; host.classList.remove('is-drag');
        if (wasMoved) return this._fling(samples);
        if (this.onPick && e.type === 'pointerup' && !e.target.closest('button, a, input')) {
          const r = host.getBoundingClientRect();
          const wx = this.M.cx + e.clientX - r.left - this.M.w / 2, wy = this.M.cy + e.clientY - r.top - this.M.h / 2;
          const ll = unproj(wx, wy, this.M.z);
          if (this.mode === 'picker') this.scene.pin = { lat: ll[0], lon: ll[1] };
          this.draw({ dropPin: true });
          if (this.onPick) this.onPick(ll[0], ll[1]);
        }
      };
      host.addEventListener('pointerup', up);
      host.addEventListener('pointercancel', up);
      host.addEventListener('wheel', (e) => {
        if (!(e.ctrlKey || e.metaKey)) return this._hint();
        e.preventDefault();
        this._wacc = (this._wacc || 0) + e.deltaY * (e.deltaMode ? 30 : 1);
        if (Math.abs(this._wacc) >= 50) {
          const r = host.getBoundingClientRect();
          this.zoomBy(this._wacc < 0 ? 1 : -1, e.clientX - r.left, e.clientY - r.top);
          this._wacc = 0;
        }
      }, { passive: false });
      host.addEventListener('dblclick', (e) => {
        if (e.target.closest('.map-ctrls')) return;
        const r = host.getBoundingClientRect();
        this.zoomBy(e.shiftKey ? -1 : 1, e.clientX - r.left, e.clientY - r.top);
      });
      host.addEventListener('keydown', (e) => {
        if (e.target !== host) return;
        const k = e.key, st = 90;
        if (k === '+' || k === '=') this.zoomBy(1);
        else if (k === '-' || k === '_') this.zoomBy(-1);
        else if (k === 'ArrowLeft') this.panBy(-st, 0);
        else if (k === 'ArrowRight') this.panBy(st, 0);
        else if (k === 'ArrowUp') this.panBy(0, -st);
        else if (k === 'ArrowDown') this.panBy(0, st);
        else return;
        e.preventDefault();
      });
      host.querySelectorAll('[data-z]').forEach((b) => b.addEventListener('click', () => this.zoomBy(+b.dataset.z)));
      host.querySelector('[data-fit]').addEventListener('click', () => this.fit(true));
    }
    _fling(s) {
      const b = s[s.length - 1], a = s.find((p) => p[0] >= b[0] - 100) || s[0], dt = b[0] - a[0];
      if (reduced() || dt <= 0 || performance.now() - b[0] > 80) return this._tiles();
      let vx = (b[1] - a[1]) / dt, vy = (b[2] - a[2]) / dt, last = performance.now();
      const step = (now) => {
        const d = Math.min(32, now - last); last = now;
        this.M.cx -= vx * d; this.M.cy -= vy * d;
        const f = Math.pow(0.93, d / 16); vx *= f; vy *= f;
        this._apply(); this._queueTiles();
        if (Math.hypot(vx, vy) > 0.02) this._anim = requestAnimationFrame(step);
      };
      this._anim = requestAnimationFrame(step);
    }

    /* ----- overlay ----- */
    _rel(lat, lon) { const p = proj(lat, lon, this.M.z); return [p[0] - this.M.ox, p[1] - this.M.oy]; }
    _d(path) { return 'M' + path.map((p) => { const q = this._rel(p[0], p[1]); return q[0].toFixed(1) + ',' + q[1].toFixed(1); }).join('L'); }

    draw(o = {}) {
      const sc = this.scene, ov = this.ov;
      ov.textContent = '';
      this.routeEls = null;
      if(this.mode==='atlas')this._syntheticZones();
      const R = SN.computeRoutes(sc);
      const showR = R && !sc.loading && sc.showRoutes !== false && sc.searched !== false && sc.state !== 'no_route';
      if (showR) {
        const g = svg('g', { class: 'routes' + (o.animate && !reduced() ? ' fade-in' : '') }, ov);
        this.routeEls = {};
        const order = sc.sel === 'short' ? ['low', 'short'] : ['short', 'low'];
        order.forEach((k) => {
          const low = k === 'low', sel = sc.sel === k, col = low ? C.safe : C.risk;
          const d = this._d(R[k].path), w = low ? (sel ? 7 : 5) : (sel ? 5.5 : 4.2);
          const halo = svg('path', { d, class: 'rt-halo', stroke: col, 'stroke-width': w * 3.4, 'stroke-opacity': sel ? 0.3 : 0.1 }, g);
          const casing = svg('path', { d, class: 'rt-case', stroke: C.halo, 'stroke-width': w + 4, 'stroke-opacity': sel ? 0.95 : 0.7 }, g);
          const main = svg('path', { d, class: 'rt-main', stroke: col, 'stroke-width': w, 'stroke-opacity': sel ? 1 : 0.92 }, g);
          if (!low) main.setAttribute('stroke-dasharray', `0.1 ${(w * 2.3).toFixed(1)}`);
          if(low)(sc.routes.lower_risk.risk_paths||[]).forEach(piece=>{
            svg('path',{d:this._d(piece.path),'class':'risk-segment',stroke:riskColor(piece.risk),
              'stroke-width':w,'data-risk':piece.risk,fill:'none','pointer-events':'none'},g);
          });
          const hit = svg('path', { d, class: 'rt-hit' }, g);
          hit.addEventListener('click', () => this.onSelect && this.onSelect(k));
          hit.addEventListener('pointermove', (e) => {
            const m = SN.computeRoutes(this.scene)[k];
            const rect=this.host.getBoundingClientRect();
            const point=unproj(this.M.cx+e.clientX-rect.left-this.M.w/2,this.M.cy+e.clientY-rect.top-this.M.h/2,this.M.z);
            const local=SN.syntheticRiskAt(point[0],point[1],sc.synthetic_zones||[],sc.risk_field||sc.routes.risk_field||{});
            SN.tip.show(e, `<b>${low?'Preferred route':'Shortest'}</b> - ${Math.round(m.d)} m - average ${m.r?.toFixed(2)} - local model risk ${local.toFixed(2)}`);
          });
          hit.addEventListener('pointerleave', SN.tip.hide);
          this.routeEls[k] = { halo, casing, main, hit };
          if (low && o.animate && !reduced()) {
            try {
              const len = main.getTotalLength();
              main.style.strokeDasharray = len; main.style.strokeDashoffset = len;
              main.getBoundingClientRect();
              main.style.transition = 'stroke-dashoffset 1.1s cubic-bezier(.65,0,.35,1)';
              main.style.strokeDashoffset = '0';
              setTimeout(() => { main.style.strokeDasharray = ''; main.style.transition = ''; }, 1200);
            } catch (_) { /* ignore */ }
          }
        });
      }
      if (sc.currentLocation) {
        const q=this._rel(sc.currentLocation.lat,sc.currentLocation.lon);
        svg('circle',{cx:q[0],cy:q[1],r:10,fill:'#2563eb',stroke:'#fff','stroke-width':3},ov);
      }
      if (sc.previewHazard) {
        const q=this._rel(sc.previewHazard.lat,sc.previewHazard.lon);
        const g=svg('g',{'class':'preview-hazard',transform:`translate(${q[0]},${q[1]})`},ov);
        svg('circle',{r:12,fill:'#dc2626',stroke:'#fff','stroke-width':3},g);
        svg('text',{y:5,'text-anchor':'middle',fill:'#fff','font-weight':'bold','font-size':17},g).textContent='!';
      }
      const detour=sc.routes?.lower_risk?.detour;
      if (detour?.turnaround) {
        const q=this._rel(...detour.turnaround);
        svg('text',{x:q[0],y:q[1]-12,fill:'#f59e0b','font-size':28,'text-anchor':'middle'},ov).textContent='\u21b6';
        if(detour.return_geometry?.length)svg('path',{d:this._d(detour.return_geometry),fill:'none',stroke:'#f59e0b','stroke-width':4,'stroke-dasharray':'7 5'},ov);
      }
      // start / end
      [[sc.start, 'Start', 'start'], [sc.end, 'End', 'end']].forEach(([p, label, cls]) => {
        if (!p) return;
        const q = this._rel(p.lat, p.lon);
        const g = svg('g', { class: 'mk mk-' + cls, transform: `translate(${q[0]},${q[1]})` }, ov);
        if (cls === 'start') svg('circle', { r: 8.5, fill: '#fff', stroke: C.accent, 'stroke-width': 4 }, g);
        else {
          svg('circle', { r: 14, fill: C.accent, 'fill-opacity': 0.22 }, g);
          svg('circle', { r: 7.5, fill: C.accent, stroke: '#fff', 'stroke-width': 2.5 }, g);
        }
        svg('text', { y: -18, class: 'mk-label', fill: C.text, stroke: C.halo }, g).textContent = label;
      });
      // hazards
      (sc.hazards || []).forEach((h) => {
        const q = this._rel(h.lat, h.lon);
        const g = svg('g', { class: 'hz', transform: `translate(${q[0]},${q[1]})` }, ov);
        const inner = svg('g', { class: h._drop ? 'drop-in' : null,opacity:h.status==='resolved'?0.45:1 }, g);
        svg('circle', { r: 10, fill: C.warn, class: 'hz-ring' }, inner);
        svg('rect', { x: -7.5, y: -7.5, width: 15, height: 15, rx: 3.5, fill: C.warn, stroke: '#fff', 'stroke-width': 2, 'stroke-dasharray': h.status === 'new' ? '3.2 2.4' : null, transform: 'rotate(45)' }, inner);
        g.addEventListener('pointermove', (e) => SN.tip.show(e, `<b>${esc(h.label)}</b> · ${h.status === 'new' ? 'unverified' : esc(h.status)}`));
        g.addEventListener('pointerleave', SN.tip.hide);
      });
      // picker pin
      if (sc.pin && this.mode === 'picker') {
        const q = this._rel(sc.pin.lat, sc.pin.lon);
        const g = svg('g', { 'class':'selected-hazard-pin','data-lat':sc.pin.lat,'data-lon':sc.pin.lon,transform: `translate(${q[0]},${q[1]})` }, ov);
        const inner = svg('g', { class: o.dropPin ? 'drop-in' : null }, g);
        svg('circle', { r: 18, fill: C.risk, 'fill-opacity': 0.16, class: 'pin-pulse' }, inner);
        svg('circle', { r: 13, fill: C.risk, 'fill-opacity': 0.18, stroke: C.risk, 'stroke-width': 2 }, inner);
        svg('circle', { r: 4.5, fill: C.risk }, inner);
      }
      this._legend();
    }
    /** Cheap per-frame update while the preference slider animates. */
    updateRoutes() {
      if (!this.routeEls) return this.draw();
      const R = SN.computeRoutes(this.scene);
      if (!R) return this.draw();
      const d = this._d(R.low.path), e = this.routeEls.low;
      ['halo', 'casing', 'main', 'hit'].forEach((n) => e[n].setAttribute('d', d));
      e.main.style.strokeDasharray = ''; e.main.style.transition = '';
    }
    _syntheticZones() {
      const zones=this.scene.synthetic_zones||[];
      if(!zones.length)return;
      const definition=this.scene.risk_field||this.scene.routes?.risk_field||{};
      const key=JSON.stringify([zones,definition])+'|'+C.dark;
      if(this._heatKey!==key){
        const anchors=zones.map(z=>{
          const [w,s,e,n]=z.bounds;
          return {lon:(w+e)/2,lat:(s+n)/2,sx:(e-w)*(definition.bandwidth_fraction??0.24),sy:(n-s)*(definition.bandwidth_fraction??0.24),
            value:0.35*z.historical_incidents+0.30*z.lighting_deficiency+0.10*z.isolation+0.05};
        });
        const west=Math.min(...anchors.map(a=>a.lon-2.5*a.sx)),east=Math.max(...anchors.map(a=>a.lon+2.5*a.sx));
        const south=Math.min(...anchors.map(a=>a.lat-2.5*a.sy)),north=Math.max(...anchors.map(a=>a.lat+2.5*a.sy));
        const canvas=document.createElement('canvas');canvas.width=256;canvas.height=256;
        const context=canvas.getContext('2d'),image=context.createImageData(256,256);
        // Strength is the synthetic risk at each anchor, not a map-wide peak.
        // Compact kernels and a transparent floor keep low-risk ground clear.
        for(let y=0;y<256;y++)for(let x=0;x<256;x++){
          const lon=west+(east-west)*(x+0.5)/256,lat=north-(north-south)*(y+0.5)/256;
          const risk=SN.syntheticRiskAt(lat,lon,zones,definition);
          const baseline=(definition.weights?.uncertainty??0.05)*(definition.uncertainty??1);
          const strength=Math.max(0,Math.min(1,(risk-baseline)/0.75));
          const color=rgb(riskColor(risk)),index=(y*256+x)*4;
          image.data[index]=color[0];image.data[index+1]=color[1];image.data[index+2]=color[2];
          image.data[index+3]=strength<0.04?0:Math.round(185*Math.pow(strength,0.8));
        }
        context.putImageData(image,0,0);this._heatKey=key;
        this._heat={url:canvas.toDataURL(),west,east,south,north};
      }
      const h=this._heat,a=this._rel(h.north,h.west),b=this._rel(h.south,h.east);
      svg('image',{x:a[0],y:a[1],width:b[0]-a[0],height:b[1]-a[1],href:h.url,
        'class':'kde-heatmap','preserveAspectRatio':'none','pointer-events':'none'},this.ov);
    }
    _legend() {
      const sc = this.scene;
      let h;
      if (this.mode === 'atlas') {
        h = '<div class="lg-title">Synthetic risk hotspots</div><div class="lg-grad"></div><div class="lg-scale"><span>Lower</span><span>Moderate</span><span>Higher</span></div>';
        if(sc.routes)h+='<div class="lg-it"><i class="lg-low"></i>Preferred (risk-coloured)</div><div class="lg-it"><i class="lg-short"></i>Shortest route</div>';
      } else if (this.mode === 'picker') {
        h = '<div class="lg-it"><i class="lg-pin"></i>Your report location</div><div class="lg-it"><i class="lg-hz"></i>Reported hazard</div>';
      } else {
        h = '<div class="lg-it"><i class="lg-low"></i>Preferred (risk-coloured)</div><div class="lg-it"><i class="lg-short"></i>Shortest</div>' +
          ((sc.hazards || []).length ? '<div class="lg-it"><i class="lg-hz"></i>Reported hazard</div>' : '');
      }
      if (h !== this._lg) { this.legendEl.innerHTML = h; this._lg = h; }
    }
  }

  SN.Map = SNMap;
})();
