# -*- coding: utf-8 -*-
"""API derivacao_etapa (Megazord config)."""
from __future__ import annotations

from datetime import datetime

from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.db.models import Q
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_exceptions import PUBLIC_INTERNAL_ERROR

from apps.access.permission_classes import portal_perm
from apps.access.registry import PLANEJAMENTO_MEGAZORD_VIEW
from apps.dimensoes_processos.models import (
    DerivacaoEtapaComparativo,
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DerivacaoEtapaUploadBatch,
    DimCliente,
    DimEtapa,
    DimNomeAlias,
    DimWorkflow,
)
from apps.dimensoes_processos.services.derivacao_etapa.capacity_invalidation import (
    invalidate_capacity_snapshots_from_date,
)
from apps.dimensoes_processos.services.derivacao_etapa.normalize import (
    normalize_csv_etapa_key,
    normalize_csv_name_key,
)
from apps.dimensoes_processos.services.derivacao_etapa.lookup import MegazordLookup
from apps.dimensoes_processos.services.derivacao_etapa.scan_comparativo import (
    build_comparativo_resumo,
    scan_derivacao_etapa_comparativo,
)
from apps.dimensoes_processos.services.derivacao_etapa.auto_resolver import auto_resolve_comparativo
from apps.dimensoes_processos.services.derivacao_etapa.source import upload_batch_id_from_scan
from apps.dimensoes_processos.services.derivacao_etapa.sync import (
    PreflightMismatchError,
    UnsafePartialImportError,
    import_derivacao_etapa_csv,
)
from apps.dimensoes_processos.services.derivacao_etapa.upload_staging import (
    UploadValidationError,
    create_upload_batch,
    delete_upload_batch,
    get_active_upload_batch,
)
from apps.dimensoes_processos.services.derivacao_etapa.import_recovery import (
    has_active_derivacao_scan_or_import,
    has_running_derivacao_purge,
    recover_stale_derivacao_import_runs,
)
from apps.dimensoes_processos.services.derivacao_etapa.purge_worker import schedule_derivacao_etapa_purge

MegazordPerm = portal_perm(PLANEJAMENTO_MEGAZORD_VIEW)
PURGE_CONFIRM_TOKEN = "APAGAR"


def _serialize_derivacao_purge_run(run: DerivacaoEtapaImportRun) -> dict:
    metrics = dict(run.metrics or {})
    return {
        "run_id": run.pk,
        "status": run.status,
        "run_kind": run.run_kind,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "message": run.message,
        "metrics": metrics,
        "deleted": metrics.get("deleted") or {},
    }


def _derivacao_purge_busy_detail() -> str:
    return "Há uma exclusão da base em andamento. Aguarde concluir."


def _parse_date(raw) -> datetime.date | None:
    if not raw:
        return None
    return datetime.strptime(str(raw), "%Y-%m-%d").date()


def _validated_period(from_raw, to_raw):
    if not from_raw or not to_raw:
        raise ValueError("Informe a data inicial e a data final.")
    from_date = _parse_date(from_raw)
    to_date = _parse_date(to_raw)
    if from_date > to_date:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    return from_date, to_date


def _filter_diaria(qs, params):
    if params.get("start_date"):
        qs = qs.filter(data__gte=params["start_date"])
    if params.get("end_date"):
        qs = qs.filter(data__lte=params["end_date"])
    if params.get("id_cliente"):
        qs = qs.filter(cliente_id=params["id_cliente"])
    if params.get("id_workflow"):
        qs = qs.filter(workflow_id=params["id_workflow"])
    if params.get("id_etapa"):
        qs = qs.filter(etapa_id=params["id_etapa"])
    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(cliente__nome__icontains=search)
            | Q(workflow__nome__icontains=search)
            | Q(etapa__nome__icontains=search)
            | Q(cliente_nome_origem__icontains=search)
            | Q(workflow_nome_origem__icontains=search)
            | Q(etapa_nome_origem__icontains=search)
            | Q(source_file__icontains=search)
        )
    return qs


