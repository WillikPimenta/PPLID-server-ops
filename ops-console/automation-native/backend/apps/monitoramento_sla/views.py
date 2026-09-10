# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
from io import StringIO

from django.db.models import Count
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_exceptions import PUBLIC_INTERNAL_ERROR

from apps.access.permission_classes import portal_perm
from apps.access.registry import (
    INDICADORES_MONITORAMENTO_SLA_SYNC,
    INDICADORES_MONITORAMENTO_SLA_VIEW,
)
from apps.monitoramento_sla.models import (
    SlaUtilConsolidado,
    SlaUtilDetalhe,
    SlaUtilSyncRun,
)
from apps.monitoramento_sla.services.resumo import build_resumo_from_params
from apps.monitoramento_sla.services.resumo_serve import (
    busy_response_detail,
    resolve_date_range,
    resolve_gated_payload,
)
from apps.monitoramento_sla.services.import_consolidado_parquet import (
    PARQUET_IMPORT_KIND,
    build_month_plan,
    kpi_from_db,
    kpi_from_parquet,
    month_plan_to_dict,
)
from apps.monitoramento_sla.services.parquet_upload_staging import (
    UploadValidationError,
    get_upload_record,
    stage_parquet_upload,
)
from apps.monitoramento_sla.services.import_serve import (
    busy_response_detail as import_busy_response_detail,
    run_gated_import,
)
from apps.monitoramento_sla.services.status_counts import get_status_counts
from apps.monitoramento_sla.services.import_parquet_worker import (
    recover_stale_parquet_imports,
    schedule_parquet_apply,
)
from apps.monitoramento_sla.services.sync_incremental import sync_monitoramento_sla
from apps.monitoramento_sla.throttling import (
    MonitoramentoSlaImportThrottle,
    MonitoramentoSlaResumoThrottle,
)

ViewPerm = portal_perm(INDICADORES_MONITORAMENTO_SLA_VIEW)
SyncPerm = portal_perm(INDICADORES_MONITORAMENTO_SLA_SYNC)


def _serialize_parquet_import_run(run: SlaUtilSyncRun) -> dict:
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    return {
        "run_id": run.pk,
        "status": run.status,
        "phase": metrics.get("phase", ""),
        "message": run.message if run.status == SlaUtilSyncRun.STATUS_OK else (
            run.message if run.status == SlaUtilSyncRun.STATUS_ERROR else ""
        ),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "filename": metrics.get("filename", ""),
        "metrics": metrics,
        "rows_written": int(metrics.get("rows_written") or run.rows_consolidado or 0),
        "rows_deleted": int(metrics.get("rows_deleted") or 0),
    }


def _parquet_import_qs():
    return SlaUtilSyncRun.objects.filter(metrics__kind=PARQUET_IMPORT_KIND)

def _filter_detalhe(qs, params):
    if params.get("start_date"):
        qs = qs.filter(data_cadastro__gte=params["start_date"])
    if params.get("end_date"):
        qs = qs.filter(data_cadastro__lte=params["end_date"])
    if params.get("id_cliente"):
        qs = qs.filter(id_cliente=params["id_cliente"])
    if params.get("id_workflow"):
        qs = qs.filter(id_workflow=params["id_workflow"])
    if params.get("id_nh"):
        qs = qs.filter(id_nh=params["id_nh"])
    if params.get("sla_natural"):
        qs = qs.filter(sla_descricao_natural=params["sla_natural"])
    if params.get("sla_ajustado"):
        qs = qs.filter(sla_descricao_ajustado=params["sla_ajustado"])
    if params.get("faixa"):
        qs = qs.filter(faixa=params["faixa"])
    if params.get("em_aberto") in {"1", "true", "True"}:
        qs = qs.filter(em_aberto=True)
    if params.get("em_aberto") in {"0", "false", "False"}:
        qs = qs.filter(em_aberto=False)
    if params.get("search"):
        qs = qs.filter(protocolo__icontains=params["search"].strip())
    if params.get("com_problema") in {"1", "true", "True"}:
        qs = qs.exclude(problemas=[])
    return qs


