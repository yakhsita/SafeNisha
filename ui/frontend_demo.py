"""Streamlit interface for OSM walking routes and map-based hazard reports."""

import html
import hashlib
import math
from datetime import datetime, timezone

import folium
import streamlit as st
from streamlit_folium import st_folium

from core.hazard_detour import find_hazard_detour
from core.osm_router import get_route_alternatives
from core.risk_engine import DEFAULT_WEIGHTS
from core.synthetic_routing import zone_profiles, build_demo_zones
from core.incident_model import DATA_PATH
import json
from core.route_scoring import (
    CORRIDOR_METERS,
    _distance_to_route_meters,
    choose_route_pair,
    score_route_alternatives,
)
from hazards import database, hazard_service
from ui.browser_location import track_location, watch_endpoint_focus
from ui.styles import APP_CSS


MAP_CENTER = (12.9352, 77.6245)
HAZARD_LABELS = {
    "broken_streetlight": "Broken streetlight",
    "dark_path": "Dark path",
    "blocked_footpath": "Blocked footpath",
    "other_infrastructure": "Other infrastructure",
}
STATUS_COLORS = {"new": "orange", "confirmed": "green", "resolved": "gray"}
ROUTE_COLORS = {"shortest": "#2878d0", "preferred": "#18a558"}


@st.cache_data(show_spinner=False)
def _synthetic_profiles(contents):
    return zone_profiles(json.loads(contents))


def _normalized_weights():
    raw_weights = {}
    with st.sidebar.expander("Risk factor weights", expanded=False):
        st.caption("Prototype assumptions; adjusted values are normalized to 100%.")
        for factor, default in DEFAULT_WEIGHTS.items():
            label = factor.replace("_", " ").title()
            raw_weights[factor] = st.slider(
                label,
                min_value=0,
                max_value=100,
                value=int(round(default * 100)),
                step=1,
                key="weight_{}".format(factor),
            )
    total = sum(raw_weights.values())
    if total == 0:
        return DEFAULT_WEIGHTS.copy()
    return {factor: value / float(total) for factor, value in raw_weights.items()}


def _format_meters(distance_m):
    if distance_m >= 1000:
        return "{:.2f} km".format(distance_m / 1000.0)
    return "{:.0f} m".format(distance_m)


def _parse_coordinates(value, label):
    try:
        coordinates = [float(part.strip()) for part in value.split(",")]
    except (AttributeError, ValueError):
        raise ValueError("{}: enter latitude, longitude or choose a point on the map."
                         .format(label))
    if len(coordinates) != 2:
        raise ValueError("{}: enter latitude, longitude or choose a point on the map."
                         .format(label))
    latitude, longitude = coordinates
    if (not math.isfinite(latitude) or not -90 <= latitude <= 90
            or not math.isfinite(longitude) or not -180 <= longitude <= 180):
        raise ValueError("{}: coordinates must be within valid latitude/longitude ranges."
                         .format(label))
    return latitude, longitude


def _distance_meters(first, second):
    latitude = math.radians((first[0] + second[0]) / 2.0)
    north = (second[0] - first[0]) * 110574.0
    east = (second[1] - first[1]) * 111320.0 * math.cos(latitude)
    return math.hypot(north, east)


def _invalidate_routes():
    st.session_state["route_options"] = None
    st.session_state["route_error"] = None
    st.session_state["route_query"] = None


def _swap_locations():
    start = st.session_state.get("start_location", "")
    destination = st.session_state.get("destination_location", "")
    st.session_state["start_location"] = destination
    st.session_state["destination_location"] = start
    _invalidate_routes()


def _open_hazard_drawer():
    st.session_state["hazard_select_mode"] = True
    st.session_state["hazard_drawer_open"] = True


def _close_hazard_drawer():
    st.session_state["hazard_drawer_open"] = False
    st.session_state["hazard_location"] = None
    st.session_state["hazard_select_mode"] = False


def _apply_pending_map_selection():
    selection = st.session_state.pop("pending_map_selection", None)
    if selection is None:
        return
    st.session_state["map_focus_location"] = False
    action, latitude, longitude = selection
    if action == "Starting point":
        st.session_state["start_location"] = "{:.6f}, {:.6f}".format(latitude, longitude)
        _invalidate_routes()
        st.session_state["map_action"] = "Destination"
    elif action == "Destination":
        st.session_state["destination_location"] = "{:.6f}, {:.6f}".format(
            latitude, longitude
        )
        _invalidate_routes()
    else:
        st.session_state["hazard_location"] = {
            "latitude": latitude,
            "longitude": longitude,
        }
        st.session_state["hazard_drawer_open"] = True


