from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.ai_chat import _parse_response, get_interaction_history
from backend.full_schema import load_ai_schema_sql, load_full_schema_sql
from server import FrontendHandler, ThreadingHTTPServer


class AiSchemaTests(unittest.TestCase):
    def test_schema_is_additive_user_owned_and_in_full_schema(self) -> None:
        sql = load_ai_schema_sql()
        normalized = " ".join(sql.lower().split())
        self.assertNotIn("drop table", normalized)
        self.assertIn("create table if not exists public.ai_sessions", normalized)
        self.assertNotIn("create table if not exists public.ai_messages", normalized)
        self.assertNotIn("structured_data", normalized)
        self.assertIn("enable row level security", normalized)
        self.assertIn("auth.uid()) = user_id", normalized)
        self.assertIn(sql, load_full_schema_sql())

    def test_structured_response_parser_handles_wrapped_json(self) -> None:
        parsed = _parse_response('Result: {"workout":null,"meals":null}')
        self.assertIsNone(parsed.workout)
        self.assertIsNone(parsed.meals)

    @patch("backend.ai_chat._client")
    def test_history_comes_from_google_interaction_steps(self, client_mock) -> None:
        client_mock.return_value.interactions.get.return_value = SimpleNamespace(
            steps=[
                SimpleNamespace(type="user_input", content="Build a leg day"),
                SimpleNamespace(
                    type="model_output",
                    content='{"workout":null,"meals":null}',
                ),
            ]
        )
        messages = get_interaction_history("provider-interaction")
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "Build a leg day")
        self.assertIn("model_response", messages[1]["structured_data"])
        client_mock.return_value.interactions.get.assert_called_once_with(
            id="provider-interaction",
            include_input=True,
        )


class AiRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FrontendHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_ai_schema_endpoint_is_public(self) -> None:
        with urlopen(f"{self.origin}/api/ai/schema", timeout=2) as response:
            body = response.read().decode()
        self.assertIn("public.ai_sessions", body)

    @patch("server.list_ai_sessions", return_value=[{"id": "session", "title": "Test"}])
    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "tester"}},
    )
    def test_session_list_uses_authenticated_internal_user(self, current_user_mock, list_mock) -> None:
        request = Request(
            f"{self.origin}/api/ai/sessions",
            headers={"Cookie": "peak_access=test-token"},
        )
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["data"]["sessions"][0]["title"], "Test")
        current_user_mock.assert_called_once_with("test-token")
        list_mock.assert_called_once_with("internal-user-id")
        self.assertNotIn("internal-user-id", json.dumps(payload))

    @patch("server.send_ai_message", return_value={"session": {"id": "chat-session"}, "messages": []})
    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "tester"}},
    )
    def test_chat_post_passes_session_username_and_prompt(self, _current_user_mock, send_mock) -> None:
        body = json.dumps({"session_id": "-1", "username": "tester", "prompt": "Leg day"}).encode()
        request = Request(
            f"{self.origin}/api/ai/chat",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": "peak_access=test-token",
                "Origin": self.origin,
            },
        )
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["data"]["session"]["id"], "chat-session")
        send_mock.assert_called_once_with("internal-user-id", "Leg day", "-1")

    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "tester"}},
    )
    def test_chat_rejects_username_that_does_not_match_login(self, _current_user_mock) -> None:
        request = Request(
            f"{self.origin}/api/ai/chat",
            data=json.dumps({"username": "someone_else", "prompt": "test"}).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": "peak_access=test-token",
                "Origin": self.origin,
            },
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
