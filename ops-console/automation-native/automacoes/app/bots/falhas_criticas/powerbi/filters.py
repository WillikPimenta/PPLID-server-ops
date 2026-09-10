"""Aplicação de filtros no relatório Power BI."""
from __future__ import annotations

import re
import time
from datetime import datetime

from playwright.sync_api import Frame, Locator

import app.bots.falhas_criticas.powerbi.config as config


def _log(msg: str) -> None:
    print(f"[filtros] {msg}")


def _quick_pause(frame: Frame) -> None:
    frame.page.wait_for_timeout(config.FILTER_QUICK_MS)


def _wait_settle(frame: Frame) -> None:
    _wait_for_loading(frame, max_wait_ms=20_000)
    frame.page.wait_for_timeout(config.FILTER_SETTLE_MS)


def _wait_for_loading(frame: Frame, max_wait_ms: int = 15_000) -> None:
    """Aguarda spinners/overlays do Power BI desaparecerem."""
    spinner = frame.locator(
        ".spinner-background, .waitingSpinner, [class*='spinner-background']"
    )
    elapsed = 0
    while elapsed < max_wait_ms:
        try:
            if spinner.count() == 0 or not spinner.first.is_visible(timeout=200):
                return
        except Exception:
            return
        frame.page.wait_for_timeout(200)
        elapsed += 200


def _dismiss_overlays(frame: Frame, full: bool = False) -> None:
    """Fecha calendários/dropdowns abertos. Modo leve por padrão."""
    frame.page.keyboard.press("Escape")
    frame.page.wait_for_timeout(150)
    if full:
        backdrop = frame.page.locator(".cdk-overlay-backdrop")
        if backdrop.count() > 0:
            try:
                backdrop.first.click(force=True, timeout=500)
            except Exception:
                frame.page.keyboard.press("Escape")
        _wait_for_loading(frame, max_wait_ms=5_000)


def _find_slicer_container(frame: Frame, label: str) -> Locator:
    """Encontra o container visual completo do slicer pelo rótulo."""
    header = frame.locator(
        f".slicer-header-text[title='{label}'], [aria-label='{label}'].slicer-header-text"
    ).first
    if header.count() > 0:
        visual = header.locator("xpath=ancestor::div[contains(@class,'visual')][1]")
        if visual.count() > 0:
            return visual.first

    patterns = [
        frame.locator(f"[aria-label='{label}']"),
        frame.get_by_text(label, exact=True),
    ]
    for pattern in patterns:
        try:
            if pattern.count() > 0:
                first = pattern.first
                if first.is_visible(timeout=1000):
                    visual = first.locator("xpath=ancestor::div[contains(@class,'visual')][1]")
                    if visual.count() > 0:
                        return visual.first
                    parent = first.locator(
                        "xpath=ancestor::div[contains(@class,'slicer-container')][1]"
                    )
                    if parent.count() > 0:
                        return parent.first
                    return first
        except Exception:
            pass

    raise RuntimeError(f"Slicer não encontrado: {label}")


def _slicer_current_value(slicer: Locator, label: str) -> str | None:
    """Lê valor selecionado no slicer (não o rótulo)."""
    combo = slicer.locator("[role='combobox']").first
    try:
        if combo.count() > 0:
            aria = (combo.get_attribute("aria-label") or "").strip()
            if aria and aria.lower() != label.lower():
                if aria.lower().startswith(label.lower()):
                    rest = aria[len(label) :].strip(" ,:-")
                    if rest:
                        return rest
                return aria
    except Exception:
        pass

    for selector in (
        ".slicer-restatement",
        ".slicer-restatement .slicerText",
        "[class*='slicer-restatement']",
        ".slicer-dropdown-menu .slicerText",
        ".slicerText:not(.slicer-header-text)",
    ):
        el = slicer.locator(selector).first
        try:
            if el.count() > 0 and el.is_visible(timeout=500):
                for candidate in (
                    (el.inner_text(timeout=500) or "").strip(),
                    (el.get_attribute("title") or "").strip(),
                ):
                    if (
                        candidate
                        and candidate.lower() != label.lower()
                        and not candidate.lower().startswith(label.lower())
                    ):
                        return candidate
        except Exception:
            pass
    return None


