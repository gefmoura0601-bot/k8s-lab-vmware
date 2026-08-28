#!/usr/bin/env python3
"""Bounded, cooperative subprocess supervision for assessment collections.

Every command starts in its own process group so cancellation and timeout also
stop kubectl/aws helpers and temporary port-forwards.
"""
from __future__ import annotations

import datetime as dt
import os
import signal
import subprocess
import threading
import time
from typing import Any


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def terminate_process_tree(process: subprocess.Popen[str], grace_seconds: float = 3.0) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=max(0.0, grace_seconds))
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


class CollectionSupervisor:
    """Own one collection, its deadline and its active process tree."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._deadline = 0.0
        self._state: dict[str, Any] = {
            "active": False,
            "status": "IDLE",
            "cancelRequested": False,
        }

    def start(self, collection_id: str, max_duration_seconds: int, planned_components: list[str] | None = None) -> None:
        duration = max(60, min(int(max_duration_seconds), 7200))
        with self._lock:
            if self._state.get("active"):
                raise RuntimeError("a collection is already active")
            self._stop.clear()
            self._process = None
            self._deadline = time.monotonic() + duration
            self._state = {
                "active": True,
                "status": "RUNNING",
                "collection": collection_id,
                "component": "preparing",
                "pid": None,
                "startedAt": utc_iso(),
                "maxDurationSeconds": duration,
                "cancelRequested": False,
                "stopKind": "",
                "reason": "",
                "plannedComponents": list(planned_components or []),
                "completedComponents": [],
                "progressPercent": 0,
            }

    def _mark_stop(self, kind: str, reason: str) -> None:
        self._stop.set()
        self._state["cancelRequested"] = kind == "CANCELLED"
        self._state["stopKind"] = kind
        self._state["status"] = kind
        self._state["reason"] = reason

    def cancel(self, reason: str = "operator request") -> bool:
        with self._lock:
            if not self._state.get("active"):
                return False
            if not self._stop.is_set():
                self._mark_stop("CANCELLED", reason)
            process = self._process
        if process is not None:
            terminate_process_tree(process)
        return True

    def _stopped_result(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        with self._lock:
            kind = self._state.get("stopKind")
            reason = self._state.get("reason") or "collection stopped"
        return subprocess.CompletedProcess(args, 124 if kind == "TIMED_OUT" else 130, "", reason)

    def run(self, component: str, args: list[str], *, timeout: float, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        with self._lock:
            if not self._state.get("active"):
                raise RuntimeError("collection supervisor is not active")
            if self._stop.is_set():
                return self._stopped_result(args)
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                self._mark_stop("TIMED_OUT", "collection time budget exhausted")
                return self._stopped_result(args)
            effective_timeout = max(0.05, min(float(timeout), remaining))
            self._state["component"] = component
            self._state["componentStartedAt"] = utc_iso()

        popen_kwargs: dict[str, Any] = {
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            **kwargs,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            process = subprocess.Popen(args, **popen_kwargs)
        except OSError as error:
            return subprocess.CompletedProcess(args, 127, "", str(error))

        with self._lock:
            self._process = process
            self._state["pid"] = process.pid
            stopped = self._stop.is_set()
        if stopped:
            terminate_process_tree(process)

        stdout = ""
        stderr = ""
        try:
            stdout, stderr = process.communicate(timeout=effective_timeout)
            returncode = int(process.returncode or 0)
        except subprocess.TimeoutExpired:
            with self._lock:
                if not self._stop.is_set():
                    self._mark_stop("TIMED_OUT", f"component {component} exceeded {effective_timeout:.1f}s")
            terminate_process_tree(process)
            stdout, stderr = process.communicate()
            returncode = 124
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
                    self._state["pid"] = None
                completed = self._state.setdefault("completedComponents", [])
                if component not in completed:
                    completed.append(component)
                planned = self._state.get("plannedComponents") or []
                if planned:
                    self._state["progressPercent"] = min(99, round(len(completed) * 100 / len(planned)))

        if self._stop.is_set():
            stopped_result = self._stopped_result(args)
            returncode = stopped_result.returncode
            if not stderr:
                stderr = stopped_result.stderr
        return subprocess.CompletedProcess(args, returncode, stdout or "", stderr or "")

    def status(self) -> dict[str, Any]:
        with self._lock:
            value = dict(self._state)
            value["remainingSeconds"] = max(0, int(self._deadline - time.monotonic())) if value.get("active") else 0
            return value

    def finish(self, status: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self._state.get("active"):
                inferred = self._state.get("stopKind") or status or "COMPLETED"
                self._state.update({"status": inferred, "active": False, "component": "", "pid": None, "finishedAt": utc_iso()})
                if inferred == "COMPLETED":
                    self._state["progressPercent"] = 100
            return dict(self._state)

    def shutdown(self) -> None:
        self.cancel("dashboard shutdown")
