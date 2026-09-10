"""Testes de meta mensal por workflow (ledger + snapshot)."""

import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.meta_cliente_mensal import (
    ano_mes_de_data,
    aplicar_ajuste_manual_consumo,
    calcular_projecao_fim_mes,
    carregar_consumo_meta_mensal,
    carregar_headroom_meta_mensal,
    carregar_metas_por_workflow,
    expandir_metas_cliente_para_workflows,
    registrar_consumo_meta_run,
    remover_consumo_meta_run,
    resolver_ano_mes,
    run_id_registrado_no_ledger,
    sincronizar_consumo_meta_run,
)
from app.bots.replicacao_aud_d1_planning import (
    PlanoReplicacao,
    _normalizar_workflow,
    _reduzir_headroom_base_run,
    apagar_plano_run_d1,
    aplicar_politica_retencao_planos_d1,
    redistribuir_amostra_priorizada_com_meta,
)
from app.bots import replicacao_aud_planning as rap
from app.config import (
    COLUNA_CONFIG_CLIENTE,
    COLUNA_CONFIG_META_CLIENTE,
    COLUNA_CONFIG_WORKFLOW,
)


def test_ledger_substituicao_por_run_id():
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        settings = {"replicacao_config_base": str(base), "fonte_banco_ativa": False}
        ano = "2026-06"
        wf = _normalizar_workflow("WF A")
        assert registrar_consumo_meta_run(ano, "run1", {wf: 10}, settings=settings)
        assert registrar_consumo_meta_run(ano, "run1", {wf: 25}, settings=settings)
        assert carregar_consumo_meta_mensal(ano, settings=settings).get(wf) == 25
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_headroom_mensal_por_workflow():
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        settings = {"replicacao_config_base": str(base), "fonte_banco_ativa": False}
        ano = "2026-06"
        wf_a = _normalizar_workflow("WF A")
        wf_b = _normalizar_workflow("WF B")
        metas = {wf_a: 100, wf_b: 50}
        registrar_consumo_meta_run(ano, "r1", {wf_a: 40}, settings=settings)
        headroom = carregar_headroom_meta_mensal(ano, metas, settings=settings)
        assert headroom[wf_a] == 60
        assert headroom[wf_b] == 50
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_metas_por_workflow_herdam_cliente():
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF A", "WF B"],
            COLUNA_CONFIG_CLIENTE: ["Cliente X", "Cliente X"],
            COLUNA_CONFIG_META_CLIENTE: [500, 500],
        }
    )
    metas = carregar_metas_por_workflow(config)
    assert metas[_normalizar_workflow("WF A")] == 500
    assert metas[_normalizar_workflow("WF B")] == 500


def test_expandir_metas_cliente_para_workflows():
    mapa = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF A", "WF B"],
            COLUNA_CONFIG_CLIENTE: ["Cliente X", "Cliente Y"],
        }
    )
    cat = pd.DataFrame(
        {
            COLUNA_CONFIG_CLIENTE: ["Cliente X", "Cliente Y"],
            COLUNA_CONFIG_META_CLIENTE: [300, 200],
            "_cli_key": [
                _normalizar_workflow("Cliente X"),
                _normalizar_workflow("Cliente Y"),
            ],
        }
    )
    metas = expandir_metas_cliente_para_workflows(mapa, cat)
    assert metas[_normalizar_workflow("WF A")] == 300
    assert metas[_normalizar_workflow("WF B")] == 200


def test_redistribuir_respeita_meta_mensal_por_workflow():
    wf_a = _normalizar_workflow("WF A")
    wf_b = _normalizar_workflow("WF B")
    headroom = {wf_a: 0, wf_b: 100}
    fontes = [{"workflow": "WF Off", "amostra": 10, "cliente": "Cliente A", "categoria": "Cat A"}]
    destinos = [
        {"workflow": "WF A", "amostra": 5, "cliente": "Cliente A", "categoria": "Cat A"},
        {"workflow": "WF B", "amostra": 5, "cliente": "Cliente B", "categoria": "Cat B"},
    ]
    bonus, sobra = redistribuir_amostra_priorizada_com_meta(fontes, destinos, headroom)
    assert bonus.get("WF A", 0) == 0
    assert bonus.get("WF B", 0) == 10
    assert sobra == 0


