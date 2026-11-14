from datetime import datetime, timezone
from threading import Event
import time
import unittest
from unittest.mock import patch
import io
import json

from core.environment import EnvironmentField, EnvironmentProvider, IST, opening_state, fetch_osm
from core.route_scoring import score_route_alternatives, choose_route_pair
from core.journey_service import JourneyService
from tests.test_journey_service import ROUTE, BODY


def place(ident, lat, lon, hours, **tags):
    return {"id":ident,"type":"node","lat":lat,"lon":lon,
            "tags":{"shop":"convenience","opening_hours":hours,**tags}}


def road(ident, lat, lit):
    return {"id":ident,"type":"way","tags":{"highway":"residential","lit":lit},
            "geometry":[{"lat":lat,"lon":77.62},{"lat":lat,"lon":77.622}]}


def snapshot(elements):
    return {"status":"available","source":"OpenStreetMap / Overpass","bounds":[77.61,12.92,77.63,12.94],
            "fetched_at":"2026-11-14T10:00:00+00:00","elements":elements,"refresh_seconds":600}


class OpeningHoursTests(unittest.TestCase):
    def state(self, hours, day=14, hour=12, minute=0):
        return opening_state(hours,datetime(2026,11,day,hour,minute,tzinfo=IST))

    def test_weekdays_weekend_and_multiple_daily_windows(self):
        h="Mo-Fr 09:00-12:00,14:00-18:00; Sa 10:00-13:00; Su off"
        self.assertEqual(self.state(h,day=13,hour=11),"open")
        self.assertEqual(self.state(h,day=13,hour=13),"closed")
        self.assertEqual(self.state(h,day=14,hour=12),"open")
        self.assertEqual(self.state(h,day=15,hour=12),"closed")

    def test_overnight_and_closing_boundary(self):
        self.assertEqual(self.state("Fr 22:00-02:00",day=14,hour=1),"open")
        self.assertEqual(self.state("Fr 22:00-02:00",day=14,hour=2),"closed")
        self.assertEqual(self.state("24/7",hour=3),"open")

    def test_holidays_solar_rules_missing_and_invalid_hours_stay_unknown(self):
        for hours in (None,"","Mo-Su 09:00-21:00; PH off","sunrise-sunset","by appointment","09:00-25:00","09:00-09:00"):
            with self.subTest(hours=hours):self.assertEqual(self.state(hours),"unknown")
        self.assertEqual(opening_state("Mo-Su 09:00-21:00",None),"unknown")


class EnvironmentScoringTests(unittest.TestCase):
    def setUp(self):
        self.routes=[{"distance":1000,"geometry":{"coordinates":[[77.62,12.93],[77.622,12.93]]}},
                     {"distance":1050,"geometry":{"coordinates":[[77.62,12.932],[77.622,12.932]]}}]
        self.layers={"lighting":[{"latitude":12.93,"longitude":77.621,"value":.8},
                                 {"latitude":12.932,"longitude":77.621,"value":.8}],
                     "isolation":[{"latitude":12.93,"longitude":77.621,"value":.5},
                                  {"latitude":12.932,"longitude":77.621,"value":.5}]}

    def test_real_context_changes_preferred_route_and_local_risk(self):
        field=EnvironmentField(snapshot([road(1,12.93,"no"),road(2,12.932,"yes"),
            place(3,12.93,77.621,"Mo-Su 09:00-17:00"),
            place(4,12.932,77.621,"24/7"),place(5,12.932,77.6208,"24/7")]),
            now=datetime(2026,11,14,20,tzinfo=IST))
        before=score_route_alternatives(self.routes,self.layers,[],5)
        self.assertEqual(choose_route_pair(before)[1]["distance"],1000)
        scored=score_route_alternatives(self.routes,{**self.layers,"environment":field},[],5)
        self.assertEqual(choose_route_pair(scored)[0]["distance"],1000)
        self.assertEqual(choose_route_pair(scored)[1]["distance"],1050)
        self.assertEqual(scored[1]["risk_source"],"hybrid")
        self.assertLess(scored[1]["risk_factors"]["lighting_deficiency"],scored[0]["risk_factors"]["lighting_deficiency"])

    def test_open_and_closed_schedule_changes_isolation_at_same_location(self):
        data=snapshot([place(1,12.93,77.621,"Mo-Su 09:00-17:00")])
        noon=EnvironmentField(data,datetime(2026,11,14,12,tzinfo=IST)).at(12.93,77.621)[0]
        night=EnvironmentField(data,datetime(2026,11,14,22,tzinfo=IST)).at(12.93,77.621)[0]
        self.assertLess(noon,night)

    def test_missing_features_or_unknown_hours_do_not_mean_empty_or_dark(self):
        field=EnvironmentField(snapshot([place(1,12.93,77.621,"unknown")]))
        normal=score_route_alternatives(self.routes,self.layers,[],2)
        actual=score_route_alternatives(self.routes,{**self.layers,"environment":field},[],2)
        self.assertEqual(actual[0]["risk_factors"],normal[0]["risk_factors"])
        self.assertEqual(actual[0]["environment"]["unknown_hours"],1)
        self.assertEqual(actual[0]["environment"]["applied"],[])

    def test_road_lighting_overrides_nearby_lamp_and_private_places_are_excluded(self):
        lamp={"id":9,"type":"node","lat":12.93,"lon":77.621,"tags":{"highway":"street_lamp"}}
        field=EnvironmentField(snapshot([road(1,12.93,"no"),lamp,place(2,12.93,77.621,"24/7",access="private")]))
        isolation,lighting,_,places=field.at(12.93,77.621)
        self.assertIsNone(isolation)
        self.assertEqual(lighting,.95)
        self.assertEqual(places,[])

    def test_sparse_lamp_coverage_does_not_light_entire_route(self):
        lamp={"id":9,"type":"node","lat":12.93,"lon":77.621,"tags":{"highway":"street_lamp"}}
        field=EnvironmentField(snapshot([lamp]))
        _,evidence=field.route_factors(self.routes[0],{"isolation":.5,"lighting_deficiency":.8},[])
        self.assertGreater(evidence["lighting_coverage"],0)
        self.assertLess(evidence["lighting_coverage"],.5)

    def test_environment_metadata_and_local_risk_reach_route_api_contract(self):
        class Provider:
            def get(self,routes):return snapshot([{"type":"way","id":1,"tags":{"highway":"residential","lit":"yes"},
                "geometry":[{"lat":lat,"lon":lon} for lon,lat in ROUTE["geometry"]["coordinates"]]}])
        # No database is required for this isolated orchestration check.
        with patch("core.journey_service.get_route_alternatives",return_value=[ROUTE]), \
             patch("core.journey_service.find_risk_alternatives",return_value=[]), \
             patch("core.journey_service.hazard_service.get_confirmed_reports",return_value=[]), \
             patch("core.journey_service.JourneyService.reports",return_value=[]):
            result=JourneyService(environment_provider=Provider()).plan(BODY)
        self.assertEqual(result["environment"]["status"],"available")
        self.assertNotIn("elements",result["environment"])
        self.assertIn("environment",result["lower_risk"])
        self.assertEqual(result["lower_risk"]["risk_source"],"hybrid")
        self.assertTrue(result["lower_risk"]["risk_paths"])


