# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re

from apps.suporte_claro.models import (
    SuporteClaroEmail,
    SuporteClaroHistorico,
    SuporteClaroRegistro,
)
from apps.suporte_claro.services.audit import log_registro_change

MAX_EMAILS = 20
MAX_ENDERECO_LEN = 254
MAX_COMENTARIO_LEN = 500
EMAIL_SEPARATORS_RE = re.compile(r"[,;\n]+")


def parse_emails_text(text: str) -> list[str]:
    if not text.strip():
        return []
    seen: set[str] = set()
    result: list[str] = []
    for part in EMAIL_SEPARATORS_RE.split(text.strip()):
        endereco = part.strip()
        if not endereco:
            continue
        key = endereco.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(endereco)
    return result


def _normalize_item(raw: dict) -> tuple[str, str] | None:
    if not isinstance(raw, dict):
        return None
    endereco = str(raw.get("endereco") or raw.get("email") or raw.get("sent_by") or "").strip()
    if not endereco:
        return None
    comentario = str(raw.get("comentario") or "").strip()
    return endereco, comentario


def _validate_items(items: list[tuple[str, str]], *, allow_empty: bool = False) -> str | None:
    if not items:
        return None if allow_empty else "Informe ao menos um e-mail."
    if len(items) > MAX_EMAILS:
        return f"Maximo de {MAX_EMAILS} e-mails por demanda."
    seen: set[str] = set()
    for endereco, comentario in items:
        if len(endereco) > MAX_ENDERECO_LEN:
            return f"E-mail muito longo (max {MAX_ENDERECO_LEN} caracteres)."
        key = endereco.casefold()
        if key in seen:
            return f"E-mail duplicado: {endereco}."
        seen.add(key)
        if len(comentario) > MAX_COMENTARIO_LEN:
            return f"Comentario do e-mail {endereco} excede {MAX_COMENTARIO_LEN} caracteres."
    return None


def _parse_emails_json(raw) -> list[tuple[str, str]]:
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("Lista de e-mails invalida.") from exc
        else:
            return [(endereco, "") for endereco in parse_emails_text(text)]
    elif isinstance(raw, list):
        parsed = raw
    else:
        raise ValueError("Lista de e-mails invalida.")

    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in parsed:
        item = _normalize_item(entry)
        if not item:
            continue
        endereco, comentario = item
        key = endereco.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append((endereco, comentario))
    return items


def parse_emails_payload(
    data, *, allow_empty: bool = False
) -> tuple[list[tuple[str, str]], str | None]:
    if "emails" in data:
        raw = data.get("emails")
        try:
            items = _parse_emails_json(raw)
        except ValueError as exc:
            return [], str(exc)
        err = _validate_items(items, allow_empty=allow_empty)
        if err:
            return [], err
        return items, None

    legacy = str(data.get("sent_by") or data.get("sentBy") or "").strip()
    if not legacy:
        return ([], None) if allow_empty else ([], "Informe ao menos um e-mail.")
    items = [(endereco, "") for endereco in parse_emails_text(legacy)]
    if not items:
        items = [(legacy, "")]
    err = _validate_items(items, allow_empty=allow_empty)
    if err:
        return [], err
    return items, None


def _format_emails_summary(items: list[tuple[str, str]]) -> str:
    if not items:
        return "Nenhum"
    parts = []
    for endereco, comentario in items:
        if comentario:
            parts.append(f"{endereco} ({comentario[:80]}{'…' if len(comentario) > 80 else ''})")
        else:
            parts.append(endereco)
    return "; ".join(parts)


def sync_sent_by_field(registro: SuporteClaroRegistro) -> None:
    first = registro.emails.order_by("ordem", "id").first()
    registro.sent_by = (first.endereco if first else "")[:255]


def save_emails(
    registro: SuporteClaroRegistro,
    items: list[tuple[str, str]],
    *,
    user,
) -> None:
    old_items = list(
        registro.emails.order_by("ordem", "id").values_list("endereco", "comentario")
    )
    new_summary = _format_emails_summary(items)
    old_summary = _format_emails_summary(old_items)
    if new_summary != old_summary:
        log_registro_change(
            registro=registro,
            user=user,
            action=SuporteClaroHistorico.ACTION_EDIT,
            field_name="emails",
            old_value=old_summary,
            new_value=new_summary,
        )

    registro.emails.all().delete()
    for ordem, (endereco, comentario) in enumerate(items):
        SuporteClaroEmail.objects.create(
            registro=registro,
            endereco=endereco,
            comentario=comentario,
            ordem=ordem,
        )
    sync_sent_by_field(registro)
    registro.save(update_fields=["sent_by", "updated_at"])


def serialize_email(email: SuporteClaroEmail) -> dict:
    return {
        "id": email.id,
        "endereco": email.endereco,
        "comentario": email.comentario or "",
    }


def serialize_emails(registro: SuporteClaroRegistro) -> list[dict]:
    return [
        serialize_email(item)
        for item in registro.emails.order_by("ordem", "id")
    ]
