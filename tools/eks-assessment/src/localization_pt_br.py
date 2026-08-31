#!/usr/bin/env python3
"""Localização pt-BR dos textos apresentados pela avaliação.

Identificadores técnicos, nomes de recursos, métricas, APIs e estados usados por
automação permanecem inalterados. Somente rótulos e textos destinados a pessoas
são traduzidos.
"""
from __future__ import annotations

import re
from typing import Any


SEVERITY_LABELS = {
    "CRIT": "CRÍTICO",
    "WARN": "ALERTA",
    "UNKNOWN": "DESCONHECIDO",
    "PARTIAL": "PARCIAL",
    "INFO": "INFORMATIVO",
    "PASS": "CONFORME",
    "N/A": "N/A",
    "FAIL": "FALHA",
}

STATE_LABELS = {
    "OPEN": "ABERTO",
    "REVIEW": "EM REVISÃO",
    "COMPLIANT": "CONFORME",
    "NOT_APPLICABLE": "NÃO APLICÁVEL",
    "APPLICABLE": "APLICÁVEL",
    "UNKNOWN": "DESCONHECIDO",
    "PARTIAL": "PARCIAL",
    "AVAILABLE": "DISPONÍVEL",
    "UNAVAILABLE": "INDISPONÍVEL",
    "DISABLED": "DESATIVADO",
    "NO_DATA": "SEM DADOS",
    "RUNNING": "EM EXECUÇÃO",
    "COMPLETED": "CONCLUÍDO",
    "FAILED": "FALHOU",
    "CANCELLED": "CANCELADO",
    "TIMED_OUT": "TEMPO ESGOTADO",
    "DETECTED": "DETECTADO",
    "HEALTHY": "SAUDÁVEL",
    "DEGRADED": "DEGRADADO",
    "HIGH": "ALTA",
    "MEDIUM": "MÉDIA",
    "LOW": "BAIXA",
    "HISTORICAL": "HISTÓRICA",
}

CATEGORY_LABELS = {
    "Assessment": "Avaliação",
    "Autoscaling": "Escalonamento automático",
    "Availability": "Disponibilidade",
    "Capacity": "Capacidade",
    "Cost": "Custos",
    "Coverage": "Cobertura",
    "DR": "Recuperação de desastres",
    "EKS": "EKS",
    "Extensions": "Extensões",
    "Governance": "Governança",
    "Health": "Saúde",
    "Network": "Rede",
    "Nodes": "Nodes",
    "Observability": "Observabilidade",
    "PodHealth": "Saúde dos Pods",
    "Reliability": "Confiabilidade",
    "Resources": "Recursos",
    "Runtime": "Runtime",
    "Security": "Segurança",
    "Storage": "Armazenamento",
    "SupplyChain": "Cadeia de suprimentos",
    "Technology": "Tecnologia",
    "Topology": "Topologia",
    "Upgrade": "Atualização",
}

