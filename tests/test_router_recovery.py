import io
import json
import unittest
from urllib.error import URLError
from unittest.mock import patch

from core.osm_router import get_route_alternatives, RoutingServiceError


class RouterRecoveryTests(unittest.TestCase):
    def test_temporary_connection_failure_is_retried(self):
        payload = {"code": "Ok", "routes": [{"distance": 100,
                   "geometry": {"coordinates": [[77, 12], [77.001, 12.001]]}}]}
        response = io.BytesIO(json.dumps(payload).encode())
        with patch("core.osm_router.urlopen", side_effect=[URLError("TLS reset"), response]) as fetch:
            routes = get_route_alternatives((12,77), (12.001,77.001))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(routes[0]["distance"], 100)

    def test_primary_outage_uses_pedestrian_fallback(self):
        payload = {"code": "Ok", "routes": [{"distance": 100,
                   "geometry": {"coordinates": [[77, 12], [77.001, 12.001]]}}]}
        with patch("core.osm_router.urlopen", side_effect=[
                URLError("TLS reset"), URLError("TLS reset"),
                io.BytesIO(json.dumps(payload).encode())]) as fetch:
            get_route_alternatives((12,77), (12.001,77.001))
        request = fetch.call_args.args[0]
        self.assertIn("valhalla1.openstreetmap.de", request.full_url)
        self.assertEqual(json.loads(request.data)["costing"], "pedestrian")

    def test_persistent_failure_has_bounded_retries(self):
        with patch("core.osm_router.urlopen", side_effect=URLError("offline")) as fetch:
            with self.assertRaises(RoutingServiceError):
                get_route_alternatives((12,77), (12.001,77.001))
        self.assertEqual(fetch.call_count, 3)
