"""Lifecycle catalog regression tests."""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import lifecycle_catalog


class LifecycleCatalogTests(unittest.TestCase):
    def test_upstream_maintenance_window_is_explicit(self) -> None:
        value = lifecycle_catalog.assess("v1.34.3", today=dt.date(2026, 8, 30))
        self.assertEqual(value["minorVersion"], "1.34")
        self.assertEqual(value["supportState"], "MAINTENANCE")
        self.assertEqual(value["supportUntil"], "2026-10-27")
        self.assertEqual(value["daysRemaining"], 58)

    def test_eks_extended_support_does_not_look_like_standard_support(self) -> None:
        value = lifecycle_catalog.assess("1.33", "eks", support_type="EXTENDED", today=dt.date(2026, 8, 30))
        self.assertEqual(value["supportState"], "EXTENDED_SUPPORT")
        self.assertEqual(value["supportUntil"], "2027-07-29")

    def test_stale_catalog_never_claims_supported(self) -> None:
        value = lifecycle_catalog.assess("1.36", "eks", today=dt.date(2027, 3, 1))
        self.assertTrue(value["catalogStale"])
        self.assertEqual(value["supportState"], "UNKNOWN_STALE_CATALOG")

    def test_unknown_version_remains_unknown(self) -> None:
        value = lifecycle_catalog.assess("vendor-build", "gke", today=dt.date(2026, 8, 30))
        self.assertEqual(value["supportState"], "UNKNOWN")
        self.assertEqual(value["supportUntil"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
