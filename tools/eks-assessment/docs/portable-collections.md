# Coletas portáteis e contratos versionados

## Validação

`assessment_contracts.py` implementa o subset de JSON Schema usado pelo projeto,
sem dependência de packages externos. Os schemas ficam em `data/schemas/` e
validam invariantes essenciais como `readOnly`, zero mutações, estado terminal,
identidade, summaries e tipos. Coletas parciais continuam inspecionáveis; uma
coleta marcada `COMPLETED` exige todos os artefatos terminais de sua versão.

```bash
bin/kubernetes-assessment validate --root ./assessment --collection <id>
```

## Bundle

`bundle export` executa contract validation e artifact validation antes de
empacotar. O archive contém uma raiz única e `bundle-manifest.json`, com tamanho
e SHA-256 de cada arquivo. Logs, PID files, locks, temporários e links não entram
no bundle.

```bash
bin/kubernetes-assessment bundle export --root ./assessment \
  --collection <id> --output ./collection.tar.gz
bin/kubernetes-assessment bundle verify --bundle ./collection.tar.gz
bin/kubernetes-assessment bundle import --root ./assessment-importado \
  --bundle ./collection.tar.gz
```

`verify` rejeita caminho absoluto, traversal, links, arquivo ausente/extra,
tamanho ou digest divergente. `import` nunca sobrescreve uma coleta existente e
extrai cada arquivo manualmente depois da verificação.

## Retenção

```bash
bin/kubernetes-assessment prune --root ./assessment --older-than 30d
bin/kubernetes-assessment prune --root ./assessment --older-than 30d --confirm
```

Sem `--confirm`, o resultado é somente um dry-run em JSON. O target precisa ser
filho direto do root, ter prefixo `eks-` e metadata legível quando disponível.
Baselines são preservados; `--include-baselines` deve ser explícito para incluí-los.

## Limites

- o bundle é sanitizado para transporte de evidência, não backup de logs brutos;
- tamanho descompactado máximo: 2 GiB;
- máximo de 10.000 arquivos;
- importação não torna uma coleta antiga compatível com funcionalidades que não
  existiam na versão que a produziu.
