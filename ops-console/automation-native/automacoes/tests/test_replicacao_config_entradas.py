"""Testes das fontes de config: volumetria + Default.xlsx + Categoria.xlsx."""

import shutil
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.bots.replicacao_aud_planning import (
    COLUNA_CONFIG_CLIENTE,
    COLUNA_CONFIG_WORKFLOW,
    COLUNA_CONFIG_WORKFLOW_D1,
    _eh_status_workflow_d1_inativo,
    _normalizar_workflow,
    _resolver_pendentes_csv_path,
    _resolver_workflow_nome_brflow,
    carregar_chaves_workflow_d1_desabilitados,
    carregar_mapa_workflow_d1,
    montar_config_replicacao,
    resolver_pasta_volumetria_mais_recente,
)
from app.config import COLUNA_CONFIG_STATUS, COLUNA_CONFIG_WORKFLOW_SELENIUM
from app.config import REPLICACAO_AUD_MARCA_PENDENTE


def _criar_fixture_config(base: Path) -> None:
    escala = base / "escala"
    escala.mkdir(parents=True)
    (escala / "escala_auditores.csv").write_text(
        "data,auditores_ativos\n20260602,10\n",
        encoding="utf-8",
    )

    vol_dir = base / "volumetria"
    vol_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "Workflow": ["WF Antigo"],
            "Automáticos": [1],
            "Manuais": [1],
        }
    ).to_excel(vol_dir / "abril26.xlsx", index=False)
    pd.DataFrame(
        {
            "Workflow": ["WF TELA", "WF SEM MAPA"],
            "Cliente": ["Cliente A", "Cliente B BI"],
            "Automáticos": [100, 50],
            "Manuais": [20, 10],
        }
    ).to_excel(vol_dir / "maio26.xlsx", index=False)
    time.sleep(0.05)
    (vol_dir / "maio26.xlsx").touch()

    pd.DataFrame(
        {
            "Cliente": ["Cliente A"],
            "Segmento": ["Seg A"],
            "Categoria": ["Cat A"],
        }
    ).to_excel(base / "Categoria.xlsx", index=False)

    default = base / "Default.xlsx"
    with pd.ExcelWriter(default, engine="openpyxl") as writer:
        pd.DataFrame(
            {
                "Workflow": ["WF Tela"],
                "Cliente": ["Cliente A"],
                "Workflow d-1": ["WF Parquet"],
                "Workflow - selenium": ["Nome BRFlow na tela"],
            }
        ).to_excel(writer, sheet_name="Workflow d1", index=False)
        # Calculadora Padrão simplificada: meta + uma linha com amostra materializada
        # Layout alinhado ao Default real: cabeçalhos na linha 2 (Excel), dados na 3+
        calc = pd.DataFrame(
            [
                ["Auditores ativos", 10, None, "Cliente", "Workflow", "Categoria", "Automático", "Manual", "Total", "Amostra total", "Amostra diária", "Amostra Conf. Prod"],
                ["Dias úteis", 24, None, None, None, None, None, None, None, None, None, None],
                ["Meta Produ (diária)", 300, None, None, None, None, None, None, None, None, None, None],
                ["Nível de confiança", 0.99, None, None, None, None, None, None, None, None, None, None],
                ["Margem de erro (high)", 0.0115, None, None, None, None, None, None, None, None, None, None],
                ["Margem de erro (low/mid)", 0.0178, None, None, None, None, None, None, None, None, None, None],
            ]
        )
        calc.to_excel(writer, sheet_name="Calculadora Padrão", index=False, header=False)


def _with_fixture(run):
    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp)
        _criar_fixture_config(base)
        run(base)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_arquivo_volumetria_mais_recente():
    def _run(base):
        fonte = resolver_pasta_volumetria_mais_recente(
            settings={"replicacao_config_base": str(base)}
        )
        assert fonte.name == "maio26.xlsx"

    _with_fixture(_run)


def test_montar_config_merge_completo():
    def _run(base):
        df, warnings, pasta = montar_config_replicacao(
            settings={"replicacao_config_base": str(base)}
        )
        assert "maio26" in pasta
        assert len(df) == 1
        row = df.iloc[0]
        assert row[COLUNA_CONFIG_WORKFLOW] == "WF Tela"
        assert _normalizar_workflow("WF TELA") == _normalizar_workflow("WF Tela")
        assert row[COLUNA_CONFIG_WORKFLOW_D1] == "WF Parquet"
        assert row[COLUNA_CONFIG_WORKFLOW_SELENIUM] == "Nome BRFlow na tela"
        assert row[COLUNA_CONFIG_CLIENTE] == "Cliente A"
        assert int(row["amostra_diaria"]) > 0
        assert any("PENDENTE CONFIG" in w for w in warnings)
        assert "WF SEM MAPA" in df.attrs.get("workflows_pendentes_config", [])

    _with_fixture(_run)


