"""Streamlit entry point. Run with: streamlit run app/main.py"""

import sys
from pathlib import Path

# Streamlit only puts app/ on the path; the engine lives in the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Crisis Scenario Engine", layout="wide")
if "player" in st.query_params:
    # A player's link: only that page, without the menu of the control team's pages.
    page = st.navigation([st.Page("views/player.py", title="Player")], position="hidden")
else:
    from app import ui  # noqa: E402
    from app.design import get_config, keep_widget_state  # noqa: E402

    keep_widget_state(get_config())
    on_home = ui.LANDING == "home"
    # The home page, then the phases of the EBA Handbook: design, prepare, deliver, close.
    page = st.navigation(
        [
            st.Page("views/home.py", title="Home", default=on_home, **({} if on_home else {"url_path": "home"})),
            st.Page("views/design.py", title="Design", default=not on_home,
                    **({"url_path": "design"} if on_home else {})),
            st.Page("views/prepare.py", title="Prepare", url_path="prepare"),
            st.Page("views/console.py", title="Deliver", url_path="console"),
            st.Page("views/close.py", title="Close", url_path="close"),
            st.Page("views/assumptions.py", title="Assumptions and limits", url_path="assumptions"),
        ],
        # The header of each page is the navigation (app/ui.py): the phases as a stepper.
        position="hidden",
    )
page.run()