def _values_match(current: str | None, expected: str) -> bool:
    if not current:
        return False
    c = current.strip().lower()
    e = expected.strip().lower()
    if c == e:
        return True
    if e == "todos" and c == "todos":
        return True
    return e in c and len(c) < 40


def _dropdown_is_open(frame: Frame, slicer: Locator) -> bool:
    if frame.locator(
        ".cdk-overlay-container .slicer-dropdown-popup:visible, "
        ".cdk-overlay-container [role='listbox']:visible"
    ).count() > 0:
        return True
    if slicer.locator(
        ".slicer-dropdown-popup:visible, .slicer-list:visible, .slicerItemContainer:visible"
    ).count() > 0:
        return True
    return frame.locator(".slicerItemContainer:visible").count() > 0


def _open_slicer_dropdown(frame: Frame, slicer: Locator, label: str) -> None:
    """Abre o painel do dropdown do slicer."""
    visual = _find_slicer_visual(frame, label)
    visual.hover()
    frame.page.wait_for_timeout(200)

    targets = [
        slicer.locator(".slicer-dropdown-menu").first,
        slicer.locator(".slicer-restatement").first,
        slicer.locator(".dropdown-chevron").first,
        slicer.locator("[role='combobox']").first,
        slicer.locator(".slicer-body, .slicerBody").first,
    ]
    for target in targets:
        try:
            if target.count() > 0 and target.is_visible(timeout=500):
                target.click(force=True, timeout=5_000)
                frame.page.wait_for_timeout(400)
                if _dropdown_is_open(frame, slicer):
                    return
        except Exception:
            continue
    slicer.click(force=True, timeout=5_000)
    frame.page.wait_for_timeout(400)


def _get_open_dropdown(frame: Frame, slicer: Locator | None = None):
    for selector in (
        ".cdk-overlay-container .slicer-dropdown-popup:visible",
        ".cdk-overlay-container [role='listbox']:visible",
        ".slicer-dropdown-popup:visible",
        "[role='listbox']:visible",
    ):
        popup = frame.page.locator(selector).last
        if popup.count() > 0:
            return popup

    if slicer is not None:
        inline = slicer.locator(".slicer-dropdown-popup:visible, .slicer-list:visible")
        if inline.count() > 0:
            return inline.last

    return frame.page.locator(".slicer-dropdown-popup:visible, [role='listbox']:visible").last


def _click_option_in_container(container, value: str) -> bool:
    patterns = [
        container.locator(f".slicerItemContainer[title='{value}']"),
        container.locator(".slicerItemContainer").filter(
            has_text=re.compile(rf"^{re.escape(value)}$", re.I)
        ),
        container.locator(".slicerText").filter(
            has_text=re.compile(rf"^{re.escape(value)}$", re.I)
        ),
        container.get_by_role("option", name=value, exact=True),
        container.get_by_role("option", name=re.compile(rf"^{re.escape(value)}$", re.I)),
        container.get_by_text(value, exact=True),
        container.get_by_text(re.compile(rf"^{re.escape(value)}$", re.I)),
    ]
    for option in patterns:
        try:
            if option.count() > 0:
                option.first.click(force=True, timeout=8_000)
                return True
        except Exception:
            continue
    return False


def _select_dropdown_option(frame: Frame, slicer: Locator, label: str, value: str) -> None:
    """Seleciona opção no dropdown aberto do Power BI."""
    popup = _get_open_dropdown(frame, slicer)

    if popup.count() > 0:
        search = popup.locator(
            "input[type='text'], input[placeholder*='Pesquisar'], input[placeholder*='Search']"
        ).first
        if search.count() > 0:
            try:
                if search.is_visible(timeout=500):
                    search.click(force=True)
                    search.fill(value)
                    frame.page.wait_for_timeout(400)
            except Exception:
                pass

    containers = []
    if popup.count() > 0:
        containers.append(popup)
    containers.extend([
        frame.page.locator(".cdk-overlay-container"),
        slicer,
        _find_slicer_visual(frame, label),
    ])

    for container in containers:
        if _click_option_in_container(container, value):
            return

    raise RuntimeError(f"Opção '{value}' não encontrada no dropdown aberto")


def _is_todos_state(current: str | None) -> bool:
    if not current:
        return False
    return current.strip().lower() in ("todos", "all")


