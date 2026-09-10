# -*- coding: utf-8 -*-
from rest_framework import status
from rest_framework.response import Response

from apps.produtividade.services.analytics import (
    build_agent_detail,
    build_dashboard,
    build_evolucao,
    build_kpis,
    build_por_agente,
    build_por_equipe,
    build_por_hora_page,
    build_supervisao,
    get_filter_options,
    serialize_record,
)
from apps.produtividade.services.meta_context import context_meta
from apps.produtividade.services.goal_adjustment import (
    build_logado_lookup_for_qs,
    build_ociosidade_lookup_for_qs,
    build_productivity_discount_lookup_for_qs,
)
from apps.produtividade.views.base import ProdutividadeAPIView
from apps.produtividade.views.heavy import HeavyProdutividadeMixin


class FilterOptionsView(ProdutividadeAPIView):
    def get(self, request):
        from apps.produtividade.models import ProductivityRecord
        from apps.produtividade.scoping import apply_produtividade_scope, direct_report_matriculas

        user = self.effective_user(request)
        qs = apply_produtividade_scope(ProductivityRecord.objects.all(), user)
        options = get_filter_options(qs)
        team_mats = direct_report_matriculas(user)
        options["team_filter"] = {
            "can_filter_team": len(team_mats) > 0,
        }
        return Response(options)


class KPIsView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request):
        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        return self.gated_response(
            route="kpis",
            params=params,
            builder=lambda: build_kpis(qs),
        )


class DashboardView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request):
        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        top_n = None
        if params.get("top_n"):
            try:
                top_n = int(params["top_n"])
            except (TypeError, ValueError):
                top_n = None

        def builder():
            return build_dashboard(qs, top_n=top_n)

        return self.gated_response(
            route="dashboard",
            params=params,
            builder=builder,
            extra={"top_n": top_n},
        )


class SupervisaoView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    """Visão agregada por líder — gerência / escopo global."""

    def get(self, request):
        from apps.access.constants import SCOPE_GLOBAL
        from apps.access.permissions import access_enforcement_enabled
        from apps.access.resolve import resolve_user_access
        from apps.access.services.impersonation import resolve_effective_user
        from apps.produtividade.scoping import produtividade_scope_for_user

        user = resolve_effective_user(request)
        access = resolve_user_access(user)
        scope = produtividade_scope_for_user(user)
        allowed = bool(access.get("bypass")) or scope == SCOPE_GLOBAL
        # Em shadow mode (ACCESS_ENFORCEMENT=false), não bloqueia além de CanViewProductivity —
        # alinha ao Dashboard, que já libera a tela para gestor.dev / sem perfil.
        if not allowed and access_enforcement_enabled():
            return Response(
                {
                    "detail": (
                        "Visão de supervisão disponível apenas para Op. Gerência "
                        "(escopo global de produtividade)."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        return self.gated_response(
            route="supervisao",
            params=params,
            builder=lambda: build_supervisao(qs),
        )


class EvolucaoView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request):
        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        compare_days = 7
        if params.get("compare_days"):
            try:
                compare_days = int(params["compare_days"])
            except (TypeError, ValueError):
                compare_days = 7
        return self.gated_response(
            route="evolucao",
            params=params,
            builder=lambda: build_evolucao(qs, compare_days=compare_days),
            extra={"compare_days": compare_days},
        )


class PorAgenteView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request):
        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        sort = params.get("sort") or "impact"
        page = int(params.get("page") or 1)
        page_size = int(params.get("page_size") or 50)

        def builder():
            results, summary = build_por_agente(qs, sort=sort)
            start = (page - 1) * page_size
            end = start + page_size
            return {
                "count": len(results),
                "page": page,
                "page_size": page_size,
                "summary": summary,
                "results": results[start:end],
            }

        return self.gated_response(
            route="por-agente",
            params=params,
            builder=builder,
            extra={"sort": sort, "page": page, "page_size": page_size},
        )


class PorEquipeView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request):
        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        group_by = params.get("group_by") or "team"

        def builder():
            results = build_por_equipe(qs, group_by=group_by)
            ctx = context_meta(qs)
            return {
                "group_by": group_by,
                "daily_threshold": ctx.applied_meta,
                "meta_evaluation": ctx.to_dict(),
                "results": results,
            }

        return self.gated_response(
            route="por-equipe",
            params=params,
            builder=builder,
            extra={"group_by": group_by},
        )


class PorHoraView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request):
        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        return self.gated_response(
            route="por-hora",
            params=params,
            builder=lambda: build_por_hora_page(qs),
        )


class RegistrosView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request):
        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad
        ordering = params.get("ordering") or "-recorded_at"
        if ordering.lstrip("-") in {
            "recorded_at",
            "matricula_norm",
            "etapa",
            "analysis_seconds",
            "team",
        }:
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by("-recorded_at")

        page = int(params.get("page") or 1)
        page_size = min(int(params.get("page_size") or 50), 200)
        start = (page - 1) * page_size
        end = start + page_size

        def builder():
            total = qs.count()
            ociosidade_lookup = build_ociosidade_lookup_for_qs(qs)
            discount_lookup = build_productivity_discount_lookup_for_qs(qs)
            logado_lookup = build_logado_lookup_for_qs(qs)
            records = [
                serialize_record(r, ociosidade_lookup, discount_lookup, logado_lookup)
                for r in qs[start:end]
            ]
            return {
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": records,
            }

        return self.gated_response(
            route="registros",
            params=params,
            builder=builder,
            extra={"ordering": ordering, "page": page, "page_size": page_size},
        )


class AgentDetailView(HeavyProdutividadeMixin, ProdutividadeAPIView):
    def get(self, request, matricula):
        from apps.produtividade.scoping import matricula_in_scope

        if not matricula_in_scope(self.effective_user(request), matricula):
            return Response({"detail": "Agente não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        qs, params = self.filtered_queryset(request)
        bad = self.enforce_date_range(params)
        if bad:
            return bad

        response = self.gated_response(
            route="agente",
            params=params,
            builder=lambda: build_agent_detail(
                matricula,
                qs,
                etapa=params.get("detail_etapa") or None,
                hour=params.get("detail_hour") or None,
            ),
            extra={
                "matricula": (matricula or "").strip().lower(),
                "detail_etapa": params.get("detail_etapa"),
                "detail_hour": params.get("detail_hour"),
            },
        )
        if response.status_code == 200 and not response.data:
            return Response({"detail": "Agente não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        return response
