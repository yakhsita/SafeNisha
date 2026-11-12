"""Normalized, configurable prototype risk scoring."""

import math


DEFAULT_WEIGHTS = {
    "historical_incidents": 0.35,
    "lighting_deficiency": 0.30,
    "live_hazards": 0.20,
    "isolation": 0.10,
    "uncertainty": 0.05,
}


def calculate_risk(factors, weights=None):
    """Return a weighted risk score after validating factors and weights."""
    weights = DEFAULT_WEIGHTS if weights is None else weights
    if set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError("Weights must define exactly the five prototype risk factors.")

    weight_values = list(weights.values())
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
           for value in weight_values):
        raise ValueError("Risk weights must be finite, non-negative numbers.")
    if not math.isclose(sum(weight_values), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("Risk weights must sum to 1.")

    if set(factors) != set(DEFAULT_WEIGHTS):
        raise ValueError("Risk factors must define exactly the five prototype indicators.")

    score = 0.0
    for name, weight in weights.items():
        value = factors[name]
        if (not isinstance(value, (int, float)) or not math.isfinite(value)
                or not 0.0 <= value <= 1.0):
            raise ValueError("Risk factor '{}' must be a finite value in [0, 1].".format(name))
        score += weight * value
    return score
