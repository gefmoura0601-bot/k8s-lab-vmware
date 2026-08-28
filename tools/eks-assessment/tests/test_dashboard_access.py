#!/usr/bin/env python3
"""Authentication tests for the remotely exposed assessment dashboard."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "src" / "assessment_dashboard.py"
STATIC = ROOT / "web" / "public"
sys.path.insert(0, str(ROOT / "src"))

import assessment_dashboard as dashboard


class DashboardAccessTests(unittest.TestCase):
    def test_remote_binding_requires_access_token(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DASHBOARD), "--root", str(ROOT / "tests"), "--static", str(STATIC),
             "--host", "0.0.0.0", "--port", "8765", "--allow-remote"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires an access token", result.stderr)

    @staticmethod
    def handler(path: str, cookie: str = ""):
        instance = object.__new__(dashboard.Handler)
        instance.path = path
        instance.headers = {"Cookie": cookie}
        instance.access_token = "a" * 43
        instance.response_status = None
        instance.response_headers = []
        instance.send_response = lambda status: setattr(instance, "response_status", status)
        instance.send_header = lambda name, value: instance.response_headers.append((name, value))
        instance.end_headers = lambda: None
        instance.send_html = lambda _value, status=200: setattr(instance, "response_status", status)
        return instance

    def test_token_is_exchanged_for_http_only_session_cookie(self) -> None:
        token = "a" * 43
        login = self.handler(f"/?access_token={token}")
        self.assertFalse(login.authenticated())
        self.assertEqual(login.response_status, 303)
        self.assertIn(("Location", "/"), login.response_headers)
        cookie = next(value for name, value in login.response_headers if name == "Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

        session = self.handler("/api/health", f"assessment_session={token}")
        self.assertTrue(session.authenticated())

        denied = self.handler("/api/health")
        self.assertFalse(denied.authenticated())
        self.assertEqual(denied.response_status, 401)


if __name__ == "__main__":
    unittest.main()
