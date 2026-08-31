from __future__ import annotations

import os
from unittest import IsolatedAsyncioTestCase, TestCase, mock

from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings, get_settings
from backend.app.main import create_app


class BackendFoundationTests(TestCase):
    def tearDown(self) -> None:
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
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            settings = Settings()

        self.assertEqual(settings.app_title, "Environment Banking API")
        self.assertEqual(settings.app_version, "2.1.0")


class HealthEndpointTests(IsolatedAsyncioTestCase):
    async def test_health_endpoint_reports_process_health(self) -> None:
        application = create_app(Settings())
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
