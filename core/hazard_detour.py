"""Build direct hazard detours without artificial dead-end excursions."""

import math

from core.osm_router import get_routes
from core.route_scoring import CORRIDOR_METERS


FALLBACK_BACKOFF_METERS = 300.0
REQUEST_TIMEOUT_SECONDS = 12


def _xy(latitude, longitude, reference_latitude):
    scale = math.cos(math.radians(reference_latitude))
    return longitude * 111320.0 * scale, latitude * 110574.0


def _meters(first, second):
    """Approximate distance between two (latitude, longitude) points."""
    x1, y1 = _xy(first[0], first[1], first[0])
    x2, y2 = _xy(second[0], second[1], first[0])
    return math.hypot(x2 - x1, y2 - y1)


def _points(route):
    """Route geometry as (latitude, longitude) tuples."""
    return [(lat, lon) for lon, lat in route["geometry"]["coordinates"]]


def _cumulative(points):
    totals = [0.0]
    for first, second in zip(points, points[1:]):
        totals.append(totals[-1] + _meters(first, second))
    return totals


def _project(point, points, cumulative):
    """Return (distance to route, distance along route, segment index)."""
    best = (float("inf"), 0.0, 0)
    for index, (start, end) in enumerate(zip(points, points[1:])):
        x1, y1 = _xy(start[0], start[1], point[0])
        x2, y2 = _xy(end[0], end[1], point[0])
        px, py = _xy(point[0], point[1], point[0])
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        fraction = 0.0 if length_sq == 0 else max(
            0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq)
        )
        distance = math.hypot(px - (x1 + fraction * dx), py - (y1 + fraction * dy))
        if distance < best[0]:
            along = cumulative[index] + fraction * (cumulative[index + 1] - cumulative[index])
            best = (distance, along, index)
    return best


def _clear_of(route, hazards):
    points = _points(route)
    cumulative = _cumulative(points)
    return all(
        _project((hazard["latitude"], hazard["longitude"]), points, cumulative)[0]
        > CORRIDOR_METERS
        for hazard in hazards
    )


def _first_hazard_on_route(points, cumulative, hazards):
    """Return (hazard, along, segment index) for the earliest hazard on the route."""
    hits = []
    for hazard in hazards:
        location = (hazard["latitude"], hazard["longitude"])
        distance, along, index = _project(location, points, cumulative)
        if distance <= CORRIDOR_METERS:
            hits.append((along, index, hazard))
    if not hits:
        return None
    along, index, hazard = min(hits, key=lambda hit: hit[0])
    return hazard, along, index


def _divergence_points(route, points, cumulative, hazard_location, hazard_along):
    """Junctions to leave the route from, nearest to the hazard first.

    The first is the last junction before the hazard that lies outside the
    hazard corridor; the second, used only if the first yields no clear detour,
    is a junction at least FALLBACK_BACKOFF_METERS further back (or the start).
    """
    junctions = [(0.0, points[0])]
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            for intersection in step.get("intersections", []):
                location = intersection.get("location")
                if not location or len(location) != 2:
                    continue
                junction = (location[1], location[0])
                _, along, _ = _project(junction, points, cumulative)
                if (along < hazard_along
                        and _meters(junction, hazard_location) > CORRIDOR_METERS):
                    junctions.append((along, junction))
    junctions.sort(key=lambda item: item[0], reverse=True)
    nearest = junctions[0]
    fallback = next(
        (item for item in junctions
         if item[0] <= nearest[0] - FALLBACK_BACKOFF_METERS),
        junctions[-1],
    )
    return [nearest] if fallback[0] == nearest[0] else [nearest, fallback]


def _hazard_polygons(hazards):
    polygons = []
    for hazard in hazards:
        lat, lon = hazard["latitude"], hazard["longitude"]
        # A small margin keeps accepted geometry outside the scoring corridor.
        radius = CORRIDOR_METERS + 1.0
        ring = [[lon + radius * math.cos(angle * math.pi / 8) /
                 (111320.0 * math.cos(math.radians(lat))),
                 lat + radius * math.sin(angle * math.pi / 8) / 110574.0]
                for angle in range(16)]
        polygons.append(ring + [ring[0]])
    return polygons


