from __future__ import annotations

import os
from unittest import IsolatedAsyncioTestCase, TestCase, mock

from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings, get_settings
from backend.app.db.engine import (
    dispose_runtime_engine,
    get_runtime_engine,
)
from backend.app.main import create_app


class BackendFoundationTests(TestCase):
    def tearDown(self) -> None:
        dispose_runtime_engine()
        get_settings.cache_clear()

    def test_application_uses_explicit_settings(self) -> None:
        application = create_app(
            Settings(app_title="Test Banking API", app_version="9.8.7")
        )

        self.assertEqual(application.title, "Test Banking API")
        self.assertEqual(application.version, "9.8.7")

    def test_settings_read_environment_variables(self) -> None:
        environment = {
            "APP_TITLE": "Environment Banking API",
            "APP_VERSION": "2.1.0",
            "QUERY_STATEMENT_TIMEOUT_MS": "2500",
            "QUERY_MAX_ROWS": "125",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            settings = Settings()

        self.assertEqual(settings.app_title, "Environment Banking API")
        self.assertEqual(settings.app_version, "2.1.0")
        self.assertEqual(settings.query_statement_timeout_ms, 2_500)
        self.assertEqual(settings.query_max_rows, 125)

    def test_repeated_cleanup_without_initialized_engine_is_safe(self) -> None:
        get_runtime_engine.cache_clear()

        dispose_runtime_engine()
        dispose_runtime_engine()

        self.assertEqual(get_runtime_engine.cache_info().currsize, 0)


class HealthEndpointTests(IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        dispose_runtime_engine()
        get_settings.cache_clear()

    async def test_health_endpoint_reports_process_health(self) -> None:
        application = create_app(
            Settings(banking_reader_database_url=None, _env_file=None)
        )
        transport = ASGITransport(app=application)
        async with application.router.lifespan_context(application):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    async def test_lifecycle_without_database_access_does_not_create_engine(self) -> None:
        get_runtime_engine.cache_clear()
        application = create_app(
            Settings(banking_reader_database_url=None, _env_file=None)
        )

        async with application.router.lifespan_context(application):
            self.assertEqual(get_runtime_engine.cache_info().currsize, 0)

        self.assertEqual(get_runtime_engine.cache_info().currsize, 0)

    async def test_initialized_runtime_engine_is_disposed_on_shutdown(self) -> None:
        environment = {
            "BANKING_READER_USER": "banking_reader",
            "BANKING_READER_DATABASE_URL": (
                "postgresql+psycopg://banking_reader:secret@localhost/banking_ai"
            ),
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            get_settings.cache_clear()
            engine = get_runtime_engine()
            application = create_app(Settings(_env_file=None))
            with mock.patch.object(
                engine, "dispose", wraps=engine.dispose
            ) as dispose:
                async with application.router.lifespan_context(application):
                    self.assertIs(get_runtime_engine(), engine)

                dispose.assert_called_once_with()

        self.assertEqual(get_runtime_engine.cache_info().currsize, 0)
