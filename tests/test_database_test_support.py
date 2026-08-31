from __future__ import annotations

import os
from unittest import TestCase, mock

import psycopg
from sqlalchemy.engine import make_url

from database_test_support import (
    TEST_OWNER_DATABASE_URL_ENV,
    TestDatabaseConfigurationError,
    _connection_kwargs,
    configured_test_owner_url,
    temporary_database,
)


class TestDatabaseConfigurationTests(TestCase):
    def test_foreign_database_url_does_not_configure_test_harness(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql+psycopg://foreign_user:foreign_password@"
                    "foreign-host/foreign_database"
                )
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                TestDatabaseConfigurationError,
                f"{TEST_OWNER_DATABASE_URL_ENV} must be set",
            ):
                configured_test_owner_url()

    def test_project_specific_url_wins_when_foreign_database_url_exists(self) -> None:
        project_url = (
            "postgresql+psycopg://banking_owner:test_password@"
            "banking-host:55432/banking_ai"
        )
        with mock.patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql+psycopg://foreign_user:foreign_password@"
                    "foreign-host/foreign_database"
                ),
                TEST_OWNER_DATABASE_URL_ENV: project_url,
            },
            clear=True,
        ):
            configured = configured_test_owner_url()

        self.assertEqual(configured.username, "banking_owner")
        self.assertEqual(configured.host, "banking-host")
        self.assertEqual(configured.port, 55432)
        self.assertEqual(configured.database, "banking_ai")


class TemporaryDatabaseCleanupTests(TestCase):
    def test_temporary_database_is_removed_after_context_exit(self) -> None:
        owner_url = configured_test_owner_url()
        with temporary_database() as temporary_url:
            temporary_name = make_url(temporary_url).database
            with psycopg.connect(
                **_connection_kwargs(owner_url, "postgres"), autocommit=True
            ) as connection:
                exists_during_context = connection.execute(
                    "SELECT EXISTS (SELECT FROM pg_database WHERE datname = %s)",
                    (temporary_name,),
                ).fetchone()[0]

        with psycopg.connect(
            **_connection_kwargs(owner_url, "postgres"), autocommit=True
        ) as connection:
            exists_after_context = connection.execute(
                "SELECT EXISTS (SELECT FROM pg_database WHERE datname = %s)",
                (temporary_name,),
            ).fetchone()[0]

        self.assertTrue(exists_during_context)
        self.assertFalse(exists_after_context)