def _hazard_detour(route_query, base_route, hazards, destination):
    """Detour around the first hazard on ``base_route``, cached per query and hazards."""
    key = (
        "direct-detour-v2",
        route_query,
        round(base_route["distance"], 1),
        tuple(sorted(
            (round(hazard["latitude"], 6), round(hazard["longitude"], 6))
            for hazard in hazards
        )),
    )
    cache = st.session_state.setdefault("hazard_detour_cache", {})
    if cache.get(key) is None:
        with st.spinner("Rerouting around the hazard…"):
            cache[key] = find_hazard_detour(base_route, hazards, destination)
    return cache[key]


def _apply_endpoint_focus(payload):
    if not isinstance(payload, dict) or payload.get("endpoint") not in ("Starting point", "Destination"):
        return
    if payload.get("event_id") == st.session_state.get("endpoint_focus_event"):
        return
    st.session_state["endpoint_focus_event"] = payload["event_id"]
    st.session_state["map_action"] = payload["endpoint"]
    st.session_state["hazard_select_mode"] = False


def _new_map_click(map_data):
    """Return the changed click event, ignoring stale map/layer click values."""
    if not map_data:
        return None
    previous = st.session_state.get("map_click_events", {})
    current = {name: map_data.get(name) for name in ("last_clicked", "last_object_clicked")}
    st.session_state["map_click_events"] = current
    for name in ("last_clicked", "last_object_clicked"):
        if current[name] and current[name] != previous.get(name):
            return current[name]
    return None


def _request_map_location(use_as_start=False):
    st.session_state["map_location_request"] = st.session_state.get("map_location_request", 0) + 1
    st.session_state["map_focus_location"] = True
    st.session_state["map_location_pending"] = True
    st.session_state["map_location_as_start"] = use_as_start


def _apply_map_location(payload):
    if not isinstance(payload, dict) or not st.session_state.get("map_location_pending"):
        return
    if payload.get("request_id") != st.session_state.get("map_location_request", 0):
        return
    st.session_state["map_location_pending"] = False
    if payload.get("error"):
        st.session_state["map_location_error"] = payload["error"]
        return
    try:
        point = _parse_coordinates("{}, {}".format(payload["latitude"], payload["longitude"]),
                                   "Current location")
    except (KeyError, ValueError):
        return
    st.session_state["map_current_location"] = point
    st.session_state["map_location_error"] = None
    st.session_state["map_location_revision"] = st.session_state.get("map_location_revision", 0) + 1
    if st.session_state.pop("map_location_as_start", False):
        st.session_state["start_location"] = "{:.6f}, {:.6f}".format(*point)
        st.session_state["map_action"] = "Destination"
        _invalidate_routes()


