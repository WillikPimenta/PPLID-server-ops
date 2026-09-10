# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import re

from django.conf import settings

_CPF_DIGITS_RE = re.compile(r"\D")


def normalize_cpf(value: str) -> str:
    """Remove formatação e retorna apenas dígitos (mesma lógica do bot rotina)."""
    if not value:
        return ""
    return _CPF_DIGITS_RE.sub("", str(value).strip())


def hash_cpf(value: str) -> str:
    """HMAC-SHA256 do CPF normalizado usando SECRET_KEY. Vazio/inválido → ''."""
    normalized = normalize_cpf(value)
    if len(normalized) < 11:
        return ""
    key = settings.SECRET_KEY.encode("utf-8")
    digest = hmac.new(key, normalized.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()