CHECK_LABELS = {
    ".NET diagnostics": "Diagnósticos do .NET",
    ".NET GC/container memory": "Memória do GC/container .NET",
    "Admission policy reports": "Relatórios de Admission Policy",
    "Admission webhook reliability": "Confiabilidade dos Admission Webhooks",
    "Aggregated APIService health": "Saúde dos APIServices agregados",
    "Amazon EKS applicability": "Aplicabilidade ao Amazon EKS",
    "API collection budget": "Orçamento de coleta da API",
    "AWS/EKS assessment artifact": "Artefato da avaliação AWS/EKS",
    "Backup and restore evidence": "Evidências de backup e restauração",
    "cluster-admin bindings": "Vínculos com cluster-admin",
    "Default StorageClass": "StorageClass padrão",
    "Deep resource collection": "Coleta detalhada de recursos",
    "Deprecated API objects": "Objetos de API obsoleta",
    "Dynamic scaling": "Escalonamento dinâmico",
    "Failure domains": "Domínios de falha",
    "Finished Job cleanup": "Limpeza de Jobs concluídos",
    "High-risk RBAC permissions": "Permissões RBAC de alto risco",
    "Host namespace sharing": "Compartilhamento de Host Namespaces",
    "hostPath volumes": "Volumes hostPath",
    "HPA coverage": "Cobertura de HPA",
    "HPA semantic health": "Saúde semântica do HPA",
    "Identical readiness and liveness probes": "Readiness e Liveness Probes idênticas",
    "Image digest": "Digest da imagem",
    "Image SBOM/signature/vulnerability evidence": "Evidências de SBOM, assinatura e vulnerabilidades da imagem",
    "Ingress backend references": "Referências de backend do Ingress",
    "Ingress TLS": "TLS do Ingress",
    "Init container resources": "Recursos dos Init Containers",
    "Java heap sizing": "Dimensionamento de heap Java",
    "Java OOM behavior": "Comportamento de OOM do Java",
    "KEDA ScaledObject health": "Saúde dos KEDA ScaledObjects",
    "Kubelet version consistency": "Consistência da versão do kubelet",
    "LimitRange": "LimitRange",
    "Linux capabilities": "Linux Capabilities",
    "Liveness probe": "Liveness Probe",
    "Mutable image": "Imagem mutável",
    "Namespace default deny": "Default deny do Namespace",
    "NetworkPolicy coverage": "Cobertura de NetworkPolicy",
    "Node inventory": "Inventário de Nodes",
    "Node pressure conditions": "Condições de pressão dos Nodes",
    "Node requested capacity": "Capacidade solicitada nos Nodes",
    "Nodes Ready": "Nodes Ready",
    "Non-root enforcement": "Execução obrigatória sem root",
    "PDB eviction semantics": "Semântica de eviction do PDB",
    "Pod health": "Saúde dos Pods",
    "Pod Security enforcement": "Aplicação de Pod Security",
    "Pod state": "Estado do Pod",
    "Privilege escalation": "Escalonamento de privilégios",
    "Privileged container": "Container privilegiado",
    "Prometheus capacity analysis": "Análise de capacidade pelo Prometheus",
    "Prometheus sizing proposal": "Proposta de dimensionamento pelo Prometheus",
    "PVC attachment evidence": "Evidências de vínculo dos PVCs",
    "PVC health": "Saúde dos PVCs",
    "RabbitMQ memory watermark": "Limite de memória do RabbitMQ",
    "RBAC wildcards": "Wildcards de RBAC",
    "Readiness probe": "Readiness Probe",
    "Read-only root filesystem": "Filesystem raiz somente leitura",
    "Released or failed PersistentVolumes": "PersistentVolumes liberados ou com falha",
    "Replica redundancy": "Redundância de réplicas",
    "Replica spreading": "Distribuição de réplicas",
    "Requested-capacity distribution": "Distribuição da capacidade solicitada",
    "Resource limits": "Limits de recursos",
    "Resource requests": "Requests de recursos",
    "Root user": "Usuário root",
    "Seccomp": "Seccomp",
    "Service account": "ServiceAccount",
    "Service ready endpoints": "Endpoints Ready do Service",
    "Spot and On-Demand distribution": "Distribuição entre Spot e On-Demand",
    "Spot interruption handling evidence": "Evidências de tratamento de interrupção Spot",
    "StatefulSet governing service": "Service principal do StatefulSet",
    "Topology spread semantics": "Semântica de Topology Spread",
    "Unsafe sysctls": "Sysctls inseguros",
    "VPA and HPA compatibility": "Compatibilidade entre VPA e HPA",
    "Workload NetworkPolicy isolation": "Isolamento dos Workloads por NetworkPolicy",
}

