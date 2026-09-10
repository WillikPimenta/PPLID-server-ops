# -*- coding: utf-8 -*-
from __future__ import annotations

from django.conf import settings
from django.http import FileResponse, Http404
from django.test import Client
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.falhas_criticas.models import SyncAuditLog
from apps.falhas_criticas.services.data_bounds import get_portal_data_bounds
from apps.falhas_criticas.views.base_overview import _serialize_last_sync
from apps.psa.permissions import IsStaffOrCanSync, user_has_portal_ops_access
from apps.psa.services.documents import read_doc_text, resolve_repo_doc
from apps.psa.services.health import run_and_save_full_checks, run_quick_health, summarize_results
from apps.psa.services.qualidade_dates import check_qualidade_future_dates
from apps.psa.services.qualidade_case_key_dupes import check_qualidade_case_key_dupes
from apps.psa.services.registry import load_last_health_report, load_registry, summarize_modules


class PortalOpsAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"has_access": user_has_portal_ops_access(request.user)})


class PortalOpsOverviewView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        registry = load_registry()
        client = Client()
        client.force_login(request.user)

        modules = registry.get("modules") or []
        module_summary = summarize_modules(modules)
        last_log = SyncAuditLog.objects.order_by("-started_at").first()
        bounds = get_portal_data_bounds()
        health_quick = run_quick_health(client, request.user)
        last_report = load_last_health_report()

        risks = registry.get("risks") or []
        backlog = registry.get("backlog") or []
        open_risks = [r for r in risks if r.get("status") not in ("done", "accepted")]
        open_backlog = [b for b in backlog if b.get("status") not in ("done",)]

        severity_counts: dict[str, int] = {}
        for risk in open_risks:
            sev = str(risk.get("severity") or "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        health_summary = summarize_results(health_quick)

        payload = {
            "meta": {
                "timestamp": timezone.now().isoformat(),
                "debug": bool(getattr(settings, "DEBUG", False)),
                "title": (registry.get("meta") or {}).get("title", "Console Ops — Portal PPLID"),
                "updated_at": (registry.get("meta") or {}).get("updated_at"),
            },
            "summary": {
                "open_risks": len(open_risks),
                "open_backlog": len(open_backlog),
                "severity_counts": severity_counts,
                "health_quick_ok": health_summary["all_ok"],
                "last_sync_success": bool(last_log and last_log.success),
                "modules": module_summary["totals"],
                "by_section": module_summary["by_section"],
            },
            "modules": modules,
            "roadmap": registry.get("roadmap") or [],
            "risks": risks,
            "backlog": backlog,
            "links": registry.get("links") or [],
            "environment": {
                "last_sync": _serialize_last_sync(last_log),
                "data_bounds": {
                    "min_date": bounds.get("min_date"),
                    "max_date": bounds.get("max_date"),
                    "default_start_date": bounds.get("default_start_date"),
                    "default_end_date": bounds.get("default_end_date"),
                },
            },
            "health_quick": health_quick,
            "health_last_run": last_report,
        }
        return Response(payload)


class PortalOpsRunChecksView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def post(self, request):
        client = Client()
        client.force_login(request.user)
        include_spa = request.data.get("include_spa", False) if isinstance(request.data, dict) else False
        report = run_and_save_full_checks(client, request.user, include_spa=bool(include_spa))
        return Response(
            {
                "summary": report["summary"],
                "duration_ms": report["duration_ms"],
                "generated_at": report["generated_at"],
                "items": report["items"],
            }
        )


class PortalOpsDocDownloadView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        relative = request.query_params.get("path", "").strip()
        if not relative:
            return Response({"detail": "Parâmetro path é obrigatório."}, status=400)
        try:
            file_path = resolve_repo_doc(relative)
        except FileNotFoundError:
            raise Http404("Documento não encontrado.")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return FileResponse(
            file_path.open("rb"),
            as_attachment=True,
            filename=file_path.name,
            content_type="application/octet-stream",
        )


class PortalOpsDocViewView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        relative = request.query_params.get("path", "").strip()
        if not relative:
            return Response({"detail": "Parâmetro path é obrigatório."}, status=400)
        try:
            content, filename, content_type = read_doc_text(relative)
        except FileNotFoundError:
            raise Http404("Documento não encontrado.")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(
            {
                "path": relative,
                "filename": filename,
                "content": content,
                "content_type": content_type,
            }
        )


class PortalOpsQualidadeDatesView(APIView):
    """Diagnóstico sob demanda de datas futuras em Qualidade (somente leitura)."""

    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        raw_limit = request.query_params.get("sample_limit")
        try:
            sample_limit = int(raw_limit) if raw_limit not in (None, "") else 50
        except (TypeError, ValueError):
            sample_limit = 50
        return Response(check_qualidade_future_dates(sample_limit=sample_limit))


class PortalOpsQualidadeCaseKeyDupesView(APIView):
    """Diagnóstico sob demanda de duplicatas protocolo+matrícula em falhas EO."""

    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        raw_limit = request.query_params.get("sample_limit")
        try:
            sample_limit = int(raw_limit) if raw_limit not in (None, "") else 50
        except (TypeError, ValueError):
            sample_limit = 50
        return Response(check_qualidade_case_key_dupes(sample_limit=sample_limit))


# Aliases legados (testes/scripts antigos)
PsaAccessView = PortalOpsAccessView
PsaOverviewView = PortalOpsOverviewView
PsaRunChecksView = PortalOpsRunChecksView
