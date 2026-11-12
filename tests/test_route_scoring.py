import unittest

from core.route_scoring import choose_route_pair, score_route_alternatives


class RouteScoringTests(unittest.TestCase):
    def test_beta_ranks_only_available_routes_and_keeps_shortest(self):
        routes = [
            {
                "distance": 1000.0,
                "geometry": {"coordinates": [[77.6200, 12.9300], [77.6210, 12.9300]]},
            },
            {
                "distance": 1100.0,
                "geometry": {"coordinates": [[77.6200, 12.9320], [77.6210, 12.9320]]},
            },
        ]
        layers = {
            "historical_incidents": [
                {"latitude": 12.9300, "longitude": 77.6205, "value": 10}
            ],
            "lighting": [],
            "isolation": [],
        }

        shortest_scored = score_route_alternatives(routes, layers, [], beta=0)
        shortest, preferred = choose_route_pair(shortest_scored)
        self.assertEqual(shortest["distance"], 1000)
        self.assertEqual(preferred["distance"], 1000)

        safer_scored = score_route_alternatives(routes, layers, [], beta=5)
        shortest, preferred = choose_route_pair(safer_scored)
        self.assertEqual(shortest["distance"], 1000)
        self.assertEqual(preferred["distance"], 1100)
        self.assertGreater(safer_scored[0]["risk_factors"]["uncertainty"], 0)

    def test_selected_hazard_preview_can_change_preferred_but_not_shortest_route(self):
        routes = [
            {
                "distance": 1000.0,
                "geometry": {"coordinates": [[77.6200, 12.9300], [77.6210, 12.9300]]},
            },
            {
                "distance": 1100.0,
                "geometry": {"coordinates": [[77.6200, 12.9320], [77.6210, 12.9320]]},
            },
        ]
        preview_hazard = {
            "latitude": 12.9300,
            "longitude": 77.6205,
            "preview": True,
        }

        scored = score_route_alternatives(
            routes,
            {"historical_incidents": [], "lighting": [], "isolation": []},
            [preview_hazard],
            beta=5,
        )
        shortest, preferred = choose_route_pair(scored)
        self.assertEqual(shortest["distance"], 1000)
        self.assertEqual(preferred["distance"], 1100)
        self.assertEqual(scored[0]["risk_factors"]["live_hazards"], 1.0)


if __name__ == "__main__":
    unittest.main()
