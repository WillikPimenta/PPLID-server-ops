"""Respostas públicas seguras para falhas inesperadas da aplicação."""

from __future__ import annotations

import logging
from uuid import uuid4

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("pplid.security.errors")

PUBLIC_INTERNAL_ERROR = "Não foi possível concluir a operação. Tente novamente."


def new_incident_id() -> str:
    return uuid4().hex


def report_internal_error(exc: BaseException, *, operation: str) -> str:
    """Registra detalhes somente no servidor e devolve uma referência opaca."""

    incident_id = new_incident_id()
    logger.error(
        "Falha interna operation=%s incident_id=%s",
        operation,
        incident_id,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return incident_id


def secure_error_payload(
    exc: BaseException,
    *,
    operation: str,
    message_field: str = "detail",
    extra: dict | None = None,
) -> dict:
    payload = dict(extra or {})
    payload[message_field] = PUBLIC_INTERNAL_ERROR
    payload["incident_id"] = report_internal_error(exc, operation=operation)
    return payload


def secure_exception_handler(exc, context):
    """Preserva erros esperados e oculta qualquer detalhe de falhas HTTP 5xx."""

    response = drf_exception_handler(exc, context)
    if response is not None and response.status_code < status.HTTP_500_INTERNAL_SERVER_ERROR:
        return response

    payload = secure_error_payload(exc, operation="api.unhandled")
    return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
