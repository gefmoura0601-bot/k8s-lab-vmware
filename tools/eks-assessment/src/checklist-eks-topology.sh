#!/usr/bin/env bash
# Shell functions sourced by checklist-eks.sh. All commands are read-only.

topology_object() {
  local title="$1" kind="$2" name="$3" namespace="$4"
  if kubectl get "$kind" "$name" -n "$namespace" >/dev/null 2>&1; then ok "$title" "$kind/$name presente em $namespace"; else warning "$title" "$kind/$name não encontrado em $namespace"; fi
}

topology_deployment() {
  local title="$1" name="$2" namespace="$3" available desired
  if ! kubectl get deployment "$name" -n "$namespace" >/dev/null 2>&1; then warning "$title" "Deployment/$name não encontrado em $namespace"; return; fi
  available="$(kubectl get deployment "$name" -n "$namespace" -o jsonpath='{.status.availableReplicas}')"
  desired="$(kubectl get deployment "$name" -n "$namespace" -o jsonpath='{.spec.replicas}')"
  if [[ "${available:-0}" == "${desired:-0}" ]]; then
    ok "$title" "Deployment/$name ${available}/${desired} disponível"
  else
    warning "$title" "Deployment/$name ${available:-0}/${desired:-0} disponível"
  fi
}

topology_statefulset() {
  local title="$1" name="$2" namespace="$3" ready desired
  if ! kubectl get statefulset "$name" -n "$namespace" >/dev/null 2>&1; then warning "$title" "StatefulSet/$name não encontrado em $namespace"; return; fi
  ready="$(kubectl get statefulset "$name" -n "$namespace" -o jsonpath='{.status.readyReplicas}')"
  desired="$(kubectl get statefulset "$name" -n "$namespace" -o jsonpath='{.spec.replicas}')"
  if [[ "${ready:-0}" == "${desired:-0}" ]]; then
    ok "$title" "StatefulSet/$name ${ready}/${desired} Ready"
  else
    warning "$title" "StatefulSet/$name ${ready:-0}/${desired:-0} Ready"
  fi
}

topology_namespace() {
  local namespace="$1"
  if kubectl get namespace "$namespace" >/dev/null 2>&1; then
    ok "Namespace" "$namespace presente"
  else
    warning "Namespace" "$namespace ausente"
  fi
}

topology_argocd() {
  local count unhealthy
  if ! kubectl get crd applications.argoproj.io >/dev/null 2>&1; then not_evaluated "Argo CD" "CRD Application indisponível"; return; fi
  count="$(kubectl -n argocd get applications -o json | json_count '.items | length')"
  unhealthy="$(kubectl -n argocd get applications -o json | json_count '[.items[] | select(.status.sync.status != "Synced" or .status.health.status != "Healthy")] | length')"
  if [[ "$unhealthy" == 0 ]]; then
    ok "Argo CD" "$count Application(s) Synced/Healthy"
  else
    warning "Argo CD" "$unhealthy de $count Application(s) fora de Synced/Healthy"
  fi
}

topology_calico() {
  local available
  if ! kubectl get tigerastatus calico >/dev/null 2>&1; then not_evaluated "Calico" "Tigerastatus/calico indisponível"; return; fi
  available="$(kubectl get tigerastatus calico -o jsonpath='{range .status.conditions[?(@.type=="Available")]}{.status}{end}')"
  if [[ "$available" == True ]]; then
    ok "Calico" "Available"
  else
    critical "Calico" "Tigerastatus Available=${available:-vazio}"
  fi
}

topology_istio() {
  local mtls
  topology_deployment "Istio control plane" istiod istio-system
  topology_deployment "Istio ingress" istio-ingressgateway istio-system
  if kubectl get peerauthentication -A >/dev/null 2>&1; then
    mtls="$(kubectl get peerauthentication -A -o json | json_count '[.items[] | select(.spec.mtls.mode == "STRICT")] | length')"
    if ((mtls > 0)); then
      ok "Istio mTLS" "$mtls PeerAuthentication STRICT"
    else
      warning "Istio mTLS" "nenhuma PeerAuthentication STRICT"
    fi
  else not_evaluated "Istio mTLS" "CRD PeerAuthentication indisponível"; fi
}

