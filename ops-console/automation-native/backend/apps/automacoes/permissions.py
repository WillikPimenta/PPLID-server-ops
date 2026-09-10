from apps.access import registry as R
from apps.access.permission_classes import portal_perm_any
from apps.access.resolve import user_has_any_permission
from rest_framework.permissions import IsAuthenticated


AUTOMACAO_ANY = [
    IsAuthenticated,
    portal_perm_any(
        R.PLANEJAMENTO_AUTOMACAO_VIEW,
        R.PLANEJAMENTO_AUTOMACAO_CONFIGURE,
        R.PLANEJAMENTO_AUTOMACAO_APPROVE,
        R.PLANEJAMENTO_AUTOMACAO_START,
        R.PLANEJAMENTO_AUTOMACAO_STOP,
    ),
]


def can_access_automacoes(user) -> bool:
    return user_has_any_permission(
        user,
        R.PLANEJAMENTO_AUTOMACAO_VIEW,
        R.PLANEJAMENTO_AUTOMACAO_CONFIGURE,
        R.PLANEJAMENTO_AUTOMACAO_APPROVE,
        R.PLANEJAMENTO_AUTOMACAO_START,
        R.PLANEJAMENTO_AUTOMACAO_STOP,
    )


def can_configure_automacoes(user) -> bool:
    return user_has_any_permission(user, R.PLANEJAMENTO_AUTOMACAO_CONFIGURE)


def can_access_planning(user) -> bool:
    return user_has_any_permission(
        user,
        R.PLANEJAMENTO_MONITORAMENTO_VIEW,
        R.PLANEJAMENTO_ESCALAS_VIEW,
        R.PLANEJAMENTO_ESCALAS_IMPORT,
        R.PLANEJAMENTO_OCORRENCIAS_VIEW,
        R.PLANEJAMENTO_OCORRENCIAS_APPROVE,
        R.PLANEJAMENTO_OCORRENCIAS_EDIT,
        R.PLANEJAMENTO_TROCAS_VIEW,
        R.PLANEJAMENTO_TROCAS_APPROVE,
        R.PLANEJAMENTO_AUTOMACAO_VIEW,
        R.PLANEJAMENTO_HEADCOUNT_VIEW,
        R.PLANEJAMENTO_HEADCOUNT_MANAGE,
    )
