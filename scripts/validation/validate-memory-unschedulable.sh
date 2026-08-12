#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-memory-scheduling-lab}"
POD="${POD:-memory-request-too-large}"
REQUEST_MEMORY="${REQUEST_MEMORY:-8Gi}"
OBSERVE_SECONDS="${OBSERVE_SECONDS:-105}"

cleanup() {
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true >/dev/null 2>&1 || true
kubectl create namespace "${NAMESPACE}"
kubectl label namespace "${NAMESPACE}" team=platform environment=lab experiment=memory-unschedulable --overwrite

kubectl -n "${NAMESPACE}" apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
  labels:
    app: memory-request-too-large
    team: platform
    environment: lab
    experiment: memory-unschedulable
spec:
  restartPolicy: Never
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node-role.kubernetes.io/control-plane
                operator: DoesNotExist
  containers:
    - name: sleeper
      image: docker.io/library/alpine:3.22.1
      command: ["/bin/sh", "-c", "sleep 3600"]
      resources:
        requests:
          cpu: 10m
          memory: ${REQUEST_MEMORY}
        limits:
          cpu: 20m
          memory: ${REQUEST_MEMORY}
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
        readOnlyRootFilesystem: true
        runAsNonRoot: true
        runAsUser: 65534
        seccompProfile:
          type: RuntimeDefault
EOF

phase=""
for _ in $(seq 1 30); do
  phase="$(kubectl -n "${NAMESPACE}" get pod "${POD}" -o jsonpath='{.status.phase}')"
  [[ "${phase}" == "Pending" ]] && break
  sleep 1
done
[[ "${phase}" == "Pending" ]] || { echo "ERRO: pod não permaneceu Pending (phase=${phase})" >&2; exit 1; }

message=""
for _ in $(seq 1 30); do
  message="$(kubectl -n "${NAMESPACE}" get events --field-selector involvedObject.kind=Pod,involvedObject.name="${POD}",reason=FailedScheduling -o jsonpath='{.items[-1:].message}' 2>/dev/null || true)"
  [[ "${message}" == *"Insufficient memory"* ]] && break
  sleep 1
done
[[ "${message}" == *"Insufficient memory"* ]] || { echo "ERRO: evento FailedScheduling por memória não observado: ${message}" >&2; exit 1; }

scheduled_node="$(kubectl -n "${NAMESPACE}" get pod "${POD}" -o jsonpath='{.spec.nodeName}')"
[[ -z "${scheduled_node}" ]] || { echo "ERRO: pod foi agendado em ${scheduled_node}" >&2; exit 1; }

echo "Pending confirmado: request=${REQUEST_MEMORY} node=<none>"
echo "FailedScheduling confirmado: ${message}"
echo "Mantendo o pod por ${OBSERVE_SECONDS}s para scrape e alerta do Prometheus"
sleep "${OBSERVE_SECONDS}"

pressure="$(kubectl get nodes -o json | jq '[.items[].status.conditions[] | select(.type=="MemoryPressure" and .status!="False")] | length')"
[[ "${pressure}" == "0" ]] || { echo "ERRO: MemoryPressure detectado" >&2; exit 1; }
[[ "$(kubectl -n "${NAMESPACE}" get pod "${POD}" -o jsonpath='{.status.phase}')" == "Pending" ]] || { echo "ERRO: pod deixou de estar Pending" >&2; exit 1; }

echo "OK: insuficiência de memória validada sem consumo real e sem MemoryPressure"