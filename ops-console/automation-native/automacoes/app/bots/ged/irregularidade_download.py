"""Núcleo Selenium compartilhado — download do Relatório de Irregularidades GED."""
from __future__ import annotations

import calendar
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import DEFAULT_DOWNLOAD, TIMEOUT_DRIVER_ROTINA, TIMEOUT_SHORT_ROTINA
from app.config.selectors import ged
from app.core.credentials import get_ged_credentials
from app.infrastructure.confer_helpers import fechar_calendario_se_aberto
from app.infrastructure.selenium_helpers import (
    click_element,
    create_driver,
    send_keys_to_element,
    wait_for_element,
)

log = logging.getLogger("robots.ged_irregularidade")

_SUFFIXES_TMP = (".crdownload", ".tmp", ".part", ".download", ".unconfirmed")
_GED_FALLBACK_HTTP_SEGUNDOS = 90
_PREFIXOS_DOWNLOAD_IRREGULARIDADE = ("irregularidades", "irregularidade", "relatorio")


@dataclass(frozen=True)
class QuinzenaIrregularidade:
    yyyymm: str
    numero: int
    data_inicio: date
    data_fim: date


@dataclass
class GedIrregularidadeCallbacks:
    set_status: Callable[[str], None]
    set_progress: Callable[[int, str], None] | None = None


def cfg_headless() -> bool:
    raw = (
        os.getenv("GED_HEADLESS")
        or os.getenv("ROTINA_HEADLESS")
        or os.getenv("ROBOT_HEADLESS")
        or os.getenv("HEADLESS")
        or "0"
    ).strip().lower()
    return raw in ("1", "true", "yes", "on")


def criar_driver_ged(download_dir: Path):
    return create_driver(
        headless=cfg_headless(),
        download_dir=str(download_dir),
        ignore_certificate_errors=True,
    )


def ultimo_dia_mes(ano: int, mes: int) -> int:
    return calendar.monthrange(ano, mes)[1]


def mes_anterior(ano: int, mes: int) -> tuple[int, int]:
    if mes == 1:
        return ano - 1, 12
    return ano, mes - 1


def formatar_data_ged(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def quinzena_completa(ano: int, mes: int, numero: int) -> tuple[date, date]:
    if numero == 1:
        return date(ano, mes, 1), date(ano, mes, 15)
    ultimo = ultimo_dia_mes(ano, mes)
    return date(ano, mes, 16), date(ano, mes, ultimo)


def quinzenas_para_atualizar(data_ref: date) -> list[QuinzenaIrregularidade]:
    """Retorna as duas quinzenas a baixar com base em D-1."""
    ano, mes, dia = data_ref.year, data_ref.month, data_ref.day

    if dia <= 15:
        prev_ano, prev_mes = mes_anterior(ano, mes)
        inicio_prev, fim_prev = quinzena_completa(prev_ano, prev_mes, 2)
        return [
            QuinzenaIrregularidade(
                yyyymm=f"{prev_ano}{prev_mes:02d}",
                numero=2,
                data_inicio=inicio_prev,
                data_fim=fim_prev,
            ),
            QuinzenaIrregularidade(
                yyyymm=f"{ano}{mes:02d}",
                numero=1,
                data_inicio=date(ano, mes, 1),
                data_fim=data_ref,
            ),
        ]

    inicio_q1, fim_q1 = quinzena_completa(ano, mes, 1)
    return [
        QuinzenaIrregularidade(
            yyyymm=f"{ano}{mes:02d}",
            numero=1,
            data_inicio=inicio_q1,
            data_fim=fim_q1,
        ),
        QuinzenaIrregularidade(
            yyyymm=f"{ano}{mes:02d}",
            numero=2,
            data_inicio=date(ano, mes, 16),
            data_fim=data_ref,
        ),
    ]


def obter_data_ref_irregularidade(*, data_base: date | None = None) -> date:
    base = data_base or date.today()
    return base - timedelta(days=1)


def descricao_quinzena(quinzena: QuinzenaIrregularidade) -> str:
    return (
        f"{quinzena.yyyymm}_{quinzena.numero} "
        f"({formatar_data_ged(quinzena.data_inicio)}..{formatar_data_ged(quinzena.data_fim)})"
    )


def _clicar_com_retry(drv, locator: str, by=By.XPATH, tentativas: int = 3, descricao: str = "elemento") -> bool:
    wait = WebDriverWait(drv, TIMEOUT_SHORT_ROTINA)
    ultimo_erro: Exception | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            elemento = wait.until(EC.element_to_be_clickable((by, locator)))
            drv.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", elemento)
            time.sleep(0.3)
            try:
                elemento.click()
            except Exception:
                drv.execute_script("arguments[0].click();", elemento)
            return True
        except Exception as exc:
            ultimo_erro = exc
            log.warning("Falha ao clicar em %s (tentativa %s/%s): %s", descricao, tentativa, tentativas, exc)
            time.sleep(1)
    if ultimo_erro:
        raise ultimo_erro
    return False


def _limpar_campo_data(drv, elemento_id: str) -> None:
    wait_short = WebDriverWait(drv, TIMEOUT_SHORT_ROTINA)
    elemento = wait_short.until(EC.presence_of_element_located((By.ID, elemento_id)))
    try:
        elemento.click()
        elemento.send_keys(Keys.CONTROL, "a")
        elemento.send_keys(Keys.DELETE)
    except Exception:
        pass
    try:
        drv.execute_script(
            "arguments[0].value = '';"
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
            elemento,
        )
    except Exception:
        pass


def _preencher_campo_data(drv, elemento_id: str, valor: str) -> None:
    _limpar_campo_data(drv, elemento_id)
    wait_short = WebDriverWait(drv, TIMEOUT_SHORT_ROTINA)
    elemento = wait_short.until(EC.element_to_be_clickable((By.ID, elemento_id)))
    try:
        drv.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
            elemento,
            valor,
        )
    except Exception:
        elemento.send_keys(valor)


