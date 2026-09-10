# -*- coding: utf-8 -*-
"""Testes unitários da ponte D-1 bot ↔ PostgreSQL."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots import replicacao_d1_db_bridge as bridge
from app.bots.replicacao_d1_db_bridge import (
    fallback_parquet_dias_ausentes_ativo,
    is_fonte_banco_ativa,
    load_planning_dataframes,
    load_source_dataframe_hybrid_db,
    settings_for_d1_env_json,
)


def _normalizado(status: str, motivo: str = "") -> dict:
    status = status.upper()
    if status == "SEM_ALTERACAO":
        return {
            "resultado": "sem_alteracao",
            "severidade": "aviso",
            "status_operacional": "recebido",
            "falha_bloqueante": False,
        }
    if status == "NAO_SALVO":
        return {
            "resultado": "nao_salvo",
            "severidade": "erro",
            "status_operacional": "falhou",
            "falha_bloqueante": True,
        }
    return {
        "resultado": "processando",
        "severidade": "info",
        "status_operacional": "pendente",
        "falha_bloqueante": False,
    }


@pytest.fixture(autouse=True)
def _reset_bridge_django_state():
    bridge._DJANGO_READY = False
    bridge._BACKEND_DIR = None
    yield
    bridge._DJANGO_READY = False
    bridge._BACKEND_DIR = None


def test_is_fonte_banco_ativa_prefers_settings_flag():
    assert is_fonte_banco_ativa({"fonte_banco_ativa": True}) is True
    assert is_fonte_banco_ativa({"fonte_banco_ativa": False}) is False


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=False)
def test_is_fonte_banco_ativa_without_django_returns_false(_mock_django):
    assert is_fonte_banco_ativa({}) is False


def test_settings_for_d1_env_json_strips_dataframes():
    settings = {
        "fonte_banco_ativa": True,
        "_execution_snapshot": {"config_version": 1},
        "_mapa_workflow_d1_df": pd.DataFrame({"Workflow": ["A"]}),
        "_escala_df": pd.DataFrame({"data": ["2026-08-05"]}),
        "run_id": "test",
    }
    out = settings_for_d1_env_json(settings)
    assert "run_id" in out
    assert "_execution_snapshot" not in out
    assert "_mapa_workflow_d1_df" not in out
    assert "_escala_df" not in out
    assert out.get("fonte_banco_ativa") is True


def test_settings_for_d1_env_json_preserves_agendamento():
    settings = {
        "fonte_banco_ativa": True,
        "agendamento_ativo": True,
        "agendamento_hora_planejamento": "15:00",
        "agendamento_hora_execucao": "20:00",
    }
    out = settings_for_d1_env_json(settings)
    assert out.get("agendamento_ativo") is True
    assert out.get("agendamento_hora_planejamento") == "15:00"
    assert out.get("agendamento_hora_execucao") == "20:00"


@pytest.mark.parametrize(
    ("status", "motivo_codigo", "resultado", "erro_codigo", "erro_resumo"),
    [
        ("SEM_ALTERACAO", "QTD_JA_CONFIGURADA", "sem_alteracao", "", ""),
        (
            "NAO_SALVO",
            "SALVAMENTO_NAO_CONFIRMADO",
            "nao_salvo",
            "SALVAMENTO_NAO_CONFIRMADO",
            "painel permaneceu aberto",
        ),
    ],
)
@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_save_execution_state_db_persiste_contrato_estruturado(
    _mock_django,
    status,
    motivo_codigo,
    resultado,
    erro_codigo,
    erro_resumo,
):
    run = MagicMock(plan_hash="hash")
    run_model = MagicMock()
    run_model.objects.filter.return_value.first.return_value = run
    workflow_qs = MagicMock()
    workflow_model = MagicMock()
    workflow_model.objects.filter.return_value = workflow_qs
    models_module = MagicMock(
        ReplicacaoD1Run=run_model,
        ReplicacaoD1WorkflowDia=workflow_model,
    )
    normalization_module = MagicMock()
    normalization_module.normalizar_resultado_workflow.side_effect = _normalizado
    estado = {
        "run_id": "run_contract",
        "workflows": {
            "WF A": {
                "status": status,
                "resultado": resultado,
                "motivo_codigo": motivo_codigo,
                "motivo_resumo": (
                    "painel permaneceu aberto"
                    if status == "NAO_SALVO"
                    else "Quantidade já configurada"
                ),
                "fase_execucao": "confirmacao_salvamento",
                "quantidade_alvo": 25,
                "quantidade_encontrada": 25,
                "attempt_number": 2,
                "started_at": "2026-08-25T10:00:00+00:00",
                "finished_at": "2026-08-25T10:01:00+00:00",
                "atualizado_em": "2026-08-25T10:01:00+00:00",
            }
        },
    }

    with patch("django.db.transaction.atomic"), patch.dict(
        sys.modules,
        {
            "apps.replicacao_d1.models": models_module,
            "apps.replicacao_d1.normalization": normalization_module,
        },
    ):
        assert bridge.save_execution_state_db(estado) is True

    defaults = workflow_qs.update.call_args.kwargs
    assert defaults["resultado"] == resultado
    assert defaults["motivo_codigo"] == motivo_codigo
    assert defaults["fase_execucao"] == "confirmacao_salvamento"
    assert defaults["quantidade_alvo"] == 25
    assert defaults["quantidade_encontrada"] == 25
    assert defaults["attempt_number"] == 2
    assert defaults["protocolos_enviados"] == 0
    assert defaults["erro_codigo"] == erro_codigo
    assert defaults["erro_resumo"] == erro_resumo


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_load_execution_state_db_reconstitui_contrato_para_retomada(_mock_django):
    row = MagicMock(
        workflow_config="WF A",
        workflow_brflow="WF A BRFlow",
        fila="Bio",
        modo_replicacao="qtd",
        status_brflow="SEM_ALTERACAO",
        protocolos_planejados=25,
        protocolos_aceitos=0,
        erro_resumo="",
        resultado="sem_alteracao",
        severidade="aviso",
        motivo_codigo="QTD_JA_CONFIGURADA",
        motivo_resumo="Quantidade já configurada",
        fase_execucao="comparacao_quantidade",
        quantidade_alvo=25,
        quantidade_encontrada=25,
        attempt_number=1,
        started_at=datetime(2026, 8, 25, 10, 0),
        finished_at=datetime(2026, 8, 25, 10, 1),
    )
    run = MagicMock(
        run_id="run_contract",
        plan_hash="hash",
        data_referencia_d1=datetime(2026, 8, 24).date(),
        data_execucao=datetime(2026, 8, 25, 10, 0),
        source_batch_id=10,
        plan_revision=1,
        validation_status="approved",
        auditores_ativos_brflow=2,
        auditores_ativos_case=1,
    )
    run.workflows.order_by.return_value = [row]
    run_model = MagicMock()
    run_model.objects.filter.return_value.first.return_value = run

    with patch.dict(
        sys.modules,
        {"apps.replicacao_d1.models": MagicMock(ReplicacaoD1Run=run_model)},
    ):
        estado = bridge.load_execution_state_db("run_contract")

    entry = estado["workflows"]["WF A"]
    assert entry["resultado"] == "sem_alteracao"
    assert entry["motivo_codigo"] == "QTD_JA_CONFIGURADA"
    assert entry["motivo_resumo"] == "Quantidade já configurada"
    assert entry["fase_execucao"] == "comparacao_quantidade"
    assert entry["quantidade_alvo"] == entry["quantidade_encontrada"] == 25
    assert entry["attempt_number"] == 1



def test_resolve_agendamento_settings_from_persistent_snapshot():
    out = bridge.resolve_agendamento_settings(
        {
            "_execution_snapshot": {
                "persistent": {
                    "agendamento_ativo": True,
                    "agendamento_hora_planejamento": "07:30",
                    "agendamento_hora_execucao": "13:00",
                }
            }
        }
    )
    assert out["agendamento_ativo"] is True
    assert out["agendamento_hora_planejamento"] == "07:30"
    assert out["agendamento_hora_execucao"] == "13:00"


def test_planning_calculadora_derives_meta_from_canonical_field():
    params = bridge._planning_calculadora_params(
        {
            "meta_produ_diaria": "425.5",
            "calculadora_params": {"confianca": 0.99},
        }
    )

    assert params["meta_produ"] == 425.5
    assert params["confianca"] == 0.99
    assert "dias_uteis" not in params


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=False)
def test_load_planning_dataframes_raises_when_django_unavailable(_mock_django):
    with pytest.raises(RuntimeError, match="Django/PostgreSQL indisponível"):
        load_planning_dataframes({"fonte_banco_ativa": True})


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_load_planning_dataframes_freezes_snapshot(mock_django):
    snap = MagicMock()
    snap.config_version = 3
    snap.config_hash = "abc123"
    snap.persistent = {"calculadora_params": {"meta_produ": 300.0}, "workflows": []}
    snap.consumo_mensal = {"wf_a": 10}
    snap.competencia_resolvida = "2026-08"
    snap.to_dict.return_value = {
        "config_version": 3,
        "config_hash": "abc123",
        "persistent": snap.persistent,
        "consumo_mensal": snap.consumo_mensal,
        "competencia_resolvida": "2026-08",
    }
    frames = {
        "mapa_workflow_d1": pd.DataFrame({"Workflow": ["WF"], "_wf_key": ["wf"]}),
        "categoria_clientes": pd.DataFrame({"Cliente": ["C"], "_cli_key": ["c"]}),
        "escala_auditores": pd.DataFrame({"data": ["2026-08-05"], "auditores_ativos": [5]}),
    }

    mock_adapter = MagicMock()
    mock_adapter.load_snapshot_for_planning.return_value = snap
    mock_adapter.snapshot_to_dataframes.return_value = frames

    mock_dto = MagicMock()
    mock_dto.RunOptions = MagicMock(side_effect=lambda **kw: MagicMock(**kw))

    mock_exc = MagicMock()
    mock_exc.ConfigBancoIndisponivelError = type("ConfigBancoIndisponivelError", (Exception,), {})
    mock_exc.ConfigIncompletaError = type("ConfigIncompletaError", (Exception,), {})

    settings: dict = {"fonte_banco_ativa": True, "run_id": "run_test"}
    with patch("app.bots.replicacao_d1_db_bridge.is_fonte_banco_ativa", return_value=True), patch(
        "app.bots.replicacao_d1_db_bridge.load_snapshot_from_run_id", return_value=None
    ), patch.dict(
        sys.modules,
        {
            "apps.replicacao_d1.exceptions": mock_exc,
            "apps.replicacao_d1.services.config_dto": mock_dto,
            "apps.replicacao_d1.services.planning_adapter": mock_adapter,
        },
    ):
        out = load_planning_dataframes(settings, run_id="run_test")

    assert out["config_version"] == 3
    assert out["snapshot_hash"] == "abc123"
    assert settings.get("_execution_snapshot") is not None
    assert settings.get("_escala_df") is not None
    assert out["calculadora_params"]["meta_produ"] == 300.0


def test_fallback_parquet_dias_ausentes_ativo_reads_settings():
    assert fallback_parquet_dias_ausentes_ativo({"fallback_parquet_dias_ausentes": True}) is True
    assert fallback_parquet_dias_ausentes_ativo(
        {"persistent": {"fallback_parquet_dias_ausentes": True}}
    ) is True
    assert fallback_parquet_dias_ausentes_ativo({}) is False


def _mock_source_batch_module(*, has_records: bool):
    mod = MagicMock()
    mod.rotina_day_has_records = MagicMock(return_value=has_records)
    return mod


def _mock_rotina_module():
    mod = MagicMock()
    mod.RotinaDetalhadoBrutoRecord = MagicMock()
    mod.RotinaDetalhadoBrutoRecord.DoesNotExist = type("DoesNotExist", (Exception,), {})
    return mod


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_load_source_dataframe_hybrid_db_uses_db_when_day_has_records(_mock_django):
    data_ref = datetime(2026, 8, 10)
    db_payload = {"dataframe": pd.DataFrame(), "source_batch_id": 99, "source_name": "db"}

    with patch(
        "app.bots.replicacao_d1_db_bridge.load_source_dataframe_db",
        return_value=db_payload,
    ) as load_db, patch(
        "app.bots.replicacao_d1_db_bridge.load_source_from_parquet_fallback_db",
    ) as load_pq, patch.dict(
        sys.modules,
        {
            "apps.replicacao_d1.services.source_batch": _mock_source_batch_module(has_records=True),
            "apps.rotina_bruto.models": _mock_rotina_module(),
        },
    ):
        result = load_source_dataframe_hybrid_db(
            data_ref,
            settings={"fallback_parquet_dias_ausentes": True},
        )

    load_db.assert_called_once_with(data_ref)
    load_pq.assert_not_called()
    assert result["source_batch_id"] == 99


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_load_source_dataframe_hybrid_db_raises_when_empty_and_flag_off(_mock_django):
    mock_rotina = _mock_rotina_module()
    data_ref = datetime(2026, 8, 10)

    with patch.dict(
        sys.modules,
        {
            "apps.replicacao_d1.services.source_batch": _mock_source_batch_module(has_records=False),
            "apps.rotina_bruto.models": mock_rotina,
        },
    ):
        with pytest.raises(mock_rotina.RotinaDetalhadoBrutoRecord.DoesNotExist):
            load_source_dataframe_hybrid_db(
                data_ref,
                settings={"fallback_parquet_dias_ausentes": False},
            )


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_load_source_dataframe_hybrid_db_uses_parquet_when_empty_and_flag_on(_mock_django):
    data_ref = datetime(2026, 8, 10)
    pq_payload = {
        "dataframe": pd.DataFrame([{"Protocolo": "P1"}]),
        "source_batch_id": 42,
        "source_name": "parquet-fallback:brflow-detalhado-tratado_20260810.parquet",
        "from_parquet_fallback": True,
    }

    with patch(
        "app.bots.replicacao_d1_db_bridge.load_source_from_parquet_fallback_db",
        return_value=pq_payload,
    ) as load_pq, patch.dict(
        sys.modules,
        {
            "apps.replicacao_d1.services.source_batch": _mock_source_batch_module(has_records=False),
            "apps.rotina_bruto.models": _mock_rotina_module(),
        },
    ):
        result = load_source_dataframe_hybrid_db(
            data_ref,
            settings={"fallback_parquet_dias_ausentes": True},
            warnings=[],
        )

    load_pq.assert_called_once()
    assert result["from_parquet_fallback"] is True
    assert result["source_batch_id"] == 42


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_load_plan_for_execution_db_includes_qtd_workflows_without_csv(_mock_django, tmp_path):
    wf_bio = MagicMock()
    wf_bio.workflow_config = "WF G qtd"
    wf_bio.workflow_brflow = "G auditoria por quantidade"
    wf_bio.fila = "G auditoria"
    wf_bio.modo_replicacao = "qtd"
    wf_bio.protocolos_planejados = 1037
    wf_bio.status_brflow = "PENDENTE"
    wf_bio.pk = 11

    wf_redoc = MagicMock()
    wf_redoc.workflow_config = "WF Redoc"
    wf_redoc.workflow_brflow = "DOC DIGITAL BETS"
    wf_redoc.fila = "Redoc"
    wf_redoc.modo_replicacao = "qtd"
    wf_redoc.protocolos_planejados = 1228
    wf_redoc.status_brflow = "PENDENTE"
    wf_redoc.pk = 12

    run = MagicMock()
    run.run_id = "20260817_173253"
    run.data_referencia_d1 = datetime(2026, 8, 17).date()
    run.auditores_ativos_brflow = 5
    run.auditores_ativos_case = 2
    run.plan_warnings = []
    run.config_snapshot = None
    run.workflows.order_by.return_value = [wf_bio, wf_redoc]
    run.protocolos.order_by.return_value = []

    mock_run_model = MagicMock()
    mock_run_model.objects.select_related.return_value.prefetch_related.return_value.get.return_value = run

    mock_plano_cls = MagicMock()
    mock_plano_instance = MagicMock()
    mock_plano_instance.protocolos_por_workflow = {}
    mock_plano_instance.workflow_brflow = {}
    mock_plano_instance.workflow_fila = {}
    mock_plano_instance.workflow_regra_brflow = {}
    mock_plano_instance.qtd_por_workflow = {}
    mock_plano_instance.csv_paths = {}
    mock_plano_instance.workflows = []
    mock_plano_cls.return_value = mock_plano_instance

    with patch("app.bots.replicacao_d1_db_bridge.ensure_plan_approved_db") as ensure_approval, patch(
        "app.bots.replicacao_d1_db_bridge.tempfile.TemporaryDirectory",
        side_effect=lambda **_kwargs: TemporaryDirectory(dir=tmp_path),
    ), patch(
        "app.bots.replicacao_d1_db_bridge.load_execution_state_db",
        return_value={"run_id": "20260817_173253", "workflows": {}},
    ), patch("app.bots.replicacao_d1_db_bridge._workflow_regra_map_from_run", return_value={}), patch.dict(
        sys.modules,
        {
            "apps.replicacao_d1.models": MagicMock(ReplicacaoD1Run=mock_run_model),
            "app.bots.replicacao_aud_planning": MagicMock(
                PlanoReplicacao=mock_plano_cls,
                eh_fila_modo_qtd=lambda fila: str(fila or "").strip().casefold() in {"bio", "redoc"},
            ),
        },
    ):
        plano, _state = bridge.load_plan_for_execution_db("20260817_173253", {})

    ensure_approval.assert_called_once_with("20260817_173253")
    assert mock_plano_instance.workflows == ["WF G qtd", "WF Redoc"]
    assert mock_plano_instance.qtd_por_workflow == {"WF G qtd": 1037, "WF Redoc": 1228}
    assert mock_plano_instance.workflow_modo_replicacao == {
        "WF G qtd": "qtd",
        "WF Redoc": "qtd",
    }
    assert mock_plano_instance.csv_paths == {}
    bridge.cleanup_temporary_plan(plano)


@patch("app.bots.replicacao_d1_db_bridge.ensure_django_ready", return_value=True)
def test_load_plan_for_execution_db_materializa_csv_para_bio_em_modo_protocolos(_mock_django, tmp_path):
    workflow = MagicMock(
        workflow_config="WF Bio CSV",
        workflow_brflow="BIO BRFLOW",
        fila="Bio",
        modo_replicacao="protocolos",
        protocolos_planejados=1,
        status_brflow="PENDENTE",
        pk=21,
    )
    protocolo = MagicMock(workflow_config="WF Bio CSV", protocolo="123456")
    run = MagicMock(
        run_id="20260817_180000",
        data_referencia_d1=datetime(2026, 8, 17).date(),
        auditores_ativos_brflow=5,
        auditores_ativos_case=2,
        plan_warnings=[],
        config_snapshot=None,
    )
    run.workflows.order_by.return_value = [workflow]
    run.protocolos.order_by.return_value = [protocolo]
    mock_run_model = MagicMock()
    mock_run_model.objects.select_related.return_value.prefetch_related.return_value.get.return_value = run

    mock_plano_cls = MagicMock()
    plano_instance = MagicMock()
    plano_instance.protocolos_por_workflow = {}
    plano_instance.workflow_brflow = {}
    plano_instance.workflow_fila = {}
    plano_instance.workflow_regra_brflow = {}
    plano_instance.workflow_modo_replicacao = {}
    plano_instance.qtd_por_workflow = {}
    plano_instance.csv_paths = {}
    plano_instance.workflows = []
    mock_plano_cls.return_value = plano_instance

    with patch("app.bots.replicacao_d1_db_bridge.ensure_plan_approved_db") as ensure_approval, patch(
        "app.bots.replicacao_d1_db_bridge.tempfile.TemporaryDirectory",
        side_effect=lambda **_kwargs: TemporaryDirectory(dir=tmp_path),
    ), patch(
        "app.bots.replicacao_d1_db_bridge.load_execution_state_db",
        return_value={"run_id": run.run_id, "workflows": {}},
    ), patch("app.bots.replicacao_d1_db_bridge._workflow_regra_map_from_run", return_value={}), patch.dict(
        sys.modules,
        {
            "apps.replicacao_d1.models": MagicMock(ReplicacaoD1Run=mock_run_model),
            "app.bots.replicacao_aud_planning": MagicMock(PlanoReplicacao=mock_plano_cls),
        },
    ):
        plano, _state = bridge.load_plan_for_execution_db(
            run.run_id, {"execucao_agendada": True}
        )

    ensure_approval.assert_not_called()
    assert plano.workflow_modo_replicacao == {"WF Bio CSV": "protocolos"}
    assert plano.protocolos_por_workflow == {"WF Bio CSV": ["123456"]}
    assert plano.qtd_por_workflow == {}
    assert plano.csv_paths["WF Bio CSV"].read_text(encoding="utf-8-sig").strip() == "123456"
    bridge.cleanup_temporary_plan(plano)


try:
    import django  # noqa: F401

    DJANGO_AVAILABLE = True
except ImportError:
    DJANGO_AVAILABLE = False


@pytest.mark.skipif(not DJANGO_AVAILABLE, reason="Django não instalado no ambiente automacoes")
@patch("django.setup")
def test_ensure_django_ready_with_mock_backend(mock_setup):
    from app.bots import replicacao_d1_db_bridge as bridge

    bridge._DJANGO_READY = False
    backend = Path(__file__).resolve().parent.parent.parent / "backend"
    if not (backend / "config" / "settings.py").is_file():
        pytest.skip("backend/config/settings.py ausente")

    with patch.dict("sys.modules", {"django": MagicMock()}):
        mock_apps = MagicMock()
        mock_apps.ready = False
        with patch("django.apps.apps", mock_apps):
            result = bridge.ensure_django_ready()
    assert result in (True, False)


def test_apply_persistent_brflow_settings_propaga_codigos_bio_redoc():
    from app.bots.replicacao_d1_db_bridge import _apply_persistent_brflow_settings

    settings: dict = {}
    persistent = {
        "replicacao_workflow_cod_bio": "17761",
        "replicacao_workflow_destino_bio": "Auditoria Biometria - Auditoria Biometria",
        "replicacao_workflow_cod_redoc": "18013",
        "replicacao_workflow_destino_redoc": "Auditoria Redoc - Auditoria Redoc",
        "meta_produ_diaria_bio": 388.0,
        "meta_produ_diaria_redoc": 388.0,
    }
    _apply_persistent_brflow_settings(settings, persistent)
    assert settings["replicacao_workflow_cod_bio"] == "17761"
    assert settings["replicacao_workflow_cod_redoc"] == "18013"
    assert settings["meta_produ_bio"] == "388"
    assert settings["meta_produ_redoc"] == "388"