def test_redistribuir_todos_meta_cheia_distribui_normal():
    wf_a = _normalizar_workflow("WF A")
    wf_b = _normalizar_workflow("WF B")
    headroom = {wf_a: 0, wf_b: 0}
    fontes = [{"workflow": "WF Off", "amostra": 6, "cliente": "Cliente X", "categoria": "Cat X"}]
    destinos = [
        {"workflow": "WF A", "amostra": 4, "cliente": "Cliente A", "categoria": "Cat A"},
        {"workflow": "WF B", "amostra": 2, "cliente": "Cliente B", "categoria": "Cat B"},
    ]
    bonus, sobra = redistribuir_amostra_priorizada_com_meta(fontes, destinos, headroom)
    assert sum(bonus.values()) == 6
    assert sobra == 0


def test_redistribuir_sem_fallback_deixa_sobra():
    wf_a = _normalizar_workflow("WF A")
    headroom = {wf_a: 0}
    fontes = [{"workflow": "WF Off", "amostra": 5, "cliente": "X", "categoria": "Cat"}]
    destinos = [{"workflow": "WF A", "amostra": 5, "cliente": "Cliente A", "categoria": "Cat A"}]
    bonus, sobra = redistribuir_amostra_priorizada_com_meta(
        fontes, destinos, headroom, fallback_sem_cap=False
    )
    assert sum(bonus.values()) == 0
    assert sobra == 5


def test_reduzir_headroom_base_run_por_workflow():
    wf1 = _normalizar_workflow("WF1")
    wf2 = _normalizar_workflow("WF2")
    headroom = {wf1: 20, wf2: 20}
    itens = [
        {"workflow": "WF1", "amostra": 8, "cliente": "Cliente A", "categoria": "Cat"},
        {"workflow": "WF2", "amostra": 5, "cliente": "Cliente A", "categoria": "Cat"},
    ]
    out = _reduzir_headroom_base_run(headroom, itens)
    assert out[wf1] == 12
    assert out[wf2] == 15


def test_apagar_plano_remove_ledger():
    tmp = tempfile.mkdtemp()
    try:
        from app.config import REPLICACAO_AUD_D1_EXECUCAO_PREFIXO

        base_resumo = Path(tmp) / "resumo"
        base_resumo.mkdir(parents=True)
        config_base = Path(tmp) / "config"
        config_base.mkdir(parents=True)
        settings = {"replicacao_config_base": str(config_base), "fonte_banco_ativa": False}
        run_id = "20260101_120000"
        wf = _normalizar_workflow("WF A")
        estado_path = base_resumo / f"{REPLICACAO_AUD_D1_EXECUCAO_PREFIXO}{run_id}.json"
        estado_path.write_text(json.dumps({"run_id": run_id, "workflows": {"WF": {"status": "SALVO_OK"}}}), encoding="utf-8")
        registrar_consumo_meta_run("2026-01", run_id, {wf: 3}, settings=settings)
        assert run_id_registrado_no_ledger(run_id, settings=settings)

        with patch("app.bots.replicacao_aud_d1_planning.PASTA_REPLICACAO_AUD_D1_RESUMO", base_resumo), patch(
            "app.bots.replicacao_aud_d1_planning.caminho_estado_execucao",
            lambda rid: base_resumo / f"{REPLICACAO_AUD_D1_EXECUCAO_PREFIXO}{rid}.json",
        ), patch(
            "app.bots.replicacao_aud_d1_planning._pasta_saida_protocolos_d1",
            lambda rid: base_resumo / f"proto_{rid}",
        ), patch(
            "app.bots.replicacao_aud_d1_planning._caminho_relatorio_excel_d1",
            lambda rid: base_resumo / f"rel_{rid}.xlsx",
        ):
            res = apagar_plano_run_d1(run_id, forcar=True, remover_ledger=True, settings=settings)
            assert res["removidos"]
            assert res["ledger_removido"]
        assert not run_id_registrado_no_ledger(run_id, settings=settings)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolver_ano_mes_data_referencia():
    settings = {"meta_cliente_ano_mes_ref": "data_referencia_d1"}
    data_ref = datetime(2026, 5, 31)
    data_exec = datetime(2026, 6, 1)
    assert resolver_ano_mes(settings, data_exec=data_exec, data_referencia_d1=data_ref) == "2026-05"
    assert resolver_ano_mes({}, data_exec=data_exec) == "2026-06"


