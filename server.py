from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_DIR = ROOT_DIR / "backend"
DEFAULT_EXERCISES_JSON = FRONTEND_DIR / "exercises.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.sync_exercises import collect_exercise_records, run as build_exercise_index  # noqa: E402
from backend.db import delete_rows, insert_rows, select_rows, update_rows, upsert_rows  # noqa: E402
from backend.schema import EXERCISES_SQL  # noqa: E402


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

    def _api_parts(self) -> list[str] | None:
        parts = urlsplit(self.path).path.strip("/").split("/")
        if len(parts) in {2, 3} and parts[0] == "api":
            return parts[1:]
        return None

    def _read_json(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _filters(self, row_id: str | None = None) -> dict[str, object]:
        params = parse_qs(urlsplit(self.path).query)
        for key in ("select", "limit", "order", "on_conflict"):
            params.pop(key, None)
        filters: dict[str, object] = {key: values[-1] for key, values in params.items()}
        if row_id is not None:
            filters["id"] = row_id
        return filters

    def _handle_api(self, method: str, parts: list[str]) -> None:
        table = parts[0]
        row_id = unquote(parts[1]) if len(parts) == 2 else None
        params = parse_qs(urlsplit(self.path).query)

        try:
            if parts == ["exercises", "schema"] and method == "GET":
                self._send_text(200, EXERCISES_SQL)
                return
            if parts == ["exercises", "sync"] and method == "POST":
                records = collect_exercise_records()
                for start in range(0, len(records), 100):
                    upsert_rows("exercises", records[start : start + 100], on_conflict="url")
                self._send_json(200, {"data": {"upserted": len(records)}})
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
            self._send_json(200, {"data": data})
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})

    def do_GET(self) -> None:
        parts = self._api_parts()
        if parts:
            self._handle_api("GET", parts)
            return
        super().do_GET()

    def do_POST(self) -> None:
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
    server = ThreadingHTTPServer((host, port), FrontendHandler)
    print(f"Serving {FRONTEND_DIR} at http://{host}:{port}")
    print("Routes: / -> frontend/index.html, /<page> -> frontend/<page>.html")
    print("API: /api/<table> and /api/<table>/<id>")
    print("Exercise sync: GET /api/exercises/schema, POST /api/exercises/sync")
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
