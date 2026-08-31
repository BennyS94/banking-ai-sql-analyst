"""Small pure helpers for current-session Streamlit state."""

from __future__ import annotations

from collections.abc import Iterable


RECENT_QUESTION_LIMIT = 5


def add_recent_question(
    questions: Iterable[str],
    question: str,
    *,
    limit: int = RECENT_QUESTION_LIMIT,
) -> list[str]:
    """Return newest-first, deduplicated recent questions for one session."""
    normalized_question = question.strip()
    if not normalized_question:
        return [item for item in questions if item.strip()][:limit]
    remaining = [
        item.strip()
        for item in questions
        if item.strip() and item.strip() != normalized_question
    ]
    return [normalized_question, *remaining][:limit]
