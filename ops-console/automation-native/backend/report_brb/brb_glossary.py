# -*- coding: utf-8 -*-
"""Glossário e textos de storytelling por aba."""
from __future__ import annotations

from report_brb.config_brb import CLIENT

_NAME = CLIENT["nome_curto"]

GLOSSARY: dict[str, str] = {
    "Protocolos NA": (
        "Soma do volume agregado informado em cada solicitação Qualidade → CS. "
        "Não é o número de solicitações abertas."
    ),
    "Demandas abertas": (
        "Solicitações abertas pelo time de Qualidade ao CS responsável pelo cliente. "
        "Cada registro é uma solicitação/mês/QI, não um protocolo unitário."
    ),
    "Casos únicos FG": (
        "Contagem de falhas encontradas em auditorias pela chave Protocolo + Matrícula Agente. "
        "Um protocolo com 3 matrículas diferentes conta como 3 casos."
    ),
    "Falhas NA": (
        "Registros de falhas notificados pela Qualidade ao CS. "
        "Não confirmam, por si só, a comunicação ao cliente. "
        "Fluxo distinto das falhas encontradas em auditorias."
    ),
    "Tendência": (
        "Classificação das falhas encontradas em auditorias "
        "(ex.: Fraude não identificada, Selfie, Ilegível, Regra de CPF)."
    ),
    "CONFORME Sim": (
        "Na Contestação, SIM significa que a análise foi conforme — o caso NÃO é falha "
        "(improcedente / acerto operacional)."
    ),
    "CONFORME Não": (
        "Na Contestação, NÃO significa que o caso É falha (procedente)."
    ),
    "Possível ataque": (
        "Registro de falha notificada cujo motivo contém palavra-chave de indício "
        "(ex.: adulteração ou manipulação). Não confirma fraude — "
        "apresentar como registros com sinalização."
    ),
    "Razão descritiva entre bases": (
        "Relação entre o volume de falhas notificadas (Qualidade → CS) e o volume de casos "
        "auditados. Como as bases e os momentos podem diferir, não representa acurácia "
        "ou taxa formal de erro."
    ),
    "Mês parcial": (
        "Último mês do período quando a data final não corresponde ao encerramento do mês. "
        "Deve ser interpretado com cautela em comparações mensais."
    ),
    "Classificação inferida": (
        "Classificação atribuída a partir do motivo ou cenário quando o tipo "
        "não está preenchido diretamente na base."
    ),
    "Sequência esperada": (
        "Protocolo com falha de auditoria, notificação Qualidade → CS e contestação, "
        "com datas mínimas na ordem auditoria → notificação → contestação."
    ),
    "Ordem invertida": (
        "Protocolo em que as datas mínimas das bases não respeitam a sequência "
        "auditoria → notificação → contestação."
    ),
    "FN / FP": (
        "FN = cenário 'Não sinalizado' (falso negativo). FP = 'Sinalização incorreta' ou "
        "'Sinalização validada' (falso positivo)."
    ),
    "Demanda inferida": (
        "Quando a solicitação não vinha preenchida no registro de falha, associamos à "
        "solicitação cuja data de abertura é mais próxima da data de cadastro no mesmo mês."
    ),
    "FG sem contestação": (
        "Falhas encontradas em auditorias sem registro correspondente na Contestação "
        "(ainda não avaliados via CONFORME)."
    ),
    "FG sem match NA": (
        "Protocolos presentes nas falhas de auditoria, mas ausentes entre as falhas "
        "notificadas pela Qualidade ao CS."
    ),
    "Chave composta": (
        "Protocolo normalizado + Matrícula normalizada. Evita contar o mesmo protocolo "
        "várias vezes quando há matrículas diferentes."
    ),
    "Categoria macro": (
        "Classificação padronizada do motivo/cenário (ex.: Adulteração visual, Fraude não identificada). "
        "Agrupa descrições repetidas da operação."
    ),
    "Severidade": (
        "Nível de risco: Crítica, Alta, Média, Baixa ou Indefinida — com base no cenário e tipo de achado."
    ),
}

TENDENCIA_DESC: dict[str, str] = {
    "Fraude não identificada": "Achado de fraude sem confirmação plena na análise.",
    "Selfie": "Problema relacionado à captura ou validação de selfie.",
    "Ilegível": "Documento ou imagem ilegível na análise.",
    "Regra de CPF": "Divergência em regra de validação de CPF.",
    "Não Fraude": "Caso analisado e classificado como não fraude.",
    "Regra de incompleto": "Cadastro ou documentação incompleta.",
}

STORY: dict[str, str] = {
    "visao": (
        f"Panorama consolidado da operação {_NAME}. "
        "KPIs principais e gráficos abaixo; use as abas para detalhar cada área."
    ),
    "numeros": (
        "Por que 13, 133, 277 e 163 não batem? Cada número vem de uma aba e mede uma etapa diferente — "
        "demandas abertas ≠ volume de protocolos informado."
    ),
    "na": (
        f"Fluxo NA: Qualidade abre demandas ao CS do cliente; o CS comunica falhas ao {_NAME} "
        "via Notificação Ativa — demandas agregadas e falhas por protocolo."
    ),
    "fg": (
        "Casos auditados na aba Falhas_Gerais-BRB, classificados por <b>Tendência</b> "
        "(fraude, selfie, ilegível, etc.)."
    ),
    "procedencia": (
        "Resultado da contestação: <b>Sim</b> = conforme (não é falha) · <b>Não</b> = falha procedente."
    ),
    "capacitacao": (
        f"Ações de treinamento vinculadas ao {_NAME} no período."
    ),
    "timeline": (
        "Eventos ordenados do mais recente ao mais antigo — demandas, falhas, contestações e capacitação."
    ),
    "resumo": (
        "Leitura rápida do período — KPIs, alertas, recomendações e atalhos para o detalhe."
    ),
    "falhas": (
        "Principais falhas, categorias, severidade e motivos recorrentes na auditoria FG."
    ),
    "rastreabilidade": (
        "Lacunas entre FG, NA e Contestação — casos que exigem revisão operacional."
    ),
    "reincidencia": (
        "Quem e o que reincide — agentes, protocolos, demandas e categorias."
    ),
    "capacitacao": (
        "Ações corretivas de treinamento vinculadas ao BRB no período."
    ),
    "base": (
        "Metodologia, glossário, qualidade dos dados, linha do tempo e tabelas completas."
    ),
}


def glossary_html() -> str:
    rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in GLOSSARY.items()
    )
    tend_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in TENDENCIA_DESC.items()
    )
    return (
        '<div class="glossary-body">'
        '<h4 class="block-title" style="margin-top:0">Indicadores</h4>'
        f'<table class="glossary-table"><tbody>{rows}</tbody></table>'
        '<h4 class="block-title">Valores de Tendência</h4>'
        f'<table class="glossary-table"><tbody>{tend_rows}</tbody></table>'
        "</div>"
    )
