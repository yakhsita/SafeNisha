import unittest
from unittest.mock import patch
from pathlib import Path

from streamlit.testing.v1 import AppTest


class MapSelectionTests(unittest.TestCase):
    def test_two_map_clicks_immediately_request_and_display_route(self):
        route = {
            "distance": 500.0,
            "geometry": {"type": "LineString", "coordinates":
                         [[77.6245, 12.9352], [77.6200, 12.9400]]},
            "legs": [],
        }
        clicks = [
            {"last_clicked": {"lat": 12.9352, "lng": 77.6245}},
            {"last_clicked": {"lat": 12.9400, "lng": 77.6200}},
            {},
        ]
        with patch("ui.frontend_demo.st_folium", side_effect=clicks), \
             patch("ui.frontend_demo.track_location", return_value=None), \
             patch("ui.frontend_demo.get_route_alternatives", return_value=[route]) as fetch, \
             patch("ui.frontend_demo.hazard_service.get_confirmed_reports", return_value=[]):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
            self.assertEqual(list(app.exception), [])
            fetch.assert_called_once_with((12.9352, 77.6245), (12.9400, 77.6200))
            self.assertEqual(app.session_state["route_options"], [route])
            self.assertEqual(app.session_state["map_action"], "Destination")

    def test_starting_journey_keeps_route_while_waiting_for_gps(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.session_state["start_location"] = "12.9352, 77.6245"
        app.session_state["destination_location"] = "12.9400, 77.6200"
        app.session_state["journey_active"] = True
        route = {"distance": 500.0, "geometry": {"type": "LineString", "coordinates":
                 [[77.6245, 12.9352], [77.6200, 12.9400]]}, "legs": []}
        with patch("ui.frontend_demo.st_folium", return_value={}), \
             patch("ui.frontend_demo.track_location", return_value=None), \
             patch("ui.frontend_demo.get_route_alternatives", return_value=[route]) as fetch, \
             patch("ui.frontend_demo.hazard_service.get_confirmed_reports", return_value=[]):
            app.run(timeout=30)
            self.assertEqual(list(app.exception), [])
            fetch.assert_called_once_with((12.9352, 77.6245), (12.9400, 77.6200))

    def test_layer_click_and_subsequent_map_click_are_both_selected(self):
        from ui.frontend_demo import _new_map_click
        first = {"lat": 12.93, "lng": 77.62}
        layer = {"lat": 12.94, "lng": 77.63}
        new = {"lat": 12.95, "lng": 77.64}
        with patch("ui.frontend_demo.st.session_state", {}):
            self.assertEqual(_new_map_click({"last_clicked": first}), first)
            self.assertEqual(_new_map_click({"last_clicked": first,
                                            "last_object_clicked": layer}), layer)
            self.assertEqual(_new_map_click({"last_clicked": new,
                                            "last_object_clicked": layer}), new)
            self.assertIsNone(_new_map_click({"last_clicked": new,
                                              "last_object_clicked": layer}))

    def test_current_location_can_set_start_before_destination(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.session_state["map_location_request"] = 1
        app.session_state["map_location_as_start"] = True
        payload = {"latitude": 12.9352, "longitude": 77.6245, "request_id": 1}
        with patch("ui.frontend_demo.st_folium", return_value={}), \
             patch("ui.frontend_demo.track_location", return_value=payload), \
             patch("ui.frontend_demo.get_route_alternatives") as fetch:
            app.run(timeout=30)
            self.assertEqual(list(app.exception), [])
            self.assertEqual(app.session_state["start_location"], "12.935200, 77.624500")
            self.assertEqual(app.session_state["map_current_location"], (12.9352, 77.6245))
            fetch.assert_not_called()

    def test_location_permission_denied_keeps_manual_selection_available(self):
        with patch("ui.frontend_demo.st_folium", return_value={}), \
             patch("ui.frontend_demo.track_location", return_value={
                 "error": "Permission denied", "request_id": 0}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
            self.assertEqual(list(app.exception), [])
            self.assertIn("Permission denied", app.session_state["map_location_error"])
            self.assertEqual(app.text_input(key="start_location").value, "")


if __name__ == "__main__":
    unittest.main()