class StatusView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        last = SlaUtilSyncRun.objects.order_by("-started_at").first()
        counts = get_status_counts()
        return Response(
            {
                "ok": True,
                "detalhe_count": counts["detalhe_count"],
                "consolidado_count": counts["consolidado_count"],
                "abertos": counts["abertos"],
                "last_sync": None
                if last is None
                else {
                    "id": last.pk,
                    "status": last.status,
                    "started_at": last.started_at.isoformat(),
                    "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                    "cutover_at": last.cutover_at.isoformat(),
                    "rows_detalhe": last.rows_detalhe,
                    "rows_consolidado": last.rows_consolidado,
                    "message": (
                        last.message
                        if last.status == SlaUtilSyncRun.STATUS_OK
                        else PUBLIC_INTERNAL_ERROR
                    ),
                    "metrics": last.metrics,
                },
            }
        )


class SyncView(APIView):
    permission_classes = [SyncPerm]

    def post(self, request):
        days = int(request.data.get("days") or request.query_params.get("days") or 90)
        run = sync_monitoramento_sla(days=days, user=request.user)
        return Response(
            {
                "ok": run.status == SlaUtilSyncRun.STATUS_OK,
                "run_id": run.pk,
                "status": run.status,
                "message": (
                    run.message
                    if run.status == SlaUtilSyncRun.STATUS_OK
                    else PUBLIC_INTERNAL_ERROR
                ),
                "rows_detalhe": run.rows_detalhe,
                "rows_consolidado": run.rows_consolidado,
                "cutover_at": run.cutover_at.isoformat(),
                "metrics": run.metrics,
            },
            status=status.HTTP_200_OK if run.status == SlaUtilSyncRun.STATUS_OK else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class KpisView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        """KPIs: preferem consolidado (volume) quando houver linhas; senão detalhe."""
        from django.db.models import Sum

        cons = SlaUtilConsolidado.objects.all()
        if request.query_params.get("start_date"):
            cons = cons.filter(data_cadastro__gte=request.query_params["start_date"])
        if request.query_params.get("end_date"):
            cons = cons.filter(data_cadastro__lte=request.query_params["end_date"])
        if request.query_params.get("id_cliente"):
            cons = cons.filter(id_cliente=request.query_params["id_cliente"])
        if request.query_params.get("id_workflow"):
            cons = cons.filter(id_workflow=request.query_params["id_workflow"])

        if cons.exists():
            volume = cons.aggregate(v=Sum("quantidade"))["v"] or 0
            nat_d = cons.filter(sla_descricao_natural="Dentro").aggregate(v=Sum("quantidade"))["v"] or 0
            nat_f = cons.filter(sla_descricao_natural="Fora").aggregate(v=Sum("quantidade"))["v"] or 0
            aj_d = cons.filter(sla_descricao_ajustado="Dentro").aggregate(v=Sum("quantidade"))["v"] or 0
            aj_f = cons.filter(sla_descricao_ajustado="Fora").aggregate(v=Sum("quantidade"))["v"] or 0
            return Response(
                {
                    "ok": True,
                    "source": "consolidado",
                    "total": int(volume),
                    "linhas": cons.count(),
                    "abertos": 0,
                    "com_problema": 0,
                    "natural": {
                        "dentro": int(nat_d),
                        "fora": int(nat_f),
                        "pct_dentro": round(100.0 * nat_d / volume, 2) if volume else None,
                    },
                    "ajustado": {
                        "dentro": int(aj_d),
                        "fora": int(aj_f),
                        "pct_dentro": round(100.0 * aj_d / volume, 2) if volume else None,
                    },
                    "as_of": timezone.now().isoformat(),
                }
            )

        qs = _filter_detalhe(SlaUtilDetalhe.objects.all(), request.query_params)
        total = qs.count()
        nat = qs.values("sla_descricao_natural").annotate(c=Count("id"))
        aj = qs.values("sla_descricao_ajustado").annotate(c=Count("id"))
        abertos = qs.filter(em_aberto=True).count()
        com_prob = qs.exclude(problemas=[]).count()
        nat_map = {r["sla_descricao_natural"]: r["c"] for r in nat}
        aj_map = {r["sla_descricao_ajustado"]: r["c"] for r in aj}
        return Response(
            {
                "ok": True,
                "source": "detalhe",
                "total": total,
                "abertos": abertos,
                "com_problema": com_prob,
                "natural": {
                    "dentro": nat_map.get("Dentro", 0),
                    "fora": nat_map.get("Fora", 0),
                    "pct_dentro": round(100.0 * nat_map.get("Dentro", 0) / total, 2) if total else None,
                },
                "ajustado": {
                    "dentro": aj_map.get("Dentro", 0),
                    "fora": aj_map.get("Fora", 0),
                    "pct_dentro": round(100.0 * aj_map.get("Dentro", 0) / total, 2) if total else None,
                },
                "as_of": timezone.now().isoformat(),
            }
        )


class DetalheView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        qs = _filter_detalhe(SlaUtilDetalhe.objects.all(), request.query_params)
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(200, max(1, int(request.query_params.get("page_size") or 50)))
        except ValueError:
            page, page_size = 1, 50
        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs.order_by("-data_cadastro", "protocolo")[start : start + page_size])
        return Response(
            {
                "ok": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": [
                    {
                        "id": r.pk,
                        "protocolo": r.protocolo,
                        "id_cliente": r.id_cliente,
                        "id_workflow": r.id_workflow,
                        "id_nh": r.id_nh,
                        "cliente_nome": r.cliente_nome,
                        "workflow_nome": r.workflow_nome,
                        "nh_nome": r.nh_nome,
                        "data_cadastro": r.data_cadastro.isoformat(),
                        "hora_cadastro": r.hora_cadastro.isoformat() if r.hora_cadastro else None,
                        "data_conclusao": r.data_conclusao.isoformat() if r.data_conclusao else None,
                        "hora_conclusao": r.hora_conclusao.isoformat() if r.hora_conclusao else None,
                        "em_aberto": r.em_aberto,
                        "resultado": r.resultado,
                        "sla_segundos": r.sla_segundos,
                        "sla_descricao_natural": r.sla_descricao_natural,
                        "sla_descricao_ajustado": r.sla_descricao_ajustado,
                        "faixa": r.faixa,
                        "data_vencimento_d2u": r.data_vencimento_d2u.isoformat()
                        if r.data_vencimento_d2u
                        else None,
                        "problemas": r.problemas or [],
                    }
                    for r in rows
                ],
            }
        )