def _preencher_periodo_contestacao(drv, data_inicio: str, data_fim: str) -> None:
    _limpar_campo_data(drv, ged.DATA_FIM)
    _limpar_campo_data(drv, ged.DATA_INICIO)
    _preencher_campo_data(drv, ged.DATA_INICIO, data_inicio)
    _preencher_campo_data(drv, ged.DATA_FIM, data_fim)
    fechar_calendario_se_aberto(drv)
    time.sleep(0.5)


def _aguardar_tela_irregularidades(drv) -> None:
    wait = WebDriverWait(drv, TIMEOUT_DRIVER_ROTINA)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ged.REL_IRREGULARIDADES)))
    wait.until(EC.element_to_be_clickable((By.ID, ged.DATA_INICIO)))


def _clicar_formato_csv(drv) -> None:
    wait = WebDriverWait(drv, TIMEOUT_DRIVER_ROTINA)
    locators = [
        (By.XPATH, ged.FORMATO_CSV_XPATH),
        (By.CSS_SELECTOR, ged.FORMATO_CSV_CSS),
        (By.XPATH, ged.FORMATO_CSV_DIV_XPATH),
        (By.ID, ged.FORMATO_CSV_INPUT_ID),
    ]
    ultimo_erro: Exception | None = None
    for by, locator in locators:
        try:
            wait.until(EC.element_to_be_clickable((by, locator)))
            _clicar_com_retry(drv, locator, by=by, descricao="formato CSV")
            return
        except Exception as exc:
            ultimo_erro = exc
            log.warning("Falha ao selecionar CSV com %s=%s: %s", by, str(locator)[:80], exc)
    if ultimo_erro:
        raise ultimo_erro
    raise RuntimeError("Não foi possível selecionar formato CSV")


