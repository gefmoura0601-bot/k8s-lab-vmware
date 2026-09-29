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

`release-gate` e `regression-gate` são offline e não exigem `kubectl`, kubeconfig ou conectividade com o cluster.

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
```

O profile e o provider esperado são escolhas do operador; não são inferidos para promover uma release. EKS, AKS e GKE continuam dependendo de qualificação real antes da versão estável.

## Segurança operacional

- use um diretório dedicado e gravável em `--root`;
- preserve os artefatos de uma execução até concluir os gates;
- trate exit code diferente de zero como bloqueio do pipeline;
- não publique URLs com access token do dashboard em logs compartilhados;
- não habilite logs de containers sem targets e retenção previamente aprovados.
