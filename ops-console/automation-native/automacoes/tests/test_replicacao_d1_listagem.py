# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, patch

from app.bots.replicacao_d1.selenium.listagem import (
    contar_linhas_ativas_regra,
    extrair_texto_celula_replicacao,
    listagem_pronta_para_ler,
    normalizar_regra_brflow,
    parse_texto_paginador,
    resolver_direcao_paginacao,
    resumir_linhas_por_workflow,
    workflow_tem_linha_ativa_brflow,
    eh_workflow_apenas_inativo_brflow,
    _linha_aceita,
)
from app.bots.replicacao_d1.settings import (
    paginacao_max_paginas,
    paginacao_tamanho,
    paginacao_varrer_todas,
)


class _FakeTd:
    def __init__(self, text="", attrs=None):
        self._text = text
        self._attrs = attrs or {}

    @property
    def text(self):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)


def test_extrair_texto_celula_atributo_data_html():
    td = _FakeTd(
        text="",
        attrs={"data-html-nom_workflow_origem": "Risk Manager - PICPAY"},
    )
    assert extrair_texto_celula_replicacao(td) == "Risk Manager - PICPAY"


def test_listagem_estabiliza_quando_atinge_alvo_paginador():
    _, repeticoes, pronta = listagem_pronta_para_ler(799, 800, 0, 2, 1, 800)
    assert repeticoes == 0
    assert pronta is False
    _, repeticoes, pronta = listagem_pronta_para_ler(800, 800, 0, 2, 1, 800)
    assert repeticoes == 1
    assert pronta is False
    _, repeticoes, pronta = listagem_pronta_para_ler(800, 800, 1, 2, 1, 800)
    assert repeticoes == 2
    assert pronta is True


def test_parse_texto_paginador_multiplas_paginas():
    info = parse_texto_paginador("850 itens - Pág. 1 de 2")
    assert info["total_itens"] == 850
    assert info["pagina_atual"] == 1
    assert info["total_paginas"] == 2


def test_parse_texto_paginador_uma_pagina():
    info = parse_texto_paginador("120 itens - Pág. 1 de 1")
    assert info["total_itens"] == 120
    assert info["total_paginas"] == 1


def test_paginacao_settings_defaults():
    assert paginacao_tamanho({}) == 500
    assert paginacao_max_paginas({}) == 20
    assert paginacao_varrer_todas({}) is True
    assert paginacao_tamanho({"replicacao_paginacao_tamanho": 250}) == 250


def test_resolver_direcao_paginacao_adjacente():
    assert resolver_direcao_paginacao(1, 1) == "ja_na_pagina"
    assert resolver_direcao_paginacao(1, 2) == "proxima"
    assert resolver_direcao_paginacao(2, 1) == "anterior"
    assert resolver_direcao_paginacao(1, 5) == "indireta"


def test_resumir_duplicata_ativo_inativo():
    linhas = [
        {"_wf_key": "wf x", "workflow": "WF X", "situacao": "INATIVO"},
        {"_wf_key": "wf x", "workflow": "WF X", "situacao": "ATIVO"},
    ]
    resumo = resumir_linhas_por_workflow(linhas)
    assert resumo["wf x"]["tem_ativo"] is True
    assert resumo["wf x"]["tem_inativo"] is True
    assert workflow_tem_linha_ativa_brflow(resumo, "WF X") is True
    assert eh_workflow_apenas_inativo_brflow(resumo, "WF X") is False


def test_contar_linhas_ativas_regra_redoc():
    linhas = [
        {
            "_wf_key": "wf x",
            "workflow": "WF X",
            "situacao": "ATIVO",
            "regra": "Regra A",
            "_regra_key": normalizar_regra_brflow("Regra A"),
        },
        {
            "_wf_key": "wf x",
            "workflow": "WF X",
            "situacao": "ATIVO",
            "regra": "Regra B",
            "_regra_key": normalizar_regra_brflow("Regra B"),
        },
    ]
    assert contar_linhas_ativas_regra(linhas, "wf x", "Regra A") == 1
    assert _linha_aceita(linhas[0], "wf x", apenas_ativo=True, nome_regra="Regra A")
    assert not _linha_aceita(linhas[1], "wf x", apenas_ativo=True, nome_regra="Regra A")


