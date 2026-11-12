import unittest

from core.graph_loader import load_demo_graph
from core.risk_engine import calculate_risk
from core.routing import compare_routes


class RiskScoringTests(unittest.TestCase):
    def test_example_streets_match_documented_scores(self):
        street_a = {
            "historical_incidents": 0.9,
            "lighting_deficiency": 0.1,
            "live_hazards": 0.2,
            "isolation": 0.2,
            "uncertainty": 0.1,
        }
        street_b = {
            "historical_incidents": 0.1,
            "lighting_deficiency": 0.9,
            "live_hazards": 0.2,
            "isolation": 0.5,
            "uncertainty": 0.1,
        }
        self.assertAlmostEqual(calculate_risk(street_a), 0.41)
        self.assertAlmostEqual(calculate_risk(street_b), 0.40)

    def test_invalid_factors_and_weights_are_rejected(self):
        factors = {
            "historical_incidents": 0.5,
            "lighting_deficiency": 0.5,
            "live_hazards": 0.5,
            "isolation": 0.5,
            "uncertainty": 0.5,
        }
        factors["uncertainty"] = 1.1
        with self.assertRaises(ValueError):
            calculate_risk(factors)
        with self.assertRaises(ValueError):
            calculate_risk({key: 0.2 for key in factors}, weights={key: 0.1 for key in factors})


class RoutingTests(unittest.TestCase):
    def test_beta_zero_is_shortest_and_higher_beta_prefers_lower_risk(self):
        graph = load_demo_graph()
        shortest = compare_routes(graph, "A", "D", beta=0.0)
        risk_weighted = compare_routes(graph, "A", "D", beta=2.0)
        self.assertEqual(shortest["shortest"]["edges"], ["A-N", "N-D"])
        self.assertEqual(shortest["preferred"]["edges"], shortest["shortest"]["edges"])
        self.assertNotEqual(
            risk_weighted["preferred"]["edges"],
            risk_weighted["shortest"]["edges"],
        )
        self.assertLess(
            risk_weighted["preferred"]["risk_indicator"],
            risk_weighted["shortest"]["risk_indicator"],
        )
        self.assertGreater(
            risk_weighted["preferred"]["distance_m"],
            risk_weighted["shortest"]["distance_m"],
        )

    def test_invalid_endpoints_and_beta_are_rejected(self):
        graph = load_demo_graph()
        with self.assertRaises(ValueError):
            compare_routes(graph, "missing", "D", beta=1.0)
        with self.assertRaises(ValueError):
            compare_routes(graph, "A", "D", beta=-1.0)


if __name__ == "__main__":
    unittest.main()