def _serialize_diaria_row(row: DerivacaoEtapaDiaria) -> dict:
    return {
        "id": row.pk,
        "data": row.data.isoformat(),
        "id_cliente": row.cliente_id,
        "id_workflow": row.workflow_id,
        "id_etapa": row.etapa_id,
        "cliente_nome": row.cliente.nome,
        "workflow_nome": row.workflow.nome,
        "etapa_nome": row.etapa.nome,
        "registros": row.registros,
        "percentual": str(row.percentual),
        "cliente_nome_origem": row.cliente_nome_origem,
        "workflow_nome_origem": row.workflow_nome_origem,
        "etapa_nome_origem": row.etapa_nome_origem,
        "source_file": row.source_file,
        "import_run_id": row.import_run_id,
        "manual_override": row.manual_override,
        "override_notas": row.override_notas,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_alias_row(alias: DimNomeAlias) -> dict:
    return {
        "id": alias.pk,
        "dimensao": alias.dimensao,
        "nome_origem": alias.nome_origem,
        "id_cliente": alias.cliente_id,
        "id_workflow": alias.workflow_id,
        "id_etapa": alias.etapa_id,
        "cliente_nome": alias.cliente.nome if alias.cliente_id else None,
        "workflow_nome": alias.workflow.nome if alias.workflow_id else None,
        "etapa_nome": alias.etapa.nome if alias.etapa_id else None,
        "classificacao": alias.classificacao,
        "notas": alias.notas,
        "ativo": alias.ativo,
        "created_at": alias.created_at.isoformat(),
        "updated_at": alias.updated_at.isoformat(),
    }


def _serialize_upload_batch(batch: DerivacaoEtapaUploadBatch) -> dict:
    return {
        "token": str(batch.token),
        "status": batch.status,
        "created_at": batch.created_at.isoformat(),
        "expires_at": batch.expires_at.isoformat(),
        "file_count": len(batch.file_manifest or []),
        "files": batch.file_manifest or [],
    }


class DerivacaoEtapaUploadView(APIView):
    permission_classes = [MegazordPerm]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploads = request.FILES.getlist("files") or request.FILES.getlist("files[]")
        if not uploads and request.FILES.get("file"):
            uploads = [request.FILES["file"]]
        try:
            batch = create_upload_batch(user=request.user, uploads=uploads)
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {"ok": True, "batch": _serialize_upload_batch(batch)},
            status=status.HTTP_201_CREATED,
        )


