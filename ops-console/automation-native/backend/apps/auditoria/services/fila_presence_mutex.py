from __future__ import annotations

from django.contrib.auth import get_user_model

from apps.auditoria.models import (
    AuditoriaComplianceAuditorPresence,
    ReinspecaoAuditorPresence,
)

FILA_CONTEXTO_REINSPECAO = "reinspecao"
FILA_CONTEXTO_AUDITORIA_COMPLIANCE = "auditoria_compliance"

_CONTEXT_LABELS = {
    FILA_CONTEXTO_REINSPECAO: "Reinspeção",
    FILA_CONTEXTO_AUDITORIA_COMPLIANCE: "Auditoria Compliance",
}


def lock_presence_user(*, user) -> None:
    """Adquire a chave comum antes de consultar ou criar qualquer presença."""
    get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)


def assert_can_go_online(*, user, target_context: str) -> None:
    """Impede Online simultâneo entre reinspeção e auditoria compliance."""
    if target_context not in _CONTEXT_LABELS:
        raise ValueError("Contexto de fila inválido.")

    # As presenças vivem em tabelas diferentes. Bloquear o usuário fornece
    # uma chave comum para serializar as duas transações e elimina a janela
    # entre consultar a fila oposta e persistir o novo status.
    lock_presence_user(user=user)

    if target_context == FILA_CONTEXTO_REINSPECAO:
        conflict = AuditoriaComplianceAuditorPresence.objects.filter(
            user=user,
            status=AuditoriaComplianceAuditorPresence.STATUS_ONLINE,
        ).exists()
    else:
        conflict = ReinspecaoAuditorPresence.objects.filter(
            user=user,
            contexto=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
            status=ReinspecaoAuditorPresence.STATUS_ONLINE,
        ).exists()

    if not conflict:
        return

    other_context = (
        FILA_CONTEXTO_AUDITORIA_COMPLIANCE
        if target_context == FILA_CONTEXTO_REINSPECAO
        else FILA_CONTEXTO_REINSPECAO
    )
    other_label = _CONTEXT_LABELS[other_context]
    target_label = _CONTEXT_LABELS[target_context]
    raise ValueError(
        f"Você já está Online em {other_label}. "
        f"Altere para Offline ou Ausente antes de ficar Online em {target_label}."
    )
