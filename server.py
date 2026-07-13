from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import sys
import threading
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from supabase_auth.errors import AuthApiError


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
APP_FRONTEND_DIR = ROOT_DIR / "app_frontend"
PACE_DIR = ROOT_DIR / "PACE"
BACKEND_DIR = ROOT_DIR / "backend"
DEFAULT_EXERCISES_JSON = FRONTEND_DIR / "exercises.json"
DEFAULT_METRICS_XLSX = ROOT_DIR / "metrics.xlsx"
FOOD_UPLOAD_DIR = ROOT_DIR / ".uploads"
PROFILE_PICTURE_BUCKET = "profile-pictures"
LEGACY_PROFILE_PICTURES_DIR = ROOT_DIR / "profile_pictures"
MAX_FOOD_UPLOAD_BYTES = int(os.environ.get("MAX_FOOD_UPLOAD_BYTES", str(20 * 1024**3)))
MAX_PROFILE_PICTURE_BYTES = 1024 * 1024

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.sync_exercises import (  # noqa: E402
    collect_exercise_records,
    exercise_items_to_records,
    exercise_records_to_items,
    run as build_exercise_index,
)
from backend.db import (  # noqa: E402
    delete_rows,
    get_supabase_client,
    insert_rows,
    select_all_rows,
    select_rows,
    update_rows,
    upsert_rows,
    validate_supabase_config,
)
from backend.auth import (  # noqa: E402
    AUTH_ATTEMPTS,
    AuthInputError,
    AuthRateLimited,
    SessionTokens,
    current_user,
    login,
    logout,
    public_auth_config,
    refresh_session,
    session_from_tokens,
    signup,
)
from backend.schema import (  # noqa: E402
    AI_SESSIONS_SQL,
    APP_SCHEMA_SQL,
    EXERCISE_COLUMNS,
    EXERCISE_METRICS_SQL,
    EXERCISES_SQL,
    FOODS_SEARCH_OPTIMIZATION_SQL,
    FOODS_SQL,
    BODY_METRICS_SQL,
    USERS_SQL,
)
from backend.search import clear_search_cache, fuzzy_search_table  # noqa: E402
from backend.food_search import (  # noqa: E402
    FoodProductNotFound,
    FoodSearchRateLimited,
    FoodSearchUnavailable,
    get_food_product,
    search_food,
    search_food_names,
)
from backend.sync_foods import sync_food_names_from_parquet  # noqa: E402
from backend.sync_metrics import sync_metrics_from_xlsx  # noqa: E402
from backend.sync_exercise_metrics import sync_exercise_metrics  # noqa: E402
from backend.full_schema import EXPECTED_PUBLIC_TABLES  # noqa: E402
from backend.ai_service import (  # noqa: E402
    AiChatInputError,
    AiSessionHistoryExpired,
    AiSessionNotFound,
    get_session as get_ai_session,
    list_sessions as list_ai_sessions,
    retention_info as ai_retention_info,
    send_message as send_ai_message,
)
from backend.profile import (  # noqa: E402
    ProfileInputError,
    complete_onboarding,
    update_profile,
)


GENERIC_READ_ONLY_TABLES = {
    "body_metrics",
    "exercise_metrics",
    "exercises",
    "foods",
    "muscle_groups",
    "nutrients",
}


@lru_cache(maxsize=1)
def _frontend_index_exists() -> bool:
    return (FRONTEND_DIR / "index.html").exists()


def resolve_frontend_path(request_path: str) -> Path:
    parsed = urlsplit(request_path)
    route = unquote(parsed.path).lstrip("/").rstrip("/")

    if route in {"", "login", "signup", "app", "app/login", "app/signup"}:
        return APP_FRONTEND_DIR / "index.html"
    if route in {"dashboard", "app/dashboard"}:
        return APP_FRONTEND_DIR / "dashboard.html"
    if route in {
        "workout",
        "app/workout",
        "meals",
        "app/meals",
        "charts",
        "app/charts",
        "plan",
        "app/plan",
        "friends",
        "app/friends",
        "leagues",
        "app/leagues",
    }:
        return APP_FRONTEND_DIR / "section.html"
    if route in {"profile", "app/profile"}:
        return APP_FRONTEND_DIR / "profile.html"
    if route in {"onboarding", "app/onboarding"}:
        return APP_FRONTEND_DIR / "onboarding.html"
    if route == "app/assets/logo.png":
        return ROOT_DIR / "logo.png"
    if route.startswith("app/pace/"):
        asset_name = route.removeprefix("app/pace/")
        pace_root = PACE_DIR.resolve()
        asset_path = (pace_root / asset_name).resolve()
        if asset_path.is_relative_to(pace_root) and asset_path.suffix.lower() == ".png":
            return asset_path
        return pace_root / "not-found"
    if route.startswith("app/assets/"):
        asset_name = route.removeprefix("app/assets/")
        assets_root = (APP_FRONTEND_DIR / "assets").resolve()
        asset_path = (assets_root / asset_name).resolve()
        if asset_path.is_relative_to(assets_root):
            return asset_path
        return assets_root / "not-found"

    candidate = FRONTEND_DIR / route
    if candidate.exists():
        if candidate.is_dir():
            index_candidate = candidate / "index.html"
            if index_candidate.exists():
                return index_candidate
        return candidate

    if not candidate.suffix:
        html_candidate = candidate.with_suffix(".html")
        if html_candidate.exists():
            return html_candidate
        index_candidate = candidate / "index.html"
        if index_candidate.exists():
            return index_candidate
        return html_candidate

    return candidate


