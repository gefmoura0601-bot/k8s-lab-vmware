# Auditoria de portabilidade do EKS/Kubernetes Assessment

Data: 2026-08-27
Escopo: `tools/eks-assessment`
Objetivo: verificar se a implementação sustenta a execução documentada fora do cluster, somente por kubeconfig e APIs autorizadas, em Kubernetes on-premises, Amazon EKS e Kubernetes gerenciado não AWS.

## Resultado executivo

A implementação não depende de node `master`, SSH, Vagrant, `/workspace`, `/home`, acesso direto ao etcd ou filesystem dos nodes. Os coletores principais usam `kubectl`, AWS CLI e HTTP para Prometheus, e não foram encontrados verbos mutáveis de Kubernetes/AWS no código.

Ainda assim, a portabilidade não está pronta para release. Há quatro bloqueadores:

1. o escopo por namespace não é aplicado a todas as etapas;
2. a coleta anunciada como `Secrets metadata-only` lê objetos Secret completos;
3. snapshots brutos podem permanecer em disco após falha, timeout ou cancelamento;
4. falha de API discovery pode ser classificada como `N/A`.

O dashboard também não deve ser exposto como está: escuta em todas as interfaces, não possui autenticação efetiva e aceita uma URL Prometheus capaz de gerar requisições arbitrárias a partir do host do assessment.

## Matriz de achados

| ID | Severidade | Tema | On-premises | EKS | Gerenciado não AWS |
|---|---|---|---:|---:|---:|
| PORT-001 | Alta | Escopo por namespace incompleto | Sim | Sim | Sim |
| PORT-002 | Alta | Secrets não são realmente metadata-only | Sim | Sim | Sim |
| PORT-003 | Alta | Artefatos brutos sobrevivem a execução parcial | Sim | Sim | Sim |
| PORT-004 | Alta | Falha de discovery vira `N/A` | Sim | Sim | Sim |
| PORT-005 | Média | Falhas de RBAC viram ausência de objetos | Sim | Sim | Sim |
| PORT-006 | Alta | Dashboard exposto sem autenticação efetiva | Sim | Sim | Sim |
| PORT-007 | Alta | SSRF pela URL do Prometheus | Sim | Sim | Sim |
| PORT-008 | Média | Checklist contém topologia fixa do lab | Sim | Sim | Sim |
| PORT-009 | Média | Diretório de saída depende do layout do repositório | Sim | Sim | Sim |
| PORT-010 | Média | `--resume` não valida proveniência do snapshot | Sim | Sim | Sim |
| PORT-011 | Média | Colisão de identificador de coleta | Sim | Sim | Sim |
| PORT-012 | Média | Lookback de eventos não é aplicado | Sim | Sim | Sim |
| PORT-013 | Média | Preflight AWS valida STS, não a cobertura necessária | N/A | Sim | N/A |
| PORT-014 | Média | RBAC/IAM mínimo e pacote de release ausentes | Sim | Sim | Sim |
| PORT-015 | Baixa | Servidor HTTP sem limites de conexão/request timeout | Sim | Sim | Sim |
| PORT-016 | Média | Testes não cobrem as três plataformas nem limites de segurança | Sim | Sim | Sim |

## Achados detalhados

### PORT-001 — Escopo por namespace incompleto

Severidade: alta.

O dashboard registra `ASSESSMENT_NAMESPACE`, mas somente o scanner abrangente recebe `--namespace` (`assessment_dashboard.py:1135,1240-1241`). O baseline, discovery e inventários auxiliares continuam usando `-A` (`assessment_dashboard.py:1173-1196`; `assess-eks.sh:54-57,88-90`). O menu também não encaminha namespace ao discovery (`bin/eks-assessment.sh:174`) nem ao baseline. O preflight exige `list` cluster-wide para Pods e Deployments (`assessment-preflight.sh:97-99`).

Impacto: uma coleta solicitada para um namespace consulta e persiste dados de todo o cluster, exige RBAC maior que o informado e impede uso com uma Role namespaced mínima.

Correção:

- criar uma única representação de escopo e passá-la a todos os componentes;
- distinguir checks namespaced, cluster-scoped obrigatórios e cluster-scoped opcionais;
- fazer o preflight validar o mesmo perfil de acesso que será executado;
- gravar no metadata o escopo efetivamente usado por cada componente.

Teste necessário: fake `kubectl` que falhe se qualquer comando namespaced usar `-A` quando `--namespace` estiver ativo.

### PORT-002 — Secrets não são realmente metadata-only

Severidade: alta.

