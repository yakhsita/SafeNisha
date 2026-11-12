# SafeNight —  Adaptive Urban Safety & Risk-Aware Path Mapping 

**Project:** Risk-aware pedestrian navigation with historical incident modeling, walking-network routing, community hazard reporting, and optional Guardian Mode.  
**Team:** Four members (Member 1 = technical lead).  
**Design decision:** **Aurora AI-Native (#9)**, adapted into **responsive laptop and mobile layouts**.  
**Goal:** A reliable, demonstrable MVP, not a promise of guaranteed personal safety.

## 1. What we are building

SafeNight compares a shortest walking route against a route that trades off walking distance and **estimated incident-related risk**. It uses OpenStreetMap pedestrian graphs, historical incident/environmental data where suitable, a Poisson count model where training data permits, and user-submitted infrastructure hazard reports. Users can adjust the distance–risk tradeoff, inspect route explanations, and optionally access contact-sharing/SOS features.

**Core routing cost:** `cost(e) = length_m(e) * (1 + beta * risk_indicator(e))`, with `beta >= 0` and `risk_indicator` in `[0,1]`. Dijkstra minimizes the sum of edge costs. `beta=0` gives shortest-distance routing. Risk scores are estimates, **not guarantees of safety**.

**Statistical model (only if data supports it):** `lambda = exposure * exp(b0 + b1*past_incidents + b2*lighting + b3*hazards + b4*isolation)`. The coefficients are learned from historical observations and validated on later time periods. Poisson predictions are not automatically individual safety probabilities. If incident records are only ward-level, do not present street-level predictions as observed facts. If the data is insufficient, show a clearly labeled synthetic demonstration and explain the limitation.

## 2. Roles and exact assignments

### (Yakhsita) Member 1 — Technical Lead 

**Owns:** `core/`, integration into `app.py`, routing and prediction evaluation.

**Before hackathon:**
1. Inspect existing Python code and `koramangala_roads.graphml`; identify reusable graph loading, shortest-path, edge updates and visualization functions.
2. Verify whether the existing graph is a **walking** graph. If not, obtain a pedestrian-accessible OSMnx graph and save `data/koramangala_walk.graphml`.
3. Implement graph loading, coordinate-to-node snapping, shortest-distance routing, and tests.
4. Define the interfaces described below so teammates can code independently.
5. Investigate actual incident dataset availability, geographic resolution, time periods and exposure data before claiming Poisson model accuracy.

**During hackathon:**
1. Implement and validate Poisson regression if suitable data is available; compare with a historical-rate baseline. Consider Negative Binomial if overdispersion is significant.
2. Map supported risk estimates to appropriate geographic units and walking edges without false street-level precision.
3. Implement `cost = length_m * (1 + beta * risk_indicator)` and calculate shortest vs risk-aware routes.
4. Integrate hazard reporting; apply validated updates to edge features, recalculate cost and reroute. Avoid double-counting a broken light as two independent events.
5. Integrate UI, test edge cases, prepare working demo and backup outputs.

**Expected files:** `core/graph_loader.py`, `core/routing.py`, `core/risk_engine.py`, `core/incident_model.py`, `app.py`, `tests/test_routing.py`.

### (Shreya) Member 2 — UI/UX + beginner frontend programming

**Before hackathon:**
1. Create a Figma file with **laptop and mobile versions** of the same SafeNight interface.
2. Prepare five screen designs, in this priority order:
   - **Home/Search:** source, destination, `Faster ↔ Lower estimated risk` beta slider, map, SOS shortcut.
   - **Route Comparison:** shortest and lower-risk cards, route distance, estimated walking time, estimated risk indicator, color-coded map legend.
   - **Report Hazard:** location, category, optional description, submit and success/error states.
   - **Risk Atlas (optional):** risk overlay and clear legend; time-of-night slider **only if time-specific data exists**.
   - **Guardian Mode (optional):** trusted-contact sharing and explicit pause/stop controls.
3. Produce a design system: colors, fonts, button states, spacing, icons, loading and error states.
4. Create an interactive Figma prototype showing Home → Route Comparison → Report Hazard.
5. Export only necessary assets and record a 30–60 second walkthrough of desktop and mobile designs.

(Sanvi's design style implementation): Use **Aurora AI-Native (#9)** as the primary theme from the shared frontend references. Borrow **Transit Line (#2)** for clear route lines and markers, and **Risk Atlas (#4)** for an optional risk overlay.

**Colors:** Midnight `#0A0F1F`, Indigo `#4F46E5`, Cyan `#22D3EE`, Magenta `#E879F9`, Mist `#E5E7EB`. Route line: readable green/teal; hazards: orange/coral; ensure good contrast and a clear legend. **Typography:** Sora for headings and Inter for body (or practical available alternatives).

**Responsive requirement:** One application, **two design frames**:
- **Laptop:** 1440 × 900 reference frame; wide map with side panel, route comparison cards and persistent controls.
- **Mobile:** 390 × 844 reference frame; stacked search and route cards, map occupying most of the screen, easy-to-tap controls, visible SOS access, no horizontal overflow.

Do not assume Streamlit automatically provides native turn-by-turn navigation, continuous background tracking, or an accurate 3D digital twin.

**Programming assignment:** Build `ui/frontend_demo.py` using Streamlit and placeholder data. Include `st.text_input` (source/destination), `st.slider` (beta), `st.columns` (route cards), `st.selectbox` (hazard type), and basic CSS in `ui/styles.py`. **No routing math or database changes.**

**Input contract (dummy example):**
```python
route_result = {
    "shortest": {"distance_m": 850, "risk_indicator": 0.42},
    "lower_risk": {"distance_m": 1100, "risk_indicator": 0.25}
}
```
These are illustrative values, not verified SafeNight predictions. Show a “Data limited” state when risk information is unavailable.

**During hackathon:** Implement responsive refinements, connect real route data supplied by Member 1, fix map/card readability, test on laptop and mobile browser widths. **Stretch:** explore pydeck 3D in an isolated demo only after MVP works.

**Deliverables:** Figma link, laptop and mobile frames, design assets, `ui/frontend_demo.py`, `ui/styles.py`, screenshots and brief handoff notes.

**Acceptance checklist:** laptop and mobile readable; controls visible; two routes distinguishable without relying only on color; no fake “AI explanation” claims (route explanation is computed from actual route features); no broken buttons in final demo.

### (Shamitha) Member 3 — Research, judge Q&A, pitch + beginner data programming

**Before hackathon:**
1. Research the problem and audience; cite credible sources. Compare SafeNight with existing navigation/safety apps without claiming “first ever.”
2. Investigate potential Bengaluru/Karnataka incident datasets; record **source URL, publisher, license, coverage years, geographic resolution, incident types, last update, and limitations** in `data/incident_data_sources.md`.
3. Draft problem/solution, novelty, social impact, technical stack and limitations for slides.
4. Prepare a **30-second opener, 3-minute pitch, 5-minute detailed pitch**, and a step-by-step live demo narration.
5. Prepare backup screenshots and a short FAQ with evidence-backed answers. Do not present proposed features as implemented.

**Programming assignment:** Build `data/validate_incidents.py` using Pandas. It should load a CSV, check required columns, report missing/duplicate rows, parse dates, reject negative or non-integer incident counts, summarize counts by area and month, and write a cleaned file. Do **not** invent values for missing data or convert area totals into individual street observations.

**Suggested sample schema:** `area_id,month,incident_count`. A clearly labeled synthetic CSV can be used for testing the validator; do not present it as police data.

**During hackathon:** Test the app using a checklist (valid route, no route, invalid destination, beta=0, new hazard, resolved hazard, missing location permission, narrow mobile screen), log bugs with reproduction steps, finalize pitch and rehearse Q&A.

**Deliverables:** `data/validate_incidents.py`, `data/incident_data_sources.md`, pitch deck, 30-second/3-minute/5-minute scripts, `docs/judge_qa.md`, `docs/demo_script.md`, `docs/test_results.md`.

#### Judge question bank — prepare short answers

**Problem and innovation:**
1. What problem are you solving, and for whom?
2. Why is shortest-path navigation insufficient?
3. How is SafeNight different from Google Maps and existing safety apps?
4. What is genuinely novel about your integration?
5. What is your evidence of user need?
6. Why did you choose a pedestrian rather than driving network?

**Architecture and algorithms:**
7. Walk us through the full architecture.
8. Why OSMnx, NetworkX and OpenStreetMap?
9. What are nodes, edges and edge costs?
10. Why Dijkstra; how does it differ from A*?
11. What does beta mean, and what happens when beta=0?
12. How is risk incorporated into route cost?
13. What happens if the lower-risk route is much longer?
14. How do you handle disconnected or blocked paths?
15. How do new hazard reports change a route?

**Poisson and data quality:**
16. Why Poisson Regression rather than arbitrary weights?
17. What exactly is your prediction target?
18. What does the exposure offset represent?
19. Where did you obtain historical incidents, and what years do they cover?
20. Are the records geolocated to streets or aggregated by area?
21. How do you avoid falsely assigning ward-level incidents to streets?
22. How do you prevent data leakage across time?
23. What evaluation metric and baseline did you use?
24. What if Poisson is overdispersed? Why not Negative Binomial?
25. Do incident counts prove a street is dangerous?
26. How do reporting bias and pedestrian volume affect results?
27. How is lighting data obtained, and what if it is missing?
28. Is your route explanation generated by AI or by deterministic logic?

**Safety, privacy and trust:**
29. Can SafeNight guarantee a safe route?
30. How do you moderate false or malicious reports?
31. How do you expire outdated hazards?
32. How do you prevent stigmatizing neighbourhoods?
33. What location data do you store, and for how long?
34. Can users stop sharing their location?
35. Does SOS automatically call police or notify contacts?
36. Does Guardian Mode work in the background if the browser is closed?

**Demo and future work:**
37. What works right now versus what is only designed?
38. Can you show shortest versus risk-aware routing live?
39. Can you show a hazard update causing rerouting?
40. What code was reused from Route Resilience?
41. What are the current technical limitations?
42. How would you expand beyond Koramangala?
43. How would you evaluate real-world effectiveness ethically?
44. What would you build next with more time?

### (Sanvi) Member 4 — Hazard reporting backend + simple UI

**Owns:** `hazards/` only, plus hazard tests.

**Before hackathon:**
1. Create SQLite table `hazards` with fields: `id INTEGER PRIMARY KEY`, `latitude REAL`, `longitude REAL`, `hazard_type TEXT`, `description TEXT`, `reported_at TEXT` (UTC ISO 8601), `status TEXT`, `expires_at TEXT` (nullable).
2. Allowed hazard types: `broken_streetlight`, `dark_path`, `blocked_footpath`, `other_infrastructure`.
3. Allowed statuses: `new`, `confirmed`, `resolved`.
4. Validate latitude [-90,90], longitude [-180,180], hazard type, description length, and status. Use **parameterized SQL**, not string concatenation.
5. Implement functions:
```python
add_hazard(latitude, longitude, hazard_type, description="")
get_active_hazards()
get_hazards_nearby(latitude, longitude, radius_m=500)
update_hazard_status(hazard_id, status)
```
6. Use Haversine distance for nearby queries; filter out resolved and expired reports. Make a simple test file with temporary SQLite DB so tests do not overwrite the real DB.
7. Build `hazards/hazard_form.py` with Streamlit category selector, coordinates, description, submit button and recent reports table. A map-click location picker is a later integration step.

**Output contract (example only):**
```python
{
    "id": 17,
    "latitude": 12.9345,
    "longitude": 77.6251,
    "hazard_type": "broken_streetlight",
    "description": "Lamp not working",
    "reported_at": "2026-11-13T10:30:00Z",
    "status": "new",
    "expires_at": None
}
```

**During hackathon:** Demonstrate submit → persist → retrieve → resolve. Integrate with Member 1 through the agreed functions. **Member 1** handles snapping to walking edges and risk recalculation. Do not automatically treat unverified reports as confirmed incidents.

**Deliverables:** `hazards/__init__.py`, `hazards/database.py`, `hazards/hazard_service.py`, `hazards/hazard_form.py`, `hazards/test_hazards.py`, short run instructions.

**Acceptance checklist:** valid report saved, invalid coordinates rejected, active filter works, resolved/expired reports excluded, nearby query works, data survives app restart, and tests pass.

## 4. Suggested repository layout

```text
SafeNight/
├── app.py
├── core/
│   ├── graph_loader.py
│   ├── routing.py
│   ├── risk_engine.py
│   └── incident_model.py
├── data/
│   ├── validate_incidents.py
│   ├── incident_data_sources.md
│   ├── koramangala_walk.graphml
│   └── safenight.db
├── hazards/
│   ├── __init__.py
│   ├── database.py
│   ├── hazard_service.py
│   ├── hazard_form.py
│   └── test_hazards.py
├── ui/
│   ├── frontend_demo.py
│   ├── styles.py
│   └── assets/
├── docs/
│   ├── judge_qa.md
│   ├── demo_script.md
│   └── test_results.md
├── tests/
│   └── test_routing.py
├── requirements.txt
└── README.md
```

## 5. Coordination and Git rules

1. Use separate branches: `feature/frontend`, `feature/research-data`, `feature/hazards`, and `feature/core-routing`.
2. Each member edits their own module and opens a pull request (or sends a tested ZIP if Git is unavailable).
3. Agree on function names and return data shapes before integration.
4. Never commit private location histories, phone numbers, secrets, API keys, or real sensitive personal data.
5. Each handoff must include **what works, how to run it, screenshots/output, and known limitations**.
6. Merge and run the full app periodically; do not wait until the final hours.

## 6. Timeline

| Stage | Member 1 | Member 2 | Member 3 | Member 4 |
|---|---|---|---|---|
| Before event | Inspect/reuse Route Resilience; verify walk graph | Figma desktop + mobile; dummy Streamlit UI | Dataset/source research; judge Q&A; validator | SQLite schema; CRUD; tests |
| Hours 0–6 | Shortest route on real graph | Responsive dashboard | Verify data and test plan | Hazard form |
| Hours 6–12 | Risk costs; Poisson if suitable data | Route cards and explanation UI | Pitch draft and data checks | Nearby search and tests |
| Hours 12–18 | Integrate dynamic rerouting | Connect real route results | QA and screenshots | Connect hazard service |
| Hours 18–21 | Fix integration bugs | Mobile and laptop testing | Rehearse pitch | Database reliability |
| Hours 21–24 | Freeze and verify MVP | Final visual polish | Pitch and submission | Support demo |

## 7. MVP vs stretch goals

**Must work:** pedestrian graph; shortest route; risk-aware route using clearly described estimated indicators; map with two routes; hazard submission/storage; route recalculation after a report; responsive dashboard.

**Conditional on data:** fitted and validated Poisson model; historical incident overlay; calibrated route explanations. If unsuitable data, explicitly demonstrate a synthetic model rather than claim predictive accuracy.

**Stretch:** time-of-night risk slider, 3D map, full navigation, persistent Guardian Mode, automated contact alerts, civic dashboard.

## 8. Safety, honesty and presentation rules

- Call routes **“lower estimated risk”**, not “safe” or “safest.”
- Do not interpret missing `lit` tags as proof that a road is unlit.
- Nearby shops do not prove current footfall; historical incidents do not establish danger on every street in an area.
- Clearly distinguish **actual datasets**, **synthetic test data**, **working features**, and **future plans**.
- An SOS link to `tel:112` opens the dialer where supported; it does not by itself send a verified alert.
- Do not promise automatic contact notifications or background tracking unless those are actually implemented and tested.
- Make location sharing opt-in, time-limited and easy to stop.

## 9. Final integration checklist

- [ ] Laptop UI and mobile UI match Aurora design and work at narrow widths.
- [ ] Walking graph is verified, not assumed from a driving graph.
- [ ] Shortest route is computed and shown.
- [ ] Risk-aware route changes appropriately when beta changes.
- [ ] Hazard can be submitted, retrieved, resolved and displayed.
- [ ] New valid hazard updates relevant route costs and triggers rerouting.
- [ ] Poisson claims match the data and evaluation actually completed.
- [ ] Empty, missing-data and no-route states are handled.
- [ ] No false SOS/Guardian claims.
- [ ] Pitch, Q&A, screenshots and offline fallback demo are ready.

**Definition of done:** A judge can select a start and destination, compare two routes, adjust beta, submit a hazard, see the relevant route update, and understand which data is real versus simulated.
