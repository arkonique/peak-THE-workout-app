"""Synchronize equipment-specific tracking capabilities to Supabase."""

from __future__ import annotations

from datetime import UTC, datetime

from .db import upsert_rows, validate_supabase_config


CAPABILITY_COLUMNS = (
    "reps",
    "concentric_time",
    "eccentric_time",
    "isometric_time",
    "time",
    "set_rest_time",
    "exercise_rest_time",
    "sets",
    "weights",
    "band_color",
    "volume",
    "one_rep_max",
)


def _row(equipment: str, flags: str) -> dict[str, object]:
    values = flags.split()
    if len(values) != len(CAPABILITY_COLUMNS) or any(value not in {"y", "n"} for value in values):
        raise ValueError(f"Invalid capability flags for {equipment}.")
    return {
        "equipment": equipment,
        **{column: value == "y" for column, value in zip(CAPABILITY_COLUMNS, values, strict=True)},
    }


EXERCISE_METRIC_ROWS = (
    _row("bands", "y y y y y y y y n y n n"),
    _row("barbell", "y y y y n y y y y n y y"),
    _row("body only", "y y y y y y y y n n n n"),
    _row("cable", "y y y y n y y y y n y y"),
    _row("dumbbell", "y y y y n y y y y n y y"),
    _row("exercise ball", "y n n n y y y y y n y y"),
    _row("e-z curl bar", "y y y y n y y y y y y y"),
    _row("foam roll", "y n n n y y y y n n n n"),
    _row("kettlebells", "y y y y n y y y y n y y"),
    _row("machine", "y y y y n y y y y n y y"),
    _row("medicine ball", "y y y y n y y y y n y y"),
    _row("none", "y n n n y y y y n n n n"),
    _row("none (bodyweight exercise)", "y n n n y y y y n n n n"),
    _row("other", "y y y y y y y y y y y y"),
)


def sync_exercise_metrics() -> int:
    validate_supabase_config()
    updated_at = datetime.now(UTC).isoformat()
    rows = [{**row, "updated_at": updated_at} for row in EXERCISE_METRIC_ROWS]
    try:
        upsert_rows("exercise_metrics", rows, on_conflict="equipment")
    except Exception as exc:
        if getattr(exc, "code", None) in {"42P01", "PGRST205"}:
            raise RuntimeError(
                "Supabase table public.exercise_metrics does not exist. Run the SQL from "
                "GET /api/exercise-metrics/schema in the Supabase SQL Editor, then retry."
            ) from exc
        raise
    return len(rows)
