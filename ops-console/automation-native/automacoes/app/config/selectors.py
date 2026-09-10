"""Selenium selectors for Okta and BRFlow — values must not change."""


class okta:
    O_LINK = "https://experian.okta.com/login/default"
    O_brflow = "https://www.brflow.com.br/BrFlow/index/index"
    O_usuario = "input28"
    O_senha = "input36"
    O_entrar = '//*[@id="form20"]/div[2]/input'
    O_proximo = "/html/body/div[2]/div[2]/main/div[2]/div/div/div[2]/form/div[2]/input"
    O_verificar = "/html/body/div[2]/div[2]/main/div[2]/div/div/div[2]/form/div[2]/input"
    O_usuario2 = "input29"
    O_senha2 = "input37"
    O_entrar2 = '//*[@id="form21"]/div[2]/input'
    O_proximo2 = "/html/body/div[2]/div[2]/main/div[2]/div/div/div[2]/form/div[2]/input"
    O_verificar2 = "/html/body/div[2]/div[2]/main/div[2]/div/div/div[2]/form/div[2]/input"
    O_pesquisar = "dashboard-search-input"


class brflow:
    B_detalhado = "//a[@data-ascii='detalhado de registro']"
    B_monitor = '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[1]/ul/li[9]/a'
    B_rotina = '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[1]/ul/li[12]/a'
    B_R_data = "dataRotinaExecucao"
    B_R_pesquisar = '//*[@id="formpesquisa"]/div[2]/button'
    B_M_pesquisar = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div[12]/button'
    B_M_data = "#layout_layout2_panel_main > div.w2ui-panel-content > div > fieldset > form > div:nth-child(1) > div > input"
    B_M_data_final = "#layout_layout2_panel_main > div.w2ui-panel-content > div > fieldset > form > div:nth-child(2) > div > input"
    B_M_objeto = "#layout_layout2_panel_main > div.w2ui-panel-content > div > fieldset > form > div:nth-child(3) > select > option:nth-child(2)"
    B_M_csv = "formato-csv"
    B_usuario = '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[2]/ul/li[8]/a'
    B_U_perfil = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/div[1]/select/option[2]'
    B_U_status = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/div[2]/select/option[2]'
    B_U_csv = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/div[7]/label[3]'
    B_U_pesquisar = '//button[@class="btn btn-primary btn-sutmit-pesquisar"]'
    B_U_user = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/div[4]/input'
    B_U_editar = '//*[@id="layout_layout2_panel_main"]/div[4]/div/div[3]/div/div[3]/table/tbody/tr/td[11]/span/span'
    B_U_alterarspan = '//*[@id="layout_layout2_panel_right"]/div[4]/div/div/fieldset/form/div[1]/span'
    B_U_alterar = "/html/body/span/span/span[1]/input"
    B_U_resultados = '//*[@id="select2-codNivelHierarquico-wa-results"]/li'
    B_U_salvar = '//*[@id="layout_layout2_panel_right"]/div[4]/div/div/fieldset/form/button[2]'
    B_menu = '//*[@id="layout_layout2_panel_top"]/div[4]/div/div[2]/nav/div/div[2]/ul/li[9]/a'
    B_menu_rotina = '//*[@id="layout_layout2_panel_top"]/div[4]/div/div[2]/nav/div/div[2]/ul/li[7]/a'
    B_menu_replicacao = "//a[@data-ascii='replicacao de protocolos']"
    B_replicacao_cliente_container = "select2-codClienteDestino-7r-container"
    B_replicacao_workflow_container = "select2-codWorkFlowDestino-i0-container"
    B_replicacao_cliente_destino_pesquisa = (
        'select.clienteDestinoPesquisa[name="codClienteDestino"]'
    )
    B_replicacao_workflow_destino_pesquisa = (
        'select.workflowDestinoPesquisa[name="codWorkFlowDestino"]'
    )
    B_replicacao_select2_search = "input.select2-search__field"
    B_replicacao_select2_option = "//li[contains(@class,'select2-results__option')]"
    B_replicacao_pesquisar = "//button[contains(@class,'btn-primary') and normalize-space(string(.))='Pesquisar']"
    B_replicacao_painel_edicao = "#layout_layout2_panel_right"
    B_replicacao_workflow_destino_edicao = (
        '#layout_layout2_panel_right select[name="codWorkFlowDestino"]'
    )
    B_replicacao_workflow_destino_edicao_select2 = (
        '#layout_layout2_panel_right span[id*="select2-codWorkFlowDestino"][id$="-container"]'
    )
    B_replicacao_checkbox_protocolos = "#replicarApenasProtocolosEspecificos"
    B_replicacao_checkbox_icm_auditoria = "#replicarIcmAuditoria"
    B_replicacao_input_arquivo_csv = "#arquivoProtocolosEspecificos"
    B_replicacao_input_qtd_replicada = 'input.qtdReplicada[name="qtdReplicada"]'
    B_replicacao_td_regra = "td[data-html-nom_regra]"
    B_replicacao_salvar = (
        '//*[@id="layout_layout2_panel_right"]//button[normalize-space(string(.))="Salvar"]'
    )
    B_replicacao_salvar_posicao = (
        '//*[@id="layout_layout2_panel_right"]/div[4]/div/div/fieldset/form/button[2]'
    )
    B_replicacao_cancelar = (
        '//*[@id="layout_layout2_panel_right"]//button[normalize-space(string(.))="Cancelar"]'
    )
    B_replicacao_cancelar_posicao = (
        '//*[@id="layout_layout2_panel_right"]/div[4]/div/div/fieldset/form/button[1]'
    )
    B_replicacao_confirmar_modal = (
        "//div[contains(@class,'modal') and (contains(@class,'in') or contains(@style,'display: block'))]"
        "//button[normalize-space(string(.))='Confirmar' or normalize-space(string(.))='Sim' "
        "or normalize-space(string(.))='OK' or normalize-space(string(.))='Ok']"
    )
    B_replicacao_confirmar_w2ui_sim = "button#Yes.w2ui-popup-btn"
    B_replicacao_confirmar_w2ui_popup = (
        "//div[contains(@class,'w2ui-popup')]//button[@id='Yes' "
        "and normalize-space(string(.))='Sim']"
    )
    B_replicacao_listagem_tbody = '//tbody[contains(@class,"listagem")]'
    B_replicacao_paginador_rodape = (
        '//*[@id="layout_layout2_panel_main"]/div[4]/div/div[2]/div[3]'
    )
    B_replicacao_paginador_proxima = (
        '#layout_layout2_panel_main button.paginador-pag-posterior'
    )
    B_replicacao_paginador_anterior = (
        '#layout_layout2_panel_main button.paginador-pag-anterior'
    )
    B_replicacao_linha_listagem = "tr.js-tpl-linha"
    B_replicacao_btn_status = ".btn-status"
    B_replicacao_td_workflow_origem = "td[data-html-nom_workflow_origem]"
    B_replicacao_td_situacao_attrs = (
        "data-html-ind_ativo",
        "data-html-situacao",
        "data-html-des_situacao",
        "data-html-ind_situacao",
    )
    B_produção = '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[1]/ul/li[11]/a'
    B_produção_ascii = "//a[@data-ascii='produtividade']"
    B_P_data = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/fieldset/div[1]/div/span'
    B_P_data2 = "/html/body/div[17]/div[1]/div[5]/div[4]/div/div[5]/div[4]/div/fieldset/form/div/div/fieldset/div[1]/div/input"
    B_P_meta = "/html/body/div[17]/div[1]/div[5]/div[4]/div/div[5]/div[4]/div/fieldset/form/div/div/fieldset/div[6]/select/option[2]"
    B_P_meta2 = "/html/body/div[17]/div[1]/div[5]/div[4]/div/div[5]/div[4]/div/fieldset/form/div/div/fieldset/div[6]/select"
    B_P_CSV = '//*[@id="layout_layout2_panel_main"]//label[@for="formato-csv"]'
    B_P_CSV_input = "formato-csv"
    B_P_pesquisar = '//*[@id="layout_layout2_panel_main"]/div[4]/div/fieldset/form/div/div/div[2]/button'
    B_P_pesquisar_class = '//*[@id="layout_layout2_panel_main"]//button[contains(@class,"btn-pesquisar")]'
    B_M_table = '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[1]/ul/li[10]/a'


