"""Extract product names/codes from Parquet and sync the lightweight Supabase index."""

from __future__ import annotations

import argparse
import unicodedata
from collections.abc import Callable
from pathlib import Path

try:
    from .db import select_rows, upsert_rows, validate_name, validate_supabase_config
except ImportError:
    from db import select_rows, upsert_rows, validate_name, validate_supabase_config


ProgressCallback = Callable[[str, int, int | None, str], None]


def sanitize_food_text(value: object, max_length: int) -> str:
    """Normalize text and remove characters PostgreSQL cannot store or search."""

    normalized = unicodedata.normalize("NFKC", str(value))
    cleaned = "".join(
        " " if unicodedata.category(character).startswith("C") else character for character in normalized
    )
    return " ".join(cleaned.split())[:max_length].strip()


def sync_food_names_from_parquet(
    parquet_path: str | Path,
    column: str = "product_name",
    batch_size: int = 1000,
    progress_callback: ProgressCallback | None = None,
) -> int:
    """Read code and English product name only, then upsert public.foods."""

    path = Path(parquet_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Parquet file not found: {path}")
    if batch_size < 1 or batch_size > 2000:
        raise ValueError("batch_size must be between 1 and 2000.")
    column_name = validate_name(column)
    validate_supabase_config()

    try:
        select_rows("foods", {}, "code,name", 1, None)
    except Exception as exc:
        if getattr(exc, "code", None) in {"PGRST204", "PGRST205"}:
            raise RuntimeError(
                "Supabase public.foods is missing the required code/name schema. "
                "Apply the SQL from /api/supabase/schema, then retry."
            ) from None
        raise

    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB is not installed. Run: pip install -r requirements.txt") from exc

    if progress_callback:
        progress_callback("reading", 0, None, f"Reading code and {column_name} from {path.name}.")

    quoted_column = f'"{column_name}"'
    query = f"""
        select code, english_name
        from (
          select
            cast(code as varchar) as code,
            list_extract(
              list_filter({quoted_column}, item -> lower(item.lang) = 'en'),
              1
            ).text as english_name
          from read_parquet(?)
        )
        where code is not null
          and english_name is not null
    """
    imported = 0
    connection = duckdb.connect()
    try:
        cursor = connection.execute(query, [str(path)])
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            records_by_code = {}
            for code_value, name_value in rows:
                code = sanitize_food_text(code_value, 64)
                name = sanitize_food_text(name_value, 500)
                if code and name:
                    records_by_code[code] = {"code": code, "name": name}
            records = list(records_by_code.values())
            if not records:
                continue
            upsert_rows(
                "foods",
                records,
                on_conflict="code",
                returning="minimal",
                ignore_duplicates=False,
            )
            imported += len(records)
            if progress_callback:
                progress_callback(
                    "syncing",
                    imported,
                    None,
                    f"Uploaded {imported:,} English food names and codes to Supabase.",
                )
    finally:
        connection.close()

    if progress_callback:
        progress_callback("complete", imported, imported, f"Imported {imported:,} English names and codes.")
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the lightweight public.foods index from Parquet.")
    parser.add_argument("parquet_path")
    parser.add_argument("--column", default="product_name")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    def report(_phase: str, _current: int, _total: int | None, message: str) -> None:
        print(message)

    count = sync_food_names_from_parquet(args.parquet_path, args.column, args.batch_size, report)
    print(f"Synced {count:,} food names.")


if __name__ == "__main__":
    main()
