"""Browser geolocation component for an explicitly active local journey."""

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "safenisha_browser_location",
    path=str(Path(__file__).resolve().parent / "location_component"),
)


def track_location(active, key=None):
    """Return the latest browser location or a geolocation status payload."""
    return _COMPONENT(active=active, key=key, default=None)
