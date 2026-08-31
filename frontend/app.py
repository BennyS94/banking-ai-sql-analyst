"""Single-page Streamlit application for banking analytics."""

from __future__ import annotations

import streamlit as st

from frontend.api_client import (
    APIClientConfig,
    APIClientConfigurationError,
    APIClientError,
    BankingAPIClient,
)
from frontend.state import add_recent_question


def _select_question(question: str) -> None:
    st.session_state["question_input"] = question


def _load_examples(config: APIClientConfig) -> tuple[dict[str, str], ...]:
    with BankingAPIClient(config) as client:
        return client.examples()


def _initialize_state() -> None:
    st.session_state.setdefault("question_input", "")
    st.session_state.setdefault("example_questions", None)
    st.session_state.setdefault("latest_query", None)
    st.session_state.setdefault("recent_questions", [])


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

    _initialize_state()

    try:
        config = APIClientConfig.from_env()
    except APIClientConfigurationError:
        st.error("The frontend backend URL configuration is invalid.")
        return

    st.subheader("Example questions")
    examples = st.session_state["example_questions"]
    if examples is None:
        try:
            examples = _load_examples(config)
        except APIClientError as exc:
            st.caption(exc.user_message)
        else:
            st.session_state["example_questions"] = examples
    if examples is not None:
        example_columns = st.columns(2)
        for index, example in enumerate(examples):
            example_columns[index % 2].button(
                example["question"],
                key=f"example-{example['id']}",
                on_click=_select_question,
                args=(example["question"],),
                use_container_width=True,
            )

    with st.form("banking-query-form"):
        question = st.text_area(
            "Banking question",
            key="question_input",
            placeholder=(
                "Ask in English or Romanian, for example: "
                "How many active loans are recorded?"
            ),
            help="Each question is analyzed independently.",
        )
        submitted = st.form_submit_button("Analyze", type="primary")

    if submitted:
        if not question.strip():
            st.warning("Enter a banking analytics question before analyzing.")
        else:
            try:
                with st.spinner(
                    "Generating and safely executing the banking query..."
                ):
                    with BankingAPIClient(config) as client:
                        response = client.query(question.strip())
            except APIClientError as exc:
                st.error(exc.user_message)
            else:
                submitted_question = question.strip()
                st.session_state["latest_query"] = {
                    "question": submitted_question,
                    "response": response,
                }
                st.session_state["recent_questions"] = add_recent_question(
                    st.session_state["recent_questions"], submitted_question
                )
                st.success("Banking analysis completed.")

    latest_query = st.session_state["latest_query"]
    if latest_query is not None:
        st.subheader("Latest analysis")
        st.caption(latest_query["question"])

    recent_questions = st.session_state["recent_questions"]
    if recent_questions:
        st.subheader("Recent questions")
        for index, recent_question in enumerate(recent_questions):
            st.button(
                recent_question,
                key=f"recent-{index}",
                on_click=_select_question,
                args=(recent_question,),
            )


if __name__ == "__main__":
    main()
