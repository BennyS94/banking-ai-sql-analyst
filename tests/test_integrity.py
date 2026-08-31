from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest import TestCase, mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from banking_data.cleaning import clean_dataset
from banking_data.integrity import validate_data_foundation
from banking_data.loading import load_processed_data
from banking_data.role_management import (
    _psycopg_connection_string,
    provision_reader_role,
)
from database_test_support import temporary_database


class PhaseOneEndToEndIntegrityTests(TestCase):
    def test_fresh_clean_migrate_load_role_and_integrity_flow(self) -> None:
        raw_dir = Path("data/raw").resolve()
        with tempfile.TemporaryDirectory() as directory, temporary_database() as owner_url:
            processed_dir = Path(directory) / "processed"
            cleaning_summary = clean_dataset(raw_dir, processed_dir)

            with mock.patch.dict(os.environ, {"DATABASE_URL": owner_url}):
                command.upgrade(Config("alembic.ini"), "head")

            owner_engine = create_engine(owner_url)
            reader_user = f"banking_reader_e2e_{uuid4().hex}"
            reader_password = f"test-{uuid4().hex}"
            reader_engine = None
            role_provisioned = False
            try:
                load_summary = load_processed_data(owner_engine, processed_dir)
                provision_reader_role(owner_url, reader_user, reader_password)
                role_provisioned = True
                reader_url = make_url(owner_url).set(
                    username=reader_user,
                    password=reader_password,
                )
                reader_engine = create_engine(reader_url)
                result = validate_data_foundation(
                    owner_engine, reader_engine, raw_dir, processed_dir
                )

                self.assertEqual(cleaning_summary["totals"]["accepted_rows"], 52_869)
                self.assertEqual(load_summary["rows_inserted"], 52_869)
                self.assertEqual(result["status"], "passed")
                self.assertEqual(result["database"]["foreign_key_orphans"], 0)
                self.assertTrue(result["reader"]["mutation_denied"])
            finally:
                if reader_engine is not None:
                    reader_engine.dispose()
                if role_provisioned:
                    with psycopg.connect(
                        _psycopg_connection_string(owner_url), autocommit=True
                    ) as connection:
                        role = sql.Identifier(reader_user)
                        connection.execute(sql.SQL("DROP OWNED BY {}").format(role))
                        connection.execute(sql.SQL("DROP ROLE {}").format(role))
                owner_engine.dispose()
