"""Fast, table-agnostic fuzzy search over cached Supabase rows."""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher

try:
    from .db import select_all_rows, validate_name
except ImportError:
    from db import select_all_rows, validate_name


CACHE_TTL_SECONDS = 30
MAX_CACHED_TABLES = 8
MAX_NAME_MATCHES = 5
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class CacheEntry:
    expires_at: float
    rows: list[dict[str, object]]


_CACHE: OrderedDict[str, CacheEntry] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def normalize_search_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return " ".join(_NON_ALPHANUMERIC.sub(" ", text.lower()).split())


def _fuzzy_score(query: str, candidate: str) -> float | None:
    if not query or not candidate:
        return None

    query_tokens = query.split()
    candidate_tokens = candidate.split()
    whole_ratio = SequenceMatcher(None, query, candidate).ratio()
    token_ratios = [
        max(SequenceMatcher(None, query_token, candidate_token).ratio() for candidate_token in candidate_tokens)
        for query_token in query_tokens
    ]
    average_token_ratio = sum(token_ratios) / len(token_ratios)
    best_token_ratio = max(token_ratios)
    exact = query == candidate
    prefix = candidate.startswith(query)
    substring = query in candidate
    token_prefix = all(
        any(candidate_token.startswith(query_token) for candidate_token in candidate_tokens)
        for query_token in query_tokens
    )
    acronym = "".join(token[0] for token in candidate_tokens if token).startswith(query.replace(" ", ""))

    if len(query) == 1 and not substring:
        return None
    if len(query) == 2 and not (substring or token_prefix or best_token_ratio >= 0.67):
        return None
    if len(query) >= 3 and not (
        substring or token_prefix or acronym or whole_ratio >= 0.42 or average_token_ratio >= 0.58
    ):
        return None

    score = whole_ratio * 100 + average_token_ratio * 80
    if exact:
        score += 500
    elif prefix:
        score += 300
    elif substring:
        score += 220
    if token_prefix:
        score += 160
    if acronym:
        score += 80
    score -= abs(len(candidate) - len(query)) * 0.15
    return score


def clear_search_cache(table: str | None = None) -> None:
    with _CACHE_LOCK:
        if table is None:
            _CACHE.clear()
        else:
            _CACHE.pop(validate_name(table), None)


def _get_table_rows(table: str) -> list[dict[str, object]]:
    table_name = validate_name(table)
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(table_name)
        if entry and entry.expires_at > now:
            _CACHE.move_to_end(table_name)
            return entry.rows
        if entry:
            _CACHE.pop(table_name, None)

    rows = select_all_rows(table_name)
    with _CACHE_LOCK:
        _CACHE[table_name] = CacheEntry(now + CACHE_TTL_SECONDS, rows)
        _CACHE.move_to_end(table_name)
        while len(_CACHE) > MAX_CACHED_TABLES:
            _CACHE.popitem(last=False)
    return rows


def fuzzy_search_table(
    table: str,
    query: str,
    field: str = "name",
    limit: int = MAX_NAME_MATCHES,
    include_rows: bool = False,
) -> list[object]:
    table_name = validate_name(table)
    field_name = validate_name(field)
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return []

    maximum = 1 if include_rows else MAX_NAME_MATCHES
    result_limit = min(max(int(limit), 1), maximum)
    ranked = []
    for position, row in enumerate(_get_table_rows(table_name)):
        value = row.get(field_name)
        if value is None:
            continue
        display_value = str(value)
        score = _fuzzy_score(normalized_query, normalize_search_text(display_value))
        if score is not None:
            ranked.append((score, len(display_value), display_value.casefold(), position, row))

    ranked.sort(key=lambda match: (-match[0], match[1], match[2], match[3]))
    matches = ranked[:result_limit]
    if include_rows:
        return [match[4] for match in matches]
    return [str(match[4][field_name]) for match in matches]
