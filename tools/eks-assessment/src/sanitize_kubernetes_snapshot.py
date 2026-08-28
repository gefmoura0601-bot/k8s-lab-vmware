#!/usr/bin/env python3
"""Sanitize Kubernetes JSON from stdin before it reaches persistent storage."""
from __future__ import annotations

import argparse
import json
import sys

from eks_comprehensive_assessment import sanitize_events, sanitize_snapshot_tree


def main() -> int:
    parser = argparse.ArgumentParser(description="Sanitize Kubernetes assessment evidence")
    parser.add_argument("--mode", choices=("snapshot", "events"), default="snapshot")
    args = parser.parse_args()
    try:
        value = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        print(f"invalid Kubernetes JSON: {error}", file=sys.stderr)
        return 2
    value = sanitize_events(value) if args.mode == "events" else sanitize_snapshot_tree(value)
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
