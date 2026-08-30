# Roadmap — Operational Insights

Implementado na `0.4.0-rc.1` na ordem que minimiza chamadas e código duplicado:

1. contrato único `operational-insights.json`;
2. `Events & Diagnostics`, reutilizando Events e Pods sanitizados;
3. `Versions & Lifecycle`, reutilizando node info, imagens e tecnologias;
4. `Manifest Quality`, reutilizando findings estáveis e manifests sanitizados;
5. `Container Tuning`, reutilizando telemetria e recomendações de capacidade;
6. `Best Practices`, com regras genéricas e aplicabilidade EKS, AKS e GKE;
7. logs opcionais, por serem a evidência de maior risco e custo.

## Guardrails

- somente leitura; nenhuma recomendação altera recursos;
- regras de outro provider ficam `NOT_APPLICABLE`;
- recomendações de provider sem Cloud API ficam `MANUAL_REVIEW`, nunca `PASS`;
- mensagens livres de Events não são persistidas;
- logs ficam desabilitados por padrão e exigem opt-in e targets explícitos;
- logs usam `--tail=200`, `--since=1h`, limite global de 256 KiB por padrão e redaction;
- versões desconhecidas permanecem `UNKNOWN`; lifecycle/EOL exige catálogo oficial atualizado.

## Próximos gates

- validar em EKS, AKS e GKE reais com permissões read-only;
- integrar catálogos versionados oficiais para lifecycle/EOL;
- ampliar regras automatizadas de provider somente quando houver evidência de Cloud API;
- medir precisão, custo de API e falsos positivos em ambientes transacionais.
