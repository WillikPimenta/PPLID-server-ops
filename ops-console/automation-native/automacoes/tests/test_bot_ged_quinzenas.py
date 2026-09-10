"""Testes offline da divisão em quinzenas e consolidação do bot GED."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import time

import pandas as pd
import pytest

from app.bots import bot_ged as ged_mod
from app.bots.bot_ged import (
    _candidatos_download,
    _chave_arquivo_download,
    _consolidar_quinzenas_mes,
    _dividir_intervalo_em_quinzenas,
    _extrair_token_ged,
    _intervalos_quinzena_formatados,
    _log_arquivos_prefixo_rejeitado,
    _montar_nome_saida_ged_quinzena,
    _snapshot_arquivos_pastas,
)


def _quinzenas(inicio: date, fim: date) -> list[tuple[date, date, int]]:
    return _dividir_intervalo_em_quinzenas(inicio, fim)


def test_dividir_diurno_parcial_q1():
    """Dia 10: apenas Q1 parcial."""
    result = _quinzenas(date(2026, 6, 1), date(2026, 6, 10))
    assert result == [(date(2026, 6, 1), date(2026, 6, 10), 1)]


def test_dividir_diurno_duas_quinzenas():
    """Dia 20: Q1 completa + Q2 parcial."""
    result = _quinzenas(date(2026, 6, 1), date(2026, 6, 20))
    assert result == [
        (date(2026, 6, 1), date(2026, 6, 15), 1),
        (date(2026, 6, 16), date(2026, 6, 20), 2),
    ]


def test_dividir_mes_completo():
    """Maio completo: 31 dias."""
    result = _quinzenas(date(2026, 5, 1), date(2026, 5, 31))
    assert result == [
        (date(2026, 5, 1), date(2026, 5, 15), 1),
        (date(2026, 5, 16), date(2026, 5, 31), 2),
    ]


def test_dividir_fevereiro_bissexto():
    """Fevereiro 2024 (29 dias)."""
    result = _quinzenas(date(2024, 2, 1), date(2024, 2, 29))
    assert result == [
        (date(2024, 2, 1), date(2024, 2, 15), 1),
        (date(2024, 2, 16), date(2024, 2, 29), 2),
    ]


def test_intervalos_quinzena_formatados():
    result = _intervalos_quinzena_formatados(date(2026, 6, 1), date(2026, 6, 20))
    assert result == [
        ("01/06/2026", "15/06/2026", "202606", 1),
        ("16/06/2026", "20/06/2026", "202606", 2),
    ]


def test_montar_nome_saida_ged_quinzena():
    assert _montar_nome_saida_ged_quinzena("202606", 1) == "ged-detalhado-tratado_202606_1.parquet"
    assert _montar_nome_saida_ged_quinzena("202606", 2) == "ged-detalhado-tratado_202606_2.parquet"


def _df_ged(protocolos: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Protocolo": protocolos,
            "Data do Recebimento": ["01/06/2026"] * len(protocolos),
            "Tipo de Serviço Primário": ["Movel"] * len(protocolos),
            "Data da Venda": ["01/06/2026"] * len(protocolos),
            "Data do Batimento": ["01/06/2026"] * len(protocolos),
            "Data Retorno Inspeção": ["-"] * len(protocolos),
            "Data Envio Inspe.": ["-"] * len(protocolos),
            "Canal de Ativação": ["Loja"] * len(protocolos),
            "Status Contrato": ["Ativo"] * len(protocolos),
            "Aceite Digital": ["Sim"] * len(protocolos),
        }
    )


def test_consolidar_quinzenas_mes_duas_partes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ged_mod, "PASTA_GED_TRATADO", tmp_path)
    monkeypatch.setattr(ged_mod, "PASTA_GED_PARQUET_ONEDRIVE", tmp_path / "onedrive")
    monkeypatch.setattr(ged_mod, "PASTA_GED_CSV_GED20", tmp_path / "ged20")
    monkeypatch.setattr(ged_mod, "_espelhar_saida_onedrive", lambda _p: None)

    q1 = tmp_path / _montar_nome_saida_ged_quinzena("202606", 1)
    q2 = tmp_path / _montar_nome_saida_ged_quinzena("202606", 2)
    _df_ged(["111", "222"]).to_parquet(q1, index=False)
    _df_ged(["333"]).to_parquet(q2, index=False)

    caminho = _consolidar_quinzenas_mes("202606")
    assert caminho is not None
    assert caminho.name == "ged-detalhado-tratado_202606.parquet"
    assert not q1.exists()
    assert not q2.exists()

    df = pd.read_parquet(caminho)
    assert set(df["Protocolo"]) == {"111", "222", "333"}


def test_salvar_saida_emite_sync_apenas_mensal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(ged_mod, "PASTA_GED_TRATADO", tmp_path)
    monkeypatch.setattr(ged_mod, "PASTA_GED_PARQUET_ONEDRIVE", tmp_path / "onedrive")
    monkeypatch.setattr(ged_mod, "PASTA_GED_CSV_GED20", tmp_path / "ged20")
    monkeypatch.setattr(ged_mod, "_espelhar_saida_onedrive", lambda _p: None)

    df = _df_ged(["111"])
    quinzena = tmp_path / _montar_nome_saida_ged_quinzena("202606", 1)
    mensal = tmp_path / "ged-detalhado-tratado_202606.parquet"

    ged_mod._salvar_saida_parquet(df, quinzena)
    out_q = capsys.readouterr().out
    assert "ROTINA_BRUTO_SAVED|ged_detalhado|" not in out_q

    ged_mod._salvar_saida_parquet(df, mensal)
    out_m = capsys.readouterr().out
    assert f"ROTINA_BRUTO_SAVED|ged_detalhado|{mensal.resolve()}" in out_m


def test_salvar_saida_exporta_csv_ged20(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pasta_bots = tmp_path / "bots"
    pasta_pq = tmp_path / "ged_parquet"
    pasta_csv = tmp_path / "ged20"
    pasta_bots.mkdir()
    pasta_pq.mkdir()
    pasta_csv.mkdir()

    monkeypatch.setattr(ged_mod, "PASTA_GED_TRATADO", pasta_bots)
    monkeypatch.setattr(ged_mod, "PASTA_GED_PARQUET_ONEDRIVE", pasta_pq)
    monkeypatch.setattr(ged_mod, "PASTA_GED_CSV_GED20", pasta_csv)

    df = _df_ged(["111", "222"])
    mensal = pasta_bots / "ged-detalhado-tratado_202607.parquet"
    ged_mod._salvar_saida_parquet(df, mensal)

    assert mensal.exists()
    assert not mensal.with_suffix(".csv").exists()
    assert (pasta_pq / mensal.name).exists()
    assert not (pasta_pq / mensal.with_suffix(".csv").name).exists()

    csv_gerencial = pasta_csv / "ged-detalhado-tratado_202607.csv"
    assert csv_gerencial.exists()
    lido = pd.read_csv(csv_gerencial, sep=";", encoding="cp1252", dtype=str)
    assert list(lido["Protocolo"]) == ["111", "222"]

def test_consolidar_quinzenas_mes_apenas_q1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ged_mod, "PASTA_GED_TRATADO", tmp_path)
    monkeypatch.setattr(ged_mod, "PASTA_GED_PARQUET_ONEDRIVE", tmp_path / "onedrive")
    monkeypatch.setattr(ged_mod, "PASTA_GED_CSV_GED20", tmp_path / "ged20")
    monkeypatch.setattr(ged_mod, "_espelhar_saida_onedrive", lambda _p: None)

    q1 = tmp_path / _montar_nome_saida_ged_quinzena("202606", 1)
    _df_ged(["111"]).to_parquet(q1, index=False)

    caminho = _consolidar_quinzenas_mes("202606")
    assert caminho is not None
    df = pd.read_parquet(caminho)
    assert list(df["Protocolo"]) == ["111"]


def test_consolidar_quinzenas_mes_deduplica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ged_mod, "PASTA_GED_TRATADO", tmp_path)
    monkeypatch.setattr(ged_mod, "PASTA_GED_PARQUET_ONEDRIVE", tmp_path / "onedrive")
    monkeypatch.setattr(ged_mod, "PASTA_GED_CSV_GED20", tmp_path / "ged20")
    monkeypatch.setattr(ged_mod, "_espelhar_saida_onedrive", lambda _p: None)

    q1 = tmp_path / _montar_nome_saida_ged_quinzena("202606", 1)
    q2 = tmp_path / _montar_nome_saida_ged_quinzena("202606", 2)
    _df_ged(["111", "222"]).to_parquet(q1, index=False)
    _df_ged(["222", "333"]).to_parquet(q2, index=False)

    caminho = _consolidar_quinzenas_mes("202606")
    df = pd.read_parquet(caminho)
    assert len(df) == 3
    assert set(df["Protocolo"]) == {"111", "222", "333"}


def test_consolidar_quinzenas_mes_sem_arquivos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ged_mod, "PASTA_GED_TRATADO", tmp_path)
    assert _consolidar_quinzenas_mes("202606") is None


def test_extrair_token_ged_da_url_e_nome():
    assert _extrair_token_ged(
        "https://ged-web-frontend.claro.br.experian.eeco/protocolo/csv?token=exp_6a57d49cf35c4"
    ) == "exp_6a57d49cf35c4"
    assert _extrair_token_ged("pos_venda_exp_6a57d4959d414.csv") == "exp_6a57d4959d414"
    assert _extrair_token_ged("sem-token.csv") == ""


def test_candidatos_download_filtra_por_token(tmp_path: Path):
    proprio = tmp_path / "pos_venda_exp_aaa111.csv"
    alheio = tmp_path / "pos_venda_exp_bbb222.csv"
    proprio.write_text("x", encoding="utf-8")
    alheio.write_text("y", encoding="utf-8")
    before: set[str] = set()
    inicio = time.time()

    com_token = _candidatos_download(tmp_path, before, inicio, token_esperado="exp_aaa111")
    assert [p.name for p in com_token] == ["pos_venda_exp_aaa111.csv"]

    sem_token = _candidatos_download(tmp_path, before, inicio, token_esperado="")
    assert {p.name for p in sem_token} == {
        "pos_venda_exp_aaa111.csv",
        "pos_venda_exp_bbb222.csv",
    }


def test_candidatos_download_ignora_arquivos_ja_no_snapshot(tmp_path: Path):
    antigo = tmp_path / "pos_venda_exp_old.csv"
    antigo.write_text("old", encoding="utf-8")
    before = {_chave_arquivo_download(antigo)}
    inicio = time.time()
    assert _candidatos_download(tmp_path, before, inicio, token_esperado="exp_old") == []

    novo = tmp_path / "pos_venda_exp_new.csv"
    novo.write_text("new", encoding="utf-8")
    encontrados = _candidatos_download(tmp_path, before, inicio, token_esperado="exp_new")
    assert [p.name for p in encontrados] == ["pos_venda_exp_new.csv"]


def test_candidatos_download_detecta_path_regravado_pela_assinatura(tmp_path: Path):
    arquivo = tmp_path / "pos_venda_exp_same.csv"
    arquivo.write_text("old", encoding="utf-8")
    before = _snapshot_arquivos_pastas([tmp_path])

    arquivo.write_text("conteudo novo e maior", encoding="utf-8")
    encontrados = _candidatos_download(
        tmp_path,
        before,
        inicio_clique=time.time() + 60,
        token_esperado="exp_same",
    )

    assert encontrados == [arquivo]


def test_log_prefixo_rejeitado_nao_spam_arquivos_antigos(tmp_path: Path, caplog):
    antigo = tmp_path / "protocolos.csv"
    antigo.write_text("a", encoding="utf-8")
    before = _snapshot_arquivos_pastas([tmp_path])
    inicio = time.time()
    ja_logados: set[str] = set()

    with caplog.at_level("WARNING", logger="robots.bot_ged"):
        _log_arquivos_prefixo_rejeitado(tmp_path, before, inicio, ja_logados)

    assert "protocolos.csv" not in caplog.text
    assert ja_logados == set()

    novo = tmp_path / "outro.csv"
    novo.write_text("b", encoding="utf-8")
    with caplog.at_level("WARNING", logger="robots.bot_ged"):
        _log_arquivos_prefixo_rejeitado(tmp_path, before, inicio, ja_logados)

    assert "outro.csv" in caplog.text
    assert "outro.csv" in ja_logados
