# -*- coding: utf-8 -*-
"""Helpers puros de texto/opções Select2 (sem driver)."""


def normalizar_texto_select2_ui(texto_ui: str) -> str:
    """Remove prefixo do botão limpar (×) e espaços do texto renderizado do Select2."""
    t = (texto_ui or "").strip()
    while t and t[0] in ("\u00d7", "×", "\uf0d7"):
        t = t[1:].strip()
    return t


def ui_select2_vazia(texto_ui: str) -> bool:
    t = normalizar_texto_select2_ui(texto_ui).lower()
    if not t:
        return True
    return t == "selecione" or t.startswith("adicione ")


def termos_busca_select2(valor_cod: str, texto_opcao: str) -> list[str]:
    valor_cod = str(valor_cod or "").strip()
    texto_opcao = str(texto_opcao or "").strip()
    termos: list[str] = []
    if texto_opcao:
        termos.append(texto_opcao)
        if " - " in texto_opcao:
            termos.append(texto_opcao.split(" - ", 1)[0].strip())
    if valor_cod:
        termos.append(valor_cod)
    return [t for t in dict.fromkeys(termos) if t]


def termo_digitacao_select2(valor_cod: str, texto_opcao: str) -> str:
    """Termo curto para digitar no Select2 (ex.: 'G Auditoria' em vez do label completo)."""
    termos = termos_busca_select2(valor_cod, texto_opcao)
    nao_digit = [t for t in termos if not str(t).isdigit()]
    if nao_digit:
        return min(nao_digit, key=len)
    return termos[0] if termos else str(valor_cod or "")


def texto_select2_bate(texto_ui: str, texto_opcao: str) -> bool:
    ui_l = normalizar_texto_select2_ui(texto_ui).lower()
    if not ui_l or ui_select2_vazia(ui_l):
        return False
    if not texto_opcao:
        return True
    hints = [texto_opcao.lower()]
    if " - " in texto_opcao:
        hints.append(texto_opcao.split(" - ", 1)[0].strip().lower())
    return any(h and (h in ui_l or ui_l in h) for h in hints)
