# Troubleshooting

- Preflight falhou: valide kubeconfig, contexto, RBAC, API e dependências.
- Porta ocupada: use o dashboard atual, encerre somente o processo identificado ou escolha outra porta.
- Evidence Coverage caiu: confira RBAC/API; não trate como melhoria de postura.
- Evidência externa rejeitada: confira JSON canônico, SHA-256, `validUntil` e `evidenceSource`.
- Exceção ignorada: confira `controlId`, resources, justificativa, aprovador e validade futura.
- PDF: abra **Relatório executivo / PDF** e use Imprimir → Salvar como PDF.
