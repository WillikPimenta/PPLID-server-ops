"""Catálogo de permissões do portal (módulo.recurso.ação)."""

from __future__ import annotations

# --- Operação ---
OPERACAO_JORNADA_PAINEL_VIEW = "operacao.jornada.painel.view"
OPERACAO_JORNADA_ESCALA_VIEW = "operacao.jornada.escala.view"
OPERACAO_HISTORICO_VIEW = "operacao.historico.view"
OPERACAO_HISTORICO_APPROVE = "operacao.historico.approve"
OPERACAO_STATUS_VIEW = "operacao.status.view"
OPERACAO_STATUS_CHANGE = "operacao.status.change"
OPERACAO_OCORRENCIAS_VIEW = "operacao.ocorrencias.view"
OPERACAO_OCORRENCIAS_CREATE = "operacao.ocorrencias.create"
OPERACAO_TROCAS_VIEW = "operacao.trocas.view"
OPERACAO_TROCAS_CREATE = "operacao.trocas.create"
OPERACAO_TROCAS_APPROVE_LIDER = "operacao.trocas.approve_lider"
OPERACAO_CIENCIA_LEITURA_VIEW = "operacao.ciencia_leitura.view"
OPERACAO_CONTESTACAO_VIEW = "operacao.contestacao.view"
OPERACAO_CONTESTACAO_CREATE = "operacao.contestacao.create"
OPERACAO_SUPORTE_OPERACIONAL_VIEW = "operacao.suporte_operacional.view"
OPERACAO_SUPORTE_OPERACIONAL_CREATE = "operacao.suporte_operacional.create"
OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE = "operacao.suporte_operacional.notices.manage"
OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER = "operacao.suporte_operacional.approve_leader"
OPERACAO_SUPORTE_OPERACIONAL_CANCEL = "operacao.suporte_operacional.cancel"

# --- Planejamento ---
PLANEJAMENTO_MONITORAMENTO_VIEW = "planejamento.monitoramento.view"
PLANEJAMENTO_MONITORAMENTO_DASHBOARDS_VIEW = "planejamento.monitoramento.dashboards.view"
PLANEJAMENTO_ESCALAS_VIEW = "planejamento.escalas.view"
PLANEJAMENTO_ESCALAS_IMPORT = "planejamento.escalas.import"
PLANEJAMENTO_ESCALAS_GENERATE = "planejamento.escalas.generate"
PLANEJAMENTO_ESCALAS_PUBLISH = "planejamento.escalas.publish"
PLANEJAMENTO_OCORRENCIAS_VIEW = "planejamento.ocorrencias.view"
PLANEJAMENTO_OCORRENCIAS_APPROVE = "planejamento.ocorrencias.approve"
PLANEJAMENTO_OCORRENCIAS_EDIT = "planejamento.ocorrencias.edit"
PLANEJAMENTO_TROCAS_VIEW = "planejamento.trocas.view"
PLANEJAMENTO_TROCAS_APPROVE = "planejamento.trocas.approve"
PLANEJAMENTO_AUTOMACAO_VIEW = "planejamento.automacao.view"
PLANEJAMENTO_AUTOMACAO_CONFIGURE = "planejamento.automacao.configure"
PLANEJAMENTO_AUTOMACAO_APPROVE = "planejamento.automacao.approve"
PLANEJAMENTO_AUTOMACAO_START = "planejamento.automacao.start"
PLANEJAMENTO_AUTOMACAO_STOP = "planejamento.automacao.stop"
PLANEJAMENTO_CONSOLE_OPS_VIEW = "planejamento.console_ops.view"
PLANEJAMENTO_HEADCOUNT_VIEW = "planejamento.headcount.view"
PLANEJAMENTO_HEADCOUNT_MANAGE = "planejamento.headcount.manage"
PLANEJAMENTO_MEGAZORD_VIEW = "planejamento.megazord.view"
PLANEJAMENTO_DEMANDAS_JIRA_VIEW = "planejamento.demandas_jira.view"
PLANEJAMENTO_DEMANDAS_JIRA_SYNC = "planejamento.demandas_jira.sync"

# --- Portal ---
PORTAL_SECAO_VIEW = "portal.secao.view"

# --- Comunicação ---
COMUNICACAO_NOTICIAS_VIEW = "comunicacao.noticias.view"
COMUNICACAO_NOTICIAS_MANAGE = "comunicacao.noticias.manage"

# --- Indicadores ---
INDICADORES_DASHBOARD_VIEW = "indicadores.dashboard.view"
INDICADORES_PRODUTIVIDADE_VIEW = "indicadores.produtividade.view"
INDICADORES_PRODUTIVIDADE_SYNC = "indicadores.produtividade.sync"
INDICADORES_CASE_MANAGER_VIEW = "indicadores.case_manager.view"
INDICADORES_CASE_MANAGER_SYNC = "indicadores.case_manager.sync"
INDICADORES_MONITORAMENTO_SLA_VIEW = "indicadores.monitoramento_sla.view"
INDICADORES_MONITORAMENTO_SLA_SYNC = "indicadores.monitoramento_sla.sync"
INDICADORES_RELATORIO_BRB_VIEW = "indicadores.relatorio_brb.view"

