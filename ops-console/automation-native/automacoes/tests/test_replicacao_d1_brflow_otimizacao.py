# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from app.bots.replicacao_d1.selenium.brflow_workflows import (
    BrflowWorkflowCallbacks,
    EstadoPersistenciaBatch,
    clicar_pesquisar_replicacao,
    processar_workflows_grupo_fila,
)
from app.bots.replicacao_d1.selenium.listagem import (
    IndiceListagemBrflow,
    _atualizar_entrada_indice,
    indice_cobre_alvos,
    indice_para_resumo,
    invalidar_entrada_indice,
)
from app.bots.replicacao_d1.settings import (
    estado_batch_size,
    indexar_parar_quando_plano_completo,
    listagem_modo_indice,
    refiltrar_apos_salvar,
)


def test_atualizar_entrada_indice_agrega_situacao():
    indice = IndiceListagemBrflow()
    _atualizar_entrada_indice(
        indice,
        {"_wf_key": "wf a", "workflow": "WF A", "situacao": "ATIVO"},
        1,
    )
    _atualizar_entrada_indice(
        indice,
        {"_wf_key": "wf a", "workflow": "WF A", "situacao": "INATIVO"},
        2,
    )
    resumo = indice_para_resumo(indice)
    assert resumo["wf a"]["tem_ativo"] is True
    assert resumo["wf a"]["tem_inativo"] is True
    assert resumo["wf a"]["linhas_ativas"] == 1
    assert indice.entradas["wf a"]["pagina"] == 1


def test_atualizar_entrada_indice_pagina_ativa_na_segunda_pagina():
    indice = IndiceListagemBrflow()
    _atualizar_entrada_indice(
        indice,
        {"_wf_key": "wf z", "workflow": "WF Z", "situacao": "INATIVO"},
        1,
    )
    _atualizar_entrada_indice(
        indice,
        {"_wf_key": "wf z", "workflow": "WF Z", "situacao": "ATIVO"},
        2,
    )
    assert indice.entradas["wf z"]["pagina"] == 2
    assert indice.entradas["wf z"]["tem_ativo"] is True


def test_indice_cobre_alvos():
    indice = IndiceListagemBrflow(
        entradas={
            "a": {"workflow": "A", "tem_ativo": True},
            "b": {"workflow": "B", "tem_ativo": True},
        },
    )
    assert indice_cobre_alvos(indice, {"a", "b"}) is True
    assert indice_cobre_alvos(indice, {"a", "c"}) is False


def test_indice_cobre_alvos_exige_linha_ativa():
    indice = IndiceListagemBrflow(
        entradas={
            "a": {"workflow": "A", "tem_ativo": False, "tem_inativo": True},
        },
    )
    assert indice_cobre_alvos(indice, {"a"}) is False


def test_invalidar_entrada_indice():
    indice = IndiceListagemBrflow(
        entradas={"x": {"workflow": "X"}},
        entradas_regra={"x|regra a": {"workflow": "X", "regra": "Regra A", "pagina": 1}},
    )
    invalidar_entrada_indice(indice, "x")
    assert "x" not in indice.entradas
    assert "x|regra a" not in indice.entradas_regra


def test_settings_otimizacao_defaults():
    assert listagem_modo_indice({}) is True
    assert indexar_parar_quando_plano_completo({}) is True
    assert refiltrar_apos_salvar({}) is False
    assert estado_batch_size({}) == 5


def test_batch_estado_flush_db_only_sem_path():
    run_id = "20260817_test_db"
    estado = {"run_id": run_id, "workflows": {"WF A": {"status": "SALVO_OK"}}}
    plano = SimpleNamespace(estado_execucao_path=None)
    batch = EstadoPersistenciaBatch(estado, None, plano, {"replicacao_estado_batch_size": 5})

    with patch(
        "app.bots.replicacao_aud_d1_planning._DATABASE_ONLY_RUN_IDS",
        {run_id},
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ) as save, patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.sincronizar_consumo_meta_run"
    ):
        batch.flush(force=True, sync_meta=False)
        save.assert_called_once_with(estado, None)


def test_batch_estado_flush(tmp_path):
    estado = {"workflows": {"WF A": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(estado_execucao_path=tmp_path / "estado.json")
    batch = EstadoPersistenciaBatch(estado, plano.estado_execucao_path, plano, {"replicacao_estado_batch_size": 5})

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ) as save, patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.sincronizar_consumo_meta_run"
    ) as sync:
        batch.registrar_mudanca()
        save.assert_not_called()
        batch.flush(force=True, sync_meta=True)
        save.assert_called_once_with(estado, plano.estado_execucao_path)
        sync.assert_called_once()