TEXT_LABELS = {
    "none": "nenhum",
    "unknown": "desconhecido",
    "condition absent": "condição ausente",
    "all nodes Ready": "todos os Nodes estão Ready",
    "all reported replicas available": "todas as réplicas informadas estão disponíveis",
    "no policies found": "nenhuma policy encontrada",
    "no PDB found": "nenhum PDB encontrado",
    "no latest/untagged running image": "nenhuma imagem em execução com tag latest ou sem tag",
    "no namespace enforce label found": "nenhum label de enforcement encontrado nos Namespaces",
    "all running containers define requests and limits": "todos os containers em execução definem requests e limits",
    "AWS/EKS collector": "Coletor AWS/EKS",
    "collector failed; inspect aws-eks-assessment.log": "o coletor falhou; verifique aws-eks-assessment.log",
    "Python 3.10+ or aws_eks_assessment.py unavailable": "Python 3.10+ ou aws_eks_assessment.py indisponível",
    "allocatable or requests unavailable": "allocatable ou requests indisponíveis",
    "API resource is not served by this cluster.": "O recurso de API não é fornecido por este cluster.",
    "Capabilities do not explicitly drop ALL.": "As Capabilities não removem explicitamente ALL.",
    "ExitOnOutOfMemoryError was not detected.": "ExitOnOutOfMemoryError não foi detectado.",
    "Init container lacks a complete CPU/memory request.": "O Init Container não possui requests completos de CPU/memória.",
    "Namespace has workloads but no NetworkPolicy object.": "O Namespace possui Workloads, mas não possui NetworkPolicy.",
    "No EKS cluster name could be derived from explicit configuration or kube context.": "Não foi possível obter o nome do cluster EKS pela configuração explícita ou pelo contexto Kubernetes.",
    "No explicit GC heap hard-limit setting is visible.": "Nenhuma configuração explícita de limite rígido do heap do GC está visível.",
    "No explicit memory watermark is visible in approved runtime variables.": "Nenhum limite explícito de memória está visível nas variáveis de runtime permitidas.",
    "No explicit scaleUp/scaleDown behavior.": "Nenhum comportamento explícito de scaleUp/scaleDown.",
    "No HPA or KEDA ScaledObject targets this workload.": "Nenhum HPA ou KEDA ScaledObject está associado a este Workload.",
    "No Ingress resources detected.": "Nenhum recurso Ingress foi detectado.",
    "No matching PDB was found.": "Nenhum PDB correspondente foi encontrado.",
    "Only image references were assessed; registry contents and signatures were not inspected.": "Somente as referências das imagens foram avaliadas; o conteúdo do registry e as assinaturas não foram inspecionados.",
    "Pod does not explicitly select RuntimeDefault/Localhost.": "O Pod não seleciona explicitamente RuntimeDefault/Localhost.",
    "Readiness and liveness definitions are identical.": "As definições de Readiness e Liveness são idênticas.",
    "Readiness probe is absent.": "A Readiness Probe está ausente.",
    "Liveness probe is absent.": "A Liveness Probe está ausente.",
    "readOnlyRootFilesystem is not true.": "readOnlyRootFilesystem não está definido como true.",
    "runAsNonRoot is not explicitly true.": "runAsNonRoot não está explicitamente definido como true.",
    "Runtime diagnostics are enabled.": "Os diagnósticos de runtime estão habilitados.",
    "Runtime options do not expose -Xmx or MaxRAMPercentage.": "As opções de runtime não apresentam -Xmx nem MaxRAMPercentage.",
    "ttlSecondsAfterFinished is absent.": "ttlSecondsAfterFinished está ausente.",
    "Uses the default ServiceAccount.": "Utiliza o ServiceAccount padrão.",
    "allowPrivilegeEscalation is not explicitly false.": "allowPrivilegeEscalation não está explicitamente definido como false.",
}

