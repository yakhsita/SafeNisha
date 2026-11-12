import unittest

from ui.frontend_demo import _distance_meters, _route_instruction


class JourneyPresentationTests(unittest.TestCase):
    def test_instruction_includes_maneuver_street_and_distance(self):
        instruction = _route_instruction({
            "maneuver": {"type": "turn", "modifier": "left"},
            "name": "MG Road",
            "distance": 240,
        })
        self.assertEqual(instruction, "Turn left onto MG Road · 240 m")

    def test_instruction_handles_arrival_without_street_name(self):
        self.assertEqual(
            _route_instruction({"maneuver": {"type": "arrive"}}),
            "Arrive at your destination",
        )

    def test_distance_between_locations_is_in_meters(self):
        distance = _distance_meters((12.0, 77.0), (12.001, 77.0))
        self.assertGreater(distance, 100)
        self.assertLess(distance, 120)


if __name__ == "__main__":
    unittest.main()
