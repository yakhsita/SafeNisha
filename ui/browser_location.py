"""Browser geolocation component for an explicitly active local journey."""

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT = components.declare_component(
    "safenisha_browser_location",
    path=str(Path(__file__).resolve().parent / "location_component"),
)


def track_location(active, key=None, once=False, request_id=0):
    """Return the latest browser location or a geolocation status payload."""
    return _COMPONENT(active=active, once=once, request_id=request_id, key=key, default=None)


_ENDPOINT_COMPONENT = components.declare_component(
    "safenisha_endpoint_focus",
    path=str(Path(__file__).resolve().parent / "endpoint_component"),
)


def watch_endpoint_focus(selected, hazard_mode=False):
    """Notify Python when either native endpoint field receives browser focus."""
    return _ENDPOINT_COMPONENT(selected=selected, hazard_mode=hazard_mode,
                               key="endpoint_focus_bridge", default=None)