RECOMMENDATION_LABELS = {
    "Add a dependency-aware readiness probe so traffic is sent only to ready instances.": "Adicione uma Readiness Probe que considere as dependências, para que o tráfego seja enviado somente às instâncias Ready.",
    "Add only when a reliable deadlock/failure signal exists; avoid restart loops.": "Adicione somente quando houver um sinal confiável de deadlock ou falha; evite ciclos de reinicialização.",
    "Allow at least one voluntary disruption when quorum and availability permit.": "Permita ao menos uma interrupção voluntária quando o quorum e a disponibilidade permitirem.",
    "Avoid competing VPA updates and utilization-based HPA control on the same resources.": "Evite atualizações do VPA concorrendo com o controle do HPA baseado em utilização nos mesmos recursos.",
    "Configure TTL cleanup or a controlled history-retention process.": "Configure limpeza por TTL ou um processo controlado de retenção do histórico.",
    "Confirm .NET runtime version and use memory p95/p99 to decide whether GCHeapHardLimitPercent is needed while preserving native-memory headroom.": "Confirme a versão do runtime .NET e use p95/p99 de memória para decidir se GCHeapHardLimitPercent é necessário, preservando margem para memória nativa.",
    "Confirm scale-to-zero, retention and owner intent before deleting any unreferenced PVC.": "Confirme scale-to-zero, retenção e intenção do responsável antes de excluir qualquer PVC sem referência.",
    "Correlate p95 usage and disruption headroom before resizing nodes or requests; low requests alone do not prove waste.": "Correlacione o uso p95 e a margem para interrupções antes de redimensionar Nodes ou requests; requests baixos, isoladamente, não comprovam desperdício.",
    "Create or correct every referenced Service before routing traffic.": "Crie ou corrija todos os Services referenciados antes de rotear tráfego.",
    "Define a PDB aligned with replica count and maintenance/eviction requirements.": "Defina um PDB alinhado à quantidade de réplicas e aos requisitos de manutenção e eviction.",
    "Define requests from p90 usage plus validated headroom; requests drive scheduling and HPA utilization.": "Defina requests a partir do uso p90 com margem validada; requests orientam o agendamento e a utilização do HPA.",
    "Drop ALL and add back only capabilities proven necessary.": "Remova ALL e adicione novamente somente as Capabilities comprovadamente necessárias.",
    "Enable it when compatible and mount explicit writable paths.": "Habilite quando houver compatibilidade e monte explicitamente os caminhos graváveis.",
    "Enforce a version-pinned Pod Security Standard or an equivalent tested admission policy.": "Aplique um Pod Security Standard com versão fixada ou uma Admission Policy equivalente e testada.",
    "Evaluate fail-fast restart behavior; enable heap dumps only with bounded persistent storage and a secure collection process.": "Avalie o comportamento fail-fast de reinicialização; habilite heap dumps somente com armazenamento persistente limitado e processo seguro de coleta.",
    "For high-assurance releases, pin and attest an immutable digest.": "Para releases de alta garantia, fixe e ateste um digest imutável.",
    "For multi-tenant/shared clusters, define quotas consistent with capacity and ownership.": "Para clusters multi-tenant ou compartilhados, defina quotas consistentes com a capacidade e a responsabilidade.",
    "For production, distribute nodes and replicas across independent failure domains.": "Em produção, distribua Nodes e réplicas entre domínios de falha independentes.",
    "Inspect StorageClass, CSI health, capacity, access modes and topology constraints.": "Verifique StorageClass, saúde do CSI, capacidade, modos de acesso e restrições de topologia.",
    "Keep kubelets within supported skew and align node pools during upgrades.": "Mantenha os kubelets dentro do version skew suportado e alinhe os Node Pools durante atualizações.",
    "Keep scheduling headroom for disruption, system daemons and traffic spikes.": "Mantenha margem de agendamento para interrupções, DaemonSets de sistema e picos de tráfego.",
    "Keep webhook endpoints redundant, fast, TLS-valid and fail according to documented risk.": "Mantenha os endpoints dos Webhooks redundantes, rápidos, com TLS válido e comportamento de falha conforme o risco documentado.",
    "Maintain one intentional default StorageClass and validate topology/expansion settings.": "Mantenha uma StorageClass padrão definida intencionalmente e valide topologia e expansão.",
    "Migrate stored manifests and controllers before upgrading Kubernetes.": "Migre os manifests armazenados e os controllers antes de atualizar o Kubernetes.",
    "Pin an immutable version or image digest and validate provenance/signature.": "Fixe uma versão imutável ou digest da imagem e valide procedência e assinatura.",
    "Provide an explicit HTTP/HTTPS Prometheus URL to calculate p50/p90/p95/p99; absence of metrics is not compliance.": "Informe uma URL HTTP/HTTPS explícita do Prometheus para calcular p50/p90/p95/p99; ausência de métricas não representa conformidade.",
    "Provide AWS Backup, CSI snapshot or other backup/restore evidence with RPO/RTO.": "Forneça evidências de AWS Backup, snapshot CSI ou outro mecanismo de backup/restauração com RPO/RTO.",
    "Provide --cluster/EKS_CLUSTER_NAME for EKS; generic Kubernetes checks remain applicable.": "Informe --cluster/EKS_CLUSTER_NAME para EKS; as verificações genéricas do Kubernetes continuam aplicáveis.",
    "Provide opt-in registry evidence from ECR scanning, SBOM and signature verification.": "Forneça, de forma opt-in, evidências do registry obtidas por scan do ECR, SBOM e verificação de assinatura.",
    "Remediate current policy violations or register approved, expiring exceptions.": "Corrija as violações atuais de policy ou registre exceções aprovadas e com prazo de expiração.",
    "Remove host namespace sharing or document a controlled infrastructure exception.": "Remova o compartilhamento de Host Namespace ou documente uma exceção controlada de infraestrutura.",
    "Remove privileged mode or document a narrowly scoped exception.": "Remova o modo privilegiado ou documente uma exceção de escopo restrito.",
    "Remove unsafe sysctls or isolate them behind a reviewed node/runtime policy.": "Remova sysctls inseguros ou isole-os por meio de uma policy de Node/runtime revisada.",
    "Repair aggregated APIs and their backing Services before upgrades.": "Corrija as APIs agregadas e seus Services antes das atualizações.",
    "Replace hostPath with a constrained CSI/PVC volume or document the node-level exception.": "Substitua hostPath por um volume CSI/PVC restrito ou documente a exceção no nível do Node.",
    "Replace wildcards with exact API groups, resources, resourceNames and verbs.": "Remova wildcards e limite as permissões aos apiGroups, resources, resourceNames e verbs estritamente necessários.",
    "Resolve disk, memory, PID, CNI or kubelet pressure before scheduling more workloads.": "Resolva a pressão de disco, memória, PID, CNI ou kubelet antes de agendar mais Workloads.",
    "Resolve metric errors and saturation; validate targets and stabilization policies.": "Resolva erros e saturação das métricas; valide targets e policies de estabilização.",
    "Resolve trigger/metric authentication errors and define fallback for critical external metrics.": "Resolva erros de autenticação de triggers/métricas e defina fallback para métricas externas críticas.",
    "Restrict diagnostic socket access and disable it when production diagnostics are not required.": "Restrinja o acesso ao socket de diagnóstico e desabilite-o quando diagnósticos em produção não forem necessários.",
    "Review controller health and object status.": "Revise a saúde do controller e o status do objeto.",
    "Review describe/events, container logs, probes, image and resource pressure.": "Revise describe/events, logs do container, Probes, imagem e pressão de recursos.",
    "Review events, probes, logs, resources and dependencies.": "Revise eventos, Probes, logs, recursos e dependências.",
    "Review every cluster-admin subject and prefer scoped roles with time-bound elevation.": "Revise todos os subjects com cluster-admin e prefira Roles com escopo e elevação temporária.",
    "Review reclaim policy and data-retention approval before reclaiming storage.": "Revise a Reclaim Policy e a aprovação de retenção dos dados antes de recuperar o armazenamento.",
    "Review stabilization windows and scaling policies against traffic behavior.": "Revise as janelas de estabilização e policies de escalonamento conforme o comportamento do tráfego.",
    "Review the technology-specific checks and confirm runtime/version from the image SBOM.": "Revise as verificações específicas da tecnologia e confirme runtime/versão pelo SBOM da imagem.",
    "Run as a non-root UID and set runAsNonRoot=true.": "Execute com UID diferente de root e defina runAsNonRoot=true.",
    "Scope verbs/resources/resourceNames and review every bound subject.": "Restrinja verbs, resources e resourceNames e revise todos os subjects vinculados.",
    "Select every application pod and explicitly control both ingress and egress.": "Selecione todos os Pods da aplicação e controle explicitamente ingress e egress.",
    "Set a measured heap policy (often 65-75% of the container memory limit) leaving headroom for metaspace, threads, direct buffers and agents.": "Defina uma policy de heap baseada em medição, normalmente 65–75% do limit de memória do container, deixando margem para metaspace, threads, buffers diretos e agentes.",
    "Set a memory limit from observed p99 plus native/cache headroom; evaluate CPU limits against throttling policy.": "Defina o limit de memória pelo p99 observado com margem para memória nativa/cache; avalie os limits de CPU conforme a policy de throttling.",
    "Set allowPrivilegeEscalation=false unless technically required.": "Defina allowPrivilegeEscalation=false, exceto quando tecnicamente necessário.",
    "Set runAsNonRoot=true and use a non-zero UID supported by the image.": "Defina runAsNonRoot=true e use um UID diferente de zero suportado pela imagem.",
    "Set seccompProfile.type=RuntimeDefault at Pod level.": "Defina seccompProfile.type=RuntimeDefault no nível do Pod.",
    "Size init-container requests because Kubernetes scheduling uses the highest init requirement.": "Dimensione os requests dos Init Containers, pois o agendamento do Kubernetes considera o maior requisito de inicialização.",
    "Start with default-deny ingress/egress and allow only required flows.": "Comece com default-deny para ingress/egress e permita somente os fluxos necessários.",
    "Use a dedicated ServiceAccount and least-privilege RBAC; disable token automount when API access is unnecessary.": "Use um ServiceAccount dedicado e RBAC de privilégio mínimo; desabilite o automount do token quando o acesso à API for desnecessário.",
    "Use a valid governing headless Service for stable network identity.": "Use um Headless Service principal válido para manter identidade de rede estável.",
    "Use at least two replicas where the application supports it, then validate failure-domain placement.": "Use pelo menos duas réplicas quando a aplicação suportar e valide a distribuição entre domínios de falha.",
    "Use default-deny as a baseline and document intentional Internet-wide exceptions.": "Use default-deny como linha de base e documente exceções intencionais abertas para a Internet.",
    "Use demand and Prometheus history to decide whether HPA or event-driven KEDA is appropriate.": "Use a demanda e o histórico do Prometheus para decidir se HPA ou KEDA orientado a eventos é adequado.",
    "Use LimitRange defaults only as a guardrail; workload-specific values should come from telemetry.": "Use os valores padrão do LimitRange apenas como proteção; valores específicos do Workload devem vir da telemetria.",
    "Use matching selectors, maxSkew=1 and zone/hostname topology keys as appropriate.": "Use selectors correspondentes, maxSkew=1 e chaves de topologia de zona/hostname conforme necessário.",
    "Use readiness for traffic eligibility and liveness only for unrecoverable process failure.": "Use Readiness para habilitar tráfego e Liveness somente para falhas irrecuperáveis do processo.",
    "Validate selectors, pod readiness and EndpointSlice controller health.": "Valide selectors, Readiness dos Pods e saúde do controller de EndpointSlice.",
    "Verify vm_memory_high_watermark against the container memory limit and alert before memory/disk alarms block publishers.": "Verifique vm_memory_high_watermark em relação ao limit de memória do container e gere alertas antes que alarmes de memória/disco bloqueiem publishers.",
    "Grant missing read permissions or document exclusions; do not interpret missing data as compliance.": "Conceda as permissões de leitura ausentes ou documente as exclusões; não interprete ausência de dados como conformidade.",
    "Resume the collection with a controlled larger budget or narrower namespace scope.": "Retome a coleta com orçamento maior e controlado ou com escopo de Namespace mais restrito.",
    "Keep disruption-tolerant Spot capacity diversified and retain an intentional baseline for critical workloads.": "Mantenha diversificada a capacidade Spot tolerante a interrupções e preserve uma linha de base intencional para Workloads críticos.",
    "Verify interruption queue/events, graceful draining, PDBs and workload recovery under forced Spot interruption.": "Verifique fila/eventos de interrupção, draining gradual, PDBs e recuperação dos Workloads durante interrupção Spot forçada.",
    "Validate KafkaNodePools, odd controller quorum, rack awareness, replication and durable storage.": "Valide KafkaNodePools, quorum ímpar de controllers, rack awareness, replicação e armazenamento durável.",
    "Use an odd quorum of persistent nodes spread across failure domains and test recovery.": "Use quorum ímpar de Nodes persistentes distribuídos entre domínios de falha e teste a recuperação.",
    "Use resilient instances, tested backups/PITR and topology-aware scheduling.": "Use instâncias resilientes, backups/PITR testados e agendamento consciente de topologia.",
}


