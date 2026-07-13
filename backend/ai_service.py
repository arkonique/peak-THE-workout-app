"""Persistence and Supabase/Open Food Facts enrichment for AI chat."""

from __future__ import annotations

import os
import time
import unicodedata
import uuid
from datetime import UTC, datetime

from .ai_chat import generate_fitness_response, get_interaction_history
from .db import get_supabase_client
from .food_search import (
    FoodProductNotFound,
    FoodSearchRateLimited,
    FoodSearchUnavailable,
    get_food_product,
    search_food_names,
)


SESSION_FIELDS = "id,title,created_at,updated_at"
INVENTORY_FIELDS = "id,name,primary_muscle,equipment"
PRODUCT_FIELDS = (
    "code",
    "product_name",
    "product_name_en",
    "generic_name",
    "generic_name_en",
    "brands",
    "quantity",
    "serving_size",
    "serving_quantity",
    "nutrition_data_per",
    "nutriments",
    "ingredients",
    "ingredients_text",
    "ingredients_text_en",
    "ingredients_text_fr",
    "lang",
    "image_front_small_url",
)


class AiChatInputError(ValueError):
    pass


class AiSessionNotFound(LookupError):
    pass


class AiSessionHistoryExpired(LookupError):
    pass


def _retention_days() -> int:
    try:
        return max(1, int(os.getenv("GEMINI_INTERACTION_RETENTION_DAYS", "1")))
    except ValueError:
        return 1


def retention_info() -> dict[str, object]:
    days = _retention_days()
    return {
        "days": days,
        "message": (
            "Google's Gemini free tier deletes stored interaction history after 1 day."
            if days == 1
            else f"Google is configured to retain interaction history for {days} days."
        ),
    }


def _clean_prompt(value: object) -> str:
    if not isinstance(value, str):
        raise AiChatInputError("Prompt must be a string.")
    prompt = unicodedata.normalize("NFC", value).strip()
    if not prompt:
        raise AiChatInputError("Enter a message before sending.")
    if len(prompt) > 6000:
        raise AiChatInputError("Messages must be 6000 characters or fewer.")
    if any(unicodedata.category(character) in {"Cc", "Cs"} and character not in "\n\r\t" for character in prompt):
        raise AiChatInputError("Messages cannot contain control characters.")
    return prompt


def _session_id(value: object) -> str | None:
    if value in {None, "", "-1"}:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AiChatInputError("session_id must be a valid session identifier.") from exc


def _public_session(row: dict[str, object]) -> dict[str, object]:
    session = {field: row.get(field) for field in SESSION_FIELDS.split(",")}
    updated_at = row.get("updated_at")
    if isinstance(updated_at, str):
        try:
            updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            session["history_expires_at"] = datetime.fromtimestamp(
                updated.timestamp() + _retention_days() * 86400,
                UTC,
            ).isoformat()
        except ValueError:
            pass
    return session


def list_sessions(user_id: str) -> list[dict[str, object]]:
    rows = (
        get_supabase_client()
        .table("ai_sessions")
        .select(SESSION_FIELDS)
        .eq("user_id", user_id)
        .order("updated_at", desc=True)
        .execute()
        .data
    )
    return rows or []


