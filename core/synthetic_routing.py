"""Illustrative routing zones from synthetic area-month records, never real geography."""
import math

from core.incident_model import evaluate_dataset, predict
from core.risk_engine import DEFAULT_WEIGHTS, calculate_risk

KERNEL_BANDWIDTH = 0.24


def zone_profiles(payload):
    result = evaluate_dataset(payload)
    model = result["models"][result["selected_model"]]
    latest = max(row["month"] for row in payload["records"])
    rows = sorted((r for r in payload["records"] if r["month"] == latest), key=lambda r: r["area_id"])
    rates = [float(mu / row["exposure_k_ped_hours"]) for row, mu in zip(rows, predict(model, rows))]
    maximum = max(rates) or 1.0
    return [{"area_id": row["area_id"], "month": latest,
             "historical_incidents": rate / maximum,
             "lighting_deficiency": 1.0 - row["lighting_score"],
             "isolation": row["isolation_score"], "predicted_rate": rate,
             "source": "synthetic", "model": result["selected_model"]}
            for row, rate in zip(rows, rates)]


def build_demo_zones(profiles, routes):
    """Lay out placeholder zones over the planning extent, not real neighbourhoods."""
    points = [p for route in routes for p in route["geometry"]["coordinates"]]
    if not points:
        return []
    latitude = sum(p[1] for p in points) / len(points)
    pad_lat = 80 / 110574.0
    pad_lon = 80 / (111320.0 * max(0.01, math.cos(math.radians(latitude))))
    west, east = min(p[0] for p in points) - pad_lon, max(p[0] for p in points) + pad_lon
    south, north = min(p[1] for p in points) - pad_lat, max(p[1] for p in points) + pad_lat
    columns = math.ceil(len(profiles) / 2)
    zones = []
    for i, profile in enumerate(profiles):
        row, column = divmod(i, columns)
        zones.append({**profile, "bounds": [west + (east-west)*column/columns,
                     south + (north-south)*row/2,
                     west + (east-west)*(column+1)/columns,
                     south + (north-south)*(row+1)/2]})
    # Keep anchors fixed while comparing bypasses outside the original path's box.
    extra_lat=1500/110574.0
    extra_lon=1500/(111320*max(0.01,math.cos(math.radians(latitude))))
    for zone in zones:
        zone["coverage_bounds"]=[west-extra_lon,south-extra_lat,east+extra_lon,north+extra_lat]
    return zones


def contains(zone, lon, lat):
    west, south, east, north = zone["bounds"]
    return west <= lon <= east and south <= lat <= north


def route_zone_weights(route, zones):
    """Integrate the length of each polyline segment inside each demo rectangle."""
    weights = [0.0] * len(zones)
    total = 0.0
    for start, end in zip(route["geometry"]["coordinates"], route["geometry"]["coordinates"][1:]):
        dx, dy = end[0]-start[0], end[1]-start[1]
        length = math.hypot(dx*111320*math.cos(math.radians((start[1]+end[1])/2)), dy*110574)
        total += length
        cuts = {0.0, 1.0}
        for zone in zones:
            west, south, east, north = zone["bounds"]
            if dx:
                cuts.update(t for edge in (west,east) if 0 < (t := (edge-start[0])/dx) < 1)
            if dy:
                cuts.update(t for edge in (south,north) if 0 < (t := (edge-start[1])/dy) < 1)
        cuts = sorted(cuts)
        for a, b in zip(cuts, cuts[1:]):
            mid = (a+b)/2
            lon, lat = start[0]+dx*mid, start[1]+dy*mid
            owner = next((i for i,z in enumerate(zones) if contains(z,lon,lat)), None)
            if owner is not None:
                weights[owner] += length*(b-a)
    return [w/total if total else 0.0 for w in weights]


def field_contains(latitude,longitude,zones):
    return any(contains({"bounds":zone.get("coverage_bounds",zone["bounds"])},longitude,latitude) for zone in zones)


def risk_field_definition():
    return {"bandwidth_fraction":KERNEL_BANDWIDTH,"weights":dict(DEFAULT_WEIGHTS),"uncertainty":1.0}


def kernel_factors(latitude, longitude, zones):
    """Unnormalised Gaussian risk contributions shared with the displayed surface."""
    totals={name:0.0 for name in ("historical_incidents","lighting_deficiency","isolation")}
    for zone in zones:
        west,south,east,north=zone["bounds"]
        sx=max((east-west)*KERNEL_BANDWIDTH,1e-8)
        sy=max((north-south)*KERNEL_BANDWIDTH,1e-8)
        distance=((longitude-(west+east)/2)/sx)**2+((latitude-(south+north)/2)/sy)**2
        contribution=math.exp(-0.5*distance)
        for name in totals:totals[name]+=contribution*zone[name]
    return {name:min(1.0,value) for name,value in totals.items()}


def point_risk(latitude,longitude,zones):
    values=kernel_factors(latitude,longitude,zones) if field_contains(latitude,longitude,zones) else {name:1.0 for name in ("historical_incidents","lighting_deficiency","isolation")}
    return calculate_risk({**values,"live_hazards":0.0,"uncertainty":1.0})


def synthetic_route_factors(route, zones):
    """Length-weighted smooth kernel field; uncovered ground remains unknown."""
    totals = {name:0.0 for name in ("historical_incidents","lighting_deficiency","isolation")}
    total_length=0.0
    covered_length=0.0
    for start,end in zip(route["geometry"]["coordinates"],route["geometry"]["coordinates"][1:]):
        dx,dy=end[0]-start[0],end[1]-start[1]
        length=math.hypot(dx*111320*math.cos(math.radians((start[1]+end[1])/2)),dy*110574)
        count=max(1,math.ceil(length/25))
        for i in range(count):
            fraction=(i+0.5)/count
            lon,lat=start[0]+dx*fraction,start[1]+dy*fraction
            covered=field_contains(lat,lon,zones)
            if covered:covered_length+=length/count
            values=kernel_factors(lat,lon,zones) if covered else {name:1.0 for name in totals}
            for name in totals:
                totals[name]+=values[name]*length/count
        total_length+=length
    coverage=min(1.0,covered_length/total_length) if total_length else 0.0
    if coverage <= 1e-12:
        return {name:1.0 for name in totals},0.0
    return {name:min(1.0,value/total_length) if total_length else 1.0 for name,value in totals.items()},coverage
