# Roadmap — Operational Insights

Implementado na série `0.4.0-rc` na ordem que minimiza chamadas e código duplicado:

1. contrato único `operational-insights.json`;
2. `Events & Diagnostics`, reutilizando Events e Pods sanitizados;
3. `Node Health`, reutilizando Nodes, Pods e Metrics API sem SSH ou acesso ao filesystem;
4. `Versions & Lifecycle`, reutilizando node info, imagens e tecnologias;
5. `Manifest Quality`, reutilizando findings estáveis e manifests sanitizados;
6. `Container Tuning`, reutilizando telemetria e recomendações de capacidade;
7. `Best Practices`, com regras genéricas e aplicabilidade EKS, AKS e GKE;
8. logs opcionais, por serem a evidência de maior risco e custo;
9. catálogo versionado de lifecycle para Kubernetes, EKS e GKE, com staleness explícito;
10. evidência normalizada read-only de EKS, AKS e GKE;
11. navegação agrupada, busca global e página Cloud Provider;
12. quality gate para deduplicação, severidade coerente e confiança mínima de `PASS`;
13. métricas de impacto por coleta e por componente;
14. cancelamento/timeout com estado terminal preservado desde o preflight;
15. smoke de logs sanitizados sem exposição de conteúdo no output de validação.

## Guardrails

- somente leitura; nenhuma recomendação altera recursos;
- regras de outro provider ficam `NOT_APPLICABLE`;
- recomendações de provider sem Cloud API ficam `MANUAL_REVIEW`, nunca `PASS`;
- mensagens livres de Events não são persistidas;
- logs ficam desabilitados por padrão e exigem opt-in e targets explícitos;
- logs usam `--tail=200`, `--since=1h`, limite global de 256 KiB por padrão e redaction;
- versões desconhecidas permanecem `UNKNOWN`; catálogo vencido nunca declara versão suportada;
- payloads brutos e IDs de account/subscription/project não são persistidos;
- busca global não indexa conteúdo de logs.

## Gates externos para `0.4.0` estável

- validar em EKS, AKS e GKE reais com permissões read-only;
- calibrar falsos positivos e diferenças regionais com as três evidências reais;
- medir duração, chamadas de API e memória em ambientes transacionais;
- validar logs sanitizados com targets aprovados e política de retenção;
- repetir cancelamento, timeout e coleta grande no pacote da release.

Esses gates são operacionais, não pendências de implementação local. Sem credenciais/cluster acessível, a versão permanece release candidate e nenhum ambiente cloud é marcado como validado.

## Evidência local da RC.3

- preflight on-premises sem `WARN` ou `FAIL`;
- coleta completa no lab com estado `COMPLETED` e zero mutações;
- cancelamento real sem processo órfão, inclusive durante o preflight;
- quality gate sem duplicidades, conflitos de severidade ou `PASS` de baixa confiança;
- dashboard, exportações e coleta opt-in de logs sanitizados validados por smoke tests.

Essa evidência confirma o caminho Kubernetes genérico. Ela não substitui os gates externos em EKS, AKS e GKE.