def test_sincroniza_workflow_bi_no_default():
    def _run(base):
        settings = {"replicacao_config_base": str(base)}
        df, _, _ = montar_config_replicacao(settings=settings)
        default = base / "Default.xlsx"
        raw = pd.read_excel(default, sheet_name="Workflow d1", engine="openpyxl")
        pend = raw[raw["Workflow"].astype(str).str.upper() == "WF SEM MAPA"]
        assert len(pend) == 1
        assert pend.iloc[0]["Cliente"] == "Cliente B BI"
        assert REPLICACAO_AUD_MARCA_PENDENTE in str(pend.iloc[0]["Workflow d-1"])
        assert "WF SEM MAPA" in df.attrs.get("workflows_pendentes_novos", [])
        csv_path = _resolver_pendentes_csv_path(settings)
        assert csv_path.parent == base
        assert csv_path.exists()
        assert "WF SEM MAPA" in csv_path.read_text(encoding="utf-8-sig")

    _with_fixture(_run)


def test_nao_duplica_linha_pendente():
    def _run(base):
        settings = {"replicacao_config_base": str(base)}
        montar_config_replicacao(settings=settings)
        df2, warnings2, _ = montar_config_replicacao(settings=settings)
        raw = pd.read_excel(base / "Default.xlsx", sheet_name="Workflow d1", engine="openpyxl")
        assert len(raw[raw["Workflow"].astype(str).str.upper() == "WF SEM MAPA"]) == 1
        assert any("já registrado" in w for w in warnings2)
        assert "WF SEM MAPA" in df2.attrs.get("workflows_pendentes_existentes", [])
        assert "WF SEM MAPA" not in df2.attrs.get("workflows_pendentes_novos", [])

    _with_fixture(_run)


def test_workflow_pendente_configurado_entra_no_plano():
    def _run(base):
        settings = {"replicacao_config_base": str(base)}
        montar_config_replicacao(settings=settings)
        default = base / "Default.xlsx"
        from openpyxl import load_workbook

        wb = load_workbook(default)
        ws = wb["Workflow d1"]
        status_col = 4
        for col in range(1, ws.max_column + 1):
            if str(ws.cell(1, col).value or "").strip().lower() == "status":
                status_col = col
                break
        for row in range(2, ws.max_row + 1):
            if str(ws.cell(row, 1).value or "").strip().upper() == "WF SEM MAPA":
                ws.cell(row, 2, "Cliente A")
                ws.cell(row, 3, "WF Parquet Sem Mapa")
                break
        wb.save(default)
        wb.close()

        df, warnings, _ = montar_config_replicacao(settings=settings)
        wfs = df[COLUNA_CONFIG_WORKFLOW].tolist()
        assert "WF SEM MAPA" in wfs or any("WF Sem Mapa" == w for w in wfs)
        assert not any("WF SEM MAPA" in w and "registrado na aba" in w for w in warnings)

    _with_fixture(_run)


def test_status_pendente_nao_bloqueia_merge():
    def _run(base):
        settings = {"replicacao_config_base": str(base)}
        montar_config_replicacao(settings=settings)
        default = base / "Default.xlsx"
        from openpyxl import load_workbook

        wb = load_workbook(default)
        ws = wb["Workflow d1"]
        status_col = 4
        for col in range(1, ws.max_column + 1):
            if str(ws.cell(1, col).value or "").strip().lower() == "status":
                status_col = col
                break
        for row in range(2, ws.max_row + 1):
            if str(ws.cell(row, 1).value or "").strip().upper() == "WF SEM MAPA":
                ws.cell(row, 2, "Cliente A")
                ws.cell(row, 3, "WF Parquet Sem Mapa")
                ws.cell(row, status_col, REPLICACAO_AUD_MARCA_PENDENTE)
                break
        wb.save(default)
        wb.close()

        df, _, _ = montar_config_replicacao(settings=settings)
        assert len(df) == 2

    _with_fixture(_run)


