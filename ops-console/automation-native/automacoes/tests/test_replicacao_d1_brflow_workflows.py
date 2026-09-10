# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.bots.replicacao_d1.selenium.listagem import IndiceListagemBrflow
from app.bots.replicacao_d1.selenium.brflow_workflows import (
    BrflowWorkflowCallbacks,
    STATUS_WORKFLOW_SKIP_RETOMADA,
    _modo_replicacao_workflow,
    processar_workflows_grupo_fila,
)
from app.bots.replicacao_d1.selenium.workflow_upload import SaveNotConfirmedError


def test_status_skip_retomada_inclui_salvo_ok():
    assert "SALVO_OK" in STATUS_WORKFLOW_SKIP_RETOMADA
    assert "SEM_ALTERACAO" in STATUS_WORKFLOW_SKIP_RETOMADA
    assert "UPLOAD_OK" in STATUS_WORKFLOW_SKIP_RETOMADA
    assert "INATIVO" in STATUS_WORKFLOW_SKIP_RETOMADA
    assert "ERRO" not in STATUS_WORKFLOW_SKIP_RETOMADA


def test_modo_explicito_vence_fila_no_selenium():
    plano = SimpleNamespace(
        workflow_modo_replicacao={
            "WF G": "qtd",
            "WF Bio": "protocolos",
        }
    )

    assert _modo_replicacao_workflow(plano, "WF G", "G auditoria") == "qtd"
    assert _modo_replicacao_workflow(plano, "WF Bio", "Bio") == "protocolos"


def test_modo_banco_persiste_salvo_ok_sem_caminho_de_estado(tmp_path):
    csv_path = tmp_path / "wf.csv"
    csv_path.write_text("123", encoding="utf-8")
    estado = {
        "run_id": "run_db",
        "workflows": {
            "WF A": {
                "status": "PENDENTE",
                "protocolos": 1,
                "protocolos_salvos": 0,
            }
        },
    }
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF A": "WF A"},
        workflow_modo_replicacao={"WF A": "protocolos"},
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
        normalizar_workflow=lambda value: value,
    )

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
        return_value=Path(csv_path),
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ) as save_state, patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.sincronizar_consumo_meta_run"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF A"],
            "Bio",
            estado,
            {
                "replicacao_apenas_ativos": False,
                "apenas_pendentes": True,
                "replicacao_estado_batch_size": 1,
            },
            callbacks,
        )

    assert estado["workflows"]["WF A"]["status"] == "SALVO_OK"
    callbacks.configurar_workflow.assert_called_once()
    callbacks.configurar_workflow_qtd.assert_not_called()
    save_state.assert_not_called()


def test_ausente_na_listagem_pula_workflow(tmp_path):
    csv_path = tmp_path / "wf.csv"
    csv_path.write_text("123", encoding="utf-8")
    estado = {"workflows": {"WF A": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
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
        normalizar_workflow=lambda value: value,
    )

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
        return_value=Path(csv_path),
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows._construir_listagem_resumo",
        return_value=({}, [], None),
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF A"],
            "G auditoria",
            estado,
            {"replicacao_apenas_ativos": True},
            callbacks,
        )

    assert estado["workflows"]["WF A"]["status"] == "PULADO"
    callbacks.configurar_workflow.assert_not_called()
    callbacks.set_progress.assert_called_once_with(0, "Workflow 1/1: WF A")


def test_modo_qtd_g_usa_configurar_workflow_qtd_sem_csv(tmp_path):
    estado = {"workflows": {"WF G": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF G": "WF G BRFlow"},
        workflow_modo_replicacao={"WF G": "qtd"},
        qtd_por_workflow={"WF G": 42},
        workflow_regra_brflow={},
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
        configurar_workflow_qtd=MagicMock(return_value="SEM_ALTERACAO"),
        take_screenshot=MagicMock(),
        csv_protocolos_vazio=MagicMock(return_value=False),
        normalizar_workflow=lambda value: value.lower(),
    )

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
    ) as resolve_csv, patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows._construir_listagem_resumo",
        return_value=({"wf g brflow": {"linhas_ativas": 1}}, [], None),
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF G"],
            "G auditoria",
            estado,
            {"replicacao_apenas_ativos": False},
            callbacks,
        )

    resolve_csv.assert_not_called()
    callbacks.configurar_workflow.assert_not_called()
    callbacks.configurar_workflow_qtd.assert_called_once()
    assert callbacks.configurar_workflow_qtd.call_args[0][2] == 42
    assert estado["workflows"]["WF G"]["status"] == "SEM_ALTERACAO"
    assert estado["workflows"]["WF G"]["modo"] == "qtd"
    assert estado["workflows"]["WF G"]["qtd_calculada"] == 42


