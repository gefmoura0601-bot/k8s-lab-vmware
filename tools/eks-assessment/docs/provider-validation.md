# Provider Validation Runner

O `Provider Validation Runner` transforma uma coleta sanitizada em um gate reproduzível para validação de Amazon EKS, Azure AKS, Google GKE ou Kubernetes genérico. Ele não consulta novamente o cluster nem executa Cloud Provider CLI: toda a decisão é offline e baseada nos artefatos já coletados.

O provider esperado é obrigatório. Assim, a ferramenta não usa a própria detecção como expectativa e não aprova uma classificação incorreta por circularidade.

## Execução

```bash
python3 src/provider_validation.py \
  --collection /caminho/assessment/eks-20260831T120000Z-after-release \
  --expected-provider eks
```

Use `aks`, `gke` ou `generic-kubernetes` nos demais ambientes. O relatório padrão é gravado como `provider-validation.json` dentro da coleta. `--output` aceita outro arquivo quando o diretório pai já existe.

O exit code é:

- `0`: todos os gates obrigatórios estão `PASS` e `releaseReady=true`;
- `1`: existe `WARN` ou `FAIL`; a coleta não está pronta para promover a release;
- `2`: argumento inválido, diretório ausente ou configuração incorreta.

`WARN` nunca é tratado como aprovação. Uma coleta antiga ou parcial pode ser útil para diagnóstico, mas não certifica a release.

## Gates

O relatório cruza evidências independentes e verifica:

1. estado terminal `COMPLETED`;
2. detecção consistente entre Kubernetes evidence, `cloud-provider-assessment.json` e `operational-insights.json`;
3. integridade, schema e sanitização dos artefatos obrigatórios;
4. contratos `readOnly`, allowlists e zero mutações;
5. omissão de credenciais, payloads brutos e identificadores de account/subscription/project;
6. aplicabilidade de Best Practices e responsabilidade do control plane;
7. findings sem duplicidade, conflito de severidade ou `PASS` de baixa confiança;
8. cobertura das APIs Kubernetes e Cloud Provider;
9. duração, chamadas, retries, throttling, bytes recebidos e peak RSS.

O relatório contém somente estados, contagens, thresholds e identificadores técnicos dos gates. `clusterName`, contexto, account ID, subscription ID, project ID, endpoints e payloads cloud não são copiados.

## Thresholds padrão

| Parâmetro | Padrão |
|---|---:|
| `--max-duration-seconds` | 1800 |
| `--max-api-requests` | 1000 |
| `--max-cloud-api-requests` | 100 |
| `--max-api-retries` | 3 |
| `--max-api-throttles` | 0 |
| `--max-response-mb` | 256 |
| `--max-peak-rss-mb` | 512 |
| `--min-kubernetes-coverage-percent` | 80 |
| `--min-cloud-coverage-percent` | 80 |

Os limites devem ser definidos antes da execução no ambiente transacional. Reduzir um threshold depois de observar o resultado invalida a comparação; aumentá-lo exige justificativa operacional registrada fora do artefato.

## Matriz real

Para cada provider:

1. aplique apenas o RBAC/IAM read-only documentado;
2. execute o preflight;
3. gere uma coleta completa e confirme `COMPLETED`;
4. execute o runner informando explicitamente o provider esperado;
5. arquive a coleta sanitizada e o `provider-validation.json`;
6. calibre falsos positivos sem alterar a responsabilidade gerenciada do control plane.

Em Kubernetes genérico, `provider.cloud-api` fica `N/A` e não reduz o resultado. Em EKS, AKS ou GKE, Cloud Provider API `PARTIAL` produz `WARN`; evidência ausente ou provider divergente produz `FAIL`.

O resultado comprova os gates implementados pela ferramenta. Ele não representa certificação do cloud provider, CIS certification nem compliance integral.
