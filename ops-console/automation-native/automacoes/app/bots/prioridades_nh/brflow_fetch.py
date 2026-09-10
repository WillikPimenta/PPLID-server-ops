"""Cliente BrFlow para pesquisa de prioridade por nível hierárquico."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable
from urllib.parse import urlencode

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger("robots.prioridades_nh")

BRFLOW_ORIGIN = "https://brflow.com.br"
BRFLOW_INDEX = f"{BRFLOW_ORIGIN}/BrFlow/index/index"
BRFLOW_PAGE_PATH = "/BrFlow/configuracao/ver-pesquisa-nivel-hierarquico-prioridade/"
BRFLOW_PAGE_ROUTE = "BrFlow/configuracao/ver-pesquisa-nivel-hierarquico-prioridade"
BRFLOW_SEARCH_URL = (
    f"{BRFLOW_ORIGIN}/BrFlow/configuracao/pesquisar-prioridade-nivel-hierarquico?"
)

# XPath informado pelo usuário (menu Configurações → Prioridade NH x WF)
MENU_PRIORIDADE_XPATH = '//*[@id="menu"]/div[2]/div/div[2]/div[1]/fieldset[3]/ul/li[3]/a'


def _switch_to_brflow_window(driver) -> None:
    for handle in list(driver.window_handles):
        driver.switch_to.window(handle)
        if "brflow.com.br" in (driver.current_url or "").lower():
            return


def _canonicalize_brflow_host(driver) -> None:
    url = driver.current_url or ""
    lower = url.lower()
    if "www.brflow.com.br" in lower:
        target = url.replace("https://www.brflow.com.br", BRFLOW_ORIGIN).replace(
            "http://www.brflow.com.br", BRFLOW_ORIGIN
        )
        log.info("BrFlow: normalizando host www → apex")
        driver.get(target)
        time.sleep(1.5)


def _wait_loading_gone(driver, *, timeout: float = 20) -> None:
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.ID, "sistema-loading"))
        )
    except Exception:
        pass


def _page_has_prioridade_form(driver) -> bool:
    """
    True só com o formulário Pesquisa aberto — NÃO confundir com o link
    'Prioridade NH x WF' na página inicial / menu.
    """
    # Sinais exclusivos da tela (não existem no menu home)
    for css in (
        "form[name='cadastro'] select.consulta",
        "select.consulta",
        "form[name='cadastro'] .data-tipo-consulta-nivel",
        "input[name='formato'][value='1']",
        "form[name='cadastro'] input[name='prioridades[]']",
    ):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, css)
            if els:
                return True
        except Exception:
            continue

    # Título da barra + formulário Pesquisa (evita match só no link do menu)
    try:
        has_title = bool(
            driver.find_elements(
                By.XPATH,
                "//*[contains(@class,'titulo') or contains(@class,'breadcrumb') or self::h1 or self::h2]"
                "[contains(., 'Prioridade NH')]",
            )
        )
        has_pesquisa = bool(
            driver.find_elements(
                By.XPATH,
                "//*[contains(@class,'panel') or contains(@class,'box') or self::legend or self::h3]"
                "[contains(normalize-space(.), 'Pesquisa')]",
            )
        )
        has_tipo = bool(
            driver.find_elements(
                By.XPATH,
                "//*[contains(normalize-space(.), 'Tipo de Consulta')]",
            )
        )
        if has_title and has_pesquisa and has_tipo:
            return True
        if has_pesquisa and has_tipo and driver.find_elements(By.CSS_SELECTOR, "select.consulta, select"):
            return True
    except Exception:
        pass
    return False


def _ensure_brflow_index(driver) -> None:
    _switch_to_brflow_window(driver)
    _canonicalize_brflow_host(driver)
    url = (driver.current_url or "").lower()
    if "brflow.com.br" not in url:
        driver.get(BRFLOW_INDEX)
        time.sleep(2)
    elif "/brflow/index" not in url:
        # Mantém SPA: voltar ao index para o menu home aparecer
        driver.get(BRFLOW_INDEX)
        time.sleep(2)
    _wait_loading_gone(driver)


def _click_el(driver, el) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.25)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)


def _try_menu_click(driver) -> bool:
    """Clica no item Prioridade NH x WF do menu home / sidebar."""
    xpaths = (
        MENU_PRIORIDADE_XPATH,
        '//*[@id="menu"]//a[normalize-space()="Prioridade NH x WF"]',
        '//a[normalize-space()="Prioridade NH x WF"]',
        '//fieldset[.//legend[contains(.,"Configura") or contains(.,"Configurações")]]'
        '//a[contains(.,"Prioridade NH")]',
        '//a[contains(@data-ascii,"prioridade") and contains(@data-ascii,"nh")]',
        '//a[contains(., "Prioridade NH x WF")]',
    )
    for xpath in xpaths:
        try:
            els = driver.find_elements(By.XPATH, xpath)
            if not els:
                continue
            el = els[0]
            _click_el(driver, el)
            log.info("BrFlow: menu Prioridade NH clicado (%s)", xpath)
            time.sleep(2.0)
            _wait_loading_gone(driver, timeout=25)
            # Aguarda o formulário de fato (não o link do menu)
            try:
                WebDriverWait(driver, 15).until(lambda d: _page_has_prioridade_form(d))
            except Exception:
                pass
            if _page_has_prioridade_form(driver):
                return True
            log.warning("BrFlow: clique em %s não abriu o formulário Pesquisa", xpath)
        except Exception:
            log.debug("Falha clique menu xpath=%s", xpath, exc_info=True)
            continue
    return False


def _try_controles_tela(driver) -> bool:
    script = """
    const done = arguments[arguments.length - 1];
    const route = arguments[0];
    try {
      if (typeof Controles !== 'undefined' && Controles && typeof Controles.Tela === 'function') {
        Controles.Tela(route);
        done({ ok: true, via: 'Controles.Tela' });
        return;
      }
      done({ ok: false, via: 'none' });
    } catch (e) {
      done({ ok: false, via: 'error', err: String(e) });
    }
    """
    try:
        driver.set_script_timeout(60)
        raw = driver.execute_async_script(script, BRFLOW_PAGE_ROUTE)
        if isinstance(raw, dict) and raw.get("ok"):
            log.info("BrFlow: Controles.Tela disparado")
            time.sleep(2)
            _wait_loading_gone(driver)
            try:
                WebDriverWait(driver, 12).until(lambda d: _page_has_prioridade_form(d))
            except Exception:
                pass
            return _page_has_prioridade_form(driver)
    except Exception:
        log.debug("Controles.Tela indisponível", exc_info=True)
    return False


def _inject_page_xhr(driver) -> bool:
    script = """
    const done = arguments[arguments.length - 1];
    const path = arguments[0];
    const url = path + (path.includes('?') ? '&' : '?') + 'now=' + Date.now();
    fetch(url, {
      method: 'GET',
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'text/plain, */*; q=0.01'
      },
      credentials: 'include'
    })
      .then(async (r) => {
        const text = await r.text();
        if (!r.ok || !text || text.length < 50) {
          done({ ok: false, status: r.status, len: (text || '').length });
          return;
        }
        try {
          const main = document.querySelector('#layout_layout2_panel_main .w2ui-panel-content')
            || document.querySelector('#layout_layout2_panel_main')
            || document.querySelector('#w2ui-content')
            || document.querySelector('#content')
            || document.body;
          const wrap = document.createElement('div');
          wrap.innerHTML = text;
          const scripts = Array.from(wrap.querySelectorAll('script'));
          scripts.forEach((old) => old.parentNode && old.parentNode.removeChild(old));
          main.innerHTML = '';
          main.appendChild(wrap);
          scripts.forEach((old) => {
            const s = document.createElement('script');
            if (old.src) s.src = old.src;
            else s.text = old.textContent || '';
            document.body.appendChild(s);
          });
          done({ ok: true, status: r.status, len: text.length });
        } catch (e) {
          done({ ok: false, status: r.status, err: String(e) });
        }
      })
      .catch((e) => done({ ok: false, status: 0, err: String(e) }));
    """
    driver.set_script_timeout(60)
    raw = driver.execute_async_script(script, BRFLOW_PAGE_PATH)
    if isinstance(raw, dict) and raw.get("ok"):
        log.info("BrFlow: tela injetada via XHR (bytes=%s)", raw.get("len"))
        time.sleep(1.5)
        _wait_loading_gone(driver)
        return _page_has_prioridade_form(driver)
    log.warning("BrFlow: falha XHR ao abrir prioridade: %s", raw)
    return False


def select_tipo_consulta_nivel(driver) -> None:
    """Seleciona Tipo de Consulta = Nível Hierárquico (codConsulta=1) e revela o campo NH."""
    # Prefer select.consulta
    selects = driver.find_elements(By.CSS_SELECTOR, "form[name='cadastro'] select.consulta, select.consulta")
    if not selects:
        selects = driver.find_elements(
            By.XPATH,
            "//label[contains(.,'Tipo de Consulta')]/following::select[1]",
        )
    if not selects:
        log.warning("BrFlow: select Tipo de Consulta não encontrado")
        return

    sel = selects[0]
    try:
        Select(sel).select_by_value("1")
    except Exception:
        try:
            Select(sel).select_by_visible_text("Nível Hierárquico")
        except Exception:
            driver.execute_script(
                "arguments[0].value='1';"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));"
                "if (window.jQuery) jQuery(arguments[0]).trigger('change');",
                sel,
            )
    time.sleep(0.8)
    # Select2: dispara change via jQuery se existir
    try:
        driver.execute_script(
            "if (window.jQuery) { jQuery(arguments[0]).val('1').trigger('change'); }",
            sel,
        )
    except Exception:
        pass
    time.sleep(0.5)
    log.info("BrFlow: Tipo de Consulta = Nível Hierárquico")


def open_prioridade_page(driver, *, is_cancelled: Callable[[], bool] | None = None) -> None:
    """
    Abre Prioridade NH x WF a partir do menu home (URL permanece /index/index).
    Ordem: clique no menu (XPath real) → Controles.Tela → XHR inject.
    """
    if is_cancelled and is_cancelled():
        raise RuntimeError("Cancelado")

    _ensure_brflow_index(driver)
    if is_cancelled and is_cancelled():
        raise RuntimeError("Cancelado")

    # NÃO retornar cedo só porque o link existe no menu home.
    if _page_has_prioridade_form(driver):
        log.info("BrFlow: formulário Pesquisa já aberto")
        select_tipo_consulta_nivel(driver)
        return

    # 1) Clique explícito no menu (caminho real do usuário)
    if _try_menu_click(driver):
        select_tipo_consulta_nivel(driver)
        return
    if is_cancelled and is_cancelled():
        raise RuntimeError("Cancelado")

    # 2) SPA router
    if _try_controles_tela(driver):
        select_tipo_consulta_nivel(driver)
        return
    if is_cancelled and is_cancelled():
        raise RuntimeError("Cancelado")

    # 3) Inject
    if _inject_page_xhr(driver):
        select_tipo_consulta_nivel(driver)
        return

    raise RuntimeError(
        "Não foi possível abrir Prioridade NH x WF pelo menu. "
        "Confirme o link em Configurações na página inicial do BrFlow."
    )


def load_nivel_combo(driver) -> dict[str, int]:
    """Mapa nome_normalizado → codNivelHierarquico a partir do select/combo da página."""
    script = """
    const done = arguments[arguments.length - 1];
    const out = {};
    try {
      const sel = document.querySelector('select[name=\"codNivelHierarquico\"], #codNivelHierarquico');
      if (sel) {
        Array.from(sel.options || []).forEach((opt) => {
          const val = (opt.value || '').trim();
          const desc = (opt.textContent || opt.label || '').trim();
          if (val && desc && val !== '0') out[desc] = val;
        });
      }
      if (typeof ListaNivelHierarquico !== 'undefined' && Array.isArray(ListaNivelHierarquico)) {
        ListaNivelHierarquico.forEach((item) => {
          const val = String(item.val || item.codNivelHierarquico || '').trim();
          const desc = String(item.desc || item.nomNivelHierarquico || '').trim();
          if (val && desc) out[desc] = val;
        });
      }
    } catch (e) {}
    done(out);
    """
    try:
        driver.set_script_timeout(30)
        raw = driver.execute_async_script(script)
        if not isinstance(raw, dict):
            return {}
        result: dict[str, int] = {}
        for desc, val in raw.items():
            try:
                result[_norm_name(str(desc))] = int(val)
            except (TypeError, ValueError):
                continue
        return result
    except Exception:
        log.debug("Não foi possível ler combo de NH no BrFlow", exc_info=True)
        return {}


def _norm_name(name: str) -> str:
    return " ".join((name or "").strip().casefold().split())


def resolve_cod_nivel(
    *,
    name: str,
    sharepoint_id: int | None,
    combo: dict[str, int],
) -> int | None:
    if sharepoint_id:
        return int(sharepoint_id)
    key = _norm_name(name)
    if key in combo:
        return combo[key]
    for desc, cod in combo.items():
        if key and (key in desc or desc in key):
            return cod
    return None


def pesquisar_prioridade(driver, cod_nivel_hierarquico: int) -> list[dict[str, Any]]:
    body = urlencode(
        {
            "codConsulta": "1",
            "codCliente": "",
            "codNivelHierarquico": str(cod_nivel_hierarquico),
            "codWorkflow": "",
            "codEtapaDetalhado": "",
            "formato": "1",
        }
    )
    script = """
    const done = arguments[arguments.length - 1];
    const url = arguments[0];
    const body = arguments[1];
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
      },
      body: body,
      credentials: 'include'
    })
      .then(async (r) => {
        const text = await r.text();
        done({ ok: r.ok, status: r.status, text: text.slice(0, 2000000) });
      })
      .catch((e) => done({ ok: false, status: 0, text: String(e) }));
    """
    driver.set_script_timeout(90)
    raw = driver.execute_async_script(script, BRFLOW_SEARCH_URL, body)
    if not isinstance(raw, dict) or not raw.get("ok"):
        status = (raw or {}).get("status") if isinstance(raw, dict) else None
        raise RuntimeError(f"Falha ao pesquisar prioridade NH={cod_nivel_hierarquico} (status={status})")
    text = raw.get("text") or ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Resposta inválida ao pesquisar NH={cod_nivel_hierarquico}: {text[:200]}"
        ) from exc
    if isinstance(payload, dict):
        typ = str(payload.get("type") or "").lower()
        redirect = str(payload.get("redirectTo") or "")
        if typ in {"error", "erro", "fail"} or redirect:
            desc = str(payload.get("description") or payload.get("message") or redirect)
            raise RuntimeError(f"BrFlow rejeitou pesquisa NH={cod_nivel_hierarquico}: {desc[:200]}")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]
