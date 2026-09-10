# -*- coding: utf-8 -*-
"""APIs REST de configuração permanente D-1."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from time import perf_counter

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Max, Min, Q, When
from django.db.models.expressions import RawSQL
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from apps.automacoes.permissions import can_configure_automacoes
from apps.replicacao_d1.normalization import normalize_key
from apps.replicacao_d1.services.workflow_column_filters import build_workflow_column_filter_options
from apps.replicacao_d1.services.workflow_duplicate import duplicate_workflow
from apps.replicacao_d1.models import (
    ReplicacaoD1Categoria,
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1ConfigHistorico,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1MetaMensal,
    ReplicacaoD1RetroativoConfig,
    ReplicacaoD1RetroativoWorkflow,
    ReplicacaoD1Segmento,
    ReplicacaoD1Run,
    ReplicacaoD1SchedulerState,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.serializers_config import (
    FonteAtivarSerializer,
    ImportApplyRequestSerializer,
    ImportPreviewRequestSerializer,
    ProjecaoMensalQuerySerializer,
    ReplicacaoD1CalculadoraSerializer,
    ReplicacaoD1CategoriaSerializer,
    ReplicacaoD1ClienteSerializer,
    ReplicacaoD1ConfigGeralSerializer,
    ReplicacaoD1EscalaDiaSerializer,
    ReplicacaoD1HistoricoSerializer,
    ReplicacaoD1LedgerAjusteSerializer,
    ReplicacaoD1LedgerPurgeRunsSerializer,
    ReplicacaoD1LedgerSerializer,
    ReplicacaoD1MetaMensalSerializer,
    ReplicacaoD1RetroativoSerializer,
    ReplicacaoD1SegmentoSerializer,
    ReplicacaoD1WorkflowBulkStatusSerializer,
    ReplicacaoD1WorkflowDuplicateSerializer,
    ReplicacaoD1WorkflowSerializer,
    SnapshotPreviewQuerySerializer,
)
from apps.replicacao_d1.services.config_audit import registrar_historico
from apps.replicacao_d1.services.config_dto import RunOptions, normalizar_calculadora_params
from apps.replicacao_d1.services.config_import import apply_import, preview_import
from apps.replicacao_d1.services.config_export import build_config_csv
from apps.replicacao_d1.services.config_snapshot import (
    bump_config_version,
    load_persistent_config,
    preview_execution_snapshot,
    validate_persistent_config,
)
from apps.replicacao_d1.services.fonte_banco import ativar_fonte_banco, desativar_fonte_banco
from apps.replicacao_d1.services.ledger import aplicar_ajuste_manual, carregar_consumo_meta_mensal, purge_ledger_por_runs
from apps.replicacao_d1.services.plan_validation import build_plan_warning_groups, exclude_deleted_plans
from apps.replicacao_d1.services.projecao import gerar_projecao_mensal
from apps.replicacao_d1.views.base import ReplicacaoD1APIView
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord
from rest_framework.permissions import IsAuthenticated


class ReplicacaoD1ConfigWriteAPIView(APIView):
    permission_classes = [IsAuthenticated, portal_perm(R.PLANEJAMENTO_AUTOMACAO_CONFIGURE)]


def _parse_bool(value):
    if value is None:
        return None
    return str(value).lower() in ("1", "true", "yes")


def _query_param_values(request, param: str) -> list[str]:
    values = [str(value).strip() for value in request.query_params.getlist(param) if str(value).strip()]
    if values:
        return values
    single = (request.query_params.get(param) or "").strip()
    return [single] if single else []


def _apply_id_in_filter(queryset, request, param: str, field: str):
    ids: list[int] = []
    for raw in _query_param_values(request, param):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if ids:
        queryset = queryset.filter(**{f"{field}__in": ids})
    return queryset


def _apply_int_in_filter(queryset, request, param: str, field: str):
    values: list[int] = []
    for raw in _query_param_values(request, param):
        try:
            values.append(int(str(raw).replace(".", "").replace(",", "")))
        except (TypeError, ValueError):
            continue
    if values:
        queryset = queryset.filter(**{f"{field}__in": values})
    return queryset


def _apply_text_in_filter(queryset, request, param: str, field: str):
    values = _query_param_values(request, param)
    if values:
        queryset = queryset.filter(**{f"{field}__in": values})
    return queryset


def _apply_workflow_cliente_filter(queryset, request):
    """Aceita ID numérico, nome do cliente ou "—" para workflows sem cliente."""
    values = _query_param_values(request, "cliente")
    if not values:
        return queryset
    null_markers = {"—", "-"}
    ids: list[int] = []
    names: list[str] = []
    has_null = False
    for raw in values:
        if raw in null_markers:
            has_null = True
            continue
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            names.append(raw)
    clauses = []
    if ids:
        clauses.append(Q(cliente_id__in=ids))
    if names:
        keys = [normalize_key(name) for name in names]
        keys = [key for key in keys if key]
        if keys:
            clauses.append(Q(cliente__chave_normalizada__in=keys))
    if has_null:
        clauses.append(Q(cliente_id__isnull=True))
    if not clauses:
        return queryset
    combined = clauses[0]
    for clause in clauses[1:]:
        combined |= clause
    return queryset.filter(combined)


def _parse_date_param(value, field_name):
    if not value:
        return None, None
    try:
        return date.fromisoformat(str(value).strip()), None
    except ValueError:
        return None, {field_name: [f"Data inválida: {value!r}. Use formato ISO (YYYY-MM-DD)."]}


def _parse_datetime_param(value, field_name, *, end_of_day=False):
    if not value:
        return None, None
    raw = str(value).strip()
    try:
        if "T" in raw or " " in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return dt, None
        parsed_date = date.fromisoformat(raw)
        t = time.max if end_of_day else time.min
        dt = datetime.combine(parsed_date, t)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt, None
    except ValueError:
        return None, {
            field_name: [f"Data/hora inválida: {value!r}. Use formato ISO (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)."]
        }


def _paginate_queryset(queryset, request, serializer_class):
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(500, max(1, int(request.query_params.get("page_size") or 50)))
    except (TypeError, ValueError):
        page_size = 50
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page)
    return Response(
        {
            "count": paginator.count,
            "page": page_obj.number,
            "page_size": page_size,
            "num_pages": paginator.num_pages,
            "results": serializer_class(page_obj.object_list, many=True).data,
        }
    )


def _apply_ordering(queryset, request, allowed: dict[str, str], default: tuple[str, ...]):
    """Aplica ordenação de uma allowlist e mantém paginação determinística."""
    raw = str(request.query_params.get("ordering") or "").strip()
    descending = raw.startswith("-")
    key = raw[1:] if descending else raw
    field = allowed.get(key)
    if field:
        ordering = [f"-{field}" if descending else field]
    else:
        ordering = list(default)
    normalized = {item.lstrip("-") for item in ordering}
    if "id" not in normalized and "pk" not in normalized:
        ordering.append("-pk" if ordering and ordering[0].startswith("-") else "pk")
    return queryset.order_by(*ordering)


def _scheduler_datetime(value):
    """Normaliza timestamps ISO persistidos pelo agendador sem quebrar o resumo."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _execution_summary(run: ReplicacaoD1Run | None) -> dict | None:
    """Normaliza runs antigos afetados pela perda de estado no modo banco-only."""
    if run is None:
        return None
    status_value = run.status_canonical
    started_at = run.started_at
    finished_at = run.finished_at
    erro_resumo = run.erro_resumo

    if (
        status_value == ReplicacaoD1Run.STATUS_FAILED
        and not run.erro_codigo
        and not erro_resumo
        and not started_at
        and not finished_at
    ):
        close_event = run.events.filter(phase="close").order_by("-created_at", "-id").first()
        payload = close_event.payload if close_event and isinstance(close_event.payload, dict) else {}
        if close_event and int(payload.get("workflows_failed") or 0) == 0:
            # O processo chegou ao fechamento, mas os estados SALVO_OK não foram
            # persistidos. Sem falha explícita, o resultado correto é parcial.
            status_value = ReplicacaoD1Run.STATUS_PARTIAL
            finished_at = close_event.created_at
            manifest = run.events.filter(phase="manifest").order_by("-created_at", "-id").first()
            manifest_payload = manifest.payload if manifest and isinstance(manifest.payload, dict) else {}
            result = manifest_payload.get("result") if isinstance(manifest_payload.get("result"), dict) else {}
            started_at = _scheduler_datetime(result.get("started_at"))
            finished_at = _scheduler_datetime(result.get("finished_at")) or finished_at

    return {
        "run_id": run.run_id,
        "status": status_value,
        "data_referencia_d1": run.data_referencia_d1,
        "protocolos_total": run.protocolos_total,
        "workflows_total": run.workflows_total,
        "erro_resumo": erro_resumo,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _scheduler_summary(geral):
    scheduler = ReplicacaoD1SchedulerState.objects.filter(key="replicacao_d1").first()
    state = scheduler.state if scheduler and isinstance(scheduler.state, dict) else {}
    heartbeat = _scheduler_datetime(state.get("last_heartbeat") or state.get("heartbeat_at"))

    status_text = str(state.get("status") or "").lower()
    if not geral.agendamento_ativo:
        runtime_status = "disabled"
    elif heartbeat is None:
        runtime_status = "unknown"
    elif heartbeat < timezone.now() - timedelta(minutes=15):
        runtime_status = "stale"
    elif status_text in {"error", "failed", "stale"}:
        runtime_status = "stale"
    elif state.get("enabled") is False or status_text in {"disabled", "stopped"}:
        # Agendamento ligado na config, mas o processo do robô não está rodando.
        runtime_status = "unknown"
    else:
        runtime_status = "healthy"

    next_planning = _scheduler_datetime(
        state.get("next_planning_at") or state.get("proximo_planejamento")
    )
    next_execution = _scheduler_datetime(
        state.get("next_execution_at") or state.get("proxima_execucao")
    )
    return {
        "configurado": geral.agendamento_ativo,
        "runtime_status": runtime_status,
        "ultimo_heartbeat": heartbeat,
        "proximo_planejamento": next_planning,
        "proxima_execucao": next_execution,
        "hora_planejamento": geral.agendamento_hora_planejamento,
        "hora_execucao": geral.agendamento_hora_execucao,
    }


class ConfigGeralView(ReplicacaoD1APIView):
    def get(self, request):
        geral = ReplicacaoD1ConfigGeral.get_solo()
        return Response(ReplicacaoD1ConfigGeralSerializer(geral).data)

    def patch(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        with transaction.atomic():
            ReplicacaoD1ConfigGeral.get_solo()
            geral = ReplicacaoD1ConfigGeral.objects.select_for_update().get(pk=ReplicacaoD1ConfigGeral.SINGLETON_PK)
            before = ReplicacaoD1ConfigGeralSerializer(geral).data
            ser = ReplicacaoD1ConfigGeralSerializer(geral, data=request.data, partial=True)
            ser.is_valid(raise_exception=True)
            obj = ser.save(updated_by=request.user)
            bump_config_version(user=request.user)
            obj.refresh_from_db()
            registrar_historico(
                "ReplicacaoD1ConfigGeral",
                obj.pk,
                "patch",
                request.user,
                before,
                ReplicacaoD1ConfigGeralSerializer(obj).data,
            )
        return Response(ReplicacaoD1ConfigGeralSerializer(obj).data)


class ConfigCalculadoraView(ReplicacaoD1APIView):
    def get(self, request):
        geral = ReplicacaoD1ConfigGeral.get_solo()
        return Response(
            {
                "meta_produ_diaria": geral.meta_produ_diaria,
                "meta_produ_diaria_case": geral.meta_produ_diaria_case,
                "meta_produ_diaria_bio": geral.meta_produ_diaria_bio,
                "meta_produ_diaria_redoc": geral.meta_produ_diaria_redoc,
                "usar_amostra_mix_manual_automatico": geral.usar_amostra_mix_manual_automatico,
                "amostra_pct_manual": geral.amostra_pct_manual,
                "amostra_pct_automatico": geral.amostra_pct_automatico,
                "calculadora_params": normalizar_calculadora_params(geral.calculadora_params),
            }
        )

    def patch(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1CalculadoraSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            ReplicacaoD1ConfigGeral.get_solo()
            geral = ReplicacaoD1ConfigGeral.objects.select_for_update().get(pk=ReplicacaoD1ConfigGeral.SINGLETON_PK)
            before = {
                "calculadora_params": geral.calculadora_params,
                "meta_produ_diaria": str(geral.meta_produ_diaria),
                "meta_produ_diaria_case": str(geral.meta_produ_diaria_case),
                "meta_produ_diaria_bio": str(geral.meta_produ_diaria_bio),
                "meta_produ_diaria_redoc": str(geral.meta_produ_diaria_redoc),
                "usar_amostra_mix_manual_automatico": geral.usar_amostra_mix_manual_automatico,
                "amostra_pct_manual": geral.amostra_pct_manual,
                "amostra_pct_automatico": geral.amostra_pct_automatico,
            }
            if "calculadora_params" in ser.validated_data:
                geral.calculadora_params = ser.validated_data["calculadora_params"]
            if "meta_produ_diaria" in ser.validated_data:
                geral.meta_produ_diaria = ser.validated_data["meta_produ_diaria"]
            if "meta_produ_diaria_case" in ser.validated_data:
                geral.meta_produ_diaria_case = ser.validated_data["meta_produ_diaria_case"]
            if "meta_produ_diaria_bio" in ser.validated_data:
                geral.meta_produ_diaria_bio = ser.validated_data["meta_produ_diaria_bio"]
            if "meta_produ_diaria_redoc" in ser.validated_data:
                geral.meta_produ_diaria_redoc = ser.validated_data["meta_produ_diaria_redoc"]
            if "usar_amostra_mix_manual_automatico" in ser.validated_data:
                geral.usar_amostra_mix_manual_automatico = ser.validated_data[
                    "usar_amostra_mix_manual_automatico"
                ]
            if "amostra_pct_manual" in ser.validated_data:
                geral.amostra_pct_manual = ser.validated_data["amostra_pct_manual"]
            if "amostra_pct_automatico" in ser.validated_data:
                geral.amostra_pct_automatico = ser.validated_data["amostra_pct_automatico"]
            geral.updated_by = request.user
            geral.save()
            bump_config_version(user=request.user)
            registrar_historico(
                "ReplicacaoD1ConfigGeral",
                geral.pk,
                "patch_calculadora",
                request.user,
                before,
                {
                    "calculadora_params": geral.calculadora_params,
                    "meta_produ_diaria": str(geral.meta_produ_diaria),
                    "meta_produ_diaria_case": str(geral.meta_produ_diaria_case),
                "meta_produ_diaria_bio": str(geral.meta_produ_diaria_bio),
                "meta_produ_diaria_redoc": str(geral.meta_produ_diaria_redoc),
                    "usar_amostra_mix_manual_automatico": geral.usar_amostra_mix_manual_automatico,
                    "amostra_pct_manual": geral.amostra_pct_manual,
                    "amostra_pct_automatico": geral.amostra_pct_automatico,
                },
            )
        return Response(
            {
                "meta_produ_diaria": geral.meta_produ_diaria,
                "meta_produ_diaria_case": geral.meta_produ_diaria_case,
                "meta_produ_diaria_bio": geral.meta_produ_diaria_bio,
                "meta_produ_diaria_redoc": geral.meta_produ_diaria_redoc,
                "usar_amostra_mix_manual_automatico": geral.usar_amostra_mix_manual_automatico,
                "amostra_pct_manual": geral.amostra_pct_manual,
                "amostra_pct_automatico": geral.amostra_pct_automatico,
                "calculadora_params": normalizar_calculadora_params(geral.calculadora_params),
            }
        )


CONFIG_SUMMARY_CACHE_SECONDS = 60


def _retroativo_workflow_links(retro: ReplicacaoD1RetroativoConfig):
    return (
        ReplicacaoD1RetroativoWorkflow.objects.filter(
            config=retro,
            ativo=True,
            workflow__ativo=True,
            workflow__status=ReplicacaoD1Workflow.STATUS_ATIVO,
        )
        .select_related("workflow", "workflow__cliente")
        .order_by("workflow__nome_canonico")
    )


def _build_retroativo_payload(*, hoje: date | None = None) -> dict:
    retro = ReplicacaoD1RetroativoConfig.get_solo()
    links = list(_retroativo_workflow_links(retro))
    workflow_ids = [link.workflow_id for link in links]
    ref = hoje or timezone.now().date()
    periodo_expirado = bool(retro.retroativo_data_fim and retro.retroativo_data_fim < ref)
    aviso_periodo = ""
    if periodo_expirado:
        aviso_periodo = f"Período retroativo encerrado em {retro.retroativo_data_fim.isoformat()}."
    return {
        "retroativo_ativo": retro.retroativo_ativo,
        "retroativo_data_inicio": retro.retroativo_data_inicio.isoformat() if retro.retroativo_data_inicio else None,
        "retroativo_data_fim": retro.retroativo_data_fim.isoformat() if retro.retroativo_data_fim else None,
        "workflow_ids": workflow_ids,
        "workflows": [
            {
                "id": link.workflow_id,
                "nome_canonico": link.workflow.nome_canonico,
                "nome_d1": link.workflow.nome_d1,
                "chave_normalizada": link.workflow.chave_normalizada,
                "chave_d1_normalizada": link.workflow.chave_d1_normalizada,
                "cliente_nome": link.workflow.cliente.nome if link.workflow.cliente_id else "",
                "ativo": True,
            }
            for link in links
        ],
        "workflows_total": len(links),
        "periodo_expirado": periodo_expirado,
        "aviso_periodo": aviso_periodo,
    }


def _config_summary_static_payload(geral: ReplicacaoD1ConfigGeral) -> tuple[dict, bool]:
    cache_key = f"replicacao_d1:config_summary:v{int(geral.config_version or 0)}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached, True

    clientes_agg = ReplicacaoD1Cliente.objects.aggregate(
        ativos=Count("id", filter=Q(ativo=True)),
        inativos=Count("id", filter=Q(ativo=False)),
    )
    workflows_agg = ReplicacaoD1Workflow.objects.aggregate(
        ativos=Count("id", filter=Q(ativo=True)),
        inativos=Count("id", filter=Q(ativo=False)),
        pendentes=Count("id", filter=Q(status=ReplicacaoD1Workflow.STATUS_PENDENTE)),
    )
    segmentos_agg = ReplicacaoD1Segmento.objects.aggregate(
        ativos=Count("id", filter=Q(ativo=True)),
    )
    categorias_agg = ReplicacaoD1Categoria.objects.aggregate(
        ativos=Count("id", filter=Q(ativo=True)),
    )
    metas_agg = ReplicacaoD1MetaMensal.objects.aggregate(competencia_mais_recente=Max("competencia"))
    escala_agg = ReplicacaoD1EscalaDia.objects.aggregate(
        data_min=Min("data"),
        data_max=Max("data"),
        total_dias=Count("id"),
    )
    cfg = load_persistent_config()
    pendentes = workflows_agg.get("pendentes") or 0
    avisos = [f"{pendentes} workflow(s) pendente(s) de configuração."] if pendentes else []
    retro_payload = _build_retroativo_payload()
    if retro_payload["retroativo_ativo"] and retro_payload["aviso_periodo"]:
        avisos.append(retro_payload["aviso_periodo"])
    if geral.fonte_banco_ativa and geral.fallback_parquet_dias_ausentes:
        avisos.append(
            "Fallback parquet ativo: dias ausentes no banco serão completados com brflow-detalhado-tratado (dia exato)."
        )
    updated_by_username = geral.updated_by.username if geral.updated_by_id and geral.updated_by else ""
    payload = {
        "fonte_banco_ativa": geral.fonte_banco_ativa,
        "fallback_parquet_dias_ausentes": geral.fallback_parquet_dias_ausentes,
        "config_version": geral.config_version,
        "config_hash": geral.config_hash,
        "updated_at": geral.updated_at,
        "updated_by_username": updated_by_username,
        "contagens": {
            "clientes": {
                "ativos": clientes_agg.get("ativos") or 0,
                "inativos": clientes_agg.get("inativos") or 0,
            },
            "workflows": {
                "ativos": workflows_agg.get("ativos") or 0,
                "inativos": workflows_agg.get("inativos") or 0,
                "pendentes": pendentes,
            },
            "segmentos": {"ativos": segmentos_agg.get("ativos") or 0},
            "categorias": {"ativos": categorias_agg.get("ativos") or 0},
        },
        "metas": {"competencia_mais_recente": metas_agg.get("competencia_mais_recente")},
        "escala": {
            "data_min": escala_agg.get("data_min"),
            "data_max": escala_agg.get("data_max"),
            "total_dias": escala_agg.get("total_dias") or 0,
        },
        "validation_errors": validate_persistent_config(cfg),
        "avisos": avisos,
        "retroativo": {
            "retroativo_ativo": retro_payload["retroativo_ativo"],
            "workflows_total": retro_payload["workflows_total"],
            "retroativo_data_inicio": retro_payload["retroativo_data_inicio"],
            "retroativo_data_fim": retro_payload["retroativo_data_fim"],
            "periodo_expirado": retro_payload["periodo_expirado"],
            "aviso_periodo": retro_payload["aviso_periodo"],
        },
    }
    cache.set(cache_key, payload, timeout=CONFIG_SUMMARY_CACHE_SECONDS)
    return payload, False


class ConfigRetroativoView(ReplicacaoD1APIView):
    def get(self, request):
        return Response(_build_retroativo_payload())

    def patch(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1RetroativoSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        with transaction.atomic():
            retro = ReplicacaoD1RetroativoConfig.objects.select_for_update().get(
                pk=ReplicacaoD1RetroativoConfig.SINGLETON_PK
            )
            before = _build_retroativo_payload()
            if "retroativo_ativo" in data:
                retro.retroativo_ativo = data["retroativo_ativo"]
            if "retroativo_data_inicio" in data:
                retro.retroativo_data_inicio = data["retroativo_data_inicio"]
            if "retroativo_data_fim" in data:
                retro.retroativo_data_fim = data["retroativo_data_fim"]
            retro.updated_by = request.user
            retro.save()
            if "workflow_ids" in data:
                desired = set(data["workflow_ids"])
                existing = {
                    link.workflow_id: link
                    for link in ReplicacaoD1RetroativoWorkflow.objects.filter(config=retro)
                }
                for workflow_id in desired - set(existing):
                    ReplicacaoD1RetroativoWorkflow.objects.create(
                        config=retro,
                        workflow_id=workflow_id,
                        ativo=True,
                    )
                for workflow_id, link in existing.items():
                    link.ativo = workflow_id in desired
                    link.save(update_fields=["ativo"])
            bump_config_version(user=request.user)
            after = _build_retroativo_payload()
            registrar_historico(
                "ReplicacaoD1RetroativoConfig",
                retro.pk,
                "patch",
                request.user,
                before,
                after,
            )
        return Response(after)


class ConfigResumoView(ReplicacaoD1APIView):
    def get(self, request):
        request_started = perf_counter()
        try:
            geral = ReplicacaoD1ConfigGeral.objects.select_related("updated_by").get(
                pk=ReplicacaoD1ConfigGeral.SINGLETON_PK
            )
        except ReplicacaoD1ConfigGeral.DoesNotExist:
            geral = ReplicacaoD1ConfigGeral.get_solo()

        static_started = perf_counter()
        static_payload, cache_hit = _config_summary_static_payload(geral)
        static_duration = (perf_counter() - static_started) * 1000

        operational_started = perf_counter()
        planos_pendentes = exclude_deleted_plans(
            ReplicacaoD1Run.objects.exclude(plan_hash="").filter(
                validation_status=ReplicacaoD1Run.VALIDATION_PENDING,
            )
        )
        plano_pendente = planos_pendentes.order_by("-created_at", "-id").first()
        ultima_execucao = (
            ReplicacaoD1Run.objects.exclude(
                status_canonical__in=[
                    ReplicacaoD1Run.STATUS_PLANNED,
                    ReplicacaoD1Run.STATUS_LEGACY,
                ]
            )
            .order_by("-synced_at", "-created_at")
            .first()
        )
        ultima_fonte = (
            RotinaDetalhadoBrutoRecord.objects.values("report_date")
            .annotate(row_count=Count("id"))
            .order_by("-report_date")
            .first()
        )
        operation_payload = {
            "planos_pendentes": planos_pendentes.count(),
            "plano_pendente_recente": (
                {
                    "run_id": plano_pendente.run_id,
                    "data_referencia_d1": plano_pendente.data_referencia_d1,
                    "protocolos_total": plano_pendente.protocolos_total,
                    "workflows_total": plano_pendente.workflows.filter(
                        protocolos_planejados__gt=0
                    ).count(),
                    "warnings_count": len(build_plan_warning_groups(plano_pendente.plan_warnings)),
                    "created_at": plano_pendente.created_at,
                }
                if plano_pendente
                else None
            ),
            "ultima_execucao": _execution_summary(ultima_execucao),
            "ultima_fonte": (
                {
                    "source": "rotina_detalhado_bruto_record",
                    "report_date": ultima_fonte["report_date"],
                    "row_count": ultima_fonte["row_count"],
                }
                if ultima_fonte
                else None
            ),
            "agendamento": _scheduler_summary(geral),
        }
        operational_duration = (perf_counter() - operational_started) * 1000
        response = Response({**static_payload, "operacao": operation_payload})
        cache_label = "hit" if cache_hit else "miss"
        total_duration = (perf_counter() - request_started) * 1000
        response["Server-Timing"] = (
            f'd1_summary_static;dur={static_duration:.1f};desc="{cache_label}", '
            f"d1_summary_operational;dur={operational_duration:.1f}, "
            f"d1_summary_total;dur={total_duration:.1f}"
        )
        return response


class ConfigProjecaoMensalView(ReplicacaoD1APIView):
    def get(self, request):
        ser = ProjecaoMensalQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        return Response(gerar_projecao_mensal(ser.validated_data["competencia"]))


class ConfigExportCsvView(ReplicacaoD1APIView):
    def get(self, request, resource: str):
        try:
            filename, content = build_config_csv(resource)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(content, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class ClienteListCreateView(ReplicacaoD1APIView):
    def get(self, request):
        qs = (
            ReplicacaoD1Cliente.objects.select_related("segmento", "categoria")
            .annotate(workflows_count=Count("workflows", distinct=True))
        )
        ativo = _parse_bool(request.query_params.get("ativo"))
        if ativo is not None:
            qs = qs.filter(ativo=ativo)
        qs = _apply_id_in_filter(qs, request, "segmento", "segmento_id")
        qs = _apply_id_in_filter(qs, request, "categoria", "categoria_id")
        qs = _apply_text_in_filter(qs, request, "nome", "nome")
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(nome__icontains=q)
        qs = _apply_int_in_filter(qs, request, "meta_mensal", "meta_mensal")
        qs = _apply_int_in_filter(qs, request, "workflows_count", "workflows_count")
        qs = _apply_ordering(
            qs,
            request,
            {
                "nome": "nome",
                "segmento": "segmento__nome",
                "categoria": "categoria__nome",
                "meta_mensal": "meta_mensal",
                "workflows_count": "workflows_count",
                "ativo": "ativo",
            },
            ("nome",),
        )
        return _paginate_queryset(qs, request, ReplicacaoD1ClienteSerializer)

    def post(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1ClienteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(created_by=request.user, updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Cliente", obj.pk, "create", request.user, None, ser.data)
        return Response(ReplicacaoD1ClienteSerializer(obj).data, status=status.HTTP_201_CREATED)


class ClienteDetailView(ReplicacaoD1APIView):
    def get_object(self, pk):
        return get_object_or_404(
            ReplicacaoD1Cliente.objects.select_related("segmento", "categoria").annotate(
                workflows_count=Count("workflows", distinct=True)
            ),
            pk=pk,
        )

    def get(self, request, pk: int):
        return Response(ReplicacaoD1ClienteSerializer(self.get_object(pk)).data)

    def patch(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1ClienteSerializer(obj).data
        ser = ReplicacaoD1ClienteSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Cliente", obj.pk, "update", request.user, before, ser.data)
        return Response(ReplicacaoD1ClienteSerializer(obj).data)

    def delete(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1ClienteSerializer(obj).data
        with transaction.atomic():
            obj.ativo = False
            obj.updated_by = request.user
            obj.save(update_fields=["ativo", "updated_at", "updated_by"])
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Cliente", obj.pk, "inactivate", request.user, before, {"ativo": False})
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowColumnFilterOptionsView(ReplicacaoD1APIView):
    def get(self, request):
        return Response(build_workflow_column_filter_options())


class WorkflowListCreateView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1Workflow.objects.select_related(
            "cliente",
            "cliente__segmento",
            "cliente__categoria",
            "workflow_origem",
        ).annotate(
            amostra_pct_ordenacao=Case(
                When(amostra_pct_especial__isnull=False, then="amostra_pct_especial"),
                When(amostra_100=True, then=100),
                default=None,
                output_field=IntegerField(),
            )
        )
        status_values = [value.upper() for value in _query_param_values(request, "status")]
        if status_values:
            qs = qs.filter(status__in=status_values)
        pendentes = _parse_bool(request.query_params.get("pendentes"))
        if pendentes:
            qs = qs.filter(status=ReplicacaoD1Workflow.STATUS_PENDENTE)
        ativo = _parse_bool(request.query_params.get("ativo"))
        if ativo is not None:
            qs = qs.filter(ativo=ativo)
        qs = _apply_workflow_cliente_filter(qs, request)
        qs = _apply_text_in_filter(qs, request, "fila", "fila")
        qs = _apply_text_in_filter(qs, request, "segmento", "cliente__segmento__nome")
        qs = _apply_text_in_filter(qs, request, "categoria", "cliente__categoria__nome")
        qs = _apply_text_in_filter(qs, request, "nome", "nome_canonico")
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(nome_canonico__icontains=q)
                | Q(nome_d1__icontains=q)
                | Q(nome_selenium__icontains=q)
                | Q(fila__icontains=q)
            )
        qs = _apply_ordering(
            qs,
            request,
            {
                "nome_canonico": "nome_canonico",
                "cliente": "cliente__nome",
                "segmento": "cliente__segmento_nome",
                "categoria": "cliente__categoria_nome",
                "fila": "fila",
                "status": "status",
                "amostra_pct": "amostra_pct_ordenacao",
            },
            ("nome_canonico",),
        )
        return _paginate_queryset(qs, request, ReplicacaoD1WorkflowSerializer)

    def post(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1WorkflowSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(created_by=request.user, updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Workflow", obj.pk, "create", request.user, None, ser.data)
        return Response(ReplicacaoD1WorkflowSerializer(obj).data, status=status.HTTP_201_CREATED)


class WorkflowDetailView(ReplicacaoD1APIView):
    def get_object(self, pk):
        return get_object_or_404(
            ReplicacaoD1Workflow.objects.select_related(
                "cliente",
                "cliente__segmento",
                "cliente__categoria",
                "workflow_origem",
            ),
            pk=pk,
        )

    def get(self, request, pk: int):
        return Response(ReplicacaoD1WorkflowSerializer(self.get_object(pk)).data)

    def patch(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1WorkflowSerializer(obj).data
        ser = ReplicacaoD1WorkflowSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Workflow", obj.pk, "update", request.user, before, ser.data)
        return Response(ReplicacaoD1WorkflowSerializer(obj).data)

    def delete(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1WorkflowSerializer(obj).data
        with transaction.atomic():
            obj.status = ReplicacaoD1Workflow.STATUS_INATIVO
            obj.ativo = False
            obj.updated_by = request.user
            obj.save()
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Workflow", obj.pk, "inactivate", request.user, before, {"ativo": False})
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowDuplicateView(ReplicacaoD1ConfigWriteAPIView):
    def post(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        source = get_object_or_404(ReplicacaoD1Workflow, pk=pk)
        ser = ReplicacaoD1WorkflowDuplicateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            with transaction.atomic():
                obj = duplicate_workflow(
                    source,
                    fila=data["fila"],
                    nome_regra_brflow=data.get("nome_regra_brflow") or "",
                    nome_canonico=data.get("nome_canonico") or "",
                    nome_selenium=data.get("nome_selenium") or "",
                    status=data.get("status"),
                    user=request.user,
                )
                bump_config_version(user=request.user)
                payload = ReplicacaoD1WorkflowSerializer(obj).data
                registrar_historico(
                    "ReplicacaoD1Workflow",
                    obj.pk,
                    "duplicate",
                    request.user,
                    ReplicacaoD1WorkflowSerializer(source).data,
                    payload,
                )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkflowBulkStatusView(ReplicacaoD1ConfigWriteAPIView):
    def post(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1WorkflowBulkStatusSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target_status = ser.validated_data["status"]
        requested_ids = list(dict.fromkeys(ser.validated_data["workflow_ids"]))
        updated_ids: list[int] = []
        skipped: list[dict[str, str | int]] = []
        changed = False

        with transaction.atomic():
            workflows = {
                obj.pk: obj
                for obj in ReplicacaoD1Workflow.objects.select_for_update().filter(pk__in=requested_ids)
            }
            for workflow_id in requested_ids:
                obj = workflows.get(workflow_id)
                if obj is None:
                    skipped.append({"workflow_id": workflow_id, "detail": "Workflow não encontrado."})
                    continue
                if target_status == ReplicacaoD1Workflow.STATUS_ATIVO:
                    if obj.status == ReplicacaoD1Workflow.STATUS_PENDENTE:
                        skipped.append(
                            {
                                "workflow_id": workflow_id,
                                "detail": "Cadastro pendente; complete o cadastro antes de ativar.",
                            }
                        )
                        continue
                    if obj.status == ReplicacaoD1Workflow.STATUS_ATIVO and obj.ativo:
                        updated_ids.append(workflow_id)
                        continue
                elif (
                    obj.status == ReplicacaoD1Workflow.STATUS_INATIVO
                    and not obj.ativo
                ):
                    updated_ids.append(workflow_id)
                    continue

                before = ReplicacaoD1WorkflowSerializer(obj).data
                obj.status = target_status
                obj.updated_by = request.user
                obj.save()
                changed = True
                updated_ids.append(workflow_id)
                action = "activate" if target_status == ReplicacaoD1Workflow.STATUS_ATIVO else "inactivate"
                registrar_historico(
                    "ReplicacaoD1Workflow",
                    obj.pk,
                    action,
                    request.user,
                    before,
                    ReplicacaoD1WorkflowSerializer(obj).data,
                )

            if changed:
                bump_config_version(user=request.user)

        return Response(
            {
                "updated": len(updated_ids),
                "workflow_ids": updated_ids,
                "status": target_status,
                "skipped": skipped,
            }
        )


class EscalaListCreateView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1EscalaDia.objects.all()
        errors: dict[str, list[str]] = {}
        data_values = _query_param_values(request, "data")
        if data_values:
            parsed_dates: list[date] = []
            for raw in data_values:
                parsed, err = _parse_date_param(raw, "data")
                if err:
                    errors.update(err)
                elif parsed:
                    parsed_dates.append(parsed)
            if parsed_dates:
                qs = qs.filter(data__in=parsed_dates)
        data_inicio_raw = request.query_params.get("data_inicio")
        if data_inicio_raw:
            data_inicio, err = _parse_date_param(data_inicio_raw, "data_inicio")
            if err:
                errors.update(err)
            elif data_inicio:
                qs = qs.filter(data__gte=data_inicio)
        data_fim_raw = request.query_params.get("data_fim")
        if data_fim_raw:
            data_fim, err = _parse_date_param(data_fim_raw, "data_fim")
            if err:
                errors.update(err)
            elif data_fim:
                qs = qs.filter(data__lte=data_fim)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        qs = _apply_int_in_filter(qs, request, "auditores_brflow", "auditores_brflow")
        qs = _apply_int_in_filter(qs, request, "auditores_case", "auditores_case")
        qs = _apply_int_in_filter(qs, request, "auditores_bio", "auditores_bio")
        qs = _apply_int_in_filter(qs, request, "auditores_redoc", "auditores_redoc")
        qs = _apply_ordering(
            qs,
            request,
            {
                "data": "data",
                "auditores_brflow": "auditores_brflow",
                "auditores_case": "auditores_case",
                "auditores_bio": "auditores_bio",
                "auditores_redoc": "auditores_redoc",
            },
            ("-data",),
        )
        return _paginate_queryset(qs, request, ReplicacaoD1EscalaDiaSerializer)

    def post(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1EscalaDiaSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save()
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1EscalaDia", obj.pk, "create", request.user, None, ser.data)
        return Response(ReplicacaoD1EscalaDiaSerializer(obj).data, status=status.HTTP_201_CREATED)


class EscalaDetailView(ReplicacaoD1APIView):
    def get_object(self, pk):
        return get_object_or_404(ReplicacaoD1EscalaDia, pk=pk)

    def get(self, request, pk: int):
        return Response(ReplicacaoD1EscalaDiaSerializer(self.get_object(pk)).data)

    def patch(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1EscalaDiaSerializer(obj).data
        ser = ReplicacaoD1EscalaDiaSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save()
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1EscalaDia", obj.pk, "update", request.user, before, ser.data)
        return Response(ReplicacaoD1EscalaDiaSerializer(obj).data)

    def delete(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1EscalaDiaSerializer(obj).data
        with transaction.atomic():
            obj.delete()
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1EscalaDia", pk, "delete", request.user, before, None)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MetaMensalListCreateView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1MetaMensal.objects.select_related("cliente")
        qs = _apply_text_in_filter(qs, request, "competencia", "competencia")
        competencia = (request.query_params.get("competencia") or "").strip()
        if competencia and not _query_param_values(request, "competencia"):
            qs = qs.filter(competencia=competencia)
        qs = _apply_id_in_filter(qs, request, "cliente", "cliente_id")
        qs = _apply_text_in_filter(qs, request, "nome", "cliente__nome")
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(cliente__nome__icontains=q)
        qs = _apply_ordering(
            qs,
            request,
            {"cliente": "cliente__nome", "competencia": "competencia", "meta": "meta"},
            ("-competencia", "cliente__nome"),
        )
        return _paginate_queryset(qs, request, ReplicacaoD1MetaMensalSerializer)

    def post(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1MetaMensalSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(created_by=request.user, updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1MetaMensal", obj.pk, "create", request.user, None, ser.data)
        return Response(ReplicacaoD1MetaMensalSerializer(obj).data, status=status.HTTP_201_CREATED)


class MetaMensalDetailView(ReplicacaoD1APIView):
    def get_object(self, pk):
        return get_object_or_404(
            ReplicacaoD1MetaMensal.objects.select_related("cliente"),
            pk=pk,
        )

    def get(self, request, pk: int):
        return Response(ReplicacaoD1MetaMensalSerializer(self.get_object(pk)).data)

    def patch(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1MetaMensalSerializer(obj).data
        ser = ReplicacaoD1MetaMensalSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1MetaMensal", obj.pk, "update", request.user, before, ser.data)
        return Response(ReplicacaoD1MetaMensalSerializer(obj).data)

    def delete(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1MetaMensalSerializer(obj).data
        with transaction.atomic():
            obj.delete()
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1MetaMensal", pk, "delete", request.user, before, None)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LedgerListView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1LedgerConsumo.objects.select_related("cliente", "workflow")
        qs = _apply_text_in_filter(qs, request, "competencia", "competencia")
        competencia = (request.query_params.get("competencia") or "").strip()
        if competencia and not _query_param_values(request, "competencia"):
            qs = qs.filter(competencia=competencia)
        wf = (request.query_params.get("workflow_chave") or "").strip()
        if wf:
            qs = qs.filter(workflow_chave=wf)
        qs = _apply_id_in_filter(qs, request, "cliente", "cliente_id")
        qs = _apply_text_in_filter(qs, request, "origem", "origem")
        qs = _apply_text_in_filter(qs, request, "workflow", "workflow_nome")
        origem = (request.query_params.get("origem") or "").strip()
        if origem and not _query_param_values(request, "origem"):
            qs = qs.filter(origem=origem)
        ajuste = _parse_bool(request.query_params.get("ajuste"))
        if ajuste is not None:
            qs = qs.filter(ajuste__gt=0) if ajuste else qs.filter(ajuste=0)
        qs = _apply_text_in_filter(qs, request, "run_id", "run_id")
        qs = _apply_int_in_filter(qs, request, "protocolos", "protocolos")
        qs = _apply_ordering(
            qs,
            request,
            {
                "data_execucao": "data_execucao",
                "workflow": "workflow_nome",
                "cliente": "cliente_nome",
                "protocolos": "protocolos",
                "origem": "origem",
                "run_id": "run_id",
            },
            ("-data_execucao", "-created_at"),
        )
        data = _paginate_queryset(qs, request, ReplicacaoD1LedgerSerializer).data
        if competencia:
            data["consumo_agregado"] = carregar_consumo_meta_mensal(competencia)
        return Response(data)


class LedgerAjusteView(ReplicacaoD1ConfigWriteAPIView):
    def post(self, request):
        ser = ReplicacaoD1LedgerAjusteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obj = aplicar_ajuste_manual(user=request.user, **ser.validated_data)
        return Response(ReplicacaoD1LedgerSerializer(obj).data)


class LedgerPurgeRunsView(ReplicacaoD1ConfigWriteAPIView):
    def post(self, request):
        ser = ReplicacaoD1LedgerPurgeRunsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        deleted = purge_ledger_por_runs(ser.validated_data["run_ids"])
        return Response({"deleted": deleted, "run_ids": ser.validated_data["run_ids"]})


class HistoricoListView(ReplicacaoD1APIView):
    def get(self, request):
        qs = ReplicacaoD1ConfigHistorico.objects.select_related("usuario")
        qs = _apply_text_in_filter(qs, request, "entidade", "entidade")
        entidade = (request.query_params.get("entidade") or "").strip()
        if entidade and not _query_param_values(request, "entidade"):
            qs = qs.filter(entidade=entidade)
        qs = _apply_text_in_filter(qs, request, "operacao", "operacao")
        operacao = (request.query_params.get("operacao") or "").strip()
        if operacao and not _query_param_values(request, "operacao"):
            qs = qs.filter(operacao=operacao)
        qs = _apply_text_in_filter(qs, request, "usuario", "usuario__username")
        usuario_id = (request.query_params.get("usuario") or "").strip()
        if usuario_id and usuario_id.isdigit() and not _query_param_values(request, "usuario"):
            qs = qs.filter(usuario_id=usuario_id)
        errors: dict[str, list[str]] = {}
        created_dates = _query_param_values(request, "created_date")
        if created_dates:
            parsed_dates: list[date] = []
            for raw in created_dates:
                parsed, err = _parse_date_param(raw, "created_date")
                if err:
                    errors.update(err)
                elif parsed:
                    parsed_dates.append(parsed)
            if parsed_dates:
                qs = qs.filter(created_at__date__in=parsed_dates)
        created_after_raw = request.query_params.get("created_after")
        if created_after_raw:
            created_after, err = _parse_datetime_param(created_after_raw, "created_after")
            if err:
                errors.update(err)
            elif created_after:
                qs = qs.filter(created_at__gte=created_after)
        created_before_raw = request.query_params.get("created_before")
        if created_before_raw:
            created_before, err = _parse_datetime_param(
                created_before_raw, "created_before", end_of_day=True
            )
            if err:
                errors.update(err)
            elif created_before:
                qs = qs.filter(created_at__lte=created_before)
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)
        ordering_key = str(request.query_params.get("ordering") or "").lstrip("-")
        if ordering_key == "campos":
            qs = qs.annotate(
                campos_count=RawSQL(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT jsonb_object_keys(COALESCE(valores_anteriores, '{}'::jsonb)) AS key
                        UNION
                        SELECT jsonb_object_keys(COALESCE(valores_novos, '{}'::jsonb)) AS key
                    ) AS changed_keys
                    WHERE COALESCE(valores_anteriores -> changed_keys.key, 'null'::jsonb)
                          IS DISTINCT FROM
                          COALESCE(valores_novos -> changed_keys.key, 'null'::jsonb)
                    """,
                    [],
                )
            )
        qs = _apply_ordering(
            qs,
            request,
            {
                "created_at": "created_at",
                "usuario": "usuario__username",
                "entidade": "entidade",
                "operacao": "operacao",
                "campos": "campos_count",
            },
            ("-created_at",),
        )
        return _paginate_queryset(qs, request, ReplicacaoD1HistoricoSerializer)


class SegmentoListCreateView(ReplicacaoD1APIView):
    def get(self, request):
        qs = (
            ReplicacaoD1Segmento.objects.annotate(
                categorias_count=Count("categorias", distinct=True),
                clientes_count=Count("clientes", distinct=True),
            )
        )
        ativo = _parse_bool(request.query_params.get("ativo"))
        if ativo is not None:
            qs = qs.filter(ativo=ativo)
        qs = _apply_text_in_filter(qs, request, "nome", "nome")
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(nome__icontains=q)
        qs = _apply_int_in_filter(qs, request, "categorias_count", "categorias_count")
        qs = _apply_int_in_filter(qs, request, "clientes_count", "clientes_count")
        qs = _apply_ordering(
            qs,
            request,
            {
                "nome": "nome",
                "categorias_count": "categorias_count",
                "clientes_count": "clientes_count",
                "ativo": "ativo",
            },
            ("nome",),
        )
        return _paginate_queryset(qs, request, ReplicacaoD1SegmentoSerializer)

    def post(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1SegmentoSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(created_by=request.user, updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Segmento", obj.pk, "create", request.user, None, ser.data)
        return Response(ReplicacaoD1SegmentoSerializer(obj).data, status=status.HTTP_201_CREATED)


class SegmentoDetailView(ReplicacaoD1APIView):
    def get_object(self, pk):
        return get_object_or_404(
            ReplicacaoD1Segmento.objects.annotate(
                categorias_count=Count("categorias", distinct=True),
                clientes_count=Count("clientes", distinct=True),
            ),
            pk=pk,
        )

    def get(self, request, pk: int):
        return Response(ReplicacaoD1SegmentoSerializer(self.get_object(pk)).data)

    def patch(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1SegmentoSerializer(obj).data
        ser = ReplicacaoD1SegmentoSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Segmento", obj.pk, "update", request.user, before, ser.data)
        return Response(ReplicacaoD1SegmentoSerializer(obj).data)

    def delete(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1SegmentoSerializer(obj).data
        with transaction.atomic():
            obj.ativo = False
            obj.updated_by = request.user
            obj.save(update_fields=["ativo", "updated_at", "updated_by"])
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Segmento", obj.pk, "inactivate", request.user, before, {"ativo": False})
        return Response(status=status.HTTP_204_NO_CONTENT)


class CategoriaListCreateView(ReplicacaoD1APIView):
    def get(self, request):
        qs = (
            ReplicacaoD1Categoria.objects.select_related("segmento")
            .annotate(clientes_count=Count("clientes", distinct=True))
        )
        ativo = _parse_bool(request.query_params.get("ativo"))
        if ativo is not None:
            qs = qs.filter(ativo=ativo)
        qs = _apply_id_in_filter(qs, request, "segmento", "segmento_id")
        qs = _apply_text_in_filter(qs, request, "nome", "nome")
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(nome__icontains=q)
        qs = _apply_int_in_filter(qs, request, "clientes_count", "clientes_count")
        qs = _apply_ordering(
            qs,
            request,
            {
                "nome": "nome",
                "segmento": "segmento__nome",
                "clientes_count": "clientes_count",
                "ativo": "ativo",
            },
            ("nome",),
        )
        return _paginate_queryset(qs, request, ReplicacaoD1CategoriaSerializer)

    def post(self, request):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        ser = ReplicacaoD1CategoriaSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(created_by=request.user, updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Categoria", obj.pk, "create", request.user, None, ser.data)
        return Response(ReplicacaoD1CategoriaSerializer(obj).data, status=status.HTTP_201_CREATED)


class CategoriaDetailView(ReplicacaoD1APIView):
    def get_object(self, pk):
        return get_object_or_404(
            ReplicacaoD1Categoria.objects.select_related("segmento").annotate(
                clientes_count=Count("clientes", distinct=True),
            ),
            pk=pk,
        )

    def get(self, request, pk: int):
        return Response(ReplicacaoD1CategoriaSerializer(self.get_object(pk)).data)

    def patch(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1CategoriaSerializer(obj).data
        ser = ReplicacaoD1CategoriaSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = ser.save(updated_by=request.user)
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Categoria", obj.pk, "update", request.user, before, ser.data)
        return Response(ReplicacaoD1CategoriaSerializer(obj).data)

    def delete(self, request, pk: int):
        if not can_configure_automacoes(request.user):
            return Response({"detail": "Sem permissão para configurar automações."}, status=403)
        obj = self.get_object(pk)
        before = ReplicacaoD1CategoriaSerializer(obj).data
        with transaction.atomic():
            obj.ativo = False
            obj.updated_by = request.user
            obj.save(update_fields=["ativo", "updated_at", "updated_by"])
            bump_config_version(user=request.user)
            registrar_historico("ReplicacaoD1Categoria", obj.pk, "inactivate", request.user, before, {"ativo": False})
        return Response(status=status.HTTP_204_NO_CONTENT)


class ImportPreviewView(ReplicacaoD1ConfigWriteAPIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        ser = ImportPreviewRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        uploaded = ser.validated_data["file"]
        preview = preview_import(
            ser.validated_data["kind"],
            uploaded.read(),
            uploaded.name,
        )
        preview.pop("normalized_payload", None)
        return Response(preview)


class ImportApplyView(ReplicacaoD1ConfigWriteAPIView):
    def post(self, request):
        ser = ImportApplyRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            result = apply_import(
                ser.validated_data["kind"],
                ser.validated_data["preview_token"],
                request.user,
            )
        except ImportPreviewTokenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class FonteAtivarView(ReplicacaoD1ConfigWriteAPIView):
    def post(self, request):
        ser = FonteAtivarSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)
        try:
            geral = ativar_fonte_banco(request.user, force=ser.validated_data["force"])
        except ConfigIncompletaError as exc:
            return Response({"detail": str(exc), "errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ReplicacaoD1ConfigGeralSerializer(geral).data)


class FonteDesativarView(ReplicacaoD1ConfigWriteAPIView):
    def post(self, request):
        geral = desativar_fonte_banco(request.user)
        return Response(ReplicacaoD1ConfigGeralSerializer(geral).data)


class SnapshotPreviewView(ReplicacaoD1APIView):
    def get(self, request):
        ser = SnapshotPreviewQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        opts = RunOptions(
            run_id=ser.validated_data.get("run_id") or "",
            data_ref=ser.validated_data.get("data_ref") or "",
        )
        snapshot = preview_execution_snapshot(
            run_options=opts,
            competencia_meta=ser.validated_data.get("competencia_meta") or None,
        )
        cfg = load_persistent_config()
        return Response(
            {
                "snapshot": snapshot.to_dict(),
                "validation_errors": validate_persistent_config(cfg),
                "fonte_banco_ativa": cfg.fonte_banco_ativa,
            }
        )