def refresh_exercise_list(output_path: str | Path | None = None, progress_callback=None) -> Path:
    validate_supabase_config()
    target = Path(output_path) if output_path is not None else DEFAULT_EXERCISES_JSON
    target.parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("loading", 0, None, "Loading existing exercises from Supabase.")
    try:
        existing_records = select_all_rows("exercises", ",".join(EXERCISE_COLUMNS[1:]))
    except Exception as exc:
        if getattr(exc, "code", None) in {"42P01", "PGRST205"}:
            raise RuntimeError(
                "Supabase table public.exercises does not exist. Create it in the Supabase SQL Editor "
                "using the SQL from GET /api/exercises/schema, then retry the refresh."
            ) from exc
        raise

    existing_items = exercise_records_to_items(existing_records)
    existing_urls = {item["url"] for item in existing_items}
    if progress_callback:
        progress_callback(
            "loading",
            len(existing_items),
            len(existing_items),
            f"Loaded {len(existing_items)} existing exercises from Supabase.",
        )

    output = build_exercise_index(
        target,
        progress_callback=progress_callback,
        existing_items=existing_items,
    )
    items = json.loads(output)
    if not isinstance(items, list):
        raise RuntimeError("The generated exercise list is not a JSON array.")

    new_items = [item for item in items if item.get("url") not in existing_urls]
    records = exercise_items_to_records(new_items)
    if progress_callback:
        progress_callback("syncing", 0, len(records), f"Uploading {len(records)} new exercises to Supabase.")
    for start in range(0, len(records), 100):
        batch = records[start : start + 100]
        upsert_rows("exercises", batch, on_conflict="url")
        clear_search_cache("exercises")
        if progress_callback:
            completed = start + len(batch)
            progress_callback(
                "syncing",
                completed,
                len(records),
                f"Uploaded {completed}/{len(records)} new exercises to Supabase.",
            )
    clear_search_cache("exercises")
    return target


