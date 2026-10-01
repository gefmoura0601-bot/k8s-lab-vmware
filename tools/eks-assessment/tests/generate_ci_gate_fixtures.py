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
from blue_green_readiness import generate as generate_blue_green  # noqa: E402


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
    operational["manifestSchema"] = {"state": "PASS", "summary": {"resources": 0, "failed": 0}}
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
    manual = {
        "schemaVersion": "1.0", "generatedAt": "2026-09-29T00:00:00Z", "readOnly": True,
        "changeReference": "CI-SANITIZED", "approvalReference": "CI-SANITIZED",
        "dnsValidated": True, "loadBalancerValidated": True, "rollbackAvailable": True,
        "restoreTested": True, "schemaBackwardCompatible": True, "singletonJobsControlled": True,
        "dataReplication": {"state": "READY", "detail": "sanitized CI fixture"},
    }
    for collection in (before, after):
        (collection / "migration-evidence.json").write_text(json.dumps(manual, ensure_ascii=False, indent=2), encoding="utf-8")
        generate_blue_green(collection)
    probes = json.loads((after / "migration-probes.json").read_text(encoding="utf-8"))
    probes.update({
        "state": "PASS",
        "summary": {"probes": 1, "passed": 1, "warnings": 0, "failed": 0, "maxLatencyMs": 2000},
        "items": [{"url": "https://green.example.test/health", "state": "PASS", "httpStatus": 200, "latencyMs": 20, "tlsValidated": True}],
    })
    (after / "migration-probes.json").write_text(json.dumps(probes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"before": before.name, "after": after.name}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