class ProviderTests(unittest.TestCase):
    def test_overpass_query_requests_real_hours_and_lighting_and_rejects_partial_results(self):
        with patch("core.environment.urlopen",return_value=io.BytesIO(json.dumps({"elements":[]}).encode())) as opened:
            self.assertEqual(fetch_osm((77.61,12.92,77.63,12.94))["elements"],[])
        query=opened.call_args.args[0].data.decode()
        self.assertIn('shop',query)
        self.assertIn('street_lamp',query)
        self.assertIn('lit',query)
        with patch("core.environment.urlopen",return_value=io.BytesIO(b'{"remark":"runtime timeout","elements":[]}')):
            with self.assertRaises(ValueError):fetch_osm((77.61,12.92,77.63,12.94))

    def ready(self,provider):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            result=provider.get([ROUTE])
            if result["status"]!="pending":return result
            time.sleep(.01)
        self.fail("Background fetch did not finish")

    def test_background_fetch_returns_immediately_and_reuses_cache(self):
        release=Event();calls=[]
        def fetch(bounds):
            calls.append(bounds);release.wait(1)
            return {"elements":[]}
        provider=EnvironmentProvider(fetch=fetch)
        started=time.monotonic()
        self.assertEqual(provider.get([ROUTE])["status"],"pending")
        self.assertLess(time.monotonic()-started,.2)
        release.set()
        self.assertEqual(self.ready(provider)["status"],"available")
        self.assertEqual(provider.get([ROUTE])["status"],"available")
        self.assertEqual(len(calls),1)

    def test_failed_service_is_cached_as_unavailable(self):
        def fail(bounds):raise TimeoutError()
        provider=EnvironmentProvider(fetch=fail)
        self.assertEqual(self.ready(provider)["status"],"unavailable")
        self.assertEqual(provider.get([ROUTE])["status"],"unavailable")

    def test_expired_cache_fetches_again(self):
        calls=[]
        provider=EnvironmentProvider(fetch=lambda bounds:(calls.append(bounds) or {"elements":[]}))
        self.assertEqual(self.ready(provider)["status"],"available")
        key=next(iter(provider.cache))
        provider.cache[key]=(0,provider.cache[key][1]);provider.last_request=0
        self.assertEqual(self.ready(provider)["status"],"available")
        self.assertEqual(len(calls),2)

    def test_transport_failure_tries_documented_alternate(self):
        with patch.dict("os.environ",{"SAFENISHA_OVERPASS_URL":""}), \
             patch("core.environment.urlopen",side_effect=[TimeoutError(),io.BytesIO(b'{"elements":[]}')]) as opened:
            self.assertEqual(fetch_osm((77.61,12.92,77.63,12.94))["elements"],[])
            self.assertEqual(opened.call_count,2)

    def test_disabled_and_large_queries_do_not_fetch(self):
        with patch.dict("os.environ",{"SAFENISHA_CONTEXT_ENABLED":"0"}):
            self.assertEqual(EnvironmentProvider().get([ROUTE])["status"],"disabled")
        route={"geometry":{"coordinates":[[77,12],[78,13]]}}
        self.assertEqual(EnvironmentProvider().get([route])["status"],"unavailable")