def pt_category(value: Any) -> Any:
    return CATEGORY_LABELS.get(value, value)


def pt_severity(value: Any) -> Any:
    return SEVERITY_LABELS.get(value, value)


def pt_state(value: Any) -> Any:
    return STATE_LABELS.get(value, value)


def pt_check(value: Any) -> Any:
    return CHECK_LABELS.get(value, value)


def pt_text(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if value in RECOMMENDATION_LABELS:
        return RECOMMENDATION_LABELS[value]
    if value in TEXT_LABELS:
        return TEXT_LABELS[value]
    translated = value
    substitutions = (
        (r"^(\d+) node\(s\) not Ready$", r"\1 Node(s) não estão Ready"),
        (r"^(\d+) pending pod\(s\)$", r"\1 Pod(s) Pending"),
        (r"^(\d+) failed pod\(s\)$", r"\1 Pod(s) com falha"),
        (r"^(\d+) deployment\(s\) below desired availability$", r"\1 Deployment(s) abaixo da disponibilidade desejada"),
        (r"^(\d+) policy object\(s\) found$", r"\1 objeto(s) de policy encontrado(s)"),
        (r"^(\d+) namespace\(s\) enforce PSS$", r"\1 Namespace(s) aplicam PSS"),
        (r"^(\d+) PDB\(s\) found$", r"\1 PDB(s) encontrado(s)"),
        (r"^(\d+) container\(s\) missing (.+)$", r"\1 container(s) sem \2"),
        (r"^(\d+) privileged container\(s\)$", r"\1 container(s) privilegiado(s)"),
        (r"^Detected in (\d+) container\(s\)\.$", r"Detectado em \1 container(s)."),
        (r"^Detected zones: none$", "Zonas detectadas: nenhuma"),
        (r"^Matched (.+)$", r"Correspondência com \1"),
        (r"^Missing (.+)\.$", r"Ausência de \1."),
        (r"^Tagged image (.+)$", r"Imagem com tag \1"),
        (r"^(.+) was not detected in workload image/name/command/runtime options\.$", r"\1 não foi detectado na imagem, nome, comando ou opções de runtime do Workload."),
        (r"^(\d+) problematic pod\(s\) out of (\d+)$", r"\1 Pod(s) com problema entre \2"),
        (r"^(\d+) role\(s\) with wildcard verbs/resources; sample=(.+)$", r"\1 Role(s) com wildcards em verbs/resources; amostra=\2"),
        (r"^state=UNAVAILABLE; reason=Prometheus endpoint unavailable: (.+)$", r"state=UNAVAILABLE; motivo=endpoint do Prometheus indisponível: \1"),
        (r"^Generated (\d+) workload recommendation\(s\) from available time series\.$", r"Foram geradas \1 recomendação(ões) de Workload a partir das séries temporais disponíveis."),
        (r"^Collector state=(.+), but no Deployment had both CPU and memory series\.$", r"Estado do coletor=\1, mas nenhum Deployment apresentou simultaneamente séries de CPU e memória."),
    )
    for pattern, replacement in substitutions:
        translated = re.sub(pattern, replacement, translated)
    translated = translated.replace("sample=none", "amostra=nenhuma")
    translated = translated.replace("condition absent", "condição ausente")
    translated = translated.replace("reason=", "motivo=")
    return translated


def localize_finding(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["category"] = pt_category(result.get("category"))
    result["check"] = pt_check(result.get("check"))
    result["detail"] = pt_text(result.get("detail"))
    result["recommendation"] = pt_text(result.get("recommendation"))
    return result


def display_value(key: str, value: Any) -> Any:
    if key == "severity":
        return pt_severity(value)
    if key in {"status", "state", "applicability", "confidence"}:
        return pt_state(value)
    if key == "category":
        return pt_category(value)
    if key == "check":
        return pt_check(value)
    if key in {"detail", "recommendation", "reason", "assessment", "caveat"}:
        return pt_text(value)
    return value
