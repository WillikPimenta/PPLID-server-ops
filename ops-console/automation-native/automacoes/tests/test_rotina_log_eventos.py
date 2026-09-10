# -*- coding: utf-8 -*-
"""Testes offline do Log Eventos D-1 (download único do dia + matrícula via Relatórios)."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch


@patch("app.bots.rotina.tasks.confer._limpar_arquivos_temporarios_confer")
@patch("app.bots.rotina.tasks.confer._consolidar_csv_sessoes_confer_d1")
@patch("app.bots.rotina.tasks.confer._gerar_csv_sessoes_por_evento")
@patch("app.bots.rotina.tasks.confer._renomear_download_com_sufixo_confer")
@patch("app.bots.rotina.tasks.confer.baixar_com_retry_seguro_confer")
@patch("app.bots.rotina.tasks.confer._resolver_arquivo_confer_prod_d1")
@patch("app.bots.rotina.tasks.confer._recuperar_menu_confer_se_tela_login_rotina", return_value=False)
@patch("app.bots.rotina.tasks.confer._fechar_calendario_confer_rotina")
@patch("app.bots.rotina.tasks.confer.send_keys_to_element")
@patch("app.bots.rotina.tasks.confer._click_com_js")
@patch("app.bots.rotina.tasks.confer.click_element")
@patch("app.bots.rotina.tasks.confer.WebDriverWait")
@patch("app.bots.rotina.tasks.confer._obter_data_base_execucao")
def test_log_eventos_rotina_download_unico_dia_com_mapa_matricula(
    mock_data_base,
    mock_wait_cls,
    mock_click,
    mock_js,
    mock_send,
    mock_fecha,
    mock_recupera,
    mock_resolver_prod,
    mock_baixar,
    mock_renomear,
    mock_gerar,
    mock_consolidar,
    mock_limpar,
):
    from app.bots.rotina.tasks import confer

    mock_data_base.return_value = datetime(2026, 6, 19)
    wait_inst = MagicMock()
    wait_inst.until.return_value = MagicMock()
    mock_wait_cls.return_value = wait_inst

    with TemporaryDirectory() as tmp:
        prod_path = Path(tmp) / "confer-prod-bruto_20260618.parquet"
        bruto = Path(tmp) / "log_bruto.csv"
        sessoes = Path(tmp) / "log_sessoes.csv"
        consolidado = Path(tmp) / "confer-monitor-tratado_18062026.parquet"
        prod_path.write_bytes(b"x")
        bruto.write_text("a;b\n1;2\n", encoding="utf-8")
        sessoes.write_text("matricula;Evento\nc91123a;Logout\n", encoding="utf-8")
        consolidado.write_bytes(b"y")

        mock_resolver_prod.return_value = prod_path
        mock_baixar.return_value = bruto
        mock_renomear.return_value = bruto
        mock_gerar.return_value = sessoes
        mock_consolidar.return_value = consolidado

        drv = MagicMock()
        resultado = confer.log_eventos(drv)

        assert resultado == [str(consolidado)]
        mock_baixar.assert_called_once()
        assert mock_baixar.call_args.kwargs.get("descricao") == "download do Log Eventos do dia"
        mock_gerar.assert_called_once()
        assert mock_gerar.call_args.kwargs.get("matricula") is None
        assert mock_gerar.call_args.kwargs.get("csv_prod_confer") == prod_path
        mock_consolidar.assert_called_once()


def test_resolver_arquivo_confer_prod_d1(monkeypatch):
    from app.bots.rotina.tasks import confer

    with TemporaryDirectory() as tmp:
        pasta = Path(tmp)
        alvo = pasta / "confer-prod-bruto_20260618.parquet"
        alvo.write_bytes(b"ok")
        monkeypatch.setattr(confer, "PASTA_PRODUCAO_CONFER", pasta)
        monkeypatch.setattr(confer, "PREFIXO_CONF_BRUTO", "confer-prod-bruto_")
        monkeypatch.setattr(
            confer,
            "_obter_data_base_execucao",
            lambda: datetime(2026, 6, 19),
        )
        assert confer._resolver_arquivo_confer_prod_d1() == alvo