class DerivacaoEtapaUploadDetailView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request, token):
        try:
            batch = get_active_upload_batch(str(token), user=request.user)
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True, "batch": _serialize_upload_batch(batch)})

    def delete(self, request, token):
        try:
            batch = DerivacaoEtapaUploadBatch.objects.get(token=token, created_by=request.user)
        except (DerivacaoEtapaUploadBatch.DoesNotExist, ValueError):
            return Response({"detail": "Lote não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        delete_upload_batch(batch)
        return Response({"ok": True})


class DerivacaoEtapaScanView(APIView):
    permission_classes = [MegazordPerm]

    def post(self, request):
        if has_running_derivacao_purge():
            return Response({"detail": _derivacao_purge_busy_detail()}, status=status.HTTP_409_CONFLICT)

        from_date = request.data.get("from_date") or request.query_params.get("from_date")
        to_date = request.data.get("to_date") or request.query_params.get("to_date")
        file_name = request.data.get("file") or request.query_params.get("file") or ""
        upload_batch_id = request.data.get("upload_batch_id") or request.query_params.get("upload_batch_id")

        try:
            parsed_from, parsed_to = _validated_period(from_date, to_date)
            run, metrics = scan_derivacao_etapa_comparativo(
                file_name=str(file_name).strip(),
                from_date=parsed_from,
                to_date=parsed_to,
                upload_batch_id=str(upload_batch_id).strip() if upload_batch_id else None,
                user=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except UploadValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except FileNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        ok = run.status == DerivacaoEtapaImportRun.STATUS_OK
        return Response(
            {
                "ok": ok,
                "run_id": run.pk,
                "status": run.status,
                "message": run.message if ok else PUBLIC_INTERNAL_ERROR,
                "metrics": metrics.as_dict(),
                "resumo": build_comparativo_resumo(scan_run_id=run.pk),
            },
            status=status.HTTP_200_OK if ok else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class DerivacaoEtapaComparativoListView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        scan_run = request.query_params.get("scan_run")
        dimensao = request.query_params.get("dimensao")
        status_filter = request.query_params.get("status")
        strategy_filter = request.query_params.get("strategy")
        search = (request.query_params.get("search") or "").strip()

        qs = DerivacaoEtapaComparativo.objects.all().order_by("-registros_total", "-linhas_csv", "nome_origem")
        if scan_run:
            qs = qs.filter(scan_run_id=scan_run)
        else:
            last = (
                DerivacaoEtapaImportRun.objects.filter(
                    run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
                    status=DerivacaoEtapaImportRun.STATUS_OK,
                )
                .order_by("-started_at")
                .first()
            )
            if last:
                qs = qs.filter(scan_run=last)
            else:
                qs = qs.none()

        if dimensao:
            qs = qs.filter(dimensao=dimensao)
        if status_filter == "pending":
            qs = qs.filter(
                status__in=[
                    DerivacaoEtapaComparativo.STATUS_UNMATCHED,
                    DerivacaoEtapaComparativo.STATUS_AMBIGUOUS,
                ]
            )
        elif status_filter:
            qs = qs.filter(status=status_filter)
        if strategy_filter:
            qs = qs.filter(match_strategy=strategy_filter)
        if search:
            qs = qs.filter(Q(nome_origem__icontains=search) | Q(nome_megazord__icontains=search))

        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(200, max(1, int(request.query_params.get("page_size") or 50)))
        except ValueError:
            page, page_size = 1, 50

        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs[start : start + page_size])
        lookup = MegazordLookup.build()
        results = []
        for r in rows:
            sugestoes = r.sugestoes
            if r.status == DerivacaoEtapaComparativo.STATUS_UNMATCHED and not sugestoes:
                sugestoes = lookup.suggest_similar(r.dimensao, r.nome_origem)
            results.append(
                {
                    "id": r.pk,
                    "dimensao": r.dimensao,
                    "nome_origem": r.nome_origem,
                    "linhas_csv": r.linhas_csv,
                    "registros_total": r.registros_total,
                    "id_resolvido": r.id_resolvido,
                    "nome_megazord": r.nome_megazord,
                    "status": r.status,
                    "match_strategy": r.match_strategy,
                    "motivo": {
                        DerivacaoEtapaComparativo.STATUS_UNMATCHED: "Nenhum cadastro ou associaÃ§Ã£o compatÃ­vel foi encontrado.",
                        DerivacaoEtapaComparativo.STATUS_AMBIGUOUS: "Existe mais de um destino possÃ­vel e a escolha exige revisÃ£o.",
                        DerivacaoEtapaComparativo.STATUS_ALIAS: "Resolvido por uma associaÃ§Ã£o manual salva.",
                        DerivacaoEtapaComparativo.STATUS_OK: "Resolvido automaticamente pelo cadastro do Megazord.",
                    }.get(r.status, ""),
                    "sugestoes": sugestoes,
                }
            )
        return Response(
            {
                "ok": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": results,
            }
        )


class DerivacaoEtapaComparativoResumoView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        scan_run = request.query_params.get("scan_run")
        payload = build_comparativo_resumo(
            scan_run_id=int(scan_run) if scan_run else None,
        )
        return Response({"ok": True, **payload})


class DerivacaoEtapaDiariaListView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        qs = _filter_diaria(
            DerivacaoEtapaDiaria.objects.select_related("cliente", "workflow", "etapa"),
            request.query_params,
        )
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(200, max(1, int(request.query_params.get("page_size") or 50)))
        except ValueError:
            page, page_size = 1, 50
        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs.order_by("-data", "cliente_id", "workflow_id", "etapa_id")[start : start + page_size])
        return Response(
            {
                "ok": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": [_serialize_diaria_row(r) for r in rows],
            }
        )


class DerivacaoEtapaDiariaDetailView(APIView):
    permission_classes = [MegazordPerm]

    def patch(self, request, pk):
        row = (
            DerivacaoEtapaDiaria.objects.select_related("cliente", "workflow", "etapa")
            .filter(pk=pk)
            .first()
        )
        if row is None:
            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        update_fields: list[str] = []
        original_date = row.data

        if "registros" in data:
            try:
                registros = int(data["registros"])
            except (TypeError, ValueError):
                return Response({"detail": "registros inválido."}, status=status.HTTP_400_BAD_REQUEST)
            if registros < 0:
                return Response({"detail": "registros não pode ser negativo."}, status=status.HTTP_400_BAD_REQUEST)
            row.registros = registros
            update_fields.append("registros")

        if "percentual" in data:
            try:
                row.percentual = Decimal(str(data["percentual"]).replace(",", "."))
            except (InvalidOperation, ValueError):
                return Response({"detail": "percentual inválido."}, status=status.HTTP_400_BAD_REQUEST)
            update_fields.append("percentual")

        if "id_cliente" in data:
            cliente = DimCliente.objects.filter(pk=data["id_cliente"]).first()
            if cliente is None:
                return Response({"detail": "Cliente não encontrado."}, status=status.HTTP_404_NOT_FOUND)
            row.cliente = cliente
            update_fields.append("cliente")

        if "id_workflow" in data:
            workflow = DimWorkflow.objects.filter(pk=data["id_workflow"]).first()
            if workflow is None:
                return Response({"detail": "Workflow não encontrado."}, status=status.HTTP_404_NOT_FOUND)
            row.workflow = workflow
            update_fields.append("workflow")

        if "id_etapa" in data:
            etapa = DimEtapa.objects.filter(pk=data["id_etapa"]).first()
            if etapa is None:
                return Response({"detail": "Etapa não encontrada."}, status=status.HTTP_404_NOT_FOUND)
            row.etapa = etapa
            update_fields.append("etapa")

        if "override_notas" in data:
            row.override_notas = (data.get("override_notas") or "").strip()[:512]
            update_fields.append("override_notas")

        if not update_fields:
            return Response({"detail": "Nenhum campo para atualizar."}, status=status.HTTP_400_BAD_REQUEST)

        row.manual_override = True
        row.updated_by = request.user
        update_fields.extend(["manual_override", "updated_by", "updated_at"])

        try:
            row.save(update_fields=update_fields)
        except IntegrityError:
            return Response(
                {"detail": "Já existe registro para esta combinação de data, cliente, workflow e etapa."},
                status=status.HTTP_409_CONFLICT,
            )

        invalidate_capacity_snapshots_from_date(original_date)
        if row.data != original_date:
            invalidate_capacity_snapshots_from_date(row.data)

        row.refresh_from_db()
        return Response({"ok": True, "result": _serialize_diaria_row(row)})


class DerivacaoEtapaAutoResolveView(APIView):
    permission_classes = [MegazordPerm]

    def post(self, request):
        scan_run_id = request.data.get("scan_run_id") or request.query_params.get("scan_run_id")
        dry_run = str(request.data.get("dry_run", request.query_params.get("dry_run", "false"))).lower() in {
            "1",
            "true",
            "yes",
        }
        if not scan_run_id:
            return Response(
                {"detail": "Informe scan_run_id da análise."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            scan_run = DerivacaoEtapaImportRun.objects.get(pk=int(scan_run_id))
        except (TypeError, ValueError, DerivacaoEtapaImportRun.DoesNotExist):
            return Response({"detail": "Análise não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        if scan_run.run_kind != DerivacaoEtapaImportRun.KIND_SCAN:
            return Response({"detail": "Informe uma análise (scan) válida."}, status=status.HTTP_400_BAD_REQUEST)

        stats = auto_resolve_comparativo(
            scan_run_id=scan_run.pk,
            user=request.user,
            dry_run=dry_run,
        )
        return Response(
            {
                "ok": True,
                "dry_run": dry_run,
                "scan_run_id": scan_run.pk,
                "stats": stats.as_dict(),
                "message": (
                    "Simulação concluída. Nenhum cadastro foi salvo."
                    if dry_run
                    else "Pendências resolvidas. Analise o período novamente antes de importar."
                ),
            }
        )


class DerivacaoEtapaImportView(APIView):
    permission_classes = [MegazordPerm]

    def post(self, request):
        if has_running_derivacao_purge():
            return Response({"detail": _derivacao_purge_busy_detail()}, status=status.HTTP_409_CONFLICT)

        scan_run_id = request.data.get("scan_run_id") or request.query_params.get("scan_run_id")
        if not scan_run_id:
            return Response(
                {"detail": "Informe a análise revisada em scan_run_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            scan_run = DerivacaoEtapaImportRun.objects.get(pk=int(scan_run_id))
        except (TypeError, ValueError, DerivacaoEtapaImportRun.DoesNotExist):
            return Response({"detail": "Análise não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        if scan_run.run_kind != DerivacaoEtapaImportRun.KIND_SCAN or scan_run.status != DerivacaoEtapaImportRun.STATUS_OK:
            return Response(
                {"detail": "A análise informada não está concluída e válida."},
                status=status.HTTP_409_CONFLICT,
            )
        if (
            not scan_run.finished_at
            or not scan_run.period_from
            or not scan_run.period_to
            or not scan_run.source_fingerprint
        ):
            return Response(
                {"detail": "Esta análise não possui preflight seguro. Analise o período novamente."},
                status=status.HTTP_409_CONFLICT,
            )
        if DimNomeAlias.objects.filter(updated_at__gt=scan_run.finished_at).exists():
            return Response(
                {"detail": "As associações mudaram depois da análise. Analise o período novamente."},
                status=status.HTTP_409_CONFLICT,
            )
        if scan_run.comparativo_linhas.exclude(
            status__in=[DerivacaoEtapaComparativo.STATUS_OK, DerivacaoEtapaComparativo.STATUS_ALIAS]
        ).exists():
            return Response(
                {"detail": "A análise ainda possui pendências. Resolva-as e analise novamente antes de importar."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            run, metrics = import_derivacao_etapa_csv(
                file_name=scan_run.source_file_filter,
                from_date=scan_run.period_from,
                to_date=scan_run.period_to,
                upload_batch_id=upload_batch_id_from_scan(scan_run),
                scan_run=scan_run,
                user=request.user,
            )
        except (PreflightMismatchError, UnsafePartialImportError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except FileNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            return Response(
                {
                    "ok": False,
                    "detail": (
                        "A importação encontrou registros duplicados após consolidar etapas. "
                        "Analise o período novamente e tente importar outra vez."
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except Exception:
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        ok = run.status == DerivacaoEtapaImportRun.STATUS_OK
        return Response(
            {
                "ok": ok,
                "run_id": run.pk,
                "scan_run_id": scan_run.pk,
                "status": run.status,
                "message": run.message if ok else (run.message or PUBLIC_INTERNAL_ERROR),
                "files_processed": run.files_processed,
                "rows_inserted": run.rows_inserted,
                "rows_skipped_total": run.rows_skipped_total,
                "rows_rejected": run.rows_rejected,
                "metrics": metrics.as_dict(),
            },
            status=status.HTTP_200_OK if ok else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class DerivacaoEtapaImportStatusView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        recover_stale_derivacao_import_runs()
        last_scan = (
            DerivacaoEtapaImportRun.objects.filter(
                run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
                status=DerivacaoEtapaImportRun.STATUS_OK,
            )
            .order_by("-started_at")
            .first()
        )
        last_import = (
            DerivacaoEtapaImportRun.objects.filter(run_kind=DerivacaoEtapaImportRun.KIND_IMPORT)
            .order_by("-started_at")
            .first()
        )

        def _run_payload(run: DerivacaoEtapaImportRun | None):
            if run is None:
                return None
            ok = run.status == DerivacaoEtapaImportRun.STATUS_OK
            return {
                "id": run.pk,
                "run_kind": run.run_kind,
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "files_processed": run.files_processed,
                "rows_inserted": run.rows_inserted,
                "rows_skipped_total": run.rows_skipped_total,
                "rows_rejected": run.rows_rejected,
                "message": run.message if ok else PUBLIC_INTERNAL_ERROR,
                "metrics": run.metrics,
                "period_from": run.period_from.isoformat() if run.period_from else None,
                "period_to": run.period_to.isoformat() if run.period_to else None,
                "source_fingerprint": run.source_fingerprint,
                "reviewed_scan_id": run.reviewed_scan_id,
            }

        return Response(
            {
                "ok": True,
                "total_rows": DerivacaoEtapaDiaria.objects.count(),
                "last_scan": _run_payload(last_scan),
                "last_import": _run_payload(last_import),
                "resumo": build_comparativo_resumo(
                    scan_run_id=last_scan.pk if last_scan else None,
                ),
            }
        )


class DerivacaoEtapaPurgeView(APIView):
    permission_classes = [MegazordPerm]

    def post(self, request):
        confirm = str(request.data.get("confirm") or "").strip().upper()
        if confirm != PURGE_CONFIRM_TOKEN:
            return Response(
                {
                    "detail": f'Digite confirm="{PURGE_CONFIRM_TOKEN}" para apagar toda a base.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if has_running_derivacao_purge():
            return Response({"detail": _derivacao_purge_busy_detail()}, status=status.HTTP_409_CONFLICT)
        if has_active_derivacao_scan_or_import():
            return Response(
                {"detail": "Há scan/import em execução. Aguarde concluir antes de apagar."},
                status=status.HTTP_409_CONFLICT,
            )

        include_aliases = bool(request.data.get("include_aliases", True))
        include_capacity_snapshots = bool(request.data.get("include_capacity_snapshots", True))

        run = DerivacaoEtapaImportRun.objects.create(
            run_kind=DerivacaoEtapaImportRun.KIND_PURGE,
            status=DerivacaoEtapaImportRun.STATUS_RUNNING,
            triggered_by=request.user,
            metrics={
                "kind": "purge",
                "phase": "queued",
                "step_index": 0,
                "step_total": 0,
                "step_label": "Enfileirado — aguardando início…",
                "deleted": {},
                "stale_recovered": 0,
            },
        )

        try:
            schedule_derivacao_etapa_purge(
                run.pk,
                include_aliases=include_aliases,
                include_capacity_snapshots=include_capacity_snapshots,
            )
            run.refresh_from_db()
            payload = _serialize_derivacao_purge_run(run)
            http_status = status.HTTP_200_OK
            if run.status == DerivacaoEtapaImportRun.STATUS_RUNNING:
                http_status = status.HTTP_202_ACCEPTED
            return Response(
                {
                    "ok": run.status != DerivacaoEtapaImportRun.STATUS_ERROR,
                    "message": run.message or "Exclusão enfileirada.",
                    **payload,
                },
                status=http_status,
            )
        except Exception:
            run.status = DerivacaoEtapaImportRun.STATUS_ERROR
            run.message = PUBLIC_INTERNAL_ERROR
            run.save(update_fields=["status", "message"])
            return Response({"detail": PUBLIC_INTERNAL_ERROR}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DerivacaoEtapaPurgeRunDetailView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request, run_id: int):
        recover_stale_derivacao_import_runs()
        run = DerivacaoEtapaImportRun.objects.filter(
            pk=run_id,
            run_kind=DerivacaoEtapaImportRun.KIND_PURGE,
        ).first()
        if run is None:
            return Response({"detail": "Exclusão não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        if run.triggered_by_id and run.triggered_by_id != request.user.pk:
            return Response(
                {"detail": "Exclusão não pertence ao usuário atual."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({"ok": True, **_serialize_derivacao_purge_run(run)})


def _alias_nome_key(dimensao: str, nome_origem: str) -> str:
    if dimensao == DimNomeAlias.DIM_ETAPA:
        return normalize_csv_etapa_key(nome_origem)
    return normalize_csv_name_key(nome_origem)


class DerivacaoEtapaAliasView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        dimensao = request.query_params.get("dimensao")
        qs = DimNomeAlias.objects.filter(ativo=True).select_related("cliente", "workflow", "etapa")
        if dimensao:
            qs = qs.filter(dimensao=dimensao)
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(nome_origem__icontains=search) | Q(notas__icontains=search))
        qs = qs.order_by("-updated_at", "-created_at")
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(200, max(1, int(request.query_params.get("page_size") or 50)))
        except ValueError:
            page, page_size = 1, 50
        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs[start : start + page_size])
        return Response(
            {
                "ok": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": [_serialize_alias_row(a) for a in rows],
            }
        )

    def post(self, request):
        dimensao = (request.data.get("dimensao") or "").strip()
        nome_origem = (request.data.get("nome_origem") or "").strip()
        notas = (request.data.get("notas") or "").strip()

        if dimensao not in {DimNomeAlias.DIM_CLIENTE, DimNomeAlias.DIM_WORKFLOW, DimNomeAlias.DIM_ETAPA}:
            return Response({"detail": "dimensao inválida."}, status=status.HTTP_400_BAD_REQUEST)
        if not nome_origem:
            return Response({"detail": "nome_origem obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        key = _alias_nome_key(dimensao, nome_origem)
        cliente = workflow = etapa = None
        if dimensao == DimNomeAlias.DIM_CLIENTE:
            cid = request.data.get("id_cliente")
            if not cid:
                return Response({"detail": "id_cliente obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
            cliente = DimCliente.objects.filter(pk=cid).first()
            if cliente is None:
                return Response({"detail": "Cliente não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        elif dimensao == DimNomeAlias.DIM_WORKFLOW:
            wid = request.data.get("id_workflow")
            if not wid:
                return Response({"detail": "id_workflow obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
            workflow = DimWorkflow.objects.filter(pk=wid).first()
            if workflow is None:
                return Response({"detail": "Workflow não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        else:
            eid = request.data.get("id_etapa")
            if not eid:
                return Response({"detail": "id_etapa obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
            etapa = DimEtapa.objects.filter(pk=eid).first()
            if etapa is None:
                return Response({"detail": "Etapa não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        alias, created = DimNomeAlias.objects.update_or_create(
            dimensao=dimensao,
            nome_origem_key=key,
            defaults={
                "nome_origem": nome_origem,
                "cliente": cliente,
                "workflow": workflow,
                "etapa": etapa,
                "notas": notas,
                "ativo": True,
                "created_by": request.user,
            },
        )
        return Response(
            {
                "ok": True,
                "created": created,
                "id": alias.pk,
                "result": _serialize_alias_row(alias),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def patch(self, request):
        alias_id = request.data.get("id")
        if not alias_id:
            return Response({"detail": "id obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        alias = DimNomeAlias.objects.filter(pk=alias_id).select_related("cliente", "workflow", "etapa").first()
        if alias is None:
            return Response({"detail": "Associação não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        if "ativo" in request.data:
            alias.ativo = bool(request.data.get("ativo"))

        if "notas" in request.data:
            alias.notas = (request.data.get("notas") or "").strip()

        if "classificacao" in request.data:
            classificacao = (request.data.get("classificacao") or "").strip()
            valid = {choice[0] for choice in DimNomeAlias.CLASSIFICACAO_CHOICES}
            if classificacao not in valid:
                return Response({"detail": "classificacao inválida."}, status=status.HTTP_400_BAD_REQUEST)
            alias.classificacao = classificacao

        if "nome_origem" in request.data:
            nome_origem = (request.data.get("nome_origem") or "").strip()
            if not nome_origem:
                return Response({"detail": "nome_origem inválido."}, status=status.HTTP_400_BAD_REQUEST)
            alias.nome_origem = nome_origem
            alias.nome_origem_key = _alias_nome_key(alias.dimensao, nome_origem)

        dimensao = alias.dimensao
        if dimensao == DimNomeAlias.DIM_CLIENTE and "id_cliente" in request.data:
            cliente = DimCliente.objects.filter(pk=request.data.get("id_cliente")).first()
            if cliente is None:
                return Response({"detail": "Cliente não encontrado."}, status=status.HTTP_404_NOT_FOUND)
            alias.cliente = cliente
            alias.workflow = None
            alias.etapa = None
        elif dimensao == DimNomeAlias.DIM_WORKFLOW and "id_workflow" in request.data:
            workflow = DimWorkflow.objects.filter(pk=request.data.get("id_workflow")).first()
            if workflow is None:
                return Response({"detail": "Workflow não encontrado."}, status=status.HTTP_404_NOT_FOUND)
            alias.workflow = workflow
            alias.cliente = None
            alias.etapa = None
        elif dimensao == DimNomeAlias.DIM_ETAPA and "id_etapa" in request.data:
            etapa = DimEtapa.objects.filter(pk=request.data.get("id_etapa")).first()
            if etapa is None:
                return Response({"detail": "Etapa não encontrada."}, status=status.HTTP_404_NOT_FOUND)
            alias.etapa = etapa
            alias.cliente = None
            alias.workflow = None

        try:
            alias.save()
        except IntegrityError:
            return Response(
                {"detail": "Já existe associação para este nome de origem."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"ok": True, "result": _serialize_alias_row(alias)})

    def delete(self, request):
        alias_id = request.data.get("id") or request.query_params.get("id")
        if not alias_id:
            return Response({"detail": "id obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        alias = DimNomeAlias.objects.filter(pk=alias_id).first()
        if alias is None:
            return Response({"detail": "Associação não encontrada."}, status=status.HTTP_404_NOT_FOUND)
        alias.ativo = False
        alias.save(update_fields=["ativo", "updated_at"])
        return Response({"ok": True})