O discovery executa `kubectl get secret ... -o json` e só depois reduz a resposta com `jq` (`eks-cluster-discovery.sh:183-184`). O scanner também inclui Secrets em coleta profunda (`eks_comprehensive_assessment.py:94-95,538-553`) e remove os valores apenas antes de persistir (`eks_comprehensive_assessment.py:309-318`).

Impacto: os valores de Secret atravessam API, memória e pipes do processo. Isso contradiz `eks-cluster-discovery.sh:6`, `README.md:140` e o campo de segurança emitido em `eks_comprehensive_assessment.py:1163`. Também exige permissão sensível de `list secrets`.

Correção:

- remover Secrets da coleta padrão;
- se metadados forem indispensáveis, usar `PartialObjectMetadataList` com content negotiation e não solicitar `data`;
- tornar inventário de nomes/chaves uma opção separada, explicitamente sensível, desabilitada por padrão;
- não incluir `list secrets` no RBAC mínimo padrão.

Teste necessário: servidor Kubernetes falso que rejeite qualquer request de Secret sem `Accept` de PartialObjectMetadata.

### PORT-003 — Artefatos brutos sobrevivem a execução parcial

Severidade: alta.

O baseline grava Pods, workloads e Events brutos diretamente no diretório final (`assess-eks.sh:53-57`). A sanitização só ocorre perto do fim do scanner abrangente (`eks_comprehensive_assessment.py:1122-1138`). Cancelamento e timeout preservam a coleta parcial por design.

Impacto: uma interrupção entre baseline e sanitização deixa valores arbitrários de `env`, mensagens de Events, UIDs e outros dados operacionais em disco. O validador de artefatos só roda depois do scanner e não protege esse intervalo.

Correção:

- sanitizar durante a captura, antes da primeira escrita persistente;
- usar diretório temporário com permissões `0700` e publicação atômica somente após validação;
- executar sanitização/validação também no caminho de cancelamento, timeout e exceção;
- marcar artefatos parciais inseguros como não exportáveis.

Teste necessário: cancelar deliberadamente após cada componente e procurar valores-canário em todos os arquivos.

### PORT-004 — Falha de API discovery vira `N/A`

Severidade: alta.

Quando `kubectl api-resources` falha, `api_resources()` devolve conjuntos vazios e estado `UNAVAILABLE` (`eks_comprehensive_assessment.py:420-434`). Em seguida, cada recurso ausente nesses conjuntos é gravado como `N/A`, com motivo “API resource not served” (`eks_comprehensive_assessment.py:515-536`).

Impacto: RBAC insuficiente, timeout ou indisponibilidade da API pode ser apresentado como recurso comprovadamente não aplicável, contrariando a semântica documentada no `README.md:11`.

Correção: se discovery estiver `UNAVAILABLE`, propagar `UNKNOWN`/`PARTIAL` para recursos não comprovados; usar `N/A` apenas quando o discovery foi bem-sucedido e a API realmente não foi servida.

Teste necessário: simular timeout e `Forbidden` em `api-resources` e garantir zero classificações `N/A` derivadas dessa falha.

### PORT-005 — Falhas de RBAC viram ausência de objetos

Severidade: média.

O baseline substitui falha ao listar NetworkPolicy ou PDB por `{"items":[]}` (`assess-eks.sh:88-89`) e depois reporta que não existem objetos (`assess-eks.sh:99-103`).

Impacto: `Forbidden`, timeout e API ausente ficam indistinguíveis de uma lista vazia válida.

Correção: persistir estado de cobertura e classificar falha como `UNKNOWN` ou `PARTIAL`; somente uma resposta válida vazia pode gerar recomendação por ausência.

### PORT-006 — Dashboard exposto sem autenticação efetiva

Severidade: alta.

O dashboard e o menu usam `0.0.0.0` por padrão (`assessment_dashboard.py:1311`; `bin/eks-assessment.sh:294`). As rotas GET expõem inventário e exportação sem autenticação (`assessment_dashboard.py:1049-1072`). O token usado para ações é entregue no próprio HTML (`assessment_dashboard.py:1034`) e, portanto, funciona como proteção contra requisição cega, não como autenticação de operador.

Impacto: qualquer cliente com acesso à porta pode ler evidências, iniciar coletas, consumir quota de APIs e cancelar execução.

Correção:

- usar `127.0.0.1` como padrão;
- exigir autenticação real quando houver bind externo;
- separar autenticação de CSRF;
- proteger também todas as rotas GET de dados;
- documentar reverse proxy/TLS ou túnel aprovado.

