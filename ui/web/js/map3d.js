/* SafeNisha 3D plan map.
   Puts the 3D city renderer from ../map3d/ behind the same surface as the flat
   SN.Map, so the planner in app.js drives it unchanged.

   This file only draws. Routes, risk values, hazards and endpoints are the ones
   the Python backend returned; nothing here routes or scores. None of the 3D
   demo's own panels, search, legend or router call is included, only the map.

   If the 3D engine cannot start (no WebGL 2, files missing) the flat map takes over. */
(function () {
  'use strict';
  const SN = (window.SN = window.SN || {});
  const here = document.currentScript.src;
  const ENGINE_URL = new URL('../map3d/engine.js', here).href;
  const DATA_URL = new URL('../map3d/data/', here).href;
  // The open-data licences require this line to stay visible on the map.
  const ATTRIBUTION = '© OpenStreetMap contributors · Overture Maps · GHSL (JRC)';
  const RETURN_COLOR = '#f59e0b';
  const FIT_PITCH = 60;                 // camera tilt, in degrees from straight down, when framing a route
  const ICON = {
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    minus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>',
    fit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
    north: '<svg class="map3d-needle" viewBox="-12 -12 24 24" aria-hidden="true"><path class="n" d="M0-9 4 1 0-1-4 1Z"/><path d="M0 9 4 1 0 3-4 1Z"/></svg>',
  };
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const point = (p) => (p && Number.isFinite(p.lat) && Number.isFinite(p.lon) ? { lat: p.lat, lon: p.lon } : null);

  /** '#rrggbb' -> linear RGB, the colour space the renderer expects for per-vertex colours. */
  function linear(hex) {
    const n = parseInt(String(hex).replace('#', ''), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
      const c = v / 255;
      return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
  }

  class SNMap3D {
    constructor(host, opts = {}) {
      this.host = host;
      this.opts = opts;
      this.mode = opts.mode || 'planner';
      this.onPick = opts.onPick || null;
      this.onSelect = opts.onSelect || null;
      this.scene = {};
      this.city = null;                 // the 3D engine, once it is running
      this.flat = null;                 // SN.Map, only if 3D could not start
      this._pending = { fit: false, center: null };
      this._visible = true;
      this._autoFit = false;
      this._risk = null;
      host.classList.add('map', 'map3d');
      host.setAttribute('role', 'application');
      host.setAttribute('aria-label', (opts.label || 'Map') + ', 3D view');
      host.innerHTML =
        '<div class="map3d-stage"><canvas class="map3d-view" tabindex="0" aria-label="3D map. Drag to move, right-drag to turn and tilt, arrow keys move, plus and minus zoom."></canvas>' +
        '<div class="map3d-overlay" aria-hidden="true"></div></div>' +
        '<div class="map3d-load" aria-hidden="true"><i></i></div>' +
        `<div class="map-ctrls glass"><button type="button" data-z="1" aria-label="Zoom in">${ICON.plus}</button>` +
        `<button type="button" data-z="-1" aria-label="Zoom out">${ICON.minus}</button>` +
        `<button type="button" data-fit aria-label="Fit map to routes">${ICON.fit}</button>` +
        `<button type="button" data-north aria-label="Face north">${ICON.north}</button></div>` +
        '<div class="map-chip glass" hidden></div><div class="map-hint" aria-hidden="true"></div>' +
        '<div class="map-legend glass" hidden></div><div class="map-attr"></div>';
      const q = (s) => host.querySelector(s);
      this.canvas = q('.map3d-view');
      this.overlay = q('.map3d-overlay');
      this.bar = q('.map3d-load i');
      this.needle = q('.map3d-needle');
      this.chipEl = q('.map-chip');
      this.hintEl = q('.map-hint');
      this.legendEl = q('.map-legend');
      q('.map-attr').textContent = ATTRIBUTION;
      this._wire();
      this._boot();
    }

    /* ----- start-up ----- */
    async _boot() {
      try {
        if (!window.WebGL2RenderingContext || !('DecompressionStream' in window)) throw new Error('this browser lacks WebGL 2 or DecompressionStream');
        const { CityMap } = await import(ENGINE_URL);
        const city = new CityMap({
          canvas: this.canvas, overlay: this.overlay, dataUrl: DATA_URL, quality: 'auto',
          onEvent: (e) => this._event(e),
          onStatus: (_text, frac) => { this.bar.style.width = Math.round(4 + frac * 96) + '%'; },
        });
        city.insets = { top: 14, right: 62, bottom: 80, left: 14 };   // keep fitted routes clear of the buttons
        await city.init();
        // Stop the render loop while the map is off screen (another tab, scrolled away).
        // The engine re-reads its _loop property every frame, so wrapping it is enough.
        const loop = city._loop;
        city._loop = (now) => (this._visible ? loop(now) : requestAnimationFrame(city._loop));
        this.city = city;
        this.host.classList.add('is-ready');
        this.draw();
        const p = this._pending;
        if (p.center) city.setView({ lat: p.center.lat, lon: p.center.lon, distance: 900, pitch: 60 });
        else if (p.fit || this._points().length) this.fit(false, true);
        p.center = null;
      } catch (err) {
        console.warn('SafeNisha: 3D map unavailable, showing the flat map instead.', err);
        this._useFlat();
      }
    }
    _useFlat() {
      if (this.flat) return;
      const chip = this.chipEl.hidden ? '' : this.chipEl.textContent;
      this.host.classList.remove('map3d', 'is-ready');
      this.city = null;
      this.flat = new SN.Map(this.host, this.opts);       // rebuilds the host's contents
      this.flat.scene = this.scene;
      if (this._pending.center) {
        const p = this._pending.center;
        this.flat.centerOn(p.lat, p.lon);
      } else this.flat.fit(false);
      this.flat.draw();
      if (chip) this.flat._chip(chip);
    }
    _flat(name, args) { this.flat.scene = this.scene; return this.flat[name](...args); }

    _wire() {
      const host = this.host, canvas = this.canvas;
      // As on the flat map: a plain wheel scrolls the page, Ctrl/⌘ + wheel (or a pinch) zooms.
      host.addEventListener('wheel', (e) => {
        if (this.flat || e.ctrlKey || e.metaKey) return;
        e.stopPropagation();
        this._hint();
      }, { capture: true, passive: true });
      canvas.addEventListener('pointerdown', () => host.classList.add('is-drag'));
      ['pointerup', 'pointercancel'].forEach((t) => canvas.addEventListener(t, () => host.classList.remove('is-drag')));
      host.querySelectorAll('[data-z]').forEach((b) => b.addEventListener('click', () => this.zoomBy(+b.dataset.z)));
      host.querySelector('[data-fit]').addEventListener('click', () => this.fit(true));
      host.querySelector('[data-north]').addEventListener('click', () => { if (this.city) this.city.flyTo({ heading: 0, pitch: 58 }, 600); });
      if ('IntersectionObserver' in window) {
        new IntersectionObserver((entries) => {
          this._visible = entries[entries.length - 1].isIntersecting;
          if (!this._visible || !this.city) return;
          this.city.resize();
          if (this._pending.fit) this.fit(false, true);
        }).observe(host);
      }
      new ResizeObserver(() => {
        if (!this.city || !host.clientWidth || !host.clientHeight) return;
        this.city.resize();
        // Keep a fitted route visible when the layout switches to mobile.
        // Panning or centring on GPS leaves the user's chosen view in place.
        if (this._autoFit) this.fit(false, true);
      }).observe(host);
    }

    /* ----- engine events ----- */
    _event(e) {
      if (e.type === 'interact') {
        this._autoFit = false;
      } else if (e.type === 'view') {
        this.needle.style.transform = `rotate(${-e.view.heading}deg)`;
      } else if (e.type === 'click') {
        // Outside the modelled area there is nothing to show, so the click is not used.
        if (this.city && e.inside && this.onPick) this.onPick(e.lat, e.lon);
      } else if (e.type === 'hazard_click') {
        const id = String(e.id ?? '');
        this.overlay.querySelectorAll('.marker.hazard').forEach((el) => el.classList.toggle('open', el.dataset.id === id && !el.classList.contains('open')));
      }
    }

    /* ----- view ----- */
    _points() {
      const sc = this.scene, P = [], R = sc.routes;
      if (R && sc.showRoutes !== false && sc.searched !== false && sc.state !== 'no_route') {
        ['shortest', 'lower_risk'].forEach((k) => ((R[k] && R[k].path) || []).forEach((p) => P.push(p)));
      }
      if (sc.start) P.push([sc.start.lat, sc.start.lon]);
      if (sc.end) P.push([sc.end.lat, sc.end.lon]);
      if (!P.length && sc.currentLocation) P.push([sc.currentLocation.lat, sc.currentLocation.lon]);
      return P;
    }
    fit(_smooth, instant) {
      if (this.flat) return this._flat('fit', [_smooth]);
      this._autoFit = true;
      const city = this.city;
      this._pending.center = null;
      if (!city || !this.host.clientWidth) { this._pending.fit = true; return; }
      this._pending.fit = false;
      const pts = this._points();
      if (!pts.length) return;
      const view = pts.length === 1 ? { lat: pts[0][0], lon: pts[0][1], distance: 1100, pitch: FIT_PITCH } : this._frame(pts);
      if (instant) city.setView(view); else city.flyTo(view, 1400);
    }
    /** Closest tilted camera, at the current heading, that keeps every point on screen. */
    _frame(pts) {
      const city = this.city, rad = Math.PI / 180;
      const head = city.getView().heading * rad, phi = FIT_PITCH * rad;
      const fx = Math.sin(head), fy = Math.cos(head);           // up the screen, in east/north metres
      const rx = Math.cos(head), ry = -Math.sin(head);          // across the screen
      const tanV = Math.tan((city.camera.fov / 2) * rad), tanH = tanV * (city.width / city.height);
      const sin = Math.sin(phi), A = Math.cos(phi) / (0.74 * tanV), side = 0.84 * tanH;
      const lift = (90 * sin) / (0.74 * tanV);                  // headroom for the pins that stand above the ground
      const uv = pts.map((p) => { const q = city.toLocal(p[0], p[1]); return [q.x * rx + q.y * ry, q.x * fx + q.y * fy]; });
      let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
      for (const [u, v] of uv) { u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
      // Ground near the camera takes more screen than ground far away, so the target sits nearer the near edge.
      const cu = (u0 + u1) / 2, cv = (v1 * (A - sin) + v0 * (A + sin) + lift) / (2 * A);
      let distance = 420;
      for (const [u, v] of uv) {
        const dv = v - cv;
        distance = Math.max(distance, Math.abs(u - cu) / side - dv * sin, dv >= 0 ? dv * (A - sin) + lift : -dv * (A + sin));
      }
      const centre = city.toLatLon(cu * rx + cv * fx, cu * ry + cv * fy);
      return { lat: centre.lat, lon: centre.lon, distance: Math.min(distance, 60000), pitch: FIT_PITCH };
    }
    centerOn(lat, lon) {
      if (this.flat) return this._flat('centerOn', [lat, lon]);
      this._autoFit = false;
      this._pending.fit = false;
      if (!this.city) { this._pending.center = { lat, lon }; return; }
      this.draw();
      this.city.flyTo({ lat, lon, distance: 900, pitch: 60 }, 1200);
    }
    zoomBy(dir) {
      if (this.flat) return this._flat('zoomBy', [dir]);
      this._autoFit = false;
      if (!this.city) return;
      this.city.flyTo({ distance: this.city.getView().distance * (dir > 0 ? 0.6 : 1 / 0.6) }, 320);
    }

    /* ----- planner scene -> engine state ----- */
    /** The preferred route as the backend's risk pieces: every vertex, and the risk of each segment. */
    _pieces(route) {
      const pieces = route.risk_paths, city = this.city;
      if (!Array.isArray(pieces) || !pieces.length) return null;
      const coords = [], seg = [];
      let last = null;
      for (const piece of pieces) {
        for (const pt of piece.path || []) {
          const q = city.toLocal(pt[0], pt[1]);
          if (!Number.isFinite(q.x) || !Number.isFinite(q.y)) continue;
          // the engine merges repeated points with this same rule; match it so colours stay aligned
          if (last && Math.abs(last.x - q.x) < 0.05 && Math.abs(last.y - q.y) < 0.05) continue;
          if (last) seg.push(piece.risk);
          coords.push([pt[0], pt[1]]);
          last = q;
        }
      }
      return coords.length > 1 ? { coords, seg } : null;
    }
    _state() {
      const sc = this.scene, R = sc.routes, routes = [];
      const show = R && !sc.loading && sc.showRoutes !== false && sc.searched !== false && sc.state !== 'no_route';
      let same = false;
      this._risk = null;
      if (show) {
        const low = R.lower_risk, short = R.shortest;
        same = JSON.stringify(low.path) === JSON.stringify(short.path);
        this._risk = this._pieces(low);
        routes.push({ id: 'low', role: 'lower_risk', coords: this._risk ? this._risk.coords : low.path });
        if (!same) routes.push({ id: 'short', role: 'shortest', coords: short.path });
      }
      const detour = show && R.lower_risk && R.lower_risk.detour;
      if (detour && detour.turnaround && (detour.return_geometry || []).length > 1) {
        routes.push({ id: 'return', role: 'alternative', color: RETURN_COLOR, coords: detour.return_geometry });
      }
      return {
        start: point(sc.start),
        destination: point(sc.end),
        routes,
        hazards: (sc.hazards || []).map((h) => ({ id: h.id, lat: h.lat, lon: h.lon, hazard_type: h.hazard_type, description: h.description || '', status: h.status })),
        user_location: point(sc.currentLocation),
        // Both lines stay at full strength until the shortest route is chosen.
        selected_route: show && !same && sc.sel === 'short' ? 'short' : null,
        fit: 'none',                       // the camera moves only when the planner calls fit() or centerOn()
      };
    }
    /** Colour the preferred route by the backend's local risk, one colour per risk piece. */
    _paintRisk() {
      const risk = this._risk, obj = this.city.routeObjs.find((r) => r.id === 'low');
      if (!obj || !risk || obj._snPainted) return;
      obj._snPainted = true;
      if (obj.pts.length !== risk.seg.length + 1) return;      // not aligned: keep the engine's plain line
      const pairs = new Float32Array(risk.seg.length * 6);
      risk.seg.forEach((value, i) => { const c = linear(SN.util.riskColor(value)); pairs.set(c, i * 6); pairs.set(c, i * 6 + 3); });
      // per-segment colours are set on the line's base geometry class (start and end colour per segment)
      Object.getPrototypeOf(Object.getPrototypeOf(obj.geometry)).setColors.call(obj.geometry, pairs);
      for (const line of obj.lines) {
        line.material.vertexColors = true;
        line.material.color.set(0xffffff);
        line.material.needsUpdate = true;
      }
      this.city.dirty = true;
    }
    /** Markers the engine has no type for: the hazard being avoided and the turn-around point. */
    _extras() {
      const city = this.city, sc = this.scene;
      const pre = point(sc.previewHazard);
      if (pre) city._marker('sn:preview', 'sn-preview', city.world(pre.lat, pre.lon, 16), '<span>!</span>');
      const detour = city.state.routes.some(r => r.id === 'return') && sc.routes.lower_risk.detour;
      if (detour && detour.turnaround) city._marker('sn:turn', 'sn-turn', city.world(detour.turnaround[0], detour.turnaround[1], 10), '<span>↶</span>');
      city._sweepMarkers('sn:');
      city.dirty = true;
    }

    draw() {
      if (this.flat) return this._flat('draw', arguments);
      if (!this.city) return;                 // drawn as soon as the engine is ready
      this.city.setState(this._state());
      this._paintRisk();
      this._extras();
      this._legend();
    }
    updateRoutes() { this.draw(); }
    themeChanged() {
      if (this.flat) return this._flat('themeChanged', []);
      if (!this.city) return;
      this.city.routeObjs.forEach((r) => { r._snPainted = false; });   // risk colours follow the theme tokens
      this.draw();
    }
    _tiles() { if (this.flat) this._flat('_tiles', []); }       // the 3D tiles are served by this app, no internet needed

    _legend() {
      const routes = this.city.state.routes;
      this.legendEl.hidden = !routes.length;
      const html = '<div class="lg-it"><i class="lg-low"></i>Preferred (risk-coloured)</div>' +
        (routes.some(r => r.id === 'short') ? '<div class="lg-it"><i class="lg-short"></i>Shortest</div>' : '') +
        (routes.some(r => r.id === 'return') ? '<div class="lg-it"><i class="lg-return"></i>U-turn return</div>' : '');
      if (html !== this._lg) { this.legendEl.innerHTML = html; this._lg = html; }
    }

    /* ----- small overlays shared with the flat map's styling ----- */
    _chip(text) {
      if (this.flat) return this._flat('_chip', arguments);
      this.chipEl.textContent = text || '';
      this.chipEl.hidden = !text;
    }
    _hint() {
      this.hintEl.textContent = `Hold ${isMac ? '⌘' : 'Ctrl'} and scroll to zoom`;
      this.hintEl.classList.add('on');
      clearTimeout(this._ht);
      this._ht = setTimeout(() => this.hintEl.classList.remove('on'), 1300);
    }
  }

  SN.Map3D = SNMap3D;
})();
