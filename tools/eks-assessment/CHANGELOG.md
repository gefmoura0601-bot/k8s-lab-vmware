# Changelog

## 0.4.0-rc.2 — 2026-08-30

- redesenha o menu terminal com identidade Kubernetes, contexto, versão, total de coletas e estado read-only;
- aplica ao dashboard uma paleta enterprise baseada no azul oficial `#326CE5`;
- incorpora o SVG oficial do Kubernetes mantido no CNCF artwork;
- melhora hierarquia visual, contraste, navegação, cards, tabelas, formulários e responsividade;
- preserva saída sem ANSI quando redirecionada ou quando `NO_COLOR` está definido.

## 0.4.0-rc.1 — 2026-08-30

- adiciona `Events & Diagnostics`, `Versions & Lifecycle` e `Manifest Quality`;
- evolui capacidade para `Container Tuning` orientado por telemetria;
- adiciona engine portátil de `Best Practices` para Kubernetes, EKS, AKS e GKE;
- adiciona logs opcionais com opt-in, targets explícitos, limite e redaction;
- publica o artefato exportável `operational-insights.json`.

## 0.3.0-rc.1 — 2026-08-29

- dashboard portátil com autenticação temporária, progresso, cancelamento e tratamento de porta;
- assessment genérico Kubernetes/EKS read-only com Prometheus opcional;
- CIS Security schema 1.1, 25 controles universais, score por domínio e comparação;
- evidências externas com SHA-256/validade, lifecycle e exceções temporárias;
- relatório executivo imprimível, exportação JSON, checksum e SBOM SPDX;
- fixtures sanitizadas EKS, AKS e GKE para regressão offline.

Gate da RC: validar execução real em EKS, AKS e GKE antes da versão estável.
