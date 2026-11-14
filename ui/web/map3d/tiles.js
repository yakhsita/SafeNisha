// Tile download, decoding and mesh building for the SafeNisha 3D map.
// Runs inside the worker pool (tile-worker.js) and, as a fallback, on the
// main thread. Plain data in, typed arrays out: no three.js in here, so the
// arrays can be handed to the main thread without copying.

import earcut from './vendor/earcut.js';

export const ROAD_WIDTH = [14, 12.5, 11, 9, 7.5, 5.5, 3.4, 1.9, 2.4, 3.0, 3.0];
// draw order, bottom first: waterways, footways, service ... motorway, rail, metro
const ROAD_PRIORITY = [9, 8, 7, 6, 5, 4, 3, 2, 10, 11, 1];

// Buildings in each 500 m block are written largest first. lod[j] is the index
// count covering every building at least LOD_SIZES[j] metres across or tall,
// so drawing a shorter prefix skips the ones too small to see from far away.
export const LOD_SIZES = [0, 6, 8, 10, 12, 15, 20, 30];

const pad4 = (n) => (n + 3) & ~3;

// Tiles are gzipped binary. A host that only serves text can ship them as
// base64 .txt files instead ("tile_ext" and "tile_encoding" in the manifest).
export async function fetchTile(url, encoding) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  let buf;
  if (encoding === 'base64') {
    const bin = atob((await res.text()).trim());
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    buf = bytes.buffer;
  } else buf = await res.arrayBuffer();
  const head = new Uint8Array(buf, 0, Math.min(2, buf.byteLength));
  if (head[0] === 0x1f && head[1] === 0x8b) {
    // stored gzipped; some servers already inflate them in transit
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
    buf = await new Response(stream).arrayBuffer();
  }
  return buf;
}

