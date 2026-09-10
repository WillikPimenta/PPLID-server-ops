"""Testes offline da conferência replicacao_aud_d1_conferencia."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.config import (
    PREFIXO_AUDITORIA_REPLICADOS_D1,
    REPLICACAO_AUD_D1_CONFERENCIA_PREFIXO,
    REPLICACAO_AUD_D1_RELATORIO_PREFIXO,
)
from app.bots.rotina.tasks import auditoria as aud_mod
from app.bots.rotina.tasks.auditoria import (
    _caminho_conferencia_replicados,
    _gerar_conferencia_replicados_d1,
    _reprocessar_conferencia_replicados_pendentes,
    _resolver_relatorio_replicacao,
)


def _criar_relatorio_plano(caminho: Path, protocolos: list[str]) -> None:
    df = pd.DataFrame({"Protocolo": protocolos, "Workflow": ["WF1"] * len(protocolos)})
    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Plano", index=False)


def test_resolver_relatorio_replicacao_encontra_xlsx(tmp_path, monkeypatch):
    pasta_rel = tmp_path / "relatorios"
    pasta_rel.mkdir()
    relatorio = pasta_rel / f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}20260609_120000.xlsx"
    _criar_relatorio_plano(relatorio, ["P001"])

    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_RELATORIOS", pasta_rel)
    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_RESUMO", tmp_path / "resumo")

    encontrado = _resolver_relatorio_replicacao("20260610")
    assert encontrado == relatorio


def test_gerar_conferencia_cria_csv(tmp_path, monkeypatch):
    pasta_rel = tmp_path / "relatorios"
    pasta_conf = tmp_path / "confirmed"
    pasta_rel.mkdir()
    relatorio = pasta_rel / f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}20260609_120000.xlsx"
    _criar_relatorio_plano(relatorio, ["P001", "P002"])

    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_RELATORIOS", pasta_rel)
    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_RESUMO", tmp_path / "resumo")
    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_CONFIRMED", pasta_conf)

    df_replicados = pd.DataFrame({"Protocolo Origem": ["P001", "P999"]})
    saida = _gerar_conferencia_replicados_d1(df_replicados, "20260610", "bruto.csv")
    assert saida is not None
    assert saida.name == f"{REPLICACAO_AUD_D1_CONFERENCIA_PREFIXO}20260610.csv"

    df = pd.read_csv(saida, sep=";", encoding="utf-8-sig")
    assert set(df["Status"]) == {"REPLICADO", "FALTANTE"}
    assert "P001" in df["Protocolo"].values
    assert "P002" in df["Protocolo"].values


def test_reprocessar_pendentes_pula_existente(tmp_path, monkeypatch):
    pasta_replicados = tmp_path / "replicados"
    pasta_rel = tmp_path / "relatorios"
    pasta_conf = tmp_path / "confirmed"
    pasta_replicados.mkdir()
    pasta_rel.mkdir()
    pasta_conf.mkdir()

    data_ref = "20260610"
    csv_rep = pasta_replicados / f"{PREFIXO_AUDITORIA_REPLICADOS_D1}{data_ref}.csv"
    pd.DataFrame({"Protocolo Origem": ["P001"]}).to_csv(csv_rep, index=False, sep=";")

    relatorio = pasta_rel / f"{REPLICACAO_AUD_D1_RELATORIO_PREFIXO}20260609_120000.xlsx"
    _criar_relatorio_plano(relatorio, ["P001"])

    conferencia = pasta_conf / f"{REPLICACAO_AUD_D1_CONFERENCIA_PREFIXO}{data_ref}.csv"
    conferencia.parent.mkdir(parents=True, exist_ok=True)
    conferencia.write_text("Protocolo;Status\nP001;REPLICADO\n", encoding="utf-8-sig")

    monkeypatch.setattr(aud_mod, "PASTA_AUDITORIA_REPLICADOS_D1", pasta_replicados)
    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_RELATORIOS", pasta_rel)
    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_RESUMO", tmp_path / "resumo")
    monkeypatch.setattr(aud_mod, "PASTA_REPLICACAO_AUD_D1_CONFIRMED", pasta_conf)

    resultados = _reprocessar_conferencia_replicados_pendentes(forcar=False)
    assert len(resultados) == 1
    assert resultados[0]["status"] == "ja_existe"

    resultados_force = _reprocessar_conferencia_replicados_pendentes(forcar=True)
    assert resultados_force[0]["status"] == "ok"