def login_ged(drv, cb: GedIrregularidadeCallbacks) -> None:
    ged_user, ged_pass = get_ged_credentials()
    if not ged_user or not ged_pass:
        raise RuntimeError("Credenciais GED ausentes (GED_USER / GED_PASS)")

    cb.set_status("Acessando portal GED para irregularidades")
    drv.get(ged.LOGIN_URL)
    send_keys_to_element(drv, By.XPATH, ged.LOGIN_USER, ged_user)
    send_keys_to_element(drv, By.XPATH, ged.LOGIN_PASS, ged_pass)
    click_element(drv, By.XPATH, ged.LOGIN_SUBMIT)

    try:
        wait_for_element(drv, By.XPATH, ged.MENU_HOME, timeout=30)
        cb.set_status("Login no GED realizado com sucesso")
    except Exception:
        wait_for_element(drv, By.XPATH, "/html/body/app-root/app-message/div/div/div", timeout=10)
        drv.get("https://ged-web-api.claro.br.experian.eeco/api/v1/autenticacao/autenticar")
        drv.get(ged.LOGIN_URL)
        send_keys_to_element(drv, By.XPATH, ged.LOGIN_USER, ged_user)
        send_keys_to_element(drv, By.XPATH, ged.LOGIN_PASS, ged_pass)
        click_element(drv, By.XPATH, ged.LOGIN_SUBMIT)
        wait_for_element(drv, By.XPATH, ged.MENU_HOME, timeout=30)
        cb.set_status("Login no GED realizado com sucesso (retry)")


def _clicar_pos_venda(drv) -> None:
    locators = [
        (By.XPATH, ged.POS_VENDA_XPATH),
        (By.CSS_SELECTOR, ged.POS_VENDA_CSS),
    ]
    ultimo_erro: Exception | None = None
    for by, locator in locators:
        try:
            _clicar_com_retry(drv, locator, by=by, descricao="Pós-Venda")
            return
        except Exception as exc:
            ultimo_erro = exc
            log.warning("Falha ao clicar Pós-Venda com %s=%s: %s", by, locator[:80], exc)
    if ultimo_erro:
        raise ultimo_erro
    raise RuntimeError("Não foi possível clicar em Pós-Venda")


def _navegar_relatorio_irregularidades(drv, data_inicio: str, data_fim: str) -> None:
    _clicar_pos_venda(drv)
    time.sleep(0.5)
    _clicar_com_retry(drv, ged.RELATORIO_IRREGULARIDADES, descricao="Relatório de Irregularidades")
    _aguardar_tela_irregularidades(drv)
    _preencher_periodo_contestacao(drv, data_inicio, data_fim)
    _clicar_formato_csv(drv)
    time.sleep(0.3)
    _clicar_com_retry(drv, ged.BTN_CONSULTAR, descricao="Consultar")


def _atualizar_consulta_irregularidades(drv, data_inicio: str, data_fim: str) -> None:
    _aguardar_tela_irregularidades(drv)
    _preencher_periodo_contestacao(drv, data_inicio, data_fim)
    time.sleep(0.3)
    _clicar_com_retry(drv, ged.BTN_CONSULTAR, descricao="Consultar")


def _preparar_consulta_irregularidades(
    drv,
    data_inicio: str,
    data_fim: str,
    *,
    navegacao_completa: bool,
) -> None:
    if navegacao_completa:
        _navegar_relatorio_irregularidades(drv, data_inicio, data_fim)
        return
    try:
        _atualizar_consulta_irregularidades(drv, data_inicio, data_fim)
    except Exception as exc:
        log.warning(
            "Irregularidade GED: falha ao atualizar consulta na aba 1 (%s); refazendo navegação completa",
            exc,
        )
        _navegar_relatorio_irregularidades(drv, data_inicio, data_fim)


def _listar_handles_protocolo(drv) -> list[str]:
    encontrados: list[str] = []
    atual = None
    try:
        atual = drv.current_window_handle
    except Exception:
        pass

    for handle in list(drv.window_handles):
        try:
            drv.switch_to.window(handle)
            current = (drv.current_url or "").strip()
            if current.startswith(ged.PROTOCOLO_BASE_URL):
                encontrados.append(handle)
        except Exception:
            continue

    if atual and atual in list(drv.window_handles):
        try:
            drv.switch_to.window(atual)
        except Exception:
            pass
    return encontrados


