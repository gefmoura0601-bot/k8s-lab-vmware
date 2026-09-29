#!/usr/bin/env python3
"""Collector registry, dependency planning and durable weighted progress."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"PASS", "FAIL", "SKIPPED", "CANCELLED", "TIMED_OUT"}


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_registry(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    collectors = value.get("collectors") if isinstance(value, dict) else None
    if not isinstance(collectors, list) or not collectors:
        raise ValueError("collector registry is empty")
    seen: set[str] = set()
    for item in collectors:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("collector registry entry is invalid")
        if item["id"] in seen:
            raise ValueError(f"duplicate collector: {item['id']}")
        seen.add(item["id"])
        if int(item.get("weight", 0)) <= 0 or int(item.get("timeoutSeconds", 0)) <= 0:
            raise ValueError(f"invalid weight or timeout: {item['id']}")
    for item in collectors:
        unknown = set(item.get("dependencies") or []) - seen
        if unknown:
            raise ValueError(f"unknown dependencies for {item['id']}: {sorted(unknown)}")
    return collectors


def split_ids(values: list[str]) -> set[str]:
    return {part.strip() for value in values for part in value.split(",") if part.strip()}


def build_plan(
    registry: list[dict[str, Any]],
    *,
    channel: str,
    prometheus: bool,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    include = set(include or [])
    exclude = set(exclude or [])
    entries = {item["id"]: item for item in registry}
    unknown = (include | exclude) - set(entries)
    if unknown:
        raise ValueError(f"unknown collectors: {', '.join(sorted(unknown))}")
    channel_ids = {
        item["id"] for item in registry
        if channel in (item.get("channels") or [])
        and (item.get("conditional") != "prometheus" or prometheus)
    }
    unavailable = include - channel_ids
    if unavailable:
        raise ValueError(f"collectors unavailable for this execution: {', '.join(sorted(unavailable))}")
    required = {item["id"] for item in registry if item.get("required") and item["id"] in channel_ids}
    forbidden = required & exclude
    if forbidden:
        raise ValueError(f"required collectors cannot be excluded: {', '.join(sorted(forbidden))}")
    selected = (channel_ids if not include else required | (include & channel_ids)) - exclude

    def add_dependencies(collector_id: str, trail: tuple[str, ...] = ()) -> None:
        if collector_id in trail:
            raise ValueError(f"collector dependency cycle: {' -> '.join((*trail, collector_id))}")
        for dependency in entries[collector_id].get("dependencies") or []:
            if dependency not in channel_ids:
                raise ValueError(f"dependency {dependency} for {collector_id} is unavailable in channel {channel}")
            if dependency in exclude:
                raise ValueError(f"collector {collector_id} depends on excluded collector {dependency}")
            if dependency not in selected:
                selected.add(dependency)
                add_dependencies(dependency, (*trail, collector_id))

    for collector_id in tuple(selected):
        add_dependencies(collector_id)
    return [
        {
            "id": item["id"],
            "label": item.get("label") or item["id"],
            "weight": int(item["weight"]),
            "timeoutSeconds": int(item["timeoutSeconds"]),
            "required": bool(item.get("required")),
            "dependencies": list(item.get("dependencies") or []),
        }
        for item in registry if item["id"] in selected
    ]


def calculate_progress(state: dict[str, Any]) -> int:
    plan = state.get("plan") or []
    total = sum(int(item.get("weight", 0)) for item in plan)
    if total <= 0:
        return 0
    collectors = state.get("collectors") or {}
    completed = sum(
        int(item.get("weight", 0))
        for item in plan
        if str((collectors.get(item.get("id")) or {}).get("state")) in TERMINAL_STATES
    )
    return min(100, round(completed * 100 / total))


def init_state(
    collection: Path,
    plan: list[dict[str, Any]],
    *,
    resume: bool,
    retry_failed: bool,
) -> dict[str, Any]:
    path = collection / "collector-state.json"
    previous: dict[str, Any] = {}
    if resume and path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        previous = loaded if isinstance(loaded, dict) else {}
    old_collectors = previous.get("collectors") or {}
    collectors: dict[str, Any] = {}
    for item in plan:
        old = old_collectors.get(item["id"])
        if isinstance(old, dict):
            collectors[item["id"]] = dict(old)
        else:
            collectors[item["id"]] = {"state": "PENDING", "attempts": 0}
        current = str(collectors[item["id"]].get("state") or "PENDING")
        if item["id"] == "preflight" or current in {"RUNNING", "CANCELLED", "TIMED_OUT"} or (retry_failed and current == "FAIL"):
            collectors[item["id"]]["state"] = "PENDING"
    state = {
        "schemaVersion": "1.0",
        "collection": collection.name,
        "createdAt": previous.get("createdAt") or utc_iso(),
        "updatedAt": utc_iso(),
        "resumedAt": utc_iso() if resume else None,
        "plan": plan,
        "collectors": collectors,
        "progressPercent": 0,
    }
    state["progressPercent"] = calculate_progress(state)
    atomic_write(path, state)
    return state


def update_state(collection: Path, collector: str, state_name: str, exit_code: int | None, detail: str) -> dict[str, Any]:
    path = collection / "collector-state.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    planned = {item.get("id") for item in value.get("plan") or []}
    if collector not in planned:
        raise ValueError(f"collector is not in plan: {collector}")
    current = dict((value.get("collectors") or {}).get(collector) or {})
    if state_name == "RUNNING":
        current["startedAt"] = utc_iso()
        current["attempts"] = int(current.get("attempts", 0)) + 1
        current.pop("finishedAt", None)
    elif state_name in TERMINAL_STATES:
        current["finishedAt"] = utc_iso()
    current["state"] = state_name
    if exit_code is not None:
        current["exitCode"] = exit_code
    if detail:
        current["detail"] = detail[:500]
    value.setdefault("collectors", {})[collector] = current
    value["updatedAt"] = utc_iso()
    value["progressPercent"] = calculate_progress(value)
    atomic_write(path, value)
    return value


def should_run(collection: Path, collector: str, retry_failed: bool) -> bool:
    value = json.loads((collection / "collector-state.json").read_text(encoding="utf-8"))
    state_name = str(((value.get("collectors") or {}).get(collector) or {}).get("state") or "PENDING")
    if state_name == "PASS":
        return False
    if state_name == "FAIL" and not retry_failed:
        return False
    if state_name == "SKIPPED":
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Planeja coletores e persiste progresso ponderado")
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "collectors.json")
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--channel", choices=("cli", "web"), required=True)
    plan.add_argument("--prometheus", choices=("enabled", "disabled"), default="disabled")
    plan.add_argument("--include", action="append", default=[])
    plan.add_argument("--exclude", action="append", default=[])
    init = sub.add_parser("init")
    init.add_argument("--collection", required=True, type=Path)
    init.add_argument("--channel", choices=("cli", "web"), required=True)
    init.add_argument("--prometheus", choices=("enabled", "disabled"), default="disabled")
    init.add_argument("--include", action="append", default=[])
    init.add_argument("--exclude", action="append", default=[])
    init.add_argument("--resume", action="store_true")
    init.add_argument("--retry-failed", action="store_true")
    mark = sub.add_parser("mark")
    mark.add_argument("--collection", required=True, type=Path)
    mark.add_argument("--collector", required=True)
    mark.add_argument("--state", required=True, choices=("PENDING", "RUNNING", *sorted(TERMINAL_STATES)))
    mark.add_argument("--exit-code", type=int)
    mark.add_argument("--detail", default="")
    run = sub.add_parser("should-run")
    run.add_argument("--collection", required=True, type=Path)
    run.add_argument("--collector", required=True)
    run.add_argument("--retry-failed", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--collection", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        registry = load_registry(args.registry)
        if args.action in {"plan", "init"}:
            plan = build_plan(
                registry,
                channel=args.channel,
                prometheus=args.prometheus == "enabled",
                include=split_ids(args.include),
                exclude=split_ids(args.exclude),
            )
            if args.action == "plan":
                result: Any = {"ok": True, "plan": plan, "weight": sum(item["weight"] for item in plan)}
            else:
                args.collection.mkdir(parents=True, exist_ok=True)
                result = init_state(args.collection, plan, resume=args.resume, retry_failed=args.retry_failed)
        elif args.action == "mark":
            result = update_state(args.collection, args.collector, args.state, args.exit_code, args.detail)
        elif args.action == "should-run":
            return 0 if should_run(args.collection, args.collector, args.retry_failed) else 10
        else:
            result = json.loads((args.collection / "collector-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
