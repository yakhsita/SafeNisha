import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.graph_loader import load_demo_graph
from core.routing import compare_routes
from hazards import database, hazard_service


class HazardServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "hazards.sqlite3"
        database.initialize_database(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_unverified_report_does_not_change_route_but_confirmation_does(self):
        graph = load_demo_graph()
        before = compare_routes(graph, "A", "D", beta=2.0)["preferred"]
        report_id = hazard_service.add_hazard(
            "A-C",
            "blocked_footpath",
            "Footpath partially blocked",
            database_path=self.database_path,
        )
        reports = hazard_service.get_confirmed_reports(self.database_path)
        hazard_service.apply_confirmed_hazards(graph, reports)
        still_unverified = compare_routes(graph, "A", "D", beta=2.0)["preferred"]
        self.assertEqual(still_unverified["edges"], before["edges"])

        hazard_service.set_report_status(
            report_id, "confirmed", database_path=self.database_path
        )
        graph = load_demo_graph()
        hazard_service.apply_confirmed_hazards(
            graph, hazard_service.get_confirmed_reports(self.database_path)
        )
        after = compare_routes(graph, "A", "D", beta=2.0)["preferred"]
        self.assertEqual(before["edges"], ["A-C", "C-D"])
        self.assertEqual(after["edges"], ["A-S", "S-D"])

    def test_invalid_reports_and_statuses_are_rejected(self):
        with self.assertRaises(ValueError):
            hazard_service.add_hazard(
                "unknown-edge", "dark_path", database_path=self.database_path
            )
        with self.assertRaises(ValueError):
            hazard_service.add_hazard(
                "A-C", "invalid-type", database_path=self.database_path
            )
        with self.assertRaises(ValueError):
            hazard_service.set_report_status(1, "invalid", self.database_path)

    def test_map_report_saves_coordinates_and_rejects_invalid_locations(self):
        report_id = hazard_service.add_hazard(
            edge_id=None,
            hazard_type="dark_path",
            latitude=12.9352,
            longitude=77.6245,
            database_path=self.database_path,
        )
        report = hazard_service.get_reports(self.database_path)[0]
        self.assertEqual(report["id"], report_id)
        self.assertEqual(report["latitude"], 12.9352)
        self.assertEqual(report["longitude"], 77.6245)
        self.assertTrue(report["edge_id"].startswith("map:"))

        with self.assertRaises(ValueError):
            hazard_service.add_hazard(
                edge_id=None,
                hazard_type="dark_path",
                latitude=91,
                longitude=77.6245,
                database_path=self.database_path,
            )
        with self.assertRaises(ValueError):
            hazard_service.add_hazard(
                edge_id=None,
                hazard_type="dark_path",
                latitude=12.9352,
                database_path=self.database_path,
            )

    def test_existing_database_schema_is_migrated(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(str(legacy_path))
        try:
            connection.execute(
                """
                CREATE TABLE hazards (
                    id INTEGER PRIMARY KEY,
                    edge_id TEXT NOT NULL,
                    hazard_type TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    reported_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    expires_at TEXT
                )
                """
            )
            connection.commit()
        finally:
            connection.close()
        database.initialize_database(legacy_path)
        report_id = hazard_service.add_hazard(
            edge_id=None,
            hazard_type="dark_path",
            latitude=12.9352,
            longitude=77.6245,
            database_path=legacy_path,
        )
        self.assertEqual(hazard_service.get_reports(legacy_path)[0]["id"], report_id)

    def test_reports_persist_and_resolved_reports_are_not_active(self):
        report_id = hazard_service.add_hazard(
            "A-N", "broken_streetlight", database_path=self.database_path
        )
        self.assertEqual(hazard_service.get_reports(self.database_path)[0]["id"], report_id)
        hazard_service.set_report_status(
            report_id, "resolved", database_path=self.database_path
        )
        self.assertEqual(hazard_service.get_confirmed_reports(self.database_path), [])
        self.assertEqual(hazard_service.get_reports(self.database_path)[0]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
