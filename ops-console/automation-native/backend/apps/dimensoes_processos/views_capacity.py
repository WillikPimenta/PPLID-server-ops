"""API do Capacity diário do Megazord."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permission_classes import portal_perm
from apps.access.registry import PLANEJAMENTO_MEGAZORD_VIEW
from apps.dimensoes_processos.models import CapacityFamiliaAlias
from apps.dimensoes_processos.services.capacity import (
    CapacityUnavailableError,
    calculate_capacity_period,
    calculate_daily_capacity,
)
from apps.dimensoes_processos.services.capacity_familia import (
    familia_alias_key,
    invalidate_familia_alias_cache,
)
from apps.dimensoes_processos.services.capacity_distribution import (
    calculate_capacity_distribution,
)
from apps.dimensoes_processos.services.capacity_export import (
    derivacoes_csv,
    hourly_drilldown_csv,
    volume_esperado_hora_csv,
)
from apps.dimensoes_processos.services.capacity_scenarios import (
    normalize_scenario_id,
    scenario_public_payload,
    scenario_config,
)
from apps.dimensoes_processos.services.capacity_simulation import (
    calculate_capacity_simulation_comparison,
)
from apps.dimensoes_processos.services.capacity_observability import (
    observe_capacity_operation,
)

MegazordPerm = portal_perm(PLANEJAMENTO_MEGAZORD_VIEW)


def _bool_query_param(request, name: str, *, default: bool) -> bool:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "sim", "yes"}


def _scenario_from_request(request) -> tuple[str, dict | None]:
    scenario_id = normalize_scenario_id(
        request.query_params.get("scenario") or request.query_params.get("cenario")
    )
    manual_overrides = None
    request_data = getattr(request, "data", None)
    body_overrides = (
        request_data.get("overrides")
        if request_data is not None and hasattr(request_data, "get")
        else None
    )
    raw_overrides = request.query_params.get("overrides")
    if body_overrides is not None:
        if not isinstance(body_overrides, dict):
            raise ValueError("Parametro overrides deve ser um objeto JSON.")
        manual_overrides = body_overrides
    elif raw_overrides:
        try:
            manual_overrides = json.loads(raw_overrides)
        except json.JSONDecodeError as exc:
            raise ValueError("Parametro overrides deve ser JSON valido.") from exc

    if manual_overrides is not None:
        if not isinstance(manual_overrides, dict):
            raise ValueError("Parametro overrides deve ser um objeto JSON.")
        allowed = {"workflows", "derivations", "metas"}
        unknown = set(manual_overrides) - allowed
        if unknown:
            raise ValueError(f"Chaves de override invalidas: {', '.join(sorted(unknown))}.")
        required_by_collection = {
            "workflows": ("id_cliente", "id_workflow"),
            "derivations": ("id_cliente", "id_workflow", "id_etapa", "percentual"),
            "metas": ("id_etapa", "meta_dia"),
        }
        for collection, required in required_by_collection.items():
            rows = manual_overrides.get(collection, [])
            if not isinstance(rows, list):
                raise ValueError(f"Override {collection} deve ser uma lista.")
            for item in rows:
                if not isinstance(item, dict) or any(key not in item for key in required):
                    raise ValueError(f"Override {collection} possui chaves obrigatorias ausentes.")
                value_key = required[-1]
                if collection == "workflows":
                    supplied = [key for key in ("volume", "percentual_variacao") if key in item]
                    if len(supplied) != 1:
                        raise ValueError("Override workflows deve informar volume ou percentual_variacao.")
                    value_key = supplied[0]
                try:
                    if any(int(item[key]) <= 0 for key in required if key.startswith("id_")):
                        raise ValueError
                    value = Decimal(str(item[value_key]))
                    if not value.is_finite():
                        raise ValueError
                except (InvalidOperation, TypeError, ValueError):
                    raise ValueError(f"Override {collection} possui valor ou chave invalida.")
                if collection == "metas" and value <= 0:
                    raise ValueError("Meta manual deve ser maior que zero.")
                if collection == "derivations" and not 0 <= value <= 100:
                    raise ValueError("Percentual de derivacao deve estar entre 0 e 100.")
                if collection == "workflows" and value_key == "volume" and value < 0:
                    raise ValueError("Volume manual nao pode ser negativo.")
                if collection == "workflows" and value_key == "percentual_variacao" and value < -100:
                    raise ValueError("Variacao percentual nao pode ser menor que -100%.")
    if scenario_id == "manual" and not manual_overrides:
        manual_overrides = {}
    return scenario_id, manual_overrides


class CapacityScenarioCatalogView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        return Response(
            {
                "ok": True,
                "results": [
                    scenario_public_payload(item)
                    for item in (
                        scenario_config("planejamento"),
                        scenario_config("operacao"),
                        scenario_config("manual"),
                    )
                ],
            }
        )


def _serialize_familia_alias(row: CapacityFamiliaAlias) -> dict:
    return {
        "id": row.pk,
        "alias_origem": row.alias_origem,
        "familia_canonica": row.familia_canonica,
        "ativo": row.ativo,
        "notas": row.notas,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


class CapacityDailyView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        raw_date = request.query_params.get("date")
        if not raw_date:
            return Response(
                {"detail": "Informe a data do Capacity."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            on_date = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Data inválida. Use o formato AAAA-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_from = profile_to = None
        raw_profile_from = request.query_params.get("profile_from")
        raw_profile_to = request.query_params.get("profile_to")
        try:
            if raw_profile_from:
                profile_from = date.fromisoformat(raw_profile_from)
            if raw_profile_to:
                profile_to = date.fromisoformat(raw_profile_to)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Período do perfil inválido. Use AAAA-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if profile_from and profile_to and profile_from > profile_to:
            return Response(
                {"detail": "profile_from não pode ser posterior a profile_to."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            scenario_id, manual_overrides = _scenario_from_request(request)
            with observe_capacity_operation(
                "daily",
                scenario=scenario_id,
                days=1,
                count_queries=_bool_query_param(request, "observe_queries", default=False),
            ) as observation:
                payload = calculate_daily_capacity(
                    on_date,
                    profile_from=profile_from,
                    profile_to=profile_to,
                    include_details=_bool_query_param(
                        request, "include_details", default=True
                    ),
                    # O DAX e uma homologacao custosa e nao faz parte do caminho
                    # operacional. Carregue-o explicitamente e sob demanda.
                    include_dax=_bool_query_param(request, "include_dax", default=False),
                    include_quarterly=_bool_query_param(
                        request, "include_quarterly", default=False
                    ),
                    scenario_id=scenario_id,
                    manual_overrides=manual_overrides,
                )
                observation.capture_result(payload)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except CapacityUnavailableError as exc:
            return Response(
                {"ok": False, "ready": False, "detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(payload)

    def post(self, request):
        """Calcula cenarios manuais com overrides no corpo, evitando URLs gigantes."""
        return self.get(request)


class CapacityDerivacoesExportView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        try:
            on_date = date.fromisoformat(request.query_params.get("date", ""))
        except (TypeError, ValueError):
            return Response({"detail": "Data inválida. Use o formato AAAA-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return derivacoes_csv(on_date)
        except CapacityUnavailableError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)


class CapacityVolumeEsperadoHoraExportView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        try:
            on_date = date.fromisoformat(request.query_params.get("date", ""))
        except (TypeError, ValueError):
            return Response({"detail": "Data inválida. Use o formato AAAA-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        return volume_esperado_hora_csv(on_date)


class CapacityHourlyDrilldownExportView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        try:
            on_date = date.fromisoformat(request.query_params.get("date", ""))
            hour = int(request.query_params.get("hour", ""))
            ids = {
                name: int(request.query_params[name])
                if request.query_params.get(name) not in (None, "")
                else None
                for name in ("id_cliente", "id_workflow", "id_etapa")
            }
        except (TypeError, ValueError):
            return Response(
                {"detail": "Data, hora ou identificador invalido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return hourly_drilldown_csv(
                on_date,
                hour=hour,
                level=request.query_params.get("level") or "family",
                familia=(request.query_params.get("familia") or "").strip() or None,
                **ids,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except CapacityUnavailableError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)


class CapacityPeriodView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        raw_from = request.query_params.get("date_from")
        raw_to = request.query_params.get("date_to")
        if not raw_from or not raw_to:
            return Response(
                {"detail": "Informe date_from e date_to no formato AAAA-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            date_from = date.fromisoformat(raw_from)
            date_to = date.fromisoformat(raw_to)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Periodo invalido. Use o formato AAAA-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if date_from > date_to:
            return Response(
                {"detail": "date_from nao pode ser posterior a date_to."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (date_to - date_from).days + 1 > 31:
            return Response(
                {"detail": "O periodo maximo permitido e de 31 dias."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            scenario_id, manual_overrides = _scenario_from_request(request)
            with observe_capacity_operation(
                "period",
                scenario=scenario_id,
                days=(date_to - date_from).days + 1,
                count_queries=_bool_query_param(request, "observe_queries", default=False),
            ) as observation:
                payload = calculate_capacity_period(
                    date_from,
                    date_to,
                    scenario_id=scenario_id,
                    manual_overrides=manual_overrides,
                    force_live=_bool_query_param(request, "force_live", default=False),
                )
                observation.capture_result(payload)
            return Response(payload)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    def post(self, request):
        """Calcula cenarios manuais com overrides no corpo, evitando URLs gigantes."""
        return self.get(request)


class CapacitySimulationCompareView(APIView):
    """Executa as variantes da simulacao com um unico contexto de fontes."""

    permission_classes = [MegazordPerm]

    def post(self, request):
        try:
            date_from = date.fromisoformat(request.query_params.get("date_from", ""))
            date_to = date.fromisoformat(request.query_params.get("date_to", ""))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Periodo invalido. Use date_from/date_to em AAAA-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        day_count = (date_to - date_from).days + 1
        if day_count < 1 or day_count > 31:
            return Response(
                {"detail": "O periodo deve conter entre 1 e 31 dias."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            _scenario_id, manual_overrides = _scenario_from_request(request)
            with observe_capacity_operation(
                "simulation_compare",
                scenario="manual",
                days=day_count,
                count_queries=_bool_query_param(request, "observe_queries", default=False),
            ) as observation:
                payload = calculate_capacity_simulation_comparison(
                    date_from,
                    date_to,
                    manual_overrides=manual_overrides,
                )
                observation.capture_result((payload.get("results") or {}).get("full"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class CapacityDistributionView(APIView):
    """Lista workflows e abre o drill diario workflow -> etapa -> hora."""

    permission_classes = [MegazordPerm]

    def get(self, request):
        raw_date = request.query_params.get("date")
        if not raw_date:
            return Response(
                {"detail": "Informe date no formato AAAA-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            on_date = date.fromisoformat(raw_date)
            profile_from = (
                date.fromisoformat(request.query_params["profile_from"])
                if request.query_params.get("profile_from")
                else None
            )
            profile_to = (
                date.fromisoformat(request.query_params["profile_to"])
                if request.query_params.get("profile_to")
                else None
            )
            ids = {}
            for name in ("id_cliente", "id_workflow", "id_etapa"):
                raw = request.query_params.get(name)
                ids[name] = int(raw) if raw not in (None, "") else None
            raw_hour = request.query_params.get("hour")
            hour = int(raw_hour) if raw_hour not in (None, "") else None
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 50))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Data, identificador ou paginacao invalida."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            scenario_id, manual_overrides = _scenario_from_request(request)
            with observe_capacity_operation(
                "distribution",
                scenario=scenario_id,
                days=1,
                count_queries=_bool_query_param(request, "observe_queries", default=False),
            ) as observation:
                payload = calculate_capacity_distribution(
                    on_date,
                    **ids,
                    hour=hour,
                    level=request.query_params.get("level"),
                    familia=(request.query_params.get("familia") or "").strip() or None,
                    search=request.query_params.get("search", ""),
                    profile_from=profile_from,
                    profile_to=profile_to,
                    page=page,
                    page_size=page_size,
                    scenario_id=scenario_id,
                    manual_overrides=manual_overrides,
                )
                observation.capture_result(payload)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except CapacityUnavailableError as exc:
            return Response(
                {"ok": False, "ready": False, "detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(payload)

    def post(self, request):
        """Mantem overrides manuais fora da URL durante o drill-down."""
        return self.get(request)


class CapacityFamiliaAliasView(APIView):
    permission_classes = [MegazordPerm]

    def get(self, request):
        qs = CapacityFamiliaAlias.objects.filter(ativo=True)
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(alias_origem__icontains=search)
                | Q(familia_canonica__icontains=search)
                | Q(notas__icontains=search)
            )
        rows = qs.order_by("familia_canonica", "alias_origem")[:300]
        return Response({"ok": True, "results": [_serialize_familia_alias(row) for row in rows]})

    def post(self, request):
        alias_origem = (request.data.get("alias_origem") or "").strip()
        familia_canonica = (request.data.get("familia_canonica") or "").strip()
        notas = (request.data.get("notas") or "").strip()

        if not alias_origem or not familia_canonica:
            return Response(
                {"detail": "alias_origem e familia_canonica são obrigatórios."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        key = familia_alias_key(alias_origem)
        row, created = CapacityFamiliaAlias.objects.update_or_create(
            alias_key=key,
            defaults={
                "alias_origem": alias_origem,
                "familia_canonica": familia_canonica,
                "notas": notas,
                "ativo": True,
                "created_by": request.user if request.user.is_authenticated else None,
            },
        )
        invalidate_familia_alias_cache()
        return Response(
            {"ok": True, "created": created, "result": _serialize_familia_alias(row)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def patch(self, request):
        alias_id = request.data.get("id")
        if not alias_id:
            return Response({"detail": "id obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        row = CapacityFamiliaAlias.objects.filter(pk=alias_id).first()
        if row is None:
            return Response({"detail": "Alias não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        if "alias_origem" in request.data:
            alias_origem = (request.data.get("alias_origem") or "").strip()
            if not alias_origem:
                return Response({"detail": "alias_origem inválido."}, status=status.HTTP_400_BAD_REQUEST)
            row.alias_origem = alias_origem
            row.alias_key = familia_alias_key(alias_origem)
        if "familia_canonica" in request.data:
            familia_canonica = (request.data.get("familia_canonica") or "").strip()
            if not familia_canonica:
                return Response(
                    {"detail": "familia_canonica inválida."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            row.familia_canonica = familia_canonica
        if "notas" in request.data:
            row.notas = (request.data.get("notas") or "").strip()
        if "ativo" in request.data:
            row.ativo = bool(request.data.get("ativo"))

        try:
            row.save()
        except Exception:
            return Response(
                {"detail": "Não foi possível salvar — verifique duplicidade de alias."},
                status=status.HTTP_409_CONFLICT,
            )

        invalidate_familia_alias_cache()
        return Response({"ok": True, "result": _serialize_familia_alias(row)})
