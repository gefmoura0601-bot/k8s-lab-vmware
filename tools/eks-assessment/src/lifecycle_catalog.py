#!/usr/bin/env python3
"""Versioned Kubernetes/provider lifecycle data with conservative staleness handling."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "lifecycle-catalog.json"


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schemaVersion": "0", "asOf": "", "sources": {}, "catalogError": "lifecycle catalog unavailable"}
    return value if isinstance(value, dict) else {"schemaVersion": "0", "catalogError": "invalid lifecycle catalog"}


def minor(version: Any) -> str:
    match = re.search(r"(?:^|\D)(1\.\d+)(?:\D|$)", str(version or ""))
    return match.group(1) if match else ""


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def catalog_metadata(catalog: dict[str, Any], today: dt.date | None = None) -> dict[str, Any]:
    now = today or dt.datetime.now(dt.timezone.utc).date()
    as_of = parse_date(catalog.get("asOf"))
    max_age = int(catalog.get("staleAfterDays") or 30)
    age = (now - as_of).days if as_of else None
    return {
        "schemaVersion": str(catalog.get("schemaVersion") or "0"),
        "asOf": catalog.get("asOf") or "UNKNOWN",
        "ageDays": age,
        "stale": age is None or age > max_age,
        "staleAfterDays": max_age,
        "sources": catalog.get("sources") or {},
        "error": catalog.get("catalogError") or "",
    }


def assess(
    version: Any,
    provider: str = "generic-kubernetes",
    *,
    support_type: str = "",
    release_channel: str = "",
    provider_supported: bool | None = None,
    today: dt.date | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return lifecycle evidence without treating missing or stale data as supported."""
    now = today or dt.datetime.now(dt.timezone.utc).date()
    data = catalog or load_catalog()
    meta = catalog_metadata(data, now)
    current_minor = minor(version)
    source_key = {"generic-kubernetes": "kubernetes", "eks": "eks", "aks": "aks", "gke": "gke"}.get(provider, "kubernetes")
    record = ((data.get(source_key) or {}).get(current_minor) or {}) if current_minor else {}
    result: dict[str, Any] = {
        "minorVersion": current_minor or "UNKNOWN",
        "supportState": "UNKNOWN",
        "supportUntil": "UNKNOWN",
        "daysRemaining": None,
        "source": (meta.get("sources") or {}).get(source_key, ""),
        "catalogAsOf": meta["asOf"],
        "catalogStale": meta["stale"],
    }
    if provider == "aks" and provider_supported is not None:
        result["supportState"] = "PROVIDER_AVAILABLE" if provider_supported else "UPGRADE_REQUIRED"
        result["evidenceMode"] = "DYNAMIC_PROVIDER_API"
        return result
    if not record:
        return result

    standard = parse_date(record.get("standardEnd") or record.get("endOfSupport"))
    extended = parse_date(record.get("extendedEnd"))
    maintenance = parse_date(record.get("maintenanceEnd"))
    use_extended = support_type.upper() == "EXTENDED" or release_channel.upper() == "EXTENDED"
    boundary = extended if use_extended and extended else standard
    if not boundary:
        return result
    result["supportUntil"] = boundary.isoformat()
    result["daysRemaining"] = (boundary - now).days
    if now > boundary:
        state = "END_OF_SUPPORT"
    elif use_extended and standard and now > standard:
        state = "EXTENDED_SUPPORT"
    elif maintenance and now > maintenance:
        state = "MAINTENANCE"
    else:
        state = "SUPPORTED"
    if meta["stale"] and state in {"SUPPORTED", "MAINTENANCE", "EXTENDED_SUPPORT"}:
        state = "UNKNOWN_STALE_CATALOG"
    if provider == "gke" and provider_supported is False:
        state = "UPGRADE_REQUIRED"
        result["evidenceMode"] = "DYNAMIC_PROVIDER_API"
    elif provider == "gke" and provider_supported is True:
        result["evidenceMode"] = "CATALOG_AND_DYNAMIC_PROVIDER_API"
    result["supportState"] = state
    return result