def _make_route_map(
    reports, selected_location, shortest, preferred, start, destination, journey_active=False, synthetic_zones=None
):
    map_view = folium.Map(
        location=st.session_state.get("map_current_location", MAP_CENTER),
        zoom_start=15,
        control_scale=True,
        tiles="OpenStreetMap",
    )

    for zone in synthetic_zones or []:
        west, south, east, north = zone["bounds"]
        level = (zone["historical_incidents"] + zone["lighting_deficiency"] + zone["isolation"]) / 3
        color = "#ef4444" if level > 0.6 else "#f59e0b" if level > 0.4 else "#22c55e"
        folium.Rectangle(
            bounds=[(south, west), (north, east)], color=color, weight=1,
            fill=True, fill_opacity=0.10,
            tooltip="Synthetic demo {} | incident {:.2f} | lighting deficiency {:.2f} | isolation {:.2f}".format(
                zone["area_id"], zone["historical_incidents"], zone["lighting_deficiency"], zone["isolation"]),
            interactive=False,
        ).add_to(map_view)
    current_position = st.session_state.get("map_current_location")
    if current_position:
        folium.CircleMarker(current_position, radius=9, color="white", weight=3,
                            fill=True, fill_color="#2563eb", fill_opacity=1,
                            tooltip="Your current location").add_to(map_view)

    if shortest is not None:
        shortest_coordinates = [
            (coordinate[1], coordinate[0])
            for coordinate in shortest["geometry"]["coordinates"]
        ]
        folium.PolyLine(
            shortest_coordinates,
            color=ROUTE_COLORS["shortest"],
            weight=9,
            opacity=0.42,
            tooltip="Shortest-distance route",
        ).add_to(map_view)
    if preferred is not None:
        preferred_coordinates = [
            (coordinate[1], coordinate[0])
            for coordinate in preferred["geometry"]["coordinates"]
        ]
        folium.PolyLine(
            preferred_coordinates,
            color=ROUTE_COLORS["preferred"],
            weight=6,
            opacity=0.95,
            tooltip="Safety-preferred route",
        ).add_to(map_view)

    for report in reports:
        latitude, longitude = report.get("latitude"), report.get("longitude")
        if latitude is None or longitude is None:
            continue
        popup = (
            "<b>Report #{}</b><br>{}<br>Status: {}<br>{}, {}"
            .format(
                report["id"],
                HAZARD_LABELS[report["hazard_type"]],
                report["status"],
                latitude,
                longitude,
            )
        )
        folium.CircleMarker(
            location=(latitude, longitude),
            radius=7,
            color=STATUS_COLORS[report["status"]],
            fill=True,
            fill_opacity=0.8,
            tooltip="{} · {}".format(
                HAZARD_LABELS[report["hazard_type"]],
                html.escape(report["description"] or "No description"),
            ),
            popup=folium.Popup(popup, max_width=280),
        ).add_to(map_view)

    if preferred is not None and preferred.get("detour"):
        folium.CircleMarker(
            location=preferred["detour"]["divergence"],
            radius=8,
            color=ROUTE_COLORS["preferred"],
            fill=True,
            fill_color="white",
            fill_opacity=1.0,
            weight=3,
            tooltip="Take the hazard detour at this junction",
        ).add_to(map_view)
        return_geometry = preferred["detour"].get("return_geometry")
        if return_geometry:
            from folium.plugins import PolyLineTextPath
            return_line = folium.PolyLine(
                return_geometry, color="#f59e0b", weight=4, dash_array="7 6",
                tooltip="U-turn: walk back to the junction",
            ).add_to(map_view)
            PolyLineTextPath(return_line, "\u279c", repeat=True, offset=5,
                             attributes={"fill": "#f59e0b", "font-size": "18"}).add_to(map_view)
        turnaround = preferred["detour"]["turnaround"]
        if turnaround:
            folium.Marker(
                location=turnaround,
                tooltip="Make a U-turn here and walk back to the junction",
                icon=folium.Icon(color="red", icon="repeat", prefix="fa"),
            ).add_to(map_view)

    if selected_location is not None:
        folium.Marker(
            location=(selected_location["latitude"], selected_location["longitude"]),
            tooltip="Selected hazard location",
            icon=folium.Icon(color="red", icon="warning-sign"),
        ).add_to(map_view)

    if start is not None:
        folium.Marker(
            location=start,
            tooltip="Your current location" if journey_active else "Starting point",
            icon=folium.Icon(
                color="darkblue" if journey_active else "blue",
                icon="user" if journey_active else "play",
            ),
        ).add_to(map_view)
    if destination is not None:
        folium.Marker(
            location=destination,
            tooltip="Destination",
            icon=folium.Icon(color="green", icon="flag"),
        ).add_to(map_view)
    bounds = [point for point in (start, destination) if point is not None]
    if selected_location is not None:
        hazard_point = (selected_location["latitude"], selected_location["longitude"])
        bounds.append(hazard_point)
    if current_position and st.session_state.get("map_focus_location"):
        map_view.location = current_position
        map_view.zoom_start = 17
    elif len(bounds) > 1:
        map_view.fit_bounds(bounds, padding=(28, 28))
    elif bounds:
        map_view.location = bounds[0]
        map_view.zoom_start = 17
    return map_view


def _masked_phone(phone):
    digits = "".join(character for character in phone if character.isdigit())
    return "••••{}".format(digits[-4:]) if digits else "not provided"


def _render_sos_demo():
    if st.session_state.pop("show_sos_demo", False):
        profile = st.session_state.get("user_profile", {})
        contacts = profile.get("contacts", [])
        recipients = ", ".join(
            "{} ({})".format(
                contact.get("name") or "Trusted contact",
                _masked_phone(contact.get("phone", "")),
            )
            for contact in contacts
        ) or "none saved"
        location = st.session_state.get("journey_location")
        st.error(
            "SOS DEMO ONLY — no SMS, call, location link, or emergency-service "
            "notification was sent."
        )
        st.markdown("**Simulated alert preview**")
        st.code(
            "SAFE NIGHT SOS\n"
            "From: {}\n"
            "Trusted contacts: {}\n"
            "Location: {}".format(
                profile.get("name") or "SafeNisha user",
                recipients,
                "{:.6f}, {:.6f}".format(
                    location["latitude"], location["longitude"]
                ) if location else "not available",
            ),
            language=None,
        )
        st.caption("Real SMS and official response require a configured, verified service.")


