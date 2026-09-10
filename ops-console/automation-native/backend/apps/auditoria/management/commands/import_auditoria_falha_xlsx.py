# -*- coding: utf-8 -*-
"""Importa exportação tabular de auditoria_falha_cadastro (.xlsx) para homologação local."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from apps.auditoria.models import AuditoriaAtividade, AuditoriaFalhaCadastro, AuditoriaMotivoFalha
from apps.auditoria.services.analise_origem import get_or_create_analise_origem
from apps.auditoria.services.agent_links import resolve_agent_reference
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha, QualidadeIntranetProjection
from apps.qualidade_operacional.services.intranet_source import INTRANET_SOURCE_FILE
from apps.qualidade_operacional.services.normalize import clean_text, fold_ascii_upper

User = get_user_model()

# Colunas textuais importadas do export SQL/Excel.
_TEXT_FIELDS = (
    "protocolo",
    "brflow_raw",
    "modulo",
    "demanda_url",
    "tipo_falha",
    "usuario",
    "resultado_cliente",
    "novo_resultado",
    "sinalizacao",
    "motivo_falha",
    "etapa_falha",
    "nivel_dificuldade",
    "tipo_documento",
    "uf_documento",
    "tipo_registro",
    "descricao_irregularidades",
    "cliente",
    "status",
    "observacao",
    "auditor",
    "analise_status",
    "fila_origem",
    "qualidade_imagem",
    "origem",
    "status_falha",
)

_DATETIME_FIELDS = (
    "data_contestacao",
    "data_analise",
    "data_resposta",
    "atribuido_em",
    "analise_iniciada_em",
    "analise_concluida_em",
    "mapping_applied_at",
)

_MAPPING_TEXT_FIELDS = (
    "codigo_irregularidade",
    "mapping_scenario_status",
    "mapping_stage_status",
    "mapping_version",
    "mapping_source_hash",
)

_IMPORT_SOURCE_KEY = "homolog_import_source"
_IMPORT_ROW_KEY = "homolog_import_row_id"

_RE_AUTOMATICO = re.compile(r"^autom.tico$", re.I)

_PROFILE_AUTO = "auto"
_PROFILE_EXPORT = "export"
_PROFILE_CLARO_CONFER = "claro-confer-consolidado"

_CLARO_CONFER_CLIENT_ID = 83
_CLARO_CONFER_WORKFLOW_ID = 450
_CLARO_CONFER_CLIENT_NAME = "Claro"
_CLARO_CONFER_WORKFLOW_NAME = "Claro Confer"

_CONSOLIDATED_HEADER_ALIASES = {
    "origem": {"ORIGEM"},
    "cliente": {"CLIENTE"},
    "protocolo": {"PROTOCOLO"},
    "modulo": {
        "MODULO / TIPO DE SERVICO",
        "MDULO / TIPO DE SERVIO",
    },
    "status": {"STATUS"},
    "observacao": {"OBSERVACAO", "OBSERVAO"},
    "irregularidades": {"IRREGULARIDADES APONTADAS"},
    "status_irregularidade": {"STATUS DA IRREGULARIDADE"},
    "matricula_inspetor": {
        "MATRICULA DO INSPETOR",
        "MATRCULA DO INSPETOR",
    },
    "nome_inspetor": {"NOME DO INSPETOR"},
    "data_inspecao": {"DATA DE INSPECAO", "DATA DE INSPEO"},
    "matricula_auditor": {
        "MATRICULA DO AUDITOR",
        "MATRCULA DO AUDITOR",
    },
    "nome_auditor": {"NOME DO AUDITOR"},
    "data_auditoria": {"DATA DA AUDITORIA"},
    "data_contestacao": {"DATA DE CONTESTACAO", "DATA DE CONTESTAO"},
    "data_resposta": {"DATA DE RESPOSTA"},
    "arquivo_origem": {"ARQUIVO DE ORIGEM"},
}

_CONSOLIDATED_REQUIRED_FIELDS = frozenset(
    {
        "origem",
        "protocolo",
        "modulo",
        "status",
        "irregularidades",
        "matricula_inspetor",
        "matricula_auditor",
        "data_auditoria",
        "data_contestacao",
        "data_resposta",
        "arquivo_origem",
    }
)


def _fix_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _resolve_input_path(raw_path: str) -> Path:
    """Resolve o XLSX mesmo quando o terminal substitui acentos do diretório por U+FFFD."""
    path = Path(raw_path).expanduser()
    if path.is_file():
        return path

    ancestor = path.parent
    while ancestor != ancestor.parent and not ancestor.exists():
        ancestor = ancestor.parent
    if ancestor.exists() and path.name:
        matches = [candidate for candidate in ancestor.rglob(path.name) if candidate.is_file()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise CommandError(
                "Caminho ambíguo após normalização do terminal: "
                + ", ".join(str(item) for item in matches[:5])
            )
    raise CommandError(f"Arquivo não encontrado: {path}")


def _header_key(value: Any) -> str:
    """Normaliza cabeçalhos inclusive quando o XLSX contém U+FFFD no lugar de acentos."""
    text = _fix_text(value).replace("\ufffd", "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.upper().replace("_", " ")
    return " ".join(text.split())


def _consolidated_column_map(headers: list[Any]) -> dict[str, int]:
    aliases = {
        alias: field
        for field, field_aliases in _CONSOLIDATED_HEADER_ALIASES.items()
        for alias in field_aliases
    }
    result: dict[str, int] = {}
    for index, header in enumerate(headers):
        field = aliases.get(_header_key(header))
        if field and field not in result:
            result[field] = index
    return result


def _cell(values: tuple[Any, ...], columns: dict[str, int], field: str) -> Any:
    index = columns.get(field)
    if index is None or index >= len(values):
        return None
    return values[index]


def _consolidated_origin(value: Any) -> str:
    key = _header_key(value)
    if key.startswith("AUDITORIA"):
        return AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
    if key.startswith("CONTEST"):
        return AuditoriaFalhaCadastro.ORIGEM_REINSPECAO
    raise ValueError(f"origem não reconhecida: {_fix_text(value)!r}")


def _consolidated_is_failure(value: Any) -> bool:
    key = _header_key(value)
    if key in {"CONFORME", "PROCEDENTE"}:
        return False
    if key in {"NAO CONFORME", "NO CONFORME"}:
        return True
    raise ValueError(f"status não reconhecido: {_fix_text(value)!r}")


def _transform_claro_confer_row(
    values: tuple[Any, ...],
    *,
    columns: dict[str, int],
    row_number: int,
    resolver,
) -> tuple[dict[str, Any], dict[str, int]]:
    protocolo = _fix_text(_cell(values, columns, "protocolo"))
    if not protocolo:
        raise ValueError("protocolo vazio")

    origem = _consolidated_origin(_cell(values, columns, "origem"))
    source_status = _fix_text(_cell(values, columns, "status"))
    is_failure = _consolidated_is_failure(source_status)
    descricao = _fix_text(_cell(values, columns, "irregularidades"))
    source_module = _fix_text(_cell(values, columns, "modulo"))
    source_file = _fix_text(_cell(values, columns, "arquivo_origem"))
    data_contestacao = _cell(values, columns, "data_contestacao")
    data_auditoria = _cell(values, columns, "data_auditoria")
    data_resposta = _cell(values, columns, "data_resposta")

    fila_contexto = (
        "auditoria_compliance"
        if origem == AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
        else "reinspecao"
    )
    if origem == AuditoriaFalhaCadastro.ORIGEM_AUDITORIA:
        status = "Não conforme" if is_failure else "Conforme"
        tipo_falha = "auditoria"
        modulo = "Auditoria"
        motivo_falha = ""
        etapa_falha = source_module
        data_analise = data_auditoria or data_resposta
        analise_concluida_em = data_resposta or data_auditoria
        mapping_fields = {field: "" for field in _MAPPING_TEXT_FIELDS}
        mapping_applied_at = None
    else:
        status = "Improcedente" if is_failure else "Procedente"
        tipo_falha = "reinspecao"
        modulo = "Contestação"
        resolution = resolver.resolve(descricao)
        motivo_falha = resolution.scenario
        etapa_falha = resolution.stage
        data_analise = None
        analise_concluida_em = data_resposta
        mapping_fields = {
            "codigo_irregularidade": resolution.extracted.code,
            "mapping_scenario_status": resolution.scenario_status,
            "mapping_stage_status": resolution.stage_status,
            "mapping_version": resolution.mapping_version,
            "mapping_source_hash": resolution.source_hash,
        }
        mapping_applied_at = timezone.now()

    if _parse_dt(analise_concluida_em) is None:
        raise ValueError("data de conclusão inválida ou vazia")

    brflow = {
        "fila_contexto": fila_contexto,
        "cliente": _CLARO_CONFER_CLIENT_NAME,
        "workflow": _CLARO_CONFER_WORKFLOW_NAME,
        "id_cliente": _CLARO_CONFER_CLIENT_ID,
        "id_workflow": _CLARO_CONFER_WORKFLOW_ID,
        "consolidado_origem_original": _fix_text(_cell(values, columns, "origem")),
        "consolidado_status_original": source_status,
        "consolidado_modulo_original": source_module,
        "consolidado_arquivo_origem": source_file,
        "consolidado_excel_row": row_number,
        "nome_inspetor": _fix_text(_cell(values, columns, "nome_inspetor")),
        "nome_auditor": _fix_text(_cell(values, columns, "nome_auditor")),
    }
    row = {
        "id": f"consolidado-row-{row_number}",
        "protocolo": protocolo,
        "brflow_parsed": brflow,
        "modulo": modulo,
        "tipo_falha": tipo_falha,
        "usuario": _fix_text(_cell(values, columns, "matricula_inspetor")) or "sistema",
        "auditor": _fix_text(_cell(values, columns, "matricula_auditor")),
        "resultado_cliente": source_status,
        "novo_resultado": status,
        "motivo_falha": motivo_falha,
        "etapa_falha": etapa_falha,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
        "descricao_irregularidades": descricao,
        "cliente": _CLARO_CONFER_CLIENT_NAME,
        "status": status,
        "observacao": _fix_text(_cell(values, columns, "observacao")),
        "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
        "origem": origem,
        "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        "data_contestacao": data_contestacao if origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO else None,
        "data_analise": data_analise,
        "data_resposta": data_resposta,
        "analise_concluida_em": analise_concluida_em,
        "mapping_applied_at": mapping_applied_at,
        **mapping_fields,
    }
    source_status_key = _header_key(source_status)
    source_status_bucket = (
        "nao_conforme"
        if source_status_key in {"NAO CONFORME", "NO CONFORME"}
        else source_status_key.lower()
    )
    stats = {
        "auditados": 1,
        "falhas": int(is_failure),
        "auditoria": int(origem == AuditoriaFalhaCadastro.ORIGEM_AUDITORIA),
        "reinspecao": int(origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO),
        f"status_{source_status_bucket}": 1,
        "usuario_sistema": int(_header_key(row["usuario"]) == "SISTEMA"),
        "mapping_unmatched": int(
            origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO
            and (
                mapping_fields["mapping_scenario_status"] != "matched"
                or mapping_fields["mapping_stage_status"] != "matched"
            )
        ),
    }
    return row, stats


def _normalize_tipo_falha(value: str) -> str:
    text = _fix_text(value)
    if not text:
        return text
    if _RE_AUTOMATICO.match(fold_ascii_upper(text.replace("\ufffd", "").replace("?", ""))):
        return "Automático"
    if fold_ascii_upper(text) == "SEM FALHA":
        return "Sem Falha"
    return text


def _parse_brflow(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    text = _fix_text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        # Algumas células exportadas pelo Excel preservam o apóstrofo usado
        # para forçar texto (ex.: '2026-08-11 11:58:46.21191-03).
        text = _fix_text(value).lstrip("'")
        if not text:
            return None
        # Export SQL/Excel: "2026-08-06 11:53:50.601679-03"
        exported = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
            r"(?:\.(\d+))?([+-]\d{2})(?::(\d{2}))?",
            text,
        )
        if exported:
            fraction = exported.group(2)
            fraction_part = (
                f".{fraction[:6].ljust(6, '0')}" if fraction else ""
            )
            text = (
                f"{exported.group(1)}{fraction_part}"
                f"{exported.group(3)}:{exported.group(4) or '00'}"
            )
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _row_dict(headers: list[str], values: tuple[Any, ...]) -> dict[str, Any]:
    return {headers[i]: values[i] if i < len(values) else None for i in range(len(headers))}


def _natural_key(row: dict[str, Any]) -> tuple:
    return (
        _fix_text(row.get("protocolo")),
        _fix_text(row.get("tipo_registro")),
        _normalize_tipo_falha(_fix_text(row.get("tipo_falha"))),
        _fix_text(row.get("etapa_falha")),
        _fix_text(row.get("origem")),
        _fix_text(row.get("motivo_falha")),
    )


def _ensure_activity(
    *,
    atividade_id: int | None,
    cliente: str,
    workflow: str,
    encerrado_em: datetime | None,
    user,
) -> AuditoriaAtividade | None:
    if not atividade_id:
        return None
    atividade = AuditoriaAtividade.objects.filter(pk=atividade_id).first()
    if atividade is None:
        atividade = AuditoriaAtividade(
            pk=atividade_id,
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome=f"Import atividade {atividade_id}",
            status=AuditoriaAtividade.STATUS_CONCLUIDA,
            cliente=cliente or "",
            workflow=workflow or "",
            encerrado_em=encerrado_em,
            created_by=user,
        )
        atividade.save()
        return atividade
    changed = []
    if atividade.tipo != AuditoriaAtividade.TIPO_AUDITORIA:
        atividade.tipo = AuditoriaAtividade.TIPO_AUDITORIA
        changed.append("tipo")
    if atividade.status != AuditoriaAtividade.STATUS_CONCLUIDA:
        atividade.status = AuditoriaAtividade.STATUS_CONCLUIDA
        changed.append("status")
    if cliente and not atividade.cliente:
        atividade.cliente = cliente
        changed.append("cliente")
    if workflow and not atividade.workflow:
        atividade.workflow = workflow
        changed.append("workflow")
    if encerrado_em and atividade.encerrado_em is None:
        atividade.encerrado_em = encerrado_em
        changed.append("encerrado_em")
    if changed:
        atividade.save(update_fields=[*changed, "updated_at"])
    return atividade


def _ensure_motivo(motivo: str) -> None:
    text = _fix_text(motivo)
    if not text:
        return
    AuditoriaMotivoFalha.objects.get_or_create(
        motivo=text,
        defaults={
            "criticidade": "Crítica",
            "segmentos": "Docs",
            "subsegmento": "Importado",
            "active": True,
        },
    )


class Command(BaseCommand):
    help = "Importa export .xlsx de auditoria_falha_cadastro (evidência/homologação)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "xlsx",
            nargs="?",
            default="",
            help="Caminho do .xlsx (export da tabela).",
        )
        parser.add_argument(
            "--purge-seeds",
            action="store_true",
            help="Remove EO-SEED-* e EO-REAL-* antes de importar.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Executa sync_qualidade_intranet após importar.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente valida/leitura; não grava.",
        )
        parser.add_argument(
            "--profile",
            choices=(_PROFILE_AUTO, _PROFILE_EXPORT, _PROFILE_CLARO_CONFER),
            default=_PROFILE_AUTO,
            help=(
                "Formato da planilha. 'auto' detecta o consolidado Claro Confer "
                "pelos cabeçalhos; 'export' preserva o contrato legado."
            ),
        )

    def handle(self, *args, **options) -> None:
        raw_path = (options["xlsx"] or "").strip()
        if not raw_path:
            raise CommandError("Informe o caminho do .xlsx.")
        path = _resolve_input_path(raw_path)

        user = User.objects.order_by("date_joined").first()
        dry_run = bool(options["dry_run"])
        if options["purge_seeds"] and not dry_run:
            self._purge_seeds()

        rows, load_summary = self._load_rows(path, profile=options["profile"])
        if not rows:
            raise CommandError("Planilha vazia.")

        created = updated = skipped = auditados = falhas = nao_classificados = 0
        source_marker = path.name
        existing_by_import_id: dict[str, AuditoriaFalhaCadastro] = {}
        existing_qs = AuditoriaFalhaCadastro.objects.filter(
            brflow_parsed__homolog_import_source=source_marker,
        ).prefetch_related("alteracoes")
        for item in existing_qs.iterator(chunk_size=500):
            parsed = item.brflow_parsed if isinstance(item.brflow_parsed, dict) else {}
            import_id = _fix_text(parsed.get(_IMPORT_ROW_KEY))
            if import_id:
                existing_by_import_id[import_id] = item

        # A projeção é executada uma única vez ao final por --sync. Isso evita
        # milhares de callbacks post_save durante uma carga de homologação.
        with override_settings(QUALIDADE_INTRANET_SOURCE_ENABLED=False):
            with transaction.atomic():
                for row_number, row in enumerate(rows, start=2):
                    key = _natural_key(row)
                    if not key[0]:
                        skipped += 1
                        continue

                    brflow = _parse_brflow(row.get("brflow_parsed"))
                    export_id = _fix_text(row.get("id")) or f"excel-row-{row_number}"
                    brflow = {
                        **brflow,
                        _IMPORT_SOURCE_KEY: source_marker,
                        _IMPORT_ROW_KEY: export_id,
                    }
                    cliente = _fix_text(row.get("cliente")) or _fix_text(brflow.get("cliente"))
                    workflow = _fix_text(brflow.get("workflow"))
                    analise_em = _parse_dt(row.get("analise_concluida_em"))

                    atividade_id_raw = row.get("atividade_id")
                    atividade_id = int(atividade_id_raw) if atividade_id_raw not in (None, "") else None
                    atividade = None
                    if atividade_id and row.get("tipo_registro") == AuditoriaFalhaCadastro.REGISTRO_AUDITORIA:
                        if not dry_run:
                            atividade = _ensure_activity(
                                atividade_id=atividade_id,
                                cliente=cliente,
                                workflow=workflow,
                                encerrado_em=analise_em,
                                user=user,
                            )

                    motivo = _fix_text(row.get("motivo_falha"))
                    if motivo and not dry_run:
                        _ensure_motivo(motivo)

                    payload = {
                        "protocolo": _fix_text(row.get("protocolo")),
                        "brflow_raw": _fix_text(row.get("brflow_raw")),
                        "brflow_parsed": brflow,
                        "modulo": _fix_text(row.get("modulo")),
                        "demanda_url": _fix_text(row.get("demanda_url")),
                        "tipo_falha": _normalize_tipo_falha(_fix_text(row.get("tipo_falha"))),
                        "usuario": _fix_text(row.get("usuario")),
                        "resultado_cliente": _fix_text(row.get("resultado_cliente")),
                        "novo_resultado": _fix_text(row.get("novo_resultado")),
                        "sinalizacao": _fix_text(row.get("sinalizacao")),
                        "motivo_falha": motivo,
                        "etapa_falha": _fix_text(row.get("etapa_falha")),
                        "nivel_dificuldade": _fix_text(row.get("nivel_dificuldade")),
                        "tipo_documento": _fix_text(row.get("tipo_documento")),
                        "uf_documento": _fix_text(row.get("uf_documento")),
                        "tipo_registro": _fix_text(row.get("tipo_registro"))
                        or AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
                        "descricao_irregularidades": _fix_text(row.get("descricao_irregularidades")),
                        "cliente": cliente,
                        "status": _fix_text(row.get("status")),
                        "observacao": _fix_text(row.get("observacao")),
                        "auditor": _fix_text(row.get("auditor")),
                        "analise_status": _fix_text(row.get("analise_status"))
                        or AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
                        "fila_origem": _fix_text(row.get("fila_origem")),
                        "qualidade_imagem": _fix_text(row.get("qualidade_imagem")),
                        "origem": _fix_text(row.get("origem")),
                        "status_falha": _fix_text(row.get("status_falha"))
                        or AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                    }
                    for field in _MAPPING_TEXT_FIELDS:
                        payload[field] = _fix_text(row.get(field))
                    for field in _DATETIME_FIELDS:
                        payload[field] = _parse_dt(row.get(field))

                    resultado = AuditoriaFalhaCadastro.inferir_resultado_qualidade(
                        status=payload["status"],
                        tipo_falha=payload["tipo_falha"],
                        origem=payload["origem"],
                        tipo_registro=payload["tipo_registro"],
                        status_falha=payload["status_falha"],
                        brflow_parsed=payload["brflow_parsed"],
                    )
                    if resultado == AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO:
                        nao_classificados += 1
                    else:
                        auditados += 1
                        falhas += int(resultado == AuditoriaFalhaCadastro.RESULTADO_COM_FALHA)

                    analise_origem = None
                    if not dry_run:
                        origin_payload = dict(brflow)
                        if payload["data_analise"] is not None:
                            origin_payload.setdefault(
                                "data_analise", payload["data_analise"].isoformat()
                            )
                        if payload["resultado_cliente"]:
                            origin_payload.setdefault(
                                "resultado_analise", payload["resultado_cliente"]
                            )
                        if cliente:
                            origin_payload.setdefault("cliente", cliente)
                        if workflow:
                            origin_payload.setdefault("workflow", workflow)
                        analise_origem = get_or_create_analise_origem(
                            protocolo=payload["protocolo"],
                            brflow_raw=payload["brflow_raw"],
                            brflow_parsed=origin_payload,
                        )

                    existing = existing_by_import_id.get(export_id)
                    if existing is None and load_summary["profile"] == _PROFILE_EXPORT:
                        existing = self._find_existing(key)
                    if dry_run:
                        if existing is None:
                            created += 1
                        elif list(existing.alteracoes.all()):
                            skipped += 1
                        else:
                            updated += 1
                        continue

                    if existing is None:
                        agente_ref = resolve_agent_reference(payload.get("usuario")).agent
                        auditor_ref = resolve_agent_reference(payload.get("auditor")).agent
                        AuditoriaFalhaCadastro.objects.create(
                            **payload,
                            atividade=atividade,
                            analise_origem=analise_origem,
                            agente_ref=agente_ref,
                            auditor_ref=auditor_ref,
                            created_by=user,
                        )
                        created += 1
                    else:
                        if existing.alteracoes.exists():
                            skipped += 1
                            continue
                        for key_name, value in payload.items():
                            setattr(existing, key_name, value)
                        existing.atividade = atividade
                        existing.analise_origem = analise_origem
                        existing.agente_ref = resolve_agent_reference(payload.get("usuario")).agent
                        existing.auditor_ref = resolve_agent_reference(payload.get("auditor")).agent
                        existing.save()
                        updated += 1

                if dry_run:
                    transaction.set_rollback(True)

        if nao_classificados:
            raise CommandError(
                f"Preflight encontrou {nao_classificados} linha(s) sem classificação auditado/falha."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import {'(dry-run) ' if dry_run else ''}"
                f"rows={len(rows)} created={created} updated={updated} skipped={skipped}"
            )
        )
        self.stdout.write(
            "Preflight "
            f"profile={load_summary['profile']} auditados={auditados} falhas={falhas} "
            f"auditoria={load_summary.get('auditoria', 0)} "
            f"reinspecao={load_summary.get('reinspecao', 0)} "
            f"conforme={load_summary.get('status_conforme', 0)} "
            f"procedente={load_summary.get('status_procedente', 0)} "
            f"nao_conforme={load_summary.get('status_nao_conforme', 0)} "
            f"usuario_sistema={load_summary.get('usuario_sistema', 0)} "
            f"mapping_unmatched={load_summary.get('mapping_unmatched', 0)} "
            f"mapping_version={load_summary.get('mapping_version', '')} "
            f"cliente_id={load_summary.get('id_cliente', '')} "
            f"workflow_id={load_summary.get('id_workflow', '')}"
        )

        if options["sync"] and not dry_run:
            from django.core.management import call_command

            call_command("sync_qualidade_intranet", force=False)
            self.stdout.write(self.style.SUCCESS("Sync qualidade intranet concluído."))

    def _load_rows(
        self,
        path: Path,
        *,
        profile: str = _PROFILE_AUTO,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        iterator = ws.iter_rows(values_only=True)
        try:
            header_row = next(iterator)
        except StopIteration:
            wb.close()
            return [], {"profile": profile}

        columns = _consolidated_column_map(list(header_row))
        detected_consolidated = _CONSOLIDATED_REQUIRED_FIELDS.issubset(columns)
        selected_profile = profile
        if profile == _PROFILE_AUTO:
            selected_profile = _PROFILE_CLARO_CONFER if detected_consolidated else _PROFILE_EXPORT
        if selected_profile == _PROFILE_CLARO_CONFER and not detected_consolidated:
            missing = sorted(_CONSOLIDATED_REQUIRED_FIELDS - set(columns))
            wb.close()
            raise CommandError(
                "Cabeçalhos insuficientes para o perfil Claro Confer: " + ", ".join(missing)
            )

        if selected_profile == _PROFILE_CLARO_CONFER:
            from apps.auditoria.services.reinspecao_mapping import ReinspecaoMappingResolver

            resolver = ReinspecaoMappingResolver.from_active_mapping()
            rows: list[dict[str, Any]] = []
            summary = Counter()
            errors: list[str] = []
            for row_number, values in enumerate(iterator, start=2):
                if not any(v is not None and str(v).strip() for v in values):
                    continue
                try:
                    row, row_stats = _transform_claro_confer_row(
                        values,
                        columns=columns,
                        row_number=row_number,
                        resolver=resolver,
                    )
                except ValueError as exc:
                    if len(errors) < 20:
                        errors.append(f"linha {row_number}: {exc}")
                    continue
                rows.append(row)
                summary.update(row_stats)
            wb.close()
            if errors:
                raise CommandError(
                    f"Consolidado contém erro(s) de transformação (amostra de {len(errors)}): "
                    + "; ".join(errors)
                )
            return rows, {
                "profile": selected_profile,
                **dict(summary),
                "mapping_version": resolver.mapping_version,
                "mapping_source_hash": resolver.source_hash,
                "id_cliente": _CLARO_CONFER_CLIENT_ID,
                "id_workflow": _CLARO_CONFER_WORKFLOW_ID,
            }

        headers = [_fix_text(h) for h in header_row]
        rows = []
        for values in iterator:
            if not any(v is not None and str(v).strip() for v in values):
                continue
            rows.append(_row_dict(headers, values))
        wb.close()
        return rows, {"profile": selected_profile}

    def _find_existing(
        self,
        key: tuple,
        *,
        source_marker: str = "",
        export_id: str = "",
    ) -> AuditoriaFalhaCadastro | None:
        if source_marker and export_id:
            return AuditoriaFalhaCadastro.objects.filter(
                brflow_parsed__homolog_import_source=source_marker,
                brflow_parsed__homolog_import_row_id=export_id,
            ).first()
        protocolo, tipo_registro, tipo_falha, etapa, origem, motivo = key
        qs = AuditoriaFalhaCadastro.objects.filter(
            protocolo=protocolo,
            tipo_registro=tipo_registro,
            tipo_falha=tipo_falha,
            origem=origem,
        )
        if etapa:
            qs = qs.filter(etapa_falha=etapa)
        if motivo:
            qs = qs.filter(motivo_falha=motivo)
        return qs.first()

    def _purge_seeds(self) -> None:
        prefixes = ("EO-SEED-", "EO-REAL-")
        for prefix in prefixes:
            qs = AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=prefix)
            ids = list(qs.values_list("pk", flat=True))
            if not ids:
                continue
            QualidadeIntranetProjection.objects.filter(source_id__in=ids).delete()
            deleted, _ = qs.delete()
            self.stdout.write(self.style.WARNING(f"Removidos {deleted} registro(s) {prefix}*."))
        QualidadeAuditado.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=prefixes[0],
        ).delete()
        QualidadeAuditado.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=prefixes[1],
        ).delete()
        QualidadeFalha.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=prefixes[0],
        ).delete()
        QualidadeFalha.objects.filter(
            source_file=INTRANET_SOURCE_FILE,
            protocolo__startswith=prefixes[1],
        ).delete()