REFRESH_LOCK = threading.Lock()
REFRESH_STATE: dict[str, object] = {
    "status": "idle",
    "phase": "idle",
    "current": 0,
    "total": None,
    "progress": 0,
    "message": "Waiting to start.",
    "started_at": None,
    "finished_at": None,
    "output": str(DEFAULT_EXERCISES_JSON),
    "error": None,
    "log": [],
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def get_refresh_status() -> dict[str, object]:
    with REFRESH_LOCK:
        snapshot = dict(REFRESH_STATE)
        snapshot["log"] = list(REFRESH_STATE["log"])
        return snapshot


def _update_refresh_progress(phase: str, current: int, total: int | None, message: str) -> None:
    if phase == "loading":
        percent = 1
    elif phase == "discovering":
        percent = 2
    elif phase == "filtering":
        percent = 8
    elif phase == "fetching" and total:
        percent = 10 + round((current / total) * 80)
    elif phase == "fetching":
        percent = 90
    elif phase == "saving":
        percent = 92
    elif phase == "syncing" and total:
        percent = 92 + round((current / total) * 7)
    elif phase == "syncing":
        percent = 99
    else:
        percent = 0

    with REFRESH_LOCK:
        REFRESH_STATE.update(
            phase=phase,
            current=current,
            total=total,
            progress=percent,
            message=message,
        )
        log = REFRESH_STATE["log"]
        if isinstance(log, list):
            log.append({"time": _timestamp(), "message": message})
            del log[:-100]


def _run_refresh_job() -> None:
    try:
        saved_path = refresh_exercise_list(progress_callback=_update_refresh_progress)
        with REFRESH_LOCK:
            REFRESH_STATE.update(
                status="complete",
                phase="complete",
                progress=100,
                message=f"Refresh complete. Saved {saved_path} and synced the exercises to Supabase.",
                finished_at=_timestamp(),
                output=str(saved_path),
            )
            log = REFRESH_STATE["log"]
            if isinstance(log, list):
                log.append({"time": _timestamp(), "message": REFRESH_STATE["message"]})
    except Exception as exc:
        with REFRESH_LOCK:
            REFRESH_STATE.update(
                status="error",
                phase="error",
                message=f"Refresh failed: {exc}",
                finished_at=_timestamp(),
                error=str(exc),
            )
            log = REFRESH_STATE["log"]
            if isinstance(log, list):
                log.append({"time": _timestamp(), "message": REFRESH_STATE["message"]})


def start_refresh_job() -> bool:
    """Start a background exercise-list refresh; return False if one is already running."""
    with REFRESH_LOCK:
        if REFRESH_STATE["status"] == "running":
            return False
        started_at = _timestamp()
        REFRESH_STATE.update(
            status="running",
            phase="starting",
            current=0,
            total=None,
            progress=0,
            message="Starting exercise-list refresh.",
            started_at=started_at,
            finished_at=None,
            output=str(DEFAULT_EXERCISES_JSON),
            error=None,
            log=[{"time": started_at, "message": "Starting exercise-list refresh."}],
        )

    threading.Thread(target=_run_refresh_job, name="exercise-list-refresh", daemon=True).start()
    return True


FOOD_REFRESH_LOCK = threading.Lock()
FOOD_REFRESH_STATE: dict[str, object] = {
    "status": "idle",
    "phase": "idle",
    "current": 0,
    "total": None,
    "progress": 0,
    "message": "Waiting for a Parquet file.",
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def get_food_refresh_status() -> dict[str, object]:
    with FOOD_REFRESH_LOCK:
        return dict(FOOD_REFRESH_STATE)


def _update_food_refresh_progress(phase: str, current: int, total: int | None, message: str) -> None:
    with FOOD_REFRESH_LOCK:
        FOOD_REFRESH_STATE.update(
            status="running",
            phase=phase,
            current=current,
            total=total,
            progress=None,
            message=message,
        )


def _run_food_refresh_job(path: Path, column: str, delete_after: bool) -> None:
    try:
        imported = sync_food_names_from_parquet(
            path,
            column=column,
            progress_callback=_update_food_refresh_progress,
        )
        clear_search_cache("foods")
        with FOOD_REFRESH_LOCK:
            FOOD_REFRESH_STATE.update(
                status="complete",
                phase="complete",
                current=imported,
                total=imported,
                progress=100,
                message=f"Food index refresh complete. Imported {imported:,} English names and codes.",
                finished_at=_timestamp(),
            )
    except Exception as exc:
        with FOOD_REFRESH_LOCK:
            FOOD_REFRESH_STATE.update(
                status="error",
                phase="error",
                message=f"Food index refresh failed: {exc}",
                finished_at=_timestamp(),
                error=str(exc),
            )
    finally:
        if delete_after:
            path.unlink(missing_ok=True)


def start_food_refresh_job(path: Path, column: str = "product_name", delete_after: bool = False) -> bool:
    with FOOD_REFRESH_LOCK:
        if FOOD_REFRESH_STATE["status"] in {"uploading", "running"}:
            return False
        started_at = _timestamp()
        FOOD_REFRESH_STATE.update(
            status="running",
            phase="queued",
            current=0,
            total=None,
            progress=None,
            message=f"Queued {path.name} for code/name extraction.",
            started_at=started_at,
            finished_at=None,
            error=None,
        )
    threading.Thread(
        target=_run_food_refresh_job,
        args=(path, column, delete_after),
        name="food-name-refresh",
        daemon=True,
    ).start()
    return True


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def translate_path(self, path: str) -> str:
        return str(resolve_frontend_path(path))

    def log_message(self, format: str, *args) -> None:
        return

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if code == 404 and urlsplit(self.path).path.startswith("/api/"):
            self._send_json(404, {"error": "API endpoint not found."})
            return
        error_pages = {
            404: ("404.html", "Not Found"),
            500: ("500.html", "Internal Server Error"),
        }
        if code not in error_pages:
            super().send_error(code, message, explain)
            return
        page_name, response_message = error_pages[code]
        try:
            body = (APP_FRONTEND_DIR / page_name).read_bytes()
        except OSError:
            super().send_error(code, message, explain)
            return
        self.send_response(code, response_message)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self._cookie_secure():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        super().end_headers()

    def _api_parts(self) -> list[str] | None:
        parts = urlsplit(self.path).path.strip("/").split("/")
        if 2 <= len(parts) <= 4 and parts[0] == "api":
            return parts[1:]
        return None

    def _read_json(self, *, max_bytes: int = 1024 * 1024) -> object:
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json.")
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        if length > max_bytes:
            raise ValueError("Request body is too large.")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(
        self,
        status: int,
        payload: object,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(
        self,
        location: str,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()

    def _send_app_html(
        self,
        path: Path,
        headers: list[tuple[str, str]] | None = None,
        bootstrap_identity: dict[str, object] | None = None,
    ) -> None:
        try:
            html = path.read_text(encoding="utf-8")
        except OSError:
            self.send_error(404)
            return

        if bootstrap_identity is not None:
            profile = bootstrap_identity.get("profile")
            payload = {"profile": profile if isinstance(profile, dict) else {}}
            serialized = (
                json.dumps(payload, ensure_ascii=False)
                .replace("&", "\\u0026")
                .replace("<", "\\u003c")
                .replace(">", "\\u003e")
            )
            bootstrap = (
                f'\n  <script id="peak-bootstrap" type="application/json">'
                f"{serialized}</script>"
            )
            if "</head>" in html:
                html = html.replace("</head>", f"{bootstrap}\n</head>", 1)
            else:
                html += bootstrap

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _cookies(self) -> SimpleCookie:
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", ""))
        return cookies

    def _cookie_secure(self) -> bool:
        configured = os.environ.get("PEAK_COOKIE_SECURE")
        if configured is not None:
            return configured.strip().lower() not in {"0", "false", "no"}
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _session_cookie_headers(self, session: SessionTokens) -> list[tuple[str, str]]:
        secure = "; Secure" if self._cookie_secure() else ""
        refresh_seconds = int(os.environ.get("PEAK_REFRESH_COOKIE_SECONDS", str(30 * 24 * 60 * 60)))
        return [
            (
                "Set-Cookie",
                f"peak_access={session.access_token}; Path=/; Max-Age={session.expires_in}; "
                f"HttpOnly; SameSite=Lax{secure}",
            ),
            (
                "Set-Cookie",
                f"peak_refresh={session.refresh_token}; Path=/api/auth; Max-Age={refresh_seconds}; "
                f"HttpOnly; SameSite=Lax{secure}",
            ),
        ]

    def _clear_session_cookie_headers(self) -> list[tuple[str, str]]:
        secure = "; Secure" if self._cookie_secure() else ""
        return [
            ("Set-Cookie", f"peak_access=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}"),
            (
                "Set-Cookie",
                f"peak_refresh=; Path=/api/auth; Max-Age=0; HttpOnly; SameSite=Lax{secure}",
            ),
        ]

    def _require_same_origin(self) -> None:
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site and fetch_site != "same-origin":
            raise PermissionError("Cross-origin authentication requests are not allowed.")
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != self.headers.get("Host", "").lower():
            raise PermissionError("Cross-origin authentication requests are not allowed.")

    def _authenticated_user(self) -> dict[str, object]:
        authorization = self.headers.get("Authorization", "")
        access_token = ""
        if authorization.lower().startswith("bearer "):
            access_token = authorization[7:].strip()
        if not access_token:
            cookie = self._cookies().get("peak_access")
            access_token = cookie.value if cookie else ""
        if not access_token:
            raise PermissionError("Sign in before using AI chat.")
        result = current_user(access_token)
        if not result.get("user_id") or not isinstance(result.get("profile"), dict):
            raise PermissionError("The authenticated account has no user profile.")
        return result

    def _page_session(self) -> tuple[list[tuple[str, str]], dict[str, object]] | None:
        cookies = self._cookies()
        access = cookies.get("peak_access")
        if access:
            try:
                return [], current_user(access.value)
            except Exception:
                pass
        refresh = cookies.get("peak_refresh")
        if not refresh:
            return None
        try:
            result = refresh_session(refresh.value)
            session = result.pop("session")
            return self._session_cookie_headers(session), result
        except Exception:
            return None

    def _handle_ai(self, method: str, parts: list[str]) -> None:
        try:
            identity = self._authenticated_user()
            user_id = str(identity["user_id"])

            if method == "GET" and parts == ["sessions"]:
                self._send_json(
                    200,
                    {"data": {"sessions": list_ai_sessions(user_id), "retention": ai_retention_info()}},
                )
                return
            if method == "GET" and len(parts) == 2 and parts[0] == "sessions":
                self._send_json(200, {"data": get_ai_session(user_id, parts[1])})
                return
            if method == "POST" and parts == ["chat"]:
                self._require_same_origin()
                payload = self._read_json(max_bytes=16 * 1024)
                if not isinstance(payload, dict):
                    raise AiChatInputError("JSON body must be an object.")
                profile = identity["profile"]
                authenticated_username = str(profile.get("username") or "")
                supplied_username = str(payload.get("username") or "").strip()
                if not supplied_username:
                    raise AiChatInputError("username is required.")
                if supplied_username.casefold() != authenticated_username.casefold():
                    raise PermissionError("username does not match the authenticated account.")
                AUTH_ATTEMPTS.check(
                    "ai-chat",
                    f"{self.client_address[0]}:{user_id}",
                    limit=30,
                    window_seconds=600,
                )
                data = send_ai_message(user_id, payload.get("prompt"), payload.get("session_id"))
                self._send_json(200, {"data": data})
                return

            self._send_json(405, {"error": "Method not allowed."})
        except AuthRateLimited as exc:
            self._send_json(429, {"error": str(exc)})
        except AiChatInputError as exc:
            self._send_json(400, {"error": str(exc)})
        except AiSessionNotFound as exc:
            self._send_json(404, {"error": str(exc)})
        except AiSessionHistoryExpired as exc:
            self._send_json(410, {"error": str(exc)})
        except PermissionError as exc:
            self._send_json(401, {"error": str(exc)})
        except Exception as exc:
            reference = uuid.uuid4().hex[:10]
            detail = str(exc).replace("\r", " ").replace("\n", " ")[:500]
            print(
                f"[ai:{reference}] request failed: {type(exc).__name__} detail={detail}",
                file=sys.stderr,
                flush=True,
            )
            message = "AI chat is unavailable."
            if "ai_sessions" in detail:
                message = "Create the AI session tables using /api/ai/schema, then retry."
            self._send_json(502, {"error": message, "reference": reference})

    def _handle_profile_picture(self) -> None:
        self._require_same_origin()
        identity = self._authenticated_user()
        user_id = str(identity["user_id"])
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if content_type != "image/png":
            raise ValueError("Profile pictures must be uploaded as PNG images.")
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid profile-picture upload size.") from exc
        if content_length <= 0 or content_length > MAX_PROFILE_PICTURE_BYTES:
            raise ValueError("Profile pictures must be between 1 byte and 1 MB.")

        from PIL import Image, ImageOps, UnidentifiedImageError

        raw_image = self.rfile.read(content_length)
        try:
            with Image.open(io.BytesIO(raw_image)) as uploaded:
                if uploaded.format != "PNG":
                    raise ValueError("Profile pictures must be valid PNG images.")
                width, height = uploaded.size
                if not (64 <= width <= 1024 and 64 <= height <= 1024):
                    raise ValueError("Profile pictures must be between 64 and 1024 pixels per side.")
                uploaded.load()
                rendered = ImageOps.fit(
                    uploaded.convert("RGBA"),
                    (256, 256),
                    method=Image.Resampling.LANCZOS,
                )
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("Profile picture is not a valid PNG image.") from exc

        client = get_supabase_client()
        profile = identity.get("profile") if isinstance(identity.get("profile"), dict) else {}
        previous_url = profile.get("profile_picture")
        if "profile_picture" not in profile:
            existing = (
                client.table("users")
                .select("profile_picture")
                .eq("id", user_id)
                .limit(1)
                .execute()
                .data
            )
            previous_url = existing[0].get("profile_picture") if existing else None
        object_path = f"{secrets.token_urlsafe(24)}.png"
        encoded_image = io.BytesIO()
        rendered.save(encoded_image, format="PNG", optimize=True)
        storage = client.storage.from_(PROFILE_PICTURE_BUCKET)
        try:
            storage.upload(
                path=object_path,
                file=encoded_image.getvalue(),
                file_options={
                    "content-type": "image/png",
                    "cache-control": "31536000",
                    "upsert": "false",
                },
            )
            profile_url = storage.get_public_url(object_path)
            client.table("users").update(
                {"profile_picture": profile_url, "updated_at": _timestamp()}
            ).eq("id", user_id).execute()
        except Exception:
            try:
                storage.remove([object_path])
            except Exception:
                pass
            raise

        if isinstance(previous_url, str):
            public_prefix = f"/storage/v1/object/public/{PROFILE_PICTURE_BUCKET}/"
            old_url_path = unquote(urlsplit(previous_url).path)
            if public_prefix in old_url_path:
                old_object_path = old_url_path.split(public_prefix, 1)[1]
                if old_object_path and old_object_path != object_path:
                    try:
                        storage.remove([old_object_path])
                    except Exception as exc:
                        print(
                            f"[profile-picture] could not delete replaced object: {type(exc).__name__}",
                            file=sys.stderr,
                            flush=True,
                        )
            elif previous_url.startswith("/profile-pictures/"):
                old_name = previous_url.removeprefix("/profile-pictures/")
                legacy_root = LEGACY_PROFILE_PICTURES_DIR.resolve()
                legacy_path = (legacy_root / old_name).resolve()
                if legacy_path.is_relative_to(legacy_root):
                    legacy_path.unlink(missing_ok=True)
        self._send_json(201, {"data": {"profile_picture": profile_url}})

    def _handle_profile(self, method: str) -> None:
        try:
            identity = self._authenticated_user()
            user_id = str(identity["user_id"])
            if method == "GET":
                self._send_json(200, {"data": {"profile": identity["profile"]}})
                return
            if method == "PATCH":
                self._require_same_origin()
                profile = update_profile(user_id, self._read_json(max_bytes=32 * 1024))
                self._send_json(200, {"data": {"profile": profile}})
                return
            self._send_json(405, {"error": "Method not allowed."})
        except PermissionError as exc:
            self._send_json(401, {"error": str(exc)})
        except ProfileInputError as exc:
            self._send_json(400, {"error": str(exc)})
        except LookupError as exc:
            self._send_json(404, {"error": str(exc)})
        except Exception as exc:
            reference = uuid.uuid4().hex[:10]
            detail = str(exc).replace("\r", " ").replace("\n", " ")[:500]
            print(
                f"[profile:{reference}] request failed: {type(exc).__name__} detail={detail}",
                file=sys.stderr,
                flush=True,
            )
            message = (
                "Apply /api/users/schema in the Supabase SQL Editor, then retry."
                if any(field in detail for field in ("goals", "workout_experience", "profile_picture", "pace_gender"))
                else "Profile could not be loaded."
            )
            self._send_json(502, {"error": message, "reference": reference})

    def _handle_onboarding(self, method: str) -> None:
        try:
            if method != "PATCH":
                self._send_json(405, {"error": "Method not allowed."})
                return
            self._require_same_origin()
            identity = self._authenticated_user()
            profile = complete_onboarding(
                str(identity["user_id"]), self._read_json(max_bytes=32 * 1024)
            )
            self._send_json(200, {"data": {"profile": profile}})
        except PermissionError as exc:
            self._send_json(401, {"error": str(exc)})
        except ProfileInputError as exc:
            self._send_json(400, {"error": str(exc)})
        except LookupError as exc:
            self._send_json(404, {"error": str(exc)})
        except Exception as exc:
            reference = uuid.uuid4().hex[:10]
            detail = str(exc).replace("\r", " ").replace("\n", " ")[:500]
            print(
                f"[onboarding:{reference}] request failed: {type(exc).__name__} detail={detail}",
                file=sys.stderr,
                flush=True,
            )
            message = (
                "Apply /api/users/schema in the Supabase SQL Editor, then retry."
                if "pace_gender" in detail
                else "Onboarding could not be completed."
            )
            self._send_json(502, {"error": message, "reference": reference})
    @staticmethod
    def _public_auth_payload(result: dict[str, object], message: str) -> dict[str, object]:
        return {
            "message": message,
            "email": result.get("email"),
            "username": result.get("username"),
            "profile": result.get("profile"),
            "email_confirmation_required": result.get("email_confirmation_required", False),
        }

    def _send_auth_provider_error(self, action: str, error: AuthApiError) -> None:
        reference = uuid.uuid4().hex[:10]
        code = str(error.code or "auth_error")
        detail = str(error).replace("\r", " ").replace("\n", " ")[:500]
        print(
            f"[auth:{reference}] {action} failed: {type(error).__name__} "
            f"status={error.status} code={code} detail={detail}",
            file=sys.stderr,
            flush=True,
        )

        if code == "over_email_send_rate_limit":
            status, message = 429, "Supabase's confirmation-email rate limit was reached. Wait before retrying."
        elif code == "over_request_rate_limit":
            status, message = 429, "Supabase's authentication rate limit was reached. Wait before retrying."
        elif code == "weak_password":
            status, message = 400, "Supabase rejected this password as weak or compromised. Choose a different passphrase."
        elif code == "email_not_confirmed":
            status, message = 403, "Confirm the email address before logging in."
        elif code in {"email_provider_disabled", "signup_disabled"}:
            status, message = 503, "Email signup is disabled in the Supabase Auth settings."
        elif code == "email_address_invalid":
            status, message = 400, "Supabase rejected this email address as invalid."
        elif code == "email_address_not_authorized":
            status = 403
            message = (
                "Supabase's default email service only sends confirmation mail to project team members. "
                "Use your Supabase team email for testing or configure custom SMTP."
            )
        elif code == "captcha_failed":
            status, message = 403, "Supabase requires a valid CAPTCHA response for signup."
        elif code in {"hook_timeout", "hook_timeout_after_retry", "request_timeout"}:
            status, message = 503, "A Supabase authentication hook timed out. Check Authentication > Logs."
        elif code in {"email_exists", "user_already_exists", "conflict"} and action == "signup":
            status = 400
            message = "Account creation could not be completed. Try logging in or use a different email and username."
        elif code in {"unexpected_failure", "validation_failed"} and action == "signup":
            status = 502
            message = (
                "Supabase rejected the user-profile database trigger. Reapply /api/users/schema, "
                "then check Authentication > Logs if it persists."
            )
        elif code in {"invalid_credentials", "user_not_found"} or action == "login":
            status, message = 400, "Invalid username/email or password."
        else:
            status = error.status if 400 <= error.status < 500 else 502
            message = "Account could not be created." if action == "signup" else "Authentication failed."
        self._send_json(status, {"error": message, "code": code, "reference": reference})

    def _handle_auth(self, method: str, action: str) -> None:
        address = self.client_address[0]
        try:
            if method == "GET" and action == "config":
                self._send_json(200, {"data": public_auth_config()})
                return
            if method == "GET" and action == "me":
                cookies = self._cookies()
                access = cookies.get("peak_access")
                refresh = cookies.get("peak_refresh")
                if access:
                    try:
                        result = current_user(access.value)
                        self._send_json(200, {"data": self._public_auth_payload(result, "Session is valid.")})
                        return
                    except Exception:
                        pass
                if not refresh:
                    self._send_json(401, {"error": "Not signed in."})
                    return
                result = refresh_session(refresh.value)
                session = result.pop("session")
                self._send_json(
                    200,
                    {"data": self._public_auth_payload(result, "Session refreshed.")},
                    self._session_cookie_headers(session),
                )
                return

            if method != "POST":
                self._send_json(405, {"error": "Method not allowed."})
                return
            self._require_same_origin()
            payload = self._read_json(max_bytes=16 * 1024)
            if not isinstance(payload, dict):
                raise AuthInputError("JSON body must be an object.")

            if action == "signup":
                AUTH_ATTEMPTS.check("signup", address, limit=5, window_seconds=3600)
                result = signup(payload.get("email"), payload.get("username"), payload.get("password"))
                result.pop("session")
                message = (
                    "Account stored in Supabase. Check your email to confirm it before logging in."
                    if result["email_confirmation_required"]
                    else "Account stored in Supabase. Sign in to continue."
                )
                self._send_json(201, {"data": self._public_auth_payload(result, message)})
                return

            if action == "login":
                AUTH_ATTEMPTS.check("login", address, limit=10, window_seconds=300)
                identifier = payload.get("identifier", payload.get("email"))
                result = login(identifier, payload.get("password"))
                session = result.pop("session")
                self._send_json(
                    200,
                    {"data": self._public_auth_payload(result, "Login verified; profile retrieved from Supabase.")},
                    self._session_cookie_headers(session),
                )
                return

            if action == "session":
                AUTH_ATTEMPTS.check("passkey-session", address, limit=10, window_seconds=300)
                result = session_from_tokens(payload.get("access_token"), payload.get("refresh_token"))
                session = result.pop("session")
                self._send_json(
                    200,
                    {"data": self._public_auth_payload(result, "Passkey login verified by Supabase.")},
                    self._session_cookie_headers(session),
                )
                return

            if action == "refresh":
                refresh = self._cookies().get("peak_refresh")
                if not refresh:
                    self._send_json(401, {"error": "No refresh session is available."})
                    return
                result = refresh_session(refresh.value)
                session = result.pop("session")
                self._send_json(
                    200,
                    {"data": self._public_auth_payload(result, "Session refreshed.")},
                    self._session_cookie_headers(session),
                )
                return

            if action == "logout":
                cookies = self._cookies()
                access = cookies.get("peak_access")
                refresh = cookies.get("peak_refresh")
                try:
                    logout(access.value if access else None, refresh.value if refresh else None)
                except Exception:
                    pass
                self._send_json(
                    200,
                    {"data": {"message": "Signed out and local session cookies cleared."}},
                    self._clear_session_cookie_headers(),
                )
                return

            self._send_json(404, {"error": "Authentication endpoint not found."})
        except AuthRateLimited as exc:
            self._send_json(429, {"error": str(exc)})
        except AuthInputError as exc:
            self._send_json(400, {"error": str(exc)})
        except PermissionError as exc:
            self._send_json(403, {"error": str(exc)})
        except AuthApiError as exc:
            self._send_auth_provider_error(action, exc)
        except Exception as exc:
            reference = uuid.uuid4().hex[:10]
            detail = str(exc).replace("\r", " ").replace("\n", " ")[:500]
            print(
                f"[auth:{reference}] {action} failed: {type(exc).__name__} detail={detail}",
                file=sys.stderr,
                flush=True,
            )
            if action == "signup":
                message = "Account could not be created. Check the users schema and try again."
            elif action in {"login", "session"}:
                message = "Authentication failed. Check the credentials and project Auth settings."
            else:
                message = "Authentication service is unavailable."
            self._send_json(502, {"error": message, "reference": reference})

    def _filters(self, row_id: str | None = None) -> dict[str, object]:
        params = parse_qs(urlsplit(self.path).query)
        for key in ("select", "limit", "order", "on_conflict"):
            params.pop(key, None)
        filters: dict[str, object] = {key: values[-1] for key, values in params.items()}
        if row_id is not None:
            filters["id"] = row_id
        return filters

    def _handle_food_upload(self) -> None:
        local_addresses = {"127.0.0.1", "::1"}
        bound_address = self.server.server_address[0]
        if (
            self.client_address[0] not in local_addresses or bound_address not in local_addresses
        ) and os.environ.get("ALLOW_REMOTE_FOOD_UPLOAD") != "1":
            self._send_json(403, {"error": "Food index uploads are restricted to localhost."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0:
            self._send_json(411, {"error": "A Content-Length header and non-empty Parquet file are required."})
            return
        if content_length > MAX_FOOD_UPLOAD_BYTES:
            self._send_json(413, {"error": "The upload exceeds MAX_FOOD_UPLOAD_BYTES."})
            return

        with FOOD_REFRESH_LOCK:
            if FOOD_REFRESH_STATE["status"] in {"uploading", "running"}:
                self._send_json(409, {"error": "A food index refresh is already in progress."})
                return
            started_at = _timestamp()
            FOOD_REFRESH_STATE.update(
                status="uploading",
                phase="uploading",
                current=0,
                total=content_length,
                progress=0,
                message="Receiving source file for code/name extraction.",
                started_at=started_at,
                finished_at=None,
                error=None,
            )

        upload_path: Path | None = None
        received = 0
        job_started = False
        try:
            FOOD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            upload_path = FOOD_UPLOAD_DIR / f"{uuid.uuid4().hex}.parquet"
            with upload_path.open("wb") as handle:
                while received < content_length:
                    chunk = self.rfile.read(min(1024 * 1024, content_length - received))
                    if not chunk:
                        raise RuntimeError("The upload ended before the complete file was received.")
                    handle.write(chunk)
                    received += len(chunk)
                    with FOOD_REFRESH_LOCK:
                        FOOD_REFRESH_STATE.update(
                            current=received,
                            progress=round((received / content_length) * 100),
                            message=f"Received {received / (1024**2):,.1f} MiB.",
                        )

            params = parse_qs(urlsplit(self.path).query)
            column = params.get("column", ["product_name"])[0]
            with FOOD_REFRESH_LOCK:
                FOOD_REFRESH_STATE.update(
                    status="running",
                    phase="queued",
                    current=0,
                    total=None,
                    progress=None,
                    message="Upload complete. Queued code/name extraction.",
                )
            threading.Thread(
                target=_run_food_refresh_job,
                args=(upload_path, column, True),
                name="food-name-refresh",
                daemon=True,
            ).start()
            job_started = True
            self._send_json(202, {"data": get_food_refresh_status()})
        except Exception as exc:
            if job_started:
                return
            if upload_path is not None:
                upload_path.unlink(missing_ok=True)
            with FOOD_REFRESH_LOCK:
                FOOD_REFRESH_STATE.update(
                    status="error",
                    phase="error",
                    message=f"Food index upload failed: {exc}",
                    finished_at=_timestamp(),
                    error=str(exc),
                )
            self._send_json(400, {"error": str(exc)})

    def _handle_api(self, method: str, parts: list[str]) -> None:
        table = parts[0]
        row_id = unquote(parts[1]) if len(parts) == 2 else None
        params = parse_qs(urlsplit(self.path).query)

        try:
            if parts == ["schema"] and method == "GET":
                self._send_text(200, APP_SCHEMA_SQL)
                return
            if parts == ["supabase", "schema"] and method == "GET":
                self._send_text(200, APP_SCHEMA_SQL)
                return
            if parts == ["ai", "schema"] and method == "GET":
                self._send_text(200, AI_SESSIONS_SQL)
                return
            if len(parts) >= 2 and parts[0] == "ai":
                self._handle_ai(method, parts[1:])
                return
            if parts == ["profile"] and method in {"GET", "PATCH"}:
                self._handle_profile(method)
                return
            if parts == ["onboarding"]:
                self._handle_onboarding(method)
                return
            if parts == ["profile-picture"] and method == "POST":
                try:
                    self._handle_profile_picture()
                except PermissionError as exc:
                    self._send_json(401, {"error": str(exc)})
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                except Exception as exc:
                    reference = uuid.uuid4().hex[:10]
                    detail = str(exc).replace("\r", " ").replace("\n", " ")[:500]
                    print(
                        f"[profile-picture:{reference}] upload failed: "
                        f"{type(exc).__name__} detail={detail}",
                        file=sys.stderr,
                        flush=True,
                    )
                    message = (
                        "Apply /api/users/schema in the Supabase SQL Editor, then retry."
                        if "profile_picture" in detail
                        else "Profile picture could not be saved."
                    )
                    self._send_json(502, {"error": message, "reference": reference})
                return
            if parts == ["foods", "schema"] and method == "GET":
                self._send_text(200, FOODS_SQL)
                return
            if parts == ["foods", "search-optimization-schema"] and method == "GET":
                self._send_text(200, FOODS_SEARCH_OPTIMIZATION_SQL)
                return
            if parts == ["users", "schema"] and method == "GET":
                self._send_text(200, USERS_SQL)
                return
            if parts == ["metrics", "schema"] and method == "GET":
                self._send_text(200, BODY_METRICS_SQL)
                return
            if parts == ["metrics", "sync"] and method == "POST":
                count = sync_metrics_from_xlsx(DEFAULT_METRICS_XLSX)
                clear_search_cache("body_metrics")
                self._send_json(200, {"data": {"upserted": count, "source": DEFAULT_METRICS_XLSX.name}})
                return
            if parts == ["exercise-metrics", "schema"] and method == "GET":
                self._send_text(200, EXERCISE_METRICS_SQL)
                return
            if parts == ["exercise-metrics", "sync"] and method == "POST":
                count = sync_exercise_metrics()
                self._send_json(200, {"data": {"upserted": count}})
                return
            if len(parts) == 2 and parts[0] == "auth":
                self._handle_auth(method, parts[1])
                return
            if parts == ["foods", "refresh"] and method == "GET":
                self._send_json(200, {"data": get_food_refresh_status()})
                return
            if parts == ["food-names"] and method == "GET":
                query = params.get("q", [""])[0]
                self._send_json(200, {"data": {"query": query, "results": search_food_names(query)}})
                return
            if parts == ["food-search"] and method == "GET":
                query = params.get("q", [""])[0]
                try:
                    result = search_food(query)
                except FoodSearchRateLimited as exc:
                    self._send_json(
                        429,
                        {"error": str(exc), "retry_after": exc.retry_after, "code": "food_search_rate_limited"},
                    )
                    return
                except FoodSearchUnavailable as exc:
                    self._send_json(502, {"error": str(exc), "code": "food_search_unavailable"})
                    return
                self._send_json(200, {"data": result})
                return
            if parts == ["food-product"] and method == "GET":
                code = params.get("code", [""])[0]
                try:
                    result = get_food_product(code)
                except FoodSearchRateLimited as exc:
                    self._send_json(
                        429,
                        {"error": str(exc), "retry_after": exc.retry_after, "code": "food_product_rate_limited"},
                    )
                    return
                except FoodSearchUnavailable as exc:
                    self._send_json(502, {"error": str(exc), "code": "food_product_unavailable"})
                    return
                except FoodProductNotFound as exc:
                    self._send_json(404, {"error": str(exc), "code": "food_product_not_found"})
                    return
                self._send_json(200, {"data": result})
                return
            if parts == ["search"] and method == "GET":
                search_table = params.get("table", [""])[0]
                search_field = params.get("field", ["name"])[0]
                query = params.get("q", [""])[0]
                include_rows = params.get("details", ["false"])[0].lower() in {"1", "true", "yes"}
                limit = 1 if include_rows else 5
                results = fuzzy_search_table(
                    search_table,
                    query,
                    field=search_field,
                    limit=limit,
                    include_rows=include_rows,
                )
                self._send_json(
                    200,
                    {
                        "data": {
                            "table": search_table,
                            "field": search_field,
                            "query": query,
                            "mode": "row" if include_rows else "names",
                            "results": results,
                        }
                    },
                )
                return
            if parts == ["exercises", "schema"] and method == "GET":
                self._send_text(200, EXERCISES_SQL)
                return
            if parts == ["exercises", "refresh"] and method == "GET":
                self._send_json(200, {"data": get_refresh_status()})
                return
            if parts == ["exercises", "table"] and method == "GET":
                data = select_all_rows("exercises", ",".join(EXERCISE_COLUMNS))
                self._send_json(200, {"data": data})
                return
            if parts == ["exercises", "sync"] and method == "POST":
                records = collect_exercise_records()
                for start in range(0, len(records), 100):
                    upsert_rows("exercises", records[start : start + 100], on_conflict="url")
                    clear_search_cache("exercises")
                self._send_json(200, {"data": {"upserted": len(records)}})
                return

            if table not in GENERIC_READ_ONLY_TABLES or method != "GET":
                self._send_json(
                    403,
                    {"error": "This table is not available through the generic API."},
                )
                return
            if method == "GET":
                data = select_rows(
                    table,
                    self._filters(row_id),
                    params.get("select", ["*"])[0],
                    int(params["limit"][0]) if "limit" in params else None,
                    params.get("order", [None])[0],
                )
            elif method == "POST" and row_id is None:
                data = insert_rows(table, self._read_json())
            elif method == "PUT" and row_id is None:
                data = upsert_rows(table, self._read_json(), params.get("on_conflict", [None])[0])
            elif method == "PATCH":
                data = update_rows(table, self._filters(row_id), self._read_json())
            elif method == "DELETE":
                data = delete_rows(table, self._filters(row_id))
            else:
                self._send_json(405, {"error": "Method not allowed"})
                return
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                clear_search_cache(table)
            self._send_json(200, {"data": data})
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})

    def do_GET(self) -> None:
        request_path = urlsplit(self.path).path.rstrip("/") or "/"
        auth_paths = {"/", "/login", "/signup", "/app", "/app/login", "/app/signup"}
        protected_paths = {
            "/dashboard": "/app/dashboard",
            "/app/dashboard": "/app/dashboard",
            "/workout": "/app/workout",
            "/app/workout": "/app/workout",
            "/meals": "/app/meals",
            "/app/meals": "/app/meals",
            "/charts": "/app/charts",
            "/app/charts": "/app/charts",
            "/plan": "/app/plan",
            "/app/plan": "/app/plan",
            "/friends": "/app/friends",
            "/app/friends": "/app/friends",
            "/leagues": "/app/leagues",
            "/app/leagues": "/app/leagues",
            "/profile": "/app/profile",
            "/app/profile": "/app/profile",
            "/onboarding": "/app/onboarding",
            "/app/onboarding": "/app/onboarding",
        }
        if request_path in auth_paths:
            session_state = self._page_session()
            if session_state is not None:
                session_headers, identity = session_state
                profile = identity.get("profile") or {}
                destination = (
                    "/app/dashboard"
                    if profile.get("onboarding_completed") is True
                    else "/app/onboarding"
                )
                self._send_redirect(destination, session_headers)
                return
        elif request_path == "/logout":
            self._send_redirect("/login", self._clear_session_cookie_headers())
            return
        elif request_path in protected_paths:
            session_state = self._page_session()
            if session_state is None:
                self._send_redirect("/login")
                return
            session_headers, identity = session_state
            profile = identity.get("profile") or {}
            onboarding_complete = profile.get("onboarding_completed") is True
            is_onboarding = protected_paths[request_path] == "/app/onboarding"
            if not onboarding_complete and not is_onboarding:
                self._send_redirect("/app/onboarding", session_headers)
                return
            if onboarding_complete and is_onboarding:
                self._send_redirect("/app/dashboard", session_headers)
                return
            self._send_app_html(
                resolve_frontend_path(protected_paths[request_path]),
                session_headers,
                identity,
            )
            return
        if request_path == "/refresh":
            start_refresh_job()
            self.path = "/refresh.html"
            super().do_GET()
            return
        parts = self._api_parts()
        if parts:
            self._handle_api("GET", parts)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path.rstrip("/") in {"/refresh-food", "/api/foods/refresh"}:
            self._handle_food_upload()
            return
        parts = self._api_parts()
        if parts:
            self._handle_api("POST", parts)
            return
        self._send_json(404, {"error": "Not found"})

    def do_PUT(self) -> None:
        parts = self._api_parts()
        if parts:
            self._handle_api("PUT", parts)
            return
        self._send_json(404, {"error": "Not found"})

    def do_PATCH(self) -> None:
        parts = self._api_parts()
        if parts:
            self._handle_api("PATCH", parts)
            return
        self._send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:
        parts = self._api_parts()
        if parts:
            self._handle_api("DELETE", parts)
            return
        self._send_json(404, {"error": "Not found"})


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    APP_FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), FrontendHandler)
    print(f"Serving {FRONTEND_DIR} at http://{host}:{port}")
    print("Production login: GET /, /login, /signup, or /app (assets: /app/assets/*)")
    print("Test bench: GET /test and existing frontend test routes")
    print("API: /api/<table> and /api/<table>/<id>")
    print("Exercise sync: GET /api/exercises/schema, POST /api/exercises/sync")
    print("Exercise list refresh: GET /refresh (status: GET /api/exercises/refresh)")
    print("Exercise table viewer: GET /exercises")
    print("Fuzzy search: GET /api/search?table=<table>&field=name&q=<query>")
    print("Open Food Facts search: GET /api/food-search?q=<query>")
    print("Food code label: GET /food-code-lookup, API: GET /api/food-product?code=<code>")
    print("Food name index: GET /api/food-names?q=<query>, import UI: GET /refresh-food")
    print("Full Supabase SQL: GET /api/supabase/schema")
    print("Authentication: GET /auth-test, schema: GET /api/users/schema")
    print("AI chat: GET /ai-chat-test, schema: GET /api/ai/schema, POST /api/ai/chat")
    print("Metric catalog: GET /api/metrics/schema, POST /api/metrics/sync")
    print("Exercise metric matrix: GET /api/exercise-metrics/schema, POST /api/exercise-metrics/sync")
    print("Checks: GET /test, search demo: GET /search-page")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the frontend and refresh the exercise list.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--refresh-exercises",
        action="store_true",
        help="Refresh the local exercise list, sync it to Supabase, and exit.",
    )
    parser.add_argument(
        "--output",
        help="Optional output path for the refresh job. Defaults to frontend/exercises.json.",
    )
    parser.add_argument(
        "--refresh-foods",
        metavar="PARQUET_PATH",
        help="Extract only English names/codes into public.foods and exit.",
    )
    parser.add_argument(
        "--food-column",
        default="product_name",
        help="Source column containing multilingual product names. Defaults to product_name.",
    )
    parser.add_argument(
        "--refresh-metrics",
        nargs="?",
        const=str(DEFAULT_METRICS_XLSX),
        metavar="XLSX_PATH",
        help="Import metric definitions from XLSX and exit. Defaults to metrics.xlsx.",
    )
    parser.add_argument(
        "--refresh-exercise-metrics",
        action="store_true",
        help="Upsert the equipment tracking capability matrix and exit.",
    )
    parser.add_argument(
        "--refresh-supabase-full",
        "--refresh",
        action="store_true",
        help=(
            "Verify the manually applied full schema, sync body/exercise metric "
            "catalogs, refresh exercises, and exit."
        ),
    )
    args = parser.parse_args()

    selected_jobs = sum(
        bool(value)
        for value in (
            args.refresh_exercises,
            args.refresh_foods,
            args.refresh_metrics,
            args.refresh_exercise_metrics,
            args.refresh_supabase_full,
        )
    )
    if selected_jobs > 1:
        parser.error("Choose only one refresh operation at a time.")

    if args.refresh_supabase_full:
        try:
            validate_supabase_config()
            print("Verifying the Supabase schema from /api/supabase/schema.")
            missing_tables = []
            for table_name in EXPECTED_PUBLIC_TABLES:
                try:
                    select_rows(table_name, {}, "*", 1, None)
                except Exception as exc:
                    if getattr(exc, "code", None) in {"42P01", "PGRST205"}:
                        missing_tables.append(table_name)
                    else:
                        raise
            if missing_tables:
                raise RuntimeError(
                    "Apply the SQL from /api/supabase/schema in the Supabase SQL Editor first. "
                    f"Missing tables: {', '.join(missing_tables)}"
                )
            print(f"Verified {len(EXPECTED_PUBLIC_TABLES)} public application tables.")

            body_metric_count = sync_metrics_from_xlsx(DEFAULT_METRICS_XLSX)
            clear_search_cache("body_metrics")
            print(f"Synced {body_metric_count:,} body metric definitions.")

            exercise_metric_count = sync_exercise_metrics()
            print(f"Synced {exercise_metric_count:,} equipment capability rows.")

            saved_path = refresh_exercise_list(args.output)
            print(f"Refreshed exercises and saved the local index to {saved_path}.")
        except Exception as exc:
            print(f"Full Supabase refresh failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print("Full Supabase refresh complete.")
        return

    if args.refresh_exercise_metrics:
        try:
            count = sync_exercise_metrics()
        except Exception as exc:
            print(f"Exercise metric sync failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"Synced {count:,} exercise metric rows to Supabase.")
        return

    if args.refresh_metrics:
        try:
            count = sync_metrics_from_xlsx(args.refresh_metrics)
        except Exception as exc:
            print(f"Metric refresh failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"Synced {count:,} metric definitions to Supabase.")
        return

    if args.refresh_foods:
        try:
            count = sync_food_names_from_parquet(
                args.refresh_foods,
                column=args.food_column,
                progress_callback=lambda _phase, _current, _total, message: print(message),
            )
        except Exception as exc:
            print(f"Food index refresh failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"Synced {count:,} food names and codes to public.foods.")
        return

    if args.refresh_exercises:
        saved_path = refresh_exercise_list(args.output)
        print(f"Saved exercise list to {saved_path} and synced it to Supabase.")
        return

    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
