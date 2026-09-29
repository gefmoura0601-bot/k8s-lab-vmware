# Regression Gate offline

O `Regression Gate` compara duas coletas sanitizadas do mesmo cluster sem executar novas chamadas ao Kubernetes ou ao Cloud Provider. O objetivo é impedir que uma mudança introduza regressões invisíveis entre um baseline e a coleta atual.

Ele não substitui testes funcionais, validação de carga, change review ou certificação de segurança.

## Execução

```bash
python3 src/regression_validation.py \
  --before assessment/<coleta-anterior> \
  --after assessment/<coleta-atual> \
  --profile standard
```

Também é possível usar a opção 8 do menu ou abrir **Governança → Regression Gate** no dashboard. A seleção das duas coletas e do policy profile é sempre explícita.

O exit code é:

- `0`: todos os gates obrigatórios estão `PASS` e `releaseReady=true`;
- `1`: a comparação foi concluída, mas existe `WARN` ou `FAIL` obrigatório;
- `2`: argumentos, policy ou diretórios são inválidos.

## Policy profiles

A policy padrão está em `data/assessment-policy.json`, possui `schemaVersion` e contém dois profiles:

- `standard`: bloqueia somente regressões novas, sem exigir a correção imediata de riscos legados;
- `strict`: bloqueia regressões e também exige ausência de `CRIT`, `WARN`, `UNKNOWN` e `PARTIAL` nos findings atuais.

Uma policy alternativa pode ser informada com `--policy <arquivo>`. No menu e dashboard, defina `ASSESSMENT_POLICY_FILE` antes de iniciar a sessão. Campos desconhecidos, ausentes, negativos ou com tipo inválido interrompem a validação; um typo nunca reduz silenciosamente o rigor. Os requisitos de mesmo cluster, estado `COMPLETED`, integridade, CIS e Operational Evidence não podem ser desabilitados por uma policy customizada.

Os thresholds controlam riscos novos, regressões de severidade, Evidence Loss, CIS, Node Health, Manifest Quality, lifecycle, qualidade dos findings e aumento percentual de duração, API requests, response bytes e peak RSS.

## Gates

O relatório avalia:

1. mesmo cluster, usando um sinal disponível sem persistir seu valor;
2. estado terminal `COMPLETED` das duas coletas;
3. integridade e sanitização dos artefatos;
4. novos `CRIT`/`WARN`, piora de severidade e perda de evidência por fingerprint estável;
5. regressões de Posture Score, Evidence Coverage e controles CIS;
6. piora de Node Health e cobertura da Metrics API;
7. aumento de problemas em manifests, versões sem suporte e version skew;
8. duplicidades, severidades conflitantes ou `PASS` com baixa confiança;
9. regressão do impacto medido da coleta.

Evidência obrigatória ausente produz `WARN` ou `FAIL`, conforme a policy; nunca produz `PASS`.

## Artefatos para CI/CD

Os três arquivos são gravados atomicamente dentro da coleta atual:

- `regression-validation.json`: contrato completo e sanitizado;
- `regression-validation.junit.xml`: um test case por gate obrigatório;
- `regression-validation.sarif.json`: resultados bloqueantes no formato SARIF 2.1.0.

O JSON contém no máximo 500 mudanças detalhadas e registra quando houve truncamento. Ele também grava SHA-256 da policy e do conjunto de artefatos de cada entrada, vinculando o resultado exatamente às evidências comparadas. Mensagens livres de logs, payloads cloud brutos, credenciais e valores de cluster usados na comparação de identidade não são copiados.

## Escopo atual

A implementação da `0.4.0-rc.8` é provider-neutral e está validada no caminho Kubernetes genérico. EKS, AKS e GKE continuam suportados pelos contratos existentes, mas a qualificação real nesses providers permanece adiada e não é inferida por este gate.
