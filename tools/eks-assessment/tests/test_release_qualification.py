#!/usr/bin/env python3
"""Release qualification must never imply unverified cloud support."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseQualificationTests(unittest.TestCase):
    def test_version_and_provider_qualification_are_explicit(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        document = json.loads(
            (ROOT / "data" / "release-qualification.json").read_text(encoding="utf-8")
        )

        self.assertEqual("1.0", document["schemaVersion"])
        self.assertEqual(version, document["toolVersion"])
        self.assertEqual("STABLE", document["profiles"]["generic-kubernetes"]["status"])
        for provider in ("eks", "aks", "gke"):
            with self.subTest(provider=provider):
                profile = document["profiles"][provider]
                self.assertEqual("PREVIEW", profile["status"])
                self.assertIn("real", profile["limitations"])


if __name__ == "__main__":
    unittest.main()
