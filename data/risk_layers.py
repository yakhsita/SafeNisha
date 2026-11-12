"""Load optional, geolocated prototype risk layers from CSV files."""

import csv
import math
from pathlib import Path


LAYER_COLUMNS = {
    "historical_incidents": ("historical_incidents.csv", "incident_count", True),
    "lighting": ("lighting.csv", "lighting_deficiency", False),
    "isolation": ("isolation.csv", "isolation", False),
}


def _read_layer(path, value_column, integer_values):
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"latitude", "longitude", value_column}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(
                "{} must contain columns: {}".format(path.name, ", ".join(sorted(required)))
            )

        rows = []
        for line_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            try:
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
                raw_value = float(row[value_column])
            except (TypeError, ValueError):
                raise ValueError(
                    "{}:{} has a non-numeric coordinate or value.".format(path.name, line_number)
                )
            if (not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0
                    or not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0):
                raise ValueError(
                    "{}:{} has coordinates outside valid latitude/longitude ranges."
                    .format(path.name, line_number)
                )
            if (not math.isfinite(raw_value) or raw_value < 0
                    or (integer_values and not raw_value.is_integer())
                    or (not integer_values and raw_value > 1)):
                expected = "a non-negative integer" if integer_values else "a value in [0, 1]"
                raise ValueError(
                    "{}:{} {} must be {}.".format(
                        path.name, line_number, value_column, expected
                    )
                )
            rows.append(
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "value": int(raw_value) if integer_values else raw_value,
                }
            )
    return rows


def load_risk_layers(data_dir=None):
    """Return each optional spatial layer, validating available CSV files."""
    directory = Path(data_dir) if data_dir else Path(__file__).resolve().parent
    result = {}
    for layer_name, (filename, value_column, integer_values) in LAYER_COLUMNS.items():
        path = directory / filename
        result[layer_name] = _read_layer(path, value_column, integer_values) if path.exists() else []
    return result
