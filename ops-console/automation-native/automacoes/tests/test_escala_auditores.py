"""Testes da fórmula de escala de auditores (espelho Excel)."""

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.bots.replicacao_aud_planning import (
    EscalaAuditoresAusenteError,
    InfoCapacidade,
    _buscar_valor_resumo,
    _ler_resumo_config_excel,
    _normalizar_rotulo,
    _resolver_csv_escala_auditores,
    _resolver_data_escala_replicacao,
    _settings_usam_escala_d1,
    aplicar_escala_auditores_config_por_fila,
    calcular_amostras_por_capacidade,
    carregar_config_auditoria,
    carregar_escala_auditores,
    carregar_meta_produ,
)
from app.config import (
    COLUNA_CONFIG_FILA,
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_ESCALA_AUDITORES_CASE,
    PASTA_REPLICACAO_AUD_D1_CONFIG,
)


def test_formula_capacidade_120():
    df = pd.DataFrame({
        "Workflow": ["A", "B"],
        "amostra_diaria": [50, 50],
    })
    out, info = calcular_amostras_por_capacidade(df, auditores_ativos=12, meta_produ=10.0)
    assert info.capacidade_produtiva == 120.0
    assert abs(info.fator_capacidade - 1.2) < 1e-9
    assert list(out["amostra_ajustada"]) == [60, 60]


def test_formula_capacidade_50():
    df = pd.DataFrame({
        "Workflow": ["A", "B"],
        "amostra_diaria": [50, 50],
    })
    out, info = calcular_amostras_por_capacidade(df, auditores_ativos=5, meta_produ=10.0)
    assert info.capacidade_produtiva == 50.0
    assert abs(info.fator_capacidade - 0.5) < 1e-9
    assert list(out["amostra_ajustada"]) == [25, 25]


def test_carregar_escala_csv():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "escala.csv"
        path.write_text(
            "data,auditores_ativos\n20260527,12\n20260528,15\n",
            encoding="utf-8-sig",
        )
        data_ref = datetime(2026, 5, 27)
        n = carregar_escala_auditores(
            data_ref, path=path, settings={"fonte_banco_ativa": False}
        )
        assert n == 12


def test_carregar_escala_zero_auditores():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "escala.csv"
        path.write_text(
            "data,auditores_ativos\n20260527,0\n",
            encoding="utf-8-sig",
        )
        n = carregar_escala_auditores(
            datetime(2026, 5, 27), path=path, settings={"fonte_banco_ativa": False}
        )
        assert n == 0


def test_formula_capacidade_zero_auditores():
    df = pd.DataFrame({
        "Workflow": ["A", "B"],
        "amostra_diaria": [50, 50],
    })
    out, info = calcular_amostras_por_capacidade(df, auditores_ativos=0, meta_produ=10.0)
    assert info.capacidade_produtiva == 0.0
    assert info.fator_capacidade == 0.0
    assert list(out["amostra_ajustada"]) == [0, 0]


def test_escala_ausente_nao_executa():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "escala.csv"
        path.write_text(
            "data,auditores_ativos\n20260527,12\n",
            encoding="utf-8-sig",
        )
        try:
            carregar_escala_auditores(
                datetime(2026, 5, 29), path=path,
                settings={"fonte_banco_ativa": False},
            )
            assert False, "deveria falhar sem a data"
        except EscalaAuditoresAusenteError as exc:
            assert "20260529" in str(exc)
            assert "auditores_ativos" in str(exc)


def test_carregar_escala_coluna_case():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "escala.csv"
        path.write_text(
            "data,auditores_ativos,auditores_ativos_case\n20260527,12,7\n",
            encoding="utf-8-sig",
        )
        data_ref = datetime(2026, 5, 27)
        n = carregar_escala_auditores(
            data_ref,
            path=path,
            settings={"fonte_banco_ativa": False},
            coluna_auditores=COLUNA_ESCALA_AUDITORES_CASE,
        )
        assert n == 7


def test_aplicar_escala_por_fila():
    df = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["G1", "G2", "WF31"],
            "amostra_diaria": [50, 50, 40],
            COLUNA_CONFIG_FILA: ["G auditoria", "G auditoria", "3.1"],
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "escala.csv"
        path.write_text(
            "data,auditores_ativos,auditores_ativos_case\n20260602,10,5\n",
            encoding="utf-8-sig",
        )
        out, info = aplicar_escala_auditores_config_por_fila(
            df,
            datetime(2026, 6, 1),
            settings={
                "fonte_banco_ativa": False,
                "replicacao_aud_data_ref": "20260601",
                "replicacao_aud_data_escala": "20260602",
                "escala_auditores_csv": str(path),
                "meta_produ": 10,
            },
        )
        assert info.auditores_ativos == 10
        assert info.auditores_ativos_case == 5
        g_rows = out[out[COLUNA_CONFIG_WORKFLOW].isin(["G1", "G2"])]
        row31 = out[out[COLUNA_CONFIG_WORKFLOW] == "WF31"].iloc[0]
        assert list(g_rows["amostra_ajustada"]) == [50, 50]
        assert int(row31["amostra_ajustada"]) == 50


