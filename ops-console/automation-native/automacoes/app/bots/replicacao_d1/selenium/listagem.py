# -*- coding: utf-8 -*-
"""Leitura, filtro e interação com a listagem BRFlow (replicação D-1)."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Set

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.bots.replicacao_aud_d1_planning import _normalizar_workflow
from app.bots.replicacao_d1.selenium.text_utils import classificar_situacao_texto
from app.bots.replicacao_d1.settings import (
    indexar_parar_quando_plano_completo,
    paginacao_max_paginas,
    paginacao_tamanho,
    paginacao_varrer_todas,
)
from app.config import brflow

log = logging.getLogger("robots.bot_replicacao_aud_d1")

SetStatusFn = Callable[[str], None]
RefilterFn = Callable[..., None]
XpathEscapeFn = Callable[[str], str]

_ATTR_CELULA_WORKFLOW_ORIGEM = (
    "data-html-nom_workflow_origem",
    "data-html-nom-workflow-origem",
)
_ATTR_CELULA_REGRA = (
    "data-html-nom_regra",
    "data-html-nom-regra",
    "data-html-des_regra",
)


def normalizar_regra_brflow(texto: str) -> str:
    return _normalizar_workflow(str(texto or ""))


def suffix_regra_distintivo(nome_regra: str) -> str:
    """Último segmento após '+' — identifica regra Redoc quando WF origem se repete."""
    partes = [p.strip() for p in str(nome_regra or "").split("+") if p.strip()]
    alvo = partes[-1] if partes else str(nome_regra or "")
    return normalizar_regra_brflow(alvo)


def prefixo_regra_brflow(nome_regra: str) -> str:
    """Tag entre colchetes no início da regra (ex.: [G AUDITORIA], [AUDITORIA REDOC])."""
    match = re.match(r"^\s*\[([^\]]+)\]", str(nome_regra or ""))
    return normalizar_regra_brflow(match.group(1)) if match else ""


def regra_brflow_bate_estrita(nome_regra_cadastro: str, nome_regra_listagem: str) -> bool:
    """Match estrito — não confunde linhas do mesmo workflow origem (G vs Redoc, sufixos distintos)."""
    cadastro = normalizar_regra_brflow(nome_regra_cadastro)
    listagem = normalizar_regra_brflow(nome_regra_listagem)
    if not cadastro or not listagem:
        return False
    if cadastro == listagem:
        return True

    prefix_c = prefixo_regra_brflow(nome_regra_cadastro)
    prefix_l = prefixo_regra_brflow(nome_regra_listagem)
    if prefix_c and prefix_l and prefix_c != prefix_l:
        return False

    suffix_c = suffix_regra_distintivo(nome_regra_cadastro)
    suffix_l = suffix_regra_distintivo(nome_regra_listagem)
    if suffix_c and suffix_l and suffix_c == suffix_l:
        return True

    if prefix_c and prefix_l and prefix_c == prefix_l:
        if cadastro in listagem or listagem in cadastro:
            return True

    return False


def regra_brflow_bate(nome_regra_cadastro: str, nome_regra_listagem: str) -> bool:
    """Alias estrito — regras Redoc exigem sufixo distintivo, não segmentos compartilhados."""
    return regra_brflow_bate_estrita(nome_regra_cadastro, nome_regra_listagem)


def situacao_conta_como_ativa(situacao: str) -> bool:
    """Linhas sem btn-status (Redoc/Bio) ficam DESCONHECIDO e devem ser editáveis."""
    return str(situacao or "").upper() in ("ATIVO", "DESCONHECIDO")


def contar_linhas_ativas_regra(
    linhas: list,
    wf_key: str,
    nome_regra: str,
    *,
    indice: Optional[IndiceListagemBrflow] = None,
    resumo_br: Optional[dict] = None,
) -> int:
    if not normalizar_regra_brflow(nome_regra):
        return 0
    if linhas:
        count = 0
        for item in linhas:
            if item.get("_wf_key") != wf_key:
                continue
            if not situacao_conta_como_ativa(str(item.get("situacao") or "")):
                continue
            regra_linha = str(item.get("regra") or item.get("_regra_key") or "")
            if regra_brflow_bate(nome_regra, regra_linha):
                count += 1
        return count
    for bloco in _blocos_regra_resumo(wf_key, indice=indice, resumo_br=resumo_br):
        regras = bloco.get("regras_ativas") or {}
        count = 0
        for regra_key, qty in regras.items():
            if regra_brflow_bate(nome_regra, regra_key):
                count += int(qty or 0)
        if count:
            return count
    return 0


def _blocos_regra_resumo(
    wf_key: str,
    *,
    indice: Optional[IndiceListagemBrflow] = None,
    resumo_br: Optional[dict] = None,
) -> list[dict]:
    blocos: list[dict] = []
    if indice is not None:
        bloco = indice.entradas.get(wf_key)
        if bloco:
            blocos.append(bloco)
    if resumo_br is not None:
        bloco = resumo_br.get(wf_key)
        if bloco and bloco not in blocos:
            blocos.append(bloco)
    return blocos


def _linha_aceita(item: dict, wf_key: str, *, apenas_ativo: bool, nome_regra: Optional[str] = None) -> bool:
    if not item or item.get("_wf_key") != wf_key:
        return False
    if apenas_ativo and not situacao_conta_como_ativa(str(item.get("situacao") or "")):
        return False
    if nome_regra:
        regra_linha = str(item.get("regra") or item.get("_regra_key") or "")
        return regra_brflow_bate(nome_regra, regra_linha)
    return True


@dataclass
class IndiceListagemBrflow:
    """Mapa wf_key → metadados de listagem (página e situação)."""

    entradas: dict = field(default_factory=dict)
    entradas_regra: dict = field(default_factory=dict)
    paginas_lidas: int = 0
    linhas_lidas: int = 0
    parada_antecipada: bool = False


def _chave_indice_regra(wf_key: str, nome_regra: str) -> str:
    return f"{wf_key}|{suffix_regra_distintivo(nome_regra)}"


def resolver_pagina_indice_por_regra(
    indice: Optional[IndiceListagemBrflow],
    wf_key: str,
    nome_regra: str,
) -> Optional[int]:
    """Página da listagem para par wf+regra (Redoc)."""
    if indice is None or not nome_regra:
        return None
    chave = _chave_indice_regra(wf_key, nome_regra)
    bloco = indice.entradas_regra.get(chave)
    if bloco:
        pagina = int(bloco.get("pagina") or 0)
        return pagina if pagina > 0 else None
    for key, bloco in indice.entradas_regra.items():
        if not str(key).startswith(f"{wf_key}|"):
            continue
        regra_linha = str(bloco.get("regra") or "")
        if regra_brflow_bate_estrita(nome_regra, regra_linha):
            pagina = int(bloco.get("pagina") or 0)
            return pagina if pagina > 0 else None
    return None


def _atualizar_entrada_indice(indice: IndiceListagemBrflow, item: dict, pagina: int) -> None:
    key = item["_wf_key"]
    if key not in indice.entradas:
        indice.entradas[key] = {
            "workflow": item["workflow"],
            "pagina": pagina,
            "tem_ativo": False,
            "tem_inativo": False,
            "linhas_ativas": 0,
            "linhas_inativas": 0,
            "regras_ativas": {},
        }
    bloco = indice.entradas[key]
    if situacao_conta_como_ativa(item["situacao"]):
        bloco["tem_ativo"] = True
        bloco["linhas_ativas"] += 1
        bloco["pagina"] = pagina
        regra_texto = str(item.get("regra") or item.get("_regra_key") or "").strip()
        if regra_texto:
            regras = bloco.setdefault("regras_ativas", {})
            regra_key = normalizar_regra_brflow(regra_texto)
            regras[regra_key] = int(regras.get(regra_key, 0)) + 1
            chave_regra = _chave_indice_regra(key, regra_texto)
            indice.entradas_regra[chave_regra] = {
                "workflow": item["workflow"],
                "regra": regra_texto,
                "pagina": pagina,
                "suffix": suffix_regra_distintivo(regra_texto),
            }
    elif item["situacao"] == "INATIVO":
        bloco["tem_inativo"] = True
        bloco["linhas_inativas"] += 1
        if not bloco.get("tem_ativo"):
            bloco["pagina"] = pagina


def indice_para_resumo(indice: IndiceListagemBrflow) -> dict:
    """Converte índice para o formato de resumir_linhas_por_workflow."""
    resumo: dict = {}
    for key, bloco in indice.entradas.items():
        resumo[key] = {
            "workflow": bloco["workflow"],
            "tem_ativo": bloco["tem_ativo"],
            "tem_inativo": bloco["tem_inativo"],
            "linhas_ativas": bloco["linhas_ativas"],
            "linhas_inativas": bloco["linhas_inativas"],
            "regras_ativas": dict(bloco.get("regras_ativas") or {}),
        }
    return resumo


def indice_cobre_alvos(indice: IndiceListagemBrflow, wf_keys_alvo: Set[str]) -> bool:
    """True quando cada alvo tem linha ativa indexada (não basta key inativo na pág. 1)."""
    if not wf_keys_alvo:
        return False
    for key in wf_keys_alvo:
        bloco = indice.entradas.get(key)
        if not bloco or not bloco.get("tem_ativo"):
            return False
    return True


def invalidar_entrada_indice(indice: Optional[IndiceListagemBrflow], wf_key: str) -> None:
    if indice is None:
        return
    indice.entradas.pop(wf_key, None)
    prefixo = f"{wf_key}|"
    for chave in list(indice.entradas_regra.keys()):
        if str(chave).startswith(prefixo):
            indice.entradas_regra.pop(chave, None)


def resolver_direcao_paginacao(atual: int, alvo: int) -> str:
    """Retorna a ação de navegação entre páginas adjacentes."""
    if alvo == atual:
        return "ja_na_pagina"
    if alvo == atual + 1:
        return "proxima"
    if alvo == atual - 1:
        return "anterior"
    return "indireta"


def parse_texto_paginador(texto: str) -> dict:
    """Extrai total de itens e página atual/total do rodapé BRFlow."""
    texto = (texto or "").strip()
    info = {
        "total_itens": None,
        "pagina_atual": 1,
        "total_paginas": 1,
        "texto": texto,
    }
    match_itens = re.search(r"(\d+)\s*itens", texto, re.IGNORECASE)
    if match_itens:
        info["total_itens"] = int(match_itens.group(1))
    match_pag = re.search(r"Pág\.?\s*(\d+)\s*de\s*(\d+)", texto, re.IGNORECASE)
    if match_pag:
        info["pagina_atual"] = int(match_pag.group(1))
        info["total_paginas"] = max(1, int(match_pag.group(2)))
    return info


def ler_texto_rodape_paginador(driver) -> str:
    try:
        bloco = driver.find_element(By.XPATH, brflow.B_replicacao_paginador_rodape)
        return (bloco.text or "").strip()
    except Exception:
        return ""


def ler_info_paginador(driver) -> dict:
    return parse_texto_paginador(ler_texto_rodape_paginador(driver))


def ler_total_itens_paginador(driver) -> Optional[int]:
    """Extrai total de itens do rodapé (ex.: '4 itens - Pág. 1 de 1')."""
    info = ler_info_paginador(driver)
    return info.get("total_itens")


def extrair_texto_celula_replicacao(td, attr_names=None, driver=None) -> str:
    """Lê texto visível ou valor em data-html-* / title (BRFlow costuma usar atributos)."""
    attr_names = attr_names or _ATTR_CELULA_WORKFLOW_ORIGEM
    texto = (td.text or "").strip()
    if texto:
        return texto
    for attr in attr_names:
        valor = (td.get_attribute(attr) or "").strip()
        if valor:
            return valor
    title = (td.get_attribute("title") or "").strip()
    if title:
        return title
    if driver is not None:
        try:
            inner = driver.execute_script(
                "return (arguments[0].innerText || arguments[0].textContent || '').trim();",
                td,
            )
            if inner:
                return str(inner).strip()
        except Exception:
            pass
    return ""


def aguardar_mudanca_pagina_replicacao(
    driver,
    pagina_antes: int,
    *,
    timeout: float = 20,
) -> bool:
    """Aguarda o rodapé indicar página diferente da atual."""

    def _mudou(_driver) -> bool:
        atual = int(ler_info_paginador(_driver).get("pagina_atual") or pagina_antes)
        return atual != pagina_antes

    try:
        WebDriverWait(driver, timeout).until(_mudou)
        return True
    except TimeoutException:
        return False


def _clicar_botao_paginador_replicacao(
    driver,
    seletor: str,
    *,
    pagina_antes: int,
    alvo: int,
    mode: str,
) -> dict:
    """Clica posterior/anterior e confirma que a página mudou."""
    tbody_antes = None
    try:
        tbody_antes = driver.find_element(By.XPATH, brflow.B_replicacao_listagem_tbody)
    except Exception:
        pass

    try:
        btn = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, seletor))
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});",
            btn,
        )
        driver.execute_script("arguments[0].click();", btn)
    except Exception as exc:
        log.warning("[PAGINACAO] Falha ao clicar %s (%s): %s", mode, seletor, exc)
        return {"ok": False, "reason": str(exc), "mode": mode, "anterior": pagina_antes}

    if tbody_antes is not None:
        try:
            WebDriverWait(driver, 15).until(EC.staleness_of(tbody_antes))
        except TimeoutException:
            log.debug("[PAGINACAO] tbody não ficou stale após clique %s", mode)

    if not aguardar_mudanca_pagina_replicacao(driver, pagina_antes):
        depois = int(ler_info_paginador(driver).get("pagina_atual") or pagina_antes)
        log.warning(
            "[PAGINACAO] Página não mudou após %s | antes=%s depois=%s alvo=%s",
            mode,
            pagina_antes,
            depois,
            alvo,
        )
        return {
            "ok": False,
            "reason": "pagina_nao_mudou",
            "mode": mode,
            "anterior": pagina_antes,
            "pagina_atual_depois": depois,
        }

    aguardar_listagem_replicacao_carregada(
        driver,
        timeout=30,
        usar_alvo_paginador=False,
        min_linhas=0,
        tamanho_pagina=paginacao_tamanho(),
    )
    time.sleep(0.25)
    depois = int(ler_info_paginador(driver).get("pagina_atual") or pagina_antes)
    log.info("[PAGINACAO] %s | página %s → %s (alvo=%s)", mode, pagina_antes, depois, alvo)
    return {
        "ok": True,
        "mode": mode,
        "anterior": pagina_antes,
        "pagina_atual_depois": depois,
        "pagina": alvo,
    }


def _clicar_pagina_replicacao(driver, numero: int) -> dict:
    info = ler_info_paginador(driver)
    pagina_antes = int(info.get("pagina_atual") or 1)
    total = int(info.get("total_paginas") or 1)
    alvo = max(1, int(numero))
    if alvo > total:
        return {
            "ok": False,
            "reason": "pagina_inexistente",
            "anterior": pagina_antes,
            "total": total,
            "pagina": alvo,
        }

    direcao = resolver_direcao_paginacao(pagina_antes, alvo)
    if direcao == "ja_na_pagina":
        return {
            "ok": True,
            "mode": "ja_na_pagina",
            "pagina_atual_depois": pagina_antes,
            "pagina": alvo,
        }
    if direcao == "proxima":
        return _clicar_botao_paginador_replicacao(
            driver,
            brflow.B_replicacao_paginador_proxima,
            pagina_antes=pagina_antes,
            alvo=alvo,
            mode="paginador-pag-posterior",
        )
    if direcao == "anterior":
        return _clicar_botao_paginador_replicacao(
            driver,
            brflow.B_replicacao_paginador_anterior,
            pagina_antes=pagina_antes,
            alvo=alvo,
            mode="paginador-pag-anterior",
        )
    return {
        "ok": False,
        "reason": "salto_pagina_nao_suportado",
        "anterior": pagina_antes,
        "pagina": alvo,
    }


def obter_tbody_listagem_replicacao(driver):
    """Retorna tbody com mais linhas (evita pegar tbody vazio quando há duplicatas)."""
    tbodies = driver.find_elements(By.XPATH, brflow.B_replicacao_listagem_tbody)
    melhor = None
    melhor_n = 0
    for tb in tbodies:
        try:
            n = len(tb.find_elements(By.TAG_NAME, "tr"))
        except StaleElementReferenceException:
            continue
        if n > melhor_n:
            melhor_n = n
            melhor = tb
    if melhor is not None:
        return melhor, melhor_n
    return None, 0


def contar_linhas_listagem_replicacao(driver) -> int:
    """Conta linhas visíveis na listagem (tbody com mais tr ou células workflow)."""
    try:
        _, n_tr = obter_tbody_listagem_replicacao(driver)
        if n_tr > 0:
            return n_tr
        n_celulas = len(
            driver.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_td_workflow_origem)
        )
        if n_celulas > 0:
            return n_celulas
        return n_tr
    except StaleElementReferenceException:
        return -1
    except Exception:
        return 0


def listagem_pronta_para_ler(
    ultima_contagem: int,
    contagem: int,
    repeticoes: int,
    polls_estaveis: int,
    min_linhas: int,
    alvo_linhas: Optional[int] = None,
    polls_estavel_sem_alvo: int = 8,
) -> tuple[int, int, bool]:
    """Calcula próximo estado da espera da listagem (extraído para testes)."""
    if contagem < 0:
        return ultima_contagem, 0, False
    if contagem > ultima_contagem and ultima_contagem >= 0:
        return contagem, 0, False
    if contagem == ultima_contagem and contagem >= min_linhas:
        repeticoes += 1
        if alvo_linhas is None or contagem >= alvo_linhas:
            pronta = repeticoes >= polls_estaveis
        elif contagem > 0:
            pronta = repeticoes >= polls_estavel_sem_alvo
        else:
            pronta = False
        return contagem, repeticoes, pronta
    return contagem, 0, False


def aguardar_listagem_replicacao_carregada(
    driver,
    timeout: float = 45,
    polls_estaveis: int = 2,
    intervalo_seg: float = 0.25,
    min_linhas: int = 1,
    usar_alvo_paginador: bool = True,
    polls_estavel_sem_alvo: int = 8,
    tamanho_pagina: int = 500,
) -> int:
    """
    Aguarda a listagem BRFlow terminar de carregar após Pesquisar ou mudança de paginação.
    Não estabiliza enquanto a contagem ainda sobe; paginador é referência, não bloqueio rígido.
    """
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, brflow.B_replicacao_listagem_tbody))
    )
    inicio = time.time()
    ultima_contagem = -1
    repeticoes = 0
    total_paginador = ler_total_itens_paginador(driver) if usar_alvo_paginador else None
    alvo_linhas = min(total_paginador, tamanho_pagina) if total_paginador else None
    if total_paginador is not None:
        log.debug(
            "[LISTAGEM] Paginador indica %d item(ns); alvo=%d linha(s) (flexível se divergir)",
            total_paginador,
            alvo_linhas,
        )

    while time.time() - inicio < timeout:
        contagem = contar_linhas_listagem_replicacao(driver)
        if contagem < 0:
            repeticoes = 0
            time.sleep(intervalo_seg)
            continue

        ultima_contagem, repeticoes, pronta = listagem_pronta_para_ler(
            ultima_contagem,
            contagem,
            repeticoes,
            polls_estaveis,
            min_linhas,
            alvo_linhas,
            polls_estavel_sem_alvo,
        )
        if pronta:
            if alvo_linhas and contagem < alvo_linhas:
                log.info(
                    "[LISTAGEM] Carregada com %d linha(s) após %.1fs "
                    "(paginador=%d; contagem diverge do rodapé)",
                    contagem,
                    time.time() - inicio,
                    total_paginador,
                )
            else:
                log.info(
                    "[LISTAGEM] Carregada com %d linha(s) após %.1fs%s",
                    contagem,
                    time.time() - inicio,
                    f" (paginador={total_paginador})" if total_paginador else "",
                )
            return contagem

        time.sleep(intervalo_seg)

    contagem = max(contar_linhas_listagem_replicacao(driver), 0)
    log.warning(
        "[LISTAGEM] Timeout (%ds) aguardando estabilização; seguindo com %d linha(s)%s",
        int(timeout),
        contagem,
        f" (paginador={total_paginador})" if total_paginador else "",
    )
    return contagem


def pausar_listagem_replicacao(
    segundos: float,
    motivo: str,
    *,
    set_status: Optional[SetStatusFn] = None,
) -> None:
    log.info("[LISTAGEM] Aguardando %.0fs (%s)", segundos, motivo)
    if set_status:
        set_status(f"Aguardando listagem ({motivo})")
    time.sleep(segundos)


def filtrar_listagem_apenas_ativos_js(driver) -> dict:
    """Remove tr.js-tpl-linha inativas. Linhas sem .btn-status (Redoc) são mantidas."""
    script = """
    let removidas = 0, mantidas = 0;
    document.querySelectorAll("tr.js-tpl-linha").forEach(tr => {
        const status = tr.querySelector(".btn-status");
        if (!status) {
            mantidas++;
            return;
        }
        if (status.getAttribute("title") !== "Ativo") {
            tr.remove();
            removidas++;
        } else {
            mantidas++;
        }
    });
    return { removidas: removidas, mantidas: mantidas, total: removidas + mantidas };
    """
    resultado = driver.execute_script(script)
    if not isinstance(resultado, dict):
        resultado = {"removidas": 0, "mantidas": 0, "total": 0}
    log.info(
        "[LISTAGEM] Filtro ativos | mantidas=%s | removidas=%s | total=%s",
        resultado.get("mantidas", 0),
        resultado.get("removidas", 0),
        resultado.get("total", 0),
    )
    return resultado


def refiltrar_listagem_ativos_apos_salvar(
    driver,
    settings: Optional[dict] = None,
    motivo: str = "após salvar",
    *,
    set_status: Optional[SetStatusFn] = None,
    listagem_espera_seg: float = 30.0,
) -> None:
    """Aguarda listagem estável e remove linhas inativas (pós confirmar / próximo workflow)."""
    settings = settings or {}
    if not bool(settings.get("replicacao_apenas_ativos", True)):
        return
    espera = float(settings.get("replicacao_listagem_espera_seg", listagem_espera_seg))
    if set_status:
        set_status(f"Preparando listagem ({motivo})")
    aguardar_listagem_replicacao_carregada(
        driver,
        timeout=15,
        usar_alvo_paginador=False,
        min_linhas=1,
    )
    filtrar_listagem_apenas_ativos_js(driver)
    pausar_listagem_replicacao(espera, motivo, set_status=set_status)


def logar_diagnostico_listagem_replicacao(driver) -> None:
    """Loga contagem de linhas quando Selenium não enxerga linhas visíveis."""
    try:
        total = len(driver.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_linha_listagem))
    except Exception:
        total = -1
    visiveis = len(iterar_linhas_listagem_replicacao(driver))
    log.warning(
        "[TABELA] Diagnóstico listagem | tr.js-tpl-linha=%s | visíveis=%s",
        total,
        visiveis,
    )


def preparar_listagem_pos_paginacao(
    driver,
    settings: Optional[dict] = None,
    *,
    set_status: Optional[SetStatusFn] = None,
    listagem_espera_seg: float = 30.0,
) -> None:
    """Timers fixos + filtro JS de ativos após paginação (por página)."""
    settings = settings or {}
    apenas_ativos = bool(settings.get("replicacao_apenas_ativos", True))
    espera = float(settings.get("replicacao_listagem_espera_seg", listagem_espera_seg))
    tamanho = paginacao_tamanho(settings)
    varrer_todas = paginacao_varrer_todas(settings)

    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_replicacao_linha_listagem))
    )

    pausar_listagem_replicacao(espera, f"após paginação {tamanho}", set_status=set_status)

    if apenas_ativos and not varrer_todas:
        filtrar_listagem_apenas_ativos_js(driver)
        pausar_listagem_replicacao(espera, "após filtro ativos", set_status=set_status)


def paginacao_listagem_precisa_restaurar(
    driver,
    settings: Optional[dict] = None,
    *,
    pagina_alvo: Optional[int] = None,
) -> bool:
    """Detecta paginação inconsistente (ex.: BRFlow resetou tamanho após salvar workflow)."""
    settings = settings or {}
    info = ler_info_paginador(driver)
    total_pag = int(info.get("total_paginas") or 1)
    total_itens = info.get("total_itens")
    tamanho = paginacao_tamanho(settings)
    if pagina_alvo is not None and int(pagina_alvo) > total_pag:
        return True
    if total_itens is not None and int(total_itens) > tamanho and total_pag <= 1:
        return True
    return False


def garantir_paginacao_listagem_replicacao(
    driver,
    settings: Optional[dict] = None,
    *,
    pagina_alvo: Optional[int] = None,
    set_status: Optional[SetStatusFn] = None,
) -> bool:
    """Reaplica tamanho de página quando o rodapé indica paginação truncada."""
    settings = settings or {}
    if not paginacao_listagem_precisa_restaurar(driver, settings, pagina_alvo=pagina_alvo):
        return False
    tamanho = paginacao_tamanho(settings)
    info = ler_info_paginador(driver)
    log.warning(
        "[PAGINACAO] Restaurando paginação %d/página | rodapé=%s | alvo_pag=%s",
        tamanho,
        info.get("texto") or "",
        pagina_alvo,
    )
    if set_status:
        set_status(f"Restaurando paginação da listagem ({tamanho}/página)")
    forcar_paginacao_js(driver, novo_valor=tamanho, settings=settings)
    aguardar_listagem_replicacao_carregada(
        driver,
        timeout=30,
        usar_alvo_paginador=False,
        min_linhas=0,
        tamanho_pagina=tamanho,
    )
    time.sleep(0.25)
    return True


def ir_pagina_replicacao(
    driver,
    numero: int,
    *,
    max_tentativas: int = 30,
    settings: Optional[dict] = None,
    set_status: Optional[SetStatusFn] = None,
    _restaurou_paginacao: bool = False,
) -> None:
    """Navega para a página indicada (1-based) no paginador BRFlow."""
    alvo = max(1, int(numero))
    for tentativa in range(1, max_tentativas + 1):
        info = ler_info_paginador(driver)
        atual = int(info.get("pagina_atual") or 1)
        total = int(info.get("total_paginas") or 1)
        if atual == alvo:
            return
        if alvo > total:
            if (
                not _restaurou_paginacao
                and settings is not None
                and garantir_paginacao_listagem_replicacao(
                    driver,
                    settings,
                    pagina_alvo=alvo,
                    set_status=set_status,
                )
            ):
                return ir_pagina_replicacao(
                    driver,
                    alvo,
                    max_tentativas=max_tentativas,
                    settings=settings,
                    set_status=set_status,
                    _restaurou_paginacao=True,
                )
            raise RuntimeError(
                f"Página {alvo} inexistente na listagem BRFlow (total_paginas={total})"
            )
        proximo = min(alvo, atual + 1) if alvo > atual else max(alvo, atual - 1)
        result = _clicar_pagina_replicacao(driver, proximo)
        if not result.get("ok"):
            raise RuntimeError(
                f"Falha ao navegar para página {alvo} (tentativa {tentativa}): {result}"
            )
        depois = int(
            result.get("pagina_atual_depois")
            or ler_info_paginador(driver).get("pagina_atual")
            or atual
        )
        if depois == atual and result.get("mode") != "ja_na_pagina":
            raise RuntimeError(
                f"Paginação BRFlow não avançou (continua em {atual}; alvo={alvo})"
            )
    raise RuntimeError(f"Timeout navegando para página {alvo} na listagem BRFlow")


def ir_proxima_pagina_replicacao(
    driver,
    *,
    settings: Optional[dict] = None,
    set_status: Optional[SetStatusFn] = None,
    _restaurou_paginacao: bool = False,
) -> bool:
    """Avança uma página. Retorna False se já estiver na última."""
    info = ler_info_paginador(driver)
    atual = int(info.get("pagina_atual") or 1)
    total = int(info.get("total_paginas") or 1)
    if atual >= total:
        if (
            not _restaurou_paginacao
            and settings is not None
            and paginacao_listagem_precisa_restaurar(driver, settings)
            and garantir_paginacao_listagem_replicacao(
                driver, settings, set_status=set_status
            )
        ):
            return ir_proxima_pagina_replicacao(
                driver,
                settings=settings,
                set_status=set_status,
                _restaurou_paginacao=True,
            )
        return False
    result = _clicar_pagina_replicacao(driver, atual + 1)
    if not result.get("ok"):
        raise RuntimeError(f"Falha ao avançar paginação BRFlow: {result}")
    depois = int(result.get("pagina_atual_depois") or atual)
    if depois <= atual:
        raise RuntimeError(
            f"Paginação BRFlow não avançou (continua em {atual}; esperado > {atual})"
        )
    return True


def ir_primeira_pagina_replicacao(
    driver,
    *,
    settings: Optional[dict] = None,
    set_status: Optional[SetStatusFn] = None,
) -> None:
    ir_pagina_replicacao(driver, 1, settings=settings, set_status=set_status)


def forcar_paginacao_js(driver, novo_valor=None, settings: Optional[dict] = None):
    if novo_valor is None:
        novo_valor = paginacao_tamanho(settings)
    try:
        log.info(f"[PAGINACAO_JS] Alterando paginação para {novo_valor}")

        tbody_antes = None
        try:
            tbody_antes = driver.find_element(By.XPATH, brflow.B_replicacao_listagem_tbody)
        except Exception:
            pass

        botao_dropdown = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((
                By.XPATH,
                '//*[@id="layout_layout2_panel_main"]/div[4]/div/div[2]/div[3]/div/div[1]/span/div/div/div/button[4]'
            ))
        )
        driver.execute_script("arguments[0].click();", botao_dropdown)

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((
                By.XPATH,
                f'//span[@data-nrtamanho="{novo_valor}"] | '
                f'//span[@data-nrtamanho="500"] | '
                f'//span[@data-nrtamanho="250"]',
            ))
        )

        modo = driver.execute_script(f"""
            let alvo = document.querySelector('span[data-nrtamanho="{novo_valor}"]');
            if (alvo) {{
                let li = alvo.closest('li');
                if (li) {{
                    li.click();
                    return 'direto';
                }}
            }}

            let span = document.querySelector('span[data-nrtamanho="250"]');
            if (!span) return null;

            span.setAttribute('data-nrtamanho', '{novo_valor}');
            span.innerText = '{novo_valor}';

            let li = span.closest('li');
            if (li) {{
                li.setAttribute('data-paginador-tamanho-pagina', '{novo_valor}');
                li.click();
                return 'ajuste_250';
            }}

            return null;
        """)

        if modo is None:
            raise Exception(f"Opção de paginação {novo_valor} não encontrada no dropdown")

        log.info(f"[PAGINACAO_JS] Paginação {novo_valor} selecionada ({modo})")

        if tbody_antes is not None:
            try:
                WebDriverWait(driver, 15).until(EC.staleness_of(tbody_antes))
            except TimeoutException:
                log.debug("[PAGINACAO_JS] tbody não ficou stale após mudança de página")

    except Exception as e:
        log.error(f"[PAGINACAO_JS] Erro ao alterar paginação: {e}")
        raise


def extrair_texto_situacao_linha(linha) -> str:
    """Texto de situação: .btn-status title, data-html-* ou vazio."""
    try:
        btn = linha.find_element(By.CSS_SELECTOR, brflow.B_replicacao_btn_status)
        titulo = (btn.get_attribute("title") or "").strip()
        if titulo:
            return titulo
    except Exception:
        pass
    partes: list[str] = []
    for attr in brflow.B_replicacao_td_situacao_attrs:
        for td in linha.find_elements(By.CSS_SELECTOR, f"td[{attr}]"):
            txt = (td.text or "").strip()
            if txt:
                partes.append(txt)
    if partes:
        return " | ".join(partes)
    return ""


def linha_listagem_visivel(tr) -> bool:
    try:
        return tr.is_displayed()
    except Exception:
        return False


def iterar_linhas_listagem_replicacao(driver):
    """Itera linhas visíveis da tabela (tr.js-tpl-linha após filtro JS)."""
    trs = driver.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_linha_listagem)
    if trs:
        return [tr for tr in trs if linha_listagem_visivel(tr)]
    tbody, n_tr = obter_tbody_listagem_replicacao(driver)
    if tbody is not None and n_tr > 0:
        return [tr for tr in tbody.find_elements(By.TAG_NAME, "tr") if linha_listagem_visivel(tr)]
    return [
        tr
        for tr in driver.find_elements(
            By.XPATH,
            "//tr[.//td[@data-html-nom_workflow_origem]]",
        )
        if linha_listagem_visivel(tr)
    ]


def extrair_regra_linha_replicacao(linha, driver=None) -> str:
    """Lê Nome da Regra da listagem (atributo data-html-nom_regra ou 1ª coluna)."""
    regra_tds = linha.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_td_regra)
    if regra_tds:
        regra = extrair_texto_celula_replicacao(
            regra_tds[0],
            attr_names=_ATTR_CELULA_REGRA,
            driver=driver,
        )
        if regra:
            return regra
    tds = linha.find_elements(By.TAG_NAME, "td")
    if tds:
        return extrair_texto_celula_replicacao(
            tds[0],
            attr_names=_ATTR_CELULA_REGRA,
            driver=driver,
        )
    return ""


def classificar_linha_replicacao(linha, indice: int, driver=None) -> Optional[dict]:
    """Extrai workflow + situação de uma linha da listagem BRFlow."""
    if not linha_listagem_visivel(linha):
        return None
    wf_tds = linha.find_elements(By.CSS_SELECTOR, brflow.B_replicacao_td_workflow_origem)
    if not wf_tds:
        return None
    workflow = extrair_texto_celula_replicacao(wf_tds[0], driver=driver)
    if not workflow:
        return None
    regra = extrair_regra_linha_replicacao(linha, driver=driver)
    situacao = classificar_situacao_texto(extrair_texto_situacao_linha(linha))
    return {
        "workflow": workflow,
        "_wf_key": _normalizar_workflow(workflow),
        "regra": regra,
        "_regra_key": normalizar_regra_brflow(regra),
        "situacao": situacao,
        "indice_linha": indice,
    }


def ler_linhas_replicacao(driver) -> list:
    """Lê todas as linhas da listagem (inclui duplicatas ativo/inativo)."""
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, brflow.B_replicacao_listagem_tbody))
    )
    trs = iterar_linhas_listagem_replicacao(driver)
    if not trs:
        log.warning("[LISTAGEM] Nenhuma linha com Workflow Origem encontrada")
        return []
    linhas: list = []
    for idx, tr in enumerate(trs, start=1):
        try:
            item = classificar_linha_replicacao(tr, idx, driver=driver)
            if item:
                linhas.append(item)
        except Exception as exc:
            log.debug("Listagem BRFlow | linha %d ignorada: %s", idx, exc)
    if linhas:
        log.debug(
            "Listagem BRFlow | amostra linha 1: workflow=%s situacao=%s",
            linhas[0]["workflow"],
            linhas[0]["situacao"],
        )
    return linhas


def ler_linhas_todas_paginas_replicacao(
    driver,
    settings: Optional[dict] = None,
    *,
    set_status: Optional[SetStatusFn] = None,
) -> list:
    """Percorre todas as páginas da listagem e acumula linhas (limite configurável)."""
    settings = settings or {}
    max_paginas = paginacao_max_paginas(settings)
    ir_primeira_pagina_replicacao(driver, settings=settings)
    todas: list = []
    pagina = 1
    while pagina <= max_paginas:
        if set_status:
            set_status(f"Lendo listagem BRFlow (página {pagina})")
        linhas = ler_linhas_replicacao(driver)
        for item in linhas:
            item["pagina"] = pagina
        todas.extend(linhas)
        info = ler_info_paginador(driver)
        log.info(
            "[LISTAGEM] Página %d/%d | %d linha(s) | acumulado=%d",
            int(info.get("pagina_atual") or pagina),
            int(info.get("total_paginas") or 1),
            len(linhas),
            len(todas),
        )
        if int(info.get("pagina_atual") or pagina) >= int(info.get("total_paginas") or 1):
            break
        if not ir_proxima_pagina_replicacao(driver, settings=settings):
            break
        pagina += 1
    log.info(
        "[LISTAGEM] Varredura multi-página | paginas_lidas=%d | linhas=%d | workflows_unicos=%d",
        pagina,
        len(todas),
        len({item.get("_wf_key") for item in todas if item.get("_wf_key")}),
    )
    return todas


def indexar_listagem_replicacao(
    driver,
    settings: Optional[dict] = None,
    *,
    wf_keys_alvo: Optional[Set[str]] = None,
    set_status: Optional[SetStatusFn] = None,
) -> IndiceListagemBrflow:
    """Varre páginas e indexa workflows (wf_key → página/situação) sem acumular todas as linhas."""
    settings = settings or {}
    max_paginas = paginacao_max_paginas(settings)
    parar_cedo = indexar_parar_quando_plano_completo(settings) and wf_keys_alvo
    indice = IndiceListagemBrflow()
    alvo = set(wf_keys_alvo or [])

    ir_primeira_pagina_replicacao(driver, settings=settings)
    pagina = 1
    while pagina <= max_paginas:
        if set_status:
            set_status(f"Indexando listagem BRFlow (página {pagina})")
        linhas = ler_linhas_replicacao(driver)
        for item in linhas:
            _atualizar_entrada_indice(indice, item, pagina)
        indice.linhas_lidas += len(linhas)
        indice.paginas_lidas = pagina
        info = ler_info_paginador(driver)
        log.info(
            "[LISTAGEM] Indexação pág. %d/%d | %d linha(s) | workflows=%d | alvo=%d/%d",
            int(info.get("pagina_atual") or pagina),
            int(info.get("total_paginas") or 1),
            len(linhas),
            len(indice.entradas),
            len(alvo & indice.entradas.keys()) if alvo else len(indice.entradas),
            len(alvo),
        )
        if parar_cedo and indice_cobre_alvos(indice, alvo):
            indice.parada_antecipada = True
            log.info(
                "[LISTAGEM] Indexação parada cedo: todos os %d workflow(s) alvo com linha ativa",
                len(alvo),
            )
            break
        if int(info.get("pagina_atual") or pagina) >= int(info.get("total_paginas") or 1):
            break
        if not ir_proxima_pagina_replicacao(driver, settings=settings):
            break
        pagina += 1

    info_final = ler_info_paginador(driver)
    total_paginas = int(info_final.get("total_paginas") or 1)
    if indice.paginas_lidas < total_paginas and not indice.parada_antecipada:
        log.warning(
            "[LISTAGEM] Indexação parcial | paginas_lidas=%d | total_paginas=%d",
            indice.paginas_lidas,
            total_paginas,
        )
    try:
        ir_primeira_pagina_replicacao(driver, settings=settings)
    except Exception as exc:
        log.warning("[LISTAGEM] Falha ao voltar para página 1 após indexação: %s", exc)

    log.info(
        "[LISTAGEM] Indexação concluída | paginas=%d/%d | linhas=%d | workflows=%d | parada_antecipada=%s",
        indice.paginas_lidas,
        total_paginas,
        indice.linhas_lidas,
        len(indice.entradas),
        indice.parada_antecipada,
    )
    return indice


def resumir_linhas_por_workflow(linhas: list) -> dict:
    """Agrega duplicatas por workflow normalizado."""
    resumo: dict = {}
    for item in linhas:
        key = item["_wf_key"]
        if key not in resumo:
            resumo[key] = {
                "workflow": item["workflow"],
                "tem_ativo": False,
                "tem_inativo": False,
                "linhas_ativas": 0,
                "linhas_inativas": 0,
            }
        bloco = resumo[key]
        if situacao_conta_como_ativa(item["situacao"]):
            bloco["tem_ativo"] = True
            bloco["linhas_ativas"] += 1
        elif item["situacao"] == "INATIVO":
            bloco["tem_inativo"] = True
            bloco["linhas_inativas"] += 1
    return resumo


def workflow_tem_linha_ativa_brflow(resumo: dict, workflow: str) -> bool:
    key = _normalizar_workflow(workflow)
    return bool(resumo.get(key, {}).get("tem_ativo"))


def workflow_ausente_listagem_brflow(resumo: dict, workflow: str) -> bool:
    return _normalizar_workflow(workflow) not in resumo


def eh_workflow_apenas_inativo_brflow(resumo: dict, workflow: str) -> bool:
    key = _normalizar_workflow(workflow)
    if key not in resumo:
        return False
    bloco = resumo[key]
    return bool(bloco["tem_inativo"] and not bloco["tem_ativo"])


def logar_workflow_ausente_diagnostico(
    workflow_config: str,
    workflow_brflow: str,
    linhas_br: list,
    resumo_br: dict,
) -> None:
    """Loga contexto quando workflow do plano não aparece no resumo da listagem."""
    wf_key = _normalizar_workflow(workflow_brflow)
    log.warning(
        "[PESQUISAR] Diagnóstico ausente | config=%s | brflow=%s | linhas_lidas=%d | workflows_resumo=%d",
        workflow_config,
        workflow_brflow,
        len(linhas_br),
        len(resumo_br),
    )
    parciais: list[str] = []
    for item in linhas_br:
        nome = item.get("workflow", "")
        chave = item.get("_wf_key", "")
        if not nome:
            continue
        if wf_key and (wf_key in chave or chave in wf_key):
            parciais.append(nome)
            continue
        alvo = workflow_brflow or workflow_config
        if alvo.casefold() in nome.casefold() or nome.casefold() in alvo.casefold():
            parciais.append(nome)
    if parciais:
        unicos = list(dict.fromkeys(parciais))[:5]
        log.warning("[PESQUISAR] Match parcial na listagem: %s", unicos)
    log.warning(
        "[PESQUISAR] Verifique Default.xlsx aba Workflow d1: coluna 'Workflow - selenium' "
        "deve coincidir com Workflow Origem no BRFlow"
    )


def _clicar_editar_linha(driver, tr, workflow_alvo: str, *, nome_regra: str = "") -> None:
    botao_editar = tr.find_element(
        By.XPATH,
        './/span[contains(@class,"glyphicon-pencil")]',
    )
    driver.execute_script("arguments[0].click();", botao_editar)
    if nome_regra:
        log.info(
            "[TABELA] Clique em editar (regra=%s): %s",
            suffix_regra_distintivo(nome_regra),
            workflow_alvo,
        )
    else:
        log.info("[TABELA] Clique em editar (linha visível): %s", workflow_alvo)


def _editar_workflow_na_pagina_atual(
    driver,
    workflow_alvo: str,
    *,
    apenas_ativo: bool,
    nome_regra: Optional[str] = None,
) -> bool:
    """Procura workflow na página atual e clica editar. Retorna True se encontrou."""
    wf_key = _normalizar_workflow(workflow_alvo)
    for tr in iterar_linhas_listagem_replicacao(driver):
        item = classificar_linha_replicacao(tr, 0, driver=driver)
        if not _linha_aceita(item, wf_key, apenas_ativo=apenas_ativo, nome_regra=nome_regra):
            continue
        _clicar_editar_linha(driver, tr, workflow_alvo, nome_regra=nome_regra or "")
        return True
    return False


def _localizar_linha_workflow_paginas(
    driver,
    workflow_alvo: str,
    *,
    apenas_ativo: bool,
    settings: Optional[dict] = None,
    refilter_ativos: Optional[RefilterFn] = None,
    nome_regra: Optional[str] = None,
) -> bool:
    """Varre páginas até achar linha do workflow. Retorna True se encontrou e está na página."""
    settings = settings or {}
    wf_key = _normalizar_workflow(workflow_alvo)
    max_paginas = paginacao_max_paginas(settings)
    garantir_paginacao_listagem_replicacao(driver, settings)
    ir_primeira_pagina_replicacao(driver, settings=settings)

    for pagina in range(1, max_paginas + 1):
        if refilter_ativos and apenas_ativo:
            refilter_ativos(driver, settings, motivo=f"busca workflow pág. {pagina}")
        trs = iterar_linhas_listagem_replicacao(driver)
        for tr in trs:
            item = classificar_linha_replicacao(tr, 0, driver=driver)
            if not _linha_aceita(item, wf_key, apenas_ativo=apenas_ativo, nome_regra=nome_regra):
                continue
            _clicar_editar_linha(driver, tr, workflow_alvo, nome_regra=nome_regra or "")
            return True
        info = ler_info_paginador(driver)
        if int(info.get("pagina_atual") or pagina) >= int(info.get("total_paginas") or 1):
            break
        if not ir_proxima_pagina_replicacao(driver, settings=settings):
            break
    return False


def clicar_editar_por_workflow(
    driver,
    workflow_alvo,
    apenas_ativo: bool = False,
    settings: Optional[dict] = None,
    *,
    refilter_ativos: Optional[RefilterFn] = None,
    xpath_escape: Optional[XpathEscapeFn] = None,
    indice_listagem: Optional[IndiceListagemBrflow] = None,
    nome_regra: Optional[str] = None,
) -> None:
    try:
        log.info(
            "[TABELA] Procurando workflow: %s (apenas_ativo=%s, indice=%s, regra=%s)",
            workflow_alvo,
            apenas_ativo,
            indice_listagem is not None,
            suffix_regra_distintivo(nome_regra) if nome_regra else "",
        )

        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, brflow.B_replicacao_listagem_tbody))
        )
        wf_key = _normalizar_workflow(workflow_alvo)

        pagina_indice: Optional[int] = None
        index_miss = False
        if indice_listagem is not None:
            if nome_regra:
                pagina_indice = resolver_pagina_indice_por_regra(
                    indice_listagem, wf_key, nome_regra
                )
            elif wf_key in indice_listagem.entradas:
                pagina_indice = int(indice_listagem.entradas[wf_key]["pagina"])

        if apenas_ativo and indice_listagem and pagina_indice:
            garantir_paginacao_listagem_replicacao(
                driver,
                settings,
                pagina_alvo=pagina_indice,
            )
            info = ler_info_paginador(driver)
            atual = int(info.get("pagina_atual") or 1)
            if atual != pagina_indice:
                log.info(
                    "[TABELA] Navegando para página %d (workflow=%s, regra=%s)",
                    pagina_indice,
                    workflow_alvo,
                    suffix_regra_distintivo(nome_regra) if nome_regra else "",
                )
                try:
                    ir_pagina_replicacao(
                        driver,
                        pagina_indice,
                        settings=settings,
                    )
                except RuntimeError as exc:
                    log.warning(
                        "[TABELA] Índice pág. %d indisponível (%s); fallback varredura",
                        pagina_indice,
                        exc,
                    )
                    pagina_indice = None
            if pagina_indice and _editar_workflow_na_pagina_atual(
                driver,
                workflow_alvo,
                apenas_ativo=True,
                nome_regra=nome_regra,
            ):
                return
            if pagina_indice:
                log.warning(
                    "[TABELA] Workflow %s (regra=%s) não encontrado na pág. %d do índice; fallback varredura",
                    workflow_alvo,
                    suffix_regra_distintivo(nome_regra) if nome_regra else "",
                    pagina_indice,
                )
                index_miss = True

        if apenas_ativo:
            if not index_miss:
                trs = iterar_linhas_listagem_replicacao(driver)
                if not trs and refilter_ativos:
                    refilter_ativos(driver, settings, motivo="retry antes de editar")
                    trs = iterar_linhas_listagem_replicacao(driver)
                for tr in trs:
                    item = classificar_linha_replicacao(tr, 0, driver=driver)
                    if not _linha_aceita(
                        item,
                        wf_key,
                        apenas_ativo=True,
                        nome_regra=nome_regra,
                    ):
                        continue
                    _clicar_editar_linha(driver, tr, workflow_alvo, nome_regra=nome_regra or "")
                    return
            if _localizar_linha_workflow_paginas(
                driver,
                workflow_alvo,
                apenas_ativo=True,
                settings=settings,
                refilter_ativos=refilter_ativos,
                nome_regra=nome_regra,
            ):
                return
            logar_diagnostico_listagem_replicacao(driver)
            detalhe = f" (regra={nome_regra})" if nome_regra else ""
            raise TimeoutException(
                f"Nenhuma linha ativa encontrada para workflow: {workflow_alvo}{detalhe}"
            )

        if nome_regra:
            if _localizar_linha_workflow_paginas(
                driver,
                workflow_alvo,
                apenas_ativo=False,
                settings=settings,
                refilter_ativos=refilter_ativos,
                nome_regra=nome_regra,
            ):
                return
            detalhe = f" (regra={suffix_regra_distintivo(nome_regra)})"
            raise TimeoutException(
                f"Nenhuma linha encontrada para workflow: {workflow_alvo}{detalhe}"
            )

        escape = xpath_escape or (lambda t: f"'{t}'")
        texto_xpath = escape(workflow_alvo)
        xpath_linha = f"""
        //tr[
            .//td[@data-html-nom_workflow_origem
            and (
                contains(normalize-space(), {texto_xpath})
                or contains(@data-html-nom_workflow_origem, {texto_xpath})
            )]
        ]
        """
        linha = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, xpath_linha))
        )
        botao_editar = linha.find_element(
            By.XPATH,
            './/span[contains(@class,"glyphicon-pencil")]',
        )
        driver.execute_script("arguments[0].click();", botao_editar)
        log.info(f"[TABELA] Clique em editar realizado para: {workflow_alvo}")

    except Exception as e:
        log.error(f"[TABELA] Erro ao clicar no editar: {e}")
        raise