class ConsolidadoView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        qs = SlaUtilConsolidado.objects.all()
        if request.query_params.get("start_date"):
            qs = qs.filter(data_cadastro__gte=request.query_params["start_date"])
        if request.query_params.get("end_date"):
            qs = qs.filter(data_cadastro__lte=request.query_params["end_date"])
        if request.query_params.get("id_cliente"):
            qs = qs.filter(id_cliente=request.query_params["id_cliente"])
        if request.query_params.get("id_workflow"):
            qs = qs.filter(id_workflow=request.query_params["id_workflow"])
        if request.query_params.get("id_nh"):
            qs = qs.filter(id_nh=request.query_params["id_nh"])
        if request.query_params.get("sla_natural"):
            qs = qs.filter(sla_descricao_natural=request.query_params["sla_natural"])
        if request.query_params.get("sla_ajustado"):
            qs = qs.filter(sla_descricao_ajustado=request.query_params["sla_ajustado"])
        if request.query_params.get("faixa"):
            qs = qs.filter(faixa=request.query_params["faixa"])
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(200, max(1, int(request.query_params.get("page_size") or 50)))
        except ValueError:
            page, page_size = 1, 50
        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs.order_by("-data_cadastro")[start : start + page_size])

        from apps.dimensoes_processos.models import DimCliente, DimNivelHierarquico, DimWorkflow

        c_ids = {r.id_cliente for r in rows if r.id_cliente}
        w_ids = {r.id_workflow for r in rows if r.id_workflow}
        n_ids = {r.id_nh for r in rows if r.id_nh}
        nomes_c = dict(DimCliente.objects.filter(pk__in=c_ids).values_list("pk", "nome"))
        nomes_w = dict(DimWorkflow.objects.filter(pk__in=w_ids).values_list("pk", "nome"))
        nomes_n = dict(DimNivelHierarquico.objects.filter(pk__in=n_ids).values_list("pk", "nome"))

        return Response(
            {
                "ok": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": [
                    {
                        "id": r.pk,
                        "data_cadastro": r.data_cadastro.isoformat(),
                        "id_cliente": r.id_cliente,
                        "id_workflow": r.id_workflow,
                        "id_nh": r.id_nh,
                        "cliente_nome": r.cliente_nome or nomes_c.get(r.id_cliente or -1, ""),
                        "workflow_nome": r.workflow_nome or nomes_w.get(r.id_workflow or -1, ""),
                        "nh_nome": r.nh_nome or nomes_n.get(r.id_nh or -1, ""),
                        "sla_descricao_natural": r.sla_descricao_natural,
                        "sla_descricao_ajustado": r.sla_descricao_ajustado,
                        "faixa": r.faixa,
                        "resultado": r.resultado,
                        "tipo_conclusao": r.tipo_conclusao,
                        "quantidade": r.quantidade,
                    }
                    for r in rows
                ],
            }
        )


