#!/usr/bin/env python3
"""Generate sanitized generic collections for headless CI gate execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_provider_validation import ProviderValidationTests  # noqa: E402


def complete_regression_evidence(collection: Path) -> None:
    path = collection / "operational-insights.json"
    operational = json.loads(path.read_text(encoding="utf-8"))
    operational["nodeHealth"] = {
        "state": "PASS",
        "summary": {
            "nodes": 1,
            "critical": 0,
            "warnings": 0,
            "partial": 0,
            "passed": 1,
            "metricsNodes": 1,
            "metricsCoveragePercent": 100.0,
        },
        "items": [{
            "node": "node-sanitized",
            "state": "PASS",
            "ready": True,
            "evidence": {"metrics": "MetricsAPI"},
        }],
    }
    operational["versions"] = {
        "summary": {
            "components": 1,
            "nodeVersionSkew": False,
            "endOfSupport": 0,
            "lifecycleUnknown": 0,
        }
    }
    operational["manifestQuality"] = {
        "summary": {"resources": 1, "issues": 0, "critical": 0, "warnings": 0},
        "findings": [],
    }
    path.write_text(
        json.dumps(operational, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    factory = ProviderValidationTests(methodName="runTest")
    before = factory.collection(
        destination / "eks-ci-before", "generic-kubernetes"
    )
    after = factory.collection(
        destination / "eks-ci-after", "generic-kubernetes"
    )
    complete_regression_evidence(before)
    complete_regression_evidence(after)
    print(json.dumps({"before": before.name, "after": after.name}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
