# -*- coding: utf-8 -*-
"""Importação assistida em duas etapas (preview + apply) para config D-1."""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.replicacao_d1.exceptions import ImportPreviewTokenError
from apps.replicacao_d1.models import (
    ReplicacaoD1Cliente,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1Segmento,
    ReplicacaoD1Categoria,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.normalization import normalize_key
from apps.replicacao_d1.services.config_audit import registrar_historico
from apps.replicacao_d1.services.config_snapshot import bump_config_version
from apps.replicacao_d1.services.ledger import ORIGEM_AJUSTE_MANUAL, ORIGEM_CONFIRMADO

IMPORT_KINDS = frozenset(
    {
        "default_xlsx",
        "categoria_xlsx",
        "clientes_csv",
        "workflows_csv",
        "escala_csv",
        "ledger_csv",
    }
)
PREVIEW_TTL_SECONDS = 30 * 60
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

ABA_WORKFLOW_D1 = "Workflow d1"
COL_WORKFLOW = "Workflow"
COL_CLIENTE = "Cliente"
COL_WF_D1 = "Workflow d-1"
COL_WF_SEL = "Workflow - selenium"
COL_FILA = "Fila"
COL_STATUS = "Status"
COL_SEGMENTO = "Segmento"
COL_CATEGORIA = "Categoria"
COL_META = "Meta Cliente"
COL_ESCALA_DATA = "data"
COL_ESCALA_BR = "auditores_ativos"
COL_ESCALA_CASE = "auditores_ativos_case"


def preview_import(
    kind: str,
    file_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    """Parseia arquivo, não escreve no banco; retorna preview + token."""
    kind = str(kind or "").strip().lower()
    if kind not in IMPORT_KINDS:
        raise ValueError(f"kind inválido: {kind}")
    if len(file_bytes or b"") > MAX_UPLOAD_BYTES:
        raise ValueError("Arquivo excede tamanho máximo permitido (10MB).")

    parsed, errors = _parse_file(kind, file_bytes, filename)
    parsed, duplicate_errors = _deduplicate_rows(kind, parsed)
    preview_body = _build_preview(kind, parsed)
    preview_body["errors"] = errors + duplicate_errors + preview_body.get("errors", [])
    preview_body["counts"]["errors"] = len(preview_body["errors"])
    preview_body["kind"] = kind
    preview_body["filename"] = filename

    token = _store_preview_token(preview_body)
    preview_body["preview_token"] = token
    preview_body["expires_at"] = (
        timezone.now() + timedelta(seconds=PREVIEW_TTL_SECONDS)
    ).isoformat(timespec="seconds")
    return preview_body


@transaction.atomic
def apply_import(
    kind: str,
    preview_token: str,
    user,
    *,
    lote_id: str | None = None,
) -> dict[str, Any]:
    """Valida token e aplica upsert transacional."""
    cached = _load_preview_token(preview_token)
    if cached.get("kind") != kind:
        raise ImportPreviewTokenError("Token não corresponde ao kind informado.")
    if cached.get("errors"):
        raise ImportPreviewTokenError("A importação possui erros bloqueantes. Corrija o CSV e gere um novo preview.")

    lote = lote_id or str(uuid.uuid4())
    parsed = cached.get("normalized_payload") or {}
    stats = {"includes": 0, "updates": 0, "unchanged": 0, "inactivated": 0}

    if kind in ("default_xlsx", "workflows_csv"):
        stats = _apply_workflows(parsed, user, lote, inactivate_missing=kind == "default_xlsx")
    elif kind in ("categoria_xlsx", "clientes_csv"):
        stats = _apply_clientes(parsed, user, lote, inactivate_missing=kind == "categoria_xlsx")
    elif kind == "escala_csv":
        stats = _apply_escala(parsed, user, lote)
    elif kind == "ledger_csv":
        stats = _apply_ledger(parsed, user, lote)
    else:
        raise ValueError(f"kind não suportado: {kind}")

    bump_config_version(user=user)
    cache.delete(_cache_key(preview_token))
    return {"ok": True, "lote_id": lote, **stats}


def _parse_file(kind: str, file_bytes: bytes, filename: str) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    if kind.endswith("_xlsx") and ext not in ("xlsx", "xls"):
        errors.append("Extensão esperada: .xlsx")
    if kind.endswith("_csv") and ext != "csv":
        errors.append("Extensão esperada: .csv")

    if kind == "default_xlsx":
        return _parse_default_xlsx(file_bytes), errors
    if kind == "categoria_xlsx":
        return _parse_categoria_xlsx(file_bytes), errors
    if kind == "clientes_csv":
        return _parse_clientes_csv(file_bytes), errors
    if kind == "workflows_csv":
        return _parse_workflows_csv(file_bytes), errors
    if kind == "escala_csv":
        return _parse_escala_csv(file_bytes), errors
    if kind == "ledger_csv":
        return _parse_ledger_csv(file_bytes), errors
    raise ValueError(f"kind inválido: {kind}")


def _parse_default_xlsx(file_bytes: bytes) -> list[dict]:
    xl = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    sheet = _find_sheet(xl.sheet_names, ABA_WORKFLOW_D1)
    raw = xl.parse(sheet)
    raw.columns = [str(c).strip() for c in raw.columns]
    col_wf = _find_col(raw, [COL_WORKFLOW, "workflow"])
    col_cli = _find_col(raw, [COL_CLIENTE, "cliente"])
    col_d1 = _find_col(raw, [COL_WF_D1, "Workflow d1", "Workflow D-1"])
    col_sel = _find_col(raw, [COL_WF_SEL, "Workflow selenium"])
    col_fila = _find_col(raw, [COL_FILA, "fila"])
    col_status = _find_col(raw, [COL_STATUS, "status"])
    if not col_wf:
        raise ValueError(f"Coluna {COL_WORKFLOW} ausente na aba {sheet}")

    rows: list[dict] = []
    for _, r in raw.iterrows():
        nome = str(r.get(col_wf, "") or "").strip()
        if not nome or nome.lower() in ("nan", "total"):
            continue
        status_val = str(r.get(col_status, "") or "").strip().lower() if col_status else ""
        ativo = status_val not in ("false", "0", "inativo", "inactive")
        rows.append(
            {
                "nome_canonico": nome,
                "chave_normalizada": normalize_key(nome),
                "nome_d1": str(r.get(col_d1, "") or nome).strip() if col_d1 else nome,
                "nome_selenium": str(r.get(col_sel, "") or nome).strip() if col_sel else nome,
                "cliente_nome": str(r.get(col_cli, "") or "").strip() if col_cli else "",
                "fila": _normalizar_fila(r.get(col_fila)) if col_fila else "G auditoria",
                "ativo": ativo,
                "status": ReplicacaoD1Workflow.STATUS_ATIVO if ativo else ReplicacaoD1Workflow.STATUS_INATIVO,
            }
        )
    return rows


def _parse_categoria_xlsx(file_bytes: bytes) -> list[dict]:
    raw = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
    raw.columns = [str(c).strip() for c in raw.columns]
    col_cli = _find_col(raw, [COL_CLIENTE, "cliente"])
    col_seg = _find_col(raw, [COL_SEGMENTO, "segmento"])
    col_cat = _find_col(raw, [COL_CATEGORIA, "categoria"])
    col_meta = _find_col(raw, [COL_META, "Meta", "Meta Mensal", "meta mensal"])
    if not col_cli:
        raise ValueError(f"Coluna {COL_CLIENTE} ausente")

    rows: list[dict] = []
    for _, r in raw.iterrows():
        nome = str(r.get(col_cli, "") or "").strip()
        if not nome:
            continue
        meta_val = None
        if col_meta:
            num = pd.to_numeric(r.get(col_meta), errors="coerce")
            if pd.notna(num):
                meta_val = int(round(float(num)))
        rows.append(
            {
                "nome": nome,
                "chave_normalizada": normalize_key(nome),
                "segmento_nome": str(r.get(col_seg, "") or "").strip() if col_seg else "",
                "categoria_nome": str(r.get(col_cat, "") or "").strip() if col_cat else "",
                "meta_mensal": meta_val,
                "ativo": True,
            }
        )
    return rows


def _parse_clientes_csv(file_bytes: bytes) -> list[dict]:
    raw = _read_csv(file_bytes)
    col_nome = _find_col(raw, ["nome", COL_CLIENTE, "cliente"])
    col_seg = _find_col(raw, ["segmento_nome", COL_SEGMENTO, "segmento"])
    col_cat = _find_col(raw, ["categoria_nome", COL_CATEGORIA, "categoria"])
    col_meta = _find_col(raw, ["meta_mensal", COL_META, "meta", "meta mensal"])
    col_ativo = _find_col(raw, ["ativo", "status"])
    if not col_nome:
        raise ValueError("CSV de clientes: coluna nome/cliente obrigatória")

    rows: list[dict] = []
    for _, row in raw.iterrows():
        nome = _cell_text(row.get(col_nome))
        if not nome:
            continue
        meta = None
        if col_meta:
            numero = pd.to_numeric(row.get(col_meta), errors="coerce")
            if pd.notna(numero):
                meta = int(round(float(numero)))
        rows.append(
            {
                "nome": nome,
                "chave_normalizada": normalize_key(nome),
                "segmento_nome": _cell_text(row.get(col_seg)) if col_seg else "",
                "categoria_nome": _cell_text(row.get(col_cat)) if col_cat else "",
                "meta_mensal": meta,
                "ativo": _parse_bool_cell(row.get(col_ativo), default=True) if col_ativo else True,
            }
        )
    return rows


def _parse_workflows_csv(file_bytes: bytes) -> list[dict]:
    raw = _read_csv(file_bytes)
    col_nome = _find_col(raw, ["nome_canonico", COL_WORKFLOW, "workflow"])
    col_d1 = _find_col(raw, ["nome_d1", COL_WF_D1, "workflow d1", "workflow_d1"])
    col_sel = _find_col(raw, ["nome_selenium", COL_WF_SEL, "workflow selenium"])
    col_cli = _find_col(raw, ["cliente_nome", COL_CLIENTE, "cliente"])
    col_fila = _find_col(raw, ["fila", COL_FILA])
    col_status = _find_col(raw, ["status"])
    col_ativo = _find_col(raw, ["ativo"])
    col_pct = _find_col(raw, ["amostra_pct_especial", "amostra %", "amostra_pct"])
    col_100 = _find_col(raw, ["amostra_100", "amostra 100"])
    col_usar_csv = _find_col(
        raw,
        ["usar_arquivo_csv", "usar arquivo csv", "arquivo csv"],
    )
    col_regra = _find_col(raw, ["nome_regra_brflow", "regra", "nome da regra"])
    if not col_nome:
        raise ValueError("CSV de workflows: coluna nome_canonico/workflow obrigatória")

    rows: list[dict] = []
    for _, row in raw.iterrows():
        nome = _cell_text(row.get(col_nome))
        if not nome:
            continue
        ativo = _parse_bool_cell(row.get(col_ativo), default=True) if col_ativo else True
        status_raw = _cell_text(row.get(col_status)).upper() if col_status else ""
        status = status_raw if status_raw in dict(ReplicacaoD1Workflow.STATUS_CHOICES) else (
            ReplicacaoD1Workflow.STATUS_ATIVO if ativo else ReplicacaoD1Workflow.STATUS_INATIVO
        )
        pct = None
        if col_pct:
            numero = pd.to_numeric(row.get(col_pct), errors="coerce")
            if pd.notna(numero):
                pct = min(100, max(0, int(round(float(numero)))))
        amostra_100 = _parse_bool_cell(row.get(col_100), default=False) if col_100 else False
        parsed_row = {
            "nome_canonico": nome,
            "chave_normalizada": normalize_key(nome),
            "nome_d1": _cell_text(row.get(col_d1)) if col_d1 else nome,
            "nome_selenium": _cell_text(row.get(col_sel)) if col_sel else nome,
            "nome_regra_brflow": _cell_text(row.get(col_regra)) if col_regra else "",
            "cliente_nome": _cell_text(row.get(col_cli)) if col_cli else "",
            "fila": _normalizar_fila(row.get(col_fila)) if col_fila else "G auditoria",
            "status": status,
            "ativo": status == ReplicacaoD1Workflow.STATUS_ATIVO,
            "amostra_pct_especial": pct,
            "amostra_100": amostra_100,
        }
        if col_usar_csv:
            parsed_row["usar_arquivo_csv"] = _parse_bool_cell(
                row.get(col_usar_csv),
                default=_fallback_usar_arquivo_csv(parsed_row["fila"]),
            )
        rows.append(parsed_row)
    return rows


def _parse_escala_csv(file_bytes: bytes) -> list[dict]:
    raw = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python", encoding="utf-8-sig")
    raw.columns = [str(c).strip() for c in raw.columns]
    col_data = _find_col(raw, [COL_ESCALA_DATA, "Data", "dia"])
    col_br = _find_col(raw, [COL_ESCALA_BR, "auditores", "Auditores"])
    col_case = _find_col(raw, [COL_ESCALA_CASE, "auditores case", "auditores_case"])
    col_bio = _find_col(raw, ["auditores_bio", "auditores bio", "Auditores Bio"])
    col_redoc = _find_col(raw, ["auditores_redoc", "auditores redoc", "Auditores Redoc"])
    if not col_data or not col_br:
        raise ValueError("CSV de escala inválido: faltam data e auditores_ativos")

    rows: list[dict] = []
    for _, r in raw.iterrows():
        data_str = _parse_data_escala(r.get(col_data))
        if not data_str:
            continue
        br = _parse_nonnegative_int(r.get(col_br), default=0, field="auditores_brflow")
        case = (
            _parse_nonnegative_int(r.get(col_case), default=br, field="auditores_case")
            if col_case
            else br
        )
        bio = _parse_nonnegative_int(r.get(col_bio), default=0, field="auditores_bio") if col_bio else 0
        redoc = _parse_nonnegative_int(r.get(col_redoc), default=0, field="auditores_redoc") if col_redoc else 0
        rows.append({
            "data": data_str,
            "auditores_brflow": br,
            "auditores_case": case,
            "auditores_bio": bio,
            "auditores_redoc": redoc,
        })
    return rows


def _parse_ledger_csv(file_bytes: bytes) -> list[dict]:
    raw = pd.read_csv(io.BytesIO(file_bytes), sep=";", encoding="utf-8-sig", dtype=str)
    if raw.empty:
        raw = pd.read_csv(io.BytesIO(file_bytes), sep=None, engine="python", encoding="utf-8-sig", dtype=str)
    raw.columns = [str(c).strip() for c in raw.columns]
    col_mes = _find_col(raw, ["ano_mes", "competencia", "Competencia"])
    col_wf = _find_col(raw, ["workflow", "Workflow"])
    col_run = _find_col(raw, ["run_id", "Run"])
    col_proto = _find_col(raw, ["protocolos", "Protocolos"])
    col_origem = _find_col(raw, ["origem", "Origem"])
    if not col_mes or not col_wf:
        raise ValueError("Ledger CSV: colunas ano_mes/competencia e workflow obrigatórias")

    rows: list[dict] = []
    for _, r in raw.iterrows():
        wf = str(r.get(col_wf, "") or "").strip()
        if not wf:
            continue
        origem = str(r.get(col_origem, "") or ORIGEM_CONFIRMADO).strip() if col_origem else ORIGEM_CONFIRMADO
        if origem == "manual":
            origem = ORIGEM_AJUSTE_MANUAL
        rows.append(
            {
                "competencia": str(r.get(col_mes, "") or "").strip(),
                "workflow_chave": normalize_key(wf),
                "workflow_nome": wf,
                "run_id": str(r.get(col_run, "") or f"import_{uuid.uuid4().hex[:12]}").strip(),
                "protocolos": int(pd.to_numeric(r.get(col_proto), errors="coerce") or 0),
                "origem": origem,
            }
        )
    return rows


def _build_preview(kind: str, parsed: list[dict]) -> dict[str, Any]:
    includes: list[dict] = []
    updates: list[dict] = []
    unchanged: list[dict] = []
    to_inactivate: list[dict] = []
    errors: list[str] = []

    if kind in ("default_xlsx", "workflows_csv"):
        existing = {w.chave_normalizada: w for w in ReplicacaoD1Workflow.objects.all()}
        seen = set()
        for row in parsed:
            chave = row["chave_normalizada"]
            seen.add(chave)
            obj = existing.get(chave)
            if not obj:
                includes.append(row)
            elif _workflow_changed(obj, row):
                updates.append({"before": obj.nome_canonico, "after": row})
            else:
                unchanged.append({"nome": row["nome_canonico"]})
        if kind == "default_xlsx":
            for chave, obj in existing.items():
                if chave not in seen and obj.ativo and obj.status == ReplicacaoD1Workflow.STATUS_ATIVO:
                    to_inactivate.append({"nome": obj.nome_canonico, "chave": chave})

    elif kind in ("categoria_xlsx", "clientes_csv"):
        existing = {c.chave_normalizada: c for c in ReplicacaoD1Cliente.objects.all()}
        seen = set()
        for row in parsed:
            chave = row["chave_normalizada"]
            seen.add(chave)
            obj = existing.get(chave)
            if not obj:
                includes.append(row)
            elif _cliente_changed(obj, row):
                updates.append({"before": obj.nome, "after": row})
            else:
                unchanged.append({"nome": row["nome"]})
        if kind == "categoria_xlsx":
            for chave, obj in existing.items():
                if chave not in seen and obj.ativo:
                    to_inactivate.append({"nome": obj.nome, "chave": chave})

    elif kind == "escala_csv":
        existing = {e.data.isoformat(): e for e in ReplicacaoD1EscalaDia.objects.all()}
        for row in parsed:
            obj = existing.get(row["data"])
            if not obj:
                includes.append(row)
            elif (
                obj.auditores_brflow != row["auditores_brflow"]
                or obj.auditores_case != row["auditores_case"]
                or obj.auditores_bio != row.get("auditores_bio", 0)
                or obj.auditores_redoc != row.get("auditores_redoc", 0)
            ):
                updates.append({"data": row["data"], "after": row})
            else:
                unchanged.append({"data": row["data"]})

    elif kind == "ledger_csv":
        for row in parsed:
            if not re.match(r"^\d{4}-\d{2}$", row.get("competencia", "")):
                errors.append(f"Competência inválida: {row.get('competencia')}")
                continue
            includes.append(row)

    normalized_payload = {"rows": parsed}
    return {
        "includes": includes,
        "updates": updates,
        "unchanged": unchanged,
        "to_inactivate": to_inactivate,
        "errors": errors,
        "counts": {
            "includes": len(includes),
            "updates": len(updates),
            "unchanged": len(unchanged),
            "to_inactivate": len(to_inactivate),
            "errors": len(errors),
        },
        "normalized_payload": normalized_payload,
    }


def _json_safe_audit(data: dict) -> dict:
    out = {}
    for key, val in (data or {}).items():
        if val is None:
            out[key] = None
        elif hasattr(val, "pk"):
            out[key] = str(val.pk)
        else:
            out[key] = val
    return out


def _apply_workflows(parsed: dict, user, lote_id: str, *, inactivate_missing: bool) -> dict[str, int]:
    rows = parsed.get("rows") or []
    stats = {"includes": 0, "updates": 0, "unchanged": 0, "inactivated": 0}
    seen = set()
    for row in rows:
        chave = row["chave_normalizada"]
        seen.add(chave)
        cliente = None
        cli_nome = row.get("cliente_nome") or ""
        if cli_nome:
            cliente, _ = ReplicacaoD1Cliente.objects.get_or_create(
                chave_normalizada=normalize_key(cli_nome),
                defaults={"nome": cli_nome, "ativo": True},
            )
        obj = ReplicacaoD1Workflow.objects.filter(chave_normalizada=chave).first()
        defaults = {
            "nome_canonico": row["nome_canonico"],
            "nome_d1": row.get("nome_d1") or row["nome_canonico"],
            "nome_selenium": row.get("nome_selenium") or row["nome_canonico"],
            "nome_regra_brflow": row.get("nome_regra_brflow") or "",
            "fila": row.get("fila") or "G auditoria",
            "cliente": cliente,
            "status": row.get("status", ReplicacaoD1Workflow.STATUS_ATIVO),
            "ativo": bool(row.get("ativo", True)),
            "amostra_pct_especial": row.get("amostra_pct_especial"),
            "amostra_100": bool(row.get("amostra_100", False)),
            "updated_by": user if getattr(user, "is_authenticated", False) else None,
        }
        if "usar_arquivo_csv" in row:
            defaults["usar_arquivo_csv"] = bool(row["usar_arquivo_csv"])
        elif obj is None:
            # Imports legados criam Bio/Redoc no modo quantidade e as demais
            # filas no modo protocolos, preservando o comportamento anterior.
            defaults["usar_arquivo_csv"] = _fallback_usar_arquivo_csv(defaults["fila"])
        if obj:
            if not _workflow_changed(obj, row):
                stats["unchanged"] += 1
                continue
            before = {"nome": obj.nome_canonico, "status": obj.status}
            for k, v in defaults.items():
                setattr(obj, k, v)
            obj.save()
            stats["updates"] += 1
            registrar_historico("ReplicacaoD1Workflow", obj.pk, "import_update", user, before, _json_safe_audit(defaults), lote_id=lote_id)
        else:
            obj = ReplicacaoD1Workflow.objects.create(
                chave_normalizada=chave,
                created_by=user if getattr(user, "is_authenticated", False) else None,
                **defaults,
            )
            stats["includes"] += 1
            registrar_historico("ReplicacaoD1Workflow", obj.pk, "import_create", user, None, _json_safe_audit(defaults), lote_id=lote_id)

    if inactivate_missing:
        for obj in ReplicacaoD1Workflow.objects.filter(ativo=True, status=ReplicacaoD1Workflow.STATUS_ATIVO):
            if obj.chave_normalizada not in seen:
                before = {"ativo": obj.ativo, "status": obj.status}
                obj.status = ReplicacaoD1Workflow.STATUS_INATIVO
                obj.ativo = False
                obj.updated_by = user if getattr(user, "is_authenticated", False) else None
                obj.save()
                stats["inactivated"] += 1
                registrar_historico("ReplicacaoD1Workflow", obj.pk, "import_inactivate", user, before, before, lote_id=lote_id)
    return stats


def _apply_clientes(parsed: dict, user, lote_id: str, *, inactivate_missing: bool) -> dict[str, int]:
    rows = parsed.get("rows") or []
    stats = {"includes": 0, "updates": 0, "unchanged": 0, "inactivated": 0}
    seen = set()
    for row in rows:
        chave = row["chave_normalizada"]
        seen.add(chave)
        segmento = _get_or_create_segmento(row.get("segmento_nome"), user)
        categoria = _get_or_create_categoria(row.get("categoria_nome"), segmento, user)
        obj = ReplicacaoD1Cliente.objects.filter(chave_normalizada=chave).first()
        defaults = {
            "nome": row["nome"],
            "segmento_nome": row.get("segmento_nome") or "",
            "categoria_nome": row.get("categoria_nome") or "",
            "segmento": segmento,
            "categoria": categoria,
            "meta_mensal": row.get("meta_mensal"),
            "ativo": bool(row.get("ativo", True)),
            "updated_by": user if getattr(user, "is_authenticated", False) else None,
        }
        if obj:
            if not _cliente_changed(obj, row):
                stats["unchanged"] += 1
                continue
            before = {"nome": obj.nome, "meta_mensal": obj.meta_mensal}
            for k, v in defaults.items():
                setattr(obj, k, v)
            obj.save()
            stats["updates"] += 1
            registrar_historico("ReplicacaoD1Cliente", obj.pk, "import_update", user, before, _json_safe_audit(defaults), lote_id=lote_id)
        else:
            obj = ReplicacaoD1Cliente.objects.create(
                chave_normalizada=chave,
                created_by=user if getattr(user, "is_authenticated", False) else None,
                **defaults,
            )
            stats["includes"] += 1
            registrar_historico("ReplicacaoD1Cliente", obj.pk, "import_create", user, None, _json_safe_audit(defaults), lote_id=lote_id)

    if inactivate_missing:
        for obj in ReplicacaoD1Cliente.objects.filter(ativo=True):
            if obj.chave_normalizada not in seen:
                before = {"ativo": obj.ativo}
                obj.ativo = False
                obj.updated_by = user if getattr(user, "is_authenticated", False) else None
                obj.save()
                stats["inactivated"] += 1
                registrar_historico("ReplicacaoD1Cliente", obj.pk, "import_inactivate", user, before, before, lote_id=lote_id)
    return stats


def _apply_escala(parsed: dict, user, lote_id: str) -> dict[str, int]:
    rows = parsed.get("rows") or []
    stats = {"includes": 0, "updates": 0, "unchanged": 0, "inactivated": 0}
    for row in rows:
        data = datetime.strptime(row["data"], "%Y-%m-%d").date()
        obj, created = ReplicacaoD1EscalaDia.objects.update_or_create(
            data=data,
            defaults={
                "auditores_brflow": row["auditores_brflow"],
                "auditores_case": row["auditores_case"],
                "auditores_bio": row.get("auditores_bio", 0),
                "auditores_redoc": row.get("auditores_redoc", 0),
            },
        )
        if created:
            stats["includes"] += 1
            registrar_historico("ReplicacaoD1EscalaDia", obj.pk, "import_create", user, None, row, lote_id=lote_id)
        else:
            stats["updates"] += 1
            registrar_historico("ReplicacaoD1EscalaDia", obj.pk, "import_update", user, None, row, lote_id=lote_id)
    return stats


def _apply_ledger(parsed: dict, user, lote_id: str) -> dict[str, int]:
    rows = parsed.get("rows") or []
    stats = {"includes": 0, "updates": 0, "unchanged": 0, "inactivated": 0}
    for row in rows:
        obj, created = ReplicacaoD1LedgerConsumo.objects.update_or_create(
            run_id=row["run_id"],
            competencia=row["competencia"],
            workflow_chave=row["workflow_chave"],
            origem=row.get("origem", ORIGEM_CONFIRMADO),
            defaults={
                "workflow_nome": row.get("workflow_nome", ""),
                "protocolos": int(row.get("protocolos") or 0),
                "data_execucao": timezone.now(),
                "ajuste": int(row.get("protocolos") or 0) if row.get("origem") == ORIGEM_AJUSTE_MANUAL else 0,
            },
        )
        if created:
            stats["includes"] += 1
        else:
            stats["updates"] += 1
        registrar_historico("ReplicacaoD1LedgerConsumo", obj.pk, "import_upsert", user, None, row, lote_id=lote_id)
    return stats


def _get_or_create_segmento(nome: str, user):
    nome = str(nome or "").strip()
    if not nome:
        return None
    chave = normalize_key(nome)
    obj, _ = ReplicacaoD1Segmento.objects.get_or_create(
        chave_normalizada=chave,
        defaults={"nome": nome, "created_by": user if getattr(user, "is_authenticated", False) else None},
    )
    return obj


def _get_or_create_categoria(nome: str, segmento, user):
    nome = str(nome or "").strip()
    if not nome:
        return None
    chave = normalize_key(nome)
    obj, _ = ReplicacaoD1Categoria.objects.get_or_create(
        chave_normalizada=chave,
        defaults={
            "nome": nome,
            "segmento": segmento,
            "created_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    if segmento is not None and obj.segmento_id != getattr(segmento, "pk", None):
        obj.segmento = segmento
        obj.save(update_fields=["segmento", "updated_at"])
    return obj


def _workflow_changed(obj: ReplicacaoD1Workflow, row: dict) -> bool:
    checks = [
        obj.nome_d1 != (row.get("nome_d1") or row["nome_canonico"]),
        obj.nome_selenium != (row.get("nome_selenium") or row["nome_canonico"]),
        (obj.nome_regra_brflow or "") != (row.get("nome_regra_brflow") or ""),
        obj.fila != (row.get("fila") or "G auditoria"),
        obj.status != row.get("status"),
        obj.cliente_id != _cliente_id_por_nome(row.get("cliente_nome")),
        obj.amostra_pct_especial != row.get("amostra_pct_especial"),
        obj.amostra_100 != bool(row.get("amostra_100", False)),
    ]
    if "usar_arquivo_csv" in row:
        checks.append(obj.usar_arquivo_csv != bool(row["usar_arquivo_csv"]))
    return any(checks)


def _cliente_changed(obj: ReplicacaoD1Cliente, row: dict) -> bool:
    return any(
        [
            obj.segmento_nome != (row.get("segmento_nome") or ""),
            obj.categoria_nome != (row.get("categoria_nome") or ""),
            obj.meta_mensal != row.get("meta_mensal"),
            obj.ativo != bool(row.get("ativo", True)),
        ]
    )


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _read_csv(file_bytes: bytes) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            raw = pd.read_csv(
                io.BytesIO(file_bytes),
                sep=None,
                engine="python",
                encoding=encoding,
                dtype=str,
            )
            raw.columns = [str(column).strip() for column in raw.columns]
            return raw
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise ValueError(
        "Não foi possível ler o CSV. Salve como UTF-8 (CSV UTF-8 no Excel) "
        "ou evite regravar o arquivo após abrir no Excel."
    ) from last_error


def _cell_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in ("nan", "none", "null") else text


def _parse_bool_cell(value, *, default: bool) -> bool:
    text = _cell_text(value).casefold()
    if not text:
        return default
    return text in ("1", "true", "sim", "s", "yes", "ativo", "active")


def _fallback_usar_arquivo_csv(fila: str) -> bool:
    return str(fila or "").strip().casefold() not in ("bio", "redoc")


def _parse_nonnegative_int(value, *, default: int, field: str) -> int:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return default
    parsed = int(number)
    if parsed < 0:
        raise ValueError(f"{field} nao pode ser negativo.")
    return parsed


def _cliente_id_por_nome(nome: str) -> int | None:
    chave = normalize_key(nome)
    if not chave:
        return None
    return ReplicacaoD1Cliente.objects.filter(chave_normalizada=chave).values_list("id", flat=True).first()


def _deduplicate_rows(kind: str, rows: list[dict]) -> tuple[list[dict], list[str]]:
    if kind in ("default_xlsx", "workflows_csv", "categoria_xlsx", "clientes_csv"):
        key_field = "chave_normalizada"
    elif kind == "escala_csv":
        key_field = "data"
    else:
        return rows, []
    unique: list[dict] = []
    seen: set[str] = set()
    errors: list[str] = []
    for index, row in enumerate(rows, start=2):
        key = str(row.get(key_field) or "").strip()
        if key in seen:
            errors.append(f"Linha {index}: registro duplicado ({key}).")
            continue
        seen.add(key)
        unique.append(row)
    return unique, errors


def _find_sheet(names: list[str], target: str) -> str:
    alvo = target.strip().casefold()
    for nome in names:
        if str(nome).strip().casefold() == alvo:
            return nome
    for nome in names:
        if alvo in str(nome).strip().casefold():
            return nome
    raise ValueError(f"Aba '{target}' não encontrada. Abas: {names}")


def _normalizar_fila(val) -> str:
    text = str(val or "").strip()
    return text if text else "G auditoria"


def _parse_data_escala(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    text = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10] if fmt != "%Y%m%d" else text[:8], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _store_preview_token(preview_body: dict) -> str:
    payload_for_hash = {
        "kind": preview_body.get("kind"),
        "normalized_payload": preview_body.get("normalized_payload"),
        "counts": preview_body.get("counts"),
    }
    digest = hashlib.sha256(
        json.dumps(payload_for_hash, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    token = f"{digest[:32]}_{uuid.uuid4().hex[:8]}"
    cache.set(_cache_key(token), preview_body, timeout=PREVIEW_TTL_SECONDS)
    return token


def _load_preview_token(token: str) -> dict:
    if not token:
        raise ImportPreviewTokenError("preview_token obrigatório.")
    data = cache.get(_cache_key(token))
    if not data:
        raise ImportPreviewTokenError("Token inválido ou expirado.")
    return data


def _cache_key(token: str) -> str:
    return f"replicacao_d1_import_preview:{token}"
