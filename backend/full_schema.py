"""Load the additive Supabase schema shown by the schema endpoint."""

from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
FULL_SCHEMA_PATH = ROOT_DIR / "supabase" / "migrations" / "202607060001_full_application_schema.sql"
AI_SCHEMA_PATH = ROOT_DIR / "supabase" / "migrations" / "202607060002_ai_chat_sessions.sql"
EXPECTED_PUBLIC_TABLES = (
    "users",
    "exercise_metrics",
    "exercises",
    "exercise_muscle_groups",
    "foods",
    "body_metrics",
    "muscle_groups",
    "nutrients",
    "user_days",
    "day_muscle_targets",
    "meals",
    "meal_items",
    "meal_nutrients",
    "workout_sessions",
    "workout_exercises",
    "exercise_sets",
    "body_measurements",
    "friendships",
    "leagues",
    "league_members",
    "league_metrics",
    "league_metric_results",
    "league_results",
    "ai_sessions",
)


def load_full_schema_sql() -> str:
    return "\n\n".join(
        path.read_text(encoding="utf-8").strip()
        for path in (FULL_SCHEMA_PATH, AI_SCHEMA_PATH)
    )


def load_ai_schema_sql() -> str:
    return AI_SCHEMA_PATH.read_text(encoding="utf-8").strip()