// Returns { blocks, roads, lamps, ms, transfer }. Building positions are in
// tile units (q metres) relative to the tile centre; roads and lamps in metres.
export function buildTile(buf, { chunk, sub: SUB, q: Q }) {
  const t0 = performance.now();
  const dv = new DataView(buf);
  const nB = dv.getUint32(4, true), nBV = dv.getUint32(8, true), nR = dv.getUint32(12, true), nRP = dv.getUint32(16, true), nL = dv.getUint32(20, true);
  let off = 24;
  const sub = new Uint32Array(buf, off, SUB * SUB); off += SUB * SUB * 4;
  const heights = new Uint16Array(buf, off, nB); off += pad4(nB * 2);
  const counts = new Uint8Array(buf, off, nB); off += pad4(nB);
  const bxy = new Int16Array(buf, off, nBV * 2); off += pad4(nBV * 4);
  const rcls = new Uint8Array(buf, off, nR); off += pad4(nR);
  const rcnt = new Uint16Array(buf, off, nR); off += pad4(nR * 2);
  const rxy = new Int16Array(buf, off, nRP * 2); off += pad4(nRP * 4);
  const lxy = new Int16Array(buf, off, nL * 2);

  const transfer = [];
  const blocks = [];
  const ring = new Float64Array(512);
  const subSize = chunk / SUB;
  const L = LOD_SIZES.length;
  let b = 0, v = 0;

  // ---- buildings: one block per 500 m sub-tile, largest buildings first ----
  for (let s = 0; s < SUB * SUB; s++) {
    const n = sub[s];
    if (!n) continue;
    let ringVerts = 0;
    for (let i = b; i < b + n; i++) ringVerts += counts[i];
    const rings = new Int32Array(ringVerts * 2);
    const start = new Uint32Array(n);
    const size = new Float32Array(n);
    let rv = 0;
    for (let j = 0; j < n; j++) {
      const i = b + j, c = counts[i];
      start[j] = rv;
      let x = 0, y = 0;
      for (let k = 0; k < c; k++, v++) {
        x += bxy[v * 2]; y += bxy[v * 2 + 1];
        rings[(rv + k) * 2] = x; rings[(rv + k) * 2 + 1] = y;
      }
      let a2 = 0;
      for (let k = 0; k < c; k++) {
        const p = (rv + k) * 2, r = (rv + ((k + 1) % c)) * 2;
        a2 += rings[p] * rings[r + 1] - rings[r] * rings[p + 1];
      }
      size[j] = Math.max(Math.sqrt(Math.abs(a2) / 2) * Q, 0.6 * heights[i] * Q);
      rv += c;
    }
    const order = new Uint32Array(n);
    for (let j = 0; j < n; j++) order[j] = j;
    order.sort((p, r) => size[r] - size[p]);

    let nv = 0, nt = 0;
    for (let i = b; i < b + n; i++) { const c = counts[i]; nv += 2 * c; nt += 3 * c - 2; }
    const pos = new Int16Array(nv * 3);
    const tone = new Uint8Array(nv);
    let idx = nv > 65535 ? new Uint32Array(nt * 3) : new Uint16Array(nt * 3);
    const lod = new Uint32Array(L);
    let vi = 0, ii = 0;
    for (let oj = 0; oj < n; oj++) {
      const j = order[oj], i = b + j, c = counts[i], h = heights[i];
      for (let k = 0; k < c; k++) { ring[k * 2] = rings[(start[j] + k) * 2]; ring[k * 2 + 1] = rings[(start[j] + k) * 2 + 1]; }
      const t = ((i * 2654435761) >>> 24) & 255;
      const base = vi;
      for (let k = 0; k < c; k++) {
        const px = ring[k * 2], pz = -ring[k * 2 + 1];
        let o = (base + k) * 3;
        pos[o] = px; pos[o + 1] = 0; pos[o + 2] = pz;
        o = (base + c + k) * 3;
        pos[o] = px; pos[o + 1] = h; pos[o + 2] = pz;
        tone[base + k] = t; tone[base + c + k] = t;
      }
      vi += 2 * c;
      for (let k = 0; k < c; k++) {
        const kn = (k + 1) % c;
        idx[ii++] = base + k; idx[ii++] = base + kn; idx[ii++] = base + c + kn;
        idx[ii++] = base + k; idx[ii++] = base + c + kn; idx[ii++] = base + c + k;
      }
      const top = base + c;
      if (c === 3) {
        idx[ii++] = top; idx[ii++] = top + 1; idx[ii++] = top + 2;
      } else {
        const tris = earcut(ring.subarray(0, c * 2), null, 2);
        for (let k = 0; k < tris.length; k += 3) {
          const a = tris[k], bb = tris[k + 1], cc = tris[k + 2];
          const cross = (ring[bb * 2] - ring[a * 2]) * (ring[cc * 2 + 1] - ring[a * 2 + 1]) - (ring[bb * 2 + 1] - ring[a * 2 + 1]) * (ring[cc * 2] - ring[a * 2]);
          idx[ii++] = top + a;
          if (cross >= 0) { idx[ii++] = top + bb; idx[ii++] = top + cc; } else { idx[ii++] = top + cc; idx[ii++] = top + bb; }
        }
      }
      for (let l = 0; l < L; l++) if (size[j] >= LOD_SIZES[l]) lod[l] = ii;
    }
    if (ii !== idx.length) idx = idx.slice(0, ii);    // earcut skips degenerate roofs
    b += n;
    const sx = s % SUB, sy = Math.floor(s / SUB);
    blocks.push({
      s, pos, tone, idx, lod, count: n,
      cx: -chunk / 2 + (sx + 0.5) * subSize, cy: -chunk / 2 + (sy + 0.5) * subSize,
    });
    transfer.push(pos.buffer, tone.buffer, idx.buffer, lod.buffer);
  }

  // ---- roads: ribbons, widest classes drawn last ---------------------------
  let roads = null;
  if (nR) {
    const starts = new Uint32Array(nR);
    let acc = 0, segs = 0, pts = 0;
    for (let i = 0; i < nR; i++) { starts[i] = acc; acc += rcnt[i]; pts += rcnt[i]; segs += rcnt[i] - 1; }
    const order = Array.from({ length: nR }, (_, i) => i).sort((a, c2) => ROAD_PRIORITY[rcls[a]] - ROAD_PRIORITY[rcls[c2]]);
    const pos = new Float32Array(pts * 2 * 3);
    const cls = new Uint8Array(pts * 2);
    let idx = new Uint32Array(segs * 6);
    const px = new Float64Array(1024), pz = new Float64Array(1024);
    let vi = 0, ii = 0;
    for (const i of order) {
      const c = Math.min(rcnt[i], 1024), k0 = starts[i], code = rcls[i];
      const hw = ROAD_WIDTH[code] / 2;
      let x = 0, y = 0;
      for (let k = 0; k < c; k++) {
        x += rxy[(k0 + k) * 2]; y += rxy[(k0 + k) * 2 + 1];
        px[k] = x * Q; pz[k] = -y * Q;
      }
      // butt ends are pushed out a little so joins between roads close up
      const ext = hw * 0.6;
      for (let k = 0; k < c; k++) {
        let dx1 = 0, dz1 = 0, dx2 = 0, dz2 = 0;
        if (k > 0) { dx1 = px[k] - px[k - 1]; dz1 = pz[k] - pz[k - 1]; const l = Math.hypot(dx1, dz1) || 1; dx1 /= l; dz1 /= l; }
        if (k < c - 1) { dx2 = px[k + 1] - px[k]; dz2 = pz[k + 1] - pz[k]; const l = Math.hypot(dx2, dz2) || 1; dx2 /= l; dz2 /= l; }
        if (k === 0) { dx1 = dx2; dz1 = dz2; }
        if (k === c - 1) { dx2 = dx1; dz2 = dz1; }
        let tx = dx1 + dx2, tz = dz1 + dz2;
        const tl = Math.hypot(tx, tz);
        if (tl < 1e-6) { tx = dx2; tz = dz2; } else { tx /= tl; tz /= tl; }
        const nx = -tz, nz = tx;
        const miter = hw / Math.max(0.45, nx * -dz2 + nz * dx2);
        let cxp = px[k], czp = pz[k];
        if (k === 0) { cxp -= dx2 * ext; czp -= dz2 * ext; }
        if (k === c - 1) { cxp += dx1 * ext; czp += dz1 * ext; }
        pos[vi * 3] = cxp + nx * miter; pos[vi * 3 + 1] = 0; pos[vi * 3 + 2] = czp + nz * miter;
        pos[vi * 3 + 3] = cxp - nx * miter; pos[vi * 3 + 4] = 0; pos[vi * 3 + 5] = czp - nz * miter;
        cls[vi] = code; cls[vi + 1] = code;
        if (k < c - 1) {
          idx[ii++] = vi; idx[ii++] = vi + 1; idx[ii++] = vi + 2;
          idx[ii++] = vi + 1; idx[ii++] = vi + 3; idx[ii++] = vi + 2;
        }
        vi += 2;
      }
    }
    if (ii !== idx.length) idx = idx.slice(0, ii);
    roads = { pos, cls, idx };
    transfer.push(pos.buffer, cls.buffer, idx.buffer);
  }

  // ---- mapped street lamps --------------------------------------------------
  let lamps = null;
  if (nL) {
    lamps = new Float32Array(nL * 3);
    for (let i = 0; i < nL; i++) { lamps[i * 3] = lxy[i * 2] * Q; lamps[i * 3 + 1] = 7; lamps[i * 3 + 2] = -lxy[i * 2 + 1] * Q; }
    transfer.push(lamps.buffer);
  }

  return { blocks, roads, lamps, ms: performance.now() - t0, transfer };
}
