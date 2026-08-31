from __future__ import annotations

import os
from unittest import IsolatedAsyncioTestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from backend.app.core.config import Settings
from backend.app.db.engine import create_runtime_engine, get_runtime_engine
from backend.app.db.schema import SchemaIntrospectionError
from backend.app.main import create_app
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database


class BankingSchemaApiTests(IsolatedAsyncioTestCase):
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

    async def asyncSetUp(self) -> None:
        self.application = create_app(Settings())
        self.application.dependency_overrides[get_runtime_engine] = lambda: self.engine
        self.transport = ASGITransport(app=self.application)

    async def test_schema_endpoint_returns_typed_deterministic_metadata(self) -> None:
        async with AsyncClient(
            transport=self.transport, base_url="http://test"
        ) as client:
            first_response = await client.get("/api/v1/database/schema")
            second_response = await client.get("/api/v1/database/schema")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json(), second_response.json())
        payload = first_response.json()
        self.assertEqual(set(payload), {"schema", "tables"})
        self.assertEqual(payload["schema"], "banking")

        table_names = [table["name"] for table in payload["tables"]]
        self.assertEqual(table_names, sorted(table_names))
        self.assertIn("customers", table_names)
        self.assertIn("transactions", table_names)
        self.assertNotIn("alembic_version", table_names)

        accounts = next(
            table for table in payload["tables"] if table["name"] == "accounts"
        )
        self.assertEqual(
            set(accounts), {"name", "columns", "primary_key", "foreign_keys"}
        )
        self.assertEqual(accounts["primary_key"], ["account_id"])
        customer_fk = next(
            foreign_key
            for foreign_key in accounts["foreign_keys"]
            if foreign_key["columns"] == ["customer_id"]
        )
        self.assertEqual(customer_fk["referenced_schema"], "banking")
        self.assertEqual(customer_fk["referenced_table"], "customers")
        self.assertEqual(customer_fk["referenced_columns"], ["customer_id"])

        balance = next(
            column for column in accounts["columns"] if column["name"] == "balance"
        )
        self.assertEqual(
            balance,
            {"name": "balance", "type": "numeric(14, 2)", "nullable": False},
        )

    async def test_schema_endpoint_handles_introspection_failure(self) -> None:
        with mock.patch(
            "backend.app.api.routes.database_schema.introspect_banking_schema",
            side_effect=SchemaIntrospectionError("contains-sensitive-internals"),
        ):
            async with AsyncClient(
                transport=self.transport, base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/database/schema")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Banking schema metadata is temporarily unavailable"},
        )
        self.assertNotIn("contains-sensitive-internals", response.text)
