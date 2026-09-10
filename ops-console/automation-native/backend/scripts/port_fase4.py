"""Porta arquivos do app reports para apps.falhas_criticas (fase 4)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

SRC = Path(r"C:\Users\c93233a\Downloads\RELATORIO_PRONTO_EXECUTAR_PADRAO\web_platform\backend\reports")
DST = Path(r"C:\Users\c93233a\Downloads\PPLID-integracao\backend\apps\falhas_criticas")

SKIP_SERVICES = {
    "excel_path.py",
    "excel_sync.py",
    "sync_runner.py",
    "sync_helpers.py",
    "__init__.py",
}

COPY_FILES = [
    "permissions.py",
    "scoping.py",
    "utils_metrics.py",
]

COPY_SERVICE_FILES = [
    "analytics.py",
    "alerts.py",
    "compare_analytics.py",
    "data_bounds.py",
    "dataframes.py",
    "executive_bridge.py",
    "export_service.py",
    "formatters.py",
    "narrative.py",
    "support_analytics.py",
    "team_hierarchy.py",
    "training_analytics.py",
]

COPY_VIEW_FILES = [
    "__init__.py",
    "base.py",
    "analytics.py",
    "exports.py",
    "sync.py",
]


def transform(text: str) -> str:
    text = text.replace("from reports.", "from apps.falhas_criticas.")
    text = text.replace("import reports.", "import apps.falhas_criticas.")
    text = re.sub(
        r"from apps\.falhas_criticas\.models import Agent\b",
        "from apps.falhas_criticas.models import FalhasAgent",
        text,
    )
    text = re.sub(
        r"from apps\.falhas_criticas\.models import ([^;\n]*)\bAgent\b",
        lambda m: m.group(0).replace("Agent", "FalhasAgent"),
        text,
    )
    text = re.sub(r"\bAgent\.objects\b", "FalhasAgent.objects", text)
    text = re.sub(r"\bAgent\(", "FalhasAgent(", text)
    text = text.replace("'portal/index.html'", "'falhas_criticas/portal/index.html'")
    text = text.replace("login_url = '/login/'", "login_url = None  # PPLID Vue login")
    return text


def write_transformed(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(transform(src.read_text(encoding="utf-8")), encoding="utf-8")


def main() -> None:
    for name in COPY_FILES:
        write_transformed(SRC / name, DST / name)

    # constants - merge MODULOS import
    const_src = (SRC / "constants.py").read_text(encoding="utf-8")
    const_dst = DST / "constants.py"
    existing = const_dst.read_text(encoding="utf-8") if const_dst.exists() else ""
    if "MODULOS_METRICA_OFICIAL" not in existing:
        modulos_line = "from report_falhas.filters import MODULOS_METRICA_OFICIAL\n\n"
        const_dst.write_text(modulos_line + existing, encoding="utf-8")

    for name in COPY_SERVICE_FILES:
        write_transformed(SRC / "services" / name, DST / "services" / name)

    views_dst = DST / "views"
    views_dst.mkdir(exist_ok=True)
    for name in COPY_VIEW_FILES:
        write_transformed(SRC / "views" / name, views_dst / name)

    # portal helpers split from portal_auth
    portal_auth = (SRC / "portal_auth.py").read_text(encoding="utf-8")
    portal_auth = transform(portal_auth)

    me_py = '''# -*- coding: utf-8 -*-
from django.conf import settings as dj_settings
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.falhas_criticas.permissions import IsAuthenticatedPortal
from apps.falhas_criticas.scoping import get_user_scope
from apps.falhas_criticas.services.sync_status import last_sync_info
from apps.falhas_criticas.services.team_hierarchy import get_team_context
from apps.falhas_criticas.services.user_display import resolve_user_display_name


class MeView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def get(self, request):
        scope = get_user_scope(request.user)
        sync_info = last_sync_info()
        display_name = resolve_user_display_name(request.user)
        team_ctx = get_team_context(request.user)
        return Response({
            "username": scope["username"],
            "display_name": display_name,
            "email": (request.user.email or "").strip(),
            "scope": scope["scope"],
            "localidade_forcada": scope["localidade_forcada"],
            "can_sync": scope["can_sync"],
            "can_choose_localidade": scope["can_choose_localidade"],
            "is_staff": request.user.is_staff,
            "last_sync": sync_info["last_sync"],
            "last_sync_trigger": sync_info["last_sync_trigger"],
            "last_auto_sync": sync_info["last_auto_sync"],
            "last_sync_failed": sync_info["last_sync_failed"],
            "last_sync_failed_trigger": sync_info["last_sync_failed_trigger"],
            "sync_error_message": sync_info["sync_error_message"],
            "excel_source_name": sync_info["excel_source_name"],
            "data_max_date": sync_info["data_max_date"],
            "total_failures": sync_info["total_failures"],
            "team": team_ctx,
            "jira_collector_enabled": getattr(dj_settings, "JIRA_COLLECTOR_ENABLED", False),
        })
'''
    (DST / "views" / "me.py").write_text(me_py, encoding="utf-8")

    portal_py = '''# -*- coding: utf-8 -*-
from django.conf import settings
from django.views.generic import TemplateView

from apps.falhas_criticas.auth import PPLIDSessionRequiredMixin


class PortalView(PPLIDSessionRequiredMixin, TemplateView):
    template_name = "falhas_criticas/portal/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["pplid_home_url"] = settings.PPLID_FRONTEND_URL.rstrip("/") + "/home"
        return ctx
'''
    (DST / "views" / "portal.py").write_text(portal_py, encoding="utf-8")

    # sync_status + user_display from portal_auth logic
    sync_status = '''# -*- coding: utf-8 -*-
from pathlib import Path

from apps.falhas_criticas.models import Failure, SyncAuditLog
from apps.falhas_criticas.services.data_bounds import get_portal_data_bounds
from apps.falhas_criticas.services.excel_path import get_excel_source_path
from apps.falhas_criticas.services.sync_runner import _finalize_stale_sync_logs


def _default_sync_error_message(log) -> str:
    if log.trigger_source == SyncAuditLog.TRIGGER_SYSTEM:
        return "Verifique se o Excel está fechado."
    return "Feche o Excel e tente novamente pelo botão Sincronizar Excel."


def last_sync_info():
    _finalize_stale_sync_logs()
    info = {
        "last_sync": None,
        "last_sync_trigger": None,
        "last_auto_sync": None,
        "last_sync_failed": None,
        "last_sync_failed_trigger": None,
        "sync_error_message": None,
        "excel_source_name": None,
        "data_max_date": None,
        "total_failures": 0,
    }
    last_ok = (
        SyncAuditLog.objects.filter(success=True, finished_at__isnull=False)
        .order_by("-finished_at")
        .first()
    )
    if last_ok:
        info["last_sync"] = last_ok.finished_at or last_ok.started_at
        info["last_sync_trigger"] = last_ok.trigger_source
    last_auto = (
        SyncAuditLog.objects.filter(
            success=True,
            trigger_source=SyncAuditLog.TRIGGER_SYSTEM,
            finished_at__isnull=False,
        )
        .order_by("-finished_at")
        .first()
    )
    if last_auto:
        info["last_auto_sync"] = last_auto.finished_at or last_auto.started_at
    last_fail = (
        SyncAuditLog.objects.filter(success=False, finished_at__isnull=False)
        .order_by("-finished_at")
        .first()
    )
    if last_fail and last_ok:
        ok_time = last_ok.finished_at or last_ok.started_at
        fail_time = last_fail.finished_at or last_fail.started_at
        if fail_time > ok_time:
            info["last_sync_failed"] = fail_time
            info["last_sync_failed_trigger"] = last_fail.trigger_source
            msg = (last_fail.message or "").strip()
            info["sync_error_message"] = msg or _default_sync_error_message(last_fail)
    try:
        info["excel_source_name"] = Path(get_excel_source_path()).name
    except Exception:
        pass
    try:
        bounds = get_portal_data_bounds()
        info["data_max_date"] = bounds.get("max_date")
        info["total_failures"] = Failure.objects.count()
    except Exception:
        pass
    return info
'''
    (DST / "services" / "sync_status.py").write_text(sync_status, encoding="utf-8")

    user_display = '''# -*- coding: utf-8 -*-
from report_falhas.io.data_loader import norm_matricula

from apps.falhas_criticas.models import FalhasAgent


def resolve_user_display_name(user) -> str:
    if not user or not user.is_authenticated:
        return ""
    try:
        mat = norm_matricula(user.username)
        if mat:
            agent = FalhasAgent.objects.filter(pk=mat).first()
            if agent and agent.name.strip():
                return agent.name.strip()
    except Exception:
        pass
    fn = (user.first_name or "").strip()
    ln = (user.last_name or "").strip()
    full = f"{fn} {ln}".strip()
    if full:
        return full
    return user.username
'''
    (DST / "services" / "user_display.py").write_text(user_display, encoding="utf-8")

    # api urls (no tickets - phase 5)
    api_urls = (SRC / "urls.py").read_text(encoding="utf-8")
    api_urls = transform(api_urls)
    api_urls = re.sub(
        r"from apps\.falhas_criticas\.ticket_views import.*?\)\n",
        "",
        api_urls,
        flags=re.DOTALL,
    )
    api_urls = api_urls.replace(
        "from apps.falhas_criticas.portal_auth import MeView",
        "from apps.falhas_criticas.views.me import MeView",
    )
    api_urls = re.sub(
        r"\n    path\('tickets/.*?\n",
        "\n",
        api_urls,
    )
    (DST / "api_urls.py").write_text(api_urls, encoding="utf-8")

    # static + template
    tpl_src = SRC / "templates" / "portal" / "index.html"
    tpl_dst = DST / "templates" / "falhas_criticas" / "portal" / "index.html"
    tpl_dst.parent.mkdir(parents=True, exist_ok=True)
    tpl = tpl_src.read_text(encoding="utf-8")
    tpl = tpl.replace("/static/portal/", "/static/falhas_criticas/portal/")
    tpl = tpl.replace("/api/reports/", "/api/v1/falhas/")
    tpl = tpl.replace("/logout/", "/api/v1/auth/logout/")
    tpl_dst.write_text(tpl, encoding="utf-8")

    static_src = SRC / "static" / "portal"
    static_dst = DST / "static" / "falhas_criticas" / "portal"
    if static_dst.exists():
        shutil.rmtree(static_dst)
    shutil.copytree(static_src, static_dst)

    store = (static_dst / "store.js").read_text(encoding="utf-8")
    store = store.replace("'/api/reports'", "'/api/v1/falhas'")
    store = store.replace('"/api/reports"', '"/api/v1/falhas"')
    store = store.replace("/api/reports/", "/api/v1/falhas/")
    (static_dst / "store.js").write_text(store, encoding="utf-8")

    app_js = (static_dst / "portal-app.js").read_text(encoding="utf-8")
    app_js = app_js.replace("/api/reports/", "/api/v1/falhas/")
    app_js = app_js.replace("'/login/'", "window.PPLID_LOGIN_URL || '/'")
    (static_dst / "portal-app.js").write_text(app_js, encoding="utf-8")

    print("Port complete.")


if __name__ == "__main__":
    main()