def test_contar_linhas_ativas_regra_usa_indice_quando_linhas_vazias():
    from app.bots.replicacao_d1.selenium.listagem import IndiceListagemBrflow, _atualizar_entrada_indice

    indice = IndiceListagemBrflow()
    _atualizar_entrada_indice(
        indice,
        {
            "_wf_key": "wf redoc",
            "workflow": "WF Redoc",
            "situacao": "ATIVO",
            "regra": "Regra A",
            "_regra_key": normalizar_regra_brflow("Regra A"),
        },
        1,
    )
    assert contar_linhas_ativas_regra([], "wf redoc", "Regra A", indice=indice) == 1
    assert contar_linhas_ativas_regra([], "wf redoc", "Regra B", indice=indice) == 0


def test_regra_brflow_bate_por_substring():
    from app.bots.replicacao_d1.selenium.listagem import (
        regra_brflow_bate,
        regra_brflow_bate_estrita,
        suffix_regra_distintivo,
    )

    cadastro_bolsa = (
        "[AUDITORIA REDOC] - 0368 - A2FBR LTDA + DOC DIGITAL BETS - A2FBR LTDA + "
        "DOC DIGITAL BETS - BOLSA DE APOSTA - LVS"
    )
    cadastro_fulltbet = (
        "[AUDITORIA REDOC] - 0368 - A2FBR LTDA + DOC DIGITAL BETS - A2FBR LTDA + "
        "DOC DIGITAL BETS - FULLTBET - LVS"
    )
    listagem_bolsa = (
        "[AUDITORIA REDOC] - 0368 - A2FBR LTDA + DOC DIGITAL BETS - A2FBR LTDA + "
        "DOC DIGITAL BETS - BOLSA DE APOSTA - LVS"
    )
    listagem_pinnacle = (
        "[AUDITORIA REDOC] - 0368 - A2FBR LTDA + DOC DIGITAL BETS - A2FBR LTDA + "
        "DOC DIGITAL BETS - PINNACLE - LVS"
    )
    assert regra_brflow_bate(cadastro_bolsa, listagem_bolsa)
    assert not regra_brflow_bate_estrita(cadastro_bolsa, listagem_pinnacle)
    assert not regra_brflow_bate_estrita(cadastro_fulltbet, listagem_bolsa)
    assert suffix_regra_distintivo(cadastro_bolsa) == normalizar_regra_brflow(
        "DOC DIGITAL BETS - BOLSA DE APOSTA - LVS"
    )
    assert contar_linhas_ativas_regra(
        [
            {
                "_wf_key": "wf redoc",
                "workflow": "DOC DIGITAL BETS - A2FBR LTDA",
                "situacao": "DESCONHECIDO",
                "regra": listagem_bolsa,
                "_regra_key": normalizar_regra_brflow(listagem_bolsa),
            },
            {
                "_wf_key": "wf redoc",
                "workflow": "DOC DIGITAL BETS - A2FBR LTDA",
                "situacao": "DESCONHECIDO",
                "regra": listagem_pinnacle,
                "_regra_key": normalizar_regra_brflow(listagem_pinnacle),
            },
        ],
        "wf redoc",
        cadastro_bolsa,
    ) == 1
    assert not _linha_aceita(
        {
            "_wf_key": "wf redoc",
            "situacao": "DESCONHECIDO",
            "regra": listagem_pinnacle,
            "_regra_key": normalizar_regra_brflow(listagem_pinnacle),
        },
        "wf redoc",
        apenas_ativo=True,
        nome_regra=cadastro_bolsa,
    )