def trocar_para_guia_protocolo(drv, cb: GedIrregularidadeCallbacks, timeout: int = 30) -> None:
    cb.set_status("Aguardando guia de protocolo do GED")
    inicio = time.time()
    while (time.time() - inicio) < timeout:
        protocolos = _listar_handles_protocolo(drv)
        if protocolos:
            escolhido = protocolos[-1]
            drv.switch_to.window(escolhido)
            log.info(
                "Irregularidade GED: guia protocolo focada | total=%s | escolhida=%s | url=%s",
                len(protocolos),
                escolhido,
                (drv.current_url or "").strip(),
            )
            return
        time.sleep(0.5)
    raise TimeoutError("Não foi possível localizar a guia de protocolo do GED")


def fechar_guias_protocolo_ged(drv) -> int:
    fechadas = 0
    for handle in list(_listar_handles_protocolo(drv)):
        try:
            drv.switch_to.window(handle)
            drv.close()
            fechadas += 1
        except Exception as exc:
            log.warning("Irregularidade GED: falha ao fechar guia protocolo %s: %s", handle, exc)

    restantes = list(drv.window_handles)
    if restantes:
        for handle in restantes:
            try:
                drv.switch_to.window(handle)
                current = (drv.current_url or "").strip()
                if not current.startswith(ged.PROTOCOLO_BASE_URL):
                    break
            except Exception:
                continue
        else:
            try:
                drv.switch_to.window(restantes[0])
            except Exception:
                pass

    if fechadas:
        log.info("Irregularidade GED: fechou %s guia(s) /protocolo", fechadas)
    return fechadas


def voltar_para_guia_principal_ged(drv, cb: GedIrregularidadeCallbacks, timeout: int = 15) -> None:
    fechar_guias_protocolo_ged(drv)
    inicio = time.time()
    while (time.time() - inicio) < timeout:
        handles = list(drv.window_handles)
        if not handles:
            raise RuntimeError("Nenhuma guia do GED disponível")

        for handle in handles:
            try:
                drv.switch_to.window(handle)
                current = (drv.current_url or "").strip()
                if current.startswith(ged.PROTOCOLO_BASE_URL):
                    continue
                log.info("Irregularidade GED: voltou para aba 1 | url=%s", current)
                cb.set_status("Irregularidade GED: voltou para aba principal")
                time.sleep(0.5)
                return
            except Exception:
                continue
        time.sleep(0.5)

    handles = list(drv.window_handles)
    if not handles:
        raise RuntimeError("Nenhuma guia do GED disponível")
    drv.switch_to.window(handles[0])
    log.info("Irregularidade GED: foco forçado na aba 1 | url=%s", drv.current_url or "")
    cb.set_status("Irregularidade GED: voltou para aba principal")
    time.sleep(0.5)


def _encontrar_link_download(drv, timeout: float = 2):
    for xpath in ged.DOWNLOAD_LINK_XPATHS:
        link = wait_for_element(drv, By.XPATH, xpath, timeout=timeout)
        if link:
            href = (link.get_attribute("href") or "").strip()
            return link, href, xpath
    return None, "", ""


def _aguardar_link_download(drv, cb: GedIrregularidadeCallbacks, timeout: int = 120):
    trocar_para_guia_protocolo(drv, cb)
    cb.set_status("Aguardando link Baixar arquivo (GED irregularidades)")
    inicio = time.time()
    while (time.time() - inicio) < timeout:
        link, href, xpath = _encontrar_link_download(drv, timeout=2)
        if link:
            return link, href, xpath
        time.sleep(0.3)
    raise TimeoutError("Relatório processado, mas link de download não apareceu")


