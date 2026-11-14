"""Score returned walking-route alternatives using available local evidence."""

import math

from core.risk_engine import DEFAULT_WEIGHTS, calculate_risk


CORRIDOR_METERS = 50.0


def _point_to_segment_meters(point, start, end):
    latitude = point[0]
    longitude_scale = math.cos(math.radians(latitude))
    x1 = start[1] * 111320.0 * longitude_scale
    y1 = start[0] * 110574.0
    x2 = end[1] * 111320.0 * longitude_scale
    y2 = end[0] * 110574.0
    px = point[1] * 111320.0 * longitude_scale
    py = point[0] * 110574.0
    dx, dy = x2 - x1, y2 - y1
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.hypot(px - x1, py - y1)
    fraction = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / denominator))
    return math.hypot(px - (x1 + fraction * dx), py - (y1 + fraction * dy))


def _distance_to_route_meters(latitude, longitude, coordinates):
    point = (latitude, longitude)
    route_points = [(coordinate[1], coordinate[0]) for coordinate in coordinates]
    return min(
        _point_to_segment_meters(point, first, second)
        for first, second in zip(route_points, route_points[1:])
    )


def _nearby_layer_values(route, observations):
    geometry = route["geometry"]["coordinates"]
    return [
        observation["value"]
        for observation in observations
        if _distance_to_route_meters(
            observation["latitude"], observation["longitude"], geometry
        ) <= CORRIDOR_METERS
    ]


def score_route_alternatives(routes, layers, confirmed_hazards, beta, weights=None):
    """Score each alternative; unknown source layers increase uncertainty."""
    if not isinstance(beta, (int, float)) or not math.isfinite(beta) or beta < 0:
        raise ValueError("Safety preference must be a finite, non-negative number.")
    if not routes:
        raise ValueError("At least one walking-route alternative is required.")
    weights = DEFAULT_WEIGHTS if weights is None else weights
    scored = []
    raw_incident_rates = []

    for route in routes:
        incident_values = _nearby_layer_values(
            route, layers.get("historical_incidents", [])
        )
        raw_incident_rates.append(
            sum(incident_values) / max(route["distance"] / 1000.0, 0.1)
        )

    maximum_incident_rate = max(raw_incident_rates, default=0.0)
    for index, route in enumerate(routes):
        lighting_values = _nearby_layer_values(route, layers.get("lighting", []))
        isolation_values = _nearby_layer_values(route, layers.get("isolation", []))
        hazard_values = [
            hazard for hazard in confirmed_hazards
            if hazard.get("latitude") is not None
            and hazard.get("longitude") is not None
            and _distance_to_route_meters(
                hazard["latitude"],
                hazard["longitude"],
                route["geometry"]["coordinates"],
            ) <= CORRIDOR_METERS
        ]
        preview_values = [hazard for hazard in hazard_values if hazard.get("preview")]
        missing_layers = sum(
            not values for values in (
                _nearby_layer_values(route, layers.get("historical_incidents", [])),
                lighting_values,
                isolation_values,
            )
        )
        factors = {
            "historical_incidents": (
                raw_incident_rates[index] / maximum_incident_rate
                if maximum_incident_rate > 0 else 0.0
            ),
            "lighting_deficiency": (
                sum(lighting_values) / float(len(lighting_values)) if lighting_values else 0.0
            ),
            "live_hazards": (
                1.0 if preview_values
                else min(1.0, len(hazard_values) / 2.0)
            ),
            "isolation": (
                sum(isolation_values) / float(len(isolation_values))
                if isolation_values else 0.0
            ),
            "uncertainty": missing_layers / 3.0,
        }
        synthetic_zones = layers.get("synthetic_zones", [])
        coverage = None
        if synthetic_zones:
            from core.synthetic_routing import synthetic_route_factors
            synthetic_factors, coverage = synthetic_route_factors(route, synthetic_zones)
            factors.update(synthetic_factors)
            # Synthetic placement provides no verified real-world confidence.
            factors["uncertainty"] = 1.0
        result = dict(route)
        result["risk_source"] = "synthetic" if synthetic_zones else "observations"
        if synthetic_zones:
            result["synthetic_coverage"] = coverage
        result["risk_factors"] = factors
        result["risk_indicator"] = calculate_risk(factors, weights)
        result["generalized_cost"] = route["distance"] * (
            1.0 + beta * result["risk_indicator"]
        )
        scored.append(result)
    return scored


def choose_route_pair(scored_routes):
    """Return the shortest and beta-preferred candidates."""
    shortest = min(scored_routes, key=lambda route: route["distance"])
    preferred = min(scored_routes, key=lambda route: route["generalized_cost"])
    return shortest, preferred
