import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.journey_service import JourneyService, coordinates
from hazards import database, hazard_service


ROUTE = {"distance":500,"geometry":{"type":"LineString","coordinates":[[77.6245,12.9352],[77.6200,12.9400]]},"legs":[]}
BODY = {"start":[12.9352,77.6245],"destination":[12.9400,77.6200],"beta":2}


class JourneyServiceTests(unittest.TestCase):
    def setUp(self):
        patcher=patch("core.journey_service.find_risk_alternatives",return_value=[])
        patcher.start();self.addCleanup(patcher.stop)
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"reports.sqlite3"
        database.initialize_database(self.path)
        self.service=JourneyService(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_route_contract_contains_dataset_risk_and_exact_geometry(self):
        with patch("core.journey_service.get_route_alternatives",return_value=[ROUTE]) as fetch:
            result=self.service.plan(BODY)
            second=self.service.plan({**BODY,"beta":5})
        fetch.assert_called_once()
        self.assertTrue(result["live"])
        self.assertEqual(result["shortest"]["path"],[[12.9352,77.6245],[12.9400,77.6200]])
        self.assertEqual(len(result["synthetic_zones"]),8)
        self.assertGreater(result["shortest"]["risk_indicator"],0.05)
        self.assertEqual(second["shortest"]["risk_factors"],result["shortest"]["risk_factors"])

    def test_hazard_preview_reaches_detour_service(self):
        hazard={"lat":12.9376,"lon":77.62225}
        with patch("core.journey_service.get_route_alternatives",return_value=[ROUTE]), \
             patch("core.journey_service.find_hazard_detour",return_value=None) as detour:
            result=self.service.plan({**BODY,"preview_hazard":hazard})
        detour.assert_called_once()
        self.assertTrue(result["hazard_blocked"])
        self.assertEqual(result["shortest"]["risk_factors"]["live_hazards"],1)

    def test_reports_are_persistent_and_only_confirmed_influence_routes(self):
        report_id=hazard_service.add_hazard(hazard_type="dark_path",latitude=12.9376,
            longitude=77.62225,database_path=self.path)
        second=JourneyService(self.path)
        self.assertEqual(second.reports()[0]["id"],report_id)
        with patch("core.journey_service.get_route_alternatives",return_value=[ROUTE]), \
             patch("core.journey_service.find_hazard_detour",return_value=None) as detour:
            self.assertEqual(second.plan(BODY)["shortest"]["risk_factors"]["live_hazards"],0)
            detour.assert_not_called()
            hazard_service.set_report_status(report_id,"confirmed",self.path)
            self.assertTrue(second.plan(BODY)["hazard_blocked"])
            hazard_service.set_report_status(report_id,"resolved",self.path)
            self.assertFalse(second.plan(BODY)["hazard_blocked"])

    def test_invalid_inputs_are_rejected_before_requesting_routes(self):
        for bad in ("address without coordinates",[91,77],[True,77],[float('nan'),77]):
            with self.assertRaises(ValueError): coordinates(bad)
        for beta in (-1,6,True,"2"):
            with self.assertRaises(ValueError): self.service.plan({**BODY,"beta":beta})

    def test_explicit_reroute_avoids_own_unverified_report_even_at_beta_zero(self):
        report_id=hazard_service.add_hazard(hazard_type="dark_path",latitude=12.9376,
                    longitude=77.62225,database_path=self.path)
        direct={"distance":800,"geometry":{"coordinates":[[77.6245,12.9352],[77.626,12.9352],
                [77.626,12.9400],[77.6200,12.9400]]},"legs":[]}
        with patch("core.journey_service.get_route_alternatives",return_value=[ROUTE]), \
             patch("core.journey_service.get_routes",return_value=[direct]) as fetch:
            result=self.service.plan({**BODY,"beta":0,"force_reroute":True,"avoid_report_ids":[report_id]})
        self.assertFalse(result["hazard_blocked"])
        self.assertEqual(result["lower_risk"]["distance_m"],800)
        self.assertEqual(result["origin_mode"],"planned")
        self.assertIn(report_id,result["avoided_report_ids"])
        self.assertIn("exclude_polygons",fetch.call_args.kwargs)
        self.assertEqual(self.service.reports()[0]["status"],"new")

    def test_active_reroute_uses_current_fix_and_marks_true_reverse_departure(self):
        current={"lat":12.936,"lon":77.624}
        reverse={"distance":700,"geometry":{"coordinates":[[77.624,12.936],[77.623,12.936],
                 [77.620,12.940]]},"legs":[]}
        with patch("core.journey_service.get_route_alternatives",return_value=[reverse]) as fetch:
            result=self.service.plan({**BODY,"force_reroute":True,"journey_active":True,
                                      "current_location":current,"heading":90})
        fetch.assert_called_once_with((12.936,77.624),(12.94,77.62),timeout=12)
        self.assertEqual(result["start"],current)
        self.assertEqual(result["origin_mode"],"current")
        self.assertEqual(result["lower_risk"]["steps"][0]["maneuver"]["modifier"],"uturn")
        self.assertEqual(result["lower_risk"]["detour"]["turnaround"],(12.936,77.624))

    def test_active_reroute_never_silently_uses_stale_planned_start(self):
        with self.assertRaises(ValueError):
            self.service.plan({**BODY,"force_reroute":True,"journey_active":True})

    def test_escape_from_hazard_must_move_away_without_reentry(self):
        from core.journey_service import avoids_hazards
        hazard={"latitude":12.93,"longitude":77.62}
        away={"geometry":{"coordinates":[[77.62,12.93],[77.619,12.93],[77.619,12.932]]}}
        reentry={"geometry":{"coordinates":[[77.62,12.93],[77.619,12.93],[77.6201,12.93],[77.621,12.93]]}}
        self.assertTrue(avoids_hazards(away,[hazard],allow_escape=True))
        self.assertFalse(avoids_hazards(away,[hazard],allow_escape=False))
        self.assertFalse(avoids_hazards(reentry,[hazard],allow_escape=True))
        short_escape={"geometry":{"coordinates":[[77.62,12.93],[77.619,12.93]]}}
        toward_hazard={"geometry":{"coordinates":[[77.6198,12.93],[77.621,12.93]]}}
        self.assertTrue(avoids_hazards(short_escape,[hazard],allow_escape=True))
        self.assertFalse(avoids_hazards(toward_hazard,[hazard],allow_escape=True))

    def test_atlas_can_load_dataset_without_a_planned_route(self):
        result=self.service.atlas({"center":{"lat":12.9352,"lon":77.6245}})
        self.assertEqual(len(result["synthetic_zones"]),8)
        self.assertLess(result["bounds"]["south"],12.9352)
        self.assertGreater(result["bounds"]["north"],12.9352)
        with self.assertRaises(ValueError):self.service.atlas({"center":[12.93,77.62],"radius_m":float("nan")})

    def test_safety_preference_can_select_new_hotspot_bypass(self):
        alternative={"distance":520,"geometry":{"coordinates":[[77.6245,12.9352],
                     [77.627,12.9352],[77.627,12.942],[77.6200,12.9400]]},"legs":[]}
        with patch("core.journey_service.get_route_alternatives",return_value=[ROUTE]), \
             patch("core.journey_service.find_risk_alternatives",return_value=[alternative]):
            distance_only=self.service.plan({**BODY,"beta":0})
            safer=self.service.plan({**BODY,"beta":5})
        self.assertEqual(distance_only["lower_risk"]["distance_m"],500)
        self.assertEqual(safer["candidate_count"],2)
        self.assertEqual(safer["lower_risk"]["distance_m"],520)
        self.assertLess(safer["lower_risk"]["risk_indicator"],safer["shortest"]["risk_indicator"])
        self.assertGreater(len(safer["lower_risk"]["risk_paths"]),1)
        self.assertEqual(safer["risk_field"]["bandwidth_fraction"],0.24)


if __name__ == "__main__":unittest.main()
