"""Rate-limited, cached Open Food Facts text search."""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass

try:
    from .db import get_supabase_client
except ImportError:
    from db import get_supabase_client

MIN_UPSTREAM_INTERVAL_SECONDS = 6.2
MIN_PRODUCT_INTERVAL_SECONDS = 4.2
CACHE_TTL_SECONDS = 600
DEFAULT_USER_AGENT = "PeakWorkoutApp/1.0 (Open Food Facts search test)"


class FoodSearchError(RuntimeError):
    """Base error for Open Food Facts search failures."""


class FoodSearchRateLimited(FoodSearchError):
    def __init__(self, retry_after: int, message: str | None = None) -> None:
        self.retry_after = max(int(retry_after), 1)
        super().__init__(message or f"Please wait {self.retry_after} seconds before searching again.")


class FoodSearchUnavailable(FoodSearchError):
    """Raised when Open Food Facts cannot serve the search."""


class FoodProductNotFound(FoodSearchError):
    """Raised when Open Food Facts has no product for an exact code."""


@dataclass(frozen=True)
class CachedSearch:
    expires_at: float
    payload: dict[str, object]


_LOCK = threading.Lock()
_CACHE: dict[str, CachedSearch] = {}
_PRODUCT_CACHE: dict[str, CachedSearch] = {}
_LAST_UPSTREAM_REQUEST = 0.0
_LAST_PRODUCT_REQUEST = 0.0
_API = None
_PRODUCT_API = None


def _normalize_query(query: str) -> str:
    return " ".join(query.casefold().split())


def _get_api():
    global _API
    if _API is None:
        try:
            import openfoodfacts
        except ImportError as exc:
            raise FoodSearchUnavailable(
                "The openfoodfacts package is not installed. Run: pip install -r requirements.txt"
            ) from exc
        user_agent = os.environ.get("OPENFOODFACTS_USER_AGENT", DEFAULT_USER_AGENT).strip()
        if not user_agent:
            raise FoodSearchUnavailable("OPENFOODFACTS_USER_AGENT must not be empty.")
        _API = openfoodfacts.API(user_agent=user_agent, timeout=15)
    return _API


def _get_product_api():
    """Return the v3 client used for exact product reads."""

    global _PRODUCT_API
    if _PRODUCT_API is None:
        try:
            import openfoodfacts
        except ImportError as exc:
            raise FoodSearchUnavailable(
                "The openfoodfacts package is not installed. Run: pip install -r requirements.txt"
            ) from exc
        user_agent = os.environ.get("OPENFOODFACTS_USER_AGENT", DEFAULT_USER_AGENT).strip()
        if not user_agent:
            raise FoodSearchUnavailable("OPENFOODFACTS_USER_AGENT must not be empty.")
        version = os.environ.get("OPENFOODFACTS_PRODUCT_API_VERSION", "v3").strip() or "v3"
        _PRODUCT_API = openfoodfacts.API(user_agent=user_agent, version=version, timeout=15)
    return _PRODUCT_API


def _retry_after_from_exception(exc: Exception) -> int:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("Retry-After")
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 60


