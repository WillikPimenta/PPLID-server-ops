"""Migra artefatos legados D-1 para o banco. Dry-run é o padrão."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.replicacao_d1.models import (
    ReplicacaoD1ExecutionEvent,
    ReplicacaoD1Protocolo,
    ReplicacaoD1Run,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.normalization import normalize_protocolo
from apps.replicacao_d1.services.plan_validation import adopt_legacy_run
from apps.replicacao_d1.services.source_batch import has_source_contract, ingest_source_rows
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db

ACCEPTANCE_TOKEN = "REMOVER_ARQUIVOS_MIGRADOS"
SUPPORTED = {".parquet", ".xlsx", ".csv", ".json"}


def _run_id(path: Path) -> str:
    match = re.search(r"(20\d{6}(?:_\d{6})?)", path.stem)
    return match.group(1) if match else ""


def _reference_date(path: Path, frame: pd.DataFrame | None = None) -> date:
    if frame is not None:
        for name in ("Data Referencia D1", "data_referencia_d1"):
            if name in frame.columns and not frame[name].dropna().empty:
                value = str(frame[name].dropna().iloc[0])[:10]
                parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
                if not pd.isna(parsed):
                    return parsed.date()
    rid = _run_id(path)
    if len(rid) >= 8:
        return datetime.strptime(rid[:8], "%Y%m%d").date()
    match = re.search(r"(20\d{6})", path.stem)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def _read_csv(path: Path) -> pd.DataFrame:
    for sep in (";", ","):
        try:
            frame = pd.read_csv(path, sep=sep, encoding="utf-8-sig", dtype="string")
            if len(frame.columns) > 1:
                return frame
        except Exception:
            continue
    raise ValueError("CSV inválido ou sem colunas reconhecidas")


def _source_rows(frame: pd.DataFrame) -> list[dict]:
    return frame.where(pd.notna(frame), None).to_dict("records")


@transaction.atomic
def _import_plan_csv(path: Path, frame: pd.DataFrame) -> str:
    protocol_col = "Protocolo" if "Protocolo" in frame.columns else "protocolo"
    workflow_col = "WorkflowConfig" if "WorkflowConfig" in frame.columns else "Workflow"
    if protocol_col not in frame.columns or workflow_col not in frame.columns:
        raise ValueError("CSV não é um plano detalhado (Protocolo/Workflow ausentes)")
    rid = _run_id(path)
    if not rid:
        raise ValueError("run_id não identificado no nome do CSV")
    data_ref = _reference_date(path, frame)
    run, _ = ReplicacaoD1Run.objects.get_or_create(
        run_id=rid,
        defaults={"data_referencia_d1": data_ref, "status_canonical": ReplicacaoD1Run.STATUS_LEGACY},
    )
    if run.plan_hash:
        return rid
    ReplicacaoD1Protocolo.objects.filter(run=run).delete()
    ReplicacaoD1WorkflowDia.objects.filter(run=run).delete()
    records = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        protocol = str(row.get(protocol_col) or "").strip()
        workflow = str(row.get(workflow_col) or "").strip()
        norm = normalize_protocolo(protocol)
        if not norm or not workflow or norm in seen:
            continue
        seen.add(norm)
        data_analise = pd.to_datetime(row.get("Data da Análise"), dayfirst=True, errors="coerce")
        records.append(ReplicacaoD1Protocolo(
            run=run,
            data_referencia_d1=data_ref,
            protocolo=protocol,
            protocolo_normalizado=norm,
            workflow_config=workflow,
            workflow_d1=str(row.get("Workflow") or workflow),
            data_analise=None if pd.isna(data_analise) else data_analise.to_pydatetime(),
            hora=None if pd.isna(data_analise) else int(data_analise.hour),
            canal_destino=str(row.get("Canal Destino") or ""),
        ))
    ReplicacaoD1Protocolo.objects.bulk_create(records, batch_size=2000)
    counts: dict[str, int] = {}
    for record in records:
        counts[record.workflow_config] = counts.get(record.workflow_config, 0) + 1
    ReplicacaoD1WorkflowDia.objects.bulk_create([
        ReplicacaoD1WorkflowDia(
            run=run,
            data_referencia_d1=data_ref,
            workflow_config=workflow,
            protocolos_planejados=count,
            amostra_efetiva=count,
            protocolos_salvos=count,
        )
        for workflow, count in counts.items()
    ])
    adopt_legacy_run(rid, reason=f"Migração histórica do CSV {path.name}")
    return rid


def _import_state_json(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rid = str(payload.get("run_id") or _run_id(path)).strip()
    run = ReplicacaoD1Run.objects.filter(run_id=rid).first()
    if run is None:
        raise ValueError("Estado JSON sem run correspondente no banco")
    for workflow, state in dict(payload.get("workflows") or {}).items():
        ReplicacaoD1WorkflowDia.objects.filter(run=run, workflow_config=workflow).update(
            status_brflow=str(state.get("status") or "")[:64],
            erro_resumo=str(state.get("motivo") or "")[:255],
        )
    if not ReplicacaoD1ExecutionEvent.objects.filter(
        run=run, phase="migration", payload__artifact_name=path.name
    ).exists():
        ReplicacaoD1ExecutionEvent.objects.create(
            run=run,
            phase="migration",
            status="state_imported",
            message="Estado JSON histórico importado.",
            payload={"artifact_name": path.name},
        )
    return rid


class Command(BaseCommand):
    help = "Migra parquet/Excel/CSV/JSON históricos da Replicação D-1 para o PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument("--root", action="append", required=True, help="Raiz legada; pode ser repetida.")
        parser.add_argument("--apply", action="store_true", help="Aplica a migração; sem esta flag é dry-run.")
        parser.add_argument("--delete-after-acceptance", action="store_true")
        parser.add_argument("--acceptance-token", default="")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        roots = [Path(value).resolve() for value in options["root"]]
        for root in roots:
            if not root.exists() or (root.is_file() and root.suffix.lower() not in SUPPORTED):
                raise CommandError(f"Raiz/arquivo inexistente ou não suportado: {root}")
        allowed_roots = [root if root.is_dir() else root.parent for root in roots]
        apply = bool(options["apply"])
        delete = bool(options["delete_after_acceptance"])
        if delete and (not apply or options["acceptance_token"] != ACCEPTANCE_TOKEN):
            raise CommandError(
                f"Para excluir, use --apply --delete-after-acceptance --acceptance-token {ACCEPTANCE_TOKEN}"
            )

        files = sorted({
            path.resolve()
            for root in roots
            for path in ([root] if root.is_file() else root.rglob("*"))
            if path.is_file() and path.suffix.lower() in SUPPORTED
        })
        report = {"mode": "apply" if apply else "dry-run", "files_found": len(files), "migrated": [], "skipped": [], "errors": [], "deleted": []}
        migrated_paths: list[Path] = []
        for path in files:
            try:
                suffix = path.suffix.lower()
                kind = "unknown"
                rows = None
                if suffix == ".parquet":
                    if apply:
                        frame = pd.read_parquet(path)
                        columns = set(frame.columns)
                        rows = len(frame)
                    else:
                        # O dry-run consulta apenas metadados. Isso evita hidratar e
                        # carregar parquets grandes do OneDrive sem necessidade.
                        import pyarrow.parquet as parquet

                        parquet_file = parquet.ParquetFile(path)
                        columns = set(parquet_file.schema.names)
                        rows = parquet_file.metadata.num_rows
                        frame = None
                    if has_source_contract(columns):
                        kind = "source"
                        if apply and frame is not None:
                            ingest_source_rows(_reference_date(path, frame), _source_rows(frame))
                    else:
                        raise ValueError("parquet fora do contrato da fonte D-1")
                elif suffix == ".xlsx":
                    kind = "plan_excel"
                    if apply:
                        ok, _source, rows, rid, rejected = sync_replicacao_d1_to_db(path=path)
                        if not ok:
                            raise ValueError(f"leitura rejeitada ({rejected} linha(s))")
                        adopt_legacy_run(rid, reason=f"Migração histórica do Excel {path.name}")
                elif suffix == ".csv":
                    frame = _read_csv(path)
                    if has_source_contract(frame.columns) and "plano" not in path.name.casefold():
                        kind, rows = "source", len(frame)
                        if apply:
                            ingest_source_rows(_reference_date(path, frame), _source_rows(frame))
                    else:
                        kind, rows = "plan_csv", len(frame)
                        if apply:
                            _import_plan_csv(path, frame)
                else:
                    kind = "state_json"
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    rows = len(dict(payload.get("workflows") or {}))
                    if apply:
                        _import_state_json(path)
                report["migrated"].append({"name": path.name, "kind": kind, "rows": rows})
                if apply:
                    migrated_paths.append(path)
            except Exception as exc:
                report["errors"].append({"name": path.name, "error": str(exc)[:300]})

        if delete and not report["errors"]:
            for path in migrated_paths:
                if not any(path.is_relative_to(root) for root in allowed_roots):
                    raise CommandError(f"Destino de exclusão fora das raízes aprovadas: {path}")
                path.unlink()
                report["deleted"].append(path.name)
        elif delete and report["errors"]:
            report["skipped"].append("Exclusão cancelada porque houve falhas de migração.")

        if options["json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            self.stdout.write(f"Migração D-1 [{report['mode']}] encontrados={report['files_found']} migrados={len(report['migrated'])} erros={len(report['errors'])} excluídos={len(report['deleted'])}")
            for error in report["errors"]:
                self.stderr.write(f"  ERRO {error['name']}: {error['error']}")
