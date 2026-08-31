from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.main import create_app


class ExampleQuestionsAPITests(IsolatedAsyncioTestCase):
    async def test_examples_are_answerable_prompt_questions_without_sql(self) -> None:
        application = create_app(Settings(_env_file=None))
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/examples")

        self.assertEqual(response.status_code, 200)
        examples = response.json()
        self.assertEqual(len(examples), 6)
        self.assertEqual({item["language"] for item in examples}, {"en", "ro"})
        self.assertTrue(all(item["question"].strip() for item in examples))
        self.assertTrue(
            all(
                set(item) == {"id", "language", "question"}
                for item in examples
            )
        )
