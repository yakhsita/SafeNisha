import io
import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from core.geocoding import AddressSearch, GeocodingServiceError


class GeocodingTests(unittest.TestCase):
    def test_address_results_are_validated_and_cached(self):
        payload={"features":[{"geometry":{"coordinates":[77.59,12.97]},
                  "properties":{"name":"Park","city":"Bengaluru","country":"India"}},
                  {"geometry":{"coordinates":[0,999]},"properties":{"name":"Invalid"}}]}
        search=AddressSearch()
        with patch("core.geocoding.urlopen",return_value=io.BytesIO(json.dumps(payload).encode())) as fetch:
            first=search.search("Park Bengaluru")
            second=search.search("Park Bengaluru")
        self.assertEqual(first,second)
        self.assertEqual(len(first),1)
        self.assertEqual(first[0]["lat"],12.97)
        self.assertIn("Bengaluru",first[0]["label"])
        fetch.assert_called_once()

    def test_bad_queries_and_service_failures_are_actionable(self):
        for value in (None,"a","x"*251):
            with self.assertRaises(ValueError):AddressSearch().search(value)
        with patch("core.geocoding.urlopen",side_effect=URLError("offline")):
            with self.assertRaisesRegex(GeocodingServiceError,"choose a point"):
                AddressSearch().search("Park Bengaluru")

    def test_bias_cannot_inject_nonfinite_coordinates(self):
        with self.assertRaises(ValueError):
            AddressSearch().search("Park Bengaluru",{"lat":float("nan"),"lon":77})

    def test_nearby_search_expands_results_and_limits_to_seven_kilometres(self):
        from urllib.parse import urlsplit,parse_qs
        features=[{"geometry":{"coordinates":[77.62+i*0.001,12.93]},"properties":{"name":"Place "+str(i)}} for i in range(19)]
        features.append({"geometry":{"coordinates":[77.82,12.93]},"properties":{"name":"Too far"}})
        with patch("core.geocoding.urlopen",return_value=io.BytesIO(json.dumps({"features":features}).encode())) as fetch:
            results=AddressSearch().search("Place",{"lat":12.93,"lon":77.62})
        parameters=parse_qs(urlsplit(fetch.call_args.args[0].full_url).query)
        self.assertEqual(parameters["limit"],["20"])
        self.assertIn("bbox",parameters)
        self.assertEqual(len(results),19)
        self.assertTrue(all(r["distance_m"]<=7000 for r in results))
        self.assertEqual(results,sorted(results,key=lambda r:r["distance_m"]))

    def test_global_fallback_is_available_when_nearby_query_is_empty(self):
        from urllib.parse import urlsplit,parse_qs
        responses=[io.BytesIO(json.dumps({"features":[]}).encode()),
                   io.BytesIO(json.dumps({"features":[{"geometry":{"coordinates":[78,13]},"properties":{"name":"Distant place"}}]}).encode())]
        with patch("core.geocoding.urlopen",side_effect=responses) as fetch,patch("core.geocoding.time.sleep"):
            results=AddressSearch().search("Distant place",{"lat":12.93,"lon":77.62})
        self.assertEqual(len(results),1)
        self.assertNotIn("bbox",parse_qs(urlsplit(fetch.call_args.args[0].full_url).query))
