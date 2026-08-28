#!/usr/bin/env python3
"""Authentication tests for the remotely exposed assessment dashboard."""
from __future__ import annotations

import http.cookiejar
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "src" / "assessment_dashboard.py"
STATIC = ROOT / "web" / "public"


class DashboardAccessTests(unittest.TestCase):
    def test_remote_binding_requires_access_token(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DASHBOARD), "--root", str(ROOT / "tests"), "--static", str(STATIC),
             "--host", "0.0.0.0", "--port", "8765", "--allow-remote"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires an access token", result.stderr)

    def test_token_is_exchanged_for_http_only_session_cookie(self) -> None:
        token = "a" * 43
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [sys.executable, str(DASHBOARD), "--root", directory, "--static", str(STATIC),
                 "--host", "127.0.0.1", "--port", str(port), "--access-token", token],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(40):
                    try:
                        urllib.request.urlopen(base, timeout=0.25)
                    except urllib.error.HTTPError as error:
                        if error.code == 401:
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail("dashboard did not start")

                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(f"{base}/api/health", timeout=2)
                self.assertEqual(denied.exception.code, 401)

                jar = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
                response = opener.open(f"{base}/?access_token={token}", timeout=2)
                self.assertEqual(response.status, 200)
                session = next(cookie for cookie in jar if cookie.name == "assessment_session")
                self.assertTrue(session.has_nonstandard_attr("HttpOnly"))
                self.assertEqual(opener.open(f"{base}/api/health", timeout=2).status, 200)
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
