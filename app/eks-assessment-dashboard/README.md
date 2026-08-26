# EKS Assessment Dashboard

Dashboard interativo, server-side, somente leitura e sem dependência de JavaScript. O servidor e todo o HTML são gerados com Python 3.10+ e biblioteca padrão.

No node master:

```bash
python3.11 /workspace/scripts/validation/assessment_dashboard.py \
  --root /workspace/assessment \
  --static /workspace/app/eks-assessment-dashboard/public \
  --host 0.0.0.0 --port 8765
```

Abra `http://<ip-do-master>:8765`.

O painel oferece:

- cards clicáveis de nodes, pods, todos os tipos de workload, HPA, KEDA, PVC e RabbitMQ;
- detalhe por workload e container, com requests/limits, runtime e recomendações;
- páginas de tecnologias, capacidade, Prometheus e cobertura;
- filtros `CRIT`, `WARN`, `INFO`, `PASS` e `N/A`;
- coleta completa, baseline antes/depois, comparação e exportação;
- manifests sanitizados e evidências persistidas em `assessment/`.

O coletor Prometheus exige URL HTTP/HTTPS explícita. Ausência de URL ou de séries aparece como `DISABLED`, `UNAVAILABLE`, `NO_DATA` ou `N/A`, nunca como conformidade.