def _has_excursion(route):
    """Reject routes that visit a junction twice to reach a forced waypoint."""
    seen = set()
    previous = None
    for lon, lat in route["geometry"]["coordinates"]:
        point = (round(lon, 6), round(lat, 6))
        if point == previous:
            continue
        if point in seen:
            return True
        seen.add(point)
        previous = point
    return False


def _starts_backwards(original, candidate):
    def heading(points):
        origin = points[0]
        for point in points[1:]:
            if _meters(origin, point) > 3.0:
                first = _xy(*origin, origin[0])
                second = _xy(*point, origin[0])
                return (second[0] - first[0], second[1] - first[1])
        return (0.0, 0.0)
    forward = heading(original)
    departure = heading(candidate)
    norm = math.hypot(*forward) * math.hypot(*departure)
    return norm > 0 and sum(a * b for a, b in zip(forward, departure)) / norm < -0.7


def _prefix_steps(route, divergence_along):
    steps = []
    walked = 0.0
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            if walked >= divergence_along:
                return steps
            distance = step.get("distance") or 0.0
            if step.get("maneuver", {}).get("type") == "arrive":
                continue
            trimmed = dict(step)
            trimmed["distance"] = min(distance, divergence_along - walked)
            steps.append(trimmed)
            walked += distance
    return steps


def find_hazard_detour(route, hazards, destination, fetch=get_routes):
    """Leave at an available junction; never approach a hazard just to turn back.

    Avoidance polygons let the walking router find a direct detour without
    artificial via points that can snap to dead ends. U-turns are included only
    when the route actually leaves the current origin in the reverse direction.
    """
    points = _points(route)
    if len(points) < 2:
        return None
    cumulative = _cumulative(points)
    hit = _first_hazard_on_route(points, cumulative, hazards)
    if hit is None:
        return None
    hazard, hazard_along, _ = hit
    hazard_location = (hazard["latitude"], hazard["longitude"])
    for divergence_along, divergence in _divergence_points(
        route, points, cumulative, hazard_location, hazard_along
    ):
        try:
            options = {"alternatives": 3, "timeout": REQUEST_TIMEOUT_SECONDS}
            if fetch is get_routes:
                options["exclude_polygons"] = _hazard_polygons(hazards)
            candidates = fetch([divergence, destination], **options)
        except ValueError:
            continue
        prefix = [point for point, along in zip(points, cumulative)
                  if along < divergence_along] + [divergence]
        clear = []
        for candidate in candidates:
            combined = dict(candidate)
            combined["geometry"] = {"type": "LineString", "coordinates":
                [[lon, lat] for lat, lon in prefix + _points(candidate)[1:]]}
            if _clear_of(combined, hazards) and not _has_excursion(combined):
                clear.append((candidate, combined))
        if not clear:
            continue
        detour, combined = min(clear, key=lambda pair: pair[0]["distance"])
        combined["distance"] = divergence_along + detour["distance"]
        detour_steps = [dict(step) for leg in detour.get("legs", [])
                        for step in leg.get("steps", [])
                        if step.get("maneuver", {}).get("type") != "waypoint"]
        prefix_steps = _prefix_steps(route, divergence_along)
        if prefix_steps and detour_steps and detour_steps[0].get("maneuver", {}).get("type") == "depart":
            # Keep the first detour segment and its distance in the directions.
            detour_steps[0]["maneuver"] = {**detour_steps[0]["maneuver"], "type": "continue"}
        uturn = divergence_along == 0 and _starts_backwards(points, _points(detour))
        if uturn:
            if detour_steps:
                detour_steps[0]["maneuver"] = {**detour_steps[0].get("maneuver", {}),
                                              "type": "turn", "modifier": "uturn"}
            else:
                detour_steps = [{"distance": 0, "maneuver": {"type": "turn", "modifier": "uturn"}}]
        combined["legs"] = [{"steps": prefix_steps + detour_steps}]
        combined["detour"] = {
            "divergence": divergence,
            "turnaround": points[0] if uturn else None,
            "return_geometry": _points(detour)[:2] if uturn else [],
            "hazard": hazard_location,
            "added_meters": combined["distance"] - route["distance"],
        }
        return combined
    return None
