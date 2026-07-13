"""Validation and persistence for editable public user profiles."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

from .auth import normalize_username
from .db import get_supabase_client


PROFILE_FIELDS = (
    "username",
    "display_name",
    "profile_picture",
    "pace_gender",
    "goals",
    "workout_experience",
    "cuisine_preferences",
    "dietary_preferences",
    "preferred_units",
    "bio",
    "onboarding_completed",
    "created_at",
    "updated_at",
)
EDITABLE_PROFILE_FIELDS = set(PROFILE_FIELDS) - {
    "profile_picture",
    "onboarding_completed",
    "created_at",
    "updated_at",
}
EXPERIENCE_LEVELS = {"beginner", "intermediate", "advanced"}
UNIT_SYSTEMS = {"metric", "imperial"}
PACE_GENDERS = {"female", "male"}
ONBOARDING_FIELDS = {
    "pace_gender",
    "display_name",
    "bio",
    "goals",
    "workout_experience",
    "cuisine_preferences",
    "dietary_preferences",
    "preferred_units",
}


class ProfileInputError(ValueError):
    pass


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileInputError(f"{field} must be text.")
    cleaned = unicodedata.normalize("NFC", value).strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise ProfileInputError(f"{field} must be {maximum} characters or fewer.")
    if any(unicodedata.category(character) in {"Cc", "Cs"} and character not in "\n\t" for character in cleaned):
        raise ProfileInputError(f"{field} cannot contain control characters.")
    return cleaned


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ProfileInputError(f"{field} must be a list.")
    if len(value) > 20:
        raise ProfileInputError(f"{field} can contain at most 20 items.")
    cleaned_values = []
    seen = set()
    for item in value:
        cleaned = _optional_text(item, field, 60)
        if not cleaned:
            continue
        folded = cleaned.casefold()
        if folded not in seen:
            cleaned_values.append(cleaned)
            seen.add(folded)
    return cleaned_values


def _select_profile(user_id: str) -> dict[str, object]:
    rows = (
        get_supabase_client()
        .table("users")
        .select(",".join(PROFILE_FIELDS))
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise LookupError("User profile was not found.")
    return {field: rows[0].get(field) for field in PROFILE_FIELDS}


def get_profile(user_id: str) -> dict[str, object]:
    return _select_profile(user_id)


def _validated_updates(payload: object, allowed_fields: set[str]) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ProfileInputError("Profile body must be a JSON object.")
    unknown = set(payload) - allowed_fields
    if unknown:
        raise ProfileInputError(f"Unsupported profile field: {sorted(unknown)[0]}.")

    updates: dict[str, object] = {}
    if "username" in payload:
        try:
            updates["username"] = normalize_username(payload["username"])
        except ValueError as exc:
            raise ProfileInputError(str(exc)) from exc
    if "display_name" in payload:
        updates["display_name"] = _optional_text(payload["display_name"], "Display name", 80)
    if "pace_gender" in payload:
        pace_gender = payload["pace_gender"] or None
        if pace_gender not in PACE_GENDERS | {None}:
            raise ProfileInputError("Pace avatar must be female or male.")
        updates["pace_gender"] = pace_gender
    if "goals" in payload:
        updates["goals"] = _string_list(payload["goals"], "Goals")
    if "cuisine_preferences" in payload:
        updates["cuisine_preferences"] = _string_list(
            payload["cuisine_preferences"], "Cuisine preferences"
        )
    if "dietary_preferences" in payload:
        updates["dietary_preferences"] = _string_list(
            payload["dietary_preferences"], "Dietary preferences"
        )
    if "workout_experience" in payload:
        experience = payload["workout_experience"] or None
        if experience not in EXPERIENCE_LEVELS | {None}:
            raise ProfileInputError("Workout experience must be beginner, intermediate, or advanced.")
        updates["workout_experience"] = experience
    if "preferred_units" in payload:
        if payload["preferred_units"] not in UNIT_SYSTEMS:
            raise ProfileInputError("Preferred units must be metric or imperial.")
        updates["preferred_units"] = payload["preferred_units"]
    if "bio" in payload:
        updates["bio"] = _optional_text(payload["bio"], "Bio", 500)
    return updates


def _persist_updates(user_id: str, updates: dict[str, object]) -> dict[str, object]:
    if not updates:
        raise ProfileInputError("No editable profile fields were provided.")
    updates["updated_at"] = datetime.now(UTC).isoformat()
    try:
        get_supabase_client().table("users").update(updates).eq("id", user_id).execute()
    except Exception as exc:
        if getattr(exc, "code", None) == "23505":
            raise ProfileInputError("That username is already in use.") from None
        raise
    return _select_profile(user_id)


def update_profile(user_id: str, payload: object) -> dict[str, object]:
    return _persist_updates(user_id, _validated_updates(payload, EDITABLE_PROFILE_FIELDS))


def complete_onboarding(user_id: str, payload: object) -> dict[str, object]:
    updates = _validated_updates(payload, ONBOARDING_FIELDS)
    if updates.get("pace_gender") not in PACE_GENDERS:
        raise ProfileInputError("Choose a Pace avatar before finishing onboarding.")
    updates["onboarding_completed"] = True
    return _persist_updates(user_id, updates)
