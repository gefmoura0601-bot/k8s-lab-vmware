#!/usr/bin/env bash
set -Eeuo pipefail

NODE_IP="${1:?node IP is required}"
CALICO_VERSION="${2:?Calico version is required}"
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

install -d -m 0700 /root/.kube
install -m 0600 "${KUBECONFIG}" /root/.kube/config
chown -R root:root /root/.kube
kubectl apply --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/v1_crd_projectcalico_org.yaml"
kubectl apply --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VERSION}/manifests/tigera-operator.yaml"
kubectl apply -f /workspace/iac/vagrant/calico/installation.yaml

for _ in $(seq 1 120); do
  if kubectl get tigerastatus calico >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
kubectl wait --for=condition=Available tigerastatus/calico --timeout=10m
kubectl apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCAL_PATH_VERSION}/deploy/local-path-storage.yaml"

mkdir -p /vagrant/.cluster
kubeadm token create --ttl 2h --print-join-command >/vagrant/.cluster/join-command.sh
chmod 0600 /vagrant/.cluster/join-command.sh

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side --force-conflicts -n argocd \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
argocd_server_insecure="$(kubectl -n argocd get configmap argocd-cmd-params-cm \
  -o jsonpath='{.data.server\.insecure}' 2>/dev/null || true)"
kubectl apply --server-side -f /workspace/kubernetes/argocd/argocd-cmd-params-cm.yaml
if [[ "${argocd_server_insecure}" != "true" ]]; then
  kubectl -n argocd rollout restart deployment/argocd-server
  kubectl -n argocd rollout status deployment/argocd-server --timeout=3m
fi
kubectl apply --server-side -f /workspace/kubernetes/argocd/root-app.yaml
