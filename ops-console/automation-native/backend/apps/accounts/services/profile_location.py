"""Resolve localização e UF a partir do headcount do colaborador."""

from __future__ import annotations

import unicodedata

# Localidade do AgentHistory.location → UF
_LOCATION_TO_UF: dict[str, str] = {
    "sao carlos": "SP",
    "sao paulo": "SP",
    "campinas": "SP",
    "ribeirao preto": "SP",
    "santos": "SP",
    "brasilia": "DF",
    "brasilia df": "DF",
    "rio de janeiro": "RJ",
    "belo horizonte": "MG",
    "curitiba": "PR",
    "porto alegre": "RS",
    "salvador": "BA",
    "fortaleza": "CE",
    "recife": "PE",
    "manaus": "AM",
    "belem": "PA",
    "goiania": "GO",
}


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", (value or "").strip().lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def resolve_uf_from_location(location: str) -> str:
    key = _normalize(location)
    if not key:
        return ""
    if key in _LOCATION_TO_UF:
        return _LOCATION_TO_UF[key]
    # sufixo ", SP" / "- SP" / "SP"
    for sep in (",", "-", "/"):
        if sep in key:
            tail = key.split(sep)[-1].strip()
            if len(tail) == 2 and tail.isalpha():
                return tail.upper()
    if len(key) == 2 and key.isalpha():
        return key.upper()
    return ""


def resolve_profile_location(user) -> tuple[str, str]:
    """Retorna (location, uf) do histórico ativo do agente vinculado."""
    profile = getattr(user, "profile", None)
    agent = getattr(profile, "agent", None) if profile is not None else None
    if agent is None:
        return "", ""

    history = (
        agent.history.filter(active=True, final_date__isnull=True)
        .order_by("-start_date")
        .only("location")
        .first()
    )
    if history is None:
        history = agent.history.order_by("-start_date").only("location").first()

    location = (getattr(history, "location", None) or "").strip()
    return location, resolve_uf_from_location(location)
