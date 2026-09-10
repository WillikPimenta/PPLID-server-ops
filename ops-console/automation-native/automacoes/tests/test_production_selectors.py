"""Validação dos seletores e helpers de navegação do bot production."""
from unittest.mock import MagicMock, patch

from app.bots.bot_production import _clicar_menu_produtividade
from app.config.selectors import brflow


def test_b_producao_selectors():
    assert brflow.B_produção == '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[1]/ul/li[11]/a'
    assert brflow.B_produção_ascii == "//a[@data-ascii='produtividade']"
    assert brflow.B_M_table == '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[1]/ul/li[10]/a'
    assert brflow.B_P_CSV == '//*[@id="layout_layout2_panel_main"]//label[@for="formato-csv"]'
    assert brflow.B_P_CSV_input == "formato-csv"
    assert brflow.B_P_pesquisar == '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/div[2]/button'
    assert brflow.B_P_pesquisar_class == '//*[@id="layout_layout2_panel_main"]//button[contains(@class,"btn-pesquisar")]'


def test_clicar_pesquisar_produtividade_usa_seletor_layout():
    drv = MagicMock()
    drv.current_url = "https://www.brflow.com.br/BrFlow/index/index"
    wait = MagicMock()
    wait_short = MagicMock()

    with patch("app.bots.bot_production.click_element") as mock_click:
        from app.bots.bot_production import _clicar_pesquisar_produtividade

        _clicar_pesquisar_produtividade(drv, wait, wait_short)
        mock_click.assert_called_once_with(drv, "xpath", brflow.B_P_pesquisar)


def test_clicar_exportar_csv_usa_input_id():
    drv = MagicMock()
    drv.current_url = "https://www.brflow.com.br/BrFlow/index/index"
    wait = MagicMock()
    wait_short = MagicMock()

    with patch("app.bots.bot_production.click_element") as mock_click:
        from app.bots.bot_production import _clicar_exportar_csv

        _clicar_exportar_csv(drv, wait, wait_short)
        mock_click.assert_called_once_with(drv, "id", brflow.B_P_CSV_input)


def test_clicar_menu_produtividade_usa_seletor_menu():
    drv = MagicMock()
    drv.current_url = "https://www.brflow.com.br/BrFlow/index/index"
    wait = MagicMock()
    wait_short = MagicMock()

    with patch("app.bots.bot_production._wait_overlay_invisible"), patch(
        "app.bots.bot_production.click_element"
    ) as mock_click:
        _clicar_menu_produtividade(drv, wait, wait_short)
        mock_click.assert_called_once_with(drv, "xpath", brflow.B_produção)
