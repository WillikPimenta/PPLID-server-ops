"""Preflight e diff do workbook Identificação dos Processos."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.utils import timezone

from apps.dimensoes_processos.models import (
    DimCliente,
    DimEtapa,
    DimNivelHierarquico,
    DimProduto,
    DimWorkflow,
    MetaEtapa,
    ProjecaoSla,
)
from apps.dimensoes_processos.services.identificacao_processos.cleanup_vigencia import (
    list_duplicate_vigente_groups,
)
from apps.dimensoes_processos.services.identificacao_processos.import_scope import (
    ImportScope,
    REQUIRED_SHEETS_BY_SCOPE,
    SCOPE_LABELS,
    filter_workbook_for_scope,
    normalize_import_scope,
)
from apps.dimensoes_processos.services.identificacao_processos.reader import (
    WorkbookData,
    find_sheet,
    load_workbook_data,
    _sheet_names,
)
from apps.dimensoes_processos.services.vigencia import find_vigente_sla


@dataclass
class PreflightResult:
    ok: bool
    import_scope: str = "full"
    file_name: str = ""
    sheet_names: list[str] = field(default_factory=list)
    missing_sheets: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    clientes: dict[str, int] = field(default_factory=dict)
    workflows: dict[str, int] = field(default_factory=dict)
    projecao_sla: dict[str, int] = field(default_factory=dict)
    metas_etapa: dict[str, int] = field(default_factory=dict)
    etapas: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sample_volume_changes: list[dict[str, Any]] = field(default_factory=list)
    capacity_preview: dict[str, Any] = field(default_factory=dict)
    duplicate_vigente_risk: list[dict[str, Any]] = field(default_factory=list)
    invalidates_capacity_snapshots: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "import_scope": self.import_scope,
            "import_scope_label": SCOPE_LABELS.get(self.import_scope, self.import_scope),
            "file_name": self.file_name,
            "sheet_names": self.sheet_names,
            "missing_sheets": self.missing_sheets,
            "row_counts": self.row_counts,
            "clientes": self.clientes,
            "workflows": self.workflows,
            "projecao_sla": self.projecao_sla,
            "metas_etapa": self.metas_etapa,
            "etapas": self.etapas,
            "warnings": self.warnings,
            "errors": self.errors,
            "sample_volume_changes": self.sample_volume_changes,
            "capacity_preview": self.capacity_preview,
            "duplicate_vigente_risk": self.duplicate_vigente_risk,
            "invalidates_capacity_snapshots": self.invalidates_capacity_snapshots,
        }


def _validate_sheets(sheet_names: list[str], scope: ImportScope) -> tuple[list[str], list[str]]:
    names = _sheet_names(type("WB", (), {"sheetnames": sheet_names})())
    missing: list[str] = []
    errors: list[str] = []
    for required in REQUIRED_SHEETS_BY_SCOPE[scope]:
        if find_sheet(names, required, required=False) is None:
            missing.append(required)
    if missing:
        errors.append(f"Abas obrigatórias ausentes para escopo {scope}: {', '.join(missing)}")
    return missing, errors


def _diff_clientes(rows: list[dict[str, Any]]) -> dict[str, int]:
    existing = {c.id_cliente: c for c in DimCliente.objects.all()}
    created = updated = unchanged = 0
    for row in rows:
        cid = row["id_cliente"]
        current = existing.get(cid)
        if current is None:
            created += 1
        elif (
            current.nome != row["nome"]
            or current.operations != row["operations"]
            or current.id_classificacao != row["id_classificacao"]
        ):
            updated += 1
        else:
            unchanged += 1
    return {"new": created, "updated": updated, "unchanged": unchanged, "total_in_file": len(rows)}


def _diff_workflows(rows: list[dict[str, Any]]) -> dict[str, int]:
    existing = {w.id_workflow: w for w in DimWorkflow.objects.select_related("produto").all()}
    produto_ids = set(DimProduto.objects.values_list("pk", flat=True))
    created = updated = unchanged = 0
    for row in rows:
        wid = row["id_workflow"]
        pid = row.get("id_produto")
        produto_id = pid if pid in produto_ids else None
        current = existing.get(wid)
        if current is None:
            created += 1
        elif (
            current.nome != row["nome"]
            or current.ind_considerar != row["ind_considerar"]
            or current.produto_id != produto_id
            or current.tipo_atendimento != row["tipo_atendimento"]
        ):
            updated += 1
        else:
            unchanged += 1
    return {"new": created, "updated": updated, "unchanged": unchanged, "total_in_file": len(rows)}


def _diff_etapas(rows: list[dict[str, Any]]) -> dict[str, int]:
    existing = {item.id_etapa: item for item in DimEtapa.objects.all()}
    created = updated = unchanged = 0
    for row in rows:
        eid = row["id_etapa"]
        current = existing.get(eid)
        if current is None:
            created += 1
        elif current.nome != row["nome"] or current.manual != row["manual"]:
            updated += 1
        else:
            unchanged += 1
    return {"new": created, "updated": updated, "unchanged": unchanged, "total_in_file": len(rows)}


def _diff_metas_etapa(rows: list[dict[str, Any]]) -> dict[str, int]:
    etapa_ids = set(DimEtapa.objects.values_list("pk", flat=True))
    created = updated = unchanged = skipped = 0
    for row in rows:
        if row["id_etapa"] not in etapa_ids:
            skipped += 1
            continue
        existing = MetaEtapa.objects.filter(
            etapa_id=row["id_etapa"],
            servico_id=row.get("id_servico"),
            data_inicio=row["data_inicio"],
        ).first()
        if existing is None:
            created += 1
        elif existing.meta_dia != row["meta_dia"] or existing.data_fim != row.get("data_fim"):
            updated += 1
        else:
            unchanged += 1
    return {
        "new": created,
        "updated": updated,
        "unchanged": unchanged,
        "skipped_fk": skipped,
        "total_in_file": len(rows),
    }


def _sla_lookup_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["id_cliente"],
        row["id_workflow"],
        row["id_nivel_hierarquico"],
        str(row["dias_semana"] or "").strip(),
        row["data_inicio"],
    )


def _detect_duplicate_vigente_risk(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for row in rows:
        cid, wid, nid = row["id_cliente"], row["id_workflow"], row["id_nivel_hierarquico"]
        dias = str(row["dias_semana"] or "").strip()
        vigente = find_vigente_sla(
            cliente_id=cid,
            workflow_id=wid,
            nivel_hierarquico_id=nid,
            dias_semana=dias,
        )
        if vigente and vigente.data_inicio != row["data_inicio"] and row["data_inicio"] > vigente.data_inicio:
            risks.append(
                {
                    "id_cliente": cid,
                    "id_workflow": wid,
                    "id_nivel_hierarquico": nid,
                    "dias_semana": dias,
                    "vigente_data_inicio": vigente.data_inicio.isoformat(),
                    "xlsx_data_inicio": row["data_inicio"].isoformat(),
                    "action": "rotacionar_sla_ciclo",
                }
            )
    return risks[:50]


def _diff_projecao_sla(
    rows: list[dict[str, Any]],
    *,
    max_samples: int = 20,
) -> tuple[dict[str, int], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    cliente_ids = set(DimCliente.objects.values_list("pk", flat=True))
    workflow_ids = set(DimWorkflow.objects.values_list("pk", flat=True))
    nh_ids = set(DimNivelHierarquico.objects.values_list("pk", flat=True))

    existing_map: dict[tuple[Any, ...], ProjecaoSla] = {}
    for sla in ProjecaoSla.objects.all().iterator():
        key = (
            sla.cliente_id,
            sla.workflow_id,
            sla.nivel_hierarquico_id,
            str(sla.dias_semana or "").strip(),
            sla.data_inicio,
        )
        existing_map[key] = sla

    created = updated = unchanged = skipped = 0
    warnings: list[str] = []
    samples: list[dict[str, Any]] = []

    for row in rows:
        cid, wid, nid = row["id_cliente"], row["id_workflow"], row["id_nivel_hierarquico"]
        if cid not in cliente_ids or wid not in workflow_ids or nid not in nh_ids:
            skipped += 1
            if len(warnings) < 50:
                warnings.append(
                    f"Projeção SLA ignorada: cliente={cid} workflow={wid} nh={nid} (FK ausente no portal)"
                )
            continue

        key = _sla_lookup_key(row)
        current = existing_map.get(key)
        if current is None:
            created += 1
            if row.get("volume") is not None and len(samples) < max_samples:
                samples.append(
                    {
                        "id_cliente": cid,
                        "id_workflow": wid,
                        "data_inicio": row["data_inicio"].isoformat(),
                        "volume_before": None,
                        "volume_after": row.get("volume"),
                        "change": "new",
                    }
                )
        else:
            fields = (
                "data_fim",
                "hora_inicio",
                "hora_fim",
                "duracao_atendimento",
                "sla_segundos",
                "flag_ajuste_sla",
                "sla_ajuste",
                "volume",
            )
            changed = any(getattr(current, field_name) != row.get(field_name) for field_name in fields)
            if changed:
                updated += 1
                if current.volume != row.get("volume") and len(samples) < max_samples:
                    samples.append(
                        {
                            "id_cliente": cid,
                            "id_workflow": wid,
                            "data_inicio": row["data_inicio"].isoformat(),
                            "volume_before": current.volume,
                            "volume_after": row.get("volume"),
                            "change": "volume",
                        }
                    )
            else:
                unchanged += 1

    stats = {
        "new": created,
        "updated": updated,
        "unchanged": unchanged,
        "skipped_fk": skipped,
        "total_in_file": len(rows),
    }
    duplicate_risk = _detect_duplicate_vigente_risk(rows)
    return stats, warnings, samples, duplicate_risk


def _capacity_contractual_preview(rows: list[dict[str, Any]], *, top_n: int = 20) -> dict[str, Any]:
    xlsx_by_wf: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        if row.get("volume") is None:
            continue
        key = (int(row["id_cliente"]), int(row["id_workflow"]))
        xlsx_by_wf[key] += Decimal(str(row["volume"]))

    on_date = timezone.localdate()
    db_rows = ProjecaoSla.objects.filter(data_inicio__lte=on_date).filter(
        models_q_fim(on_date),
    )
    db_by_wf: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    for sla in db_rows.iterator():
        if sla.volume is None:
            continue
        db_by_wf[(sla.cliente_id, sla.workflow_id)] += Decimal(str(sla.volume))

    preview: list[dict[str, Any]] = []
    for key, xlsx_volume in sorted(xlsx_by_wf.items(), key=lambda item: item[1], reverse=True)[:top_n]:
        db_volume = db_by_wf.get(key, Decimal("0"))
        preview.append(
            {
                "id_cliente": key[0],
                "id_workflow": key[1],
                "xlsx_volume": str(xlsx_volume.quantize(Decimal("0.01"))),
                "db_volume": str(db_volume.quantize(Decimal("0.01"))),
                "delta": str((xlsx_volume - db_volume).quantize(Decimal("0.01"))),
            }
        )
    high_contractual = [item for item in preview if Decimal(item["xlsx_volume"]) > Decimal(item["db_volume"]) * 2]
    return {"top_workflows": preview, "capacity_contractual_high_count": len(high_contractual)}


def models_q_fim(on_date):
    from django.db.models import Q

    return Q(data_fim__isnull=True) | Q(data_fim__gte=on_date)


def _apply_preflight_data(result: PreflightResult, data: WorkbookData, *, scope: ImportScope) -> None:
    scoped = filter_workbook_for_scope(data, scope)
    missing, errors = _validate_sheets(data.sheet_names, scope)
    result.missing_sheets = missing
    result.errors.extend(errors)
    result.row_counts = {
        "produtos": len(scoped.produtos),
        "clientes": len(scoped.clientes),
        "workflows": len(scoped.workflows),
        "niveis_hierarquicos": len(scoped.niveis_hierarquicos),
        "etapas": len(scoped.etapas),
        "metas_etapa": len(scoped.metas_etapa),
        "projecao_sla": len(scoped.projecao_sla),
        "projecao_equipes": len(scoped.projecao_equipes),
    }
    if scoped.clientes:
        result.clientes = _diff_clientes(scoped.clientes)
    if scoped.workflows:
        result.workflows = _diff_workflows(scoped.workflows)
    if scoped.etapas:
        result.etapas = _diff_etapas(scoped.etapas)
    if scoped.metas_etapa:
        result.metas_etapa = _diff_metas_etapa(scoped.metas_etapa)
    if scoped.projecao_sla:
        sla_stats, sla_warnings, samples, duplicate_risk = _diff_projecao_sla(scoped.projecao_sla)
        result.projecao_sla = sla_stats
        result.warnings.extend(sla_warnings)
        result.sample_volume_changes = samples
        result.duplicate_vigente_risk = duplicate_risk
        result.capacity_preview = _capacity_contractual_preview(scoped.projecao_sla)
        if duplicate_risk:
            result.warnings.append(
                f"duplicate_vigente: {len(duplicate_risk)} linha(s) SLA rotacionarão ciclo vigente."
            )
        if result.capacity_preview.get("capacity_contractual_high_count"):
            result.warnings.append(
                "capacity_contractual_high: volumes contratuais no xlsx acima do dobro do cadastrado "
                f"em {result.capacity_preview['capacity_contractual_high_count']} workflow(s) (amostra top 20)."
            )
        result.invalidates_capacity_snapshots = bool(
            sla_stats.get("new") or sla_stats.get("updated")
        )

    dup_groups = list_duplicate_vigente_groups()
    if dup_groups["total_groups"]:
        result.warnings.append(
            f"duplicate_vigente_db: {dup_groups['total_groups']} grupo(s) com ciclos vigentes duplicados no portal."
        )

    if scope == "full" and (scoped.metas_etapa or scoped.etapas):
        meta_stats = result.metas_etapa
        if meta_stats.get("new") or meta_stats.get("updated"):
            result.invalidates_capacity_snapshots = True
            result.warnings.append(
                "Import completo alterará metas/etapas — snapshots Capacity serão invalidados."
            )

    if result.errors:
        result.ok = False


def run_preflight(
    path: Path | str,
    *,
    file_name: str = "",
    import_scope: ImportScope | str = "full",
) -> PreflightResult:
    scope = normalize_import_scope(import_scope)
    path = Path(path)
    data = load_workbook_data(path)
    result = PreflightResult(
        ok=True,
        import_scope=scope,
        file_name=file_name or path.name,
        sheet_names=data.sheet_names,
    )
    _apply_preflight_data(result, data, scope=scope)
    return result


def run_preflight_from_data(
    data: WorkbookData,
    *,
    file_name: str = "",
    import_scope: ImportScope | str = "full",
) -> PreflightResult:
    scope = normalize_import_scope(import_scope)
    result = PreflightResult(ok=True, import_scope=scope, file_name=file_name, sheet_names=data.sheet_names)
    _apply_preflight_data(result, data, scope=scope)
    return result