topology_observability() {
  local targets
  topology_deployment "Prometheus operator" kube-prometheus-stack-operator monitoring
  if kubectl get servicemonitor -A >/dev/null 2>&1; then
    targets="$(kubectl get servicemonitor -A -o json | json_count '.items | length')"
    if ((targets > 0)); then
      ok "Prometheus discovery" "$targets ServiceMonitor(s)"
    else
      warning "Prometheus discovery" "nenhum ServiceMonitor"
    fi
  else not_evaluated "Prometheus discovery" "CRD ServiceMonitor indisponível"; fi
  topology_deployment "Grafana" kube-prometheus-stack-grafana monitoring
}

topology_data_and_messaging() {
  topology_statefulset "PostgreSQL" postgres databases
  topology_statefulset "RabbitMQ" rabbitmq messaging
  local pvcs pending
  pvcs="$(kubectl get pvc -A -o json | json_count '.items | length')"
  pending="$(kubectl get pvc -A -o json | json_count '[.items[] | select(.status.phase != "Bound")] | length')"
  if [[ "$pending" == 0 ]]; then
    ok "Persistent volumes" "$pvcs PVC(s) Bound"
  else
    warning "Persistent volumes" "$pending PVC(s) não Bound"
  fi
}

topology_autoscaling_and_policies() {
  local scaled ready policies
  if kubectl get scaledobject -A >/dev/null 2>&1; then
    scaled="$(kubectl get scaledobject -A -o json | json_count '.items | length')"
    ready="$(kubectl get scaledobject -A -o json | json_count '[.items[] | select(.status.conditions[]? | select(.type == "Ready" and .status == "True"))] | length')"
    if ((scaled == ready)); then
      ok "KEDA" "$scaled ScaledObject(s) Ready"
    else
      warning "KEDA" "$ready/$scaled ScaledObject(s) Ready"
    fi
  else not_evaluated "KEDA" "CRD ScaledObject indisponível"; fi
  if kubectl get clusterpolicy >/dev/null 2>&1; then
    policies="$(kubectl get clusterpolicy -o json | json_count '.items | length')"
    if ((policies > 0)); then
      ok "Kyverno" "$policies ClusterPolicy(s) presente(s)"
    else
      warning "Kyverno" "nenhuma ClusterPolicy"
    fi
  else not_evaluated "Kyverno" "CRD ClusterPolicy indisponível"; fi
}

topology_workload_security() {
  local policies probes
  policies="$(kubectl get networkpolicy -A -o json | json_count '.items | length')"
  if ((policies > 0)); then
    ok "NetworkPolicy" "$policies policy object(s)"
  else
    warning "NetworkPolicy" "nenhuma policy"
  fi
  probes="$(kubectl get pods -A -o json | json_count '[.items[] | .spec.containers[]? | select(.livenessProbe != null and .readinessProbe != null)] | length')"
  if ((probes > 0)); then
    ok "Probes" "$probes container(s) com liveness/readiness"
  else
    warning "Probes" "nenhum container com ambas as probes"
  fi
}

run_topology_checks() {
  printf '\n== TOPOLOGIA DA PLATAFORMA ==\n'
  if [[ "${ASSESSMENT_TOPOLOGY_PROFILE:-generic}" == "lab-vmware" ]]; then
    local namespace
    for namespace in argocd apps databases messaging workers monitoring istio-system nginx-lab; do topology_namespace "$namespace"; done
    topology_calico; topology_argocd; topology_istio; topology_observability
    topology_data_and_messaging; topology_autoscaling_and_policies; topology_workload_security
    return
  fi
  not_evaluated "Topologia específica" "perfil genérico; defina ASSESSMENT_TOPOLOGY_PROFILE=lab-vmware somente para o lab conhecido"
  topology_autoscaling_and_policies
  topology_workload_security
}
