import csv
from io import StringIO

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.controle_sla.models import EtapaGap, SlaBreach, SlaBreachHistorico
from apps.controle_sla.services.poller import get_poller
from apps.controle_sla.services.sla_eval import (
    DEFAULT_PROTOCOLOS_DIAS,
    _norm_name,
    _vigente_sla_by_workflow,
    compute_breach_moment,
    is_protocolo_atual,
    normalize_protocolos_dias,
    projecao_windows,
)
from apps.dimensoes_processos.models import DimCliente
from apps.escala_flex.rbac import MONITORING_VIEW

CRITICIDADE_LABELS = {
    SlaBreach.CRIT_BAIXO: "Baixo",
    SlaBreach.CRIT_MEDIO: "Médio",
    SlaBreach.CRIT_ALTO: "Alto",
    SlaBreach.CRIT_CRITICO: "Crítico",
}

WEEKDAY_LABELS_PT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


def _cliente_id_by_name_index() -> dict[str, int]:
    out: dict[str, int] = {}
    for cid, nome in DimCliente.objects.values_list("id_cliente", "nome"):
        key = _norm_name(nome).casefold()
        if key and key not in out:
            out[key] = cid
    return out


def _windows_for_historico_row(
    *,
    nom_cliente: str,
    id_workflow: int | None,
    cliente_by_name: dict[str, int],
    sla_map_cw: dict,
):
    if id_workflow is None:
        return []
    mz_cliente_id = cliente_by_name.get(_norm_name(nom_cliente).casefold())
    if mz_cliente_id is None:
        return []
    return projecao_windows(sla_map_cw.get((mz_cliente_id, id_workflow), []))


def _breach_moment_local(dat_antiga, sla_limite, windows=None):
    """
    Momento real do estouro contando só janelas da Projeção SLA
    (dias da semana + hora início/fim). Sem janela: wall-clock.
    """
    if not dat_antiga:
        return None
    limite = int(sla_limite or 0)
    if limite <= 0:
        return None
    return compute_breach_moment(dat_antiga, limite, list(windows or []))


def _weekday_hour_heatmap(qs) -> dict:
    """Matriz 7×24 (seg→dom × 0h→23h) pelo momento real do estouro (janelas Projeção SLA)."""
    cliente_by_name = _cliente_id_by_name_index()
    sla_map_cw = _vigente_sla_by_workflow()
    matrix = [[0] * 24 for _ in range(7)]
    for dat_antiga, sla_limite, nom_cliente, id_workflow in qs.values_list(
        "dat_registro_antigo",
        "sla_limite_segundos",
        "nom_cliente",
        "id_workflow",
    ).iterator(chunk_size=2000):
        windows = _windows_for_historico_row(
            nom_cliente=nom_cliente or "",
            id_workflow=id_workflow,
            cliente_by_name=cliente_by_name,
            sla_map_cw=sla_map_cw,
        )
        local = _breach_moment_local(dat_antiga, sla_limite, windows)
        if local is None:
            continue
        matrix[local.weekday()][local.hour] += 1
    max_v = max((cell for row in matrix for cell in row), default=0)
    return {
        "days": list(WEEKDAY_LABELS_PT),
        "hours": list(range(24)),
        "matrix": matrix,
        "max": max_v,
    }


def _filter_qs_by_weekday_hour(qs, weekday: int | None, hour: int | None):
    """Filtra pelo momento real do estouro (mesmo critério do mapa de calor)."""
    if weekday is None and hour is None:
        return qs
    cliente_by_name = _cliente_id_by_name_index()
    sla_map_cw = _vigente_sla_by_workflow()
    matching_ids: list[int] = []
    for pk, dat_antiga, sla_limite, nom_cliente, id_workflow in qs.values_list(
        "id",
        "dat_registro_antigo",
        "sla_limite_segundos",
        "nom_cliente",
        "id_workflow",
    ).iterator(chunk_size=2000):
        windows = _windows_for_historico_row(
            nom_cliente=nom_cliente or "",
            id_workflow=id_workflow,
            cliente_by_name=cliente_by_name,
            sla_map_cw=sla_map_cw,
        )
        local = _breach_moment_local(dat_antiga, sla_limite, windows)
        if local is None:
            continue
        if weekday is not None and local.weekday() != weekday:
            continue
        if hour is not None and local.hour != hour:
            continue
        matching_ids.append(pk)
    return qs.filter(pk__in=matching_ids)


