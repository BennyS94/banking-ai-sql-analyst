from __future__ import annotations

from unittest import TestCase

from sqlglot import exp

from backend.app.safety.sql_validator import (
    SQLASTValidator,
    SQLSafetyReasonCode,
)


class SQLASTValidatorTests(TestCase):
    def setUp(self) -> None:
        self.validator = SQLASTValidator()

    def test_accepts_supported_analytical_structures(self) -> None:
        cases = {
            "basic_select": "SELECT customer_id FROM banking.customers",
            "where": (
                "SELECT account_id FROM banking.accounts WHERE balance < 0 "
                "ORDER BY account_id LIMIT 5 OFFSET 1"
            ),
            "join": (
                "SELECT c.customer_id, a.account_id FROM banking.customers c "
                "JOIN banking.accounts a ON a.customer_id = c.customer_id"
            ),
            "aggregation": "SELECT COUNT(*) FROM banking.transactions",
            "group_having": (
                "SELECT branch_id, SUM(amount) FROM banking.transactions "
                "GROUP BY branch_id HAVING SUM(amount) > 10"
            ),
            "cte": (
                "WITH totals AS (SELECT customer_id, SUM(balance) AS total "
                "FROM banking.accounts GROUP BY customer_id) "
                "SELECT * FROM totals"
            ),
            "nested_subquery": (
                "SELECT customer_id FROM banking.customers WHERE customer_id IN "
                "(SELECT customer_id FROM banking.accounts)"
            ),
            "window": (
                "SELECT account_id, ROW_NUMBER() OVER (ORDER BY balance DESC) AS rank "
                "FROM banking.accounts"
            ),
            "union": "SELECT customer_id FROM banking.customers UNION SELECT customer_id FROM banking.accounts",
            "intersect": "SELECT customer_id FROM banking.customers INTERSECT SELECT customer_id FROM banking.accounts",
            "except": "SELECT customer_id FROM banking.customers EXCEPT SELECT customer_id FROM banking.accounts",
            "comments": (
                "/* analytical query */\nSELECT customer_id -- projected identifier\n"
                "FROM banking.customers;"
            ),
        }

        for case_id, sql in cases.items():
            with self.subTest(case_id=case_id):
                result = self.validator.validate(sql)
                self.assertTrue(result.accepted, result)
                self.assertIsNone(result.reason_code)
                self.assertIsInstance(result.expression, exp.Expression)

    def test_rejects_mutation_statements(self) -> None:
        cases = (
            "INSERT INTO banking.accounts (account_id) VALUES (1)",
            "UPDATE banking.accounts SET balance = 0",
            "DELETE FROM banking.accounts",
            (
                "MERGE INTO banking.accounts a USING banking.accounts b "
                "ON a.account_id = b.account_id "
                "WHEN MATCHED THEN UPDATE SET balance = 0"
            ),
        )

        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, SQLSafetyReasonCode.MUTATION_STATEMENT)

    def test_rejects_ddl_statements(self) -> None:
        cases = (
            "DROP TABLE banking.accounts",
            "ALTER TABLE banking.accounts ADD COLUMN unsafe integer",
            "CREATE TABLE banking.unsafe (id integer)",
            "TRUNCATE TABLE banking.accounts",
        )

        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, SQLSafetyReasonCode.DDL_STATEMENT)

    def test_rejects_administrative_statements(self) -> None:
        cases = (
            "COPY banking.accounts TO STDOUT",
            "CALL unsafe_procedure()",
            "DO $$ BEGIN NULL; END $$",
            "SET search_path TO banking",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "GRANT SELECT ON banking.accounts TO public",
            "REVOKE SELECT ON banking.accounts FROM public",
            "VACUUM banking.accounts",
            "ANALYZE banking.accounts",
            "SELECT * FROM banking.accounts FOR UPDATE",
            "SELECT * FROM banking.accounts FOR SHARE",
        )

        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(
                    sql, SQLSafetyReasonCode.ADMINISTRATIVE_STATEMENT
                )

    def test_rejects_select_into(self) -> None:
        self.assert_rejected(
            "SELECT * INTO banking.accounts_copy FROM banking.accounts",
            SQLSafetyReasonCode.SELECT_INTO,
        )

    def test_rejects_data_modifying_cte(self) -> None:
        self.assert_rejected(
            "WITH deleted AS (DELETE FROM banking.accounts RETURNING account_id) "
            "SELECT * FROM deleted",
            SQLSafetyReasonCode.DATA_MODIFYING_CTE,
        )

    def test_rejects_multiple_executable_statements(self) -> None:
        cases = (
            "SELECT 1; SELECT 2",
            "SELECT 1; DROP TABLE banking.accounts",
        )
        for sql in cases:
            with self.subTest(sql=sql):
                self.assert_rejected(sql, SQLSafetyReasonCode.MULTIPLE_STATEMENTS)

    def test_ignores_empty_statement_separators_when_counting_executable_sql(self) -> None:
        result = self.validator.validate("; SELECT 1;;")

        self.assertTrue(result.accepted, result)

    def test_rejects_empty_and_malformed_sql(self) -> None:
        for sql in ("", "-- comment only", "SELECT FROM"):
            with self.subTest(sql=sql):
                self.assert_rejected(sql, SQLSafetyReasonCode.PARSE_ERROR)

    def test_rejects_unsupported_read_looking_root_statement(self) -> None:
        self.assert_rejected(
            "EXPLAIN SELECT 1",
            SQLSafetyReasonCode.ADMINISTRATIVE_STATEMENT,
        )
        self.assert_rejected(
            "VALUES (1)",
            SQLSafetyReasonCode.UNSUPPORTED_STATEMENT,
        )

    def assert_rejected(self, sql: str, reason: SQLSafetyReasonCode) -> None:
        result = self.validator.validate(sql)
        self.assertFalse(result.accepted, result)
        self.assertEqual(result.reason_code, reason)
        self.assertIsNone(result.expression)
