#!/usr/bin/env python3
"""Print non-sensitive rule-level evidence for finding quality failures."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in report.get("findings") or []:
        grouped[str(item.get("fingerprint") or "")].append(item)
    duplicates = [
        {"ruleIds": sorted({str(item.get("ruleId")) for item in values}), "severities": sorted({str(item.get("severity")) for item in values}), "confidences": sorted({str(item.get("confidence")) for item in values}), "count": len(values)}
        for fingerprint, values in grouped.items() if fingerprint and len(values) > 1
    ]
    low = sorted({str(item.get("ruleId")) for item in report.get("findings") or [] if item.get("severity") == "PASS" and item.get("confidence") == "LOW"})
    print(json.dumps({"duplicates": duplicates, "lowConfidencePassRuleIds": low}, ensure_ascii=False))
    return 1 if duplicates or low else 0


if __name__ == "__main__":
    raise SystemExit(main())