def test_regra_g_auditoria_nao_bate_redoc_mesmo_workflow_origem():
    from app.bots.replicacao_d1.selenium.listagem import regra_brflow_bate

    cadastro_g = (
        "[G AUDITORIA] Auditoria de Acompanhamento | 0308 - Aposta Ganha Loterias LTDA + "
        "DOC DIGITAL BETS - APOSTA GANHA"
    )
    listagem_redoc_receba = (
        "[AUDITORIA REDOC] - 0308 - Aposta Ganha Loterias LTDA + DOC DIGITAL BETS - APOSTA GANHA + "
        "DOC DIGITAL BETS - APOSTA GANHA - RECEBA"
    )
    listagem_g = cadastro_g

    assert regra_brflow_bate(cadastro_g, listagem_g)
    assert not regra_brflow_bate(cadastro_g, listagem_redoc_receba)
    assert contar_linhas_ativas_regra(
        [
            {
                "_wf_key": "bet - aposta ganha - doc digital bets - aposta ganha",
                "workflow": "BET - APOSTA GANHA - DOC DIGITAL BETS - APOSTA GANHA",
                "situacao": "DESCONHECIDO",
                "regra": listagem_redoc_receba,
                "_regra_key": normalizar_regra_brflow(listagem_redoc_receba),
            },
            {
                "_wf_key": "bet - aposta ganha - doc digital bets - aposta ganha",
                "workflow": "BET - APOSTA GANHA - DOC DIGITAL BETS - APOSTA GANHA",
                "situacao": "DESCONHECIDO",
                "regra": listagem_g,
                "_regra_key": normalizar_regra_brflow(listagem_g),
            },
        ],
        "bet - aposta ganha - doc digital bets - aposta ganha",
        cadastro_g,
    ) == 1
    assert _linha_aceita(
        {
            "_wf_key": "bet - aposta ganha - doc digital bets - aposta ganha",
            "situacao": "DESCONHECIDO",
            "regra": listagem_g,
            "_regra_key": normalizar_regra_brflow(listagem_g),
        },
        "bet - aposta ganha - doc digital bets - aposta ganha",
        apenas_ativo=True,
        nome_regra=cadastro_g,
    )
    assert not _linha_aceita(
        {
            "_wf_key": "bet - aposta ganha - doc digital bets - aposta ganha",
            "situacao": "DESCONHECIDO",
            "regra": listagem_redoc_receba,
            "_regra_key": normalizar_regra_brflow(listagem_redoc_receba),
        },
        "bet - aposta ganha - doc digital bets - aposta ganha",
        apenas_ativo=True,
        nome_regra=cadastro_g,
    )


def test_resolver_pagina_indice_por_regra():
    from app.bots.replicacao_d1.selenium.listagem import (
        IndiceListagemBrflow,
        _atualizar_entrada_indice,
        resolver_pagina_indice_por_regra,
    )

    indice = IndiceListagemBrflow()
    regra_bolsa = (
        "[AUDITORIA REDOC] - 0368 - A2FBR LTDA + DOC DIGITAL BETS - A2FBR LTDA + "
        "DOC DIGITAL BETS - BOLSA DE APOSTA - LVS"
    )
    regra_full = (
        "[AUDITORIA REDOC] - 0368 - A2FBR LTDA + DOC DIGITAL BETS - A2FBR LTDA + "
        "DOC DIGITAL BETS - FULLTBET - LVS"
    )
    _atualizar_entrada_indice(
        indice,
        {
            "_wf_key": "doc digital bets - a2fbr ltda",
            "workflow": "DOC DIGITAL BETS - A2FBR LTDA",
            "situacao": "DESCONHECIDO",
            "regra": regra_bolsa,
            "_regra_key": normalizar_regra_brflow(regra_bolsa),
        },
        1,
    )
    _atualizar_entrada_indice(
        indice,
        {
            "_wf_key": "doc digital bets - a2fbr ltda",
            "workflow": "DOC DIGITAL BETS - A2FBR LTDA",
            "situacao": "DESCONHECIDO",
            "regra": regra_full,
            "_regra_key": normalizar_regra_brflow(regra_full),
        },
        2,
    )
    assert (
        resolver_pagina_indice_por_regra(
            indice, "doc digital bets - a2fbr ltda", regra_bolsa
        )
        == 1
    )
    assert (
        resolver_pagina_indice_por_regra(
            indice, "doc digital bets - a2fbr ltda", regra_full
        )
        == 2
    )


