"""Single-page Streamlit application for banking analytics."""

from __future__ import annotations

import streamlit as st

from frontend.api_client import (
    APIClientConfig,
    APIClientConfigurationError,
    APIClientError,
    BankingAPIClient,
)


def main() -> None:
    """Render the Streamlit application foundation."""
    st.set_page_config(
        page_title="Banking AI SQL Analyst",
        page_icon=None,
        layout="wide",
    )

    st.title("Banking AI SQL Analyst")
    st.write(
        "Ask analytical questions about the synthetic banking dataset. "
        "Questions are converted into validated read-only PostgreSQL queries."
    )

    with st.form("banking-query-form"):
        question = st.text_area(
            "Banking question",
            placeholder="Ask a standalone banking analytics question...",
        )
        submitted = st.form_submit_button("Analyze", type="primary")

    if not submitted:
        return
    if not question.strip():
        st.warning("Enter a banking analytics question before analyzing.")
        return

    try:
        config = APIClientConfig.from_env()
        with st.spinner("Connecting to the application backend..."):
            with BankingAPIClient(config) as client:
                client.health()
    except APIClientConfigurationError:
        st.error("The frontend backend URL configuration is invalid.")
    except APIClientError as exc:
        st.error(exc.user_message)
    else:
        st.success("Connected to the application backend.")


if __name__ == "__main__":
    main()
