"""Composition dependencies for the safe banking query flow."""

from typing import Annotated

from fastapi import Depends, status
from sqlalchemy import Engine

from backend.app.ai.context import BankingAIContextBuilder
from backend.app.ai.groq_client import (
    GroqConfigurationError,
    GroqStructuredGenerationClient,
)
from backend.app.ai.service import NLToSQLService
from backend.app.api.query_errors import query_http_error
from backend.app.core.config import Settings, get_settings
from backend.app.db.engine import get_runtime_engine
from backend.app.db.query_executor import ReadOnlyQueryExecutor
from backend.app.db.schema import SchemaIntrospectionError
from backend.app.query.service import SafeQueryService
from backend.app.safety.access_policy import BankingSQLAccessPolicy
from backend.app.safety.sql_validator import SQLASTValidator


def get_safe_query_service(
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[Engine, Depends(get_runtime_engine)],
) -> SafeQueryService:
    """Compose the production safety path from existing component boundaries."""
    try:
        generation_client = GroqStructuredGenerationClient(settings)
        access_policy = BankingSQLAccessPolicy.from_engine(engine)
    except GroqConfigurationError as exc:
        raise query_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "provider_configuration",
            "The query generation service is not configured",
        ) from exc
    except SchemaIntrospectionError as exc:
        raise query_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_failure",
            "Banking schema metadata is temporarily unavailable",
        ) from exc

    return SafeQueryService(
        NLToSQLService(BankingAIContextBuilder(engine), generation_client),
        SQLASTValidator(),
        access_policy,
        ReadOnlyQueryExecutor(
            engine,
            statement_timeout_ms=settings.query_statement_timeout_ms,
            max_rows=settings.query_max_rows,
        ),
    )