class ExportView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        qs = _filter_detalhe(SlaUtilDetalhe.objects.all(), request.query_params).order_by(
            "-data_cadastro"
        )[:20000]
        buf = StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(
            [
                "protocolo",
                "cliente",
                "workflow",
                "nh",
                "data_cadastro",
                "data_conclusao",
                "em_aberto",
                "sla_segundos",
                "sla_natural",
                "sla_ajustado",
                "faixa",
                "problemas",
            ]
        )
        for r in qs.iterator(chunk_size=1000):
            w.writerow(
                [
                    r.protocolo,
                    r.cliente_nome,
                    r.workflow_nome,
                    r.nh_nome,
                    r.data_cadastro,
                    r.data_conclusao or "",
                    "1" if r.em_aberto else "0",
                    r.sla_segundos if r.sla_segundos is not None else "",
                    r.sla_descricao_natural,
                    r.sla_descricao_ajustado,
                    r.faixa,
                    "|".join(r.problemas or []),
                ]
            )
        resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="monitoramento_sla_detalhe.csv"'
        return resp


class ResumoView(APIView):
    """Painel Resumo (KPIs + calendário + série mensal + heatmap)."""

    permission_classes = [ViewPerm]
    throttle_classes = [MonitoramentoSlaResumoThrottle]

    def get(self, request):
        params = {k: request.query_params.get(k) for k in request.query_params.keys()}
        start, end, err = resolve_date_range(params)
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        def builder():
            return build_resumo_from_params(params)

        payload, busy = resolve_gated_payload(
            route="resumo",
            params={
                "start_date": start.isoformat() if start else "",
                "end_date": end.isoformat() if end else "",
                "id_cliente": params.get("id_cliente") or "",
                "id_workflow": params.get("id_workflow") or "",
                "id_nh": params.get("id_nh") or "",
                "month": params.get("month") or "",
                "year": params.get("year") or "",
                "calendar_year": params.get("calendar_year") or "",
                "tipo_conclusao": params.get("tipo_conclusao") or "",
                "sla_natural": params.get("sla_natural") or "",
                "hour": params.get("hour") or "",
                "dow": params.get("dow") or "",
                "granularity": params.get("granularity") or "auto",
                "meta_sla": params.get("meta_sla") or "",
            },
            builder=builder,
        )
        if busy:
            body, http_status, headers = busy_response_detail(busy)
            return Response(body, status=http_status, headers=headers)
        return Response(payload)


