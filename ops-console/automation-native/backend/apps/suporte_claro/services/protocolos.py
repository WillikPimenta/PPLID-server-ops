# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re

from apps.suporte_claro.models import (
    SuporteClaroHistorico,
    SuporteClaroProtocolo,
    SuporteClaroRegistro,
)
from apps.suporte_claro.services.audit import log_registro_change

MAX_PROTOCOLOS = 150
MAX_NUMERO_LEN = 64
MAX_COMENTARIO_LEN = 2000
# Campo legado `registro.protocolo` — lista completa fica em SuporteClaroProtocolo.
LEGACY_PROTOCOLO_MAX_LEN = 128
SEPARADORES_RE = re.compile(r"[,.;:|\s]+")


def parse_protocolos_text(text: str) -> list[str]:
    """Separa numeros de protocolo por virgula, ponto, espaco etc."""
    if not text:
        return []
    parts = SEPARADORES_RE.split(text.strip())
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        numero = part.strip()
        if not numero or numero in seen:
            continue
        seen.add(numero)
        result.append(numero)
    return result


def _normalize_item(raw: dict) -> tuple[str, str] | None:
    if not isinstance(raw, dict):
        return None
    numero = str(raw.get("numero") or raw.get("protocolo") or "").strip()
    if not numero:
        return None
    comentario = str(raw.get("comentario") or "").strip()
    return numero, comentario


def _validate_items(items: list[tuple[str, str]], *, allow_empty: bool = False) -> str | None:
    if not items:
        return None if allow_empty else "Informe ao menos um protocolo."
    if len(items) > MAX_PROTOCOLOS:
        return f"Maximo de {MAX_PROTOCOLOS} protocolos por demanda."
    seen: set[str] = set()
    for numero, comentario in items:
        if len(numero) > MAX_NUMERO_LEN:
            return f"Protocolo muito longo (max {MAX_NUMERO_LEN} caracteres): {numero[:20]}..."
        if numero in seen:
            return f"Protocolo duplicado: {numero}."
        seen.add(numero)
        if len(comentario) > MAX_COMENTARIO_LEN:
            return f"Comentario do protocolo {numero} excede {MAX_COMENTARIO_LEN} caracteres."
    return None


def _parse_protocolos_json(raw) -> list[tuple[str, str]]:
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("Lista de protocolos invalida.") from exc
        else:
            return [(numero, "") for numero in parse_protocolos_text(text)]
    elif isinstance(raw, list):
        parsed = raw
    else:
        raise ValueError("Lista de protocolos invalida.")

    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in parsed:
        item = _normalize_item(entry)
        if not item:
            continue
        numero, comentario = item
        if numero in seen:
            continue
        seen.add(numero)
        items.append((numero, comentario))
    return items


def parse_protocolos_payload(
    data, *, allow_empty: bool = False
) -> tuple[list[tuple[str, str]], str | None]:
    """Aceita `protocolos` (JSON/lista) ou campo legado `protocolo` (string)."""
    if "protocolos" in data:
        raw = data.get("protocolos")
        try:
            items = _parse_protocolos_json(raw)
        except ValueError as exc:
            return [], str(exc)
        err = _validate_items(items, allow_empty=allow_empty)
        if err:
            return [], err
        return items, None

    legacy = str(data.get("protocolo") or "").strip()
    if not legacy:
        return ([], None) if allow_empty else ([], "Informe ao menos um protocolo.")
    items = [(numero, "") for numero in parse_protocolos_text(legacy)]
    err = _validate_items(items, allow_empty=allow_empty)
    if err:
        return [], err
    return items, None


def format_protocolo_legacy(items: list[tuple[str, str]]) -> str:
    """Valor do campo legado `protocolo`: apenas o 1º número (limite 128 chars)."""
    for numero, _comentario in items:
        if numero:
            return numero[:LEGACY_PROTOCOLO_MAX_LEN]
    return ""


def _format_protocolos_summary(items: list[tuple[str, str]]) -> str:
    if not items:
        return "Nenhum"
    parts = []
    for numero, comentario in items:
        if comentario:
            parts.append(f"{numero} ({comentario[:80]}{'…' if len(comentario) > 80 else ''})")
        else:
            parts.append(numero)
    return "; ".join(parts)


def sync_primary_protocolo_field(registro: SuporteClaroRegistro) -> None:
    items = list(
        registro.protocolos.order_by("ordem", "id").values_list("numero", "comentario")
    )
    registro.protocolo = format_protocolo_legacy(items)


def save_protocolos(
    registro: SuporteClaroRegistro,
    items: list[tuple[str, str]],
    *,
    user,
) -> None:
    old_items = list(
        registro.protocolos.order_by("ordem", "id").values_list("numero", "comentario")
    )
    new_summary = _format_protocolos_summary(items)
    old_summary = _format_protocolos_summary(old_items)
    if new_summary != old_summary:
        log_registro_change(
            registro=registro,
            user=user,
            action=SuporteClaroHistorico.ACTION_EDIT,
            field_name="protocolos",
            old_value=old_summary,
            new_value=new_summary,
        )

    registro.protocolos.all().delete()
    for ordem, (numero, comentario) in enumerate(items):
        SuporteClaroProtocolo.objects.create(
            registro=registro,
            numero=numero,
            comentario=comentario,
            ordem=ordem,
        )
    sync_primary_protocolo_field(registro)
    registro.save(update_fields=["protocolo", "updated_at"])


def serialize_protocolo(protocolo: SuporteClaroProtocolo) -> dict:
    return {
        "id": protocolo.id,
        "numero": protocolo.numero,
        "comentario": protocolo.comentario or "",
    }


def serialize_protocolos(registro: SuporteClaroRegistro) -> list[dict]:
    return [
        serialize_protocolo(protocolo)
        for protocolo in registro.protocolos.order_by("ordem", "id")
    ]


def display_titulo(registro: SuporteClaroRegistro) -> str:
    titulo = (registro.titulo or "").strip()
    if titulo:
        return titulo
    first = registro.protocolos.order_by("ordem", "id").first()
    if first:
        return first.numero
    return registro.protocolo or ""
