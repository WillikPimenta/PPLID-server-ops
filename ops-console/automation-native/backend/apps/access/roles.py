"""Mapa perfil → permissões e escopo padrão."""

from __future__ import annotations

from apps.access import registry as R
from apps.access.constants import (
    ROLE_ADM_PORTAL,
    ROLE_OP_AGENTE,
    ROLE_OP_GERENCIA,
    ROLE_OP_LIDER,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_ASSISTENTE,
    ROLE_PLAN_GERENCIA,
    ROLE_PROC_USUARIO,
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_AUDITORIA_FRAUD,
    ROLE_QUAL_CAPACITACAO,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_FRAUD,
    ROLE_QUAL_GERENCIA,
    ROLE_QUAL_USUARIO,
    SCOPE_GLOBAL,
    SCOPE_OWN,
    SCOPE_TEAM,
)
from apps.access.registry import ALL_PERMISSIONS, validate_permissions

# Planejamento — permissões completas
_PLAN_HEADCOUNT = {
    R.PLANEJAMENTO_HEADCOUNT_VIEW,
    R.INDICADORES_DASHBOARD_VIEW,
}

_PLAN_COMMON = {
    R.PLANEJAMENTO_MONITORAMENTO_VIEW,
    R.PLANEJAMENTO_MONITORAMENTO_DASHBOARDS_VIEW,
    R.PLANEJAMENTO_ESCALAS_VIEW,
    R.PLANEJAMENTO_OCORRENCIAS_VIEW,
    R.PLANEJAMENTO_TROCAS_VIEW,
    R.PLANEJAMENTO_MEGAZORD_VIEW,
    R.PLANEJAMENTO_DEMANDAS_JIRA_VIEW,
    R.QUAL_FALHAS_VIEW,
    R.QUAL_FALHAS_EXPORT,
    R.QUAL_FALHAS_COMPARATIVO,
    R.INDICADORES_RELATORIO_BRB_VIEW,
    R.QUAL_AUDITORIA_VIEW,
    R.QUAL_OPERACIONAL_VIEW,
    R.COMUNICACAO_NOTICIAS_VIEW,
    R.PORTAL_SECAO_VIEW,
} | _PLAN_HEADCOUNT

