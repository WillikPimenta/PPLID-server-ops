# -*- coding: utf-8 -*-
"""Projeção operacional D-1 baseada em escala, capacidade e volumetria."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from math import floor

from django.db.models import Count, Sum
from django.utils import timezone

from apps.replicacao_d1.models import (
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.normalization import normalize_key
from apps.replicacao_d1.services.config_dto import CALCULADORA_DEFAULTS
from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord


def _parse_competencia(competencia: str) -> tuple[int, int]:
    try:
        year, month = (int(part) for part in str(competencia).split("-", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("Competência inválida. Use YYYY-MM.") from exc
    if year < 1 or month < 1 or month > 12:
        raise ValueError("Competência inválida. Use YYYY-MM.")
    return year, month


def _dias_mes(year: int, month: int) -> list[date]:
    return [
        date(year, month, day)
        for day in range(1, monthrange(year, month)[1] + 1)
    ]


def _eh_fila_case(fila: str) -> bool:
    texto = str(fila or "").strip().casefold().replace(",", ".")
    return texto == "3.1" or ("documentoscopia" in texto and "3.1" in texto)


def _calcular_amostra(volumetria: int, categoria: str, params: dict, pct_especial: int | None) -> int:
    if volumetria <= 0:
        return 0
    if pct_especial is not None:
        pct = min(100, max(0, int(pct_especial)))
        if pct <= 0:
            return 0
        return min(volumetria, max(1, round(volumetria * pct / 100)))

    confianca = float(params.get("confianca", CALCULADORA_DEFAULTS["confianca"]))
    z = confianca**2 * 0.5 * (1 - 0.5)
    categoria_norm = str(categoria or "").strip().casefold()
    margem = float(params.get("margin_high", CALCULADORA_DEFAULTS["margin_high"]))
    if categoria_norm and "high" not in categoria_norm and categoria_norm not in ("alto", "alta"):
        margem = float(params.get("margin_low", CALCULADORA_DEFAULTS["margin_low"]))
    denominador = margem**2 + (z / volumetria)
    if denominador <= 0:
        return 0
    return min(volumetria, max(0, round(z / denominador)))


def _ultima_volumetria(data_limite: date) -> tuple[date | None, dict[str, int]]:
    report_date = (
        RotinaDetalhadoBrutoRecord.objects.filter(report_date__lte=data_limite)
        .order_by("-report_date")
        .values_list("report_date", flat=True)
        .first()
    )
    if report_date is None:
        return None, {}

    rows = (
        RotinaDetalhadoBrutoRecord.objects.filter(report_date=report_date)
        .exclude(workflow="")
        .values("workflow")
        .annotate(total=Count("id"))
    )
    volumes = {
        normalize_key(row["workflow"]): int(row["total"] or 0)
        for row in rows
        if normalize_key(row["workflow"])
    }
    return report_date, volumes


def _alocar_capacidade(
    capacidade: int,
    pesos: dict[str, int],
    limites: dict[str, int],
) -> dict[str, int]:
    """Distribuição proporcional com teto por volumetria e redistribuição da sobra."""
    alocado: dict[str, int] = {chave: 0 for chave in pesos}
    ativos = {
        chave
        for chave, peso in pesos.items()
        if int(peso or 0) > 0 and int(limites.get(chave, 0) or 0) > 0
    }
    restante = min(max(0, int(capacidade)), sum(int(limites[chave]) for chave in ativos))

    while restante > 0 and ativos:
        peso_total = sum(int(pesos[chave]) for chave in ativos)
        if peso_total <= 0:
            break

        saturados: list[str] = []
        for chave in ativos:
            espaco = int(limites[chave]) - alocado[chave]
            quota = restante * int(pesos[chave]) / peso_total
            if quota >= espaco:
                saturados.append(chave)
        if saturados:
            for chave in saturados:
                incremento = int(limites[chave]) - alocado[chave]
                alocado[chave] += incremento
                restante -= incremento
                ativos.remove(chave)
            continue

        quotas = {
            chave: restante * int(pesos[chave]) / peso_total
            for chave in ativos
        }
        incrementos = {
            chave: min(int(limites[chave]) - alocado[chave], floor(quota))
            for chave, quota in quotas.items()
        }
        usados = sum(incrementos.values())
        for chave, incremento in incrementos.items():
            alocado[chave] += incremento
        restante -= usados

        if restante <= 0:
            break
        ordem = sorted(
            ativos,
            key=lambda chave: (-(quotas[chave] - floor(quotas[chave])), chave),
        )
        houve_incremento = False
        for chave in ordem:
            if restante <= 0:
                break
            if alocado[chave] >= int(limites[chave]):
                continue
            alocado[chave] += 1
            restante -= 1
            houve_incremento = True
        if not houve_incremento:
            break
        break

    return alocado


def gerar_projecao_mensal(
    competencia: str,
    *,
    hoje: date | None = None,
) -> dict:
    """Projeta consumo pela capacidade futura, distribuída conforme volumetria dos workflows."""
    competencia = str(competencia or "").strip()
    year, month = _parse_competencia(competencia)
    hoje = hoje or timezone.localdate()

    primeiro_dia = date(year, month, 1)
    ultimo_dia = date(year, month, monthrange(year, month)[1])
    if hoje < primeiro_dia:
        referencia = primeiro_dia - timedelta(days=1)
        limite_volumetria = hoje
    elif hoje > ultimo_dia:
        referencia = ultimo_dia
        limite_volumetria = ultimo_dia
    else:
        referencia = hoje
        limite_volumetria = hoje

    dias_mes = _dias_mes(year, month)
    dias_decorridos = sum(1 for dia in dias_mes if dia <= referencia)
    dias_restantes = [dia for dia in dias_mes if dia > referencia]

    ledger_qs = ReplicacaoD1LedgerConsumo.objects.filter(competencia=competencia)
    consumo_rows = ledger_qs.values("workflow_chave").annotate(total=Sum("protocolos"))
    consumo_por_workflow = {
        normalize_key(row["workflow_chave"]): int(row["total"] or 0)
        for row in consumo_rows
        if normalize_key(row["workflow_chave"])
    }
    dias_com_execucao = len(
        {
            timezone.localtime(data_execucao).date()
            for data_execucao in ledger_qs.filter(protocolos__gt=0).values_list("data_execucao", flat=True)
        }
    )

    geral = ReplicacaoD1ConfigGeral.get_solo()
    meta_produ_diaria = max(0.0, float(geral.meta_produ_diaria or 0))
    meta_produ_diaria_case = max(0.0, float(geral.meta_produ_diaria_case or 0))
    if meta_produ_diaria_case <= 0:
        meta_produ_diaria_case = meta_produ_diaria
    params = dict(CALCULADORA_DEFAULTS)
    params.update(geral.calculadora_params or {})
    data_volumetria, volumes = _ultima_volumetria(limite_volumetria)

    workflows = list(
        ReplicacaoD1Workflow.objects.filter(ativo=True)
        .select_related("cliente", "cliente__categoria")
        .order_by("nome_canonico")
    )
    detalhes: list[dict] = []
    estados: dict[str, dict] = {}
    chaves_mapeadas: set[str] = set()
    for workflow in workflows:
        chave = normalize_key(workflow.chave_normalizada)
        if not chave:
            continue
        chaves_mapeadas.add(chave)
        cliente = workflow.cliente
        categoria = ""
        if cliente is not None:
            categoria = cliente.categoria.nome if cliente.categoria_id else cliente.categoria_nome
        consumo = int(consumo_por_workflow.get(chave, 0))
        chave_d1 = normalize_key(workflow.nome_d1) or chave
        volumetria = int(volumes.get(chave_d1, 0))
        pct_especial = 100 if workflow.amostra_100 else workflow.amostra_pct_especial
        amostra_base = _calcular_amostra(volumetria, categoria, params, pct_especial)
        fila = "3.1" if _eh_fila_case(workflow.fila) else "G auditoria"
        estado = {
            "workflow": workflow.nome_canonico,
            "workflow_chave": chave,
            "cliente": cliente.nome if cliente else "",
            "fila": fila,
            "consumo_acumulado": consumo,
            "volumetria_d1": volumetria,
            "amostra_base_diaria": amostra_base,
            "capacidade_alocada_mes": 0,
        }
        estados[chave] = estado

    escala = {
        row.data: row
        for row in ReplicacaoD1EscalaDia.objects.filter(data__in=dias_restantes)
    }
    dias_sem_escala: list[str] = []
    capacidade_total_restante = 0
    capacidade_alocada_restante = 0

    for dia in dias_restantes:
        escala_dia = escala.get(dia)
        if geral.usar_escala_auditores:
            if escala_dia is None:
                dias_sem_escala.append(dia.isoformat())
                continue
            capacidades = {
                "G auditoria": round(int(escala_dia.auditores_brflow) * meta_produ_diaria),
                "3.1": round(int(escala_dia.auditores_case) * meta_produ_diaria_case),
            }
        else:
            capacidades = {
                fila: sum(
                    int(estado["amostra_base_diaria"])
                    for estado in estados.values()
                    if estado["fila"] == fila
                )
                for fila in ("G auditoria", "3.1")
            }

        capacidade_total_restante += sum(max(0, int(valor)) for valor in capacidades.values())
        for fila, capacidade in capacidades.items():
            grupo = {
                chave: estado
                for chave, estado in estados.items()
                if estado["fila"] == fila and int(estado["amostra_base_diaria"]) > 0
            }
            pesos = {chave: int(estado["amostra_base_diaria"]) for chave, estado in grupo.items()}
            limites = {
                chave: int(estado["volumetria_d1"])
                for chave, estado in grupo.items()
            }
            alocacao = _alocar_capacidade(int(capacidade), pesos, limites)
            for chave, quantidade in alocacao.items():
                estados[chave]["capacidade_alocada_mes"] += int(quantidade)
                capacidade_alocada_restante += int(quantidade)

    for estado in estados.values():
        consumo = int(estado["consumo_acumulado"])
        projetado = consumo + int(estado["capacidade_alocada_mes"])
        estado["projecao_fim_mes"] = projetado
        estado["ritmo_diario"] = round(
            int(estado["capacidade_alocada_mes"]) / len(dias_restantes), 1
        ) if dias_restantes else 0.0
        detalhes.append(estado)

    # Consumos históricos de workflows fora do cadastro ativo permanecem nos totais, sem projeção futura.
    for chave, consumo in consumo_por_workflow.items():
        if chave in chaves_mapeadas:
            continue
        detalhes.append(
            {
                "workflow": chave,
                "workflow_chave": chave,
                "cliente": "",
                "fila": "",
                "consumo_acumulado": consumo,
                "volumetria_d1": 0,
                "amostra_base_diaria": 0,
                "capacidade_alocada_mes": 0,
                "ritmo_diario": 0.0,
                "projecao_fim_mes": consumo,
            }
        )

    detalhes.sort(key=lambda row: (str(row["fila"]), str(row["cliente"]), str(row["workflow"])))
    consumo_total = sum(int(row["consumo_acumulado"]) for row in detalhes)
    projecao_final = sum(int(row["projecao_fim_mes"]) for row in detalhes)
    volumetria_total = sum(int(row["volumetria_d1"]) for row in detalhes)
    demanda_diaria_total = sum(int(row["amostra_base_diaria"]) for row in detalhes)

    avisos: list[str] = []
    if data_volumetria is None:
        avisos.append("Nenhuma volumetria sincronizada foi encontrada; a projeção futura ficou zerada.")
    if geral.usar_escala_auditores and dias_sem_escala:
        avisos.append(
            f"Escala ausente em {len(dias_sem_escala)} dia(s) corrido(s); esses dias foram considerados com capacidade zero."
        )
    if not geral.usar_escala_auditores:
        avisos.append("Uso da escala está desativado; a projeção considera somente a demanda calculada pela volumetria.")

    return {
        "competencia": competencia,
        "gerada_em": timezone.now().isoformat(),
        "consumo_total": consumo_total,
        "dias_mes": len(dias_mes),
        "dias_decorridos": dias_decorridos,
        "dias_restantes": len(dias_restantes),
        "dias_com_execucao": dias_com_execucao,
        "dias_com_escala": len(dias_restantes) - len(dias_sem_escala),
        "dias_sem_escala": dias_sem_escala,
        "meta_produ_diaria": meta_produ_diaria,
        "meta_produ_diaria_case": meta_produ_diaria_case,
        "usar_escala_auditores": geral.usar_escala_auditores,
        "volumetria_total": volumetria_total,
        "demanda_diaria_total": demanda_diaria_total,
        "volumetria_run_id": (
            f"rotina_detalhado_bruto_record:{data_volumetria.isoformat()}"
            if data_volumetria else ""
        ),
        "volumetria_data_referencia": (
            data_volumetria.isoformat() if data_volumetria else None
        ),
        "capacidade_total_restante": capacidade_total_restante,
        "capacidade_alocada_restante": capacidade_alocada_restante,
        "ociosidade_capacidade": max(0, capacidade_total_restante - capacidade_alocada_restante),
        "ritmo_diario": round(
            capacidade_alocada_restante / len(dias_restantes), 1
        ) if dias_restantes else 0.0,
        "projecao_ritmo": projecao_final,
        "projecao_fim_mes": projecao_final,
        "avisos": avisos,
        "metodo": (
            "A volumetria mais recente de rotina_detalhado_bruto_record alimenta a calculadora de amostra. "
            "Em cada dia corrido restante, a capacidade da fila (auditores da escala × produtividade diária) "
            "é distribuída proporcionalmente entre os workflows e limitada somente pela volumetria diária "
            "disponível. Os limites mensais de balanceamento não participam desta projeção analítica."
        ),
        "workflows": detalhes,
    }
