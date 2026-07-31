# Documentação do ambiente

Esta documentação descreve o estado desejado versionado na branch `main`.
Comandos que dependem do cluster devem ser executados no `k8s-master`, via SSH,
salvo indicação em contrário.

## Jornada recomendada

1. [Arquitetura](architecture.md): topologia, componentes e fluxo de tráfego.
2. [Preparação e provisionamento](getting-started.md): pré-requisitos, criação e
   reconstrução inicial.
3. [Acessos](access.md): SSH, kubectl, Argo CD, Grafana, RabbitMQ e aplicação.
4. [GitOps e CI/CD](gitops-cicd.md): fluxo de mudança, pipelines e reconciliação.
5. [Operações](operations.md): verificações, logs, escala e manutenção.
6. [Segurança](security.md): segredos, mTLS, políticas e responsabilidades.
7. [Backup e recuperação](disaster-recovery.md): escopo de dados e restauração.
8. [Troubleshooting](troubleshooting.md): diagnóstico orientado por sintomas.

## Guias especializados

- [Aplicação bancária Java/.NET](../app/README-banking.md)
- [Observabilidade de runtime](runtime-observability.md)

## Fonte de verdade

| Assunto | Local |
|---|---|
| VMs e versões-base | `iac/vagrant/Vagrantfile` |
| Provisionamento | `iac/vagrant/scripts/` |
| Aplicações Argo CD | `kubernetes/argocd/` |
| Workloads | `kubernetes/apps/` |
| Plataforma e políticas | `kubernetes/platform/`, `kubernetes/policies/` |
| Pipelines | `.github/workflows/` |
| Diagnósticos e smoke tests | `scripts/` |

Não copie credenciais para documentação, issues ou logs. Os runbooks mostram
como consultá-las no momento do uso.

