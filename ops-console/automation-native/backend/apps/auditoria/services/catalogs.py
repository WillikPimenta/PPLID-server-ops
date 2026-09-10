from __future__ import annotations

from apps.auditoria.constants import BACKOFFICE_I_JOB_TITLE, ETAPA_FALHA_AUTOMATICO
from apps.auditoria.models import AuditoriaCatalogItem
from apps.auditoria.services.catalog_items import get_catalog_values, get_etapa_falha_automatico
from apps.auditoria.services.motivos_falha import get_motivo_falha_values
from apps.auditoria.services.text_format import format_auditoria_label
from apps.dimensoes_processos.models import DimNivelHierarquico, DimWorkflow
from apps.falhas_criticas.models import FalhasAgent
from apps.workforce.models import Agent, AgentHistory

SISTEMA_USUARIO = "SISTEMA"


def _megazord_dim_options(queryset, id_attr: str) -> list[dict[str, str]]:
    """Opções de catálogo Megazord (nome como value; id — nome no label)."""
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in queryset:
        nome = (getattr(row, "nome", None) or "").strip()
        if not nome or nome.lower() in seen:
            continue
        seen.add(nome.lower())
        pk = getattr(row, id_attr, None)
        label = f"{pk} — {nome}" if pk is not None else nome
        options.append({"value": nome, "label": label})
    return options


def _backoffice_i_usuarios() -> list[dict[str, str]]:
    histories = (
        AgentHistory.objects.filter(
            active=True,
            final_date__isnull=True,
            job_title=BACKOFFICE_I_JOB_TITLE,
            agent__active=True,
        )
        .select_related("agent")
        .order_by("agent__full_name", "agent__user_lan_id")
    )

    seen: set[str] = {SISTEMA_USUARIO.casefold()}
    usuarios: list[dict[str, str]] = [
        {"value": SISTEMA_USUARIO, "label": SISTEMA_USUARIO}
    ]
    for history in histories:
        lan = (history.agent.user_lan_id or "").strip()
        name = (history.agent.full_name or "").strip()
        if not lan or not name or lan.lower() in seen:
            continue
        seen.add(lan.lower())
        usuarios.append({"value": lan, "label": name})

    if len(usuarios) > 1:
        # Include remaining active agents so Brflow LAN IDs can resolve to names.
        for agent in (
            Agent.objects.filter(active=True)
            .exclude(full_name__exact="")
            .exclude(user_lan_id__exact="")
            .order_by("full_name", "user_lan_id")
        ):
            lan = (agent.user_lan_id or "").strip()
            name = (agent.full_name or "").strip()
            if not lan or not name or lan.lower() in seen:
                continue
            seen.add(lan.lower())
            usuarios.append({"value": lan, "label": name})
        return usuarios

    agents = (
        FalhasAgent.objects.filter(job_title__iexact=BACKOFFICE_I_JOB_TITLE)
        .exclude(name__exact="")
        .order_by("name", "matricula_norm")
    )
    for agent in agents:
        value = (agent.matricula_norm or "").strip()
        label = (agent.name or "").strip()
        if not value or not label or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        usuarios.append({"value": value, "label": label})
    return usuarios


def build_auditoria_catalogs() -> dict:
    tipo_falha = get_catalog_values(AuditoriaCatalogItem.CATALOG_TIPO_FALHA)
    etapa_auto = format_auditoria_label(get_etapa_falha_automatico() or ETAPA_FALHA_AUTOMATICO)
    workflows = DimWorkflow.objects.filter(ind_considerar=True).order_by("nome", "id_workflow")
    niveis = DimNivelHierarquico.objects.filter(ind_considerar=True).order_by("nome", "id_nh")
    return {
        "modulo": get_catalog_values(AuditoriaCatalogItem.CATALOG_MODULO),
        "tipo_falha": [
            {"value": value, "label": format_auditoria_label(value)} for value in tipo_falha
        ],
        "etapa_falha": get_catalog_values(AuditoriaCatalogItem.CATALOG_ETAPA_FALHA),
        "motivo_falha": get_motivo_falha_values(),
        "nivel_dificuldade": get_catalog_values(AuditoriaCatalogItem.CATALOG_NIVEL_DIFICULDADE),
        "tipo_documento": get_catalog_values(AuditoriaCatalogItem.CATALOG_TIPO_DOCUMENTO),
        "uf_documento": get_catalog_values(AuditoriaCatalogItem.CATALOG_UF_DOCUMENTO),
        "sinalizacao": get_catalog_values(AuditoriaCatalogItem.CATALOG_SINALIZACAO),
        "novo_resultado": get_catalog_values(AuditoriaCatalogItem.CATALOG_NOVO_RESULTADO),
        "cruzamento_bases": get_catalog_values(AuditoriaCatalogItem.CATALOG_CRUZAMENTO_BASES),
        "qualidade_imagem": get_catalog_values(AuditoriaCatalogItem.CATALOG_QUALIDADE_IMAGEM),
        "tipo_acao_controle": get_catalog_values(AuditoriaCatalogItem.CATALOG_TIPO_ACAO_CONTROLE),
        "motivo_base_negativa": get_catalog_values(AuditoriaCatalogItem.CATALOG_MOTIVO_BASE_NEGATIVA),
        "irregularidades_confer": get_catalog_values(
            AuditoriaCatalogItem.CATALOG_IRREGULARIDADES_CONFER
        ),
        "usuario": _backoffice_i_usuarios(),
        "etapa_falha_automatico": etapa_auto,
        # Mesma fonte dos catálogos Megazord (/planejamento/megazord/workflow|nivel-hierarquico).
        "workflow": _megazord_dim_options(workflows, "id_workflow"),
        "nivel_hierarquico": _megazord_dim_options(niveis, "id_nh"),
    }
