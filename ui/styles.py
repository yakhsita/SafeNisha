"""Shared CSS for the local SafeNisha prototype."""

APP_CSS = """
<style>
    .stApp { background: #0a0f1f; color: #e5e7eb; }
    [data-testid="stSidebar"] { background: #111827; }
    div[data-testid="stMetric"] {
        background: #111827;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 14px;
    }
    div[data-testid="stAlert"] { border-radius: 12px; }
    h1, h2, h3 { color: #f8fafc; }
    .st-key-interactive_map { position: relative; }
    .st-key-swap_endpoints {
        display: flex; justify-content: flex-end;
        width: 100%; margin-top: 0;
    }
    .st-key-swap_endpoints [data-testid="stTooltipIcon"],
    .st-key-swap_endpoints .stTooltipHoverTarget {
        margin-left: auto; width: fit-content !important;
    }
    .st-key-swap_endpoints button { width: 40px; min-width: 40px; padding: 0; }
    .st-key-map_hazard_report, .st-key-map_locate_button {
        position: absolute; bottom: 32px; width: 46px; z-index: 10;
    }
    .st-key-map_hazard_report { right: 18px; }
    .st-key-map_locate_button { right: 74px; }
    .st-key-map_hazard_report button, .st-key-map_locate_button button {
        width: 46px; height: 46px; min-height: 46px; padding: 0;
        border-radius: 50%; border: 2px solid white;
        color: white; box-shadow: 0 3px 12px #0008;
    }
    .st-key-map_hazard_report button { background: #dc2626; }
    .st-key-map_locate_button button { background: #2563eb; }
    .st-key-map_hazard_report button p { font-size: 23px; }
    .st-key-map_hazard_report button:hover { background: #b91c1c; color: white; }
    .st-key-map_locate_button button:hover { background: #1d4ed8; color: white; }
    .st-key-map_hazard_report button:focus-visible,
    .st-key-map_locate_button button:focus-visible { outline: 3px solid #93c5fd; }
    /* Location lookups stay silent; map controls show the useful actions. */
    .st-key-map_location_lookup, .st-key-journey_location_tracker { display: none; }
</style>
"""
