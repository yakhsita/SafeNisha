"""Validation and moderation rules for community hazard reports."""

import math
from datetime import datetime, timezone

from hazards import database


HAZARD_TYPES = {
    "broken_streetlight",
    "dark_path",
    "blocked_footpath",
    "other_infrastructure",
}
STATUSES = {"new", "confirmed", "resolved"}
MAX_DESCRIPTION_LENGTH = 500
DEMO_EDGE_IDS = {"A-N", "N-D", "A-C", "C-D", "A-S", "S-D"}


def add_hazard(edge_id=None, hazard_type=None, description="", latitude=None,
               longitude=None, database_path=None):
    if (latitude is None) != (longitude is None):
        raise ValueError("Both latitude and longitude must be provided together.")
    if latitude is None:
        if edge_id not in DEMO_EDGE_IDS:
            raise ValueError("Select a map location or a segment from the demo network.")
    else:
        if (not isinstance(latitude, (int, float)) or not math.isfinite(latitude)
                or not -90.0 <= latitude <= 90.0):
            raise ValueError("Latitude must be a finite value between -90 and 90.")
        if (not isinstance(longitude, (int, float)) or not math.isfinite(longitude)
                or not -180.0 <= longitude <= 180.0):
            raise ValueError("Longitude must be a finite value between -180 and 180.")
        if edge_id is None:
            edge_id = "map:{:.6f},{:.6f}".format(latitude, longitude)
    if hazard_type not in HAZARD_TYPES:
        raise ValueError("Select a supported hazard type.")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError("Description must be at most {} characters.".format(
            MAX_DESCRIPTION_LENGTH
        ))

    reported_at = datetime.now(timezone.utc).isoformat()
    return database.insert_hazard(
        edge_id=edge_id,
        hazard_type=hazard_type,
        description=description.strip(),
        reported_at=reported_at,
        latitude=latitude,
        longitude=longitude,
        database_path=database_path,
    )


def get_reports(database_path=None):
    return database.get_hazards(database_path=database_path, include_expired=False)


def get_confirmed_reports(database_path=None):
    return database.get_confirmed_hazards(database_path=database_path)


def set_report_status(hazard_id, status, database_path=None):
    if status not in STATUSES:
        raise ValueError("Status must be one of: {}.".format(", ".join(sorted(STATUSES))))
    if not isinstance(hazard_id, int) or hazard_id <= 0:
        raise ValueError("Hazard report ID must be a positive integer.")
    database.update_hazard_status(hazard_id, status, database_path=database_path)


def apply_confirmed_hazards(graph, reports):
    """Raise the live-hazard feature only for active, locally confirmed reports."""
    hazards_by_edge = {}
    for report in reports:
        if report["status"] != "confirmed":
            continue
        edge_id = report.get("edge_id")
        if edge_id is None:
            continue
        expires_at = report.get("expires_at")
        if expires_at:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                continue
        hazards_by_edge[edge_id] = hazards_by_edge.get(edge_id, 0) + 1

    for edge in graph["edges"]:
        count = hazards_by_edge.get(edge["id"], 0)
        if count:
            edge["risk_factors"]["live_hazards"] = 1.0
    return graph
