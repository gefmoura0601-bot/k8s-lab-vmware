# CLI headless

`bin/eks-assessment.sh` abre o menu quando executado sem argumentos. Com um subcomando, ele não solicita input e pode ser usado em automação. O comportamento continua read-only e usa os mesmos limites, sanitização e validação de artefatos da interface interativa.

## Subcomandos

| Subcomando | Dependências principais | Exit code |
|---|---|---|
| `preflight` | Python e dependências verificadas pelo preflight | `0` aprovado; `1` com falha obrigatória |
| `collect` | Kubernetes toolchain completa | `0` `COMPLETED`; `1` `FAILED`; `124` timeout; `130` cancelamento |
| `list` | filesystem local | `0` |
| `compare` | `jq` e duas coletas | `0`; `2` argumentos/artefatos inválidos |
| `terminal` | `jq` e uma coleta | `0`; `2` coleta inválida |
| `dashboard` | Python 3.10+ | permanece em foreground; `Ctrl+C` encerra |
| `release-gate` | Python e `jq` | `0` aprovado; `1` bloqueado; `2` input inválido |
| `regression-gate` | Python e `jq` | `0` aprovado; `1` bloqueado; `2` input inválido |
| `blue-green-gate` | Python e `jq`; rede somente para probes explícitos | `0` somente em `GO`; `1` em `NO_GO`/`UNKNOWN`; `2` input inválido |
| `migration-gate` | Python, `jq` e duas coletas | `0` somente em `GO`; `1` em `NO_GO`/`UNKNOWN`; `2` input inválido |
| `validate` | Python e schemas locais | `0` contratos válidos; `1` inválidos |
| `bundle export/verify/import` | Python e filesystem local | `0` íntegro; `1` inválido |
| `prune` | Python e filesystem local | dry-run por padrão; `--confirm` remove |
| `verify-release` | Python; `cosign` apenas com Sigstore bundle | `0` íntegro; `1` inválido |

`release-gate`, `regression-gate` e `migration-gate` são offline e não exigem `kubectl`, kubeconfig ou conectividade com o cluster. `blue-green-gate` usa somente artefatos locais, salvo quando recebe `--probe-url` explícita.

## Coleta

```bash
bin/eks-assessment.sh collect \
  --phase before \
  --change-id release-2026-09-28 \
  --namespace banking \
  --no-prometheus \
  --max-duration 1800 \
  --root ./assessment
```

`--phase` aceita `before` ou `after`; `--change-id` aceita somente letras, números, ponto, `_` e `-`. Namespace vazio significa cluster inteiro. A janela Prometheus aceita `1d`, `3d`, `7d`, `14d` ou `30d`.

O modo headless não descobre Prometheus por padrão. Isso evita que uma automação passe a consultar um endpoint apenas porque um Service novo apareceu no cluster. Escolha uma das opções:

- `--no-prometheus`: desabilitado explicitamente;
- `--prometheus-url http://prometheus.monitoring.svc:9090`: endpoint explícito;
- `--auto-detect-prometheus`: opt-in na sugestão read-only usada pelo menu.

Nunca inclua credenciais na URL. O preflight rejeita credentials, redirects e destinos proibidos.

Para resolver Configuration References sem persistir values, habilite um ou os dois opt-ins:

```bash
bin/kubernetes-assessment collect --phase after --change-id green \
  --namespace banking-green --configmap-metadata --secret-metadata \
  --probe-url https://green.example.test/health --no-prometheus
```

Os dois opt-ins exigem `--namespace` explícito; metadata cluster-wide é bloqueada. O preflight exige o RBAC correspondente. Secret metadata deve usar identidade temporária e namespace mínimo; consulte `docs/blue-green-migration.md`.

### Collector registry e retomada

`data/collectors.json` define ID, dependências, timeout, obrigatoriedade e peso.
Collectors obrigatórios não podem ser excluídos. Para limitar etapas opcionais:

```bash
bin/kubernetes-assessment collect --phase after --change-id release-42 \
  --no-prometheus --exclude-collector node-evidence
```

Uma coleta parcial pode ser retomada sem repetir collectors em `PASS`:

```bash
bin/kubernetes-assessment collect --resume "$COLLECTION_ID"
bin/kubernetes-assessment collect --resume "$COLLECTION_ID" --retry-failed
```

A retomada confirma o mesmo contexto Kubernetes e preserva `createdAt`, scope,
tentativas e evidência anterior. `preflight` sempre é executado novamente.

## Contratos, bundles e retenção

```bash
bin/kubernetes-assessment validate --root ./assessment --collection "$COLLECTION_ID"
bin/kubernetes-assessment bundle export --root ./assessment --collection "$COLLECTION_ID" --output ./collection.tar.gz
bin/kubernetes-assessment bundle verify --bundle ./collection.tar.gz
bin/kubernetes-assessment bundle import --root ./imported --bundle ./collection.tar.gz
bin/kubernetes-assessment prune --root ./assessment --older-than 30d
bin/kubernetes-assessment prune --root ./assessment --older-than 30d --confirm
```

O primeiro `prune` apenas lista candidatos. Baselines são protegidos por padrão.

## Gates

```bash
bin/eks-assessment.sh release-gate \
  --root ./assessment \
  --collection eks-20260928T120000Z-after-release.abcd1234 \
  --provider generic-kubernetes

bin/eks-assessment.sh regression-gate \
  --root ./assessment \
  --before eks-20260928T110000Z-before-release.abcd1234 \
  --after eks-20260928T120000Z-after-release.efgh5678 \
  --profile standard

bin/eks-assessment.sh blue-green-gate \
  --root ./assessment \
  --collection eks-20260928T120000Z-after-release.efgh5678 \
  --probe-url https://green.example.test/health

bin/eks-assessment.sh migration-gate \
  --root ./assessment \
  --source eks-20260928T110000Z-before-release.abcd1234 \
  --target eks-20260928T120000Z-after-release.efgh5678 \
  --mapping docs/migration-mapping.example.json
```

O profile, o provider esperado e o mapping são escolhas do operador; não são inferidos para promover uma release. As duas coletas do Regression Gate precisam ter o mesmo cluster e o mesmo `namespaceScope`; o Migration Gate permite identidades distintas por mapping explícito. EKS, AKS e GKE permanecem `PREVIEW` até qualificação real.

O workflow `EKS Assessment CI` executa os dois gates com fixtures sanitizadas do
profile `generic-kubernetes` e publica JSON/JUnit/SARIF/Markdown do Release Gate,
JSON/JUnit/SARIF do Regression Gate e logs resumidos como artifact. O mesmo
workflow valida checksum, SBOM, provenance, paths, alias provider-neutral e
execução do pacote portátil, além de gerar attestation no push.

## Segurança operacional

- use um diretório dedicado e gravável em `--root`;
- preserve os artefatos de uma execução até concluir os gates;
- trate exit code diferente de zero como bloqueio do pipeline;
- não publique URLs com access token do dashboard em logs compartilhados;
- não habilite logs de containers sem targets e retenção previamente aprovados.
