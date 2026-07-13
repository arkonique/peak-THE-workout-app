"""Supabase helpers for the PlainExercise exercise table."""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

try:
    from .schema import INTEGER_PROFILE_FIELDS, PROFILE_FIELDS
except ImportError:
    from schema import INTEGER_PROFILE_FIELDS, PROFILE_FIELDS


DEFAULT_DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_KEY_ENV = "SUPABASE_KEY"
SUPABASE_SECRET_KEY_ENV = "SUPABASE_SECRET_KEY"
SUPABASE_API_KEY_DEFAULT_ENV = "SUPABASE_API_KEY_DEFAULT"
SUPABASE_SERVICE_KEY_ENV = "SUPABASE_SERVICE_ROLE_KEY"
SUPABASE_SECRET_ROLE_KEY_ENV = "SUPABASE_SECRET_ROLE_KEY"
SUPABASE_PUBLISHABLE_KEY_ENV = "SUPABASE_PUBLISHABLE_KEY"
SUPABASE_ANON_KEY_ENV = "SUPABASE_ANON_KEY"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

load_dotenv(DEFAULT_DOTENV_PATH)


def _key_access_level(key: str) -> str:
    if key.startswith("sb_secret_"):
        return "server"
    if key.startswith("sb_publishable_"):
        return "public"
    if key.count(".") == 2:
        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            role = json.loads(base64.urlsafe_b64decode(payload)).get("role")
        except (ValueError, json.JSONDecodeError):
            return "unknown"
        if role == "service_role":
            return "server"
        if role in {"anon", "authenticated"}:
            return "public"
    return "unknown"


def get_supabase_credentials() -> tuple[str, str]:
    supabase_url = os.environ.get(SUPABASE_URL_ENV, "").strip()
    if not supabase_url:
        raise RuntimeError(f"Set {SUPABASE_URL_ENV} in .env.")

    configured_keys = []
    for key_env in (
        SUPABASE_SECRET_KEY_ENV,
        SUPABASE_SERVICE_KEY_ENV,
        SUPABASE_SECRET_ROLE_KEY_ENV,
        SUPABASE_API_KEY_DEFAULT_ENV,
        SUPABASE_KEY_ENV,
    ):
        key = os.environ.get(key_env, "").strip()
        if not key:
            continue
        access_level = _key_access_level(key)
        configured_keys.append((key_env, access_level))
        if access_level == "server":
            return supabase_url, key

    if configured_keys:
        public_names = ", ".join(name for name, level in configured_keys if level == "public")
        if public_names:
            raise RuntimeError(
                f"{public_names} contains a publishable/anon Supabase key, which cannot bypass RLS. "
                f"Set {SUPABASE_SECRET_KEY_ENV} to an sb_secret_ key or set "
                f"{SUPABASE_SERVICE_KEY_ENV} to the legacy service_role key."
            )
        raise RuntimeError(
            "No recognized server-side Supabase key is configured. "
            f"Set {SUPABASE_SECRET_KEY_ENV} to an sb_secret_ key or set "
            f"{SUPABASE_SERVICE_KEY_ENV} to the legacy service_role key."
        )

    raise RuntimeError(
        f"Set {SUPABASE_SECRET_KEY_ENV} to an sb_secret_ key or set "
        f"{SUPABASE_SERVICE_KEY_ENV} to the legacy service_role key."
    )


def validate_supabase_config() -> None:
    get_supabase_credentials()


def get_supabase_public_credentials() -> tuple[str, str]:
    supabase_url = os.environ.get(SUPABASE_URL_ENV, "").strip()
    if not supabase_url:
        raise RuntimeError(f"Set {SUPABASE_URL_ENV} in .env.")

    for key_env in (
        SUPABASE_PUBLISHABLE_KEY_ENV,
        SUPABASE_ANON_KEY_ENV,
        SUPABASE_API_KEY_DEFAULT_ENV,
        SUPABASE_KEY_ENV,
    ):
        key = os.environ.get(key_env, "").strip()
        if key and _key_access_level(key) == "public":
            return supabase_url, key

    raise RuntimeError(
        f"Set {SUPABASE_PUBLISHABLE_KEY_ENV} to the project's sb_publishable_ key "
        f"(or set {SUPABASE_ANON_KEY_ENV} to the legacy anon key)."
    )


def get_supabase_client() -> Client:
    supabase_url, supabase_key = get_supabase_credentials()
    return create_client(supabase_url, supabase_key)


def get_supabase_public_client() -> Client:
    supabase_url, supabase_key = get_supabase_public_credentials()
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


def select_all_rows(table: str, select: str = "*", page_size: int = 1000) -> list[dict[str, object]]:
    if page_size < 1:
        raise ValueError("page_size must be at least 1.")

    client = get_supabase_client()
    table_name = validate_name(table)
    rows: list[dict[str, object]] = []
    start = 0
    while True:
        page = (
            client.table(table_name)
            .select(select)
            .order("id")
            .range(start, start + page_size - 1)
            .execute()
            .data
        )
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


def insert_rows(table: str, rows: dict[str, object] | list[dict[str, object]]):
    return get_supabase_client().table(validate_name(table)).insert(rows).execute().data


def upsert_rows(
    table: str,
    rows: list[dict[str, object]],
    on_conflict: str | None = None,
    *,
    returning: str = "representation",
    ignore_duplicates: bool = False,
):
    kwargs = {"on_conflict": validate_name(on_conflict)} if on_conflict else {}
    kwargs.update(returning=returning, ignore_duplicates=ignore_duplicates)
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
    scraped_at = datetime.now(UTC).isoformat()
    for exercise in exercises:
        record: dict[str, object] = {
            "name": exercise["name"],
            "url": exercise["url"],
            "scraped_at": scraped_at,
        }
        for field in PROFILE_FIELDS:
            value = exercise.get(field)
            if field in INTEGER_PROFILE_FIELDS:
                record[field] = int(value) if value not in {None, "", "—"} else None
            else:
                record[field] = value
        records.append(record)

    for start in range(0, len(records), 100):
        upsert_rows("exercises", records[start : start + 100], on_conflict="url")

    return len(records)