def _render_guardian_demo():
    if st.session_state.pop("show_guardian_demo", False):
        profile = st.session_state.get("user_profile", {})
        location = st.session_state.get("journey_location")
        st.info(
            "GUARDIAN MODE DEMO ONLY — this location update was not shared."
        )
        st.code(
            "SAFE NIGHT JOURNEY UPDATE\n"
            "From: {}\n"
            "Location: {}".format(
                profile.get("name") or "SafeNisha user",
                "{:.6f}, {:.6f}".format(
                    location["latitude"], location["longitude"]
                ) if location else "not available",
            ),
            language=None,
        )


def _render_profile_page():
    st.title("Profile & Guardian Mode")
    st.warning(
        "Local prototype only. Profile details are kept in this browser session and "
        "are not encrypted or sent to a guardian. Use test details, not real contact data."
    )
    existing = st.session_state.get("user_profile", {})
    defaults = {
        "profile_name": existing.get("name", ""),
        "profile_phone": existing.get("phone", ""),
    }
    contacts = existing.get("contacts", [])
    if not contacts and (existing.get("guardian_name") or existing.get("guardian_phone")):
        contacts = [{
            "name": existing.get("guardian_name", ""),
            "phone": existing.get("guardian_phone", ""),
        }]
    for index in range(3):
        contact = contacts[index] if index < len(contacts) else {}
        defaults["contact_{}_name".format(index + 1)] = contact.get("name", "")
        defaults["contact_{}_phone".format(index + 1)] = contact.get("phone", "")
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    st.subheader("Your details")
    with st.form("profile_form"):
        name = st.text_input("Your name", key="profile_name", max_chars=80)
        phone = st.text_input(
            "Your phone number", key="profile_phone", max_chars=40,
            help="Prototype only; use a placeholder instead of a real number.",
        )
        st.subheader("Trusted loved ones")
        for index in range(1, 4):
            st.markdown("**Trusted contact {}**".format(index))
            st.text_input(
                "Name", key="contact_{}_name".format(index), max_chars=80
            )
            st.text_input(
                "Phone number", key="contact_{}_phone".format(index), max_chars=40,
                help="No SMS is sent in this prototype.",
            )
        submitted = st.form_submit_button("Save for this session")
    if submitted:
        st.session_state["user_profile"] = {
            "name": name.strip(),
            "phone": phone.strip(),
            "contacts": [
                {
                    "name": st.session_state["contact_{}_name".format(index)].strip(),
                    "phone": st.session_state["contact_{}_phone".format(index)].strip(),
                }
                for index in range(1, 4)
                if st.session_state["contact_{}_name".format(index)].strip()
                or st.session_state["contact_{}_phone".format(index)].strip()
            ],
        }
        st.success("Profile saved in this session only.")

    profile = st.session_state.get("user_profile", {})
    st.divider()
    st.subheader("Guardian Mode")
    enabled = st.checkbox(
        "Enable Guardian Mode (demo)",
        key="guardian_mode_enabled",
        help="This only previews the flow; it does not share location with anyone.",
    )
    if enabled:
        st.info(
            "Guardian Mode is on for this demo. Secure hosted links and live sharing "
            "are not configured; no one else can view your location."
        )
        st.button(
            "Preview guardian alert",
            on_click=lambda: st.session_state.__setitem__("show_guardian_demo", True),
        )
        saved_contacts = profile.get("contacts", [])
        if saved_contacts:
            st.caption("Trusted contacts saved in this session:")
            for contact in saved_contacts:
                st.write(
                    "{} · {}".format(
                        contact.get("name") or "Unnamed contact",
                        _masked_phone(contact.get("phone", "")),
                    )
                )
        else:
            st.caption("No trusted contacts are saved in this session.")
        _render_guardian_demo()

    st.divider()
    st.subheader("Emergency alert")
    st.caption(
        "The SOS control below creates a local preview only. It does not send a "
        "beeping SMS, place a call, or contact officials."
    )
    st.button(
        "SOS — Demo only",
        type="primary",
        on_click=lambda: st.session_state.__setitem__("show_sos_demo", True),
    )
    _render_sos_demo()


