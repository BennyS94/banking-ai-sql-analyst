from __future__ import annotations

import os
from unittest import TestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from backend.app.ai.benchmark import (
    BenchmarkValidationError,
    load_banking_benchmark,
    validate_few_shot_separation,
)
from backend.app.ai.prompt import load_few_shot_examples
from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import ReadOnlyQueryExecutor
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database


class BankingBenchmarkIntegrityTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_banking_benchmark()

    def test_case_count_ids_questions_and_contracts_are_valid(self) -> None:
        self.assertEqual(len(self.cases), 23)
        self.assertEqual(len({case.id for case in self.cases}), len(self.cases))
        self.assertEqual(
            len({case.question.casefold() for case in self.cases}), len(self.cases)
        )
        for case in self.cases:
            self.assertTrue(case.question.strip())
            if case.expected_status == "answerable":
                self.assertTrue(case.reference_sql)
            else:
                self.assertIsNone(case.reference_sql)

    def test_languages_categories_and_statuses_are_represented(self) -> None:
        self.assertEqual({case.language for case in self.cases}, {"en", "ro"})
        self.assertEqual(
            {case.expected_status for case in self.cases},
            {"answerable", "unanswerable", "ambiguous"},
        )
        self.assertTrue(
            {
                "filter",
                "aggregation",
                "grouping",
                "single_join",
                "multi_table_join",
                "ranking",
                "temporal",
                "having",
                "subquery_cte",
                "window_function",
                "unanswerable",
                "ambiguous",
            }.issubset({case.category for case in self.cases})
        )

    def test_benchmark_has_no_exact_overlap_with_few_shot_questions(self) -> None:
        validate_few_shot_separation(self.cases, load_few_shot_examples())

    def test_overlap_validation_normalizes_case_and_whitespace(self) -> None:
        examples = load_few_shot_examples()
        overlapping = self.cases[0].model_copy(
            update={"question": f"  {examples[0].question.upper()}  "}
        )
        with self.assertRaises(BenchmarkValidationError):
            validate_few_shot_separation((overlapping,), examples)


class BankingBenchmarkReferenceSQLTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.owner_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(
            os.environ, {"DATABASE_URL": cls.owner_url}
        )
        cls.environment.start()
        command.upgrade(Config("alembic.ini"), "head")
        cls.reader_user = f"banking_benchmark_reader_{uuid4().hex}"
        cls.reader_password = f"test-{uuid4().hex}"
        provision_reader_role(
            cls.owner_url, cls.reader_user, cls.reader_password
        )
        reader_url = make_url(cls.owner_url).set(
            username=cls.reader_user,
            password=cls.reader_password,
        )
        cls.engine = create_runtime_engine(
            Settings(
                banking_reader_user=cls.reader_user,
                banking_reader_database_url=reader_url.render_as_string(
                    hide_password=False
                ),
            )
        )
        cls.executor = ReadOnlyQueryExecutor(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with psycopg.connect(
            _psycopg_connection_string(cls.owner_url), autocommit=True
        ) as connection:
            role = sql.Identifier(cls.reader_user)
            connection.execute(sql.SQL("DROP OWNED BY {}").format(role))
            connection.execute(sql.SQL("DROP ROLE {}").format(role))
        cls.environment.stop()
        cls.database_context.__exit__(None, None, None)

    def test_all_answerable_reference_sql_executes(self) -> None:
        answerable = [
            case
            for case in load_banking_benchmark()
            if case.expected_status == "answerable"
        ]

        for case in answerable:
            with self.subTest(case=case.id):
                result = self.executor.execute(case.reference_sql or "")
                self.assertGreaterEqual(result.row_count, 0)