### PORT-007 — SSRF pela URL do Prometheus

Severidade: alta.

O formulário aceita URL Prometheus e o host do assessment faz as requisições (`assessment_dashboard.py:1126,1202-1211`). A validação rejeita credenciais, query e fragment, mas aceita qualquer host HTTP/HTTPS (`prometheus_telemetry.py:320-330`).

Impacto: um usuário da interface pode fazer o host consultar endpoints internos, loopback, link-local ou metadata de nuvem. Redirects também precisam ser considerados.

Correção:

- por padrão, aceitar URL apenas por configuração local de inicialização;
- se a entrada web for mantida, usar allowlist explícita e bloquear loopback, link-local, metadata e redes não aprovadas;
- validar novamente cada redirect e o endereço após resolução DNS;
- adicionar política de proxy e CA explícita.

### PORT-008 — Checklist contém topologia fixa do lab

Severidade: média.

`checklist-eks-topology.sh` assume namespaces e nomes específicos: `argocd`, `monitoring`, `istio-system`, `databases`, `messaging`, `postgres`, `rabbitmq`, `kube-prometheus-stack-*` e outros (`checklist-eks-topology.sh:42-148`). O discovery também contém seções fixas para `nginx-lab`, `argocd` e `monitoring` (`eks-cluster-discovery.sh:173-195`).

Impacto: clusters válidos com nomes/topologias diferentes recebem alertas do lab ou cobertura enganosa.

Correção: mover o checklist atual para um perfil explícito `lab-vmware`; no perfil genérico, descobrir operadores por CRD/labels/owner references e registrar `N/A` quando não aplicável.

### PORT-009 — Diretório de saída depende do layout do repositório

Severidade: média.

O menu calcula `REPOSITORY_ROOT` como dois níveis acima do pacote e usa esse caminho para a saída padrão (`bin/eks-assessment.sh:5-7`). Em um pacote instalado como `/opt/eks-assessment`, isso pode resultar em `/assessment`, normalmente sem permissão. Os smokes repetem a mesma premissa.

Correção: tornar `--output-dir`/`ASSESSMENT_ROOT` explícito para execução não interativa e usar, como fallback, `$PWD/assessment` ou um diretório XDG do usuário; não inferir raiz de repositório em pacote instalado.

### PORT-010 — `--resume` não valida proveniência

Severidade: média.

O resume reutiliza arquivos se eles apenas contiverem uma lista JSON (`eks_comprehensive_assessment.py:448-457,526-531`). Não valida contexto, cluster UID, escopo, schema, versão, horário ou hash da coleta.

Impacto: uma execução pode combinar snapshots de outro cluster, namespace ou versão da ferramenta.

Correção: criar manifest de proveniência assinado por hashes locais, contendo schema, tool version, context, cluster UID, namespace, timestamp e parâmetros; recusar mismatch.

### PORT-011 — Colisão de identificador de coleta

Severidade: média.

Menu e dashboard geram IDs com precisão de segundos e criam o diretório sem estratégia de colisão (`bin/eks-assessment.sh:158-159`; `assessment_dashboard.py:1150-1153`).

Correção: adicionar sufixo aleatório/UUID e criar o diretório atomicamente.

### PORT-012 — Lookback de eventos não é aplicado

Severidade: média.

O discovery aceita `--since` e anuncia lookback, mas o comando apenas ordena todos os Events por timestamp (`eks-cluster-discovery.sh:9,24,198`).

Correção: filtrar por `eventTime`/`lastTimestamp` no cliente, registrar a janela efetiva e tratar timestamps ausentes como cobertura parcial.

### PORT-013 — Preflight AWS insuficiente

Severidade: média.

O preflight chama apenas `aws sts get-caller-identity` e declara credenciais utilizáveis (`assessment-preflight.sh:110-119`). Isso não comprova região nem permissões para EKS, EC2, Auto Scaling, ECR ou checks opcionais de conta.

Correção: construir uma matriz de capacidades por operação, usando chamadas read-only baratas e classificando cada família como `AVAILABLE`, `PARTIAL` ou `UNKNOWN`.

### PORT-014 — Artefatos de distribuição e least privilege ausentes

Severidade: média.

Não existem no pacote exemplos versionados de Role/ClusterRole/RoleBinding, política IAM mínima, manifesto de versão, instalador, checksum ou formato de release. O README pede apenas “RBAC somente leitura”, sem enumerar recursos e escopos.

Correção: publicar perfis separados: namespaced mínimo, cluster-wide Kubernetes, EKS básico e account-security opcional. Gerar pacote versionado com checksum e SBOM.

