"""Natural-language banking query API."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from backend.app.ai.context import BankingContextError
from backend.app.ai.groq_client import (
    GroqConfigurationError,
    GroqRateLimitError,
    GroqRequestError,
    GroqTimeoutError,
    GroqUnavailableError,
    InvalidStructuredResponseError,
)
from backend.app.ai.prompt import PromptResourceError
from backend.app.ai.service import QuestionValidationError
from backend.app.api.dependencies import get_safe_query_service
from backend.app.api.query_errors import query_http_error
from backend.app.api.query_models import QueryRequest, QueryResponse
from backend.app.db.query_executor import (
    QueryDatabaseError,
    QueryExecutionError,
    QueryTimeoutError,
)
from backend.app.db.schema import SchemaIntrospectionError
from backend.app.query.service import (
    QueryRepairError,
    QuerySafetyError,
    SafeQueryService,
)


router = APIRouter(prefix="/api/v1", tags=["queries"])


@router.post("/query", response_model=QueryResponse)
def query_banking_data(
    request: QueryRequest,
    service: Annotated[SafeQueryService, Depends(get_safe_query_service)],
) -> QueryResponse:
    """Generate, validate and safely execute SQL for one natural-language question."""
    try:
        result = service.query(request.question)
    except QuestionValidationError as exc:
        raise query_http_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_question",
            str(exc),
        ) from exc
    except GroqTimeoutError as exc:
        raise query_http_error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "provider_timeout",
            "The query generation service timed out",
        ) from exc
    except GroqRateLimitError as exc:
        raise query_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "provider_rate_limit",
            "The query generation service is temporarily rate limited",
        ) from exc
    except GroqUnavailableError as exc:
        raise query_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "provider_unavailable",
            "The query generation service is unavailable",
        ) from exc
    except GroqRequestError as exc:
        raise query_http_error(
            status.HTTP_502_BAD_GATEWAY,
            "provider_request",
            "The query generation request failed",
        ) from exc
    except GroqConfigurationError as exc:
        raise query_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "provider_configuration",
            "The query generation service is not configured",
        ) from exc
    except InvalidStructuredResponseError as exc:
        raise query_http_error(
            status.HTTP_502_BAD_GATEWAY,
            "invalid_generation_protocol",
            "The query generation service returned an invalid response",
        ) from exc
    except QuerySafetyError as exc:
        raise query_http_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "safety_rejection",
            "Generated SQL did not pass the safety policy",
        ) from exc
    except QueryTimeoutError as exc:
        raise query_http_error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "database_timeout",
            "The banking query exceeded its execution limit",
        ) from exc
    except QueryExecutionError as exc:
        raise query_http_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "query_execution_error",
            "The generated query could not be executed",
        ) from exc
    except QueryRepairError as exc:
        raise query_http_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "query_repair_failed",
            "The generated query could not be corrected",
        ) from exc
    except (QueryDatabaseError, SchemaIntrospectionError) as exc:
        raise query_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_failure",
            "The banking query service is temporarily unavailable",
        ) from exc
    except (BankingContextError, PromptResourceError) as exc:
        raise query_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "context_failure",
            "The banking generation context is unavailable",
        ) from exc
    return QueryResponse.from_result(result)
