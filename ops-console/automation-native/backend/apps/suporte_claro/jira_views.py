# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime

from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.permissions import CanViewSuporteClaro, IsAuthenticatedPortal
from apps.suporte_claro.services.jira_copy import (
    build_batch_jira_copy,
    build_registro_jira_copy,
    build_weekly_jira_copy,
    jira_pendentes_all_queryset,
    jira_pendentes_queryset,
    jira_pendentes_today_count,
    profile_label,
    resolve_batch_registros,
    resolve_jira_profile_key,
    validate_registros_for_jira_formalization,
)
from apps.suporte_claro.services.jira_link import auto_link_jira_issue_to_registro
from apps.suporte_claro.services.jira_rest import (
    clear_user_jira_token,
    create_issue,
    idle_jira_job,
    jira_base_configured,
    jira_config_payload,
    save_user_jira_token,
    user_jira_configured,
)
from apps.suporte_claro.services.serialization import serialize_registro


def _parse_optional_date(raw) -> date | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    parsed = parse_date(str(raw).strip())
    return parsed


def _resolve_profile(request) -> str | Response:
    profile_key = str(request.data.get("profile") or "").strip().lower() or resolve_jira_profile_key(
        request.user
    )
    if profile_key not in ("planejamento", "processos"):
        return Response({"detail": "Perfil inválido."}, status=status.HTTP_400_BAD_REQUEST)
    return profile_key


def _create_and_link(
    *,
    profile_key: str,
    summary: str,
    description: str,
    registro_id: int | None,
    user,
    protocolo: str | None = None,
) -> dict:
    created = create_issue(
        profile_key,
        summary=summary,
        description=description,
        user=user,
    )
    item: dict = {
        "ok": bool(created.get("ok")),
        "issue_key": created.get("issue_key"),
        "error": created.get("error"),
        "registro_id": registro_id,
        "protocolo": protocolo,
        "auto_link": None,
    }
    if item["ok"] and registro_id and item["issue_key"]:
        item["auto_link"] = auto_link_jira_issue_to_registro(
            registro_id=int(registro_id),
            issue_key=str(item["issue_key"]),
            user=user,
        )
    return item


class JiraConfigView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def get(self, request):
        profile_key = resolve_jira_profile_key(request.user)
        return Response(
            jira_config_payload(profile_key, profile_label(profile_key), user=request.user)
        )


class JiraCredentialsView(APIView):
    """Salva/remove o PAT Jira do Agent do usuário autenticado."""

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def put(self, request):
        token = str(request.data.get("jira_api_token") or request.data.get("token") or "")
        ok, message = save_user_jira_token(request.user, token)
        if not ok:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        profile_key = resolve_jira_profile_key(request.user)
        return Response(
            {
                "ok": True,
                "message": message,
                **jira_config_payload(profile_key, profile_label(profile_key), user=request.user),
            }
        )

    def delete(self, request):
        ok, message = clear_user_jira_token(request.user)
        if not ok:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        profile_key = resolve_jira_profile_key(request.user)
        return Response(
            {
                "ok": True,
                "message": message,
                **jira_config_payload(profile_key, profile_label(profile_key), user=request.user),
            }
        )


