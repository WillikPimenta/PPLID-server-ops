from report_falhas.filters import MODULOS_METRICA_OFICIAL

# -*- coding: utf-8 -*-
"""Constantes de grupos e escopo do módulo falhas (fase 5+)."""

GROUP_GLOBAL = "Lideranca_Global"
GROUP_BRASILIA = "Lideranca_Brasilia"
GROUP_SAO_CARLOS = "Lideranca_SaoCarlos"
GROUP_SYNC = "Operacao_Sync"

DEFAULT_GROUPS = [GROUP_GLOBAL, GROUP_BRASILIA, GROUP_SAO_CARLOS, GROUP_SYNC]

GROUP_TO_LOCALIDADE = {
    GROUP_BRASILIA: "Brasília",
    GROUP_SAO_CARLOS: "São Carlos",
}