def test_aplicar_escala_por_fila_bio_redoc_sem_doc31():
    df = pd.DataFrame(
        {
            COLUNA_CONFIG_WORKFLOW: ["WF Bio", "WF Redoc A", "WF Redoc B"],
            "amostra_diaria": [100, 200, 200],
            COLUNA_CONFIG_FILA: ["Bio", "Redoc", "Redoc"],
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "escala.csv"
        path.write_text(
            "data,auditores_ativos,auditores_ativos_bio,auditores_ativos_redoc\n"
            "20260818,99,4,6\n",
            encoding="utf-8-sig",
        )
        out, info = aplicar_escala_auditores_config_por_fila(
            df,
            datetime(2026, 8, 17),
            settings={
                "fonte_banco_ativa": False,
                "replicacao_aud_data_ref": "20260817",
                "replicacao_aud_data_escala": "20260818",
                "escala_auditores_csv": str(path),
                "meta_produ": 100,
                "meta_produ_bio": 100,
                "meta_produ_redoc": 100,
            },
        )
        assert info.auditores_ativos_bio == 4
        assert info.auditores_ativos_redoc == 6
        bio = out[out[COLUNA_CONFIG_WORKFLOW] == "WF Bio"].iloc[0]
        redoc = out[out[COLUNA_CONFIG_WORKFLOW] == "WF Redoc A"].iloc[0]
        # Bio usa coluna auditores_ativos_bio (4), não auditores_ativos (99)
        assert int(bio["amostra_ajustada"]) == 400
        assert int(redoc["amostra_ajustada"]) == 300
        assert int(bio["amostra_ajustada"]) != 9900


def test_meta_resumo_fila_bio_redoc_auditores():
    from app.bots.replicacao_aud_planning import _meta_resumo_fila

    plano = SimpleNamespace(
        workflow_fila={"WF Bio": "Bio", "WF Redoc": "Redoc"},
        auditores_ativos=99,
        auditores_ativos_case=5,
        csv_paths={},
        pasta_protocolos=None,
    )
    info = InfoCapacidade(
        auditores_ativos=99,
        meta_produ=300,
        capacidade_produtiva=1000,
        soma_amostra_diaria=500,
        fator_capacidade=2.0,
        auditores_ativos_case=5,
        auditores_ativos_bio=4,
        auditores_ativos_redoc=6,
    )
    assert _meta_resumo_fila("WF Bio", plano, info)["Auditores Ativos"] == 4
    assert _meta_resumo_fila("WF Redoc", plano, info)["Auditores Ativos"] == 6


def test_settings_usam_escala_d1():
    assert _settings_usam_escala_d1(
        {"replicacao_config_base": str(PASTA_REPLICACAO_AUD_D1_CONFIG)}
    )
    assert _settings_usam_escala_d1(
        {"replicacao_config_base": "C:/tmp/brflow-auditoria-replic-d1/config"}
    )
    assert not _settings_usam_escala_d1(
        {"replicacao_config_base": "C:/tmp/brflow-auditoria-replic/config"}
    )
    assert not _settings_usam_escala_d1({})


def test_resolver_csv_escala_d1_no_onedrive():
    from app.config import ESCALA_AUDITORES_CSV, ESCALA_AUDITORES_D1_CSV

    if not ESCALA_AUDITORES_D1_CSV.exists():
        return
    resolved = _resolver_csv_escala_auditores(
        {"replicacao_config_base": str(PASTA_REPLICACAO_AUD_D1_CONFIG)}
    )
    assert resolved.resolve() == ESCALA_AUDITORES_D1_CSV.resolve()
    if ESCALA_AUDITORES_CSV.exists():
        assert resolved.resolve() != ESCALA_AUDITORES_CSV.resolve()


def test_data_escala_dia_seguinte_ao_parquet():
    data_ref = datetime(2026, 5, 26)
    data_esc = _resolver_data_escala_replicacao(
        data_ref,
        settings={"replicacao_aud_data_ref": "20260526"},
    )
    assert data_esc == datetime(2026, 5, 27)

    data_esc_custom = _resolver_data_escala_replicacao(
        data_ref,
        settings={"replicacao_aud_data_escala": "20260530"},
    )
    assert data_esc_custom == datetime(2026, 5, 30)


def test_meta_produ_bloco_resumo_excel():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "Config_auditoria.xlsx"
        # Layout real: resumo em A/B; tabela com cabeçalho na linha 1 (colunas C+)
        planilha = pd.DataFrame(
            [
                ["Auditores ativos", 12, "Cliente", None, "Workflow", None, "Amostra diária"],
                ["Meta Produ (diária)", 300, None, None, None, None, None],
                [None, None, "X", None, "WF Teste", None, 10],
            ]
        )
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            planilha.to_excel(writer, index=False, header=False)

        rotulos = {
            _normalizar_rotulo(k): v
            for k, v in _ler_resumo_config_excel(path).items()
        }
        assert rotulos[_normalizar_rotulo("Meta Produ (diária)")] == 300

        meta = _buscar_valor_resumo(
            _ler_resumo_config_excel(path),
            ["Meta Produ (diária)", "meta produ"],
        )
        assert float(meta) == 300.0

        df = carregar_config_auditoria(path)
        assert carregar_meta_produ(df) == 300.0


def main():
    test_formula_capacidade_120()
    test_formula_capacidade_50()
    test_carregar_escala_csv()
    test_carregar_escala_coluna_case()
    test_aplicar_escala_por_fila()
    test_escala_ausente_nao_executa()
    test_data_escala_dia_seguinte_ao_parquet()
    test_meta_produ_bloco_resumo_excel()
    print("OK: testes escala auditores passaram")


if __name__ == "__main__":
    main()
