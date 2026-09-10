from report_brb.brb_normalize import padronizar_descricao, strip_accents


def test_padronizar_preserva_prefixo_nao_sinalizado():
    result = padronizar_descricao("NAO SINALIZADO - DOC. POSSUI SOBREPOSICAO NA FOTO")
    normalized = strip_accents(result)
    assert normalized.endswith("Sobreposicao na foto")
    assert normalized.startswith("Nao sinalizado")


def test_padronizar_preserva_prefixo_sinalizacao_incorreta():
    result = padronizar_descricao("SINALIZACAO INCORRETA - DOC. POSSUI SOBREPOSICAO NA FOTO")
    normalized = strip_accents(result)
    assert normalized.endswith("Sobreposicao na foto")
    assert normalized.startswith("Sinalizacao incorreta")


def test_padronizar_encurta_cenario_longo_nao_sinalizado():
    raw = "NAO SINALIZADO - FACE A DO DOCUMENTO DE IDENTIFICACAO INCOMPATIVEL COM A FACE B"
    assert "Face A" in padronizar_descricao(raw)
    assert "Face B" in padronizar_descricao(raw)


def test_padronizar_mantem_texto_curto_sem_prefixo():
    assert "FORMATA" in padronizar_descricao("FORMATAcao INCORRETA")


def test_padronizar_consolida_variacoes_de_documento_adulterado():
    valores = [
        "NAO SINALIZADO - DOC. ADULTERADO",
        "NAO SINALIZADO - DOC. IDENTIFICACAO ADULTERADO",
        "NAO SINALIZADO - DOCUMENTO DE IDENTIFICACAO ADULTERADA",
    ]
    result = {padronizar_descricao(value) for value in valores}
    assert len(result) == 1
    assert next(iter(result)).endswith("Documento adulterado")


def test_padronizar_remove_prefixo_operacional_repetido():
    raw = "SINALIZACAO INCORRETA - SINALIZACAO INCORRETA - DOCUMENTO DE IDENTIFICACAO INCOMPLETO"
    result = padronizar_descricao(raw)
    assert result.count("Sinaliza") == 1
    assert result.endswith("Documento incompleto")
