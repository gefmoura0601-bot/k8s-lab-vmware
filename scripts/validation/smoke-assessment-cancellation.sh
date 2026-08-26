#!/usr/bin/env bash
# Starts a real read-only collection, cancels it, and verifies bounded cleanup.
set -euo pipefail

BASE_URL="${ASSESSMENT_BASE_URL:-http://127.0.0.1:8765}"
ROOT="${ASSESSMENT_ROOT:-/workspace/assessment}"
page="$(curl -fsS "$BASE_URL/collect")"
token="$(sed -n 's/.*name="action_token" value="\([A-Za-z0-9_-]*\)".*/\1/p' <<<"$page" | head -1)"
[[ -n "$token" ]] || { echo "action token not found" >&2; exit 1; }

response="$(mktemp)"
curl -sS -o "$response" \
  --data-urlencode "action_token=$token" \
  --data-urlencode "label=cancel-smoke" \
  --data-urlencode "profile=low-impact" \
  --data-urlencode "baseline=0" \
  "$BASE_URL/collect" &
request_pid=$!

collection=""; child_pid=""
for _ in {1..100}; do
  status="$(curl -fsS "$BASE_URL/api/collection-status")"
  collection="$(jq -r '.collection // empty' <<<"$status")"
  child_pid="$(jq -r '.pid // empty' <<<"$status")"
  [[ "$(jq -r '.active' <<<"$status")" == true && -n "$child_pid" ]] && break
  sleep 0.05
done
[[ -n "$collection" && -n "$child_pid" ]] || { echo "collection did not become active" >&2; kill "$request_pid" 2>/dev/null || true; exit 1; }

curl -sS -o /dev/null -X POST "$BASE_URL/cancel"
wait "$request_pid"
for _ in {1..100}; do
  status="$(curl -fsS "$BASE_URL/api/collection-status")"
  [[ "$(jq -r '.active' <<<"$status")" == false ]] && break
  sleep 0.05
done

[[ ! -d "/proc/$child_pid" ]] || { echo "collector process remains alive: $child_pid" >&2; exit 1; }
jq -e '.status == "CANCELLED" and .completed == false and .cancelled == true' "$ROOT/$collection/metadata.json" >/dev/null
jq -n --arg collection "$collection" --arg childPid "$child_pid" '{ok:true,status:"CANCELLED",collection:$collection,terminatedPid:$childPid,orphan:false}'