class ImportParquetPreviewView(APIView):
    permission_classes = [SyncPerm]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [MonitoramentoSlaImportThrottle]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "Envie um arquivo .parquet."}, status=status.HTTP_400_BAD_REQUEST)

        user_id = getattr(request.user, "pk", "anon")

        def _preview():
            record = stage_parquet_upload(user=request.user, upload=upload)
            path = record.path
            plan = build_month_plan(path)
            parquet_kpis = kpi_from_parquet(path)
            competencias = [row.competencia for row in plan]
            replace_months = [row.competencia for row in plan if row.action == "replace"]
            return {
                "record": record,
                "plan": plan,
                "parquet_kpis": parquet_kpis,
                "db_kpis": kpi_from_db(competencias=competencias),
                "replace_months": replace_months,
            }

        try:
            payload, busy = run_gated_import(f"preview:{user_id}", _preview)
            if busy:
                body, http_status, headers = import_busy_response_detail(busy)
                return Response(body, status=http_status, headers=headers)
            record = payload["record"]
            return Response(
                {
                    "ok": True,
                    "upload_id": record.upload_id,
                    "filename": record.original_name,
                    "size_bytes": record.size_bytes,
                    "sha256": record.sha256,
                    "parquet_kpis": payload["parquet_kpis"],
                    "db_kpis": payload["db_kpis"],
                    "month_plan": month_plan_to_dict(payload["plan"]),
                    "months_to_apply": payload["replace_months"],
                    "months_skipped": [row.competencia for row in payload["plan"] if row.action == "skip"],
                    "db_kpis_scope": "competencias_arquivo",
                },
                status=status.HTTP_201_CREATED,
            )
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, FileNotFoundError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ImportParquetApplyView(APIView):
    permission_classes = [SyncPerm]
    throttle_classes = [MonitoramentoSlaImportThrottle]

    def post(self, request):
        upload_id = (request.data.get("upload_id") or "").strip()
        if not upload_id:
            return Response({"detail": "upload_id é obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        months = request.data.get("months")
        user_id = getattr(request.user, "pk", "anon")

        def _enqueue():
            record = get_upload_record(upload_id, user=request.user)
            plan = build_month_plan(record.path)
            selected_months = months
            if selected_months is None:
                selected_months = [row.competencia for row in plan if row.action == "replace"]
            elif not isinstance(selected_months, list):
                raise ValueError("months deve ser uma lista.")
            selected_months = [str(item).strip() for item in selected_months if str(item).strip()]
            if not selected_months:
                raise ValueError("Nenhuma competência com alteração para aplicar.")

            sorted_months = sorted(selected_months)
            skipped = [row.competencia for row in plan if row.action == "skip"]
            run = SlaUtilSyncRun.objects.create(
                status=SlaUtilSyncRun.STATUS_RUNNING,
                cutover_at=timezone.now(),
                triggered_by=request.user if getattr(request.user, "pk", None) else None,
                message=f"Import parquet enfileirado: {record.original_name}",
                metrics={
                    "kind": PARQUET_IMPORT_KIND,
                    "phase": "queued",
                    "upload_id": upload_id,
                    "filename": record.original_name,
                    "months_total": len(sorted_months),
                    "months_done": 0,
                    "current_competencia": sorted_months[0] if sorted_months else "",
                    "rows_written": 0,
                    "rows_deleted": 0,
                    "months_applied": [],
                    "months_skipped": skipped,
                    "months_pending": sorted_months,
                },
            )
            schedule_parquet_apply(
                run.pk,
                upload_id,
                sorted_months,
                request.user.pk if getattr(request.user, "pk", None) else None,
                record.original_name,
            )
            return run

        try:
            run, busy = run_gated_import(f"apply:{user_id}:{upload_id}", _enqueue)
            if busy:
                body, http_status, headers = import_busy_response_detail(busy)
                return Response(body, status=http_status, headers=headers)
            payload = _serialize_parquet_import_run(run)
            return Response(
                {
                    "ok": True,
                    **payload,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ImportParquetRunDetailView(APIView):
    permission_classes = [SyncPerm]

    def get(self, request, run_id: int):
        recover_stale_parquet_imports()
        run = _parquet_import_qs().filter(pk=run_id).first()
        if run is None:
            return Response({"detail": "Import não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        if run.triggered_by_id and run.triggered_by_id != request.user.pk:
            return Response({"detail": "Import não pertence ao usuário atual."}, status=status.HTTP_403_FORBIDDEN)
        return Response({"ok": True, **_serialize_parquet_import_run(run)})


class ImportParquetRunListView(APIView):
    permission_classes = [SyncPerm]

    def get(self, request):
        recover_stale_parquet_imports()
        active_only = request.query_params.get("active") in {"1", "true", "True"}
        qs = _parquet_import_qs().filter(triggered_by=request.user).order_by("-started_at")
        if active_only:
            running = list(qs.filter(status=SlaUtilSyncRun.STATUS_RUNNING)[:20])
            recent = list(
                qs.exclude(status=SlaUtilSyncRun.STATUS_RUNNING)[:10]
            )
            seen = {run.pk for run in running}
            merged = running + [run for run in recent if run.pk not in seen]
            runs = merged
        else:
            runs = list(qs[:20])
        return Response(
            {
                "ok": True,
                "results": [_serialize_parquet_import_run(run) for run in runs],
            }
        )