def test_situacao_desconhecido_conta_como_ativa():
    from app.bots.replicacao_d1.selenium.listagem import situacao_conta_como_ativa

    assert situacao_conta_como_ativa("ATIVO")
    assert situacao_conta_como_ativa("DESCONHECIDO")
    assert not situacao_conta_como_ativa("INATIVO")
    linhas = [
        {
            "_wf_key": "wf x",
            "workflow": "WF X",
            "situacao": "DESCONHECIDO",
            "regra": "Regra A",
            "_regra_key": normalizar_regra_brflow("Regra A"),
        }
    ]
    assert _linha_aceita(linhas[0], "wf x", apenas_ativo=True, nome_regra="Regra A")


def test_editar_workflow_na_pagina_atual_filtra_por_regra():
    from app.bots.replicacao_d1.selenium.listagem import _editar_workflow_na_pagina_atual

    driver = MagicMock()
    tr_regra_a = MagicMock()
    tr_regra_b = MagicMock()
    linhas_dom = [tr_regra_a, tr_regra_b]
    items = [
        {
            "_wf_key": "wf g brflow",
            "workflow": "WF G BRFlow",
            "situacao": "ATIVO",
            "regra": "Regra A",
            "_regra_key": normalizar_regra_brflow("Regra A"),
        },
        {
            "_wf_key": "wf g brflow",
            "workflow": "WF G BRFlow",
            "situacao": "ATIVO",
            "regra": "Regra B",
            "_regra_key": normalizar_regra_brflow("Regra B"),
        },
    ]

    with patch(
        "app.bots.replicacao_d1.selenium.listagem.iterar_linhas_listagem_replicacao",
        return_value=linhas_dom,
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.classificar_linha_replicacao",
        side_effect=items,
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem._clicar_editar_linha"
    ) as clicar_linha:
        found = _editar_workflow_na_pagina_atual(
            driver,
            "WF G BRFlow",
            apenas_ativo=True,
            nome_regra="Regra A",
        )

    assert found is True
    clicar_linha.assert_called_once_with(
        driver,
        tr_regra_a,
        "WF G BRFlow",
        nome_regra="Regra A",
    )


def test_clicar_editar_por_indice_e_regra_navega_pagina_correta():
    from app.bots.replicacao_d1.selenium.listagem import (
        IndiceListagemBrflow,
        _atualizar_entrada_indice,
        clicar_editar_por_workflow,
    )

    driver = MagicMock()
    indice = IndiceListagemBrflow()
    _atualizar_entrada_indice(
        indice,
        {
            "_wf_key": "wf alvo",
            "workflow": "WF Alvo",
            "situacao": "ATIVO",
            "regra": "Regra Especial",
            "_regra_key": normalizar_regra_brflow("Regra Especial"),
        },
        3,
    )

    with patch(
        "app.bots.replicacao_d1.selenium.listagem.WebDriverWait"
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_info_paginador",
        return_value={"pagina_atual": 1, "total_paginas": 3},
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
            nome_regra="Regra Especial",
        )

    ir_pagina.assert_called_once_with(driver, 3, settings=None)
    editar_pagina.assert_called_once()
    assert editar_pagina.call_args.kwargs["nome_regra"] == "Regra Especial"