def _owned_session(user_id: str, session_id: str) -> dict[str, object]:
    rows = (
        get_supabase_client()
        .table("ai_sessions")
        .select("id,user_id,title,latest_provider_interaction_id,created_at,updated_at")
        .eq("id", session_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise AiSessionNotFound("AI session was not found.")
    return rows[0]


def get_session(user_id: str, session_id_value: object) -> dict[str, object]:
    session_id = _session_id(session_id_value)
    if not session_id:
        raise AiChatInputError("A session identifier is required.")
    session = _owned_session(user_id, session_id)
    interaction_id = session.get("latest_provider_interaction_id")
    if not interaction_id:
        messages = []
    else:
        try:
            messages = get_interaction_history(str(interaction_id))
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if status in {404, "404", "NOT_FOUND"}:
                raise AiSessionHistoryExpired(
                    "This chat has expired from Google's Interactions API and can no longer be opened."
                ) from exc
            raise
    return {
        "session": _public_session(session),
        "messages": messages,
        "retention": retention_info(),
    }


def _exercise_inventory() -> list[dict[str, object]]:
    return (
        get_supabase_client()
        .table("exercises")
        .select(INVENTORY_FIELDS)
        .order("id")
        .execute()
        .data
        or []
    )


def _exercise_details(model_response: dict[str, object]) -> list[dict[str, object]]:
    workout = model_response.get("workout")
    exercises = workout.get("exercises", []) if isinstance(workout, dict) else []
    ids = []
    for exercise in exercises if isinstance(exercises, list) else []:
        exercise_id = exercise.get("exercise_id") if isinstance(exercise, dict) else None
        if isinstance(exercise_id, int) and exercise_id not in ids:
            ids.append(exercise_id)
    if not ids:
        return []

    rows = (
        get_supabase_client()
        .table("exercises")
        .select("*")
        .in_("id", ids)
        .execute()
        .data
        or []
    )
    by_id = {row.get("id"): row for row in rows}
    return [by_id[exercise_id] for exercise_id in ids if exercise_id in by_id]


def _trim_product(product: dict[str, object]) -> dict[str, object]:
    return {field: product.get(field) for field in PRODUCT_FIELDS if product.get(field) is not None}


def _product_with_local_wait(code: str) -> dict[str, object]:
    try:
        return get_food_product(code)
    except FoodSearchRateLimited as exc:
        if exc.retry_after > 8:
            raise
        time.sleep(exc.retry_after)
        return get_food_product(code)


def _food_details(model_response: dict[str, object]) -> list[dict[str, object]]:
    meals = model_response.get("meals")
    if not isinstance(meals, list):
        return []

    details = []
    product_by_code: dict[str, dict[str, object]] = {}
    for meal in meals:
        if not isinstance(meal, dict):
            continue
        meal_name = str(meal.get("meal_name") or "Meal")
        foods = meal.get("foods")
        for food in foods if isinstance(foods, list) else []:
            if not isinstance(food, dict):
                continue
            requested_name = str(food.get("name") or "").strip()
            detail: dict[str, object] = {
                "meal_name": meal_name,
                "requested_name": requested_name,
                "quantity": food.get("quantity"),
                "reasoning": food.get("reasoning"),
            }
            try:
                matches = search_food_names(requested_name, limit=1)
                if not matches:
                    detail["error"] = "No matching food code was found in public.foods."
                    details.append(detail)
                    continue
                match = matches[0]
                code = str(match.get("code") or "")
                detail["match"] = {
                    "code": code,
                    "name": match.get("name"),
                    "score": match.get("score"),
                }
                if code not in product_by_code:
                    payload = _product_with_local_wait(code)
                    product = payload.get("product")
                    if not isinstance(product, dict):
                        raise FoodProductNotFound(f"No product data was returned for code {code}.")
                    product_by_code[code] = _trim_product(product)
                detail["product"] = product_by_code[code]
            except (FoodSearchRateLimited, FoodSearchUnavailable, FoodProductNotFound, ValueError, RuntimeError) as exc:
                detail["error"] = str(exc)
            details.append(detail)
    return details


def _title_from_prompt(prompt: str) -> str:
    title = " ".join(prompt.split())
    return title[:77] + "..." if len(title) > 80 else title


def send_message(user_id: str, prompt_value: object, session_id_value: object = None) -> dict[str, object]:
    """Persist only the provider cursor and return the provider-hosted conversation."""

    prompt = _clean_prompt(prompt_value)
    session_id = _session_id(session_id_value)
    client = get_supabase_client()

    if session_id:
        session = _owned_session(user_id, session_id)
    else:
        inserted = (
            client.table("ai_sessions")
            .insert({"user_id": user_id, "title": _title_from_prompt(prompt)})
            .execute()
            .data
        )
        if not inserted:
            raise RuntimeError("Supabase did not create the AI session.")
        session = inserted[0]
        session_id = str(session["id"])

    generated = generate_fitness_response(
        prompt,
        _exercise_inventory(),
        session.get("latest_provider_interaction_id"),
    )
    model_response = generated["model_response"]
    if not isinstance(model_response, dict):
        raise RuntimeError("Gemini returned an unexpected structured response.")
    enriched = {
        "model_response": model_response,
        "exercise_details": _exercise_details(model_response),
        "food_details": _food_details(model_response),
    }
    provider_interaction_id = str(generated["provider_interaction_id"])
    now = datetime.now(UTC).isoformat()
    client.table("ai_sessions").update(
        {"latest_provider_interaction_id": provider_interaction_id, "updated_at": now}
    ).eq("id", session_id).eq("user_id", user_id).execute()
    try:
        result = get_session(user_id, session_id)
    except Exception:
        result = {
            "session": _public_session({**session, "updated_at": now}),
            "messages": [
                {"role": "user", "content": prompt},
                {
                    "role": "assistant",
                    "content": str(generated["raw_text"]),
                    "structured_data": enriched,
                },
            ],
            "retention": retention_info(),
        }
        return result

    for message in reversed(result["messages"]):
        if message.get("role") == "assistant":
            message["structured_data"] = enriched
            break
    return result
