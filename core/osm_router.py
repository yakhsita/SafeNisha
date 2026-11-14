"""Client for the public OpenStreetMap foot-routing service."""

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROUTE_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/driving"
FALLBACK_ROUTE_URL = "https://valhalla1.openstreetmap.de/route"
USER_AGENT = "SafeNisha/0.1 (https://github.com/yakhsita/SafeNisha)"


class RoutingServiceError(ValueError):
    """Raised when the public foot router cannot return valid route geometry."""


def get_route_alternatives(start, destination, timeout=30):
    """Request walking-route alternatives for (latitude, longitude) points."""
    return get_routes([start, destination], alternatives=3, timeout=timeout)


def get_routes(points, alternatives=3, timeout=30, exclude_polygons=None):
    """Request walking routes through ordered (latitude, longitude) waypoints.

    The router only returns alternatives for two-point requests; with via
    points it returns a single route that passes through each waypoint.
    """
    if len(points) < 2:
        raise ValueError("At least two waypoints are required.")
    coordinates = ";".join(
        "{:.6f},{:.6f}".format(longitude, latitude) for latitude, longitude in points
    )
    query = urlencode(
        {
            "alternatives": str(alternatives) if len(points) == 2 else "false",
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
        }
    )
    request = Request(
        "{}/{}?{}".format(ROUTE_URL, coordinates, query),
        headers={"User-Agent": USER_AGENT},
    )
    fallback_request = Request(
        FALLBACK_ROUTE_URL,
        data=json.dumps({
            "locations": [{"lat": lat, "lon": lon} for lat, lon in points],
            "costing": "pedestrian", "format": "osrm", "shape_format": "geojson",
            "alternates": min(alternatives, 2) if len(points) == 2 else 0,
            **({"exclude_polygons": exclude_polygons} if exclude_polygons else {}),
        }).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    # Public routers occasionally reset TLS connections. Retry transport failures
    # without caching a temporary outage as a permanent routing result.
    for attempt in range(3):
        try:
            with urlopen(fallback_request if exclude_polygons or attempt == 2 else request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as error:
            if error.code >= 500 and attempt < 2:
                continue
            raise RoutingServiceError(
                "The public walking router returned HTTP {}.".format(error.code)
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            if attempt < 2:
                continue
            raise RoutingServiceError(
                "Could not reach the walking router. Please retry. {}".format(error)
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RoutingServiceError("The walking router returned invalid JSON.") from error

    if not isinstance(payload, dict):
        raise RoutingServiceError("The walking router returned an unexpected response.")
    if payload.get("code") != "Ok":
        raise RoutingServiceError(
            "The walking router could not find a route: {}.".format(
                payload.get("message", payload.get("code", "unknown error"))
            )
        )
    routes = payload.get("routes", [])
    if not isinstance(routes, list) or not routes:
        raise RoutingServiceError("The walking router returned no route alternatives.")
    for route in routes:
        if not isinstance(route, dict):
            raise RoutingServiceError("The walking router returned an invalid route.")
        geometry_data = route.get("geometry")
        geometry = geometry_data.get("coordinates", []) if isinstance(geometry_data, dict) else []
        distance = route.get("distance")
        valid_geometry = (
            isinstance(geometry, list)
            and len(geometry) >= 2
            and all(
                isinstance(coordinate, (list, tuple))
                and len(coordinate) == 2
                and all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in coordinate
                )
                and -180 <= coordinate[0] <= 180
                and -90 <= coordinate[1] <= 90
                for coordinate in geometry
            )
        )
        if (not valid_geometry or not isinstance(distance, (int, float))
                or not math.isfinite(distance) or distance <= 0):
            raise RoutingServiceError("The walking router returned incomplete route geometry.")
    return routes