def _update_journey_location(payload):
    if not isinstance(payload, dict):
        return
    if payload.get("error"):
        st.session_state["journey_location_error"] = payload["error"]
        st.session_state["journey_location"] = None
        st.session_state["journey_route_origin"] = None
        st.session_state["journey_route_origin_at"] = 0
        _invalidate_routes()
        return
    try:
        location = _parse_coordinates(
            "{}, {}".format(payload["latitude"], payload["longitude"]),
            "Current location",
        )
    except (KeyError, ValueError):
        return
    st.session_state["journey_location"] = {
        "latitude": location[0],
        "longitude": location[1],
        "accuracy": payload.get("accuracy"),
        "timestamp": payload.get("timestamp"),
    }
    st.session_state["journey_location_error"] = None


def _maybe_update_journey_route_origin():
    location = st.session_state.get("journey_location")
    if location is None:
        return
    current = (location["latitude"], location["longitude"])
    previous = st.session_state.get("journey_route_origin")
    now = datetime.now(timezone.utc).timestamp()
    previous_at = st.session_state.get("journey_route_origin_at", 0)
    if (previous is None or _distance_meters(previous, current) >= 50
            or now - previous_at >= 60):
        st.session_state["journey_route_origin"] = current
        st.session_state["journey_route_origin_at"] = now


def _route_instruction(step):
    maneuver = step.get("maneuver") or {}
    action = maneuver.get("type", "continue").replace("_", " ")
    modifier = maneuver.get("modifier")
    street = step.get("name")
    if modifier == "uturn":
        instruction = "Make a U-turn"
    elif action == "depart":
        instruction = "Start walking"
    elif action == "arrive":
        instruction = "Arrive at your destination"
    elif modifier:
        instruction = "{} {}".format(action.capitalize(), modifier)
    else:
        instruction = action.capitalize()
    if street:
        instruction += " onto {}".format(street)
    distance = step.get("distance")
    if isinstance(distance, (int, float)) and distance > 0:
        instruction += " · {}".format(_format_meters(distance))
    return instruction


def _render_directions(route):
    directions = [
        _route_instruction(step)
        for leg in route.get("legs", [])
        for step in leg.get("steps", [])
    ]
    if directions:
        st.subheader("Walking directions")
        for index, direction in enumerate(directions, start=1):
            st.markdown("**{}.** {}".format(index, direction))
        st.caption(
            "Directions and GPS can be delayed or inaccurate. Keep your screen available "
            "and use your own judgment; this prototype is not emergency navigation."
        )
    else:
        st.warning("The router did not return walking directions for this route.")


def _render_hazard_drawer(reports):
    if not st.session_state.get("hazard_drawer_open"):
        return
    with st.sidebar:
        st.header("Report a hazard")
        st.button("Close report panel", on_click=_close_hazard_drawer)
        selected_location = st.session_state.get("hazard_location")
        if selected_location is None:
            st.info("Select the affected point on the map.")
        else:
            st.markdown(
                "Selected point: **{:.6f}, {:.6f}**".format(
                    selected_location["latitude"], selected_location["longitude"]
                )
            )
        with st.form("report_hazard_form", clear_on_submit=True):
            hazard_type = st.selectbox(
                "Hazard type",
                options=list(HAZARD_LABELS),
                format_func=lambda value: HAZARD_LABELS[value],
            )
            description = st.text_area(
                "Description (optional, max 500 characters)", max_chars=500
            )
            submitted = st.form_submit_button(
                "Submit report", disabled=selected_location is None
            )
        if submitted and selected_location is not None:
            report_id = hazard_service.add_hazard(
                edge_id=None,
                hazard_type=hazard_type,
                description=description,
                latitude=selected_location["latitude"],
                longitude=selected_location["longitude"],
            )
            st.success("Report #{} saved for local review.".format(report_id))
            st.session_state["hazard_location"] = None
            st.session_state["hazard_drawer_open"] = False
            st.session_state["hazard_select_mode"] = False
            st.rerun()

        if reports:
            st.caption("New reports are not used in route scoring until locally confirmed.")
            unresolved = [report for report in reports if report["status"] != "resolved"]
            if unresolved:
                selected_id = st.selectbox(
                    "Review report",
                    options=[report["id"] for report in unresolved],
                    format_func=lambda report_id: "Report #{} — {}".format(
                        report_id,
                        next(item["status"] for item in unresolved
                             if item["id"] == report_id),
                    ),
                )
                current_status = next(
                    report["status"] for report in unresolved
                    if report["id"] == selected_id
                )
                first, second = st.columns(2)
                if first.button("Confirm report", disabled=current_status == "confirmed"):
                    hazard_service.set_report_status(selected_id, "confirmed")
                    st.rerun()
                if second.button("Resolve report"):
                    hazard_service.set_report_status(selected_id, "resolved")
                    st.rerun()


