# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

MAX_ANEXOS = 5
MAX_ANEXO_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
ALLOWED_EML_EXTENSIONS = {".eml"}
ALLOWED_ANEXO_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_EML_EXTENSIONS


from apps.suporte_claro.models import SuporteClaroRegistro


def parse_chamado_sistema(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return ""
    valid = {choice[0] for choice in SuporteClaroRegistro.CHAMADO_SISTEMA_CHOICES}
    return value if value in valid else None


def validate_chamado_externo(sistema: str, codigo: str, url: str) -> str | None:
    code = (codigo or "").strip()
    link = (url or "").strip()
    if not sistema:
        if code or link:
            return "Selecione Jira ou ServiceNow para vincular um chamado."
        return None
    if not code and not link:
        return "Informe o código do chamado ou o link direto."
    return None


def parse_status(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return SuporteClaroRegistro.STATUS_ABERTO
    valid = {choice[0] for choice in SuporteClaroRegistro.STATUS_CHOICES}
    return value if value in valid else None


def parse_origem(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    valid = {choice[0] for choice in SuporteClaroRegistro.ORIGEM_CHOICES}
    return value if value in valid else None


def parse_categoria(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return SuporteClaroRegistro.CATEGORIA_DEMANDA
    valid = {choice[0] for choice in SuporteClaroRegistro.CATEGORIA_CHOICES}
    return value if value in valid else None


def parse_tipo_incidente(raw: str) -> str | None:
    """Empty string is valid for demanda; None means invalid value."""
    value = (raw or "").strip()
    if not value:
        return ""
    valid = {choice[0] for choice in SuporteClaroRegistro.TIPO_INCIDENTE_CHOICES}
    return value if value in valid else None


def validate_categoria_tipo(categoria: str, tipo_incidente: str) -> str | None:
    """Tipo de incidente é opcional (legado); novos incidentes podem vir vazios."""
    if categoria == SuporteClaroRegistro.CATEGORIA_INCIDENTE:
        return None
    if (tipo_incidente or "").strip():
        return "tipo_incidente só se aplica a categoria incidente."
    return None


def parse_received_at(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None and "T" in text and len(text) == 16:
        parsed = parse_datetime(f"{text}:00")
    if parsed is None:
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def validate_anexo_upload(uploaded_file) -> str | None:
    """Valida imagem (JPEG/PNG/WebP) ou e-mail (.eml)."""
    if not uploaded_file:
        return "Arquivo vazio."
    name = (uploaded_file.name or "").lower()
    ext = ""
    for candidate in ALLOWED_ANEXO_EXTENSIONS:
        if name.endswith(candidate):
            ext = candidate
            break
    if not ext:
        return (
            f"Formato não permitido: {uploaded_file.name}. "
            "Use JPEG, PNG, WebP ou EML."
        )

    content_type = (getattr(uploaded_file, "content_type", "") or "").lower().strip()
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        if content_type and content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
            return f"Tipo de arquivo não permitido: {content_type}."
    # .eml: MIME varia (Outlook/Chrome); a extensão é a regra.

    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        return "Arquivo vazio."
    if size > MAX_ANEXO_BYTES:
        return f"Arquivo muito grande (máx. {MAX_ANEXO_BYTES // (1024 * 1024)} MB)."
    return None


def validate_image_upload(uploaded_file) -> str | None:
    """Compat: delega para validate_anexo_upload (imagens + .eml)."""
    return validate_anexo_upload(uploaded_file)
