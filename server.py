from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_DIR = ROOT_DIR / "backend"
DEFAULT_EXERCISES_JSON = FRONTEND_DIR / "exercises.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.plainexercise_exercise_links import run as build_exercise_index  # noqa: E402


@lru_cache(maxsize=1)
def _frontend_index_exists() -> bool:
    return (FRONTEND_DIR / "index.html").exists()


def resolve_frontend_path(request_path: str) -> Path:
    parsed = urlsplit(request_path)
    route = unquote(parsed.path).lstrip("/").rstrip("/")

    if not route:
        return FRONTEND_DIR / "index.html"

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


def refresh_exercise_list(output_path: str | Path | None = None) -> Path:
    target = Path(output_path) if output_path is not None else DEFAULT_EXERCISES_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    build_exercise_index(target)
    return target


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def translate_path(self, path: str) -> str:
        return str(resolve_frontend_path(path))

    def log_message(self, format: str, *args) -> None:
        return


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), FrontendHandler)
    print(f"Serving {FRONTEND_DIR} at http://{host}:{port}")
    print("Routes: / -> frontend/index.html, /<page> -> frontend/<page>.html")
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
        help="Run the monthly exercise-list refresh once and exit.",
    )
    parser.add_argument(
        "--output",
        help="Optional output path for the refresh job. Defaults to frontend/exercises.json.",
    )
    args = parser.parse_args()

    if args.refresh_exercises:
        saved_path = refresh_exercise_list(args.output)
        print(saved_path)
        return

    run_server(args.host, args.port)


if __name__ == "__main__":
    main()