def test_amostras_hibridas_materializado_e_calculado():
    def _run(base):
        default = base / "Default.xlsx"
        with pd.ExcelWriter(default, engine="openpyxl", mode="a", if_sheet_exists="overlay") as writer:
            calc_extra = pd.DataFrame(
                [[None, None, None, "Cliente A", "WF Tela", "high", 500, 500, 1000, 120, 12, 12]]
            )
            calc_extra.to_excel(writer, sheet_name="Calculadora Padrão", startrow=6, index=False, header=False)
        settings = {"replicacao_config_base": str(base)}
        montar_config_replicacao(settings=settings)
        from openpyxl import load_workbook

        wb = load_workbook(default)
        ws = wb["Workflow d1"]
        for row in range(2, ws.max_row + 1):
            if str(ws.cell(row, 1).value or "").strip().upper() == "WF SEM MAPA":
                ws.cell(row, 2, "Cliente A")
                ws.cell(row, 3, "WF Parquet Sem Mapa")
                break
        wb.save(default)
        wb.close()

        df, _, _ = montar_config_replicacao(settings=settings)
        assert len(df) == 2
        for _, row in df.iterrows():
            assert int(row["amostra_diaria"]) > 0

    _with_fixture(_run)


def test_workflow_selenium_fallback_para_workflow():
    def _run(base):
        default = base / "Default.xlsx"
        with pd.ExcelWriter(default, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            pd.DataFrame(
                {
                    "Workflow": ["WF Tela"],
                    "Cliente": ["Cliente A"],
                    "Workflow d-1": ["WF Parquet"],
                }
            ).to_excel(writer, sheet_name="Workflow d1", index=False)
        mapa = carregar_mapa_workflow_d1(default, settings={"replicacao_config_base": str(base)})
        row = mapa.iloc[0]
        assert row[COLUNA_CONFIG_WORKFLOW_SELENIUM] == "WF Tela"
        assert _resolver_workflow_nome_brflow(row) == "WF Tela"

    _with_fixture(_run)


def test_workflow_d1_fallback_na_aba():
    def _run(base):
        default = base / "Default.xlsx"
        with pd.ExcelWriter(default, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            pd.DataFrame(
                {
                    "Workflow": ["WF Tela"],
                    "Cliente": ["Cliente A"],
                    "Workflow d-1": [""],
                }
            ).to_excel(writer, sheet_name="Workflow d1", index=False)
        df, _, _ = montar_config_replicacao(settings={"replicacao_config_base": str(base)})
        assert df.iloc[0][COLUNA_CONFIG_WORKFLOW_D1] == "WF Tela"

    _with_fixture(_run)


def test_volumetria_lê_coluna_cliente():
    def _run(base):
        from app.bots.replicacao_aud_planning import carregar_volumetria, COLUNA_VOLUMETRIA_CLIENTE

        vol_dir = base / "volumetria"
        df = carregar_volumetria(vol_dir / "maio26.xlsx")
        assert COLUNA_VOLUMETRIA_CLIENTE in df.columns
        row = df[df["Workflow"].str.upper() == "WF SEM MAPA"].iloc[0]
        assert row[COLUNA_VOLUMETRIA_CLIENTE] == "Cliente B BI"

    _with_fixture(_run)


def test_volumetria_ignora_linha_total():
    def _run(base):
        from app.bots.replicacao_aud_planning import carregar_volumetria

        vol_dir = base / "volumetria"
        extra = vol_dir / "com_total.xlsx"
        pd.DataFrame(
            {
                "Workflow": ["WF OK", "Total", "Filtros aplicados: teste"],
                "Automáticos": [10, 999, None],
                "Manuais": [5, 888, None],
            }
        ).to_excel(extra, index=False)
        extra.touch()

        df = carregar_volumetria(extra)
        assert len(df) == 1
        assert df.iloc[0]["Workflow"] == "WF OK"

    _with_fixture(_run)


def test_eh_status_workflow_d1_inativo():
    assert _eh_status_workflow_d1_inativo(False) is True
    assert _eh_status_workflow_d1_inativo("false") is True
    assert _eh_status_workflow_d1_inativo("False") is True
    assert _eh_status_workflow_d1_inativo(0) is True
    assert _eh_status_workflow_d1_inativo("no") is True
    assert _eh_status_workflow_d1_inativo(True) is False
    assert _eh_status_workflow_d1_inativo("True") is False
    assert _eh_status_workflow_d1_inativo("") is False
    assert _eh_status_workflow_d1_inativo(None) is False


def test_mapa_exclui_status_false():
    def _run(base):
        default = base / "Default.xlsx"
        with pd.ExcelWriter(default, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            pd.DataFrame(
                {
                    "Workflow": ["WF Tela", "WF Desligado"],
                    "Cliente": ["Cliente A", "Cliente A"],
                    "Workflow d-1": ["WF Parquet", "WF Parquet Off"],
                    "Workflow - selenium": ["Nome BRFlow", "Nome Off"],
                    "Status": [True, False],
                }
            ).to_excel(writer, sheet_name="Workflow d1", index=False)

        mapa = carregar_mapa_workflow_d1(
            default, settings={"replicacao_config_base": str(base)}
        )
        chaves = set(mapa[COLUNA_CONFIG_WORKFLOW].tolist())
        assert "WF Desligado" not in chaves
        assert any("Tela" in w or "TELA" in w.upper() for w in chaves)

        desabilitados = carregar_chaves_workflow_d1_desabilitados(
            default, settings={"replicacao_config_base": str(base)}
        )
        assert _normalizar_workflow("WF Desligado") in desabilitados

    _with_fixture(_run)


def test_status_vazio_permanece_ativo_no_mapa():
    def _run(base):
        default = base / "Default.xlsx"
        with pd.ExcelWriter(default, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            pd.DataFrame(
                {
                    "Workflow": ["WF Tela", "WF Sem Status"],
                    "Cliente": ["Cliente A", "Cliente A"],
                    "Workflow d-1": ["WF Parquet", "WF Parquet 2"],
                    "Status": [True, None],
                }
            ).to_excel(writer, sheet_name="Workflow d1", index=False)

        mapa = carregar_mapa_workflow_d1(
            default, settings={"replicacao_config_base": str(base)}
        )
        assert len(mapa) == 2

    _with_fixture(_run)


def test_montar_config_ignora_volumetria_com_status_false():
    def _run(base):
        vol_dir = base / "volumetria"
        pd.DataFrame(
            {
                "Workflow": ["WF TELA", "WF OFF"],
                "Cliente": ["Cliente A", "Cliente A"],
                "Automáticos": [100, 40],
                "Manuais": [20, 10],
            }
        ).to_excel(vol_dir / "junho26.xlsx", index=False)
        time.sleep(0.05)
        (vol_dir / "junho26.xlsx").touch()

        default = base / "Default.xlsx"
        with pd.ExcelWriter(default, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            pd.DataFrame(
                {
                    "Workflow": ["WF Tela", "WF Off"],
                    "Cliente": ["Cliente A", "Cliente A"],
                    "Workflow d-1": ["WF Parquet", "WF Parquet Off"],
                    "Workflow - selenium": ["Nome BRFlow", "Nome Off"],
                    "Status": [True, False],
                }
            ).to_excel(writer, sheet_name="Workflow d1", index=False)
            calc = pd.DataFrame(
                [
                    ["Auditores ativos", 10, None, "Cliente", "Workflow", "Categoria", "Automático", "Manual", "Total", "Amostra total", "Amostra diária", "Amostra Conf. Prod"],
                    ["Dias úteis", 24, None, None, None, None, None, None, None, None, None, None],
                    ["Meta Produ (diária)", 300, None, None, None, None, None, None, None, None, None, None],
                    [None, None, None, "Cliente A", "WF Tela", "Cat A", 100, 20, 120, 120, 10, 10],
                    [None, None, None, "Cliente A", "WF Off", "Cat A", 40, 10, 50, 50, 5, 5],
                ]
            )
            calc.to_excel(writer, sheet_name="Calculadora Padrão", index=False, header=False)

        df, warnings, _ = montar_config_replicacao(
            settings={"replicacao_config_base": str(base), "usar_escala_auditores": False}
        )
        workflows = df[COLUNA_CONFIG_WORKFLOW].tolist()
        assert len(df) == 1
        assert "Off" not in " ".join(workflows)
        assert any("Status=False" in w for w in warnings)
        assert not any("PENDENTE CONFIG" in w and "WF OFF" in w.upper() for w in warnings)

    _with_fixture(_run)


def test_coluna_status_ausente_regressao():
    def _run(base):
        df, _, _ = montar_config_replicacao(
            settings={"replicacao_config_base": str(base), "usar_escala_auditores": False}
        )
        assert len(df) == 1
        assert COLUNA_CONFIG_STATUS not in df.columns

    _with_fixture(_run)


def main():
    test_arquivo_volumetria_mais_recente()
    test_volumetria_lê_coluna_cliente()
    test_volumetria_ignora_linha_total()
    test_montar_config_merge_completo()
    test_sincroniza_workflow_bi_no_default()
    test_nao_duplica_linha_pendente()
    test_workflow_pendente_configurado_entra_no_plano()
    test_status_pendente_nao_bloqueia_merge()
    test_amostras_hibridas_materializado_e_calculado()
    test_workflow_d1_fallback_na_aba()
    test_workflow_selenium_fallback_para_workflow()
    test_eh_status_workflow_d1_inativo()
    test_mapa_exclui_status_false()
    test_status_vazio_permanece_ativo_no_mapa()
    test_montar_config_ignora_volumetria_com_status_false()
    test_coluna_status_ausente_regressao()
    print("OK: testes replicacao config entradas passaram")


if __name__ == "__main__":
    main()
