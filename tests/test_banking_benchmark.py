from __future__ import annotations

import os
from collections import Counter
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
from backend.app.ai.groq_client import StructuredGeneration
from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine
from backend.app.db.query_executor import ReadOnlyQueryExecutor
from backend.app.safety.access_policy import BankingSQLAccessPolicy
from backend.app.safety.sql_validator import SQLASTValidator
from backend.app.evaluation.safety_metrics import evaluate_safety_policy
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
        self.assertEqual(len(self.cases), 52)
        self.assertEqual(len({case.id for case in self.cases}), len(self.cases))
        self.assertEqual(
            len({case.question.casefold() for case in self.cases}), len(self.cases)
        )
        for case in self.cases:
            self.assertTrue(case.question.strip())
            if case.expected_status == "answerable":
                self.assertTrue(case.reference_sql)
                self.assertIsNotNone(case.comparison_mode)
            else:
                self.assertIsNone(case.reference_sql)
                self.assertIsNone(case.comparison_mode)

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
                "single_table",
                "null_semantics",
                "negative_balance",
                "transaction_semantics",
                "loan_account",
                "branch_analytics",
                "unanswerable",
                "ambiguous",
            }.issubset({case.category for case in self.cases})
        )

    def test_benchmark_distribution_and_cross_language_pairs_are_balanced(self) -> None:
        self.assertEqual(Counter(case.language for case in self.cases), {"en": 35, "ro": 17})
        self.assertEqual(
            Counter(case.difficulty for case in self.cases),
            {"easy": 20, "medium": 21, "hard": 11},
        )
        self.assertEqual(
            Counter(case.expected_status for case in self.cases),
            {"answerable": 40, "unanswerable": 7, "ambiguous": 5},
        )
        paired = [case for case in self.cases if case.pair_id is not None]
        self.assertEqual(len(paired), 6)
        for pair_id in {case.pair_id for case in paired}:
            pair = [case for case in paired if case.pair_id == pair_id]
            self.assertEqual({case.language for case in pair}, {"en", "ro"})
            self.assertEqual(
                {case.reference_sql for case in pair}, {pair[0].reference_sql}
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

    def test_reference_sql_cannot_overlap_few_shot_sql(self) -> None:
        examples = load_few_shot_examples()
        leaking = examples[0].model_copy(
            update={
                "output": StructuredGeneration(
                    status="answerable",
                    sql=f"  {self.cases[0].reference_sql};  ",
                    message=None,
                )
            }
        )
        with self.assertRaises(BenchmarkValidationError):
            validate_few_shot_separation(self.cases, (leaking,))


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
        cls.structural_validator = SQLASTValidator()
        cls.access_policy = BankingSQLAccessPolicy.from_engine(cls.engine)

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
                structural = self.structural_validator.validate(case.reference_sql or "")
                self.assertTrue(structural.accepted)
                self.assertTrue(self.access_policy.validate(structural).accepted)
                result = self.executor.execute(case.reference_sql or "")
                self.assertGreaterEqual(result.row_count, 0)

    def test_safety_metrics_separate_attacks_and_legitimate_sql(self) -> None:
        result = evaluate_safety_policy(
            load_banking_benchmark(), self.access_policy
        )
        self.assertEqual(result.adversarial_total, 41)
        self.assertEqual(result.adversarial_blocked, 41)
        self.assertEqual(result.legitimate_total, 40)
        self.assertEqual(result.legitimate_accepted, 40)
        self.assertEqual(result.legitimate_false_positive_rejections, ())