def test_clicar_editar_index_miss_pula_scan_pagina_atual():
    from app.bots.replicacao_d1.selenium.listagem import (
        IndiceListagemBrflow,
        clicar_editar_por_workflow,
    )

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
        "app.bots.replicacao_d1.selenium.listagem.garantir_paginacao_listagem_replicacao"
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_info_paginador",
        return_value={"pagina_atual": 2, "total_paginas": 2},
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem._editar_workflow_na_pagina_atual",
        return_value=False,
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.iterar_linhas_listagem_replicacao"
    ) as iterar, patch(
        "app.bots.replicacao_d1.selenium.listagem._localizar_linha_workflow_paginas",
        return_value=True,
    ) as localizar:
        clicar_editar_por_workflow(
            driver,
            "WF Alvo",
            apenas_ativo=True,
            indice_listagem=indice,
        )

    iterar.assert_not_called()
    localizar.assert_called_once()


def test_indexar_listagem_nao_para_cedo_sem_linha_ativa():
    from app.bots.replicacao_d1.selenium.listagem import indexar_listagem_replicacao

    driver = MagicMock()
    linhas_p1 = [
        {"_wf_key": "wf alvo", "workflow": "WF Alvo", "situacao": "INATIVO"},
    ]
    linhas_p2 = [
        {"_wf_key": "wf alvo", "workflow": "WF Alvo", "situacao": "ATIVO"},
    ]
    info_seq = [
        {"pagina_atual": 1, "total_paginas": 2},
        {"pagina_atual": 2, "total_paginas": 2},
        {"pagina_atual": 1, "total_paginas": 2},
    ]

    with patch(
        "app.bots.replicacao_d1.selenium.listagem.ir_primeira_pagina_replicacao"
    ) as ir_primeira, patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_linhas_replicacao",
        side_effect=[linhas_p1, linhas_p2],
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_info_paginador",
        side_effect=info_seq,
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.ir_proxima_pagina_replicacao",
        return_value=True,
    ):
        indice = indexar_listagem_replicacao(
            driver,
            {"replicacao_indexar_parar_quando_plano_completo": True},
            wf_keys_alvo={"wf alvo"},
        )

    assert indice.paginas_lidas == 2
    assert indice.entradas["wf alvo"]["pagina"] == 2
    assert indice.entradas["wf alvo"]["tem_ativo"] is True
    assert ir_primeira.call_count == 2


def test_paginacao_listagem_precisa_restaurar():
    from app.bots.replicacao_d1.selenium.listagem import paginacao_listagem_precisa_restaurar

    class FakeDriver:
        pass

    driver = FakeDriver()
    settings = {"replicacao_paginacao_tamanho": 500}

    with patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_info_paginador",
        return_value={"total_paginas": 1, "total_itens": 850, "pagina_atual": 1},
    ):
        assert paginacao_listagem_precisa_restaurar(driver, settings, pagina_alvo=2) is True
        assert paginacao_listagem_precisa_restaurar(driver, settings) is True

    with patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_info_paginador",
        return_value={"total_paginas": 2, "total_itens": 850, "pagina_atual": 1},
    ):
        assert paginacao_listagem_precisa_restaurar(driver, settings, pagina_alvo=2) is False


def test_ir_pagina_replicacao_restaura_paginacao_quando_indice_aponta_pagina_inexistente():
    from app.bots.replicacao_d1.selenium.listagem import ir_pagina_replicacao

    driver = MagicMock()
    settings = {"replicacao_paginacao_tamanho": 500}
    info_seq = [
        {"pagina_atual": 1, "total_paginas": 1, "total_itens": 850},
        {"pagina_atual": 1, "total_paginas": 2, "total_itens": 850},
        {"pagina_atual": 2, "total_paginas": 2, "total_itens": 850},
    ]

    with patch(
        "app.bots.replicacao_d1.selenium.listagem.ler_info_paginador",
        side_effect=info_seq,
    ), patch(
        "app.bots.replicacao_d1.selenium.listagem.garantir_paginacao_listagem_replicacao",
        return_value=True,
    ) as restaurar, patch(
        "app.bots.replicacao_d1.selenium.listagem._clicar_pagina_replicacao",
        return_value={"ok": True, "pagina_atual_depois": 2, "mode": "paginador-pag-posterior"},
    ):
        ir_pagina_replicacao(driver, 2, settings=settings)
        restaurar.assert_called_once()
