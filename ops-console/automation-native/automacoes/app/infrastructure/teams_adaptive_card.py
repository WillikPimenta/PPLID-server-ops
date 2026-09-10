"""Builder compartilhado de Adaptive Cards para notificações Teams."""

from typing import Callable, Optional


def adaptive_table_cell(
    text: str,
    bold: bool = False,
    color: str = None,
    center: bool = False,
) -> dict:
    block = {"type": "TextBlock", "text": str(text), "wrap": True}
    if bold:
        block["weight"] = "Bolder"
    if color:
        block["color"] = color
    if center:
        block["horizontalAlignment"] = "Center"
    return {"type": "TableCell", "items": [block]}


def adaptive_status_cell(estado: str) -> dict:
    labels = {
        "ok": ("✅", "Good"),
        "erro": ("❌", "Attention"),
        "nao_executado": ("—", "Default"),
        "pendente": ("⏳", "Warning"),
    }
    text, color = labels.get(estado, ("—", "Default"))
    return adaptive_table_cell(text, color=color, center=True)


def humanizar_observacao(
    texto: str,
    obs_map: dict = None,
    extra_rules: Callable[[str], Optional[str]] = None,
) -> str:
    valor = str(texto or "").strip()
    for prefixo in ("❌ ", "⚠️ ", "• ", "⚠ "):
        if valor.startswith(prefixo):
            valor = valor[len(prefixo):].strip()
    if not valor:
        return ""
    chave = valor.lower()
    if obs_map and chave in obs_map:
        return obs_map[chave]
    if extra_rules:
        custom = extra_rules(valor)
        if custom:
            return custom
    return valor[:60]


def gerar_observacao_linha(
    task_ids: list,
    task_status: dict,
    obs_map: dict = None,
    extra_rules: Callable[[str], Optional[str]] = None,
) -> str:
    estados = [
        task_status.get(tid, {}).get("estado", "nao_executado")
        for tid in task_ids
    ]
    if all(e == "nao_executado" for e in estados):
        return "Não executada"
    for tid, estado in zip(task_ids, estados):
        if estado in ("erro", "pendente"):
            obs = task_status.get(tid, {}).get("obs", "")
            return (
                humanizar_observacao(obs, obs_map=obs_map, extra_rules=extra_rules)
                or "Falha na execução"
            )
    return "Concluída"


def estado_agregado_linha(task_ids: list, task_status: dict) -> str:
    """Resume o status de uma linha (uma ou mais tarefas)."""
    estados = [
        task_status.get(tid, {}).get("estado", "nao_executado")
        for tid in task_ids
    ]
    if all(e == "nao_executado" for e in estados):
        return "nao_executado"
    if any(e == "erro" for e in estados):
        return "erro"
    if any(e == "pendente" for e in estados):
        return "pendente"
    return "ok"


def calcular_resumo_demandas(demandas_tabela: list, task_status: dict) -> tuple:
    executadas = concluidas = com_falha = 0
    for _, _, task_ids in demandas_tabela:
        for tid in task_ids:
            estado = task_status.get(tid, {}).get("estado", "nao_executado")
            if estado == "nao_executado":
                continue
            executadas += 1
            if estado == "ok":
                concluidas += 1
            elif estado in ("erro", "pendente"):
                com_falha += 1
    return executadas, concluidas, com_falha


def build_status_card(
    *,
    titulo_card: str,
    subtitulo: str,
    demandas_tabela: list,
    task_status: dict,
    erros: list = None,
    obs_map: dict = None,
    extra_obs_rules: Callable[[str], Optional[str]] = None,
    layout: str = "dias",
) -> dict:
    """Monta Adaptive Card com tabela de status das demandas.

    layout:
        - "dias": colunas D-1, D-2, D-3 (rotina)
        - "simples": coluna Status única por sistema (produção)
    """
    if layout == "simples":
        header_cells = [
            adaptive_table_cell("Sistema", bold=True),
            adaptive_table_cell("Status", bold=True, center=True),
            adaptive_table_cell("Detalhes", bold=True),
        ]
        col_widths = [{"width": 2}, {"width": 1}, {"width": 3}]
    else:
        header_cells = [
            adaptive_table_cell("Demanda", bold=True),
            adaptive_table_cell("D-1", bold=True, center=True),
            adaptive_table_cell("D-2", bold=True, center=True),
            adaptive_table_cell("D-3", bold=True, center=True),
            adaptive_table_cell("Detalhes", bold=True),
        ]
        col_widths = [{"width": 2}, {"width": 1}, {"width": 1}, {"width": 1}, {"width": 2}]

    rows = [{
        "type": "TableRow",
        "cells": header_cells,
        "style": "accent",
    }]

    for _, label, task_ids in demandas_tabela:
        detalhes = gerar_observacao_linha(
            task_ids,
            task_status,
            obs_map=obs_map,
            extra_rules=extra_obs_rules,
        )
        if layout == "simples":
            estado = estado_agregado_linha(task_ids, task_status)
            data_cells = [
                adaptive_table_cell(label),
                adaptive_status_cell(estado),
                adaptive_table_cell(detalhes),
            ]
        else:
            estados = []
            for i in range(3):
                if i < len(task_ids):
                    tid = task_ids[i]
                    estados.append(task_status.get(tid, {}).get("estado", "nao_executado"))
                else:
                    estados.append("nao_executado")
            data_cells = [
                adaptive_table_cell(label),
                adaptive_status_cell(estados[0]),
                adaptive_status_cell(estados[1]),
                adaptive_status_cell(estados[2]),
                adaptive_table_cell(detalhes),
            ]
        rows.append({"type": "TableRow", "cells": data_cells})

    executadas, concluidas, com_falha = calcular_resumo_demandas(demandas_tabela, task_status)

    if com_falha == 0 and executadas > 0:
        resumo = f"Todas as {concluidas} etapa(s) executada(s) foram concluídas com sucesso."
        resumo_cor = "Good"
    elif com_falha > 0:
        em_andamento = executadas - concluidas - com_falha
        resumo = f"{concluidas} etapa(s) concluída(s) e {com_falha} com pendência(s)"
        if em_andamento > 0:
            resumo += f"; {em_andamento} em andamento"
        resumo += "."
        resumo_cor = "Attention"
    else:
        resumo = "Nenhuma demanda foi executada nesta rodada."
        resumo_cor = "Default"

    body = [
        {
            "type": "TextBlock",
            "text": titulo_card,
            "weight": "Bolder",
            "size": "Large",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": subtitulo,
            "isSubtle": True,
            "spacing": "None",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": resumo,
            "wrap": True,
            "spacing": "Small",
            "color": resumo_cor,
        },
        {
            "type": "Table",
            "firstRowAsHeaders": True,
            "showGridLines": True,
            "gridStyle": "accent",
            "columns": col_widths,
            "rows": rows,
        },
        {
            "type": "TextBlock",
            "text": "Legenda: ✅ concluído · ❌ pendente · — não executado",
            "isSubtle": True,
            "size": "Small",
            "spacing": "Medium",
            "wrap": True,
        },
    ]

    if erros:
        itens_erros = [{
            "type": "TextBlock",
            "text": "Pendências e alertas",
            "weight": "Bolder",
            "color": "Attention",
        }]
        for erro in erros[:10]:
            itens_erros.append({
                "type": "TextBlock",
                "text": f"• {humanizar_observacao(erro, obs_map=obs_map, extra_rules=extra_obs_rules)}",
                "wrap": True,
                "spacing": "Small",
            })
        body.append({
            "type": "Container",
            "style": "attention",
            "spacing": "Medium",
            "items": itens_erros,
        })

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": body,
    }
