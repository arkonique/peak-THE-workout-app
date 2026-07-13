from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from supabase_auth.errors import AuthApiError

from backend.auth import (
    AuthInputError,
    SessionTokens,
    normalize_email,
    normalize_password,
    normalize_username,
    resolve_login_email,
)
from backend.schema import USERS_SQL
from server import FrontendHandler, ThreadingHTTPServer


class AuthValidationTests(unittest.TestCase):
    def test_normalizes_email_and_username(self) -> None:
        self.assertEqual(normalize_email("Test@Example.COM"), "Test@example.com")
        self.assertEqual(normalize_username("  Test_User  "), "test_user")

    def test_password_policy_accepts_long_passphrases_and_spaces(self) -> None:
        password = "a long passphrase with spaces"
        self.assertEqual(normalize_password(password), password)

    def test_password_policy_rejects_short_and_contextual_passwords(self) -> None:
        with self.assertRaises(AuthInputError):
            normalize_password("too short")
        with self.assertRaises(AuthInputError):
            normalize_password("workout_fan_2026", username="workout_fan_2026")

    def test_users_schema_never_stores_passwords(self) -> None:
        self.assertNotIn("password", USERS_SQL.lower())
        self.assertIn("references auth.users", USERS_SQL.lower())
        self.assertIn("enable row level security", USERS_SQL.lower())

    def test_users_schema_has_typed_profile_fields(self) -> None:
        normalized = " ".join(USERS_SQL.lower().split())
        self.assertIn("profile_picture text", normalized)
        self.assertIn("pace_gender text", normalized)
        self.assertIn("goals text[]", normalized)
        self.assertIn("workout_experience text", normalized)
        self.assertIn("cuisine_preferences text[]", normalized)
        self.assertIn("dietary_preferences text[]", normalized)
        self.assertIn("preferred_units text", normalized)
        self.assertIn("onboarding_completed boolean", normalized)

    @patch("backend.auth.get_supabase_client")
    def test_username_is_resolved_to_auth_email_server_side(self, get_client) -> None:
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "internal-uuid"}
        ]
        client.auth.admin.get_user_by_id.return_value = SimpleNamespace(
            user=SimpleNamespace(email="Person@Example.com")
        )
        get_client.return_value = client

        self.assertEqual(resolve_login_email(" Person "), "Person@example.com")
        client.auth.admin.get_user_by_id.assert_called_once_with("internal-uuid")

    @patch("backend.auth.get_supabase_client")
    def test_email_login_does_not_query_profile_table(self, get_client) -> None:
        self.assertEqual(resolve_login_email("Person@Example.com"), "Person@example.com")
        get_client.assert_not_called()


class AuthHttpTests(unittest.TestCase):
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

    def test_auth_page_and_schema_are_served(self) -> None:
        with urlopen(f"{self.origin}/auth-test", timeout=2) as response:
            self.assertIn(b"Supabase Auth test console", response.read())
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        with urlopen(f"{self.origin}/api/users/schema", timeout=2) as response:
            self.assertIn(b"create table if not exists public.users", response.read())

    def test_invalid_signup_is_rejected_before_contacting_supabase(self) -> None:
        body = json.dumps({"email": "not-an-email", "username": "valid_user", "password": "a" * 20}).encode()
        request = Request(
            f"{self.origin}/api/auth/signup",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.origin},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("valid email", caught.exception.read().decode())

    def test_generic_users_api_is_blocked(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.origin}/api/users", timeout=2)
        self.assertEqual(caught.exception.code, 403)

    @patch("server.login")
    def test_login_sets_http_only_cookies_without_returning_tokens(self, login_mock) -> None:
        login_mock.return_value = {
            "email": "person@example.com",
            "profile": {"username": "person"},
            "session": SessionTokens("access-secret", "refresh-secret", 3600),
        }
        body = json.dumps({"identifier": "person", "password": "a safe passphrase 123"}).encode()
        request = Request(
            f"{self.origin}/api/auth/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.origin},
        )
        with urlopen(request, timeout=2) as response:
            payload = response.read().decode()
            cookies = response.headers.get_all("Set-Cookie")
        self.assertEqual(len(cookies), 2)
        self.assertTrue(all("HttpOnly" in cookie for cookie in cookies))
        self.assertNotIn("access-secret", payload)
        self.assertNotIn("refresh-secret", payload)
        self.assertNotIn("user_id", payload)
        login_mock.assert_called_once_with("person", "a safe passphrase 123")

    def test_cross_origin_login_is_rejected(self) -> None:
        body = json.dumps({"email": "person@example.com", "password": "a safe passphrase 123"}).encode()
        request = Request(
            f"{self.origin}/api/auth/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "https://attacker.example"},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 403)

    @patch("server.signup")
    def test_signup_trigger_error_is_actionable_without_leaking_details(self, signup_mock) -> None:
        signup_mock.side_effect = AuthApiError(
            "Database error saving new user: internal table detail",
            500,
            "unexpected_failure",
        )
        body = json.dumps(
            {"email": "person@example.com", "username": "person", "password": "a safe passphrase 123"}
        ).encode()
        request = Request(
            f"{self.origin}/api/auth/signup",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.origin},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        payload = json.loads(caught.exception.read())
        self.assertEqual(caught.exception.code, 502)
        self.assertEqual(payload["code"], "unexpected_failure")
        self.assertIn("Reapply /api/users/schema", payload["error"])
        self.assertNotIn("internal table detail", payload["error"])

    @patch("server.signup")
    def test_signup_never_creates_a_browser_session(self, signup_mock) -> None:
        signup_mock.return_value = {
            "email": "person@example.com",
            "username": "person",
            "email_confirmation_required": True,
            "session": SessionTokens("signup-access", "signup-refresh", 3600),
        }
        request = Request(
            f"{self.origin}/api/auth/signup",
            data=json.dumps(
                {
                    "email": "person@example.com",
                    "username": "person",
                    "password": "a safe passphrase 123",
                }
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.origin},
        )
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
            cookies = response.headers.get_all("Set-Cookie") or []
        self.assertEqual(cookies, [])
        self.assertTrue(payload["data"]["email_confirmation_required"])
        self.assertIn("Check your email", payload["data"]["message"])

    @patch("server.signup")
    def test_unauthorized_email_explains_default_smtp_restriction(self, signup_mock) -> None:
        signup_mock.side_effect = AuthApiError(
            "Email address not authorized",
            400,
            "email_address_not_authorized",
        )
        body = json.dumps(
            {"email": "person@example.com", "username": "person", "password": "a safe passphrase 123"}
        ).encode()
        request = Request(
            f"{self.origin}/api/auth/signup",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Origin": self.origin},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        payload = json.loads(caught.exception.read())
        self.assertEqual(caught.exception.code, 403)
        self.assertIn("project team members", payload["error"])


if __name__ == "__main__":
    unittest.main()
