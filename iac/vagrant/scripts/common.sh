#!/usr/bin/env bash
set -Eeuo pipefail

NODE_IP="${1:?node IP is required}"
KUBERNETES_MINOR="${2:?Kubernetes minor version is required}"

cat >/etc/hosts <<'EOF'
127.0.0.1 localhost localhost.localdomain
::1 localhost localhost.localdomain
192.168.109.151 k8s-master
192.168.109.153 k8s-worker-01
192.168.109.155 k8s-worker-02
EOF

TARGET_DEVICE="$(ip -o -4 addr show | awk '$4 ~ /^192\.168\.109\./ {print $2; exit}')"
[[ -n "${TARGET_DEVICE}" ]] || TARGET_DEVICE="$(ip route | awk '/default/ {print $5; exit}')"
TARGET_CONNECTION="$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: -v dev="${TARGET_DEVICE}" '$2 == dev {print $1; exit}')"
[[ -n "${TARGET_DEVICE}" && -n "${TARGET_CONNECTION}" ]] || { echo "Primary network connection not found" >&2; exit 1; }

nmcli connection modify "${TARGET_CONNECTION}" connection.autoconnect yes \
  ipv4.method auto ipv4.ignore-auto-dns yes ipv4.dns "1.1.1.1 8.8.8.8"
if ! nmcli -g ipv4.addresses connection show "${TARGET_CONNECTION}" | grep -Fq "${NODE_IP}/24"; then
  nmcli connection modify "${TARGET_CONNECTION}" +ipv4.addresses "${NODE_IP}/24"
fi
nmcli device reapply "${TARGET_DEVICE}" || true

swapoff -a
sed -ri '/\sswap\s/s/^/#/' /etc/fstab

# Lab-only posture. Production nodes should use managed SELinux and firewall policies.
setenforce 0 2>/dev/null || true
sed -ri 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config
systemctl disable --now firewalld || true

cat >/etc/modules-load.d/kubernetes.conf <<'EOF'
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter

cat >/etc/sysctl.d/99-kubernetes.conf <<'EOF'
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF
sysctl --system >/dev/null

dnf install -y dnf-plugins-core
dnf config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y containerd.io
mkdir -p /etc/containerd
containerd config default >/etc/containerd/config.toml
sed -ri 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
systemctl enable --now containerd

cat >/etc/yum.repos.d/kubernetes.repo <<EOF
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/${KUBERNETES_MINOR}/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/${KUBERNETES_MINOR}/rpm/repodata/repomd.xml.key
exclude=kubelet kubeadm kubectl cri-tools kubernetes-cni
EOF
dnf install -y kubelet kubeadm kubectl --disableexcludes=kubernetes
cat >/etc/sysconfig/kubelet <<EOF
KUBELET_EXTRA_ARGS=--node-ip=${NODE_IP}
EOF
systemctl enable kubelet