def _find_slicer_visual(frame: Frame, label: str) -> Locator:
    """Encontra o container visual completo do slicer."""
    return _find_slicer_container(frame, label)


def _clear_via_dropdown(frame: Frame, slicer: Locator, label: str) -> None:
    """Fallback: limpa seleção abrindo o dropdown e desmarcando itens."""
    _open_slicer_dropdown(frame, slicer, label)
    frame.page.wait_for_timeout(400)
    popup = _get_open_dropdown(frame, slicer)
    if popup.count() == 0:
        raise RuntimeError(f"Dropdown não abriu para limpar '{label}'")

    for text in ("Limpar seleções", "Limpar", "Selecionar tudo"):
        item = popup.get_by_text(text, exact=False)
        if item.count() > 0:
            item.first.click(force=True, timeout=5_000)
            frame.page.keyboard.press("Escape")
            return

    checked = popup.locator(
        ".slicerItemContainer[aria-selected='true'], "
        ".slicerItemContainer.selected, "
        "[aria-checked='true']"
    )
    if checked.count() > 0:
        for i in range(checked.count()):
            try:
                checked.nth(i).click(force=True, timeout=3_000)
            except Exception:
                pass
        frame.page.keyboard.press("Escape")
        return

    before = _slicer_current_value(slicer, label)
    if before:
        item = popup.locator(f".slicerItemContainer[title*='{before[:20]}']")
        if item.count() == 0:
            item = popup.get_by_text(re.compile(re.escape(before[:12]), re.I))
        if item.count() > 0:
            item.first.click(force=True, timeout=5_000)
            frame.page.keyboard.press("Escape")
            return

    frame.page.keyboard.press("Escape")
    raise RuntimeError(f"Não foi possível limpar '{label}' via dropdown")


def _clear_slicer_selections(frame: Frame, slicer: Locator, label: str) -> None:
    """Clica no botão 'Limpar seleções' (ícone borracha) do slicer."""
    visual = _find_slicer_visual(frame, label)
    visual.scroll_into_view_if_needed()
    visual.hover()
    frame.page.wait_for_timeout(500)

    header = visual.locator(".slicer-header, .slicer-head, .visual-header").first
    if header.count() > 0:
        header.hover()
        frame.page.wait_for_timeout(300)

    clear_selectors = [
        visual.get_by_role("button", name=re.compile(r"Limpar sele", re.I)),
        visual.locator("[aria-label*='Limpar seleções']"),
        visual.locator("[aria-label*='Limpar sele']"),
        visual.locator("[title*='Limpar seleções']"),
        visual.locator("[title*='Clear selection']"),
        visual.locator("button.clear-slicer-selections"),
        visual.locator("[class*='clear']"),
        slicer.get_by_role("button", name=re.compile(r"Limpar sele", re.I)),
        slicer.locator("[aria-label*='Limpar seleções']"),
        slicer.locator("[title*='Limpar seleções']"),
    ]
    for clear_btn in clear_selectors:
        try:
            if clear_btn.count() > 0:
                clear_btn.first.click(force=True, timeout=5_000)
                return
        except Exception:
            continue

    # Último botão do header costuma ser a borracha
    if header.count() > 0:
        buttons = header.locator("button")
        if buttons.count() > 0:
            try:
                buttons.last.click(force=True, timeout=5_000)
                return
            except Exception:
                pass

    _log(f"  borracha não encontrada em '{label}', tentando via dropdown...")
    _clear_via_dropdown(frame, slicer, label)


