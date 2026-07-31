#!/usr/bin/env bash
set -Eeuo pipefail

JOIN_FILE="/vagrant/.cluster/join-command.sh"
[[ ! -f /etc/kubernetes/kubelet.conf ]] || exit 0

for _ in $(seq 1 60); do
  if [[ -s "${JOIN_FILE}" ]]; then
    bash "${JOIN_FILE}" --node-name "$(hostname -s)"
    exit 0
  fi
  sleep 5
done
echo "Timed out waiting for the control-plane join command" >&2
exit 1
