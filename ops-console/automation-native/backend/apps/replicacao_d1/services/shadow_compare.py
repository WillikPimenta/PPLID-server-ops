# -*- coding: utf-8 -*-
"""Shadow mode: compara contagens Excel vs banco sem dupla escrita (Fase 15.2)."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db.models import Count

from apps.replicacao_d1.models import ReplicacaoD1Protocolo, ReplicacaoD1Replicado, ReplicacaoD1WorkflowDia
from apps.replicacao_d1.normalization import STATUS_RECEBIDO, STATUS_REPLICADO
from apps.replicacao_d1.services.excel_reader import ParsedReport

logger = logging.getLogger(__name__)

_BRFLOW_UPLOAD_OK = frozenset({"SALVO_OK", "UPLOAD_OK"})


def _excel_upload_ok_count(parsed: ParsedReport) -> int:
    return sum(
        1
        for prot in parsed.protocolos
        if (prot.status_brflow or "").strip().upper() in _BRFLOW_UPLOAD_OK
    )


def compare_parsed_report_to_db(parsed: ParsedReport) -> dict[str, Any]:
    """Compara contagens do parse Excel com o estado atual do banco para o run."""
    run_id = parsed.run_id
    db_protocolos = ReplicacaoD1Protocolo.objects.filter(run_id=run_id).count()
    db_workflows = ReplicacaoD1WorkflowDia.objects.filter(run_id=run_id).count()
    excel_protocolos = len(parsed.protocolos)
    excel_workflows = len(parsed.workflows)
    diffs: list[str] = []
    if excel_protocolos != db_protocolos:
        diffs.append(f"protocolos excel={excel_protocolos} db={db_protocolos}")
    if excel_workflows != db_workflows:
        diffs.append(f"workflows excel={excel_workflows} db={db_workflows}")
    operational = compare_operational_status(parsed)
    diffs.extend(operational.get("diffs", []))
    return {
        "run_id": run_id,
        "protocolos_excel": excel_protocolos,
        "protocolos_db": db_protocolos,
        "workflows_excel": excel_workflows,
        "workflows_db": db_workflows,
        "match": not diffs,
        "diffs": diffs,
        "operational": operational,
    }


def compare_operational_status(parsed: ParsedReport) -> dict[str, Any]:
    """Compara agregados upload (Excel) vs confirmação (banco) por run."""
    run_id = parsed.run_id
    data_ref = parsed.data_referencia_d1
    window_end = data_ref + timedelta(days=1)

    excel_upload_ok = _excel_upload_ok_count(parsed)
    db_status = dict(
        ReplicacaoD1Protocolo.objects.filter(run_id=run_id)
        .values("status_operacional")
        .annotate(c=Count("id"))
        .values_list("status_operacional", "c")
    )
    db_upload_ok = db_status.get(STATUS_RECEBIDO, 0) + db_status.get(STATUS_REPLICADO, 0)
    db_confirmed = db_status.get(STATUS_REPLICADO, 0)

    db_replicados = ReplicacaoD1Replicado.objects.filter(
        report_date__gte=data_ref,
        report_date__lte=window_end,
    ).count()

    diffs: list[str] = []
    if excel_upload_ok != db_upload_ok:
        diffs.append(f"upload excel={excel_upload_ok} db={db_upload_ok}")
    if db_confirmed > db_replicados:
        diffs.append(f"confirmacao db_protocolos={db_confirmed} replicados={db_replicados}")

    return {
        "excel_upload_ok": excel_upload_ok,
        "db_upload_ok": db_upload_ok,
        "db_confirmed": db_confirmed,
        "db_replicados_window": db_replicados,
        "db_status_counts": db_status,
        "match": not diffs,
        "diffs": diffs,
    }


def log_shadow_comparison(parsed: ParsedReport) -> dict[str, Any]:
    result = compare_parsed_report_to_db(parsed)
    op = result.get("operational", {})
    if result["match"]:
        logger.info(
            "[shadow_d1] run %s: contagens OK (upload=%s confirmados=%s)",
            result["run_id"],
            op.get("excel_upload_ok"),
            op.get("db_confirmed"),
        )
    else:
        logger.warning(
            "[shadow_d1] run %s: divergência — %s",
            result["run_id"],
            "; ".join(result["diffs"]),
        )
    return result