def set_slicer(frame: Frame, label: str, value: str, *, force: bool = True) -> None:
    """Define valor do slicer. Para 'Todos', usa 'Limpar seleções'."""
    slicer = _find_slicer_container(frame, label)
    slicer.scroll_into_view_if_needed()

    before = _slicer_current_value(slicer, label)

    if value == "Todos":
        if _is_todos_state(before):
            return

        _log(f"{label} -> limpar seleções" + (f" (era: {before})" if before else ""))
        for attempt in range(2):
            _dismiss_overlays(frame)
            _clear_slicer_selections(frame, slicer, label)
            _quick_pause(frame)

            after = _slicer_current_value(slicer, label)
            if _is_todos_state(after) or _values_match(after, "Todos"):
                return

            if attempt == 0:
                _log(f"  retry limpar '{label}' (antes: {before!r}, depois: {after!r})")

        after = _slicer_current_value(slicer, label)
        if _is_todos_state(after) or _values_match(after, "Todos"):
            return
        raise RuntimeError(
            f"Falha ao limpar seleções de '{label}' (valor atual: {after!r})"
        )

    if _values_match(before, value):
        _log(f"  '{label}' já está em '{before}'")
        return

    _log(f"{label} -> {value}" + (f" (era: {before})" if before else ""))

    for attempt in range(2):
        _dismiss_overlays(frame, full=True)
        _open_slicer_dropdown(frame, slicer, label)
        frame.page.wait_for_timeout(400)
        _select_dropdown_option(frame, slicer, label, value)
        frame.page.keyboard.press("Escape")
        frame.page.wait_for_timeout(400)

        after = _slicer_current_value(slicer, label)
        if _values_match(after, value):
            return

        if attempt == 0:
            _log(f"  retry '{label}' (antes: {before!r}, depois: {after!r})")

    after = _slicer_current_value(slicer, label)
    if not _values_match(after, value):
        raise RuntimeError(
            f"Falha ao definir '{label}' = '{value}' (valor atual: {after!r})"
        )


def ensure_checkbox(frame: Frame, label: str, checked: bool) -> None:
    """Garante estado de checkbox (marcado/desmarcado)."""
    checkbox = frame.get_by_role("checkbox", name=re.compile(re.escape(label), re.I))
    if checkbox.count() == 0:
        label_el = frame.get_by_text(label, exact=True)
        if label_el.count() == 0:
            label_el = frame.get_by_text(re.compile(re.escape(label), re.I))
        container = label_el.first.locator("xpath=ancestor::*[contains(@class,'slicer') or @role='checkbox'][1]")
        if container.count() == 0:
            container = label_el.first.locator("xpath=..")
        checkbox = container.locator("[role='checkbox'], input[type='checkbox']").first
        if checkbox.count() == 0:
            current = label_el.first
            try:
                aria_checked = current.get_attribute("aria-checked")
            except Exception:
                aria_checked = None
            is_checked = aria_checked == "true"
            if is_checked == checked:
                return
            _log(f"checkbox '{label}' -> {'marcado' if checked else 'desmarcado'}")
            current.click()
            _quick_pause(frame)
            return

    cb = checkbox.first
    try:
        is_checked = cb.is_checked()
    except Exception:
        aria = cb.get_attribute("aria-checked")
        is_checked = aria == "true"

    if is_checked == checked:
        return

    _log(f"checkbox '{label}' -> {'marcado' if checked else 'desmarcado'}")
    cb.click()
    _quick_pause(frame)


def _resolve_clickable_button(element: Locator) -> Locator:
    parent = element.locator(
        "xpath=ancestor::*[self::button or @role='button'][1]"
    )
    if parent.count() > 0:
        return parent.first
    return element


def _find_data_section_visual(frame: Frame) -> Locator | None:
    header = frame.locator(
        ".slicer-header-text[title='Data'], [aria-label='Data'].slicer-header-text"
    ).first
    if header.count() > 0:
        visual = header.locator("xpath=ancestor::div[contains(@class,'visual')][1]")
        if visual.count() > 0:
            return visual.first

    date_input = frame.locator("input.date-slicer-datepicker, .date-slicer-datepicker input").first
    if date_input.count() > 0:
        visual = date_input.locator("xpath=ancestor::div[contains(@class,'visual')][1]")
        if visual.count() > 0:
            return visual.first
    return None


def _find_period_button(frame: Frame, name: str) -> Locator:
    """Localiza botões Q1–Q4/FY27 no slicer de Data (evita outros Q1 no relatório)."""
    visual = _find_data_section_visual(frame)
    if visual is not None:
        btn = visual.get_by_role("button", name=name, exact=True)
        if btn.count() > 0:
            return btn.first

        text = visual.get_by_text(name, exact=True)
        if text.count() > 0:
            return _resolve_clickable_button(text.first)

    btn = frame.get_by_role("button", name=name, exact=True)
    if btn.count() > 0:
        return btn.first

    text = frame.get_by_text(name, exact=True)
    if text.count() > 0:
        return _resolve_clickable_button(text.first)

    raise RuntimeError(f"Botão '{name}' não encontrado no relatório")


