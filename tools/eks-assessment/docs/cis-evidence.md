# Evidências externas e lifecycle CIS

Arquivos opcionais no diretório da coleta não alteram o cluster.

`cis-external-evidence.json` aceita `NodeEvidence`, `ControlPlaneEvidence`, `ManualEvidence` e `CloudProviderAPI`. Cada entrada exige validade futura e SHA-256 do `payload` serializado como JSON canônico (`sort_keys`, separadores `,` e `:`):

```json
{"evidence":[{"controlId":"cis.k8s.node.kubelet-configuration","evidenceSource":"NodeEvidence","status":"PASS","payload":{"anonymousAuth":false},"sha256":"<sha256>","reviewedBy":"security","validUntil":"2099-01-01T00:00:00Z"}]}
```

Hash inválido, evidência vencida ou origem desconhecida são rejeitados e registrados em `summary.evidenceErrors`. Evidência ausente nunca vira `PASS`.

`cis-remediation-state.json` adiciona metadados operacionais sem alterar findings:

```json
{"controls":{"cis.k8s.rbac.wildcards":{"owner":"platform","dueDate":"2026-12-31","state":"IN_PROGRESS","ticket":"SEC-123","justification":"remediação planejada","riskAcceptedUntil":"2026-10-31"}}}
```

Campos suportados: `owner`, `dueDate`, `state`, `ticket`, `justification` e `riskAcceptedUntil`. Não inclua credenciais, Secrets ou dados pessoais.