def _render_route_cards(
    shortest, preferred, beta, candidate_count, hazard_preview, hazard_blocked=False
):
    left, right = st.columns(2)
    with left:
        st.subheader("Original shortest route" if hazard_preview else "Shortest distance")
        st.metric("Walking distance", _format_meters(shortest["distance"]))
        st.metric("Synthetic risk indicator" if shortest.get("risk_source") == "synthetic" else "Estimated risk indicator", "{:.2f}".format(shortest["risk_indicator"]))
    with right:
        st.subheader("Safety-preferred")
        st.metric("Walking distance", _format_meters(preferred["distance"]))
        st.metric("Synthetic risk indicator" if preferred.get("risk_source") == "synthetic" else "Estimated risk indicator", "{:.2f}".format(preferred["risk_indicator"]))
    if hazard_blocked:
        st.warning("No route avoiding this hazard is available.")


def render_app():
    database.initialize_database()
    st.markdown(APP_CSS, unsafe_allow_html=True)
    st.session_state.setdefault("map_action", "Starting point")
    previous_click = st.session_state.get("last_map_click")
    if isinstance(previous_click, tuple) and len(previous_click) == 3:
        st.session_state["last_map_click"] = previous_click[1:]
    _apply_pending_map_selection()
    _apply_endpoint_focus(watch_endpoint_focus(
        st.session_state["map_action"], st.session_state.get("hazard_select_mode", False)
    ))

    if st.session_state.get("app_page") == "Synthetic data":
        st.session_state["app_page"] = "Plan journey"
    st.title("SafeNisha")
    page = st.radio(
        "App page",
        options=["Plan journey", "Profile & Guardian"],
        horizontal=True,
        label_visibility="collapsed",
        key="app_page",
    )
    if page == "Profile & Guardian":
        track_location(active=False, key="journey_location_tracker")
        _render_profile_page()
        return

    st.session_state.setdefault("map_location_pending", True)
    st.session_state.setdefault("map_location_request", 0)
    map_location_payload = track_location(
        active=False, once=st.session_state["map_location_pending"],
        request_id=st.session_state["map_location_request"], key="map_location_lookup",
    )
    _apply_map_location(map_location_payload)
    if st.session_state.get("map_location_error"):
        st.warning("Location unavailable: {}. Allow location access in your browser, then retry."
                   .format(st.session_state["map_location_error"]))

    st.markdown("### Plan a walking route")

    hazard_select_mode = st.session_state.get("hazard_select_mode", False)
    map_controls, map_column = st.columns([0.29, 0.71])
    with map_controls:
        st.text_input("Starting point", key="start_location",
                      placeholder="Click here, then choose on map",
                      on_change=_invalidate_routes)
        location_action, swap_action = st.columns([0.72, 0.28], gap="small")
        location_action.button("Use my current location", key="use_current_location_start",
                               on_click=_request_map_location, args=(True,))
        swap_action.button("", icon=":material/swap_vert:",
                           help="Swap start and destination", on_click=_swap_locations,
                           key="swap_endpoints")
        st.text_input("Destination", key="destination_location",
                      placeholder="Click here, then choose on map",
                      on_change=_invalidate_routes)
        active_field = "start_location" if st.session_state["map_action"] == "Starting point" else "destination_location"
        if not hazard_select_mode:
            st.markdown("<style>.st-key-" + active_field +
                        " input {outline: 2px solid #60a5fa; outline-offset: 1px;}</style>",
                        unsafe_allow_html=True)
        st.markdown("Safety preference")
        beta = st.slider("Safety preference", min_value=0.0, max_value=5.0,
                         value=2.0, step=0.1, label_visibility="collapsed")
        st.caption("Simulated risk - illustrative zones")
        if st.session_state.get("journey_active"):
            journey_button_col, journey_status_col = st.columns([0.25, 0.75])
            if journey_button_col.button("Stop journey", key="stop_journey"):
                st.session_state["journey_active"] = False
                st.session_state["journey_location"] = None
                st.session_state["journey_route_origin"] = None
                st.session_state["journey_route_origin_at"] = 0
                _invalidate_routes()
                st.rerun()
            if st.session_state.get("journey_location"):
                journey_status_col.info(
                    "Journey active · GPS updates while this page is open."
                )
            elif st.session_state.get("journey_location_error"):
                journey_status_col.warning(
                    "Journey started but waiting for location permission; retry by stopping "
                    "and starting again."
                )
            else:
                journey_status_col.info(
                    "Journey active · waiting for location permission."
                )
        elif st.button(
            "Start journey",
            type="primary",
            help="Requests your current location and starts a walking route to the destination.",
        ):
            try:
                _parse_coordinates(
                    st.session_state.get("destination_location", ""), "Destination"
                )
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["journey_active"] = True
                st.session_state["journey_location"] = None
                st.session_state["journey_location_error"] = None
                st.session_state["journey_route_origin"] = None
                st.session_state["journey_route_origin_at"] = 0
                _invalidate_routes()
                st.rerun()


    weights = _normalized_weights()
    layers = {}
    reports = hazard_service.get_reports()
    confirmed_hazards = hazard_service.get_confirmed_reports()
    journey_active = st.session_state.get("journey_active", False)
    location_payload = track_location(
        active=journey_active,
        key="journey_location_tracker",
    )
    if journey_active:
        _update_journey_location(location_payload)
        _maybe_update_journey_route_origin()
        if st.session_state.get("journey_location_error"):
            st.error(
                "Could not access your location: {}. Check browser permission and "
                "restart the journey to retry."
                .format(st.session_state["journey_location_error"])
            )

    current_start = None
    current_destination = None
    try:
        current_start = _parse_coordinates(
            st.session_state.get("start_location", ""), "Starting point"
        )
    except ValueError:
        pass
    if journey_active:
        current_location = st.session_state.get("journey_location")
        if current_location:
            current_start = (current_location["latitude"], current_location["longitude"])
    try:
        current_destination = _parse_coordinates(
            st.session_state.get("destination_location", ""), "Destination"
        )
    except ValueError:
        pass

    route_origin = (
        (st.session_state.get("journey_route_origin") or current_start)
        if journey_active else current_start
    )
    route_query = (
        (route_origin, current_destination)
        if route_origin is not None and current_destination is not None
        else None
    )
    previous_query = st.session_state.get("route_query")
    route_options = st.session_state.get("route_options")
    route_error = st.session_state.get("route_error")
    if route_query != previous_query:
        st.session_state["route_query"] = route_query
        st.session_state["route_options"] = None
        st.session_state["route_error"] = None
        route_options = None
        route_error = None
    if route_query is not None and route_options is None and route_error is None:
        try:
            with st.spinner("Finding walking routes…"):
                route_options = get_route_alternatives(route_origin, current_destination)
            st.session_state["route_options"] = route_options
        except ValueError as error:
            st.session_state["route_error"] = str(error)
            route_error = str(error)

    synthetic_zones = []
    if route_options:
        try:
            profiles = _synthetic_profiles(DATA_PATH.read_text(encoding="utf-8-sig"))
            synthetic_zones = build_demo_zones(profiles, route_options)
            layers = {"synthetic_zones": synthetic_zones}
        except (OSError, ValueError, KeyError) as error:
            st.error("Could not load synthetic routing data: {}".format(error))
    scored_routes = []
    shortest = preferred = None
    hazard_blocked = False
    hazard_note = None
    hazard_preview = st.session_state.get("hazard_location")
    if route_options:
        scoring_hazards = list(confirmed_hazards)
        if hazard_preview is not None:
            scoring_hazards.append(
                {
                    "latitude": hazard_preview["latitude"],
                    "longitude": hazard_preview["longitude"],
                    "preview": True,
                }
            )
        # The route the user saw before placing the hazard pin (confirmed hazards
        # only). A new hazard adds a U-turn and a return leg before the detour.
        scored_routes = score_route_alternatives(
            route_options, layers, confirmed_hazards, beta, weights
        )
        shortest, base_route = choose_route_pair(scored_routes)
        base_route = route_options[scored_routes.index(base_route)]
        scored_routes = score_route_alternatives(
            route_options, layers, scoring_hazards, beta, weights
        )
        preferred = next(
            route for route, original in zip(scored_routes, route_options)
            if original is base_route
        )
        shortest = min(scored_routes, key=lambda route: route["distance"])
        if hazard_preview is not None:
            pin_distance = _distance_to_route_meters(
                hazard_preview["latitude"], hazard_preview["longitude"],
                base_route["geometry"]["coordinates"],
            )
            if beta == 0:
                hazard_note = (
                    "Safety preference is 0, so hazards are not avoided. Raise it above "
                    "0 to reroute around the pin."
                )
            elif pin_distance > CORRIDOR_METERS:
                hazard_note = (
                    "The hazard pin is {} from the green route, so the route is not "
                    "changed. Click on the green route itself (within {:.0f} m) to "
                    "reroute around it.".format(
                        _format_meters(pin_distance), CORRIDOR_METERS
                    )
                )
        if beta > 0 and scoring_hazards and preferred["risk_factors"]["live_hazards"] > 0:
            detour = _hazard_detour(
                route_query, base_route, scoring_hazards, current_destination
            )
            if detour is not None:
                scored_routes = score_route_alternatives(
                    list(route_options) + [detour], layers, scoring_hazards, beta, weights
                )
                shortest = min(scored_routes[:-1], key=lambda route: route["distance"])
                # Follow the approach, U-turn, return leg, and clear detour.
                preferred = scored_routes[-1]
            else:
                clear_routes = [
                    route for route in scored_routes
                    if route["risk_factors"]["live_hazards"] == 0
                ]
                if clear_routes:
                    preferred = min(
                        clear_routes, key=lambda route: route["generalized_cost"]
                    )
                else:
                    hazard_blocked = True

    map_revision = repr((synthetic_zones, hazard_select_mode, hazard_preview, current_start, current_destination,
                         preferred["geometry"] if preferred else None,
                         st.session_state.get("map_location_revision", 0)))
    map_key = "route_map_" + hashlib.sha256(map_revision.encode()).hexdigest()[:16]
    if st.session_state.get("active_map_key") != map_key:
        st.session_state["map_click_events"] = {}
        st.session_state["active_map_key"] = map_key
        st.session_state["last_map_click"] = None
    with map_column, st.container(key="interactive_map"):
        map_data = st_folium(
            _make_route_map(
                reports, hazard_preview, shortest, preferred,
                current_start, current_destination, journey_active, synthetic_zones=synthetic_zones,
            ),
            height=560,
            use_container_width=True,
            returned_objects=["last_clicked", "last_object_clicked"],
            key=map_key,
        )
        st.button(
            ":material/my_location:", key="map_locate_button", on_click=_request_map_location,
            help="Show my current location",
        )
        st.button(
            "⚠", key="map_hazard_report", on_click=_open_hazard_drawer,
            help="Report a hazard: click to choose its location on the map.",
        )
    clicked = _new_map_click(map_data)
    if clicked:
        latitude, longitude = float(clicked["lat"]), float(clicked["lng"])
        fingerprint = (round(latitude, 6), round(longitude, 6))
        if fingerprint != st.session_state.get("last_map_click"):
            st.session_state["last_map_click"] = fingerprint
            st.session_state["pending_map_selection"] = (
                "hazard" if hazard_select_mode
                else st.session_state.get("map_action"),
                latitude,
                longitude,
            )
            st.rerun()
    else:
        st.session_state["map_click_initialized"] = True

    if route_error:
        st.error(route_error)
        st.button("Retry route", on_click=_invalidate_routes, key="retry_route")
    if hazard_blocked:
        st.button("Retry hazard detour", key="retry_hazard_detour",
                  on_click=lambda: st.session_state.pop("hazard_detour_cache", None))
    if hazard_note:
        st.warning(hazard_note)
    if shortest is not None and preferred is not None:
        _render_route_cards(
            shortest, preferred, beta, len(route_options), hazard_preview is not None,
            hazard_blocked,
        )
        if journey_active:
            _render_directions(preferred)

    if journey_active:
        if st.session_state.get("guardian_mode_enabled"):
            pass
            st.button(
                "Preview guardian update",
                on_click=lambda: st.session_state.__setitem__("show_guardian_demo", True),
            )
            _render_guardian_demo()
        st.button(
            "SOS — Demo only",
            type="primary",
            on_click=lambda: st.session_state.__setitem__("show_sos_demo", True),
            key="journey_sos_demo",
        )
        _render_sos_demo()

    if reports:
        with st.expander("Community hazard reports", expanded=False):
            st.dataframe(
                [
                    {
                        "ID": report["id"],
                        "Type": HAZARD_LABELS[report["hazard_type"]],
                        "Status": report["status"],
                        "Latitude": report["latitude"],
                        "Longitude": report["longitude"],
                        "Reported (UTC)": report["reported_at"][:19].replace("T", " "),
                        "Description": report["description"],
                    }
                    for report in reports
                ],
                use_container_width=True,
                hide_index=True,
            )

    _render_hazard_drawer(reports)
