from __future__ import annotations

import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from backend.full_schema import (
    EXPECTED_PUBLIC_TABLES,
    load_full_schema_sql,
)
from backend.schema import APP_SCHEMA_SQL
from server import FrontendHandler, ThreadingHTTPServer


class FullSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = load_full_schema_sql()
        cls.normalized = " ".join(cls.sql.lower().split())

    def test_schema_is_additive_and_excludes_open_food_database(self) -> None:
        self.assertNotIn("drop table", self.normalized)
        self.assertNotIn("openfooddb", self.normalized)
        self.assertIn("create table if not exists public.foods", self.normalized)
        self.assertIn("create or replace function public.search_food_names", self.normalized)

    def test_all_public_application_tables_are_declared(self) -> None:
        for table in EXPECTED_PUBLIC_TABLES:
            self.assertIn(f"create table if not exists public.{table}", self.normalized)

    def test_schema_has_rls_and_corrected_relationships(self) -> None:
        self.assertIn("enable row level security", self.normalized)
        self.assertIn("meal_item_id bigint not null", self.normalized)
        self.assertIn("source text not null", self.normalized)
        self.assertIn("food_code text not null references public.foods(code)", self.normalized)
        self.assertIn("references public.exercise_metrics(equipment)", self.normalized)
        self.assertIn("security_invoker = true", self.normalized)

    def test_api_schema_uses_the_migration_source_of_truth(self) -> None:
        self.assertEqual(APP_SCHEMA_SQL, self.sql)

class GenericApiProtectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FrontendHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_user_owned_tables_are_blocked_from_generic_service_role_api(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.origin}/api/user_days", timeout=2)
        self.assertEqual(caught.exception.code, 403)
        self.assertIn("not available through the generic API", caught.exception.read().decode())

    def test_complete_schema_is_available_for_manual_sql_editor_use(self) -> None:
        with urlopen(f"{self.origin}/api/supabase/schema", timeout=2) as response:
            schema = response.read().decode()
        self.assertIn("create table if not exists public.user_days", schema.lower())
        self.assertIn("create table if not exists public.foods", schema.lower())
        self.assertNotIn("openfooddb", schema.lower())


if __name__ == "__main__":
    unittest.main()
