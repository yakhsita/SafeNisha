# Test results

Record test commands, outcomes, environment, and known limitations here.


## Standalone frontend integration - 2026-11-14

- Automated: `python -m unittest discover -s tests` - 38 tests pass.
- Chrome: `python tests/web_integration_check.py` against localhost:8503 with an isolated SQLite database.
- Verified: native map clicks after selecting either field, actual walking paths,
  dataset-based risk and eight zones, exact (unblended) distance/geometry, hazard
  preview, persistent submission, confirmation/resolution, browser geolocation,
  journey start/stop and directions, light/dark theme and a 390 px mobile layout.
- Browser execution reported no JavaScript errors. No test reports were written to
  the user's main hazard database. Guardian/profile remain local previews.


## Report/reroute/address/KDE flow - 2026-11-14

- Automated suite: 46 tests pass, including explicit avoidance of unverified reports,
  beta-zero avoidance, fresh GPS origin requirements, true reverse-departure U-turns,
  departure from a hazard without re-entry, address-search validation/caching, and
  smooth kernel covariates across former block boundaries.
- Chrome checks: real Photon address lookup and endpoint pin; report icon opens the
  report page; draft location edits cause no route requests; Submit saves and returns
  to a full-trip reroute; separate reroute uses a changed live GPS fix; report history
  stays visible despite the planner hazard toggle; density image replaces block cells;
  desktop/mobile render without JavaScript errors.
- Browser tests caught and verified recovery from transient GPS errors. Test reports
  were isolated from the main database.


## Atlas-only hotspots and nearby autocomplete - 2026-11-14

- 49 automated tests pass, including 7 km bounding/radius filtering, expanded nearby
  matches, cached searches, distant-place fallback, and atlas loading before routing.
- Chrome verified automatic suggestions while typing, keyboard selection and dropdown
  dismissal, no heatmap on Plan route or Report hazard, Risk Atlas hotspot rendering,
  plus the existing report-submit, live-GPS reroute and responsive-layout checks.
- Address coverage depends on the geocoder's indexed OpenStreetMap data.


## Pick-first reporting and Risk Atlas routes - 2026-11-14

- Automated suite remains 49 passing tests.
- Chrome verified that the map report button stays on Plan route until a location
  is picked; the exact picked coordinates and selected pin reach the report page,
  with previous reports visible and no draft reroute or endpoint changes.
- Verified red report navigation icon; shortest and preferred paths plus the KDE
  layer in Risk Atlas; report submission, mid-journey rerouting and mobile behavior.


## Screenshot route risk-routing regression - 2026-11-14

- 53 automated tests pass; JavaScript syntax checks pass.
- `tests/exact_route_browser_check.py` verifies the exact supplied endpoints:
  beta 0 selects the shortest route; beta 5 finds a distinct lower-risk path.
- Live services returned six paths. Shortest: 3,855 m / 0.2766 model risk;
  preferred: 3,878 m / 0.1908 model risk.
- Verified varying per-road risk colours, equal-distance card bands, and agreement
  between Python and browser risk values to 1e-10 at all eight anchor centres.
- The full report, pin transfer, GPS reroute, nearby suggestions and mobile browser
  checks also pass with risk-aware alternative generation enabled.

## 3D map on Plan route only - 2026-11-14

- Plan route uses the local Three.js city renderer through `ui/web/js/map3d.js`.
  Report Hazard and Risk Atlas continue to use the existing 2D renderer; KDE
  remains exclusive to Risk Atlas.
- All 53 automated tests pass. JavaScript syntax checks pass for the adapter,
  planner, and city renderer. SHA-256 comparisons confirm every `core/` file
  stayed unchanged during this integration.
- The live screenshot-route regression still returns six candidates: shortest
  3,855 m / 0.2766 risk, preferred 3,878 m / 0.1908 risk. The browser verifies
  that 3D route coordinates come from the server's paths/risk pieces and that
  the preferred line carries varying per-segment risk colours.
- Chrome verifies nearby address suggestions, GPS, hazard picking in the tilted
  map, transfer to the 2D report map, submit-only rerouting, and rerouting from
  an updated mid-journey GPS fix. No JavaScript errors occur.
- A browser with WebGL 2 disabled falls back to the existing 2D Plan map;
  GPS and endpoint markers remain functional.
- Desktop-to-mobile resizing keeps every fitted route point inside the 3D
  viewport, with no horizontal page overflow.
- Initial service checks failed under network restrictions. The live checks
  above passed after restarting the isolated local server with network access.

## Account and profile backend - 2026-11-14

- Added SQLite-backed accounts and profiles, salted PBKDF2 password verification,
  hashed session tokens, seven-day session expiry, logout revocation and login
  rate limiting. Browser authentication uses HttpOnly, SameSite=Strict cookies.
- Eight account tests cover password rejection, duplicate addresses, profile
  persistence across logout/server restart, account isolation, expiry, validation,
  throttling and actual HTTP requests including cross-origin write rejection.
- Main automated suite: 61 passing tests. Existing routing calculations are unchanged.
- `tests/profile_browser_check.py` exercises account creation, login, profile
  editing, contacts, saved safety preference and session restoration against
  a temporary database, independently of the main report/account history.

## Refreshed place activity and mapped lighting - 2026-11-14

- Main automated suite: 76 passing tests. Fifteen environment tests verify weekly
  and overnight schedules, unsupported hours, open/closed isolation changes,
  actual changes in preferred-route ranking, partial lighting coverage, private
  access exclusion, unknown-data fallback, API metadata, nonblocking fetching,
  cache reuse/expiry, failure backoff, response validation and alternate transport.
- `tests/environment_browser_check.py` verifies that initial routes remain visible,
  background evidence updates risk/source labels, outages restore synthetic scores,
  the heatmap stays atlas-only and mobile layout remains within the viewport.
- Live probes to public Overpass servers timed out in this environment; one
  additional documented endpoint failed certificate validation. TLS verification
  was retained. No successful live-data coverage is claimed from these checks;
  scoring behaviour and recovery were verified using controlled test fixtures.
