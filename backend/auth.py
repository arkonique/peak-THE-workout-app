"""Authentication and profile helpers backed by Supabase Auth."""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email

from .db import (
    get_supabase_client,
    get_supabase_public_client,
    get_supabase_public_credentials,
)


USERNAME_RE = re.compile(r"^[a-z0-9_]{3,30}$")
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128
COMMON_PASSWORDS = {
    "123456789012345",
    "1234567890123456",
    "correcthorsebatterystaple",
    "letmeinletmeinletmein",
    "passwordpassword",
    "password123456789",
    "qwertyqwertyqwerty",
}


class AuthInputError(ValueError):
    pass


class AuthRateLimited(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    expires_in: int


class AttemptLimiter:
    """Small per-process guard in addition to Supabase Auth's project rate limits."""

    def __init__(self) -> None:
        self._attempts: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, action: str, address: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        key = (action, address)
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= now - window_seconds:
                attempts.popleft()
            if len(attempts) >= limit:
                retry_after = max(1, round(window_seconds - (now - attempts[0])))
                raise AuthRateLimited(f"Too many attempts. Try again in {retry_after} seconds.")
            attempts.append(now)


AUTH_ATTEMPTS = AttemptLimiter()


def normalize_email(value: object) -> str:
    if not isinstance(value, str) or len(value) > 320:
        raise AuthInputError("Enter a valid email address.")
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise AuthInputError("Enter a valid email address.") from exc


def normalize_username(value: object) -> str:
    if not isinstance(value, str):
        raise AuthInputError("Username must be 3-30 lowercase letters, numbers, or underscores.")
    username = unicodedata.normalize("NFKC", value).strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise AuthInputError("Username must be 3-30 lowercase letters, numbers, or underscores.")
    return username


def normalize_password(value: object, *, email: str | None = None, username: str | None = None) -> str:
    if not isinstance(value, str):
        raise AuthInputError("Password is required.")
    password = unicodedata.normalize("NFC", value)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthInputError(f"Password must contain at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthInputError(f"Password must contain no more than {MAX_PASSWORD_LENGTH} characters.")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in password):
        raise AuthInputError("Password cannot contain control characters.")

    folded = password.casefold()
    contextual_values = {"peak", "peakworkout", "peaktheworkoutapp"}
    if username:
        contextual_values.add(username.casefold())
    if email:
        contextual_values.add(email.partition("@")[0].casefold())
    if folded in COMMON_PASSWORDS or folded in contextual_values:
        raise AuthInputError("Choose a less common password that is unrelated to your account details.")
    return password


def _profile_for_user(user_id: str) -> dict[str, object] | None:
    client = get_supabase_client()
    fields = (
        "username,display_name,profile_picture,pace_gender,goals,workout_experience,"
        "cuisine_preferences,dietary_preferences,preferred_units,bio,"
        "onboarding_completed,created_at,updated_at"
    )
    try:
        response = client.table("users").select(fields).eq("id", user_id).limit(1).execute()
    except Exception as exc:
        if getattr(exc, "code", None) not in {"42703", "PGRST204"}:
            raise
        response = (
            client.table("users")
            .select("username,display_name,created_at,updated_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    return response.data[0] if response.data else None


def resolve_login_email(identifier_value: object) -> str:
    if not isinstance(identifier_value, str):
        raise AuthInputError("Invalid username/email or password.")
    identifier = unicodedata.normalize("NFKC", identifier_value).strip()
    if "@" in identifier:
        return normalize_email(identifier)

    try:
        username = normalize_username(identifier)
    except AuthInputError as exc:
        raise AuthInputError("Invalid username/email or password.") from exc
    response = (
        get_supabase_client()
        .table("users")
        .select("id")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise AuthInputError("Invalid username/email or password.")
    user_response = get_supabase_client().auth.admin.get_user_by_id(response.data[0]["id"])
    user = user_response.user
    if user is None or not user.email:
        raise AuthInputError("Invalid username/email or password.")
    return normalize_email(user.email)


def _session_tokens(session) -> SessionTokens:
    if session is None:
        raise RuntimeError("Supabase did not return a session. Confirm the email before logging in.")
    return SessionTokens(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=int(session.expires_in or 3600),
    )


def signup(email_value: object, username_value: object, password_value: object) -> dict[str, object]:
    email = normalize_email(email_value)
    username = normalize_username(username_value)
    password = normalize_password(password_value, email=email, username=username)
    response = get_supabase_public_client().auth.sign_up(
        {
            "email": email,
            "password": password,
            "options": {"data": {"username": username}},
        }
    )
    user = response.user
    if user is None:
        raise RuntimeError("Supabase did not create the account.")
    return {
        "email": email,
        "username": username,
        "email_confirmation_required": response.session is None,
        "session": _session_tokens(response.session) if response.session else None,
    }


def login(identifier_value: object, password_value: object) -> dict[str, object]:
    password = normalize_password(password_value)
    email = resolve_login_email(identifier_value)
    response = get_supabase_public_client().auth.sign_in_with_password(
        {"email": email, "password": password}
    )
    user = response.user
    if user is None:
        raise RuntimeError("Invalid username/email or password.")
    return {
        "email": user.email,
        "profile": _profile_for_user(user.id),
        "session": _session_tokens(response.session),
    }


def refresh_session(refresh_token: str) -> dict[str, object]:
    response = get_supabase_public_client().auth.refresh_session(refresh_token)
    user = response.user
    if user is None:
        raise RuntimeError("Session could not be refreshed.")
    return {
        "email": user.email,
        "profile": _profile_for_user(user.id),
        "session": _session_tokens(response.session),
    }


def session_from_tokens(access_token: object, refresh_token: object) -> dict[str, object]:
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise AuthInputError("A valid Supabase session is required.")
    client = get_supabase_public_client()
    response = client.auth.set_session(access_token, refresh_token)
    user_response = client.auth.get_user(response.session.access_token)
    user = user_response.user
    if user is None:
        raise RuntimeError("Session validation failed.")
    return {
        "email": user.email,
        "profile": _profile_for_user(user.id),
        "session": _session_tokens(response.session),
    }


def current_user(access_token: str) -> dict[str, object]:
    response = get_supabase_public_client().auth.get_user(access_token)
    user = response.user
    if user is None:
        raise RuntimeError("Session is not valid.")
    return {
        "user_id": user.id,
        "email": user.email,
        "profile": _profile_for_user(user.id),
    }


def logout(access_token: str | None, refresh_token: str | None) -> None:
    if not access_token or not refresh_token:
        return
    client = get_supabase_public_client()
    client.auth.set_session(access_token, refresh_token)
    client.auth.sign_out(options={"scope": "local"})


def public_auth_config() -> dict[str, str]:
    url, key = get_supabase_public_credentials()
    return {"url": url, "publishable_key": key}