ROLE_DEFINITIONS: dict[str, dict] = {
    ROLE_PLAN_GERENCIA: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": _PLAN_COMMON
        | {
            R.PLANEJAMENTO_ESCALAS_IMPORT,
            R.PLANEJAMENTO_ESCALAS_GENERATE,
            R.PLANEJAMENTO_ESCALAS_PUBLISH,
            R.PLANEJAMENTO_OCORRENCIAS_APPROVE,
            R.PLANEJAMENTO_OCORRENCIAS_EDIT,
            R.PLANEJAMENTO_TROCAS_APPROVE,
            R.PLANEJAMENTO_AUTOMACAO_VIEW,
            R.PLANEJAMENTO_AUTOMACAO_CONFIGURE,
            R.PLANEJAMENTO_AUTOMACAO_APPROVE,
            R.PLANEJAMENTO_AUTOMACAO_START,
            R.PLANEJAMENTO_AUTOMACAO_STOP,
            R.PLANEJAMENTO_CONSOLE_OPS_VIEW,
            R.PLANEJAMENTO_HEADCOUNT_MANAGE,
            R.PLANEJAMENTO_DEMANDAS_JIRA_SYNC,
            R.COMUNICACAO_NOTICIAS_MANAGE,
            R.INDICADORES_PRODUTIVIDADE_VIEW,
            R.INDICADORES_PRODUTIVIDADE_SYNC,
            R.INDICADORES_CASE_MANAGER_VIEW,
            R.INDICADORES_CASE_MANAGER_SYNC,
            R.INDICADORES_MONITORAMENTO_SLA_VIEW,
            R.INDICADORES_MONITORAMENTO_SLA_SYNC,
            R.OPERACAO_CIENCIA_LEITURA_VIEW,
        },
    },
    ROLE_PLAN_ANALISTA: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": _PLAN_COMMON
        | {
            R.PLANEJAMENTO_ESCALAS_IMPORT,
            R.PLANEJAMENTO_ESCALAS_GENERATE,
            R.PLANEJAMENTO_ESCALAS_PUBLISH,
            R.PLANEJAMENTO_TROCAS_APPROVE,
            R.PLANEJAMENTO_AUTOMACAO_VIEW,
            R.PLANEJAMENTO_AUTOMACAO_CONFIGURE,
            R.PLANEJAMENTO_AUTOMACAO_START,
            R.PLANEJAMENTO_AUTOMACAO_STOP,
            R.PLANEJAMENTO_HEADCOUNT_MANAGE,
            R.PLANEJAMENTO_DEMANDAS_JIRA_SYNC,
            R.INDICADORES_PRODUTIVIDADE_VIEW,
            R.INDICADORES_CASE_MANAGER_VIEW,
            R.INDICADORES_MONITORAMENTO_SLA_VIEW,
            R.INDICADORES_MONITORAMENTO_SLA_SYNC,
            R.OPERACAO_CIENCIA_LEITURA_VIEW,
        },
    },
    ROLE_PLAN_ASSISTENTE: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": _PLAN_COMMON
        | {
            R.PLANEJAMENTO_MONITORAMENTO_DASHBOARDS_VIEW,
            R.PLANEJAMENTO_OCORRENCIAS_APPROVE,
            R.PLANEJAMENTO_OCORRENCIAS_EDIT,
        },
    },
    ROLE_OP_GERENCIA: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.OPERACAO_JORNADA_PAINEL_VIEW,
            R.OPERACAO_JORNADA_ESCALA_VIEW,
            R.OPERACAO_HISTORICO_VIEW,
            R.OPERACAO_HISTORICO_APPROVE,
            R.OPERACAO_OCORRENCIAS_VIEW,
            R.OPERACAO_OCORRENCIAS_CREATE,
            R.OPERACAO_TROCAS_VIEW,
            R.OPERACAO_TROCAS_CREATE,
            R.OPERACAO_TROCAS_APPROVE_LIDER,
            R.QUAL_FALHAS_VIEW,
            R.QUAL_FALHAS_EXPORT,
            R.QUAL_FALHAS_COMPARATIVO,
            R.INDICADORES_RELATORIO_BRB_VIEW,
            R.COMUNICACAO_NOTICIAS_VIEW,
            R.PORTAL_SECAO_VIEW,
            R.INDICADORES_PRODUTIVIDADE_VIEW,
            R.INDICADORES_CASE_MANAGER_VIEW,
            R.INDICADORES_MONITORAMENTO_SLA_VIEW,
            R.OPERACAO_CIENCIA_LEITURA_VIEW,
            R.OPERACAO_CONTESTACAO_VIEW,
            R.OPERACAO_CONTESTACAO_CREATE,
            R.OPERACAO_SUPORTE_OPERACIONAL_VIEW,
            R.OPERACAO_SUPORTE_OPERACIONAL_CREATE,
            R.OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE,
            R.OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER,
            R.OPERACAO_SUPORTE_OPERACIONAL_CANCEL,
        },
    },
    ROLE_OP_LIDER: {
        "default_scope": SCOPE_TEAM,
        "permissions": {
            R.OPERACAO_JORNADA_PAINEL_VIEW,
            R.OPERACAO_JORNADA_ESCALA_VIEW,
            R.OPERACAO_HISTORICO_VIEW,
            R.OPERACAO_HISTORICO_APPROVE,
            R.OPERACAO_OCORRENCIAS_VIEW,
            R.OPERACAO_OCORRENCIAS_CREATE,
            R.OPERACAO_TROCAS_VIEW,
            R.OPERACAO_TROCAS_CREATE,
            R.OPERACAO_TROCAS_APPROVE_LIDER,
            R.QUAL_FALHAS_VIEW,
            R.QUAL_FALHAS_EXPORT,
            R.COMUNICACAO_NOTICIAS_VIEW,
            R.PORTAL_SECAO_VIEW,
            R.INDICADORES_PRODUTIVIDADE_VIEW,
            R.INDICADORES_CASE_MANAGER_VIEW,
            R.INDICADORES_MONITORAMENTO_SLA_VIEW,
            R.OPERACAO_CIENCIA_LEITURA_VIEW,
            R.OPERACAO_CONTESTACAO_VIEW,
            R.OPERACAO_CONTESTACAO_CREATE,
            R.OPERACAO_SUPORTE_OPERACIONAL_VIEW,
            R.OPERACAO_SUPORTE_OPERACIONAL_CREATE,
            R.OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE,
            R.OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER,
            R.OPERACAO_SUPORTE_OPERACIONAL_CANCEL,
        },
    },
    ROLE_OP_AGENTE: {
        "default_scope": SCOPE_OWN,
        "permissions": {
            R.OPERACAO_JORNADA_ESCALA_VIEW,
            R.OPERACAO_HISTORICO_VIEW,
            R.OPERACAO_STATUS_VIEW,
            R.OPERACAO_STATUS_CHANGE,
            R.OPERACAO_OCORRENCIAS_VIEW,
            R.OPERACAO_TROCAS_VIEW,
            R.OPERACAO_TROCAS_CREATE,
            R.QUAL_FALHAS_VIEW,
            R.COMUNICACAO_NOTICIAS_VIEW,
            R.PORTAL_SECAO_VIEW,
            R.INDICADORES_PRODUTIVIDADE_VIEW,
            R.INDICADORES_CASE_MANAGER_VIEW,
            R.OPERACAO_SUPORTE_OPERACIONAL_VIEW,
            R.OPERACAO_SUPORTE_OPERACIONAL_CREATE,
            R.OPERACAO_SUPORTE_OPERACIONAL_CANCEL,
        },
    },
    ROLE_PROC_USUARIO: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.PROCESSOS_SUPORTE_CLARO_VIEW,
            R.PROCESSOS_SUPORTE_CLARO_CREATE,
            R.PROCESSOS_SUPORTE_CLARO_CHANGE_STATUS,
        },
    },
    # Legado “Qualidade” — mantido para quem ainda está no grupo role:qual_usuario.
    ROLE_QUAL_USUARIO: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.QUAL_FALHAS_VIEW,
            R.QUAL_FALHAS_EXPORT,
            R.QUAL_FALHAS_COMPARATIVO,
            R.INDICADORES_RELATORIO_BRB_VIEW,
            R.QUAL_AUDITORIA_VIEW,
            R.QUAL_AUDITORIA_CREATE,
            R.QUAL_OPERACIONAL_VIEW,
            R.PORTAL_SECAO_VIEW,
        },
    },
    # Novos perfis específicos — permissões mínimas; configurar depois na UI de perfis.
    ROLE_QUAL_AUDITORIA_FRAUD: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {R.PORTAL_SECAO_VIEW},
    },
    ROLE_QUAL_AUDITORIA_COMPLIANCE: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.PORTAL_SECAO_VIEW,
            R.QUAL_AUDITORIA_COMPLIANCE_VIEW,
            R.QUAL_AUDITORIA_COMPLIANCE_CREATE,
            R.QUAL_AUDITORIA_COMPLIANCE_ASSIGN,
            R.QUAL_AUDITORIA_COMPLIANCE_CHANGE,
        },
    },
    ROLE_QUAL_CONTESTACAO_FRAUD: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.PORTAL_SECAO_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE,
        },
    },
    ROLE_QUAL_CONTESTACAO_COMPLIANCE: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.PORTAL_SECAO_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE,
        },
    },
    ROLE_QUAL_CAPACITACAO: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.PORTAL_SECAO_VIEW,
            R.QUAL_CAPACITACAO_SUPORTE_VIEW,
            R.QUAL_CAPACITACAO_SUPORTE_ASSIGN,
            R.QUAL_CAPACITACAO_SUPORTE_ANSWER,
            R.OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE,
            R.QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW,
            R.QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE,
            R.QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE,
        },
    },
    ROLE_QUAL_GERENCIA: {
        "default_scope": SCOPE_GLOBAL,
        "permissions": {
            R.QUAL_FALHAS_VIEW,
            R.QUAL_FALHAS_EXPORT,
            R.QUAL_FALHAS_EXPORT_EXECUTIVE,
            R.QUAL_FALHAS_COMPARATIVO,
            R.QUAL_FALHAS_IMPORT_HISTORY,
            R.INDICADORES_RELATORIO_BRB_VIEW,
            R.QUAL_AUDITORIA_VIEW,
            R.QUAL_AUDITORIA_CREATE,
            R.QUAL_AUDITORIA_ASSIGN,
            R.QUAL_AUDITORIA_CHANGE,
            R.QUAL_AUDITORIA_COMPLIANCE_VIEW,
            R.QUAL_AUDITORIA_COMPLIANCE_CREATE,
            R.QUAL_AUDITORIA_COMPLIANCE_ASSIGN,
            R.QUAL_AUDITORIA_COMPLIANCE_CHANGE,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE,
            R.QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE,
            R.QUAL_OPERACIONAL_VIEW,
            R.QUAL_OPERACIONAL_SYNC,
            R.QUAL_CAPACITACAO_SUPORTE_VIEW,
            R.QUAL_CAPACITACAO_SUPORTE_ASSIGN,
            R.QUAL_CAPACITACAO_SUPORTE_ANSWER,
            R.OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE,
            R.QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW,
            R.QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE,
            R.QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE,
            R.PORTAL_SECAO_VIEW,
            R.OPERACAO_CIENCIA_LEITURA_VIEW,
            R.OPERACAO_CONTESTACAO_VIEW,
            R.OPERACAO_CONTESTACAO_CREATE,
        },
    },
    ROLE_ADM_PORTAL: {
        "default_scope": SCOPE_GLOBAL,
        # TI/ADM: acesso total ao portal (todos os módulos do catálogo).
        "permissions": set(ALL_PERMISSIONS),
    },
}


def permissions_for_role(role: str) -> set[str]:
    from apps.access.services.role_definitions import stored_permissions_for_role

    # TI/ADM: sempre o catálogo completo — evita snapshot DB desatualizado
    # quando novas permissões entram no registry (ex.: planejamento.megazord.view).
    if role == ROLE_ADM_PORTAL:
        return set(ALL_PERMISSIONS)

    stored = stored_permissions_for_role(role)
    if stored is not None:
        return stored
    definition = ROLE_DEFINITIONS.get(role)
    if not definition:
        return set()
    return set(definition["permissions"])


def default_scope_for_role(role: str) -> str:
    from apps.access.services.role_definitions import stored_scope_for_role

    stored = stored_scope_for_role(role)
    if stored is not None:
        return stored
    definition = ROLE_DEFINITIONS.get(role)
    if not definition:
        return SCOPE_OWN
    return definition["default_scope"]


def validate_role_definitions() -> None:
    for role, definition in ROLE_DEFINITIONS.items():
        perms = definition.get("permissions", set())
        if not isinstance(perms, set):
            raise ValueError(f"Permissões do perfil {role} devem ser um set")
        validate_permissions(perms)
