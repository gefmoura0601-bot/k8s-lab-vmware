#!/usr/bin/env bash
set -Eeuo pipefail

NODE_IP="${1:?node IP is required}"

cat >/etc/hosts <<'EOF'
127.0.0.1 localhost localhost.localdomain
::1 localhost localhost.localdomain
192.168.109.151 k8s-master
192.168.109.153 k8s-worker-01
192.168.109.155 k8s-worker-02
EOF

TARGET_DEVICE="$(ip -o -4 addr show | awk '$4 ~ /^192\.168\.109\./ {print $2; exit}')"
[[ -n "${TARGET_DEVICE}" ]] || TARGET_DEVICE="$(ip route | awk '/default/ {print $5; exit}')"
TARGET_CONNECTION="$(nmcli -t -f NAME,DEVICE connection show --active |
  awk -F: -v dev="${TARGET_DEVICE}" '$2 == dev {print $1; exit}')"
[[ -n "${TARGET_DEVICE}" && -n "${TARGET_CONNECTION}" ]] || {
  echo "Primary network connection not found" >&2
  exit 1
}

nmcli connection modify "${TARGET_CONNECTION}" connection.autoconnect yes \
  ipv4.method auto ipv4.ignore-auto-dns yes ipv4.dns "1.1.1.1 8.8.8.8"
if ! nmcli -g ipv4.addresses connection show "${TARGET_CONNECTION}" |
  grep -Fq "${NODE_IP}/24"; then
  nmcli connection modify "${TARGET_CONNECTION}" +ipv4.addresses "${NODE_IP}/24"
fi
nmcli device reapply "${TARGET_DEVICE}" || true