def test_modo_qtd_redoc_nao_pula_regra_quando_indice_tem_linhas(tmp_path):
    from app.bots.replicacao_d1.selenium.listagem import IndiceListagemBrflow, normalizar_regra_brflow

    estado = {"workflows": {"WF Redoc": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF Redoc": "WF Redoc BRFlow"},
        qtd_por_workflow={"WF Redoc": 100},
        workflow_regra_brflow={"WF Redoc": "Regra A"},
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
    regra_key = normalizar_regra_brflow("Regra A")
    indice = IndiceListagemBrflow(
        entradas={
            "wf redoc brflow": {
                "workflow": "WF Redoc BRFlow",
                "pagina": 1,
                "tem_ativo": True,
                "tem_inativo": False,
                "linhas_ativas": 1,
                "linhas_inativas": 0,
                "regras_ativas": {regra_key: 1},
            }
        }
    )
    resumo = {
        "wf redoc brflow": {
            "workflow": "WF Redoc BRFlow",
            "tem_ativo": True,
            "tem_inativo": False,
            "linhas_ativas": 1,
            "linhas_inativas": 0,
            "regras_ativas": {regra_key: 1},
        }
    }

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF Redoc"],
            "Redoc",
            estado,
            {"replicacao_apenas_ativos": True, "replicacao_listagem_modo_indice": True},
            callbacks,
            resumo_br=resumo,
            linhas_br=[],
            indice_listagem=indice,
        )

    callbacks.configurar_workflow_qtd.assert_called_once()
    assert estado["workflows"]["WF Redoc"]["status"] == "SALVO_OK"


def test_g_auditoria_com_regra_passa_nome_regra_para_clicar_editar(tmp_path):
    from app.bots.replicacao_d1.selenium.listagem import normalizar_regra_brflow

    estado = {"workflows": {"WF G": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF G": "WF G BRFlow"},
        workflow_modo_replicacao={"WF G": "qtd"},
        qtd_por_workflow={"WF G": 10},
        workflow_regra_brflow={"WF G": "Regra G"},
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
    regra_key = normalizar_regra_brflow("Regra G")
    resumo = {
        "wf g brflow": {
            "workflow": "WF G BRFlow",
            "tem_ativo": True,
            "tem_inativo": False,
            "linhas_ativas": 2,
            "linhas_inativas": 0,
            "regras_ativas": {regra_key: 1},
        }
    }
    linhas = [
        {
            "_wf_key": "wf g brflow",
            "workflow": "WF G BRFlow",
            "situacao": "ATIVO",
            "regra": "Regra G",
            "_regra_key": regra_key,
        },
        {
            "_wf_key": "wf g brflow",
            "workflow": "WF G BRFlow",
            "situacao": "ATIVO",
            "regra": "Regra H",
            "_regra_key": normalizar_regra_brflow("Regra H"),
        },
    ]

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF G"],
            "G auditoria",
            estado,
            {"replicacao_apenas_ativos": True, "replicacao_listagem_modo_indice": False},
            callbacks,
            resumo_br=resumo,
            linhas_br=linhas,
        )

    callbacks.clicar_editar.assert_called_once()
    assert callbacks.clicar_editar.call_args.kwargs["nome_regra"] == "Regra G"
    assert estado["workflows"]["WF G"]["status"] == "SALVO_OK"


def test_g_auditoria_multiplas_linhas_sem_regra_edita_primeira(tmp_path, caplog):
    import logging

    estado = {"workflows": {"WF G": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF G": "WF G BRFlow"},
        workflow_modo_replicacao={"WF G": "qtd"},
        qtd_por_workflow={"WF G": 10},
        workflow_regra_brflow={},
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
        "wf g brflow": {
            "workflow": "WF G BRFlow",
            "tem_ativo": True,
            "tem_inativo": False,
            "linhas_ativas": 2,
            "linhas_inativas": 0,
        }
    }

    with caplog.at_level(logging.WARNING), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF G"],
            "G auditoria",
            estado,
            {"replicacao_apenas_ativos": True},
            callbacks,
            resumo_br=resumo,
            linhas_br=[],
        )

    assert any("editando a primeira" in record.message for record in caplog.records)
    assert callbacks.clicar_editar.call_args.kwargs.get("nome_regra") is None


def test_sequencia_workflows_em_paginas_diferentes(tmp_path):
    estado = {"workflows": {"WF Alpha": {"status": "PENDENTE"}, "WF Zulu": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF Alpha": "WF Alpha", "WF Zulu": "WF Zulu"},
        workflow_modo_replicacao={"WF Alpha": "qtd", "WF Zulu": "qtd"},
        qtd_por_workflow={"WF Alpha": 10, "WF Zulu": 20},
        workflow_regra_brflow={},
    )
    indice = IndiceListagemBrflow(
        entradas={
            "wf alpha": {
                "workflow": "WF Alpha",
                "pagina": 1,
                "tem_ativo": True,
                "tem_inativo": False,
                "linhas_ativas": 1,
                "linhas_inativas": 0,
            },
            "wf zulu": {
                "workflow": "WF Zulu",
                "pagina": 2,
                "tem_ativo": True,
                "tem_inativo": False,
                "linhas_ativas": 1,
                "linhas_inativas": 0,
            },
        }
    )
    resumo = {
        "wf alpha": indice.entradas["wf alpha"],
        "wf zulu": indice.entradas["wf zulu"],
    }
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

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ), patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF Alpha", "WF Zulu"],
            "G auditoria",
            estado,
            {"replicacao_apenas_ativos": True, "replicacao_listagem_modo_indice": True},
            callbacks,
            resumo_br=resumo,
            linhas_br=[],
            indice_listagem=indice,
        )

    assert callbacks.clicar_editar.call_count == 2
    assert callbacks.clicar_editar.call_args_list[0].kwargs["indice_listagem"] is indice
    assert callbacks.clicar_editar.call_args_list[1].kwargs["indice_listagem"] is indice
    assert estado["workflows"]["WF Alpha"]["status"] == "SALVO_OK"
    assert estado["workflows"]["WF Zulu"]["status"] == "SALVO_OK"


def test_nao_salvo_no_primeiro_workflow_restaura_paginacao_para_segundo(tmp_path):
    estado = {"workflows": {"WF Alpha": {"status": "PENDENTE"}, "WF Zulu": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        csv_paths={},
        warnings=[],
        workflow_brflow={"WF Alpha": "WF Alpha", "WF Zulu": "WF Zulu"},
        workflow_modo_replicacao={"WF Alpha": "qtd", "WF Zulu": "qtd"},
        qtd_por_workflow={"WF Alpha": 10, "WF Zulu": 20},
        workflow_regra_brflow={},
    )
    indice = IndiceListagemBrflow(
        entradas={
            "wf alpha": {
                "workflow": "WF Alpha",
                "pagina": 1,
                "tem_ativo": True,
                "tem_inativo": False,
                "linhas_ativas": 1,
                "linhas_inativas": 0,
            },
            "wf zulu": {
                "workflow": "WF Zulu",
                "pagina": 2,
                "tem_ativo": True,
                "tem_inativo": False,
                "linhas_ativas": 1,
                "linhas_inativas": 0,
            },
        }
    )
    resumo = {
        "wf alpha": indice.entradas["wf alpha"],
        "wf zulu": indice.entradas["wf zulu"],
    }
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
        configurar_workflow_qtd=MagicMock(
            side_effect=[SaveNotConfirmedError("painel permaneceu aberto"), "SALVO_OK"]
        ),
        take_screenshot=MagicMock(),
        csv_protocolos_vazio=MagicMock(return_value=False),
        normalizar_workflow=lambda value: value.lower(),
    )

    with patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.resolve_csv_upload_workflow",
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"
    ), patch(
        "app.bots.replicacao_d1.selenium.brflow_workflows.garantir_paginacao_listagem_replicacao"
    ) as garantir, patch("app.bots.replicacao_d1.selenium.brflow_workflows.time.sleep"):
        processar_workflows_grupo_fila(
            MagicMock(),
            plano,
            ["WF Alpha", "WF Zulu"],
            "G auditoria",
            estado,
            {"replicacao_apenas_ativos": True, "replicacao_listagem_modo_indice": True},
            callbacks,
            resumo_br=resumo,
            linhas_br=[],
            indice_listagem=indice,
        )

    assert estado["workflows"]["WF Alpha"]["status"] == "NAO_SALVO"
    assert estado["workflows"]["WF Alpha"]["resultado"] == "nao_salvo"
    assert estado["workflows"]["WF Alpha"]["motivo_codigo"] == "SALVAMENTO_NAO_CONFIRMADO"
    assert estado["workflows"]["WF Alpha"]["attempt_number"] == 1
    assert estado["workflows"]["WF Zulu"]["status"] == "SALVO_OK"
    assert garantir.call_count >= 1


