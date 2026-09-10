# -*- coding: utf-8 -*-
"""Mixin de anti-gargalo para views pesadas de produtividade."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework import status
from rest_framework.response import Response

from apps.produtividade.services.produtividade_serve import (
    MAX_RANGE_DAYS,
    busy_response_detail,
    require_date_range,
    resolve_gated_payload,
)
from apps.produtividade.throttling import ProdutividadeHeavyThrottle


class HeavyProdutividadeMixin:
    throttle_classes = [ProdutividadeHeavyThrottle]
    require_dates = True
    max_range_days = MAX_RANGE_DAYS
    use_cache = True

    def enforce_date_range(self, params: dict) -> Response | None:
        if not self.require_dates:
            return None
        missing = require_date_range(params, max_days=self.max_range_days)
        if missing:
            return Response({"detail": missing}, status=status.HTTP_400_BAD_REQUEST)
        return None

    def gated_response(
        self,
        *,
        route: str,
        params: dict,
        builder: Callable[[], Any],
        extra: dict | None = None,
    ) -> Response:
        payload, err, _code = resolve_gated_payload(
            route=route,
            params=params,
            builder=builder,
            extra=extra,
            use_cache=self.use_cache,
        )
        if err:
            body, http_status, headers = busy_response_detail(err)
            return Response(body, status=http_status, headers=headers)
        return Response(payload)
