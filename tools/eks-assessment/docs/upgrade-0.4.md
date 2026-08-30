# Upgrade para 0.4

Não há migração destrutiva. Coleções `0.3` continuam visíveis; as novas abas informarão ausência de `operational-insights.json` até uma nova coleta.

Logs continuam desabilitados por padrão. Não habilite `ASSESSMENT_INCLUDE_LOGS=1` sem revisar targets, retenção e política de dados do ambiente.

Após extrair o pacote, execute:

```bash
bin/eks-assessment.sh --version
bash src/assessment-preflight.sh
```
