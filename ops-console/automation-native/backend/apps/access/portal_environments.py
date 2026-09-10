"""Ambientes (módulos) do portal e permissões necessárias para acesso."""

from __future__ import annotations

from dataclasses import dataclass

from apps.access import registry as R
from apps.access.constants import ALL_ROLES
from apps.access.roles import permissions_for_role


@dataclass(frozen=True)
class PortalEnvironment:
    """Um ambiente navegável do portal (card, rota ou API)."""

    id: str
    label: str
    section: str
    permissions_any: tuple[str, ...]
    api_method: str = "GET"
    api_path: str = ""
    api_permissions_any: tuple[str, ...] | None = None

    def role_can_access(self, role: str) -> bool:
        perms = permissions_for_role(role)
        return any(code in perms for code in self.permissions_any)

    def role_can_access_api(self, role: str) -> bool:
        perms = permissions_for_role(role)
        codes = self.api_permissions_any or self.permissions_any
        return any(code in perms for code in codes)


# Catálogo de ambientes com permissão mínima e endpoint de verificação (quando aplicável).
PORTAL_ENVIRONMENTS: tuple[PortalEnvironment, ...] = (
    PortalEnvironment(
        id="headcount",
        label="Headcount",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_HEADCOUNT_VIEW,),
        api_path="/api/v1/agents/",
    ),
    PortalEnvironment(
        id="megazord",
        label="Megazord",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_MEGAZORD_VIEW,),
        api_path="/api/v1/dimensoes-processos/meta/",
    ),
    PortalEnvironment(
        id="dashboard_headcount",
        label="Dashboard de Headcount",
        section="indicadores",
        permissions_any=(R.INDICADORES_DASHBOARD_VIEW,),
        api_path="/api/v1/dashboard/overview/",
    ),
    PortalEnvironment(
        id="produtividade",
        label="Produtividade",
        section="indicadores",
        permissions_any=(R.INDICADORES_PRODUTIVIDADE_VIEW,),
        api_path="/api/v1/produtividade/status/",
    ),
    PortalEnvironment(
        id="case_manager",
        label="Case Manager",
        section="indicadores",
        permissions_any=(R.INDICADORES_CASE_MANAGER_VIEW,),
        api_path="/api/v1/case-manager/status/",
    ),
    PortalEnvironment(
        id="falhas_criticas",
        label="Falhas Críticas",
        section="indicadores",
        permissions_any=(R.QUAL_FALHAS_VIEW,),
        api_path="/api/v1/falhas/dashboard-summary/",
    ),
    PortalEnvironment(
        id="qualidade_operacional",
        label="Qualidade",
        section="indicadores",
        permissions_any=(R.QUAL_OPERACIONAL_VIEW,),
        api_path="/api/v1/qualidade/operacional/meta/",
    ),
    PortalEnvironment(
        id="auditoria",
        label="Auditoria",
        section="qualidade",
        permissions_any=(R.QUAL_AUDITORIA_VIEW,),
        api_path="/api/v1/qualidade/auditoria/dashboard/",
    ),
    PortalEnvironment(
        id="contestacao",
        label="Contestação",
        section="qualidade",
        permissions_any=(R.QUAL_AUDITORIA_VIEW,),
        api_path="/api/v1/qualidade/contestacao/dashboard/",
    ),
    PortalEnvironment(
        id="escala_consulta",
        label="Escalas — consulta",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_ESCALAS_VIEW, R.OPERACAO_JORNADA_ESCALA_VIEW),
        api_permissions_any=(
            R.PLANEJAMENTO_ESCALAS_VIEW,
            R.PLANEJAMENTO_MONITORAMENTO_VIEW,
            R.OPERACAO_JORNADA_ESCALA_VIEW,
        ),
        api_path="/api/v1/escala-flex/escala/",
    ),
    PortalEnvironment(
        id="escala_import",
        label="Escalas — importação",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_ESCALAS_IMPORT,),
        api_method="POST",
        api_path="/api/v1/escala-flex/planejamento/import/",
    ),
    PortalEnvironment(
        id="escala_generate",
        label="Escalas — geração automática",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_ESCALAS_GENERATE,),
        api_method="POST",
        api_path="/api/v1/escala-flex/planejamento/generation/preview/",
    ),
    PortalEnvironment(
        id="escala_publish",
        label="Escalas — publicação automática",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_ESCALAS_PUBLISH,),
        api_method="POST",
        api_path=(
            "/api/v1/escala-flex/planejamento/generation/runs/"
            "00000000-0000-0000-0000-000000000000/publish/"
        ),
    ),
    PortalEnvironment(
        id="monitoramento",
        label="Monitoramento (planejamento)",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_MONITORAMENTO_VIEW,),
        api_path="",
    ),
    PortalEnvironment(
        id="automacao",
        label="Automação",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_AUTOMACAO_VIEW,),
        api_path="/api/v1/automacoes/status/",
    ),
    PortalEnvironment(
        id="ocorrencias_planejamento",
        label="Ocorrências — aprovação (planejamento)",
        section="planejamento",
        permissions_any=(R.PLANEJAMENTO_OCORRENCIAS_VIEW,),
        api_path="/api/v1/escala-flex/requests/",
    ),
    PortalEnvironment(
        id="controle_jornada_painel",
        label="Controle de jornada — painel",
        section="operacao",
        permissions_any=(R.OPERACAO_JORNADA_PAINEL_VIEW,),
        api_path="/api/v1/escala-flex/monitoring/dashboard/",
    ),
    PortalEnvironment(
        id="controle_jornada_status",
        label="Controle de jornada — meu status",
        section="operacao",
        permissions_any=(R.OPERACAO_STATUS_VIEW,),
        api_path="/api/v1/escala-flex/status-events/",
    ),
    PortalEnvironment(
        id="ocorrencias_operacao",
        label="Ocorrências (operação)",
        section="operacao",
        permissions_any=(R.OPERACAO_OCORRENCIAS_VIEW,),
        api_path="/api/v1/escala-flex/operational-occurrences/",
    ),
    PortalEnvironment(
        id="suporte_claro",
        label="Suporte Claro",
        section="processos",
        permissions_any=(R.PROCESSOS_SUPORTE_CLARO_VIEW,),
        api_path="/api/v1/suporte-claro/registros/",
    ),
    PortalEnvironment(
        id="noticias",
        label="Comunicação / notícias",
        section="home",
        permissions_any=(R.COMUNICACAO_NOTICIAS_VIEW,),
        api_path="/api/v1/news/",
    ),
    PortalEnvironment(
        id="falhas_import",
        label="Importar falhas (ADM)",
        section="planejamento",
        permissions_any=(R.ADM_FALHAS_IMPORT,),
        api_path="/api/v1/falhas/sync/imports/",
    ),
    PortalEnvironment(
        id="console_ops",
        label="Console Ops",
        section="administracao",
        permissions_any=(R.ADM_CONSOLE_OPS, R.PLANEJAMENTO_CONSOLE_OPS_VIEW),
        api_path="/api/v1/portal-ops/overview/",
    ),
    PortalEnvironment(
        id="psa_cyber",
        label="Cyber PSA",
        section="administracao",
        permissions_any=(R.ADM_PSA_CYBER,),
        api_path="/api/v1/cyber-psa/overview/",
    ),
    PortalEnvironment(
        id="gestao_usuarios",
        label="Gestão de usuários",
        section="administracao",
        permissions_any=(R.ADM_USERS_MANAGE,),
        api_path="/api/v1/portal-users/",
    ),
    PortalEnvironment(
        id="catalogos",
        label="Catálogos (config)",
        section="administracao",
        permissions_any=(R.ADM_CATALOGOS,),
        api_path="",
    ),
)

PORTAL_ENVIRONMENT_BY_ID = {env.id: env for env in PORTAL_ENVIRONMENTS}


def environments_for_role(role: str) -> set[str]:
    return {env.id for env in PORTAL_ENVIRONMENTS if env.role_can_access(role)}


def build_role_environment_matrix() -> dict[str, set[str]]:
    return {role: environments_for_role(role) for role in ALL_ROLES}