class JiraPreviewView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def post(self, request):
        kind = str(request.data.get("kind") or "weekly").strip().lower()
        profile_key = str(request.data.get("profile") or "").strip().lower() or resolve_jira_profile_key(
            request.user
        )
        if profile_key not in ("planejamento", "processos"):
            return Response({"detail": "Perfil inválido."}, status=status.HTTP_400_BAD_REQUEST)

        if kind == "registro":
            registro_id = request.data.get("registro_id")
            if not registro_id:
                return Response({"detail": "registro_id obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
            registro = SuporteClaroRegistro.objects.filter(pk=registro_id).first()
            if not registro:
                return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)
            err = validate_registros_for_jira_formalization([registro])
            if err:
                return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
            copy = build_registro_jira_copy(registro)
        elif kind == "batch":
            raw_ids = request.data.get("registro_ids")
            registro_ids = None
            if raw_ids is not None:
                try:
                    registro_ids = [int(x) for x in raw_ids]
                except (TypeError, ValueError):
                    return Response(
                        {"detail": "registro_ids inválido."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            registros = resolve_batch_registros(registro_ids)
            if not registros:
                return Response(
                    {"detail": "Nenhuma demanda pendente para formalizar."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            err = validate_registros_for_jira_formalization(registros)
            if err:
                return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
            copy = {"items": build_batch_jira_copy(registros), "count": len(registros)}
        else:
            date_from = _parse_optional_date(request.data.get("date_from"))
            date_to = _parse_optional_date(request.data.get("date_to"))
            copy = build_weekly_jira_copy(request.user, date_from=date_from, date_to=date_to)

        return Response(
            {
                "kind": kind,
                "profile_key": profile_key,
                "profile_label": profile_label(profile_key),
                **copy,
            }
        )


class JiraRunView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def post(self, request):
        if not jira_base_configured():
            return Response(
                {"detail": "Integração Jira não configurada. Defina JIRA_BASE_URL no backend."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not user_jira_configured(request.user):
            return Response(
                {
                    "detail": (
                        "Cadastre seu Personal Access Token do Jira antes de formalizar "
                        "(é salvo no seu Agent do headcount)."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_or_err = _resolve_profile(request)
        if isinstance(profile_or_err, Response):
            return profile_or_err
        profile_key = profile_or_err

        kind = str(request.data.get("kind") or "registro").strip().lower()

        if kind == "registro":
            registro_id = request.data.get("registro_id")
            registro = SuporteClaroRegistro.objects.filter(pk=registro_id).first()
            if not registro:
                return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)
            err = validate_registros_for_jira_formalization([registro])
            if err:
                return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
            copy = build_registro_jira_copy(registro)
            item = _create_and_link(
                profile_key=profile_key,
                summary=copy["summary"],
                description=copy["description"],
                registro_id=copy.get("registro_id"),
                user=request.user,
                protocolo=copy.get("protocolo"),
            )
            ok = bool(item["ok"])
            message = (
                f"Chamado {item['issue_key']} criado e vinculado."
                if ok and item.get("auto_link")
                else (
                    f"Chamado {item['issue_key']} criado."
                    if ok
                    else (item.get("error") or "Falha ao criar chamado no Jira.")
                )
            )
            return Response(
                {
                    "ok": ok,
                    "message": message,
                    "kind": kind,
                    "profile_key": profile_key,
                    "issue_key": item.get("issue_key"),
                    "auto_link": item.get("auto_link"),
                    "error": item.get("error"),
                    "registro_id": item.get("registro_id"),
                },
                status=status.HTTP_200_OK if ok else status.HTTP_502_BAD_GATEWAY,
            )

        if kind == "batch":
            raw_ids = request.data.get("registro_ids")
            registro_ids = None
            if raw_ids is not None:
                try:
                    registro_ids = [int(x) for x in raw_ids]
                except (TypeError, ValueError):
                    return Response(
                        {"detail": "registro_ids inválido."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            registros = resolve_batch_registros(registro_ids)
            if not registros:
                return Response(
                    {"detail": "Nenhuma demanda pendente para formalizar."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            err = validate_registros_for_jira_formalization(registros)
            if err:
                return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

            items_copy = build_batch_jira_copy(registros)
            items: list[dict] = []
            for copy in items_copy:
                items.append(
                    _create_and_link(
                        profile_key=profile_key,
                        summary=copy["summary"],
                        description=copy["description"],
                        registro_id=copy.get("registro_id"),
                        user=request.user,
                        protocolo=copy.get("protocolo"),
                    )
                )
            count_ok = sum(1 for i in items if i.get("ok"))
            count_fail = len(items) - count_ok
            ok = count_ok > 0
            message = f"Lote: {count_ok} criado(s), {count_fail} falha(s)."
            return Response(
                {
                    "ok": ok,
                    "message": message,
                    "kind": kind,
                    "profile_key": profile_key,
                    "items": items,
                    "count": len(items),
                    "count_ok": count_ok,
                    "count_fail": count_fail,
                },
                status=status.HTTP_200_OK if ok else status.HTTP_502_BAD_GATEWAY,
            )

        # weekly — uma issue agregada, sem auto-link de registro
        date_from = _parse_optional_date(request.data.get("date_from"))
        date_to = _parse_optional_date(request.data.get("date_to"))
        copy = build_weekly_jira_copy(request.user, date_from=date_from, date_to=date_to)
        created = create_issue(
            profile_key,
            summary=copy["summary"],
            description=copy["description"],
            user=request.user,
        )
        ok = bool(created.get("ok"))
        return Response(
            {
                "ok": ok,
                "message": (
                    f"Chamado {created.get('issue_key')} criado."
                    if ok
                    else (created.get("error") or "Falha ao criar chamado no Jira.")
                ),
                "kind": "weekly",
                "profile_key": profile_key,
                "issue_key": created.get("issue_key"),
                "error": created.get("error"),
                "count": copy.get("count"),
            },
            status=status.HTTP_200_OK if ok else status.HTTP_502_BAD_GATEWAY,
        )


class JiraStatusView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def get(self, request):
        return Response({"job": idle_jira_job()})


class JiraCancelView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def post(self, request):
        return Response(
            {
                "ok": True,
                "message": "Nenhuma formalização assíncrona em execução.",
                "job": idle_jira_job(),
            }
        )


class JiraPendentesView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def get(self, request):
        raw_scope = (request.query_params.get("scope") or "all").strip().lower()
        if raw_scope not in ("all", "today"):
            return Response(
                {"detail": "Parâmetro scope inválido. Use all ou today."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_date = (request.query_params.get("date") or "").strip()
        ref = _parse_optional_date(raw_date) if raw_date else timezone.localdate()
        if raw_date and ref is None:
            return Response({"detail": "Data inválida. Use AAAA-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        if raw_scope == "today":
            qs = jira_pendentes_queryset(ref)
        else:
            qs = jira_pendentes_all_queryset()

        items = [serialize_registro(reg, request) for reg in qs]
        for item in items:
            received_at = item.get("received_at")
            if received_at:
                try:
                    received_dt = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
                    if timezone.is_aware(received_dt):
                        received_dt = timezone.localtime(received_dt)
                    item["received_date"] = received_dt.date().isoformat()
                except (TypeError, ValueError):
                    item["received_date"] = None
            else:
                item["received_date"] = None

        return Response(
            {
                "scope": raw_scope,
                "date": ref.isoformat(),
                "total": len(items),
                "total_today": jira_pendentes_today_count(ref),
                "items": items,
            }
        )
