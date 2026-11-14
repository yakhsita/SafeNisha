// Background thread for the 3D map: downloads, unpacks and meshes one tile per
// message, then hands the typed arrays back without copying.
import { fetchTile, buildTile } from './tiles.js';

self.onmessage = async ({ data }) => {
  const { id, url, encoding, opts } = data;
  try {
    const t0 = performance.now();
    const buf = await fetchTile(url, encoding);
    const fetchMs = performance.now() - t0;
    const out = buildTile(buf, opts);
    const transfer = out.transfer;
    delete out.transfer;
    out.fetchMs = fetchMs;
    self.postMessage({ id, ok: true, out }, transfer);
  } catch (err) {
    self.postMessage({ id, ok: false, error: String((err && err.message) || err) });
  }
};
