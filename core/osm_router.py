"""Client for the public OpenStreetMap foot-routing service."""

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROUTE_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/driving"
USER_AGENT = "SafeNisha/0.1 (https://github.com/yakhsita/SafeNisha)"


class RoutingServiceError(ValueError):
    """Raised when the public foot router cannot return valid route geometry."""


def get_route_alternatives(start, destination, timeout=30):
    """Request walking-route alternatives for (latitude, longitude) points."""
    start_lat, start_lon = start
    destination_lat, destination_lon = destination
    coordinates = "{:.6f},{:.6f};{:.6f},{:.6f}".format(
        start_lon, start_lat, destination_lon, destination_lat
    )
    query = urlencode(
        {
            "alternatives": "3",
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
        }
    )
    request = Request(
        "{}/{}?{}".format(ROUTE_URL, coordinates, query),
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RoutingServiceError(
            "The public walking router returned HTTP {}.".format(error.code)
        )
    except URLError as error:
        raise RoutingServiceError(
            "Could not reach the public walking router: {}.".format(error.reason)
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RoutingServiceError("The walking router returned invalid JSON: {}.".format(error))

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