### PORT-015 — Servidor HTTP sem limites suficientes

Severidade: baixa.

`ThreadingHTTPServer` não configura timeout por conexão nem limite de threads (`assessment_dashboard.py:1315-1327`). O limite de `Content-Length` não impede clientes lentos de manter threads abertas.

Correção: timeout de socket, limite de concorrência, tamanho máximo aplicado antes da leitura e reverse proxy para exposição remota.

### PORT-016 — Lacunas na suíte de testes

Severidade: média.

Os testes atuais cobrem regras, redaction pontual, orçamento, fingerprint e cancelamento. Não cobrem:

- execução real fora do cluster com kubeconfig;
- on-premises, EKS e gerenciado não AWS;
- RBAC namespaced mínimo;
- API discovery indisponível;
- interrupção antes da sanitização;
- garantia de não leitura de Secret data;
- autenticação do dashboard e SSRF;
- instalação do pacote fora do repositório;
- colisão e proveniência de resume.

## Confirmações positivas

- Nenhuma referência executável a `/workspace`, `/home`, Vagrant, SSH ou etcd foi encontrada.
- A única leitura de `/proc` possui fallback para `ps` (`bin/eks-assessment.sh:239-244`).
- Chamadas subprocess em Python usam vetores de argumentos; não foi encontrado `shell=True`.
- Não foram encontrados comandos `kubectl apply/patch/delete/create` nem operações mutáveis AWS.
- AWS ausente e ambiente não EKS possuem caminho explícito de não aplicabilidade.
- Prometheus é opcional e ausência de séries não é tratada como conformidade.
- Há budgets de requests, duração, bytes, retry/backoff e cancelamento de árvore de processos.

## Validações executadas

- `python3 -m unittest discover -s tools/eks-assessment/tests -p 'test_*.py' -v` no WSL: 21 testes aprovados.
- `bash -n` em todos os scripts de `bin`, `src` e `tests`: aprovado.
- ShellCheck: não disponível no WSL desta estação; validação não executada.
- Não foi executado assessment contra cluster externo nesta etapa; isso pertence à validação posterior em três plataformas.

## Ordem recomendada de correção

1. PORT-002 e PORT-003: eliminar leitura/persistência sensível antes de qualquer teste externo.
2. PORT-001, PORT-004 e PORT-005: tornar escopo e semântica de cobertura confiáveis.
3. PORT-006 e PORT-007: restringir dashboard e origem Prometheus.
4. PORT-008, PORT-009, PORT-010, PORT-011, PORT-012 e PORT-013.
5. PORT-014 e PORT-016: publicar permissões mínimas, matriz de cenários e pacote de release.
6. PORT-015 como hardening final antes de exposição transacional.

## Critério de saída do item 1

O item 1 pode ser considerado concluído quando PORT-001 a PORT-013 tiverem correção implementada e teste automatizado, e quando uma nova busca confirmar ausência de dependências de topologia/localização. PORT-014 a PORT-016 são gates para os itens de permissões, validação transacional e release.

## Estado da remediação

Atualização em 2026-08-27:

- PORT-001 a PORT-013: corrigidos na implementação. O namespace é propagado, Secret/ConfigMap não são solicitados, snapshots são sanitizados antes da persistência, falhas de cobertura não viram `N/A`, dashboard é loopback-only, destinos Prometheus são validados, topologia do lab é opt-in, resume valida proveniência e hashes, IDs são aleatórios e o lookback de eventos é aplicado.
- PORT-014: corrigido com exemplos Kubernetes namespaced/cluster e políticas IAM AWS estritamente read-only em `tools/eks-assessment/deploy`.
- PORT-015: hardening implementado com timeout global, timeout por componente, cancelamento de grupo de processos, timeout de socket do dashboard, limites de API/resposta e headers defensivos. O teste transacional de carga continua sendo gate operacional antes da release.
- PORT-016: cobertura automatizada ampliada para 28 testes, incluindo não coleta de recursos sensíveis, falha de discovery, sanitização, proveniência, loopback e SSRF/allowlist. A matriz real on-premises/EKS/gerenciado não AWS continua sendo gate operacional.
- Release: versão `0.2.0`, documentação e gerador de pacote com checksum SHA-256 e SBOM SPDX adicionados. A publicação do artefato versionado depende da conclusão dos gates operacionais acima.

Portanto, a auditoria e a remediação estática do item 1 estão concluídas; a prontidão de release ainda depende das validações reais dos itens 3, 4 e 6 da ordem recomendada pelo operador.
