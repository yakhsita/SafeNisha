"""Exposure-adjusted count-model comparison for synthetic area-month records.

Fits only training rows. Neither test outcomes nor true_mean enter training.
Area IDs have no geographic meaning and must not be used as street evidence.
"""
import json
import math
from pathlib import Path

import numpy as np

FEATURES = ("intercept", "log1p_past_incidents", "lighting_score", "hazard_reports", "isolation_score")
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "synthetic_area_monthly_overdispersed.json"


def load_dataset(path=DATA_PATH):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    validate_dataset(payload)
    return payload


def validate_dataset(payload):
    if payload.get("synthetic") is not True:
        raise ValueError("This demonstration accepts explicitly labelled synthetic data only.")
    rows = payload.get("records", [])
    if not rows:
        raise ValueError("The dataset has no records.")
    seen = set()
    months = {"train": [], "test": []}
    for row in rows:
        if not isinstance(row.get("area_id"), str) or not row["area_id"]:
            raise ValueError("Each record needs an area ID.")
        month = row.get("month", "")
        try:
            year, month_number = map(int, month.split("-"))
            if len(month) != 7 or year < 1900 or not 1 <= month_number <= 12:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("Months must use YYYY-MM.")
        key = (row["area_id"], month)
        if key in seen:
            raise ValueError("Duplicate area-month record.")
        seen.add(key)
        split = row.get("split")
        if split not in months:
            raise ValueError("Every record must belong to train or test.")
        months[split].append(month)
        for field in ("incident_count", "past_incidents", "hazard_reports",
                      "exposure_k_ped_hours", "lighting_score", "isolation_score"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid numeric value for " + field)
            if field in ("incident_count", "past_incidents", "hazard_reports") and not float(value).is_integer():
                raise ValueError("Counts must be whole numbers.")
            if field in ("lighting_score", "isolation_score") and value > 1:
                raise ValueError("Scores must be between zero and one.")
            if field == "exposure_k_ped_hours" and value <= 0:
                raise ValueError("Exposure must be positive.")
    if not all(months.values()) or max(months["train"]) >= min(months["test"]):
        raise ValueError("Training months must be strictly earlier than test months.")


def design_matrix(rows):
    return np.asarray([[1, math.log1p(r["past_incidents"]), r["lighting_score"],
                        r["hazard_reports"], r["isolation_score"]] for r in rows], dtype=float)


def _log_likelihood(y, mu, alpha):
    if alpha == 0:
        return float(np.sum(y * np.log(mu) - mu - np.asarray([math.lgamma(v + 1) for v in y])))
    size = 1.0 / alpha
    return float(sum(math.lgamma(v + size) - math.lgamma(size) - math.lgamma(v + 1)
                     + size * math.log(size / (size + m))
                     + v * math.log(m / (size + m)) for v, m in zip(y, mu)))


def fit_count_model(rows, alpha=0.0):
    """Fit log(mu)=log(exposure)+X beta using Fisher scoring with line search."""
    x = design_matrix(rows)
    y = np.asarray([r["incident_count"] for r in rows], dtype=float)
    exposure = np.asarray([r["exposure_k_ped_hours"] for r in rows], dtype=float)
    offset = np.log(exposure)
    beta = np.zeros(x.shape[1])
    beta[0] = math.log(max(float(y.sum()), 1e-8) / exposure.sum())
    converged = False
    for iteration in range(100):
        eta = np.clip(offset + x @ beta, -30, 30)
        mu = np.exp(eta)
        weights = mu / (1 + alpha * mu)
        z = eta + (y - mu) / mu - offset
        root = np.sqrt(weights)
        proposed = np.linalg.lstsq(x * root[:, None], z * root, rcond=None)[0]
        change = proposed - beta
        previous_ll = _log_likelihood(y, mu, alpha)
        step = 1.0
        while step > 1e-8:
            trial = beta + step * change
            trial_mu = np.exp(np.clip(offset + x @ trial, -30, 30))
            if _log_likelihood(y, trial_mu, alpha) >= previous_ll - 1e-9:
                break
            step *= 0.5
        beta += step * change
        if np.max(np.abs(step * change)) < 1e-7:
            converged = True
            break
    mu = np.exp(np.clip(offset + x @ beta, -30, 30))
    ll = _log_likelihood(y, mu, alpha)
    parameters = x.shape[1] + (1 if alpha > 0 else 0)
    return {"coefficients": beta.tolist(), "alpha": float(alpha),
            "aic": 2 * parameters - 2 * ll, "log_likelihood": ll,
            "converged": converged, "iterations": iteration + 1,
            "dispersion": float(np.sum((y - mu) ** 2 / mu) / max(1, len(y) - x.shape[1]))}


def predict(model, rows):
    exposure = np.asarray([r["exposure_k_ped_hours"] for r in rows])
    return np.exp(np.clip(np.log(exposure) + design_matrix(rows) @ model["coefficients"], -30, 30))


def evaluate_dataset(payload):
    validate_dataset(payload)
    train = [r for r in payload["records"] if r["split"] == "train"]
    test = [r for r in payload["records"] if r["split"] == "test"]
    if len(train) <= len(FEATURES):
        raise ValueError("Not enough training records to fit the models.")
    poisson = fit_count_model(train)
    # Profile the NB2 dispersion using training likelihood only, never true_mean.
    candidates = [fit_count_model(train, alpha) for alpha in np.geomspace(0.01, 3.0, 32)]
    negative_binomial = max(candidates, key=lambda m: m["log_likelihood"])
    models = {"Poisson": poisson, "Negative Binomial": negative_binomial}
    selected = min(models, key=lambda name: models[name]["aic"])
    baseline_rate = sum(r["incident_count"] for r in train) / sum(r["exposure_k_ped_hours"] for r in train)
    predictions = {name: predict(model, test) for name, model in models.items()}
    predictions["Exposure baseline"] = np.asarray([baseline_rate * r["exposure_k_ped_hours"] for r in test])
    actual = np.asarray([r["incident_count"] for r in test])
    metrics = [{"Model": name, "Test MAE": float(np.mean(np.abs(actual - values))),
                "Test RMSE": float(np.sqrt(np.mean((actual - values) ** 2))),
                "Train AIC": models[name]["aic"] if name in models else None}
               for name, values in predictions.items()]
    output = [{"area_id": row["area_id"], "month": row["month"], "actual": row["incident_count"],
               "exposure_k_ped_hours": row["exposure_k_ped_hours"],
               **{name: float(values[i]) for name, values in predictions.items()}}
              for i, row in enumerate(test)]
    return {"models": models, "selected_model": selected, "metrics": metrics,
            "predictions": output, "train_rows": len(train), "test_rows": len(test)}
