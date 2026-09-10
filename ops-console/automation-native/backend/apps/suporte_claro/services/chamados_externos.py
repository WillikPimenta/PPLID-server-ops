# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from django.db.models import Count

from apps.suporte_claro.models import (
    SuporteClaroChamadoExterno,
    SuporteClaroHistorico,
    SuporteClaroRegistro,
)
from apps.suporte_claro.services.audit import log_registro_change
from apps.suporte_claro.services.externo import (
    CHAMADO_SISTEMA_LABELS,
    chamado_display_label,
    resolve_chamado_url,
)
from apps.suporte_claro.services.validation import (
    parse_chamado_sistema,
    validate_chamado_externo,
)

MAX_CHAMADOS_EXTERNOS = 5


def _normalize_item(raw: dict) -> tuple[str, str, str, str] | None:
    if not isinstance(raw, dict):
        return None
    raw_sistema = str(raw.get("sistema") or raw.get("chamado_sistema") or "").strip()
    if not raw_sistema:
        return None
    sistema = parse_chamado_sistema(raw_sistema)
    if sistema is None:
        return None
    codigo = str(raw.get("codigo") or raw.get("chamado_codigo") or "").strip()
    url = str(raw.get("url") or raw.get("chamado_url") or "").strip()
    tratado_por = str(
        raw.get("tratado_por") or raw.get("tratadoPor") or ""
    ).strip()[:255]
    err = validate_chamado_externo(sistema, codigo, url)
    if err:
        raise ValueError(err)
    return sistema, codigo, url, tratado_por


def _parse_chamados_json(raw) -> list[tuple[str, str, str, str]]:
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Lista de chamados externos invalida.") from exc
    elif isinstance(raw, list):
        parsed = raw
    else:
        raise ValueError("Lista de chamados externos invalida.")

    items: list[tuple[str, str, str, str]] = []
    for entry in parsed:
        item = _normalize_item(entry)
        if item:
            items.append(item)
    return items


def parse_chamados_externos_payload(data) -> tuple[list[tuple[str, str, str, str]], str | None]:
    """Aceita `chamados_externos` (JSON/lista) ou campos legados do primeiro chamado."""
    if "chamados_externos" in data or "chamadosExternos" in data:
        raw = data.get("chamados_externos") or data.get("chamadosExternos")
        try:
            items = _parse_chamados_json(raw)
        except ValueError as exc:
            return [], str(exc)
        if len(items) > MAX_CHAMADOS_EXTERNOS:
            return [], f"Maximo de {MAX_CHAMADOS_EXTERNOS} chamados externos por demanda."
        return items, None

    chamado_keys = (
        "chamado_sistema",
        "chamadoSistema",
        "chamado_codigo",
        "chamadoCodigo",
        "chamado_url",
        "chamadoUrl",
    )
    if not any(key in data for key in chamado_keys):
        return [], None

    raw_sistema = str(data.get("chamado_sistema") or data.get("chamadoSistema") or "").strip()
    if raw_sistema:
        sistema = parse_chamado_sistema(raw_sistema)
        if sistema is None:
            return [], "Sistema de chamado invalido."
    else:
        sistema = ""
    codigo = str(data.get("chamado_codigo") or data.get("chamadoCodigo") or "").strip()
    url = str(data.get("chamado_url") or data.get("chamadoUrl") or "").strip()
    tratado_por = str(
        data.get("tratado_por") or data.get("tratadoPor") or ""
    ).strip()[:255]
    err = validate_chamado_externo(sistema, codigo, url)
    if err:
        return [], err
    if not sistema:
        return [], None
    return [(sistema, codigo, url, tratado_por)], None


def sync_primary_chamado_fields(registro: SuporteClaroRegistro) -> None:
    """Espelha no registro o chamado interno (preferência) ou o primeiro externo."""
    first = (
        registro.chamados_externos.filter(natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO)
        .order_by("ordem", "id")
        .first()
    )
    if first is None:
        first = registro.chamados_externos.order_by("ordem", "id").first()
    if first:
        registro.chamado_sistema = first.sistema
        registro.chamado_codigo = first.codigo
        registro.chamado_url = first.url
    else:
        registro.chamado_sistema = ""
        registro.chamado_codigo = ""
        registro.chamado_url = ""


def _normalize_chamado_items(items) -> list[tuple[str, str, str, str]]:
    normalized = []
    for item in items or []:
        if len(item) == 3:
            sistema, codigo, url = item
            tratado_por = ""
        else:
            sistema, codigo, url, tratado_por = item
        normalized.append((sistema, codigo, url, tratado_por or ""))
    return normalized


def _format_chamados_summary(items: list[tuple[str, str, str, str]]) -> str:
    if not items:
        return "Nenhum"
    parts = []
    for sistema, codigo, _url, tratado_por in items:
        label = CHAMADO_SISTEMA_LABELS.get(sistema, sistema)
        code = codigo or "—"
        part = f"{label} {code}"
        if tratado_por:
            part += f" ({tratado_por})"
        parts.append(part)
    return "; ".join(parts)


