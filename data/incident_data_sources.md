# Incident data sources

Record a source's URL, publisher, license, coverage years, geographic resolution,
incident types, last update, and known limitations before using it.

No real-world incident sources have been verified yet.

## Synthetic routing demonstration

- Source: user-provided `synthetic_area_monthly_overdispersed.json`, copied unchanged into `data/`.
- Provenance: generated simulated counts, not police observations; 192 zone-month records for eight placeholder IDs, 2024?2025.
- Geography: none. The ?Nagarbhavi? metadata does not define coordinates or neighbourhood boundaries.
- Use: models train on 2024 and evaluate on 2025. Routing uses December 2025 covariates and the selected model's exposure-adjusted predictions.
- Spatial placement: an explicitly illustrative set of eight kernel anchors over the current route-planning extent. Placement changes with that extent and does not describe actual streets.
- Scoring: length-weighted Gaussian-smoothed predicted incident rates (normalized across the eight zones), `1-lighting_score`, and `isolation_score`. Simulated hazard counts are model inputs, not confirmed live reports. Real-world uncertainty stays at its maximum.
- Enablement: the JSON dataset directly supplies the route-scoring layers on Plan journey; there is no separate analysis page or CSV-mode switch.
- The three empty CSV templates were removed. The JSON dataset is the active routing source.

The web map displays a relative Gaussian kernel density surface rather than block cells. Anchor positions are simulated and do not identify real neighbourhood boundaries.