def search_food(query: str) -> dict[str, object]:
    """Return the first Open Food Facts text-search result.

    Identical successful queries are cached for ten minutes. Uncached upstream
    calls are serialized and spaced by at least 6.2 seconds to stay below the
    documented limit of ten search requests per minute per IP address.
    """

    global _LAST_UPSTREAM_REQUEST
    cleaned_query = " ".join(query.split())
    normalized_query = _normalize_query(cleaned_query)
    if len(normalized_query) < 2:
        raise ValueError("Enter at least two characters to search for a food.")
    if len(normalized_query) > 120:
        raise ValueError("Food search queries must be 120 characters or fewer.")

    with _LOCK:
        now = time.monotonic()
        cached = _CACHE.get(normalized_query)
        if cached and cached.expires_at > now:
            return {**cached.payload, "cached": True}
        if cached:
            _CACHE.pop(normalized_query, None)

        wait_seconds = MIN_UPSTREAM_INTERVAL_SECONDS - (now - _LAST_UPSTREAM_REQUEST)
        if wait_seconds > 0:
            raise FoodSearchRateLimited(math.ceil(wait_seconds))

        _LAST_UPSTREAM_REQUEST = now
        try:
            response = _get_api().product.text_search(cleaned_query, page=1, page_size=1)
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 429:
                raise FoodSearchRateLimited(
                    _retry_after_from_exception(exc),
                    "Open Food Facts is rate limiting searches. Please wait about a minute and try again.",
                ) from exc
            raise FoodSearchUnavailable(f"Open Food Facts search failed: {exc}") from exc

        if not isinstance(response, dict):
            raise FoodSearchUnavailable("Open Food Facts returned an unexpected response.")
        products = response.get("products")
        product = products[0] if isinstance(products, list) and products else None
        payload: dict[str, object] = {
            "query": cleaned_query,
            "count": response.get("count", 0),
            "product": product,
            "cached": False,
        }
        _CACHE[normalized_query] = CachedSearch(time.monotonic() + CACHE_TTL_SECONDS, payload)
        return payload


def search_food_names(query: str, limit: int = 5) -> list[dict[str, object]]:
    """Return up to five fuzzy name matches from the lightweight Supabase index."""

    cleaned_query = " ".join(query.split())
    if len(cleaned_query) < 2:
        return []
    if len(cleaned_query) > 120:
        raise ValueError("Food search queries must be 120 characters or fewer.")
    result_limit = min(max(int(limit), 1), 5)
    try:
        response = get_supabase_client().rpc(
            "search_food_names",
            {"search_text": cleaned_query, "result_limit": result_limit},
        ).execute()
    except Exception as exc:
        if getattr(exc, "code", None) == "57014":
            raise RuntimeError(
                "Food-name search timed out. Run /api/foods/search-optimization-schema "
                "in the Supabase SQL Editor once, then retry."
            ) from None
        raise
    return response.data or []


def get_food_product(code: str) -> dict[str, object]:
    """Return one exact Open Food Facts product by barcode/code."""

    global _LAST_PRODUCT_REQUEST
    cleaned_code = "".join(code.split())
    if not cleaned_code:
        raise ValueError("Enter a food code.")
    if len(cleaned_code) > 64 or not cleaned_code.isdigit():
        raise ValueError("Food codes must contain digits only.")

    with _LOCK:
        now = time.monotonic()
        cached = _PRODUCT_CACHE.get(cleaned_code)
        if cached and cached.expires_at > now:
            return {**cached.payload, "cached": True}
        if cached:
            _PRODUCT_CACHE.pop(cleaned_code, None)

        wait_seconds = MIN_PRODUCT_INTERVAL_SECONDS - (now - _LAST_PRODUCT_REQUEST)
        if wait_seconds > 0:
            raise FoodSearchRateLimited(math.ceil(wait_seconds))

        _LAST_PRODUCT_REQUEST = now
        try:
            product = _get_product_api().product.get(cleaned_code)
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 429:
                raise FoodSearchRateLimited(
                    _retry_after_from_exception(exc),
                    "Open Food Facts is rate limiting product lookups. Please wait about a minute and try again.",
                ) from exc
            raise FoodSearchUnavailable(f"Open Food Facts product lookup failed: {exc}") from exc

        if not isinstance(product, dict):
            raise FoodProductNotFound(f"No Open Food Facts product was found for code {cleaned_code}.")

        payload: dict[str, object] = {"code": cleaned_code, "product": product, "cached": False}
        _PRODUCT_CACHE[cleaned_code] = CachedSearch(time.monotonic() + CACHE_TTL_SECONDS, payload)
        return payload


def reset_food_search_state() -> None:
    """Clear cached/rate-limit state. Intended for tests and process resets."""

    global _API, _PRODUCT_API, _LAST_PRODUCT_REQUEST, _LAST_UPSTREAM_REQUEST
    with _LOCK:
        _CACHE.clear()
        _PRODUCT_CACHE.clear()
        _LAST_UPSTREAM_REQUEST = 0.0
        _LAST_PRODUCT_REQUEST = 0.0
        _API = None
        _PRODUCT_API = None
