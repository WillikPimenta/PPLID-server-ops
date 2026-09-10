"""Canonical robot mode identifiers."""

ROBOT_MODES_PRIMARY = (
    "nivel",
    "monitor",
    "excel",
    "production",
    "rotina",
    "confer",
    "ged",
    "replicacao_auditoria_d1",
    "falhas_criticas",
    "produtividade_case",
    "controle_sla",
    "prioridades_nh",
    "tray_ui",
)

ROBOT_MODES_LEGACY = (
    "replicacao_auditoria",
)

ROBOT_MODES = ROBOT_MODES_PRIMARY + ROBOT_MODES_LEGACY

MODE_LABELS = {
    "nivel": "Nível hierárquico",
    "monitor": "Monitor de eventos",
    "excel": "Alterações no BRFlow",
    "production": "Produção (H/H)",
    "tray_ui": "Monitorar bandeja (OneDrive + Cisco)",
    "rotina": "Rotina diária",
    "confer": "Confer (Eventos)",
    "ged": "GED",
    "replicacao_auditoria_d1": "Replicação de Auditoria",
    "replicacao_auditoria": "Replicação de Auditoria (legado)",
    "falhas_criticas": "Falhas Críticas (Power BI + Report)",
    "produtividade_case": "Produtividade Case Manager",
    "controle_sla": "Controle de SLA (BrFlow)",
    "prioridades_nh": "Prioridades por nível hierárquico",
}

# Destinos PostgreSQL acoplados ao bot Produção (HxH).
PRODUCTION_DATA_SINKS = {
    "produtividade": "brflow-prod-hxh-bruto → productivity_record",
    "monitor_eventos": "monitor-eventos-tratado (BRFlow+Log Eventos) → monitor_evento_record",
}
