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
16. `Provider Validation Runner` offline com provider esperado obrigatório e gates de release reproduzíveis.
17. `Release Gate Console` no menu e dashboard, com execução offline, persistência atômica, busca e exportação sanitizada.
18. `Regression Gate` provider-neutral com policy versionada, comparação offline e exportações JSON, JUnit e SARIF.
19. CLI headless para coleta e gates reproduzíveis, com argumentos explícitos e exit codes para CI/CD.
20. JSON Schemas versionados e validação offline dos contratos da coleta.
21. bundle portátil com manifest, hashes, export, verify, import e prune dry-run.
22. collector registry com dependências, filtros, retry, resume e progresso ponderado.
23. Schema & Semantic Validation offline para manifests sanitizados.
24. Node Evidence opcional via Prometheus, sem SSH ou acesso ao filesystem.
25. Operational Timeline sanitizada no dashboard.
26. Release Gate em JSON, JUnit, SARIF e Markdown.
27. verificação de package/SBOM/provenance e attestation no GitHub Actions.
28. alias provider-neutral `kubernetes-assessment`, mantendo compatibilidade.
29. Configuration References opt-in, sem persistência de values de ConfigMaps ou Secrets.
30. Traffic Paths para Ingress, Gateway API, Istio e OpenShift Route.
31. State & Data Readiness com evidência manual versionada para migração.
32. Blue-Green Readiness por coleta e probes HTTP/HTTPS explícitos.
33. Migration Gate source → target com mapping cross-cluster/cross-namespace e outputs de CI/CD.
34. console Blue-Green no menu e dashboard, com JSON Schemas fail-closed.

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

## Gates externos para qualificação dos profiles cloud

- validar em EKS, AKS e GKE reais com permissões read-only;
- calibrar falsos positivos e diferenças regionais com as três evidências reais;
- medir duração, chamadas de API e memória em ambientes transacionais cloud;
- validar logs sanitizados com targets aprovados e política de retenção;
- repetir cancelamento, timeout e coleta grande no pacote da release.

Esses gates são operacionais, não pendências de implementação local. A versão
`0.4.0` é estável para `generic-kubernetes`; sem credenciais e clusters reais,
EKS, AKS e GKE permanecem `PREVIEW` e nenhum ambiente cloud é marcado como
validado.

## Evidência local das RC.5 a RC.7

- preflight on-premises sem `WARN` ou `FAIL`;
- coleta completa no lab com estado `COMPLETED` e zero mutações;
- cancelamento real sem processo órfão, inclusive durante o preflight;
- quality gate sem duplicidades, conflitos de severidade ou `PASS` de baixa confiança;
- dashboard, exportações e coleta opt-in de logs sanitizados validados por smoke tests.
- `Node Health` validado no lab com três nodes e cobertura integral da Metrics API;
- página `Cloud Provider` protegida por regressão contra placeholders de template não resolvidos.
- inventário live da RC.5 medido no lab: 2.072 objetos, 156 chamadas Kubernetes API, zero retry/throttling, 16,832s de coleta, 34.405.187 bytes recebidos e peak RSS de 156.073.984 bytes.
- matriz offline da RC.6 aprovada para EKS, AKS, GKE e Kubernetes genérico; mismatch, mutação, regra de outro provider e budget excedido são rejeitados;
- coleta anterior do lab permanece `WARN` no runner por não possuir evidência de chamadas/bytes da API, evitando aprovação retroativa sem métricas.
- coleta fresh da RC.6 concluída pelo menu com todos os coletores em código zero e artefatos válidos; o runner retornou `PASS` e `releaseReady=true` para Kubernetes genérico, com cobertura Kubernetes de 100%, 156 chamadas de API, zero retry/throttling, 37s de duração, 34.614.935 bytes recebidos e peak RSS de 154.189.824 bytes;
- pacote portátil `0.4.0-rc.6` validado por checksum, compile, preflight e execução do runner a partir do diretório extraído.
- console `0.4.0-rc.7` validado pelo menu real: opção 7, provider `generic-kubernetes`, 10 gates, 9 `PASS`, 0 `WARN`, 0 `FAIL`, 1 `N/A` e `releaseReady=true`;
- página e exportação do `Release Gate` validadas por smoke HTTP autenticado, sem novas chamadas às APIs;
- pacote portátil `0.4.0-rc.7` validado por checksum, compile, sintaxe Bash, preflight com 18 `PASS`/0 `WARN`/0 `FAIL` e runner executado a partir do diretório extraído;
- lab completo validado com `k8s-master`, `k8s-worker-01` e `k8s-worker-02` em estado `Ready`, incluindo resposta da Metrics API para os três nodes.
- `Regression Gate` da RC.8 validado com matriz automatizada de 88 testes, incluindo profiles, fingerprints, CIS, Node Health, manifests, lifecycle, quality gate, performance, autenticação e outputs de CI/CD;
- comparação entre coletas reais antigas bloqueou corretamente promoção por integridade não comprovada, novos riscos e regressões operacionais, sem produzir `PASS` retroativo;
- opção 8 do menu, página autenticada, busca e exportações JSON/JUnit/SARIF validadas por smoke com cópias temporárias de uma coleta real;
- pacote portátil `0.4.0-rc.8` validado por checksum, SBOM, compile, sintaxe Bash, preflight com 18 `PASS`/0 `WARN`/0 `FAIL` e Regression Gate executado a partir do diretório extraído.
- CLI headless da RC.9 validada por 94 testes, sintaxe Bash, `--help`, `--version` e listagem sem dependência de `kubectl`;
- preflight headless limitado ao namespace `banking` concluído com 18 `PASS`, 0 `WARN` e 0 `FAIL`;
- coleta real sem prompts concluída como `COMPLETED`, com 153 checks, seis workloads, sete containers e artefatos íntegros;
- Release Gate genérico e Regression Gate idêntico executados pelos novos subcomandos com `releaseReady=true` e exports de CI/CD válidos.
- pacote portátil `0.4.0-rc.9` validado por checksum, SBOM, compile, sintaxe Bash, versão e subcomando `list` a partir do diretório extraído.

