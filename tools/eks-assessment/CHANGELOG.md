# Changelog

## 0.3.0-rc.1 — 2026-08-29

- dashboard portátil com autenticação temporária, progresso, cancelamento e tratamento de porta;
- assessment genérico Kubernetes/EKS read-only com Prometheus opcional;
- CIS Security schema 1.1, 25 controles universais, score por domínio e comparação;
- evidências externas com SHA-256/validade, lifecycle e exceções temporárias;
- relatório executivo imprimível, exportação JSON, checksum e SBOM SPDX;
- fixtures sanitizadas EKS, AKS e GKE para regressão offline.

Gate da RC: validar execução real em EKS, AKS e GKE antes da versão estável.
