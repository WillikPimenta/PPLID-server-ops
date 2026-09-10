# -*- coding: utf-8 -*-
from app.bots.replicacao_d1.state import update_workflow_state


def test_retry_bem_sucedido_limpa_erro_e_mantem_tentativas():
    estado = {
        "workflows": {
            "WF A": {
                "status": "ERRO",
                "motivo": "erro antigo",
                "motivo_resumo": "erro antigo",
                "motivo_codigo": "TIMEOUT",
                "attempt_number": 1,
            }
        }
    }

    update_workflow_state(
        estado,
        "WF A",
        "PROCESSANDO",
        iniciar_tentativa=True,
        resultado="processando",
        motivo_codigo="",
        motivo_resumo="",
        fase_execucao="abrir_edicao",
    )
    update_workflow_state(
        estado,
        "WF A",
        "SALVO_OK",
        resultado="salvo",
        fase_execucao="confirmacao_salvamento",
    )

    entry = estado["workflows"]["WF A"]
    assert entry["attempt_number"] == 2
    assert entry["motivo"] == entry["motivo_resumo"] == ""
    assert entry["motivo_codigo"] == ""
    assert entry["started_at"]
    assert entry["finished_at"]


def test_metricas_quantidade_distinguem_igual_de_salvo():
    estado = {"workflows": {"WF A": {}, "WF B": {}}}
    update_workflow_state(
        estado,
        "WF A",
        "SEM_ALTERACAO",
        resultado="sem_alteracao",
        motivo_codigo="QTD_JA_CONFIGURADA",
        quantidade_alvo=10,
        quantidade_encontrada=10,
    )
    update_workflow_state(
        estado,
        "WF B",
        "SALVO_OK",
        resultado="salvo",
        quantidade_alvo=20,
        quantidade_encontrada=5,
    )

    assert estado["metricas"] == {
        "workflows_total": 2,
        "por_status": {"SEM_ALTERACAO": 1, "SALVO_OK": 1},
        "quantidade_alvo_total": 30,
        "quantidade_encontrada_total": 15,
        "quantidade_ja_configurada": 1,
        "quantidade_salva": 1,
    }