def _parse_optional_int(raw, *, min_v: int, max_v: int) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < min_v or value > max_v:
        return None
    return value


def _norm_nh_name(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _nh_agentes_alocados_by_name() -> dict[str, int]:
    """
    Contagem de agentes por NH na escala do dia (mesma fonte do painel /monitoramento/nh).
    """
    from apps.escala_flex.services.kpis import compute_nh_counts

    out: dict[str, int] = {}
    for item in compute_nh_counts():
        name = (item.get("value") or "").strip()
        if not name or name == "—":
            continue
        key = _norm_nh_name(name)
        out[key] = int(item.get("count") or 0)
    return out


def _serialize_breach(
    row: SlaBreach | SlaBreachHistorico,
    *,
    nh_atendimento: str = "",
    nh_prioridade: int | None = None,
    nh_etapas_a_frente: int | None = None,
    nh_agentes_alocados: int | None = None,
) -> dict:
    return {
        "id": row.id,
        "cod_cliente": row.cod_cliente,
        "nom_cliente": row.nom_cliente,
        "nom_workflow": row.nom_workflow,
        "id_workflow": row.id_workflow,
        "cod_nivel_hierarquico": row.cod_nivel_hierarquico,
        "nom_fluxo": row.nom_fluxo,
        "tipo_analise": row.tipo_analise or "",
        "qtd_registro": row.qtd_registro,
        "qtd_fila": row.qtd_fila,
        "dat_registro_antigo": row.dat_registro_antigo.isoformat() if row.dat_registro_antigo else None,
        "idade_segundos": row.idade_segundos,
        "sla_limite_segundos": row.sla_limite_segundos,
        "excedente_segundos": row.excedente_segundos,
        "pct_sla": row.pct_sla,
        "criticidade": row.criticidade,
        "criticidade_label": CRITICIDADE_LABELS.get(row.criticidade, row.criticidade),
        "first_detected_at": row.first_detected_at.isoformat() if row.first_detected_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        # Derivado de prioridades_nh_fluxo (não persistido no breach).
        "nh_atendimento": nh_atendimento or "",
        "nh_prioridade": nh_prioridade,
        "nh_etapas_a_frente": nh_etapas_a_frente,
        # Contagem da escala do dia (painel NH); null se não há sugestão de NH.
        "nh_agentes_alocados": nh_agentes_alocados,
    }


def _serialize_breaches(rows) -> list[dict]:
    from apps.controle_sla.services.nh_atendimento_lookup import (
        build_nh_atendimento_index,
        resolve_nh_atendimento_detail,
    )

    rows = list(rows)
    if not rows:
        return []
    index = build_nh_atendimento_index()
    agentes_by_nh = _nh_agentes_alocados_by_name()
    out: list[dict] = []
    for row in rows:
        detail = resolve_nh_atendimento_detail(
            cod_cliente=row.cod_cliente,
            id_workflow=row.id_workflow,
            nom_cliente=row.nom_cliente or "",
            nom_workflow=row.nom_workflow or "",
            nom_fluxo=row.nom_fluxo or "",
            index=index,
        )
        nh_name = detail["nh_atendimento"] or ""
        agentes = agentes_by_nh.get(_norm_nh_name(nh_name), 0) if nh_name else None
        out.append(
            _serialize_breach(
                row,
                nh_atendimento=nh_name,
                nh_prioridade=detail["nh_prioridade"],
                nh_etapas_a_frente=detail["nh_etapas_a_frente"],
                nh_agentes_alocados=agentes,
            )
        )
    return out


def _serialize_gap(row: EtapaGap) -> dict:
    tratado_por = None
    if row.tratado_por_id:
        tratado_por = {
            "id": str(row.tratado_por_id),
            "username": getattr(row.tratado_por, "username", "") or "",
        }
    return {
        "id": row.id,
        "cod_cliente": row.cod_cliente,
        "nom_cliente": row.nom_cliente,
        "nom_workflow": row.nom_workflow,
        "id_workflow": row.id_workflow,
        "id_cliente_megazord": row.id_cliente_megazord,
        "cod_nivel_hierarquico": row.cod_nivel_hierarquico,
        "nom_fluxo": row.nom_fluxo,
        "qtd_fila": row.qtd_fila,
        "dat_registro_antigo": row.dat_registro_antigo.isoformat() if row.dat_registro_antigo else None,
        "situacao": row.situacao,
        "situacao_label": row.situacao_label,
        "ocorrencias": row.ocorrencias,
        "pode_tratar": row.pode_tratar,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "tratado_por": tratado_por,
        "tratado_em": row.tratado_em.isoformat() if row.tratado_em else None,
        "projecao_sla_id": row.projecao_sla_id,
        "etapa_id": row.etapa_id,
    }


def _filter_breaches_by_protocolo(qs, protocolo: str, dias: int):
    protocolo = (protocolo or "atuais").strip().lower()
    if protocolo not in {"atuais", "antigos"}:
        protocolo = "atuais"
    dias_n = normalize_protocolos_dias(dias)
    matched_ids: list[int] = []
    for row in qs.only("id", "dat_registro_antigo").iterator():
        atual = is_protocolo_atual(row.dat_registro_antigo, dias=dias_n)
        if protocolo == "atuais" and atual:
            matched_ids.append(row.id)
        elif protocolo == "antigos" and not atual:
            matched_ids.append(row.id)
    return SlaBreach.objects.filter(id__in=matched_ids), protocolo, dias_n


@api_view(["GET"])
@permission_classes(MONITORING_VIEW)
def connection_status_view(request):
    poller = get_poller()
    return Response({"ok": True, **poller.status()})


@api_view(["POST"])
@permission_classes(MONITORING_VIEW)
def connect_view(request):
    data = request.data if isinstance(request.data, dict) else {}
    import os

    matricula = str(data.get("matricula") or "").strip()
    senha = str(data.get("senha") or "")
    salvar_dados = bool(data.get("salvar_dados"))
    # Default: janela visível (igual Automações) — headless costuma quebrar SSO BrFlow
    if "headless" in data:
        headless = bool(data.get("headless"))
    else:
        headless = (os.getenv("CONTROLE_SLA_HEADLESS") or "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    if not matricula or not senha:
        return Response(
            {"ok": False, "message": "Informe matrícula e senha."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    force = bool(data.get("force"))
    result = get_poller().connect(
        matricula=matricula,
        senha=senha,
        salvar_dados=salvar_dados,
        headless=headless,
        force=force,
    )
    code = status.HTTP_200_OK if result.get("ok") else status.HTTP_409_CONFLICT
    return Response(result, status=code)


@api_view(["POST"])
@permission_classes(MONITORING_VIEW)
def disconnect_view(request):
    return Response(get_poller().disconnect())


@api_view(["POST"])
@permission_classes(MONITORING_VIEW)
def cancel_view(request):
    """Cancela conexão em andamento e limpa sessão/credenciais em memória."""
    return Response(get_poller().cancel())


@api_view(["GET"])
@permission_classes(MONITORING_VIEW)
def breaches_view(request):
    poller = get_poller()
    st = poller.status()
    dias = st.get("protocolos_dias", DEFAULT_PROTOCOLOS_DIAS)
    protocolo = str(request.query_params.get("protocolo") or "atuais").strip().lower()
    qs, protocolo, dias_n = _filter_breaches_by_protocolo(
        SlaBreach.objects.all().order_by("-pct_sla", "nom_cliente", "nom_workflow"),
        protocolo,
        dias,
    )
    results = list(qs[:500])
    return Response(
        {
            "ok": True,
            "count": qs.count(),
            "protocolo": protocolo,
            "protocolos_dias": dias_n,
            "results": _serialize_breaches(results),
            "connection": st,
        }
    )


@api_view(["GET"])
@permission_classes(MONITORING_VIEW)
def gaps_view(request):
    qs = EtapaGap.objects.select_related("tratado_por").all()[:500]
    count = EtapaGap.objects.count()
    return Response(
        {
            "ok": True,
            "count": count,
            "results": [_serialize_gap(r) for r in qs],
        }
    )


@api_view(["POST"])
@permission_classes(MONITORING_VIEW)
def gap_ignorar_view(request, pk: int):
    gap = get_object_or_404(EtapaGap.objects.select_related("tratado_por"), pk=pk)
    if gap.situacao != EtapaGap.SIT_PENDENTE:
        return Response(
            {"detail": "Somente etapas pendentes podem ser ignoradas.", "gap": _serialize_gap(gap)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    gap.situacao = EtapaGap.SIT_IGNORADA
    gap.tratado_por = request.user
    gap.tratado_em = timezone.now()
    gap.save(update_fields=["situacao", "tratado_por", "tratado_em"])
    return Response({"ok": True, "gap": _serialize_gap(gap)})


@api_view(["POST"])
@permission_classes(MONITORING_VIEW)
def gap_cadastrar_view(request, pk: int):
    """Marca gap como Cadastrada após criar a etapa no Megazord (ou ciclo SLA legado)."""
    gap = get_object_or_404(EtapaGap.objects.select_related("tratado_por"), pk=pk)
    if gap.situacao != EtapaGap.SIT_PENDENTE:
        return Response(
            {
                "detail": "Somente etapas pendentes podem ser cadastradas.",
                "gap": _serialize_gap(gap),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    data = request.data if isinstance(request.data, dict) else {}
    etapa_id = data.get("etapa_id")
    proj_id = data.get("projecao_sla_id")
    proj_ids = data.get("projecao_sla_ids") or []
    if proj_id is None and isinstance(proj_ids, list) and proj_ids:
        proj_id = proj_ids[0]

    etapa_id_int = None
    proj_id_int = None
    try:
        if etapa_id is not None:
            etapa_id_int = int(etapa_id)
        if proj_id is not None:
            proj_id_int = int(proj_id)
    except (TypeError, ValueError):
        return Response(
            {"detail": "Informe etapa_id ou projecao_sla_id válido."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if (etapa_id_int is None or etapa_id_int <= 0) and (proj_id_int is None or proj_id_int <= 0):
        return Response(
            {"detail": "Informe etapa_id do cadastro criado."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    gap.situacao = EtapaGap.SIT_CADASTRADA
    if etapa_id_int and etapa_id_int > 0:
        gap.etapa_id = etapa_id_int
    if proj_id_int and proj_id_int > 0:
        gap.projecao_sla_id = proj_id_int
    gap.tratado_por = request.user
    gap.tratado_em = timezone.now()
    gap.save(
        update_fields=["situacao", "etapa_id", "projecao_sla_id", "tratado_por", "tratado_em"]
    )
    return Response({"ok": True, "gap": _serialize_gap(gap)})


@api_view(["GET"])
@permission_classes(MONITORING_VIEW)
def summary_view(request):
    poller = get_poller()
    st = poller.status()
    dias = st.get("protocolos_dias", DEFAULT_PROTOCOLOS_DIAS)
    protocolo = str(request.query_params.get("protocolo") or "atuais").strip().lower()
    qs, protocolo, dias_n = _filter_breaches_by_protocolo(SlaBreach.objects.all(), protocolo, dias)
    return Response(
        {
            "ok": True,
            "breach_count": qs.count(),
            "critico_count": qs.filter(criticidade=SlaBreach.CRIT_CRITICO).count(),
            "gap_count": EtapaGap.objects.count(),
            "gap_pendente_count": EtapaGap.objects.filter(situacao=EtapaGap.SIT_PENDENTE).count(),
            "historico_count": SlaBreachHistorico.objects.count(),
            "protocolo": protocolo,
            "protocolos_dias": dias_n,
            "connection": st,
        }
    )


@api_view(["GET"])
@permission_classes(MONITORING_VIEW)
def historico_view(request):
    """Lista histórico de protocolos (padrão: apenas estourados / crítico)."""
    from django.db.models import Avg, Count

    criticidade = str(request.query_params.get("criticidade") or SlaBreach.CRIT_CRITICO).strip().lower()
    fluxo = str(request.query_params.get("fluxo") or "").strip()
    conc_cliente = str(request.query_params.get("cliente") or "").strip()
    conc_workflow = str(request.query_params.get("workflow") or "").strip()

    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get("page_size") or 50)
    except (TypeError, ValueError):
        page_size = 50
    page_size = max(1, min(page_size, 200))

    qs = SlaBreachHistorico.objects.all().order_by("-last_seen_at", "-pct_sla", "nom_cliente")
    if criticidade and criticidade != "todos":
        valid = {c[0] for c in SlaBreach.CRITICIDADE_CHOICES}
        if criticidade in valid:
            qs = qs.filter(criticidade=criticidade)
    if fluxo:
        qs = qs.filter(nom_fluxo__icontains=fluxo)

    weekday = _parse_optional_int(request.query_params.get("weekday"), min_v=0, max_v=6)
    hour = _parse_optional_int(request.query_params.get("hour"), min_v=0, max_v=23)
    # Filtros de concentração (exatos) — só a tabela; indicadores usam o conjunto completo.
    # Indicadores/mapa no conjunto completo; tabela pode fatiar por célula/categoria.
    summary_heatmap = _weekday_hour_heatmap(qs)
    agg = qs.aggregate(
        tempo_medio_estouro_segundos=Avg("excedente_segundos"),
        clientes_distintos=Count("nom_cliente", distinct=True),
        workflows_distintos=Count("nom_workflow", distinct=True),
    )
    by_cliente = [
        {"label": row["nom_cliente"] or "—", "total": row["total"]}
        for row in qs.values("nom_cliente").annotate(total=Count("id")).order_by("-total", "nom_cliente")
    ]
    by_workflow = [
        {"label": (row["nom_workflow"] or "").strip() or "—", "total": row["total"]}
        for row in qs.values("nom_workflow").annotate(total=Count("id")).order_by("-total", "nom_workflow")
    ]

    table_qs = qs
    if conc_cliente:
        table_qs = table_qs.filter(nom_cliente=conc_cliente)
    if conc_workflow:
        table_qs = table_qs.filter(nom_workflow=conc_workflow)
    table_qs = _filter_qs_by_weekday_hour(table_qs, weekday, hour)
    total = table_qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    results = _serialize_breaches(table_qs[start:end])

    return Response(
        {
            "ok": True,
            "count": total,
            "page": page,
            "page_size": page_size,
            "has_next": end < total,
            "has_previous": page > 1,
            "criticidade": criticidade or "todos",
            "weekday": weekday,
            "hour": hour,
            "cliente": conc_cliente or None,
            "workflow": conc_workflow or None,
            "results": results,
            "summary": {
                "total": qs.count(),
                "clientes_distintos": agg["clientes_distintos"] or 0,
                "workflows_distintos": agg["workflows_distintos"] or 0,
                "tempo_medio_estouro_segundos": (
                    float(agg["tempo_medio_estouro_segundos"])
                    if agg["tempo_medio_estouro_segundos"] is not None
                    else None
                ),
                "by_cliente": by_cliente,
                "by_workflow": by_workflow,
                "by_weekday_hour": summary_heatmap,
            },
        }
    )


@api_view(["GET"])
@permission_classes(MONITORING_VIEW)
def export_view(request):
    kind = str(request.query_params.get("kind") or "breaches").strip().lower()
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";")
    poller = get_poller()
    st = poller.status()
    dias = st.get("protocolos_dias", DEFAULT_PROTOCOLOS_DIAS)
    protocolo = str(request.query_params.get("protocolo") or "atuais").strip().lower()

    if kind == "gaps":
        writer.writerow(
            [
                "cod_cliente",
                "nom_cliente",
                "nom_workflow",
                "cod_nivel_hierarquico",
                "nom_fluxo",
                "qtd_fila",
                "situacao",
                "ocorrencias",
                "dat_registro_antigo",
                "first_seen_at",
                "last_seen_at",
                "tratado_por",
                "tratado_em",
                "projecao_sla_id",
            ]
        )
        for row in EtapaGap.objects.select_related("tratado_por").all().iterator():
            writer.writerow(
                [
                    row.cod_cliente,
                    row.nom_cliente,
                    row.nom_workflow,
                    row.cod_nivel_hierarquico or "",
                    row.nom_fluxo,
                    row.qtd_fila if row.qtd_fila is not None else "",
                    row.situacao_label,
                    row.ocorrencias,
                    row.dat_registro_antigo.isoformat() if row.dat_registro_antigo else "",
                    row.first_seen_at.isoformat() if row.first_seen_at else "",
                    row.last_seen_at.isoformat() if row.last_seen_at else "",
                    getattr(row.tratado_por, "username", "") if row.tratado_por_id else "",
                    row.tratado_em.isoformat() if row.tratado_em else "",
                    row.projecao_sla_id or "",
                ]
            )
        filename = "controle_sla_etapas_nao_encontradas.csv"
    else:
        from apps.controle_sla.services.nh_atendimento_lookup import (
            build_nh_atendimento_index,
            resolve_nh_atendimento_detail,
        )

        qs, protocolo, _dias_n = _filter_breaches_by_protocolo(
            SlaBreach.objects.all(), protocolo, dias
        )
        nh_index = build_nh_atendimento_index()
        agentes_by_nh = _nh_agentes_alocados_by_name()
        writer.writerow(
            [
                "nom_cliente",
                "nom_workflow",
                "nom_fluxo",
                "nh_atendimento",
                "nh_prioridade",
                "nh_etapas_a_frente",
                "nh_agentes_alocados",
                "tipo_analise",
                "qtd_fila",
                "dat_registro_antigo",
                "idade_segundos",
                "sla_limite_segundos",
                "excedente_segundos",
                "pct_sla",
                "criticidade",
                "first_detected_at",
                "last_seen_at",
                "protocolo",
            ]
        )
        for row in qs.iterator():
            detail = resolve_nh_atendimento_detail(
                cod_cliente=row.cod_cliente,
                id_workflow=row.id_workflow,
                nom_cliente=row.nom_cliente or "",
                nom_workflow=row.nom_workflow or "",
                nom_fluxo=row.nom_fluxo or "",
                index=nh_index,
            )
            nh_name = detail["nh_atendimento"] or ""
            agentes = agentes_by_nh.get(_norm_nh_name(nh_name), 0) if nh_name else ""
            writer.writerow(
                [
                    row.nom_cliente,
                    row.nom_workflow,
                    row.nom_fluxo,
                    nh_name,
                    detail["nh_prioridade"] if detail["nh_prioridade"] is not None else "",
                    detail["nh_etapas_a_frente"] if detail["nh_etapas_a_frente"] is not None else "",
                    agentes,
                    row.tipo_analise or "",
                    row.qtd_fila if row.qtd_fila is not None else "",
                    row.dat_registro_antigo.isoformat() if row.dat_registro_antigo else "",
                    row.idade_segundos,
                    row.sla_limite_segundos,
                    row.excedente_segundos,
                    row.pct_sla,
                    CRITICIDADE_LABELS.get(row.criticidade, row.criticidade),
                    row.first_detected_at.isoformat() if row.first_detected_at else "",
                    row.last_seen_at.isoformat() if row.last_seen_at else "",
                    protocolo,
                ]
            )
        filename = f"controle_sla_breaches_{protocolo}.csv"

    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
