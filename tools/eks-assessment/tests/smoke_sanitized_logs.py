#!/usr/bin/env python3
"""Collect one explicit log target and print only redaction-safe counters."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import operational_insights as insights
import validate_assessment_artifacts as validator


def leaked(value: str) -> bool:
    key_value = any("[REDACTED" not in match.group(3).upper() for match in validator.LOG_KEY_VALUE.finditer(value))
    return bool(key_value or validator.LOG_AUTH.search(value) or validator.LOG_JWT.search(value) or validator.LOG_AWS_KEY.search(value) or validator.LOG_URL_CREDENTIALS.search(value))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--max-bytes", type=int, default=65536)
    args = parser.parse_args()
    os.environ.update({"ASSESSMENT_INCLUDE_LOGS": "1", "ASSESSMENT_LOG_TARGETS": args.target, "ASSESSMENT_LOG_MAX_BYTES": str(args.max_bytes)})
    result = insights.sanitized_logs()
    content = "\n".join(str(entry.get("content") or "") + str(entry.get("error") or "") for entry in result.get("entries") or [])
    unsafe = leaked(content)
    print(json.dumps({"state": result.get("state"), "entries": len(result.get("entries") or []), "bytes": result.get("bytes", 0), "redactionRules": len(result.get("redaction") or []), "credentialPatternDetected": unsafe}))
    return 1 if unsafe or result.get("state") != "COLLECTED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