def aguardar_progresso_100(drv, cb: GedIrregularidadeCallbacks, timeout: int = 3600) -> None:
    cb.set_status("Aguardando processamento do relatório GED")
    inicio = time.time()
    barra_vista = False
    barra_sumiu_em: float | None = None
    while (time.time() - inicio) < timeout:
        link, _, _ = _encontrar_link_download(drv, timeout=1)
        if link:
            return

        barra = wait_for_element(drv, By.XPATH, ged.PROGRESS_XPATH, timeout=1)
        if barra:
            barra_vista = True
            barra_sumiu_em = None
            try:
                valor = (barra.get_attribute("value") or barra.get_attribute("aria-valuenow") or "").strip()
                if valor.replace(".", "").isdigit() and float(valor.replace(",", ".")) >= 100:
                    return
            except (StaleElementReferenceException, ValueError):
                pass
        elif barra_vista:
            if barra_sumiu_em is None:
                barra_sumiu_em = time.time()
            link, _, _ = _encontrar_link_download(drv, timeout=2)
            if link:
                return
            if (time.time() - barra_sumiu_em) >= 120:
                raise TimeoutError("Relatório processado, mas link de download não apareceu")
        time.sleep(0.5)
    raise TimeoutError("Tempo limite excedido aguardando processamento do GED")


def eh_arquivo_temp(nome: str) -> bool:
    lower = nome.lower()
    return any(lower.endswith(suf) for suf in _SUFFIXES_TMP)


def eh_arquivo_irregularidade(caminho: Path) -> bool:
    if not caminho.is_file() or eh_arquivo_temp(caminho.name):
        return False
    lower = caminho.name.lower()
    return any(lower.startswith(pref) for pref in _PREFIXOS_DOWNLOAD_IRREGULARIDADE)


def _nome_download_normalizado(nome: str) -> str:
    if Path(nome).suffix:
        return nome
    return f"{nome}.csv"


def _pastas_busca_download(download_dir: Path) -> list[Path]:
    pastas: list[Path] = []
    vistos: set[str] = set()

    def _add(p: Path) -> None:
        try:
            chave = str(p.resolve())
        except OSError:
            return
        if chave in vistos:
            return
        vistos.add(chave)
        pastas.append(p)

    _add(download_dir.resolve())
    _add(DEFAULT_DOWNLOAD.resolve())
    return pastas


def _pastas_temp_download(download_dir: Path) -> list[Path]:
    return [download_dir.resolve()]


def _tem_par_final_para_temp(temp_arq: Path, download_dir: Path) -> bool:
    if not temp_arq.is_file() or not eh_arquivo_temp(temp_arq.name):
        return False
    stem = temp_arq.stem
    possiveis = (
        download_dir / stem,
        download_dir / f"{stem}.csv",
        download_dir / f"{stem}.xlsx",
        download_dir / f"{stem}.xls",
    )
    for candidato in possiveis:
        try:
            if eh_arquivo_irregularidade(candidato) and candidato.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def limpar_temporarios_orfaos(download_dir: Path) -> int:
    download_dir = download_dir.resolve()
    removidos = 0
    if not download_dir.exists():
        return removidos
    for arq in download_dir.iterdir():
        if not arq.is_file() or not eh_arquivo_temp(arq.name):
            continue
        if not _tem_par_final_para_temp(arq, download_dir):
            continue
        try:
            arq.unlink()
            removidos += 1
            log.info("Irregularidade GED: temporário órfão removido: %s", arq.name)
        except OSError as exc:
            log.warning(
                "Irregularidade GED: falha ao remover temporário órfão %s: %s",
                arq.name,
                exc,
            )
    return removidos


def _download_em_andamento_worker(download_dir: Path) -> bool:
    limpar_temporarios_orfaos(download_dir)
    for pasta in _pastas_temp_download(download_dir):
        if not pasta.exists():
            continue
        for arq in pasta.iterdir():
            if arq.is_file() and eh_arquivo_temp(arq.name):
                return True
    return False


def limpar_arquivos_temp_worker(pasta: Path) -> None:
    if not pasta.exists():
        return
    for sufixo in _SUFFIXES_TMP:
        for arq in pasta.glob(f"*{sufixo}"):
            try:
                if arq.is_file():
                    arq.unlink()
            except OSError:
                pass


def _snapshot_pastas_download(pastas: list[Path]) -> dict[str, set[str]]:
    snapshot: dict[str, set[str]] = {}
    for pasta in pastas:
        try:
            chave = str(pasta.resolve())
        except OSError:
            continue
        if not pasta.exists():
            snapshot[chave] = set()
            continue
        snapshot[chave] = {p.name for p in pasta.iterdir() if p.is_file()}
    return snapshot


