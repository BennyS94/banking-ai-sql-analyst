"""Single-page Streamlit application for banking analytics."""

from __future__ import annotations

import streamlit as st

from frontend.api_client import (
    APIClientConfig,
    APIClientConfigurationError,
    APIClientError,
    BankingAPIClient,
)
from frontend.presentation import (
    QueryPresentation,
    QueryPresentationError,
    build_query_presentation,
    format_duration,
)
from frontend.state import add_recent_question
from frontend.ux import (
    ErrorPresentation,
    SemanticPresentation,
    client_error_presentation,
    semantic_presentation,
)


def _select_question(question: str) -> None:
    st.session_state["question_input"] = question


def _load_examples(config: APIClientConfig) -> tuple[dict[str, str], ...]:
    with BankingAPIClient(config) as client:
        return client.examples()


def _initialize_state() -> None:
    st.session_state.setdefault("question_input", "")
    st.session_state.setdefault("example_questions", None)
    st.session_state.setdefault("latest_query", None)
    st.session_state.setdefault("latest_error", None)
    st.session_state.setdefault("recent_questions", [])


def _render_answerable_result(question: str, response: dict[str, object]) -> None:
    try:
        presentation = build_query_presentation(response)
    except QueryPresentationError:
        st.error("The application backend returned an invalid query response.")
        return

    st.divider()
    st.subheader("Question")
    st.write(question)

    st.subheader("Generated SQL")
    st.code(presentation.sql, language="sql")

    st.subheader("Query results")
    if presentation.returned_row_count == 0:
        st.info("The query executed successfully but returned no rows.")
    st.dataframe(presentation.dataframe, width="stretch", hide_index=True)
    if presentation.truncated:
        st.warning(
            "Showing the first "
            f"{presentation.returned_row_count:,} rows returned by the "
            "application limit."
        )

    st.subheader("Execution details")
    metadata_columns = st.columns(4)
    metadata_columns[0].metric(
        "Rows returned", f"{presentation.returned_row_count:,}"
    )
    metadata_columns[1].metric(
        "SQL time", format_duration(presentation.execution_ms)
    )
    metadata_columns[2].metric(
        "AI time", format_duration(presentation.generation_ms)
    )
    metadata_columns[3].metric("Model", presentation.model or "Not provided")

    if presentation.repair_used:
        st.info("SQL was automatically corrected once before execution.")

    _render_technical_details(presentation)


def _render_technical_details(presentation: QueryPresentation) -> None:
    details: list[tuple[str, str]] = []
    if presentation.reasoning_effort:
        details.append(("Reasoning effort", presentation.reasoning_effort))
    if presentation.statement_timeout_ms is not None:
        details.append(
            ("Statement timeout", f"{presentation.statement_timeout_ms:,} ms")
        )
    if presentation.input_tokens is not None:
        details.append(("Input tokens", f"{presentation.input_tokens:,}"))
    if presentation.output_tokens is not None:
        details.append(("Output tokens", f"{presentation.output_tokens:,}"))
    if presentation.finish_reason:
        details.append(("Finish reason", presentation.finish_reason))
    if presentation.provider_request_id:
        details.append(("Provider request ID", presentation.provider_request_id))
    if not details:
        return

    with st.expander("Technical details"):
        for label, value in details:
            st.write(f"**{label}:** {value}")


def _render_semantic_result(
    question: str, response: dict[str, object]
) -> None:
    presentation = semantic_presentation(
        response.get("status"), response.get("message")
    )
    if presentation is None:
        st.error("The application backend returned an invalid query response.")
        return

    st.divider()
    st.subheader("Question")
    st.write(question)
    st.subheader(presentation.title)
    _render_semantic_message(presentation)


def _render_semantic_message(presentation: SemanticPresentation) -> None:
    if presentation.level == "warning":
        st.warning(presentation.message)
    else:
        st.info(presentation.message)


def _render_query_error(presentation: ErrorPresentation) -> None:
    st.divider()
    st.error(f"**{presentation.title}**\n\n{presentation.message}")


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
            submitted_question = question.strip()
            st.session_state["recent_questions"] = add_recent_question(
                st.session_state["recent_questions"], submitted_question
            )
            try:
                with st.spinner(
                    "Generating and safely executing the banking query..."
                ):
                    with BankingAPIClient(config) as client:
                        response = client.query(submitted_question)
            except APIClientError as exc:
                st.session_state["latest_query"] = None
                st.session_state["latest_error"] = client_error_presentation(exc)
            else:
                st.session_state["latest_query"] = {
                    "question": submitted_question,
                    "response": response,
                }
                st.session_state["latest_error"] = None

    latest_query = st.session_state["latest_query"]
    if latest_query is not None:
        latest_response = latest_query["response"]
        if latest_response.get("status") == "answerable":
            _render_answerable_result(latest_query["question"], latest_response)
        else:
            _render_semantic_result(latest_query["question"], latest_response)

    latest_error = st.session_state["latest_error"]
    if latest_error is not None:
        _render_query_error(latest_error)

    recent_questions = st.session_state["recent_questions"]
    if recent_questions:
        st.subheader("Recent questions")
        recent_columns = st.columns(2)
        for index, recent_question in enumerate(recent_questions):
            recent_columns[index % 2].button(
                recent_question,
                key=f"recent-{index}",
                on_click=_select_question,
                args=(recent_question,),
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
