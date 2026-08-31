# Evidência read-only de cloud providers

O arquivo `cloud-provider-assessment.json` normaliza somente campos necessários de EKS, AKS ou GKE. Payloads brutos, credenciais, endpoints, IDs de subscription/account/project e resource IDs não são persistidos. Falha de CLI, IAM ou escopo produz `UNAVAILABLE`, `PARTIAL` ou `UNKNOWN`; nunca `PASS`.

## Amazon EKS

O normalizador reutiliza `aws-eks-assessment.json`, produzido pelo coletor AWS existente. Configure:

```bash
export EKS_CLUSTER_NAME='cluster'
export AWS_REGION='us-east-1'
```

As permissões estão em `deploy/iam-eks-readonly.json`. O assessment executa somente operações `list`, `describe` e `get`.

## Azure Kubernetes Service

Instale e autentique a Azure CLI, depois informe o escopo explicitamente:

```bash
export AKS_CLUSTER_NAME='cluster'
export AKS_RESOURCE_GROUP='resource-group'
```

Chamadas realizadas:

- `az aks show`;
- `az aks get-upgrades`;
- `az aks nodepool list`;
- `az aks get-versions`.

O exemplo `deploy/azure-aks-assessment-readonly-role.json` contém apenas ações Azure RBAC de leitura para cluster, upgrade profile, agent pools e versões regionais. Ele inclui os endpoints `kubernetesversions/read` atual e `orchestrators/read` legado para compatibilidade entre versões da Azure CLI. Substitua o `AssignableScopes` antes de criar a role e associe-a somente à identidade aprovada.

## Google Kubernetes Engine

Instale e autentique a Google Cloud CLI. Contextos no formato padrão `gke_<project>_<location>_<cluster>` são detectados; também é possível informar:

```bash
export GKE_CLUSTER_NAME='cluster'
export GKE_LOCATION='us-central1'
export GCP_PROJECT='project-id'
```

O project é usado somente nos argumentos da chamada e não é persistido. Chamadas realizadas:

- `gcloud container clusters describe`;
- `gcloud container get-server-config`.

O exemplo `deploy/gcp-gke-assessment-readonly-role.yaml` limita-se a `container.clusters.get`. Como alternativa gerenciada, valide `roles/container.clusterViewer` segundo a política IAM da organização.

## Desabilitar enriquecimento

Para executar apenas com a Kubernetes API:

```bash
export ASSESSMENT_CLOUD_API=disabled
```

O artefato continuará sendo criado com estado `DISABLED` ou `N/A`, preservando o contrato de exportação e validação.
