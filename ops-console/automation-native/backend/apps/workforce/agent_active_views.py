"""API para consistência Agent.active ↔ AgentHistory aberto."""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from apps.workforce.services.agent_active_reconcile import (
    ALIGNABLE_KINDS,
    apply_align_agent_active,
    list_mismatches,
    summarize_agent_history_consistency,
)

ViewPerm = portal_perm(R.PLANEJAMENTO_HEADCOUNT_VIEW)
ManagePerm = portal_perm(R.PLANEJAMENTO_HEADCOUNT_MANAGE)


class AgentActiveAlignThrottle(UserRateThrottle):
    scope = "agent_active_align"


class AgentActiveConsistencyView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        kind = str(request.query_params.get("kind") or "").strip()
        if kind:
            if kind not in {
                "stale_active",
                "orphan_open",
                "multi_open",
                "consistent_active",
                "consistent_inactive",
            }:
                return Response(
                    {"ok": False, "message": f"kind inválido: {kind}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                limit = min(int(request.query_params.get("limit") or 50), 200)
                offset = max(int(request.query_params.get("offset") or 0), 0)
            except ValueError:
                return Response(
                    {"ok": False, "message": "limit/offset inválidos."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            payload = list_mismatches(kind, limit=limit, offset=offset)  # type: ignore[arg-type]
            return Response({"ok": True, **payload}, status=status.HTTP_200_OK)

        summary = summarize_agent_history_consistency(sample_limit=15)
        return Response({"ok": True, **summary}, status=status.HTTP_200_OK)


class AgentActiveAlignView(APIView):
    permission_classes = [ManagePerm]
    throttle_classes = [AgentActiveAlignThrottle]

    def post(self, request):
        dry_run_raw = request.data.get("dry_run", True)
        dry_run = str(dry_run_raw).lower() not in {"0", "false", "no", "nao", "não"}
        kinds = request.data.get("kinds") or list(ALIGNABLE_KINDS)
        if isinstance(kinds, str):
            kinds = [k.strip() for k in kinds.split(",") if k.strip()]
        if not isinstance(kinds, (list, tuple)):
            return Response(
                {"ok": False, "message": "kinds deve ser lista."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invalid = [k for k in kinds if k not in ALIGNABLE_KINDS]
        if invalid:
            return Response(
                {
                    "ok": False,
                    "message": (
                        f"kinds inválidos: {invalid}. "
                        f"Permitidos: {list(ALIGNABLE_KINDS)}"
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            report = apply_align_agent_active(
                kinds=kinds,
                dry_run=dry_run,
                performed_by=request.user,
            )
        except ValueError as exc:
            return Response(
                {"ok": False, "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"ok": True, **report}, status=status.HTTP_200_OK)