def test_sem_refilter_modo_indice(tmp_path):
    csv_path = tmp_path / "wf.csv"
    csv_path.write_text("123", encoding="utf-8")
    estado = {"workflows": {"WF A": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=tmp_path / "est.json",
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF A": "WF A BRFlow"},
    )
    callbacks = BrflowWorkflowCallbacks(
        set_status=MagicMock(),
        set_progress=MagicMock(),
        parar_event=MagicMock(is_set=MagicMock(return_value=False)),
        validar_filtros=MagicMock(),
        preencher_filtros=MagicMock(),
        preparar_listagem=MagicMock(),
        refilter_listagem=MagicMock(),
        clicar_editar=MagicMock(),
        configurar_workflow=MagicMock(),
        configurar_workflow_qtd=MagicMock(return_value="SALVO_OK"),
        take_screenshot=MagicMock(),
        csv_protocolos_vazio=MagicMock(return_value=False),
        normalizar_workflow=lambda value: value.lower(),
    )
    resumo = {
        "wf a brflow": {
            "workflow": "WF A BRFlow",
            "tem_ativo": True,
            "tem_inativo": False,
            "linhas_ativas": 1,
            "linhas_inativas": 0,
        }
    }
    indice = IndiceListagemBrflow(
        entradas={
            "wf a brflow": {
                "workflow": "WF A BRFlow",
                "pagina": 1,
                "tem_ativo": True,
                "tem_inativo": False,
                "linhas_ativas": 1,
                "linhas_inativas": 0,
            }
        }
    )

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
        return_value=Path(csv_path),
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.sincronizar_consumo_meta_run"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows._restaurar_listagem_pos_workflow"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF A"],
            "G auditoria",
            estado,
            {"replicacao_apenas_ativos": True, "replicacao_listagem_modo_indice": True},
            callbacks,
            resumo_br=resumo,
            indice_listagem=indice,
        )

    callbacks.refilter_listagem.assert_not_called()
    callbacks.clicar_editar.assert_called_once()
    assert callbacks.clicar_editar.call_args.kwargs.get("indice_listagem") is indice


def test_pesquisar_unico_duas_filas(tmp_path):
    plano = SimpleNamespace(
        workflows=["WF G", "WF 31"],
        workflow_fila={"WF G": "G auditoria", "WF 31": "Documentoscopia 3.1"},
        estado_execucao_path=tmp_path / "est.json",
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF G": "WF G", "WF 31": "WF 31"},
    )
    estado = {"workflows": {}}
    callbacks = BrflowWorkflowCallbacks(
        set_status=MagicMock(),
        set_progress=MagicMock(),
        parar_event=MagicMock(is_set=MagicMock(return_value=False)),
        validar_filtros=MagicMock(),
        preencher_filtros=MagicMock(),
        preparar_listagem=MagicMock(),
        refilter_listagem=MagicMock(),
        clicar_editar=MagicMock(),
        configurar_workflow=MagicMock(),
        configurar_workflow_qtd=MagicMock(return_value="SALVO_OK"),
        take_screenshot=MagicMock(),
        csv_protocolos_vazio=MagicMock(return_value=True),
        normalizar_workflow=lambda v: v.lower(),
    )

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.filtrar_workflow_destino_habilitado",
        return_value=False,
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.pesquisar_e_preparar_listagem_replicacao"
    ) as prep, patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows._construir_listagem_resumo",
        return_value=({}, [], IndiceListagemBrflow()),
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.processar_workflows_grupo_fila"
    ) as proc, patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.sincronizar_consumo_meta_run"
    ):
        clicar_pesquisar_replicacao(MagicMock(), plano, estado, {}, callbacks)

    prep.assert_called_once()
    callbacks.preencher_filtros.assert_called_once()
    assert proc.call_count == 2


def test_navegar_por_indice_sem_revarrer():
    from app.bots.replicacao_d1.selenium.listagem import clicar_editar_por_workflow

    driver = MagicMock()
    indice = IndiceListagemBrflow(
        entradas={
            "wf alvo": {
                "workflow": "WF Alvo",
                "pagina": 2,
                "tem_ativo": True,
                "tem_inativo": False,
                "linhas_ativas": 1,
                "linhas_inativas": 0,
            }
        }
    )

    with patch(
        "app.bots.replicacao_d1.selenium.listagem.WebDriverWait"
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_info_paginador",
        return_value={"pagina_atual": 1, "total_paginas": 2},
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.ir_pagina_replicacao"
    ) as ir_pagina, patch(
        "app.bots.replicacao_d1.selenium.listagem._editar_workflow_na_pagina_atual",
        return_value=True,
    ) as editar_pagina:
        clicar_editar_por_workflow(
            driver,
            "WF Alvo",
            apenas_ativo=True,
            indice_listagem=indice,
        )

    ir_pagina.assert_called_once_with(driver, 2, settings=None)
    editar_pagina.assert_called_once()
