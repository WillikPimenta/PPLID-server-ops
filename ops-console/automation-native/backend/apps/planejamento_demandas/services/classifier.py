"""Classificação heurística de demandas Jira → módulo do portal."""

from __future__ import annotations

from apps.planejamento_demandas.models import JiraDemanda

_RULES: list[tuple[str, tuple[str, ...], str]] = [
    (
        JiraDemanda.CATEGORIA_HEADCOUNT,
        (
            "headcount",
            "desligamento",
            "desligar",
            "admiss",
            "colaborador",
            "matricula",
            "matrícula",
            "e-mail",
            "email",
            "nome no brflow",
            "usuário",
            "usuario",
            "inativ",
        ),
        "/planejamento/megazord/headcount",
    ),
    (
        JiraDemanda.CATEGORIA_OCORRENCIAS,
        (
            "ocorrência",
            "ocorrencia",
            "ocorrencias",
            "tboccurrence",
            "abatimento",
            "substituição de ocorr",
            "substituicao de ocorr",
        ),
        "/planejamento/ocorrencias",
    ),
    (
        JiraDemanda.CATEGORIA_ESCALA,
        (
            "escala",
            "troca de hor",
            "troca hor",
            "volumetria",
            "trimestral",
            "aumento de volume",
        ),
        "/planejamento/escalas",
    ),
    (
        JiraDemanda.CATEGORIA_REGRAS,
        (
            "regra",
            "workflow",
            "mapeamento",
            "alerta",
            "resultado",
            "derivação",
            "derivacao",
            "documentoscopia",
            "indefinida",
            "temporária",
            "temporaria",
        ),
        "/planejamento/regras-workflow",
    ),
    (
        JiraDemanda.CATEGORIA_REPLICACAO,
        (
            "replicação",
            "replicacao",
            "replicar",
            "migração",
            "migracao",
            "de para",
            "de→para",
        ),
        "/secao/indicadores/replicacao-d1",
    ),
    (
        JiraDemanda.CATEGORIA_MEGAZORD,
        (
            "megazord",
            "meta_dia",
            "metas_etapa",
            "metas etapa",
            "projeção sla",
            "projecao sla",
            "produtividade",
            "sei",
            "identificação dos processos",
            "identificacao dos processos",
        ),
        "/planejamento/megazord",
    ),
    (
        JiraDemanda.CATEGORIA_INTRANET_BI,
        (
            "intranet",
            "power bi",
            "powerbi",
            "gráfico",
            "grafico",
            "indicador",
            "dashboard",
            "desenvolvimento",
        ),
        "/planejamento/portal-ops",
    ),
    (
        JiraDemanda.CATEGORIA_CONFER,
        ("confer",),
        "/planejamento/automacao",
    ),
    (
        JiraDemanda.CATEGORIA_BRFLOW,
        (
            "brflow",
            "nível hierárquico",
            "nivel hierarquico",
            " nh ",
            "prioridade nh",
            "horário de atividade",
            "horario de atividade",
        ),
        "/planejamento/monitoramento/nh",
    ),
    (
        JiraDemanda.CATEGORIA_SUPORTE,
        (
            "suporte claro",
            "claro controle",
            "incidente claro",
        ),
        "/processos/suporte-claro",
    ),
]


def classify_demanda_text(*parts: str) -> tuple[str, str]:
    """Retorna (categoria, portal_path)."""
    blob = " ".join(p for p in parts if p).lower()
    if not blob.strip():
        return JiraDemanda.CATEGORIA_OUTRO, ""
    for categoria, keywords, path in _RULES:
        if any(kw in blob for kw in keywords):
            return categoria, path
    return JiraDemanda.CATEGORIA_OUTRO, ""


def categoria_label(categoria: str) -> str:
    return dict(JiraDemanda.CATEGORIA_CHOICES).get(categoria, categoria)
