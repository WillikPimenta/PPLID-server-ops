# -*- coding: utf-8 -*-
from datetime import date
import re

from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from apps.produtividade_case.services.analitica_detail import (
    serialize_agente_detalhe,
    serialize_protocolos,
    serialize_ranking,
)
from apps.produtividade_case.services.consolidado_agg import (
    period_metadata,
    serialize_analitica_resumo,
    serialize_analitica_status,
    serialize_cruzamento,
    serialize_por_agente,
    serialize_por_workflow,
    serialize_serie_diaria,
)
from apps.produtividade_case.services.mvp_metrics import serialize_matriz_origem_destino
from apps.produtividade_case.views import CaseManagerAPIView


_PERIODO_RE = re.compile(r"^(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)-\d{4}$")


def _parse_date(raw: str | None, *, name: str) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValidationError({name: "Data inválida; use YYYY-MM-DD."})


def _period_kwargs(request) -> dict:
    periodo_mes = (request.query_params.get("periodo_mes") or "").strip() or None
    if periodo_mes and not _PERIODO_RE.fullmatch(periodo_mes.lower()):
        raise ValidationError(
            {"periodo_mes": "Período inválido; use a abreviação em português, por exemplo jul-2026."}
        )
    periodo_mes = periodo_mes.lower() if periodo_mes else None
    date_from = _parse_date(request.query_params.get("date_from"), name="date_from")
    date_to = _parse_date(request.query_params.get("date_to"), name="date_to")
    if date_from and date_to and date_from > date_to:
        raise ValidationError({"date_to": "date_to deve ser igual ou posterior a date_from."})
    return {
        "periodo_mes": periodo_mes,
        "date_from": date_from,
        "date_to": date_to,
    }


def _int_param(request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _response(payload: dict, kwargs: dict) -> Response:
    payload.update(period_metadata(**kwargs))
    return Response(payload)


class AnaliticaStatusView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        return _response(
            serialize_analitica_status(periodo_mes=kwargs["periodo_mes"]), kwargs
        )


class AnaliticaResumoView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        return _response(
            serialize_analitica_resumo(**kwargs, top=_int_param(request, "top", 15)), kwargs
        )


class AnaliticaPorWorkflowView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        return _response(serialize_por_workflow(**kwargs), kwargs)


class AnaliticaPorAgenteView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        return _response(serialize_por_agente(**kwargs), kwargs)


class AnaliticaSerieDiariaView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        wf = (request.query_params.get("workflow_origem") or "").strip() or None
        return _response(serialize_serie_diaria(**kwargs, workflow_origem=wf), kwargs)


class AnaliticaCruzamentoView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        return _response(
            serialize_cruzamento(
                **kwargs,
                top_workflows=_int_param(request, "top_workflows", 10),
                top_resultados=_int_param(request, "top_resultados", 8),
            ), kwargs
        )


class AnaliticaMatrizOrigemDestinoView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        return _response(
            serialize_matriz_origem_destino(
                **kwargs,
                top=_int_param(request, "top", 12),
            ), kwargs
        )


class AnaliticaRankingView(CaseManagerAPIView):
    def get(self, request):
        dimension = (request.query_params.get("dimension") or "").strip()
        kwargs = _period_kwargs(request)
        return _response(
            serialize_ranking(
                dimension=dimension,
                **kwargs,
                top=_int_param(request, "top", 50),
            ), kwargs
        )


class AnaliticaProtocolosView(CaseManagerAPIView):
    def get(self, request):
        kwargs = _period_kwargs(request)
        return _response(
            serialize_protocolos(
                **kwargs,
                workflow_origem=(request.query_params.get("workflow_origem") or "").strip()
                or None,
                matricula_destino=(request.query_params.get("matricula_destino") or "").strip()
                or None,
                resultado_destino=(request.query_params.get("resultado_destino") or "").strip()
                or None,
                resultado_origem=(request.query_params.get("resultado_origem") or "").strip()
                or None,
                q=(request.query_params.get("q") or "").strip() or None,
                ordering=(request.query_params.get("ordering") or "-conclusao_destino_at").strip(),
                page=_int_param(request, "page", 1),
                page_size=_int_param(request, "page_size", 25),
            ), kwargs
        )


class AnaliticaAgenteDetalheView(CaseManagerAPIView):
    def get(self, request, matricula: str):
        kwargs = _period_kwargs(request)
        return _response(
            serialize_agente_detalhe(
                matricula,
                **kwargs,
            ), kwargs
        )
