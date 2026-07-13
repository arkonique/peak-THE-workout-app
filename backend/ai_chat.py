"""Reusable structured Gemini fitness-chat client."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError


class ExerciseObject(BaseModel):
    exercise_id: int = Field(description="Exact database ID from the permitted exercise inventory.")
    exercise_name: str = Field(description="Exercise name from the permitted inventory.")
    reasoning: str = Field(description="Why this exercise fits the request.")


class WorkoutObject(BaseModel):
    workout_name: str = Field(description="Short descriptive name for the routine.")
    exercises: List[ExerciseObject]


class FoodObject(BaseModel):
    name: str = Field(description="Searchable generic or packaged food name.")
    quantity: str = Field(description="Portion size, including units.")
    reasoning: str = Field(description="Nutritional purpose of this item.")


class MealObject(BaseModel):
    meal_name: str
    foods: List[FoodObject]


class MealPlanResponse(BaseModel):
    workout: Optional[WorkoutObject] = None
    meals: Optional[List[MealObject]] = None


def _model_name() -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
    if not model:
        raise RuntimeError("GEMINI_MODEL cannot be empty.")
    return model


@lru_cache(maxsize=1)
def _client():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env.")
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed. Run: pip install -r requirements.txt") from exc
    return genai.Client(api_key=api_key)


def _json_object_candidates(text: str):
    for start, character in enumerate(text):
        if character != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            character = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : end + 1]
                    break


def _parse_response(output_text: str) -> MealPlanResponse:
    text = (output_text or "").strip()
    seen: set[str] = set()
    for candidate in (text, *_json_object_candidates(text)):
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return MealPlanResponse.model_validate_json(candidate)
        except ValidationError:
            continue
    preview = " ".join(text.split())[:200] or "<empty response>"
    raise RuntimeError(f"Gemini returned an invalid structured response: {preview!r}")


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return "".join(parts)


def _clean_history_input(text: str) -> str:
    """Hide inventory appended by older versions of this test client."""

    marker = "\n\n### PERMITTED EXERCISE INVENTORY"
    return text.partition(marker)[0].strip()


def get_interaction_history(interaction_id: str) -> list[dict[str, object]]:
    """Fetch a complete server-stored conversation from the Interactions API."""

    interaction = _client().interactions.get(id=interaction_id, include_input=True)
    messages = []
    for step in getattr(interaction, "steps", None) or []:
        if isinstance(step, dict):
            step_type = step.get("type")
            content = step.get("content")
        else:
            step_type = getattr(step, "type", None)
            content = getattr(step, "content", None)
        role = {"user_input": "user", "model_output": "assistant"}.get(step_type)
        text = _content_text(content)
        if not role or not text:
            continue
        if role == "user":
            text = _clean_history_input(text)
        message: dict[str, object] = {"role": role, "content": text}
        if role == "assistant":
            try:
                message["structured_data"] = {
                    "model_response": _parse_response(text).model_dump(exclude_none=True)
                }
            except RuntimeError:
                pass
        messages.append(message)
    return messages


def generate_fitness_response(
    prompt: str,
    exercise_inventory: list[dict[str, object]],
    previous_interaction_id: str | None = None,
) -> dict[str, object]:
    """Send one turn and return provider state plus validated model data."""

    system_instruction = (
        "You are an expert fitness trainer and nutritionist. Answer the user's current request. "
        "For workouts, select exercises only from the supplied inventory and copy each exact ID and name. "
        "For meals, use concise food names that can be matched against a food-product database. "
        "Return only the requested structured response."
    )
    inventory_instruction = ""
    if exercise_inventory:
        inventory_instruction = (
            "\n\n### PERMITTED EXERCISE INVENTORY\n"
            + json.dumps(exercise_inventory, ensure_ascii=False, separators=(",", ":"))
        )

    request: dict[str, object] = {
        "model": _model_name(),
        "input": prompt,
        "system_instruction": system_instruction + inventory_instruction,
        "store": True,
        "generation_config": {"temperature": 0.3},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": MealPlanResponse.model_json_schema(),
        },
    }
    if previous_interaction_id:
        request["previous_interaction_id"] = previous_interaction_id

    interaction = _client().interactions.create(**request)
    raw_text = interaction.output_text or ""
    parsed = _parse_response(raw_text)
    return {
        "provider_interaction_id": interaction.id,
        "raw_text": raw_text,
        "model_response": parsed.model_dump(exclude_none=True),
    }
