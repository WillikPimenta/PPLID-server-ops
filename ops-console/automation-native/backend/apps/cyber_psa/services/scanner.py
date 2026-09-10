# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.test import Client

from apps.cyber_psa.services.env_profile import adjust_finding_severity, get_env_profile
from apps.psa.services.registry import load_last_health_report


SENSITIVE_GET_PATHS = [
    "/api/v1/falhas/dashboard-summary/",
    "/api/v1/falhas/base-overview/",
    "/api/v1/agents/",
    "/api/v1/dashboard/overview/",
    "/api/v1/produtividade/overview/",
    "/api/v1/portal-ops/overview/",
    "/api/v1/cyber-psa/overview/",
    "/api/v1/escala-flex/context/",
]

SENSITIVE_POST_PATHS = [
    "/api/v1/falhas/sync/upload/",
]


def _finding(
    check_id: str,
    *,
    title: str,
    severity: str,
    category: str,
    description: str,
    remediation: str,
    ok: bool,
    extra: str = "",
) -> dict[str, Any]:
    severity = adjust_finding_severity(check_id, severity)
    return {
        "id": check_id,
        "title": title,
        "severity": severity,
        "category": category,
        "description": description,
        "remediation": remediation,
        "ok": ok,
        "status": "resolved" if ok else "open",
        "source": "auto",
        "extra": extra,
    }


def _ops_health_check() -> dict[str, Any]:
    report = load_last_health_report()
    if not report:
        return _finding(
            "CYBER-AUTO-OPS-HEALTH",
            title="Health check Console Ops registrado",
            severity="medium",
            category="Operação",
            description="Nenhum relatório portal-ops em documentation/psa-live-report.json.",
            remediation="Rodar validação no Console Ops antes de apresentar à SI.",
            ok=False,
            extra="missing report",
        )
    summary = report.get("summary") or {}
    failed = summary.get("failed", 0)
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    threshold_ok = failed == 0 or get_env_profile() == "local"
    return _finding(
        "CYBER-AUTO-OPS-HEALTH",
        title="Console Ops health check",
        severity="high" if not threshold_ok else "low",
        category="Operação",
        description="Integração com último health check do portal.",
        remediation="Corrigir falhas no Console Ops ou documentar exceções.",
        ok=threshold_ok,
        extra=f"{passed}/{total} OK",
    )


