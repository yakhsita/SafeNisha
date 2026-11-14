import unittest
from core.incident_model import load_dataset
from core.synthetic_routing import zone_profiles, build_demo_zones, synthetic_route_factors, route_zone_weights
from core.route_scoring import score_route_alternatives, choose_route_pair


class SyntheticRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = zone_profiles(load_dataset())
        cls.routes = [
            {"distance":1000, "geometry":{"coordinates":[[77.62,12.93],[77.62,12.932],[77.628,12.932],[77.628,12.93]]}},
            {"distance":1000, "geometry":{"coordinates":[[77.62,12.93],[77.62,12.928],[77.628,12.928],[77.628,12.93]]}},
        ]
        cls.zones = build_demo_zones(cls.profiles, cls.routes)

    def test_real_dataset_values_are_used_and_coverage_is_complete(self):
        self.assertEqual(len(self.zones), 8)
        rows = {r["area_id"]:r for r in load_dataset()["records"] if r["month"] == "2025-12"}
        for profile in self.profiles:
            self.assertAlmostEqual(profile["lighting_deficiency"], 1-rows[profile["area_id"]]["lighting_score"])
            self.assertEqual(profile["isolation"], rows[profile["area_id"]]["isolation_score"])
            self.assertGreater(profile["historical_incidents"], 0)
        for route in self.routes:
            factors, coverage = synthetic_route_factors(route, self.zones)
            self.assertAlmostEqual(coverage, 1)
            self.assertGreater(factors["lighting_deficiency"], 0)
            self.assertGreater(factors["isolation"], 0)

    def test_synthetic_values_can_change_preferred_route(self):
        layers = {"synthetic_zones":self.zones}
        initial = score_route_alternatives(self.routes, layers, [], beta=5)
        high = max(range(2), key=lambda i: initial[i]["risk_indicator"])
        low = 1-high
        routes = [{**r,"distance":1000 if i == high else 1005} for i,r in enumerate(self.routes)]
        zero = score_route_alternatives(routes,layers,[],beta=0)
        influenced = score_route_alternatives(routes,layers,[],beta=5)
        self.assertEqual(choose_route_pair(zero)[1]["distance"],1000)
        self.assertEqual(choose_route_pair(influenced)[1]["distance"],1005)
        self.assertEqual(influenced[0]["risk_source"],"synthetic")
        self.assertEqual(influenced[0]["risk_factors"]["uncertainty"],1)

    def test_route_geometry_density_does_not_change_zone_weights(self):
        route = {"geometry":{"coordinates":[[77.62,12.931],[77.628,12.931]]}}
        split = {"geometry":{"coordinates":[[77.62,12.931],[77.624,12.931],[77.628,12.931]]}}
        for a,b in zip(route_zone_weights(route,self.zones),route_zone_weights(split,self.zones)):
            self.assertAlmostEqual(a,b)

    def test_outside_grid_is_not_treated_as_zero_risk(self):
        route = {"geometry":{"coordinates":[[0,0],[0.001,0]]}}
        factors, coverage = synthetic_route_factors(route,self.zones)
        self.assertEqual(coverage,0)
        self.assertTrue(all(value == 1 for value in factors.values()))

    def test_actual_hazard_reports_are_preserved(self):
        hazard={"latitude":12.932,"longitude":77.624,"preview":True}
        result=score_route_alternatives(self.routes,{"synthetic_zones":self.zones},[hazard],beta=2)
        self.assertEqual(result[0]["risk_factors"]["live_hazards"],1)
        self.assertEqual(result[1]["risk_factors"]["live_hazards"],0)

    def test_kernel_field_is_continuous_across_former_block_boundary(self):
        from core.synthetic_routing import kernel_factors
        boundary=self.zones[0]["bounds"][2]
        latitude=sum(self.zones[0]["bounds"][i] for i in (1,3))/2
        left=kernel_factors(latitude,boundary-1e-8,self.zones)
        right=kernel_factors(latitude,boundary+1e-8,self.zones)
        for name in left:
            self.assertLess(abs(left[name]-right[name]),0.001)

if __name__ == "__main__":
    unittest.main()
