# -*- coding: utf-8 -*-
from __future__ import annotations

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cyber_psa.models import CyberRiskOverride
from apps.cyber_psa.services.env_profile import get_env_profile
from apps.cyber_psa.services.export import build_governance_markdown
from apps.cyber_psa.services.glossary import enrich_governance, enrich_scan_items, glossary_for_api
from apps.cyber_psa.services.export_package import build_export_package
from apps.cyber_psa.services.registry import load_findings, load_registry
from apps.cyber_psa.services.risk_overrides import apply_overrides_to_risks, upsert_override
from apps.cyber_psa.services.risk_store import list_scan_history, load_scan_history_item, run_and_save_scan
from apps.cyber_psa.services.scanner import apply_governance_auto_checks
from apps.psa.permissions import IsStaffOrCanSync, user_has_cyber_psa_access
from apps.psa.services.documents import read_doc_text, resolve_repo_doc
from apps.psa.services.registry import load_last_health_report


class CyberPsaAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"has_access": user_has_cyber_psa_access(request.user)})


class CyberPsaOverviewView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        registry = load_registry()
        findings = load_findings()
        ops_health = load_last_health_report()

        if findings and findings.get("governance"):
            governance = enrich_governance(findings["governance"])
            scan_items = enrich_scan_items(findings.get("scan_items") or [])
        else:
            governance = enrich_governance(apply_governance_auto_checks(registry.get("governance") or [], []))
            scan_items = []

        base_risks = (findings or {}).get("risks") if findings else (registry.get("manual_risks") or [])
        risks = apply_overrides_to_risks(base_risks or [])

        open_risks = [r for r in risks if r.get("status") == "open"]
        severity_counts: dict[str, int] = {}
        for risk in open_risks:
            sev = str(risk.get("severity") or "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        scan_summary = (findings or {}).get("summary") or {
            "passed": 0,
            "failed": 0,
            "total": 0,
            "all_ok": True,
            "open_risks": len(open_risks),
        }

        ops_summary = None
        if ops_health:
            s = ops_health.get("summary") or {}
            ops_summary = {
                "generated_at": ops_health.get("generated_at"),
                "passed": s.get("passed", 0),
                "total": s.get("total", 0),
                "suites": s.get("suites"),
            }

        return Response(
            {
                "meta": {
                    "timestamp": timezone.now().isoformat(),
                    "debug": bool(getattr(settings, "DEBUG", False)),
                    "env_profile": get_env_profile(),
                    "title": (registry.get("meta") or {}).get("title", "PSA Cyber — PPLID"),
                    "updated_at": (registry.get("meta") or {}).get("updated_at"),
                    "disclaimer": (registry.get("meta") or {}).get("disclaimer", ""),
                },
                "summary": {
                    "open_risks": len(open_risks),
                    "severity_counts": severity_counts,
                    "scan_ok": bool(scan_summary.get("all_ok")),
                    "scan_passed": scan_summary.get("passed", 0),
                    "scan_total": scan_summary.get("total", 0),
                    "governance_total": len(governance),
                    "governance_scan_ok": sum(1 for g in governance if g.get("scan_ok") is True),
                },
                "presentation": registry.get("presentation") or [],
                "governance": governance,
                "risks": risks,
                "documents": registry.get("documents") or [],
                "scan_quick": scan_items,
                "last_scan": findings,
                "ops_health": ops_summary,
                "scan_history": list_scan_history()[:10],
                "glossary": glossary_for_api(),
            }
        )


class CyberPsaRunScanView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def post(self, request):
        from django.test import Client

        client = Client()
        client.force_login(request.user)
        report = run_and_save_scan(client, request.user)
        return Response(
            {
                "summary": report["summary"],
                "duration_ms": report["duration_ms"],
                "generated_at": report["generated_at"],
                "generated_by": report.get("generated_by"),
                "risks": report["risks"],
                "scan_items": enrich_scan_items(report["scan_items"]),
                "governance": enrich_governance(report["governance"]),
                "risk_diff_tags": report.get("risk_diff_tags", {}),
            }
        )


class CyberPsaRiskPatchView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def patch(self, request, risk_id: str):
        if not risk_id or ".." in risk_id:
            return Response({"detail": "ID inválido."}, status=400)
        data = request.data if isinstance(request.data, dict) else {}
        status = str(data.get("status", "")).strip()
        note = str(data.get("note", "")).strip()
        allowed = {
            CyberRiskOverride.STATUS_OPEN,
            CyberRiskOverride.STATUS_ACCEPTED,
            CyberRiskOverride.STATUS_RESOLVED,
        }
        if status not in allowed:
            return Response({"detail": "Status inválido."}, status=400)
        if status == CyberRiskOverride.STATUS_ACCEPTED and not note:
            return Response({"detail": "Justificativa obrigatória para aceitar risco."}, status=400)

        obj = upsert_override(risk_id, status=status, note=note, user=request.user)
        return Response(
            {
                "risk_id": obj.risk_id,
                "status": obj.status,
                "note": obj.note,
                "updated_by": getattr(request.user, "username", None),
                "updated_at": obj.updated_at.isoformat(),
            }
        )


class CyberPsaHistoryListView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        return Response({"items": list_scan_history()})


class CyberPsaHistoryDetailView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request, scan_id: str):
        item = load_scan_history_item(scan_id)
        if not item:
            raise Http404("Scan não encontrado.")
        return Response(item)


class CyberPsaDocDownloadView(APIView):
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


class CyberPsaDocViewView(APIView):
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


class CyberPsaExportView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        username = getattr(request.user, "username", "unknown")
        content = build_governance_markdown(username=username)
        filename = f"psa-cyber-pplid-{timezone.now().strftime('%Y%m%d-%H%M')}.md"
        response = HttpResponse(content, content_type="text/markdown; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class CyberPsaExportPackageView(APIView):
    permission_classes = [IsAuthenticated, IsStaffOrCanSync]

    def get(self, request):
        username = getattr(request.user, "username", "unknown")
        payload, filename = build_export_package(username=username)
        response = HttpResponse(payload, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
