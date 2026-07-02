"""Supabase helpers for the PlainExercise exercise table."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client


DEFAULT_DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_KEY_ENV = "SUPABASE_KEY"
SUPABASE_SECRET_KEY_ENV = "SUPABASE_SECRET_KEY"
SUPABASE_API_KEY_DEFAULT_ENV = "SUPABASE_API_KEY_DEFAULT"
SUPABASE_SERVICE_KEY_ENV = "SUPABASE_SERVICE_ROLE_KEY"
SUPABASE_SECRET_ROLE_KEY_ENV = "SUPABASE_SECRET_ROLE_KEY"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

load_dotenv(DEFAULT_DOTENV_PATH)


def get_supabase_client() -> Client:
    supabase_url = os.environ.get(SUPABASE_URL_ENV, "").strip()
    supabase_key = ""
    for key_env in (
        SUPABASE_SECRET_KEY_ENV,
        SUPABASE_API_KEY_DEFAULT_ENV,
        SUPABASE_SERVICE_KEY_ENV,
        SUPABASE_SECRET_ROLE_KEY_ENV,
        SUPABASE_KEY_ENV,
    ):
        supabase_key = os.environ.get(key_env, "").strip()
        if supabase_key:
            break

    if not supabase_url or not supabase_key:
        raise RuntimeError(f"Set {SUPABASE_URL_ENV} and {SUPABASE_SECRET_KEY_ENV} in .env.")

    return create_client(supabase_url, supabase_key)


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid table or column name: {name!r}")
    return name


def _apply_filters(query, filters: dict[str, object]):
    for column, value in filters.items():
        query = query.eq(validate_name(column), value)
    return query


def select_rows(table: str, filters: dict[str, object], select: str, limit: int | None, order: str | None):
    query = get_supabase_client().table(validate_name(table)).select(select)
    query = _apply_filters(query, filters)
    if order:
        desc = order.startswith("-")
        query = query.order(validate_name(order.lstrip("-")), desc=desc)
    if limit is not None:
        query = query.limit(limit)
    return query.execute().data


def insert_rows(table: str, rows: dict[str, object] | list[dict[str, object]]):
    return get_supabase_client().table(validate_name(table)).insert(rows).execute().data


def upsert_rows(table: str, rows: list[dict[str, object]], on_conflict: str | None = None):
    kwargs = {"on_conflict": validate_name(on_conflict)} if on_conflict else {}
    return get_supabase_client().table(validate_name(table)).upsert(rows, **kwargs).execute().data


def update_rows(table: str, filters: dict[str, object], values: dict[str, object]):
    if not filters:
        raise ValueError("PATCH requires an id path segment or query filter.")
    query = get_supabase_client().table(validate_name(table)).update(values)
    return _apply_filters(query, filters).execute().data


def delete_rows(table: str, filters: dict[str, object]):
    if not filters:
        raise ValueError("DELETE requires an id path segment or query filter.")
    query = get_supabase_client().table(validate_name(table)).delete()
    return _apply_filters(query, filters).execute().data


def upsert_exercises(exercises: Sequence[dict[str, str]]) -> int:
    records = []
    for exercise in exercises:
        profile_data = {key: value for key, value in exercise.items() if key not in {"name", "url"}}
        records.append(
            {
                "name": exercise["name"],
                "url": exercise["url"],
                "profile_data": profile_data,
                "scraped_at": datetime.now(UTC).isoformat(),
            }
        )

    for start in range(0, len(records), 100):
        upsert_rows("exercises", records[start : start + 100], on_conflict="url")

    return len(records)
