import unittest
from unittest.mock import Mock
from core.risk_alternatives import find_risk_alternatives,hotspot_polygons


class RiskAlternativeTests(unittest.TestCase):
    def setUp(self):
        self.zone={"bounds":[77.6244,12.9294,77.6256,12.9306],"historical_incidents":1,
                   "lighting_deficiency":0.8,"isolation":0.7}
        self.route={"distance":1000,"geometry":{"coordinates":[[77.62,12.93],[77.63,12.93]]}}
        self.start=(12.93,77.62);self.end=(12.93,77.63)

    def test_single_default_path_can_trigger_hotspot_avoidance_without_reports(self):
        alternative={"distance":1100,"geometry":{"coordinates":[[77.62,12.93],[77.62,12.931],[77.63,12.931],[77.63,12.93]]}}
        fetch=Mock(return_value=[alternative])
        result=find_risk_alternatives(self.route,[self.zone],self.start,self.end,fetch=fetch)
        self.assertEqual(result,[alternative])
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0],[self.start,self.end])
        self.assertTrue(fetch.call_args.kwargs["exclude_polygons"])

    def test_hotspot_at_endpoint_is_not_forced_out_of_the_route(self):
        fetch=Mock()
        result=find_risk_alternatives(self.route,[self.zone],(12.93,77.625),self.end,fetch=fetch)
        self.assertEqual(result,[]);fetch.assert_not_called()

    def test_waypoint_spurs_and_duplicate_candidates_are_rejected(self):
        spur={"distance":1200,"geometry":{"coordinates":[[77.62,12.93],[77.623,12.93],
              [77.623,12.932],[77.623,12.93],[77.63,12.93]]}}
        fetch=Mock(return_value=[self.route,self.route,spur])
        result=find_risk_alternatives(self.route,[self.zone],self.start,self.end,fetch=fetch)
        self.assertEqual(result,[self.route])
