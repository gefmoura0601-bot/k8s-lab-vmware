# Upgrade para 0.3.0-rc.1

1. Preserve o diretório de coletas.
2. Substitua somente o pacote da ferramenta.
3. Execute `bin/eks-assessment.sh --version` e o preflight.
4. Coletas antigas continuam legíveis; gere nova coleta para CIS schema 1.1.
5. Revise os contratos opcionais `cis-external-evidence.json`, `cis-remediation-state.json` e `cis-exceptions.json`.

Rollback: restaure o pacote anterior. Os artefatos de coleta não são alterados pelo rollback.
