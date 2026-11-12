"""SafeNisha local prototype entry point."""

import streamlit as st

from ui.frontend_demo import render_app


st.set_page_config(
    page_title="SafeNisha · Route prototype",
    page_icon="🌙",
    layout="wide",
)

render_app()
