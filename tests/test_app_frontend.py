from __future__ import annotations

import io
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from server import FrontendHandler, ThreadingHTTPServer


class FakeProfileQuery:
    def __init__(self, previous_picture=None) -> None:
        self.mode = "select"
        self.updated_values = None
        self.previous_picture = previous_picture

    def select(self, _fields):
        self.mode = "select"
        return self

    def update(self, values):
        self.mode = "update"
        self.updated_values = values
        return self

    def eq(self, _field, _value):
        return self

    def limit(self, _limit):
        return self

    def execute(self):
        if self.mode == "select":
            return SimpleNamespace(data=[{"profile_picture": self.previous_picture}])
        return SimpleNamespace(data=[self.updated_values])


class FakeStorageBucket:
    def __init__(self) -> None:
        self.uploaded_path = None
        self.removed_paths = []

    def upload(self, *, path, file, file_options):
        self.uploaded_path = path
        if not file or file_options["content-type"] != "image/png":
            raise AssertionError("Expected a PNG upload")
        return SimpleNamespace(path=path)

    def get_public_url(self, path):
        return f"https://project.supabase.co/storage/v1/object/public/profile-pictures/{path}"

    def remove(self, paths):
        self.removed_paths.extend(paths)
        return []


class FakeStorage:
    def __init__(self, bucket) -> None:
        self.bucket = bucket

    def from_(self, name):
        if name != "profile-pictures":
            raise AssertionError(f"Unexpected bucket: {name}")
        return self.bucket


class FakeProfileClient:
    def __init__(self, previous_picture=None) -> None:
        self.query = FakeProfileQuery(previous_picture)
        self.bucket = FakeStorageBucket()
        self.storage = FakeStorage(self.bucket)

    def table(self, name):
        if name != "users":
            raise AssertionError(f"Unexpected table: {name}")
        return self.query