def run_security_scan(client: Client, user: AbstractBaseUser) -> list[dict[str, Any]]:
    del client, user
    items: list[dict[str, Any]] = []
    anon = Client()

    debug_on = bool(getattr(settings, "DEBUG", False))
    items.append(
        _finding(
            "CYBER-AUTO-DEBUG",
            title="DEBUG habilitado",
            severity="high" if debug_on else "low",
            category="Configuração",
            description="DEBUG=True expõe informações internas em erros.",
            remediation="Definir DEBUG=False em ambientes expostos ou de homologação/produção.",
            ok=not debug_on,
            extra=f"DEBUG={debug_on} profile={get_env_profile()}",
        )
    )

    secret = str(getattr(settings, "SECRET_KEY", ""))
    weak = not secret or "insecure" in secret.lower() or len(secret) < 32
    items.append(
        _finding(
            "CYBER-AUTO-SECRET",
            title="SECRET_KEY fraca ou padrão",
            severity="high" if weak else "low",
            category="Configuração",
            description="Chave secreta previsível compromete sessões e tokens.",
            remediation="Usar SECRET_KEY longa e única via variável de ambiente (.env).",
            ok=not weak,
        )
    )

    hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    wildcard = "*" in hosts
    items.append(
        _finding(
            "CYBER-AUTO-HOSTS",
            title="ALLOWED_HOSTS com wildcard",
            severity="medium" if wildcard else "low",
            category="Configuração",
            description="Host header attacks se ALLOWED_HOSTS=* em produção.",
            remediation="Listar hosts explicitamente.",
            ok=not wildcard,
            extra=",".join(hosts[:5]),
        )
    )

    cors = list(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
    cors_all = getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False)
    permissive = cors_all or not cors
    items.append(
        _finding(
            "CYBER-AUTO-CORS",
            title="CORS permissivo",
            severity="medium" if permissive else "low",
            category="Configuração",
            description="CORS aberto permite chamadas de origens não confiáveis.",
            remediation="Definir CORS_ALLOWED_ORIGINS apenas para o frontend do portal.",
            ok=not permissive,
            extra=f"origins={len(cors)} allow_all={cors_all}",
        )
    )

    csrf_origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
    items.append(
        _finding(
            "CYBER-AUTO-CSRF",
            title="CSRF trusted origins configurados",
            severity="low",
            category="Proteção web",
            description="Origens do frontend devem estar em CSRF_TRUSTED_ORIGINS.",
            remediation="Incluir URLs do Vite/dev e produção em CSRF_TRUSTED_ORIGINS.",
            ok=bool(csrf_origins),
            extra=f"count={len(csrf_origins)}",
        )
    )

    secure_cookie = bool(getattr(settings, "SESSION_COOKIE_SECURE", False))
    httponly = bool(getattr(settings, "SESSION_COOKIE_HTTPONLY", True))
    samesite = str(getattr(settings, "SESSION_COOKIE_SAMESITE", "") or "")
    session_ok = httponly and bool(samesite)
    if not secure_cookie:
        session_severity = "medium"
        session_note = "SESSION_COOKIE_SECURE=False (aceitável em HTTP local)"
    else:
        session_severity = "low"
        session_note = "Cookies de sessão com flags adequadas"
    items.append(
        _finding(
            "CYBER-AUTO-SESSION",
            title="Flags de cookie de sessão",
            severity=session_severity if not session_ok else "low",
            category="Proteção web",
            description=session_note,
            remediation="HttpOnly + SameSite; Secure=True quando HTTPS.",
            ok=session_ok,
            extra=f"secure={secure_cookie} httponly={httponly} samesite={samesite}",
        )
    )

    djangorest = getattr(settings, "REST_FRAMEWORK", {}) or {}
    default_perms = djangorest.get("DEFAULT_PERMISSION_CLASSES") or []
    allow_any_default = any("AllowAny" in str(p) for p in default_perms)
    items.append(
        _finding(
            "CYBER-AUTO-DRF-DEFAULT",
            title="DRF com AllowAny global",
            severity="medium" if allow_any_default else "low",
            category="Autorização",
            description="Permissão padrão AllowAny exige proteção explícita em cada view.",
            remediation="Revisar endpoints; manter IsAuthenticated nas APIs sensíveis.",
            ok=not allow_any_default,
            extra=str(default_perms),
        )
    )

    health_resp = anon.get("/api/v1/health/", HTTP_HOST="localhost")
    xfo = health_resp.headers.get("X-Frame-Options", "")
    xcto = health_resp.headers.get("X-Content-Type-Options", "")
    headers_ok = bool(xfo) and bool(xcto)
    items.append(
        _finding(
            "CYBER-AUTO-HEADERS",
            title="Security headers básicos",
            severity="medium" if not headers_ok else "low",
            category="Proteção web",
            description="X-Frame-Options e X-Content-Type-Options devem estar presentes.",
            remediation="Configurar SecurityMiddleware / headers no Django.",
            ok=headers_ok,
            extra=f"X-Frame-Options={xfo or '-'} X-Content-Type-Options={xcto or '-'}",
        )
    )

    for path in SENSITIVE_GET_PATHS:
        started = time.perf_counter()
        response = anon.get(path, HTTP_HOST="localhost")
        duration_ms = int((time.perf_counter() - started) * 1000)
        exposed = response.status_code == 200
        slug = path.strip("/").replace("/", "-").replace("api-v1-", "")
        items.append(
            _finding(
                f"CYBER-AUTO-UNAUTH-{slug}",
                title=f"Endpoint sensível sem auth: {path}",
                severity="high" if exposed else "low",
                category="Autorização",
                description="Resposta 200 sem autenticação indica possível vazamento de dados.",
                remediation="Exigir IsAuthenticated ou retornar 401/403.",
                ok=not exposed,
                extra=f"status={response.status_code} {duration_ms}ms",
            )
        )

    for path in SENSITIVE_POST_PATHS:
        response = anon.post(path, data={}, content_type="application/json", HTTP_HOST="localhost")
        protected = response.status_code in (401, 403, 405)
        slug = path.strip("/").replace("/", "-").replace("api-v1-", "")
        items.append(
            _finding(
                f"CYBER-AUTO-POST-{slug}",
                title=f"POST destrutivo protegido: {path}",
                severity="high" if not protected else "low",
                category="Autorização",
                description="Upload/sync de falhas não deve aceitar POST anônimo.",
                remediation="Exigir autenticação e can_sync.",
                ok=protected,
                extra=f"status={response.status_code}",
            )
        )

    items.append(_ops_health_check())
    return items


def apply_governance_auto_checks(governance: list[dict], scan_items: list[dict]) -> list[dict]:
    by_check: dict[str, dict] = {item["id"]: item for item in scan_items}
    auto_map = {
        "debug_enabled": "CYBER-AUTO-DEBUG",
        "weak_secret_key": "CYBER-AUTO-SECRET",
        "allowed_hosts_wildcard": "CYBER-AUTO-HOSTS",
        "cors_permissive": "CYBER-AUTO-CORS",
        "csrf_configured": "CYBER-AUTO-CSRF",
        "session_cookie_flags": "CYBER-AUTO-SESSION",
        "security_headers": "CYBER-AUTO-HEADERS",
        "sync_post_protected": "CYBER-AUTO-POST-api-v1-falhas-sync-upload-",
        "ops_health_ok": "CYBER-AUTO-OPS-HEALTH",
        "unauth_sensitive_endpoints": None,
    }
    updated: list[dict] = []
    for control in governance:
        row = dict(control)
        auto_key = row.get("auto_check")
        if not auto_key:
            updated.append(row)
            continue
        if auto_key == "unauth_sensitive_endpoints":
            unauth_fails = [i for i in scan_items if i["id"].startswith("CYBER-AUTO-UNAUTH-") and not i["ok"]]
            row["scan_ok"] = len(unauth_fails) == 0
            row["scan_detail"] = f"{len(unauth_fails)} endpoint(s) exposto(s)" if unauth_fails else "OK"
        else:
            mapped = auto_map.get(str(auto_key))
            item = by_check.get(mapped or "")
            if item:
                row["scan_ok"] = item["ok"]
                row["scan_detail"] = item.get("extra") or item.get("description", "")
        updated.append(row)
    return updated
