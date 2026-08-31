"""Stable public query error payload helpers."""

from fastapi import HTTPException


def query_http_error(
    status_code: int, category: str, message: str
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"category": category, "message": message},
    )
