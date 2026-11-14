import copy
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.incident_model import load_dataset, evaluate_dataset, fit_count_model, predict, validate_dataset


class IncidentModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = load_dataset()
        cls.result = evaluate_dataset(cls.payload)

    def test_dataset_is_used_with_chronological_split(self):
        self.assertEqual(len(self.payload["records"]), 192)
        self.assertEqual(self.result["train_rows"], 96)
        self.assertEqual(self.result["test_rows"], 96)
        self.assertEqual(self.result["selected_model"], "Negative Binomial")
        self.assertGreater(self.result["models"]["Poisson"]["dispersion"], 1)
        self.assertTrue(all(m["converged"] for m in self.result["models"].values()))
        self.assertTrue(all(r["Negative Binomial"] > 0 for r in self.result["predictions"]))

    def test_test_outcomes_and_true_mean_cannot_change_fitted_models(self):
        altered = copy.deepcopy(self.payload)
        for row in altered["records"]:
            row["true_mean"] = 999999
            if row["split"] == "test":
                row["incident_count"] += 1000
        result = evaluate_dataset(altered)
        self.assertEqual(result["models"], self.result["models"])
        self.assertEqual(result["selected_model"], self.result["selected_model"])
        self.assertEqual([r["Negative Binomial"] for r in result["predictions"]],
                         [r["Negative Binomial"] for r in self.result["predictions"]])

    def test_exposure_offset_scales_prediction(self):
        rows = [r for r in self.payload["records"] if r["split"] == "train"]
        model = fit_count_model(rows)
        first = copy.deepcopy(rows[0])
        doubled = {**first, "exposure_k_ped_hours": first["exposure_k_ped_hours"] * 2}
        values = predict(model, [first, doubled])
        self.assertAlmostEqual(values[1], 2 * values[0])

    def test_invalid_exposure_and_overlapping_splits_rejected(self):
        bad = copy.deepcopy(self.payload)
        bad["records"][0]["exposure_k_ped_hours"] = 0
        with self.assertRaises(ValueError):
            validate_dataset(bad)
        bad = copy.deepcopy(self.payload)
        bad["records"][0]["split"] = "test"
        with self.assertRaises(ValueError):
            validate_dataset(bad)

    def test_analysis_page_removed_and_old_sessions_return_to_routing(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.session_state["app_page"] = "Synthetic data"
        app.run(timeout=30)
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.radio(key="app_page").options, ["Plan journey", "Profile & Guardian"])
        self.assertEqual(app.radio(key="app_page").value, "Plan journey")
        self.assertEqual(len(app.toggle), 0)


if __name__ == "__main__":
    unittest.main()
