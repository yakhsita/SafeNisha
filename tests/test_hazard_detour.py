import unittest

from core.hazard_detour import _clear_of, find_hazard_detour


# A straight east-west route along latitude 12.9300 with junctions every ~108 m.
LAT = 12.9300
XS = [77.6200, 77.6210, 77.6220, 77.6230, 77.6240]


def _route(coordinates, distance):
    return {
        "distance": distance,
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "legs": [{
            "steps": [
                {
                    "distance": distance,
                    "name": "Main Road",
                    "maneuver": {"type": "depart"},
                    "intersections": [{"location": c} for c in coordinates[:-1]],
                },
                {
                    "distance": 0,
                    "name": "",
                    "maneuver": {"type": "arrive"},
                    "intersections": [{"location": coordinates[-1]}],
                },
            ]
        }],
    }


BASE = _route([[x, LAT] for x in XS], 433.0)
DESTINATION = (LAT, XS[-1])
HAZARD = {"latitude": LAT, "longitude": 77.6225}


class HazardDetourTests(unittest.TestCase):
    def test_detour_takes_available_junction_without_artificial_uturn(self):
        calls = []

        def fetch(waypoints, alternatives=3, timeout=None):
            calls.append(waypoints)
            start = waypoints[0]
            # A loop north around the hazard back to the destination.
            return [_route(
                [[start[1], start[0]], [start[1], 12.9310], [XS[-1], 12.9310],
                 [XS[-1], LAT]],
                420.0,
            )]

        detour = find_hazard_detour(BASE, [HAZARD], DESTINATION, fetch=fetch)

        self.assertIsNotNone(detour)
        self.assertEqual(detour["detour"]["divergence"], (LAT, 77.6220))
        self.assertTrue(all(call[0] == (LAT, 77.6220) for call in calls))
        coordinates = detour["geometry"]["coordinates"]
        self.assertIsNone(detour["detour"]["turnaround"])
        self.assertEqual(coordinates[:3], BASE["geometry"]["coordinates"][:3])
        self.assertEqual(len(coordinates), len(set(map(tuple, coordinates))))
        self.assertFalse(any(s["maneuver"].get("modifier") == "uturn"
                             for s in detour["legs"][0]["steps"]))
        self.assertTrue(_clear_of(detour, [HAZARD]))
        self.assertAlmostEqual(detour["distance"], 217.2 + 420.0, delta=1.0)

    def test_waypoint_dead_end_excursion_is_rejected(self):
        def fetch(waypoints, alternatives=3, timeout=None):
            start = waypoints[0]
            junction = [start[1], 12.9310]
            spur = _route([[start[1], start[0]], junction,
                           [start[1] - 0.0005, 12.9310], junction,
                           [XS[-1], 12.9310], [XS[-1], LAT]], 300)
            direct = _route([[start[1], start[0]], junction,
                             [XS[-1], 12.9310], [XS[-1], LAT]], 420)
            return [spur, direct]
        detour = find_hazard_detour(BASE, [HAZARD], DESTINATION, fetch=fetch)
        self.assertIsNotNone(detour)
        coordinates = detour["geometry"]["coordinates"]
        self.assertEqual(len(coordinates), len(set(map(tuple, coordinates))))
        self.assertAlmostEqual(detour["distance"], 217.2 + 420, delta=1)

    def test_actual_reverse_departure_keeps_uturn_guidance(self):
        origin = [XS[0], LAT]
        route = _route([origin, [XS[-1], LAT]], 433)
        def fetch(waypoints, alternatives=3, timeout=None):
            return [_route([origin, [XS[0] - 0.001, LAT],
                            [XS[0] - 0.001, 12.9310],
                            [XS[-1], 12.9310], [XS[-1], LAT]], 700)]
        detour = find_hazard_detour(route, [HAZARD], DESTINATION, fetch=fetch)
        self.assertIsNotNone(detour)
        self.assertEqual(detour["detour"]["turnaround"], (LAT, XS[0]))
        self.assertEqual(detour["legs"][0]["steps"][0]["maneuver"]["modifier"], "uturn")

    def test_no_detour_when_every_candidate_passes_hazard(self):
        def fetch(waypoints, alternatives=3, timeout=None):
            start = waypoints[0]
            return [_route([[start[1], start[0]], [XS[-1], LAT]], 300.0)]

        self.assertIsNone(find_hazard_detour(BASE, [HAZARD], DESTINATION, fetch=fetch))

    def test_route_without_hazard_is_not_rerouted(self):
        far_hazard = {"latitude": 12.9400, "longitude": 77.6225}

        def fetch(*args, **kwargs):
            raise AssertionError("router should not be called")

        self.assertIsNone(
            find_hazard_detour(BASE, [far_hazard], DESTINATION, fetch=fetch)
        )


if __name__ == "__main__":
    unittest.main()
