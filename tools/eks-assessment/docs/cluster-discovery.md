# EKS / Kubernetes cluster discovery

`tools/eks-assessment/src/eks-cluster-discovery.sh` produces a read-only technical
inventory of the cluster. It is an original implementation following the
safety and report-organisation approach of the AWS EKS Cluster Discovery Tool.

It covers 49 sections: API and nodes, namespaces and quotas, workloads,
autoscaling, services and mesh, Calico, storage, configuration metadata,
identity/RBAC, admission, Kyverno, Argo CD, monitoring and warning events.
It also includes the topology used by this laboratory: Argo CD, Calico, Istio,
NGINX, PostgreSQL, RabbitMQ, KEDA, Kyverno and Prometheus.

The script uses only `kubectl get`, `kubectl version`, `kubectl cluster-info`
and one read-only metrics API request. It never applies, patches, deletes,
restarts, scales or reads Secret values. Secret reporting is limited to name,
namespace, type, creation date and key names.

Run a complete report from the master node:

```bash
bash /workspace/tools/eks-assessment/src/eks-cluster-discovery.sh \
  --delay-ms 150 --timeout 20s \
  --output-dir /tmp/eks-discovery --combined-report
```

For a production-sized cluster, start with a targeted namespace and a more
conservative delay/output limit:

```bash
bash /workspace/tools/eks-assessment/src/eks-cluster-discovery.sh \
  --namespace my-namespace --large-cluster --delay-ms 500 \
  --output-dir /tmp/eks-discovery
```

The output directory contains a text file per section, a combined report when
requested, and `summary.json`. APIs/CRDs that are not installed are reported
as `N/A`; timeouts or inaccessible APIs are counted as `unavailable` in the
summary and make the command return a non-zero status.

O inventário textual é correlacionado pelo scanner
`tools/eks-assessment/src/eks_comprehensive_assessment.py`, que analisa cada workload
e container, classifica itens não aplicáveis como `N/A` e gera as evidências
sanitizadas consumidas pelo dashboard.

Prometheus telemetry remains an optional, read-only source in
`tools/eks-assessment/src/prometheus_telemetry.py`: it requires an explicit URL,
uses HTTP GET only and feeds statistical capacity recommendations without
changing workloads, gates or baselines.
