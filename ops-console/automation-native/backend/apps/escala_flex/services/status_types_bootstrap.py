"""Cadastro idempotente dos tipos de status de referência."""

from apps.escala_flex.models import StatusType

DEFAULT_STATUS_TYPES = [
    (1, "Disponível", "#109010", True),
    (2, "Ausente", "#f9536d", False),
    (3, "Deslogado", "#bdb2b0", False),
    (4, "Intervalo / Almoço", "#f9536d", True),
    (5, "Ginástica Laboral", "#f68e68", True),
    (6, "Feedback", "#0566b2", True),
    (7, "Treinamento", "#0566b2", True),
    (8, "Reunião", "#0566b2", True),
    (9, "Promoção da Saúde", "#4fdbdb", True),
    (10, "Pessoal", "#ffbf00", True),
    (11, "Problemas sistêmicos", "#a86eeb", True),
    (13, "Elevate / Portais", "#0566b2", True),
]


def ensure_status_types() -> int:
    """Garante os tipos de status padrão. Retorna a contagem após o seed."""
    if StatusType.objects.exists():
        return StatusType.objects.count()

    for sid, name, color, logged_in in DEFAULT_STATUS_TYPES:
        StatusType.objects.update_or_create(
            pk=sid,
            defaults={
                "name": name,
                "color": color,
                "active": True,
                "logged_in": logged_in,
            },
        )
    return StatusType.objects.count()