def _button_is_selected(element: Locator) -> bool:
    try:
        return bool(
            element.evaluate(
                """el => {
                    const node = el.closest('[role="button"]') || el;
                    const pressed = node.getAttribute('aria-pressed');
                    const selected = node.getAttribute('aria-selected');
                    if (pressed === 'true' || selected === 'true') return true;
                    const cls = (node.className || '').toLowerCase();
                    if (cls.includes('selected') || cls.includes('isselected')) return true;
                    const item = node.closest('.slicerItemContainer, .slicerItem');
                    if (item && /selected/i.test(item.className || '')) return true;
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def click_button_if_not_selected(frame: Frame, name: str) -> None:
    """Clica em botão de slicer (Q1–Q4, FY27) se não estiver selecionado."""
    _dismiss_overlays(frame)
    element = _find_period_button(frame, name)
    element.scroll_into_view_if_needed()

    try:
        element.wait_for(state="visible", timeout=10_000)
    except Exception as exc:
        raise RuntimeError(f"Botão '{name}' não ficou visível no relatório") from exc

    if _button_is_selected(element):
        _log(f"botão '{name}' já selecionado")
        return

    _log(f"botão '{name}' -> selecionado")
    clicked = False
    for attempt in range(2):
        try:
            element.click(force=True, timeout=10_000)
            clicked = True
            break
        except Exception:
            try:
                frame.page.keyboard.press("Escape")
                frame.page.wait_for_timeout(200)
                element.click(force=True, timeout=10_000)
                clicked = True
                break
            except Exception:
                if attempt == 0:
                    element = _resolve_clickable_button(element)
                    continue
    if not clicked:
        raise RuntimeError(f"Não foi possível clicar no botão '{name}'")
    _quick_pause(frame)


def select_fiscal_quarter(frame: Frame, start_date: str, end_date: str) -> None:
    """Seleciona Q1–Q4 conforme o período de datas informado."""
    quarter = config.get_fiscal_quarter_for_range(start_date, end_date)
    _log(f"Trimestre fiscal: {quarter} (período {start_date} - {end_date})")
    _dismiss_overlays(frame, full=True)
    _wait_for_loading(frame)
    frame.page.wait_for_timeout(500)
    click_button_if_not_selected(frame, quarter)


def _find_date_inputs(frame: Frame) -> tuple[Locator, Locator]:
    """Localiza campos de data início/fim pelo aria-label ou classe do slicer."""
    start = frame.locator(
        "[aria-label*='Data de início'], [aria-label*='data de início'], "
        "[aria-label*='início'][class*='date-slicer']"
    ).first
    end = frame.locator(
        "[aria-label*='Data de fim'], [aria-label*='data de fim'], "
        "[aria-label*='fim'][class*='date-slicer']"
    ).first

    if start.count() > 0 and end.count() > 0:
        return start, end

    datepicker = frame.locator("input.date-slicer-datepicker, .date-slicer-datepicker input")
    if datepicker.count() >= 2:
        return datepicker.nth(0), datepicker.nth(1)

    raise RuntimeError("Não foi possível localizar os campos de data (início/fim)")


def _read_date_input_value(inp: Locator) -> str | None:
    """Lê o valor atual de um campo de data do slicer."""
    for getter in (
        lambda: inp.input_value(timeout=2_000),
        lambda: (inp.get_attribute("value") or "").strip(),
        lambda: (inp.get_attribute("aria-label") or "").strip(),
    ):
        try:
            raw = getter()
            if raw:
                m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", raw)
                if m:
                    return m.group(1)
                if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", raw.strip()):
                    return raw.strip()
        except Exception:
            continue
    return None


def _dates_match(current: str | None, expected: str) -> bool:
    if not current:
        return False
    try:
        cur = datetime.strptime(current.strip(), "%d/%m/%Y").date()
        exp = datetime.strptime(expected.strip(), "%d/%m/%Y").date()
        return cur == exp
    except ValueError:
        return current.strip() == expected.strip()


def _clear_date_slicer(frame: Frame) -> None:
    """Tenta limpar seleções do slicer de Data antes de redefinir o intervalo."""
    visual = _find_data_section_visual(frame)
    if visual is None:
        return
    try:
        _clear_slicer_selections(frame, visual, "Data")
    except Exception as exc:
        _log(f"  limpar slicer Data ignorado: {exc}")


def _fill_date_input(frame: Frame, inp: Locator, value: str) -> None:
    """Preenche um campo de data e confirma com Enter."""
    inp.scroll_into_view_if_needed()
    inp.click(force=True, timeout=10_000)
    frame.page.wait_for_timeout(150)
    inp.press("Control+A")
    inp.press("Delete")
    frame.page.wait_for_timeout(100)
    inp.type(value, delay=40)
    inp.press("Enter")
    _quick_pause(frame)


def set_date_range(frame: Frame, start_date: str, end_date: str) -> None:
    """Preenche intervalo de datas no slicer 'Data' com validação e retry na virada de mês."""
    is_month_turn = False
    try:
        start_dt = datetime.strptime(start_date, "%d/%m/%Y").date()
        is_month_turn = start_dt.day == 1
    except ValueError:
        pass

    if is_month_turn:
        _log(f"Virada de mês detectada — reforçando ajuste de datas ({start_date} - {end_date})")
        _wait_for_loading(frame, max_wait_ms=25_000)
    else:
        _log(f"Data: {start_date} até {end_date}")

    _wait_for_loading(frame)

    for attempt in range(3):
        start_input, end_input = _find_date_inputs(frame)
        before_start = _read_date_input_value(start_input)
        before_end = _read_date_input_value(end_input)
        if attempt == 0 and before_start:
            _log(f"  data início atual: {before_start}")
        if attempt > 0:
            _dismiss_overlays(frame, full=True)
            _clear_date_slicer(frame)
            _wait_for_loading(frame, max_wait_ms=15_000)
            start_input, end_input = _find_date_inputs(frame)

        _fill_date_input(frame, start_input, start_date)
        frame.page.wait_for_timeout(300)
        after_start = _read_date_input_value(start_input)

        _fill_date_input(frame, end_input, end_date)
        frame.page.wait_for_timeout(300)
        after_end = _read_date_input_value(end_input)

        start_ok = _dates_match(after_start, start_date)
        end_ok = _dates_match(after_end, end_date)

        if start_ok and end_ok:
            _log(f"  datas confirmadas: {after_start} — {after_end}")
            _dismiss_overlays(frame, full=True)
            return

        _log(
            f"  retry datas ({attempt + 1}/3): "
            f"início esperado={start_date} atual={after_start!r} | "
            f"fim esperado={end_date} atual={after_end!r}"
        )

    raise RuntimeError(
        f"Falha ao definir intervalo de datas ({start_date} - {end_date}). "
        f"Últimos valores lidos: início={after_start!r}, fim={after_end!r}"
    )


def apply_all_filters(frame: Frame) -> None:
    """Aplica todos os filtros conforme config.py."""
    start, end = config.get_date_range()
    _log(f"Período MTD: {start} - {end}")

    # Trimestre fiscal antes das datas — evita início travado na virada de mês.
    select_fiscal_quarter(frame, start, end)
    _wait_settle(frame)
    set_date_range(frame, start, end)

    oper = config.FILTERS["operacional"]
    for label in oper["checkboxes_unchecked"]:
        ensure_checkbox(frame, label, checked=False)
    for label in oper["dropdowns_todos"]:
        set_slicer(frame, label, "Todos")

    principal = config.FILTERS["principal"]
    for label in principal["dropdowns_todos"]:
        set_slicer(frame, label, "Todos")
    for label in principal["checkboxes_unchecked"]:
        ensure_checkbox(frame, label, checked=False)

    # Valores específicos por último (não limpar depois)
    for label, value in principal["dropdowns_value"].items():
        set_slicer(frame, label, value)
    for btn in principal.get("buttons_selected", []):
        click_button_if_not_selected(frame, btn)

    periodo = config.FILTERS["periodo"]
    for btn in periodo["buttons_selected"]:
        click_button_if_not_selected(frame, btn)
    for label in periodo["dropdowns_todos"]:
        set_slicer(frame, label, "Todos")

    _log("Todos os filtros aplicados.")
    _wait_settle(frame)
