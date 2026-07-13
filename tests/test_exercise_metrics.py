from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.schema import EXERCISE_METRICS_SQL
from backend.sync_exercise_metrics import CAPABILITY_COLUMNS, EXERCISE_METRIC_ROWS, sync_exercise_metrics


class ExerciseMetricTests(unittest.TestCase):
    def test_matrix_has_unique_equipment_and_boolean_capabilities(self) -> None:
        self.assertEqual(len(EXERCISE_METRIC_ROWS), 14)
        self.assertEqual(len({row["equipment"] for row in EXERCISE_METRIC_ROWS}), 14)
        for row in EXERCISE_METRIC_ROWS:
            self.assertEqual(set(row), {"equipment", *CAPABILITY_COLUMNS})
            self.assertTrue(all(isinstance(row[column], bool) for column in CAPABILITY_COLUMNS))

    @patch("backend.sync_exercise_metrics.upsert_rows")
    @patch("backend.sync_exercise_metrics.validate_supabase_config")
    def test_sync_upserts_approved_rows(self, validate_config, upsert) -> None:
        self.assertEqual(sync_exercise_metrics(), 14)
        validate_config.assert_called_once_with()
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[0], "exercise_metrics")
        self.assertEqual(len(upsert.call_args.args[1]), 14)
        self.assertEqual(upsert.call_args.kwargs["on_conflict"], "equipment")

    def test_schema_uses_boolean_columns_and_rls(self) -> None:
        normalized = EXERCISE_METRICS_SQL.lower()
        self.assertEqual(normalized.count(" boolean not null"), 12)
        self.assertIn("equipment text primary key", normalized)
        self.assertIn("enable row level security", normalized)
        self.assertIn("revoke all on table public.exercise_metrics from anon", normalized)


if __name__ == "__main__":
    unittest.main()