class ProductionFrontendTests(unittest.TestCase):
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

    def read(self, path: str) -> tuple[str, str]:
        with urlopen(f"{self.origin}{path}", timeout=2) as response:
            return response.read().decode(), response.headers.get_content_type()

    def test_app_routes_serve_production_auth_page(self) -> None:
        for route in ("/", "/login", "/signup", "/app", "/app/login", "/app/signup"):
            html, content_type = self.read(route)
            self.assertEqual(content_type, "text/html")
            self.assertIn("Reach your", html)
            self.assertIn("Measure the", html)
            self.assertIn('/app/assets/auth.css', html)
            self.assertIn('/app/assets/auth.js', html)
            self.assertIn('id="passkey-login"', html)
            self.assertIn('id="passkey-setup"', html)
            self.assertIn('id="register-passkey"', html)
            self.assertIn('id="skip-passkey-setup"', html)
            self.assertIn('id="password-strength"', html)
            self.assertNotIn('type="tel"', html)
            self.assertNotIn('class="auth-footer"', html)
            self.assertNotIn("<style", html.lower())
            self.assertNotIn("<script>", html.lower())

    def test_app_assets_are_separate_and_wired_to_auth_api(self) -> None:
        css, css_type = self.read("/app/assets/auth.css")
        javascript, js_type = self.read("/app/assets/auth.js")
        self.assertEqual(css_type, "text/css")
        self.assertIn("@keyframes border-flow", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(".feedback[hidden]", css)
        self.assertIn(".passkey-setup[hidden]", css)
        self.assertIn(".strength[data-score", css)
        self.assertIn("/api/auth/login", javascript)
        self.assertIn("/api/auth/signup", javascript)
        self.assertIn("/api/auth/me", javascript)
        self.assertIn("/api/auth/config", javascript)
        self.assertIn("/api/auth/session", javascript)
        self.assertIn("signInWithPasskey", javascript)
        self.assertIn("signInWithPassword", javascript)
        self.assertIn("registerPasskey", javascript)
        self.assertIn("shouldOfferPasskeySetup", javascript)
        self.assertIn("updatePasswordStrength", javascript)
        self.assertIn(js_type, {"text/javascript", "application/javascript"})
        dashboard_javascript, dashboard_js_type = self.read("/app/assets/dashboard.js")
        self.assertIn("createGeometricAvatar", dashboard_javascript)
        self.assertIn("/api/profile-picture", dashboard_javascript)
        self.assertIn("/logout", dashboard_javascript)
        self.assertIn("/app/assets/app-nav.js", dashboard_javascript)
        self.assertIn(dashboard_js_type, {"text/javascript", "application/javascript"})
        app_nav_javascript, app_nav_js_type = self.read("/app/assets/app-nav.js")
        for label in ("Dashboard", "Workout", "Meals", "Charts", "Plan", "Friends", "Leagues"):
            self.assertIn(label, app_nav_javascript)
        self.assertNotIn(">AI<", app_nav_javascript)
        self.assertIn("pushState", app_nav_javascript)
        self.assertIn("preventDefault", app_nav_javascript)
        self.assertIn(app_nav_js_type, {"text/javascript", "application/javascript"})
        app_pages_javascript, app_pages_js_type = self.read("/app/assets/app-pages.js")
        self.assertIn("APP_SECTIONS", app_pages_javascript)
        self.assertIn("/app/leagues", app_pages_javascript)
        self.assertIn(app_pages_js_type, {"text/javascript", "application/javascript"})

    def test_unauthenticated_dashboard_redirects_before_serving_content(self) -> None:
        with urlopen(f"{self.origin}/app/dashboard", timeout=2) as response:
            html = response.read().decode()
            self.assertEqual(response.url, f"{self.origin}/login")
        self.assertIn("Peak account", html)

    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "tester", "onboarding_completed": True}},
    )
    def test_authenticated_login_route_redirects_before_serving_content(self, current_user_mock) -> None:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, request, file_pointer, code, message, headers, new_url):
                return None

        request = Request(
            f"{self.origin}/login",
            headers={"Cookie": "peak_access=test-token"},
        )
        with self.assertRaises(HTTPError) as caught:
            build_opener(NoRedirect).open(request, timeout=2)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(caught.exception.headers["Location"], "/app/dashboard")
        current_user_mock.assert_called_once_with("test-token")

    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "tester", "onboarding_completed": True}},
    )
    def test_authenticated_dashboard_serves_empty_shell(self, _current_user_mock) -> None:
        request = Request(
            f"{self.origin}/app/dashboard",
            headers={"Cookie": "peak_access=test-token"},
        )
        with urlopen(request, timeout=2) as response:
            html = response.read().decode()
        self.assertIn('<main class="dashboard"', html)
        self.assertIn('/app/assets/dashboard.css', html)
        self.assertIn('id="profile-button"', html)
        self.assertIn('id="logout-button"', html)
        self.assertIn('href="/logout"', html)
        self.assertIn('id="dashboard-date-time"', html)
        self.assertIn('id="date-prev"', html)
        self.assertIn('id="date-next"', html)
        self.assertIn('id="app-bottom-nav"', html)
        self.assertIn('/app/assets/dashboard.js', html)
        self.assertIn('href="/app/profile"', html)
        dashboard_javascript, _ = self.read("/app/assets/dashboard.js")
        self.assertIn("MIN_YEAR = 1871", dashboard_javascript)
        self.assertIn("MAX_YEAR = 2171", dashboard_javascript)
        self.assertIn("calendarView = \"decade\"", dashboard_javascript)
        self.assertIn("data-calendar-decade", dashboard_javascript)

    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "tester", "onboarding_completed": True}},
    )
    def test_authenticated_app_tab_pages_are_wired(self, _current_user_mock) -> None:
        for route in (
            "/app/workout",
            "/app/meals",
            "/app/charts",
            "/app/plan",
            "/app/friends",
            "/app/leagues",
            "/workout",
            "/meals",
            "/charts",
            "/plan",
            "/friends",
            "/leagues",
        ):
            request = Request(
                f"{self.origin}{route}",
                headers={"Cookie": "peak_access=test-token"},
            )
            with urlopen(request, timeout=2) as response:
                html = response.read().decode()
            self.assertIn('class="dashboard section-page"', html)
            self.assertIn('id="section-title"', html)
            self.assertIn('id="app-bottom-nav"', html)
            self.assertIn('id="logout-button"', html)
            self.assertIn('href="/logout"', html)
            self.assertIn('/app/assets/dashboard.js', html)
            self.assertNotIn('/app/assets/section.js', html)

    def test_logout_route_clears_local_cookies_and_redirects(self) -> None:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, request, file_pointer, code, message, headers, new_url):
                return None

        request = Request(
            f"{self.origin}/logout",
            headers={"Cookie": "peak_access=test-token; peak_refresh=refresh-token"},
        )
        with self.assertRaises(HTTPError) as caught:
            build_opener(NoRedirect).open(request, timeout=2)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(caught.exception.headers["Location"], "/login")
        cookies = caught.exception.headers.get_all("Set-Cookie") or []
        self.assertTrue(any("peak_access=;" in cookie and "Max-Age=0" in cookie for cookie in cookies))
        self.assertTrue(any("peak_refresh=;" in cookie and "Max-Age=0" in cookie for cookie in cookies))

    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "tester", "onboarding_completed": True}},
    )
    def test_authenticated_profile_page_contains_every_editable_field(self, _current_user_mock) -> None:
        request = Request(
            f"{self.origin}/app/profile",
            headers={"Cookie": "peak_access=test-token"},
        )
        with urlopen(request, timeout=2) as response:
            html = response.read().decode()
        for field in (
            "username",
            "display_name",
            "bio",
            "workout_experience",
            "preferred_units",
            "pace_gender",
            "goals",
            "cuisine_preferences",
            "dietary_preferences",
        ):
            self.assertIn(f'name="{field}"', html)
        self.assertNotIn('name="onboarding_completed"', html)
        self.assertNotIn("<select", html)
        self.assertIn('data-custom-select="workout_experience"', html)
        self.assertIn('data-custom-select="preferred_units"', html)
        self.assertIn('data-custom-select="pace_gender"', html)
        self.assertIn('/app/assets/profile.js', html)

    @patch(
        "server.current_user",
        return_value={"user_id": "internal-user-id", "profile": {"username": "new_user", "onboarding_completed": False}},
    )
    def test_incomplete_user_is_routed_to_onboarding(self, _current_user_mock) -> None:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, request, file_pointer, code, message, headers, new_url):
                return None

        request = Request(
            f"{self.origin}/app/dashboard",
            headers={"Cookie": "peak_access=test-token"},
        )
        with self.assertRaises(HTTPError) as caught:
            build_opener(NoRedirect).open(request, timeout=2)
        self.assertEqual(caught.exception.headers["Location"], "/app/onboarding")

        request = Request(
            f"{self.origin}/app/onboarding",
            headers={"Cookie": "peak_access=test-token"},
        )
        with urlopen(request, timeout=2) as response:
            html = response.read().decode()
        self.assertIn("First, choose your Pace", html)
        self.assertIn('/app/assets/onboarding.css', html)
        self.assertIn('/app/assets/onboarding.js', html)
        self.assertIn('data-tracking-answer="deny"', html)
        self.assertNotIn('class="privacy-promise"', html)
        self.assertIn('id="ai-prompt"', html)
        self.assertIn('data-label="Friends"', html)
        self.assertIn('data-label="Leagues"', html)
        self.assertIn('data-step="8" data-label="Privacy"', html)
        self.assertEqual(html.count('data-step="'), 9)

        javascript, _ = self.read("/app/assets/onboarding.js")
        css, _ = self.read("/app/assets/onboarding.css")
        self.assertIn("continueToPrivacyPage", javascript)
        self.assertIn("aiPrompts", javascript)
        self.assertIn("aiStepIndex", javascript)
        self.assertIn("prefers-reduced-motion", javascript)
        self.assertIn("background: transparent", css)
        self.assertIn("@keyframes mascot-breathe", css)
        self.assertIn("@keyframes caret-blink", css)

    def test_pace_asset_route_serves_only_png_files(self) -> None:
        with urlopen(f"{self.origin}/app/pace/pace_female_hi.png", timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertGreater(len(response.read()), 1000)
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.origin}/app/pace/%2e%2e/.env", timeout=2)
        self.assertEqual(caught.exception.code, 404)

    @patch(
        "server.current_user",
        return_value={"user_id": "private-user-uuid", "profile": {"username": "tester"}},
    )
    def test_profile_picture_upload_replaces_supabase_storage_object(self, _current_user_mock) -> None:
        from PIL import Image

        image_bytes = io.BytesIO()
        Image.new("RGBA", (256, 256), "#7779ff").save(image_bytes, format="PNG")
        previous_url = (
            "https://project.supabase.co/storage/v1/object/public/"
            "profile-pictures/previous-picture.png"
        )
        fake_client = FakeProfileClient(previous_url)
        with patch("server.get_supabase_client", return_value=fake_client):
            request = Request(
                f"{self.origin}/api/profile-picture",
                data=image_bytes.getvalue(),
                method="POST",
                headers={
                    "Content-Type": "image/png",
                    "Cookie": "peak_access=test-token",
                    "Origin": self.origin,
                },
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read())
            picture_url = payload["data"]["profile_picture"]
            self.assertIn("/storage/v1/object/public/profile-pictures/", picture_url)
            self.assertNotIn("private-user-uuid", picture_url)
            self.assertEqual(fake_client.query.updated_values["profile_picture"], picture_url)
            self.assertEqual(fake_client.bucket.removed_paths, ["previous-picture.png"])

    def test_logo_is_available_only_through_explicit_app_asset_route(self) -> None:
        with urlopen(f"{self.origin}/app/assets/logo.png", timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertGreater(len(response.read()), 1000)

    def test_app_asset_route_blocks_parent_traversal(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.origin}/app/assets/%2e%2e/%2e%2e/.env", timeout=2)
        self.assertEqual(caught.exception.code, 404)

    def test_unknown_page_uses_themed_404_with_real_404_status(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.origin}/this-route-does-not-exist", timeout=2)
        self.assertEqual(caught.exception.code, 404)
        html = caught.exception.read().decode()
        self.assertIn("You took a", html)
        self.assertIn('/app/assets/error.css', html)

    def test_server_error_uses_themed_500_with_real_500_status(self) -> None:
        class Error500Handler(FrontendHandler):
            def do_GET(self):
                self.send_error(500)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Error500Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as caught:
                urlopen(f"http://127.0.0.1:{server.server_port}/boom", timeout=2)
            self.assertEqual(caught.exception.code, 500)
            html = caught.exception.read().decode()
            self.assertIn("System", html)
            self.assertIn("recalibrating", html)
            self.assertIn('/app/assets/server-error.css', html)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        css, content_type = self.read("/app/assets/server-error.css")
        self.assertEqual(content_type, "text/css")
        self.assertIn(".diagnostic-shell", css)


if __name__ == "__main__":
    unittest.main()
