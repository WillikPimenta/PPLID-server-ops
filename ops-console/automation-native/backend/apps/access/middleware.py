"""Middleware de auditoria mínima para negações 403."""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger("apps.access.audit")


class AccessDeniedAuditMiddleware:
    """Registra respostas 403 quando DEBUG ou ACCESS_AUDIT_LOG=true."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code == 403 and self._should_log():
            user = getattr(request, "user", None)
            username = getattr(user, "username", "anonymous") if user else "anonymous"
            logger.warning(
                "403 Forbidden user=%s method=%s path=%s",
                username,
                request.method,
                request.path,
            )
        return response

    def _should_log(self) -> bool:
        return getattr(settings, "ACCESS_AUDIT_LOG", False) or settings.DEBUG