Essa evidência confirma o caminho Kubernetes genérico. Ela não substitui os gates externos em EKS, AKS e GKE.

## Evidência local `0.5.0-rc.1`

- coleta headless fresh no namespace `banking` concluída com oito exit codes zero;
- 166 checks, seis workloads, sete containers e artifact validation sem erro;
- collector registry persistido com progresso ponderado em 100%;
- 27 manifests avaliados; zero issue estrutural, com estado `PARTIAL` porque OpenAPI/server-side dry-run não foi executado;
- Operational Timeline gerada com condições de nodes e restart sanitizado;
- Node Health com três nodes e 100% de cobertura da Metrics API; Node Evidence avançada ficou explicitamente `EVIDENCE_UNAVAILABLE` na coleta sem Prometheus;
- contract validation terminal `PASS` e bundle real exportado/verificado por SHA-256;
- Release Gate genérico com 9 `PASS`, 0 `WARN`, 0 `FAIL`, 1 `N/A` e quatro formatos de saída;
- package `0.5.0-rc.1` validado por archive safety, checksum, inventário SPDX, provenance e alias provider-neutral;
- 104 testes Python e sintaxe Bash aprovados no master do lab; `shellcheck` é
  executado pelo CI porque não está instalado na VM do lab.

## Evidência local `0.5.0-rc.2`

- preflight namespaced com ConfigMap/Secret metadata e Prometheus aprovado com 21 `PASS`, 0 `WARN` e 0 `FAIL`;
- coleta fresh concluída em 32s com nove exit codes zero, 172 checks, seis workloads, sete containers e progresso em 100%;
- 15 Configuration References resolvidas sem persistir values; cinco Traffic Paths com Services e EndpointSlices saudáveis ficaram `UNKNOWN`, e não `FAIL`, porque o Istio Gateway estava fora do namespace coletado;
- database client baseado na imagem `postgres` corretamente excluído da classificação stateful; Blue-Green Readiness final `UNKNOWN`, com zero bloqueios e duas evidências externas pendentes;
- artifact validation sem erros e contract validation terminal com 14 `PASS`, 0 `FAIL` e quatro artefatos opcionais ausentes;
- Release Gate, Regression Gate, Blue-Green Readiness e Migration Gate sintéticos aprovados; Migration Gate com 17 gates, zero bloqueios e zero desconhecidos;
- smoke HTTP aprovado em 29 rotas do dashboard contra a coleta real;
- matriz local aprovada com 118 testes Python, compile, JSON Schemas e sintaxe Bash; `shellcheck` permanece delegado ao CI;
- pacote `0.5.0-rc.2` aprovado por archive safety, checksum, SBOM SPDX com 85 arquivos, provenance, compile e execução offline do alias provider-neutral.

## Evidência de qualificação `0.4.0` generic-kubernetes

- matriz local aprovada com 97 testes Python;
- preflight live do namespace `banking` com 18 `PASS`, 0 `WARN` e 0 `FAIL`;
- duas coletas live equivalentes concluídas com 166 checks, seis workloads, sete
  containers e artefatos íntegros;
- Release Gate live com 9 `PASS`, 0 `WARN`, 0 `FAIL`, 1 `N/A` e
  `releaseReady=true`;
- Regression Gate live entre scopes `banking` equivalentes com 12 gates,
  zero novos riscos, zero regressões, zero Evidence Loss e `releaseReady=true`;
- tentativa de comparar coleta cluster-wide com coleta `banking` bloqueada pelo
  gate de scope, sem publicar regressões falsas;
- pacote `0.4.0` aprovado por checksum, SBOM, compile, sintaxe Bash, versão e
  execução offline dos dois gates.