# --- Processos ---
PROCESSOS_SUPORTE_CLARO_VIEW = "processos.suporte_claro.view"
PROCESSOS_SUPORTE_CLARO_CREATE = "processos.suporte_claro.create"
PROCESSOS_SUPORTE_CLARO_CHANGE_STATUS = "processos.suporte_claro.change_status"

# --- Qualidade ---
QUAL_FALHAS_VIEW = "qual.falhas.view"
QUAL_FALHAS_EXPORT = "qual.falhas.export"
QUAL_FALHAS_EXPORT_EXECUTIVE = "qual.falhas.export_executive"
QUAL_FALHAS_COMPARATIVO = "qual.falhas.comparativo"
QUAL_FALHAS_SYNC = "qual.falhas.sync"
QUAL_FALHAS_IMPORT_HISTORY = "qual.falhas.import_history"
QUAL_AUDITORIA_VIEW = "qual.auditoria.view"
QUAL_AUDITORIA_CREATE = "qual.auditoria.create"
QUAL_AUDITORIA_ASSIGN = "qual.auditoria.assign"
QUAL_AUDITORIA_CHANGE = "qual.auditoria.change"
QUAL_AUDITORIA_RESULT_CHANGE = "qual.auditoria.result.change"
QUAL_AUDITORIA_COMPLIANCE_VIEW = "qual.auditoria_compliance.view"
QUAL_AUDITORIA_COMPLIANCE_CREATE = "qual.auditoria_compliance.create"
QUAL_AUDITORIA_COMPLIANCE_ASSIGN = "qual.auditoria_compliance.assign"
QUAL_AUDITORIA_COMPLIANCE_CHANGE = "qual.auditoria_compliance.change"
QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW = "qual.contestacao_interna.fraud.view"
QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE = "qual.contestacao_interna.fraud.analyze"
QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE = "qual.contestacao_interna.fraud.revisar"
QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW = "qual.contestacao_interna.compliance.view"
QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE = "qual.contestacao_interna.compliance.analyze"
QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE = "qual.contestacao_interna.compliance.revisar"
QUAL_OPERACIONAL_VIEW = "qual.operacional.view"
QUAL_OPERACIONAL_SYNC = "qual.operacional.sync"
QUAL_CAPACITACAO_SUPORTE_VIEW = "qual.capacitacao.suporte.view"
QUAL_CAPACITACAO_SUPORTE_ASSIGN = "qual.capacitacao.suporte.assign"
QUAL_CAPACITACAO_SUPORTE_ANSWER = "qual.capacitacao.suporte.answer"
QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW = "qual.capacitacao.revisao_falhas.view"
QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE = "qual.capacitacao.revisao_falhas.decide"
QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE = "qual.capacitacao.revisao_falhas.change"

