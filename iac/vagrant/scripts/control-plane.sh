#!/usr/bin/env bash
set -Eeuo pipefail

NODE_IP="${1:?node IP is required}"
FLANNEL_VERSION="${2:?Flannel version is required}"
ARGOCD_VERSION="${3:?Argo CD version is required}"
LOCAL_PATH_VERSION="v0.0.32"
export KUBECONFIG=/etc/kubernetes/admin.conf

if [[ ! -f "${KUBECONFIG}" ]]; then
  kubeadm init --apiserver-advertise-address="${NODE_IP}" \
    --control-plane-endpoint="k8s-master:6443" \
    --pod-network-cidr="10.244.0.0/16" --node-name="k8s-master"
fi

for _ in $(seq 1 60); do
  if kubectl get --raw=/readyz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
kubectl get --raw=/readyz >/dev/null

install -d -m 0700 /home/vagrant/.kube
install -m 0600 "${KUBECONFIG}" /home/vagrant/.kube/config
chown -R vagrant:vagrant /home/vagrant/.kube
kubectl apply -f "https://github.com/flannel-io/flannel/releases/download/${FLANNEL_VERSION}/kube-flannel.yml"
kubectl apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCAL_PATH_VERSION}/deploy/local-path-storage.yaml"

mkdir -p /vagrant/.cluster
kubeadm token create --ttl 2h --print-join-command >/vagrant/.cluster/join-command.sh
chmod 0600 /vagrant/.cluster/join-command.sh

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side --force-conflicts -n argocd \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
kubectl apply --server-side -f /workspace/kubernetes/argocd/root-app.yaml
