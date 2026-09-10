# -*- coding: utf-8 -*-
from report_falhas.io.data_loader import norm_matricula

from apps.falhas_criticas.models import FalhasAgent


def resolve_user_display_name(user) -> str:
    if not user or not user.is_authenticated:
        return ""
    try:
        mat = norm_matricula(user.username)
        if mat:
            agent = FalhasAgent.objects.filter(pk=mat).first()
            if agent and agent.name.strip():
                return agent.name.strip()
    except Exception:
        pass
    fn = (user.first_name or "").strip()
    ln = (user.last_name or "").strip()
    full = f"{fn} {ln}".strip()
    if full:
        return full
    return user.username
