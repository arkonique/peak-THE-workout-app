from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from backend.schema import BODY_METRICS_SQL
from backend.sync_metrics import read_metric_definitions, sync_metrics_from_xlsx


ROOT_DIR = Path(__file__).resolve().parents[1]
METRICS_XLSX = ROOT_DIR / "metrics.xlsx"


class MetricWorkbookTests(unittest.TestCase):
    def test_workbook_contains_expected_unique_catalog(self) -> None:
        records = read_metric_definitions(METRICS_XLSX)
        self.assertEqual(len(records), 321)
        self.assertEqual(records[0], {
            "id": 1,
            "name": "Height",
            "dimension": "Length",
            "category": "Basic body size",
        })
        self.assertEqual(len({record["id"] for record in records}), 321)
        self.assertEqual(len({str(record["name"]).casefold() for record in records}), 321)

    @patch("backend.sync_metrics.upsert_rows")
    @patch("backend.sync_metrics.validate_supabase_config")
    def test_sync_upserts_in_batches(self, validate_config, upsert) -> None:
        count = sync_metrics_from_xlsx(METRICS_XLSX, batch_size=100)
        self.assertEqual(count, 321)
        validate_config.assert_called_once_with()
        self.assertEqual(upsert.call_count, 4)
        self.assertEqual([len(call.args[1]) for call in upsert.call_args_list], [100, 100, 100, 21])
        self.assertTrue(all(call.args[0] == "body_metrics" for call in upsert.call_args_list))
        self.assertTrue(all(call.kwargs["on_conflict"] == "id" for call in upsert.call_args_list))

    def test_schema_is_rls_protected(self) -> None:
        normalized = BODY_METRICS_SQL.lower()
        self.assertIn("create table if not exists public.body_metrics", normalized)
        self.assertIn("enable row level security", normalized)
        self.assertIn("to authenticated", normalized)
        self.assertIn("revoke all on table public.body_metrics from anon", normalized)


if __name__ == "__main__":
    unittest.main()