def test_sincronizar_consumo_idempotente_retomada():
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        settings = {"replicacao_config_base": str(base), "fonte_banco_ativa": False}
        wf1 = "WF1"
        wf2 = "WF2"
        plano = PlanoReplicacao(
            data_referencia=datetime.now(),
            pasta_protocolos=base,
            pasta_resumo=base,
            run_id="run_retomada",
            resumo=[
                {
                    "Workflow": wf1,
                    COLUNA_CONFIG_CLIENTE: "Cliente A",
                    "Protocolos Salvos": 5,
                },
                {
                    "Workflow": wf2,
                    COLUNA_CONFIG_CLIENTE: "Cliente B",
                    "Protocolos Salvos": 3,
                },
            ],
        )
        estado_parcial = {"workflows": {wf1: {"status": "SALVO_OK"}}}
        assert sincronizar_consumo_meta_run(plano, estado_parcial, settings=settings)
        assert carregar_consumo_meta_mensal(ano_mes_de_data(), settings=settings).get(
            _normalizar_workflow(wf1)
        ) == 5

        estado_full = {
            "workflows": {
                wf1: {"status": "SALVO_OK"},
                wf2: {"status": "SALVO_OK"},
            }
        }
        assert sincronizar_consumo_meta_run(plano, estado_full, settings=settings)
        consumo = carregar_consumo_meta_mensal(ano_mes_de_data(), settings=settings)
        assert consumo.get(_normalizar_workflow(wf1)) == 5
        assert consumo.get(_normalizar_workflow(wf2)) == 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_validar_metas_workflows_nao_soma_limites_individuais():
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF A", "WF B"],
            COLUNA_CONFIG_CLIENTE: ["A", "A"],
            COLUNA_CONFIG_META_CLIENTE: [500, 500],
        }
    )
    warns = rap.validar_metas_workflows_config(config, capacidade_mensal_estimada=900, tolerancia_soma=50)
    assert not any("Soma das metas" in w or "diverge" in w for w in warns)


def test_validar_metas_workflows_mantem_avisos_individuais():
    config = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF Sem Meta", "WF Meta Invalida"],
            COLUNA_CONFIG_CLIENTE: ["A", "B"],
            COLUNA_CONFIG_META_CLIENTE: [None, -1],
        }
    )

    warns = rap.validar_metas_workflows_config(config)

    assert any("WF Sem Meta" in warning and "sem limite" in warning for warning in warns)
    assert any("WF Meta Invalida" in warning and "< 0" in warning for warning in warns)


def test_ajuste_manual_consumo_workflow():
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        settings = {"replicacao_config_base": str(base), "fonte_banco_ativa": False}
        ano = "2026-06"
        wf = _normalizar_workflow("WF A")
        registrar_consumo_meta_run(ano, "r1", {wf: 10}, settings=settings)
        assert aplicar_ajuste_manual_consumo(ano, "WF A", 42, settings=settings, cliente="Cliente A")
        assert carregar_consumo_meta_mensal(ano, settings=settings).get(wf) == 42
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_remover_consumo_meta_run():
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        settings = {"replicacao_config_base": str(base), "fonte_banco_ativa": False}
        ano = "2026-06"
        wf = _normalizar_workflow("WF X")
        registrar_consumo_meta_run(ano, "run_x", {wf: 7}, settings=settings)
        res = remover_consumo_meta_run("run_x", settings=settings)
        assert res["removido"]
        assert carregar_consumo_meta_mensal(ano, settings=settings).get(wf, 0) == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_projecao_fim_mes_ritmo_sem_limite_de_balanceamento():
    from datetime import date

    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        settings = {"replicacao_config_base": str(base), "fonte_banco_ativa": False}
        ano = "2026-06"
        wf = _normalizar_workflow("WF A")
        linhas = [
            {
                "ano_mes": ano,
                "workflow": wf,
                "cliente": float("nan"),  # vazio no CSV → NaN; não pode ir pro JSON
                "meta_mensal": "1",
                "consumo_acumulado": "100",
                "headroom": "0",
            }
        ]
        # Em 10/jun/2026 (quarta): resto do mês ainda tem dias úteis
        proj = calcular_projecao_fim_mes(
            ano, linhas, settings=settings, hoje=date(2026, 6, 10)
        )
        assert proj["consumo_total"] == 100
        assert "meta_total" not in proj
        assert "headroom_total" not in proj
        assert "pct_meta" not in proj
        assert "gap_vs_meta" not in proj
        assert proj["dias_com_execucao"] == 0
        assert proj["dias_uteis_restantes"] > 0
        assert proj["projecao_ritmo"] == int(
            round(100 + proj["ritmo_diario"] * proj["dias_uteis_restantes"])
        )
        assert proj["projecao_fim_mes"] == proj["projecao_ritmo"]
        assert proj["clientes"][0]["projecao_fim_mes"] == proj["projecao_ritmo"]
        assert proj["projecao_fim_mes"] > 1
        assert proj["clientes"][0]["cliente"] == ""
        import json

        json.dumps(proj, allow_nan=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
