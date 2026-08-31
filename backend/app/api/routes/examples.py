"""Public example questions for the banking analytics UI."""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from backend.app.ai.prompt import PromptResourceError, load_few_shot_examples


router = APIRouter(prefix="/api/v1", tags=["examples"])


class ExampleQuestionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    language: Literal["en", "ro"]
    question: str


@router.get("/examples", response_model=list[ExampleQuestionResponse])
def get_example_questions() -> list[ExampleQuestionResponse]:
    """Return answerable prompt examples without exposing generated SQL."""
    try:
        examples = load_few_shot_examples()
    except PromptResourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Banking example questions are unavailable",
        ) from exc
    return [
        ExampleQuestionResponse(
            id=example.id,
            language=example.language,
            question=example.question,
        )
        for example in examples
        if example.output.status == "answerable"
    ]
