"""Banking database schema API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Engine

from backend.app.api.schema_models import DatabaseSchemaResponse
from backend.app.db.engine import get_runtime_engine
from backend.app.db.schema import SchemaIntrospectionError, introspect_banking_schema


router = APIRouter(prefix="/api/v1/database", tags=["database"])


@router.get("/schema", response_model=DatabaseSchemaResponse)
def get_banking_schema(
    engine: Annotated[Engine, Depends(get_runtime_engine)],
) -> DatabaseSchemaResponse:
    """Return deterministic metadata for the approved banking schema."""
    try:
        database_schema = introspect_banking_schema(engine)
    except SchemaIntrospectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Banking schema metadata is temporarily unavailable",
        ) from exc
    return DatabaseSchemaResponse.from_database_schema(database_schema)