def save_chamados_externos(
    registro: SuporteClaroRegistro,
    items: list[tuple[str, str, str, str]],
    *,
    user,
) -> None:
    """
    Substitui apenas chamados de natureza=externo.
    Chamados internos (Jira auto) são preservados.
    """
    items = _normalize_chamado_items(items)
    old_items = list(
        registro.chamados_externos.filter(natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO)
        .order_by("ordem", "id")
        .values_list("sistema", "codigo", "url", "tratado_por")
    )
    new_summary = _format_chamados_summary(items)
    old_summary = _format_chamados_summary(old_items)
    if new_summary != old_summary:
        log_registro_change(
            registro=registro,
            user=user,
            action=SuporteClaroHistorico.ACTION_EDIT,
            field_name="chamados_externos",
            old_value=old_summary,
            new_value=new_summary,
        )

    registro.chamados_externos.filter(
        natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO
    ).delete()

    internos = list(
        registro.chamados_externos.filter(
            natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO
        ).order_by("ordem", "id")
    )
    start_ordem = len(internos)
    for offset, (sistema, codigo, url, tratado_por) in enumerate(items):
        SuporteClaroChamadoExterno.objects.create(
            registro=registro,
            sistema=sistema,
            codigo=codigo,
            url=url,
            tratado_por=tratado_por or "",
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
            ordem=start_ordem + offset,
        )
    sync_primary_chamado_fields(registro)
    registro.save(
        update_fields=[
            "chamado_sistema",
            "chamado_codigo",
            "chamado_url",
            "updated_at",
        ]
    )


def serialize_chamado_externo(chamado: SuporteClaroChamadoExterno, request=None) -> dict:
    return {
        "id": chamado.id,
        "sistema": chamado.sistema,
        "sistema_label": CHAMADO_SISTEMA_LABELS.get(chamado.sistema, chamado.sistema),
        "codigo": chamado.codigo or "",
        "url": chamado.url or "",
        "tratado_por": getattr(chamado, "tratado_por", None) or "",
        "natureza": chamado.natureza or SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
        "link": resolve_chamado_url(chamado.sistema, chamado.codigo, chamado.url),
        "label": chamado_display_label(chamado.sistema, chamado.codigo),
    }


def get_chamado_interno(registro: SuporteClaroRegistro) -> SuporteClaroChamadoExterno | None:
    return (
        registro.chamados_externos.filter(natureza=SuporteClaroChamadoExterno.NATUREZA_INTERNO)
        .order_by("ordem", "id")
        .first()
    )


def serialize_chamado_interno(registro: SuporteClaroRegistro, request=None) -> dict | None:
    chamado = get_chamado_interno(registro)
    if not chamado:
        return None
    return serialize_chamado_externo(chamado, request)


def serialize_chamados_externos(registro: SuporteClaroRegistro, request=None) -> list[dict]:
    """Apenas chamados de natureza externa (manuais)."""
    return [
        serialize_chamado_externo(chamado, request)
        for chamado in registro.chamados_externos.filter(
            natureza=SuporteClaroChamadoExterno.NATUREZA_EXTERNO
        ).order_by("ordem", "id")
    ]


def queryset_sem_chamado_externo(qs):
    """Sem nenhum chamado vinculado (interno ou externo) e sem legado no registro."""
    return qs.filter(chamado_sistema="").annotate(
        n_chamados=Count("chamados_externos")
    ).filter(n_chamados=0)


def append_jira_chamado_if_missing(
    registro: SuporteClaroRegistro,
    issue_key: str,
    *,
    user,
    natureza: str = SuporteClaroChamadoExterno.NATUREZA_INTERNO,
) -> bool:
    """
    Vincula issue Jira ao registro sem apagar chamados existentes.
    Por padrão natureza=interno (abertura automática pelo portal).
    """
    key = (issue_key or "").strip().upper()
    if not key:
        return False

    if registro.chamados_externos.filter(
        sistema=SuporteClaroRegistro.CHAMADO_JIRA,
        codigo__iexact=key,
    ).exists():
        return False

    if (
        (registro.chamado_sistema or "").strip() == SuporteClaroRegistro.CHAMADO_JIRA
        and (registro.chamado_codigo or "").strip().upper() == key
        and not registro.chamados_externos.exists()
    ):
        # Legado só no campo denormalizado: materializa como interno.
        pass
    elif (registro.chamado_sistema or "").strip() == SuporteClaroRegistro.CHAMADO_JIRA:
        if (registro.chamado_codigo or "").strip().upper() == key:
            return False

    natureza_val = (
        natureza
        if natureza
        in (
            SuporteClaroChamadoExterno.NATUREZA_INTERNO,
            SuporteClaroChamadoExterno.NATUREZA_EXTERNO,
        )
        else SuporteClaroChamadoExterno.NATUREZA_INTERNO
    )

    max_ordem = (
        registro.chamados_externos.order_by("-ordem").values_list("ordem", flat=True).first()
    )
    next_ordem = (max_ordem + 1) if max_ordem is not None else 0

    log_registro_change(
        registro=registro,
        user=user,
        action=SuporteClaroHistorico.ACTION_EDIT,
        field_name="chamados_externos",
        old_value="",
        new_value=f"Jira {key} ({natureza_val})",
    )

    SuporteClaroChamadoExterno.objects.create(
        registro=registro,
        sistema=SuporteClaroRegistro.CHAMADO_JIRA,
        codigo=key,
        url="",
        natureza=natureza_val,
        ordem=next_ordem,
    )
    sync_primary_chamado_fields(registro)
    registro.save(
        update_fields=[
            "chamado_sistema",
            "chamado_codigo",
            "chamado_url",
            "updated_at",
        ]
    )
    return True