def _candidatos_download_irregularidade(
    pasta: Path,
    before: set[str],
    inicio_clique: float,
) -> list[Path]:
    limite_mtime = inicio_clique - 5.0
    candidatos: list[Path] = []
    if not pasta.exists():
        return candidatos
    for arq in pasta.iterdir():
        if not eh_arquivo_irregularidade(arq):
            continue
        try:
            if arq.stat().st_size <= 0:
                continue
            if arq.name not in before or arq.stat().st_mtime >= limite_mtime:
                candidatos.append(arq)
        except OSError:
            continue
    return candidatos


def _mover_download_para_pasta_worker(origem: Path, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    nome_destino = _nome_download_normalizado(origem.name)
    destino = download_dir / nome_destino
    if destino.resolve() == origem.resolve():
        return destino
    if destino.exists():
        destino.unlink(missing_ok=True)

    try:
        from_downloads = origem.resolve().parent == DEFAULT_DOWNLOAD.resolve()
    except OSError:
        from_downloads = False

    if from_downloads:
        shutil.copy2(str(origem), str(destino))
        origem.unlink(missing_ok=True)
        log.info("Irregularidade GED: arquivo copiado de Downloads para %s", destino.name)
    else:
        shutil.move(str(origem), str(destino))
        log.info("Irregularidade GED: arquivo movido para %s", destino.name)
    return destino


def _finalizar_caminho_download(candidato: Path, download_dir: Path) -> Path:
    if candidato.resolve().parent != download_dir.resolve():
        return _mover_download_para_pasta_worker(candidato, download_dir)
    if not Path(candidato.name).suffix:
        destino = download_dir / _nome_download_normalizado(candidato.name)
        if destino.resolve() != candidato.resolve():
            candidato.rename(destino)
            return destino
    return candidato


def aguardar_download_csv(
    download_dir: Path,
    before_por_pasta: dict[str, set[str]],
    inicio_clique: float,
    cb: GedIrregularidadeCallbacks,
    timeout: int = 1800,
    ged_drv=None,
    href_fallback: str | None = None,
    data_inicio_fmt: str | None = None,
    data_fim_fmt: str | None = None,
) -> Path:
    download_dir = download_dir.resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    pastas = _pastas_busca_download(download_dir)
    nomes_pastas = ", ".join(p.name for p in pastas)
    cb.set_status(f"Aguardando download irregularidades em: {nomes_pastas}")

    inicio = time.time()
    ultimo_nome: str | None = None
    tamanho_anterior = -1
    estavel_por = 0
    ultimo_log_vazio = 0.0
    fallback_tentado = False

    while (time.time() - inicio) < timeout:
        candidatos_globais: list[Path] = []

        for pasta in pastas:
            try:
                chave = str(pasta.resolve())
            except OSError:
                continue
            before = before_por_pasta.get(chave, set())
            candidatos_globais.extend(
                _candidatos_download_irregularidade(pasta, before, inicio_clique)
            )

        if candidatos_globais:
            candidato = max(candidatos_globais, key=lambda p: p.stat().st_mtime)
            try:
                tamanho_atual = candidato.stat().st_size
            except OSError:
                time.sleep(0.7)
                continue

            if candidato.name == ultimo_nome and tamanho_atual == tamanho_anterior:
                estavel_por += 1
            else:
                ultimo_nome = candidato.name
                tamanho_anterior = tamanho_atual
                estavel_por = 0

            if estavel_por >= 2:
                try:
                    candidato = _finalizar_caminho_download(candidato, download_dir)
                except OSError as exc:
                    log.warning("Irregularidade GED: falha ao mover download: %s", exc)
                cb.set_status(f"Irregularidade GED: download detectado ({candidato.name})")
                return candidato

        if _download_em_andamento_worker(download_dir):
            time.sleep(0.7)
            continue

        if not candidatos_globais:
            if (
                not fallback_tentado
                and href_fallback
                and ged_drv is not None
                and data_inicio_fmt
                and data_fim_fmt
                and (time.time() - inicio) >= _GED_FALLBACK_HTTP_SEGUNDOS
            ):
                fallback_tentado = True
                try:
                    from app.bots.bot_ged import _baixar_ged_via_http

                    cb.set_status("Irregularidade GED: tentando download via HTTP")
                    return _baixar_ged_via_http(
                        ged_drv, href_fallback, download_dir, data_inicio_fmt, data_fim_fmt,
                    )
                except Exception as exc:
                    log.warning("Irregularidade GED: fallback HTTP falhou: %s", exc)

            if (time.time() - ultimo_log_vazio) >= 15:
                ultimo_log_vazio = time.time()
                log.info("Irregularidade GED: aguardando novo arquivo em %s", nomes_pastas)
            time.sleep(0.7)
            continue

        time.sleep(0.7)

    raise TimeoutError(
        f"Timeout aguardando download CSV de irregularidades (pastas: {nomes_pastas})"
    )


def limpar_pasta_worker(pasta_temp: Path) -> None:
    for arq in pasta_temp.glob("*"):
        try:
            if arq.is_file():
                arq.unlink()
        except OSError:
            pass
    limpar_arquivos_temp_worker(pasta_temp)


def download_quinzena_irregularidade(
    ged_drv,
    pasta_temp: Path,
    quinzena: QuinzenaIrregularidade,
    indice: int,
    total: int,
    cb: GedIrregularidadeCallbacks,
    *,
    progress_base: int = 62,
    progress_span: int = 6,
) -> Path | None:
    """Baixa CSV bruto de uma quinzena. Retorna Path do arquivo ou None se vazio."""
    data_inicio_fmt = formatar_data_ged(quinzena.data_inicio)
    data_fim_fmt = formatar_data_ged(quinzena.data_fim)
    descricao = descricao_quinzena(quinzena)

    try:
        if cb.set_progress:
            cb.set_progress(
                progress_base + int((indice - 1) * progress_span / max(total, 1)),
                f"Irregularidade GED [{indice}/{total}]: {descricao}",
            )
        cb.set_status(f"Irregularidade GED [{indice}/{total}]: {data_inicio_fmt} → {data_fim_fmt}")

        limpar_pasta_worker(pasta_temp)
        _preparar_consulta_irregularidades(
            ged_drv,
            data_inicio_fmt,
            data_fim_fmt,
            navegacao_completa=(indice == 1),
        )

        trocar_para_guia_protocolo(ged_drv, cb)
        aguardar_progresso_100(ged_drv, cb)
        trocar_para_guia_protocolo(ged_drv, cb)

        link, href, xpath = _aguardar_link_download(ged_drv, cb)
        limpar_arquivos_temp_worker(pasta_temp)
        pastas_busca = _pastas_busca_download(pasta_temp)
        before_por_pasta = _snapshot_pastas_download(pastas_busca)
        inicio_clique = time.time()
        href_http = href if href and not href.lower().startswith("javascript") else None
        cb.set_status(f"Irregularidade GED [{indice}/{total}]: clicando em Baixar arquivo")
        try:
            link.click()
        except Exception:
            ged_drv.execute_script("arguments[0].click();", link)

        arquivo_baixado = aguardar_download_csv(
            pasta_temp,
            before_por_pasta,
            inicio_clique,
            cb,
            ged_drv=ged_drv,
            href_fallback=href_http,
            data_inicio_fmt=data_inicio_fmt,
            data_fim_fmt=data_fim_fmt,
        )
        log.info(
            "Irregularidade GED [%s/%s] download concluído | quinzena=%s | arquivo=%s | xpath=%s",
            indice,
            total,
            descricao,
            arquivo_baixado.name,
            xpath,
        )

        if arquivo_baixado.stat().st_size <= 0:
            log.warning("Relatório de irregularidades vazio (%s)", descricao)
            return None
        return arquivo_baixado
    finally:
        if indice < total:
            try:
                voltar_para_guia_principal_ged(ged_drv, cb)
            except Exception as exc:
                log.warning(
                    "Irregularidade GED [%s/%s]: falha ao voltar para aba 1: %s",
                    indice,
                    total,
                    exc,
                )
