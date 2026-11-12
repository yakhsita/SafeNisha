import tempfile
import unittest
from pathlib import Path

from data.risk_layers import load_risk_layers


class RiskLayerTests(unittest.TestCase):
    def test_loads_template_layers_without_fabricated_rows(self):
        layers = load_risk_layers(Path(__file__).resolve().parents[1] / "data")
        self.assertEqual(layers, {
            "historical_incidents": [],
            "lighting": [],
            "isolation": [],
        })

    def test_rejects_incident_counts_that_are_not_nonnegative_integers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "historical_incidents.csv"
            path.write_text(
                "latitude,longitude,incident_count\n12.9,77.6,-1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "non-negative integer"):
                load_risk_layers(directory)


if __name__ == "__main__":
    unittest.main()