class ged:
    """Seletores do portal GED (Relatório de Irregularidades)."""

    LOGIN_URL = "https://ged-web-frontend.claro.br.experian.eeco/login"
    PROTOCOLO_BASE_URL = "https://ged-web-frontend.claro.br.experian.eeco/protocolo"

    LOGIN_USER = "/html/body/app-root/app-login/main/div/div/div/form/div[1]/div/input"
    LOGIN_PASS = "/html/body/app-root/app-login/main/div/div/div/form/div[2]/div/input"
    LOGIN_SUBMIT = "/html/body/app-root/app-login/main/div/div/div/form/button"
    MENU_HOME = "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[1]/div/div[4]/div[1]/a"

    POS_VENDA_XPATH = (
        "/html/body/app-root/app-home/main/app-menu/div/div[2]/div[2]/div/div[2]/div[1]/a"
    )
    POS_VENDA_CSS = (
        "body > app-root > app-home > main > app-menu > div > "
        "div.grid-menu.ng-star-inserted > div:nth-child(2) > div > "
        "div.mdn-CardInstitutional-content.submodulo.ng-star-inserted > "
        "div.mdn-Link.mdn-Link--arrow.titulo-submodulo.ng-star-inserted > a"
    )
    # Mantido para compatibilidade; preferir POS_VENDA_XPATH (há 2 links "Pós-Venda" na página)
    POS_VENDA = POS_VENDA_XPATH
    RELATORIO_IRREGULARIDADES = (
        "//a[contains(@class,'mdn-Link-anchor')]"
        "[contains(normalize-space(.),'Relatório de Irregularidades')]"
    )

    DATA_INICIO = "dtinidataContestacao"
    DATA_FIM = "dtfimdataContestacao"
    REL_IRREGULARIDADES = "app-relirregularidades"
    FORMATO_CSV_XPATH = (
        "/html/body/app-root/app-home/main/app-relatorios/div/div[2]/div/"
        "app-relirregularidades/div[1]/div[1]/div/app-radio/form/div[2]/div/div[2]/div/label"
    )
    FORMATO_CSV_DIV_XPATH = (
        "/html/body/app-root/app-home/main/app-relatorios/div/div[2]/div/"
        "app-relirregularidades/div[1]/div[1]/div/app-radio/form/div[2]/div/div[2]/div/div"
    )
    FORMATO_CSV_CSS = (
        "body > app-root > app-home > main > app-relatorios > div > div.row > div > "
        "app-relirregularidades > div.row > div:nth-child(1) > div > app-radio > form > "
        "div:nth-child(2) > div > div:nth-child(2) > div > label"
    )
    FORMATO_CSV_INPUT_ID = "Visualizao2"
    FORMATO_CSV = FORMATO_CSV_XPATH
    BTN_CONSULTAR = (
        "//button[contains(@class,'mdn-Button--primary')]"
        "[.//i[contains(@class,'fa-search')]]"
    )

    PROGRESS_XPATH = "/html/body/app-root/app-home/main/app-download/div/div/dl/div[2]/progress"
    DOWNLOAD_LINK_XPATHS = [
        "//app-download//a[contains(@class,'mdn-Link-anchor')]",
        "//app-download//a[.//span[contains(normalize-space(.),'Baixar arquivo')]]",
        "//a[contains(@class,'mdn-Link-anchor')][.//span[contains(@class,'mdn-Link-anchor-label')]]",
        "/html/body/app-root/app-home/main/app-download/div/div/div/div/a",
    ]
