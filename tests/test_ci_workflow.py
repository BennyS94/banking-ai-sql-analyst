from pathlib import Path
from unittest import TestCase


class AutomatedTestWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = Path(".github/workflows/test.yml").read_text(
            encoding="utf-8"
        )

    def test_push_pull_request_python_and_postgres_are_configured(self) -> None:
        self.assertIn("push:", self.workflow)
        self.assertIn("pull_request:", self.workflow)
        self.assertIn("branches: [main]", self.workflow)
        self.assertIn("actions/checkout@v7", self.workflow)
        self.assertIn("actions/setup-python@v7", self.workflow)
        self.assertIn('python-version: "3.13"', self.workflow)
        self.assertIn("postgres:17.11-alpine", self.workflow)
        self.assertIn("BANKING_TEST_OWNER_DATABASE_URL:", self.workflow)

    def test_migrations_static_checks_and_complete_suite_are_visible(self) -> None:
        self.assertIn("python -m compileall", self.workflow)
        self.assertIn("python -m pip check", self.workflow)
        self.assertIn("python -m alembic upgrade head", self.workflow)
        self.assertIn("Run offline unit checks", self.workflow)
        self.assertIn("Run PostgreSQL integration and regression suite", self.workflow)
        self.assertIn(
            "python -m unittest discover -s tests -q", self.workflow
        )

    def test_workflow_never_configures_groq_or_live_evaluation(self) -> None:
        self.assertNotIn("GROQ_API_KEY", self.workflow)
        self.assertNotIn("backend.app.evaluation", self.workflow)
        self.assertNotIn("continue-on-error", self.workflow)