def test_parada_marca_workflow_pendente_como_cancelado(tmp_path):
    estado = {"workflows": {"WF A": {"status": "PENDENTE"}}}
    plano = SimpleNamespace(
        estado_execucao_path=None,
        pasta_protocolos=tmp_path,
        workflow_brflow={"WF A": "WF A"},
    )
    callbacks = BrflowWorkflowCallbacks(
        set_status=MagicMock(),
        set_progress=MagicMock(),
        parar_event=MagicMock(is_set=MagicMock(return_value=True)),
        validar_filtros=MagicMock(),
        preencher_filtros=MagicMock(),
        preparar_listagem=MagicMock(),
        refilter_listagem=MagicMock(),
        clicar_editar=MagicMock(),
        configurar_workflow=MagicMock(),
        configurar_workflow_qtd=MagicMock(),
        take_screenshot=MagicMock(),
        csv_protocolos_vazio=MagicMock(),
        normalizar_workflow=lambda value: value,
    )

    with patch("app.bots.replicacao_d1.selenium.brflow_workflows.salvar_estado_execucao"):
        processar_workflows_grupo_fila(
            MagicMock(), plano, ["WF A"], "G auditoria", estado,
            {"replicacao_apenas_ativos": False}, callbacks,
        )

    entry = estado["workflows"]["WF A"]
    assert entry["status"] == "CANCELADO"
    assert entry["resultado"] == "cancelado"
    assert entry["motivo_codigo"] == "EXECUCAO_CANCELADA"