# --- Administração ---
ADM_FALHAS_IMPORT = "adm.falhas.import"
ADM_FALHAS_SYNC = "adm.falhas.sync"
ADM_ESCALA_IMPERSONATE = "adm.escala.impersonate"
ADM_CONSOLE_OPS = "adm.console_ops"
ADM_PSA_CYBER = "adm.psa_cyber"
ADM_CATALOGOS = "adm.catalogos"
ADM_USERS_MANAGE = "adm.users.manage"
ADM_CONFIG_HUB = "adm.config_hub"
ADM_QUALIDADE_REGISTROS_VIEW = "adm.qualidade_registros.view"
ADM_QUALIDADE_REGISTROS_MANAGE = "adm.qualidade_registros.manage"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        OPERACAO_JORNADA_PAINEL_VIEW,
        OPERACAO_JORNADA_ESCALA_VIEW,
        OPERACAO_HISTORICO_VIEW,
        OPERACAO_HISTORICO_APPROVE,
        OPERACAO_STATUS_VIEW,
        OPERACAO_STATUS_CHANGE,
        OPERACAO_OCORRENCIAS_VIEW,
        OPERACAO_OCORRENCIAS_CREATE,
        OPERACAO_TROCAS_VIEW,
        OPERACAO_TROCAS_CREATE,
        OPERACAO_TROCAS_APPROVE_LIDER,
        OPERACAO_CIENCIA_LEITURA_VIEW,
        OPERACAO_CONTESTACAO_VIEW,
        OPERACAO_CONTESTACAO_CREATE,
        OPERACAO_SUPORTE_OPERACIONAL_VIEW,
        OPERACAO_SUPORTE_OPERACIONAL_CREATE,
        OPERACAO_SUPORTE_OPERACIONAL_NOTICES_MANAGE,
        OPERACAO_SUPORTE_OPERACIONAL_APPROVE_LEADER,
        OPERACAO_SUPORTE_OPERACIONAL_CANCEL,
        PLANEJAMENTO_MONITORAMENTO_VIEW,
        PLANEJAMENTO_MONITORAMENTO_DASHBOARDS_VIEW,
        PLANEJAMENTO_ESCALAS_VIEW,
        PLANEJAMENTO_ESCALAS_IMPORT,
        PLANEJAMENTO_ESCALAS_GENERATE,
        PLANEJAMENTO_ESCALAS_PUBLISH,
        PLANEJAMENTO_OCORRENCIAS_VIEW,
        PLANEJAMENTO_OCORRENCIAS_APPROVE,
        PLANEJAMENTO_OCORRENCIAS_EDIT,
        PLANEJAMENTO_TROCAS_VIEW,
        PLANEJAMENTO_TROCAS_APPROVE,
        PLANEJAMENTO_AUTOMACAO_VIEW,
        PLANEJAMENTO_AUTOMACAO_CONFIGURE,
        PLANEJAMENTO_AUTOMACAO_APPROVE,
        PLANEJAMENTO_AUTOMACAO_START,
        PLANEJAMENTO_AUTOMACAO_STOP,
        PLANEJAMENTO_CONSOLE_OPS_VIEW,
        PLANEJAMENTO_HEADCOUNT_VIEW,
        PLANEJAMENTO_HEADCOUNT_MANAGE,
        PLANEJAMENTO_MEGAZORD_VIEW,
        PLANEJAMENTO_DEMANDAS_JIRA_VIEW,
        PLANEJAMENTO_DEMANDAS_JIRA_SYNC,
        PORTAL_SECAO_VIEW,
        COMUNICACAO_NOTICIAS_VIEW,
        COMUNICACAO_NOTICIAS_MANAGE,
        INDICADORES_DASHBOARD_VIEW,
        INDICADORES_PRODUTIVIDADE_VIEW,
        INDICADORES_PRODUTIVIDADE_SYNC,
        INDICADORES_CASE_MANAGER_VIEW,
        INDICADORES_CASE_MANAGER_SYNC,
        INDICADORES_MONITORAMENTO_SLA_VIEW,
        INDICADORES_MONITORAMENTO_SLA_SYNC,
        INDICADORES_RELATORIO_BRB_VIEW,
        PROCESSOS_SUPORTE_CLARO_VIEW,
        PROCESSOS_SUPORTE_CLARO_CREATE,
        PROCESSOS_SUPORTE_CLARO_CHANGE_STATUS,
        QUAL_FALHAS_VIEW,
        QUAL_FALHAS_EXPORT,
        QUAL_FALHAS_EXPORT_EXECUTIVE,
        QUAL_FALHAS_COMPARATIVO,
        QUAL_FALHAS_SYNC,
        QUAL_FALHAS_IMPORT_HISTORY,
        QUAL_AUDITORIA_VIEW,
        QUAL_AUDITORIA_CREATE,
        QUAL_AUDITORIA_ASSIGN,
        QUAL_AUDITORIA_CHANGE,
        QUAL_AUDITORIA_RESULT_CHANGE,
        QUAL_AUDITORIA_COMPLIANCE_VIEW,
        QUAL_AUDITORIA_COMPLIANCE_CREATE,
        QUAL_AUDITORIA_COMPLIANCE_ASSIGN,
        QUAL_AUDITORIA_COMPLIANCE_CHANGE,
        QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
        QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
        QUAL_CONTESTACAO_INTERNA_FRAUD_REVISE,
        QUAL_CONTESTACAO_INTERNA_COMPLIANCE_VIEW,
        QUAL_CONTESTACAO_INTERNA_COMPLIANCE_ANALYZE,
        QUAL_CONTESTACAO_INTERNA_COMPLIANCE_REVISE,
        QUAL_OPERACIONAL_VIEW,
        QUAL_OPERACIONAL_SYNC,
        QUAL_CAPACITACAO_SUPORTE_VIEW,
        QUAL_CAPACITACAO_SUPORTE_ASSIGN,
        QUAL_CAPACITACAO_SUPORTE_ANSWER,
        QUAL_CAPACITACAO_REVISAO_FALHAS_VIEW,
        QUAL_CAPACITACAO_REVISAO_FALHAS_DECIDE,
        QUAL_CAPACITACAO_REVISAO_FALHAS_CHANGE,
        ADM_FALHAS_IMPORT,
        ADM_FALHAS_SYNC,
        ADM_ESCALA_IMPERSONATE,
        ADM_CONSOLE_OPS,
        ADM_PSA_CYBER,
        ADM_CATALOGOS,
        ADM_USERS_MANAGE,
        ADM_CONFIG_HUB,
        ADM_QUALIDADE_REGISTROS_VIEW,
        ADM_QUALIDADE_REGISTROS_MANAGE,
    }
)


def validate_permissions(permissions: set[str]) -> None:
    unknown = permissions - ALL_PERMISSIONS
    if unknown:
        raise ValueError(f"Permissões desconhecidas no registry: {sorted(unknown)}")
