"""Handlers mínimos para não expor páginas técnicas, URLs ou caminhos locais."""

from __future__ import annotations

from django.http import HttpResponse, JsonResponse

from config.api_exceptions import PUBLIC_INTERNAL_ERROR, new_incident_id


def _is_api_request(request) -> bool:
    return request.path.startswith("/api/")


def _public_response(request, *, status_code: int, message: str):
    if _is_api_request(request):
        return JsonResponse({"detail": message}, status=status_code)
    return HttpResponse(message, status=status_code, content_type="text/plain; charset=utf-8")


def bad_request(request, exception):
    return _public_response(request, status_code=400, message="Requisição inválida.")


def permission_denied(request, exception):
    return _public_response(request, status_code=403, message="Acesso não autorizado.")


def page_not_found(request, exception):
    return _public_response(request, status_code=404, message="Recurso não encontrado.")


def server_error(request):
    payload = {"detail": PUBLIC_INTERNAL_ERROR, "incident_id": new_incident_id()}
    if _is_api_request(request):
        return JsonResponse(payload, status=500)
    return HttpResponse(
        f"{PUBLIC_INTERNAL_ERROR} Referência: {payload['incident_id']}",
        status=500,
        content_type="text/plain; charset=utf-8",
    )
