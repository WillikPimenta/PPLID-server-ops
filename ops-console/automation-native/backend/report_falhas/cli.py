# -*- coding: utf-8 -*-
"""CLI de entrada e orquestração do relatório."""

from datetime import date, datetime
import re

import report_falhas.legacy_main as legacy


def prompt_bool(pergunta: str, default: bool = False) -> bool:
    """Pergunta S/N no terminal (Enter = default)."""
    true_set = {"s", "sim", "y", "yes"}
    false_set = {"n", "nao", "não", "no"}
    while True:
        resp = input(pergunta).strip().lower()
        if resp == "":
            return bool(default)
        if resp in true_set:
            return True
        if resp in false_set:
            return False
        print("Resposta inválida. Digite S para Sim ou N para Não.")


def parse_date_br(s: str):
    """Converte 'DD/MM/AAAA' em datetime.date."""
    s = (s or "").strip()
    return datetime.strptime(s, "%d/%m/%Y").date()


def parse_mes_ano_input(pergunta: str = "Informe o mês/ano de referência (MM/AAAA) ou Enter para mês atual: "):
    """Escolhe mês/ano de referência do relatório."""
    while True:
        raw = input(pergunta).strip()
        if raw == "":
            return date.today()
        norm = raw.replace("-", "/").replace(" ", "")
        m = re.fullmatch(r"(\d{1,2})/(\d{4})", norm)
        if m:
            mes = int(m.group(1)); ano = int(m.group(2))
        else:
            m2 = re.fullmatch(r"(\d{2})(\d{4})", norm)
            if m2:
                mes = int(m2.group(1)); ano = int(m2.group(2))
            else:
                print("Formato inválido. Use MM/AAAA (ex.: 02/2026) ou pressione Enter.")
                continue
        if not (1 <= mes <= 12):
            print("Mês inválido. Informe um valor entre 01 e 12.")
            continue
        try:
            return date(ano, mes, 1)
        except Exception:
            print("Ano/mês inválidos. Tente novamente.")


def main() -> None:
    from report_falhas.config_report import require_excel_path

    require_excel_path()
    legacy._legacy_main_impl(
        prompt_bool_fn=prompt_bool,
        parse_date_br_fn=parse_date_br,
        parse_mes_ano_input_fn=parse_mes_ano_input,
    )


if __name__ == "__main__":
    main()
