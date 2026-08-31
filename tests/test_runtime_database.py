from __future__ import annotations

import os
from unittest import TestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from backend.app.core.config import Settings
from backend.app.db.engine import (
    DatabaseConfigurationError,
    DatabaseConnectionError,
    check_database_connection,
    create_runtime_engine,
    runtime_connection,
)
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database


class RuntimeDatabaseConfigurationTests(TestCase):
    def test_runtime_url_is_required_when_database_access_is_requested(self) -> None:
        with self.assertRaisesRegex(
            DatabaseConfigurationError, "BANKING_READER_DATABASE_URL must be set"
        ):
            create_runtime_engine(Settings(banking_reader_database_url=None))

    def test_runtime_url_must_use_postgresql(self) -> None:
        with self.assertRaisesRegex(DatabaseConfigurationError, "use PostgreSQL"):
            create_runtime_engine(
                Settings(
                    banking_reader_user="banking_reader",
                    banking_reader_database_url="sqlite:///banking.db",
                )
            )

    def test_runtime_url_cannot_use_a_different_identity(self) -> None:
        with self.assertRaisesRegex(
            DatabaseConfigurationError, "user must match BANKING_READER_USER"
        ):
            create_runtime_engine(
                Settings(
                    banking_reader_user="banking_reader",
                    banking_reader_database_url=(
                        "postgresql+psycopg://banking_owner:secret@localhost/banking_ai"
                    ),
                )
            )

    def test_connection_failure_is_controlled_and_does_not_expose_url(self) -> None:
        secret = "not-in-error-message"
        engine = create_runtime_engine(
            Settings(
                banking_reader_user="banking_reader",
                banking_reader_database_url=(
                    "postgresql+psycopg://banking_reader:"
                    f"{secret}@127.0.0.1:1/banking_ai?connect_timeout=1"
                ),
            )
        )
        with mock.patch("backend.app.db.engine.logger.exception"):
            with self.assertRaises(DatabaseConnectionError) as caught:
                check_database_connection(engine)
        engine.dispose()

        self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(
            str(caught.exception),
            "Unable to connect to the runtime PostgreSQL database",
        )


class RuntimeDatabaseIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_context = temporary_database()
        cls.owner_url = cls.database_context.__enter__()
        cls.environment = mock.patch.dict(os.environ, {"DATABASE_URL": cls.owner_url})
        cls.environment.start()
        command.upgrade(Config("alembic.ini"), "head")

        cls.reader_user = f"banking_reader_test_{uuid4().hex}"
        cls.reader_password = f"test-{uuid4().hex}"
        provision_reader_role(cls.owner_url, cls.reader_user, cls.reader_password)
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

    def test_connectivity_uses_effective_read_only_identity(self) -> None:
        info = check_database_connection(self.engine)

        self.assertEqual(info.user, self.reader_user)
        self.assertTrue(info.database.startswith("banking_test_"))

    def test_connection_is_returned_to_pool_after_context_exit(self) -> None:
        self.assertEqual(self.engine.pool.checkedout(), 0)
        with runtime_connection(self.engine) as connection:
            self.assertEqual(connection.scalar(text("SELECT 1")), 1)
            self.assertEqual(self.engine.pool.checkedout(), 1)
        self.assertEqual(self.engine.pool.checkedout(), 0)
