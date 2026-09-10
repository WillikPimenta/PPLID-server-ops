"""Persistência sync/replace do workbook Identificação dos Processos."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.db import transaction

from apps.dimensoes_processos.models import (
    DimCliente,
    DimEtapa,
    DimGrupoServico,
    DimNivelHierarquico,
    DimProduto,
    DimServico,
    DimWorkflow,
    MetaEtapa,
    ProjecaoEquipe,
    ProjecaoSla,
)
from apps.dimensoes_processos.services.identificacao_processos.import_scope import (
    ImportScope,
    filter_workbook_for_scope,
    normalize_import_scope,
)
from apps.dimensoes_processos.services.identificacao_processos.reader import WorkbookData
from apps.dimensoes_processos.services.vigencia import find_vigente_sla, rotacionar_sla_ciclo

CHUNK = 2000


@dataclass
class EntityStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"created": self.created, "updated": self.updated, "skipped": self.skipped}


@dataclass
class ImportResult:
    mode: str
    import_scope: str = "full"
    entities: dict[str, EntityStats] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    min_projecao_sla_date: date | None = None
    min_meta_etapa_date: date | None = None
    sla_rotated: int = 0

    def as_summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "import_scope": self.import_scope,
            "entities": {key: stats.as_dict() for key, stats in self.entities.items()},
            "warnings": self.warnings,
            "min_projecao_sla_date": (
                self.min_projecao_sla_date.isoformat() if self.min_projecao_sla_date else None
            ),
            "min_meta_etapa_date": (
                self.min_meta_etapa_date.isoformat() if self.min_meta_etapa_date else None
            ),
            "sla_rotated": self.sla_rotated,
        }


def _stats_key(name: str, result: ImportResult) -> EntityStats:
    if name not in result.entities:
        result.entities[name] = EntityStats()
    return result.entities[name]


def _clear_all() -> None:
    ProjecaoSla.objects.all().delete()
    MetaEtapa.objects.all().delete()
    ProjecaoEquipe.objects.all().delete()
    DimEtapa.objects.all().delete()
    DimWorkflow.objects.all().delete()
    DimNivelHierarquico.objects.all().delete()
    DimServico.objects.all().delete()
    DimGrupoServico.objects.all().delete()
    DimCliente.objects.all().delete()
    DimProduto.objects.all().delete()


def _sync_produtos(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("produto", result)
    for row in data.produtos:
        _, created = DimProduto.objects.update_or_create(
            id_produto=row["id_produto"],
            defaults={"tipo_produto": row["tipo_produto"]},
        )
        stats.created += int(created)
        stats.updated += int(not created)


def _sync_grupos_servico(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("grupo_servico", result)
    for row in data.grupos_servico:
        _, created = DimGrupoServico.objects.update_or_create(
            id_grupo=row["id_grupo"],
            defaults={"nome": row["nome"]},
        )
        stats.created += int(created)
        stats.updated += int(not created)


def _sync_servicos(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("servico", result)
    grupo_ids = set(DimGrupoServico.objects.values_list("pk", flat=True))
    for row in data.servicos:
        gid = row.get("id_grupo_servico")
        _, created = DimServico.objects.update_or_create(
            id_servico=row["id_servico"],
            defaults={
                "nome": row["nome"],
                "meta_dia": row["meta_dia"],
                "grupo_id": gid if gid in grupo_ids else None,
            },
        )
        stats.created += int(created)
        stats.updated += int(not created)


def _sync_clientes(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("clientes", result)
    for row in data.clientes:
        _, created = DimCliente.objects.update_or_create(
            id_cliente=row["id_cliente"],
            defaults={
                "nome": row["nome"],
                "operations": row["operations"],
                "id_classificacao": row["id_classificacao"],
            },
        )
        stats.created += int(created)
        stats.updated += int(not created)


def _sync_niveis(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("nivel_hierarquico", result)
    for row in data.niveis_hierarquicos:
        _, created = DimNivelHierarquico.objects.update_or_create(
            id_nh=row["id_nh"],
            defaults={"nome": row["nome"], "ind_considerar": row["ind_considerar"]},
        )
        stats.created += int(created)
        stats.updated += int(not created)


def _sync_workflows(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("workflow", result)
    produto_ids = set(DimProduto.objects.values_list("pk", flat=True))
    for row in data.workflows:
        pid = row.get("id_produto")
        _, created = DimWorkflow.objects.update_or_create(
            id_workflow=row["id_workflow"],
            defaults={
                "nome": row["nome"],
                "ind_considerar": row["ind_considerar"],
                "produto_id": pid if pid in produto_ids else None,
                "tipo_atendimento": row["tipo_atendimento"],
            },
        )
        stats.created += int(created)
        stats.updated += int(not created)


def _sync_etapas(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("etapas", result)
    for row in data.etapas:
        _, created = DimEtapa.objects.update_or_create(
            id_etapa=row["id_etapa"],
            defaults={"nome": row["nome"], "manual": row["manual"]},
        )
        stats.created += int(created)
        stats.updated += int(not created)


def _sync_metas_etapa(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("metas_etapa", result)
    min_date: date | None = result.min_meta_etapa_date
    etapa_ids = set(DimEtapa.objects.values_list("pk", flat=True))
    servico_ids = set(DimServico.objects.values_list("pk", flat=True))
    for row in data.metas_etapa:
        eid = row["id_etapa"]
        if eid not in etapa_ids:
            stats.skipped += 1
            continue
        sid = row.get("id_servico")
        existing = MetaEtapa.objects.filter(
            etapa_id=eid,
            servico_id=sid if sid in servico_ids else None,
            data_inicio=row["data_inicio"],
        ).first()
        defaults = {
            "data_fim": row["data_fim"],
            "meta_dia": row["meta_dia"],
            "servico_id": sid if sid in servico_ids else None,
        }
        if existing:
            changed = False
            for field_name, value in defaults.items():
                if getattr(existing, field_name) != value:
                    setattr(existing, field_name, value)
                    changed = True
            if changed:
                existing.save(update_fields=list(defaults.keys()))
                stats.updated += 1
                di = row["data_inicio"]
                min_date = di if min_date is None else min(min_date, di)
            else:
                stats.skipped += 1
        else:
            MetaEtapa.objects.create(etapa_id=eid, data_inicio=row["data_inicio"], **defaults)
            stats.created += 1
            di = row["data_inicio"]
            min_date = di if min_date is None else min(min_date, di)

    result.min_meta_etapa_date = min_date


def _sync_projecao_sla(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("projecao_sla", result)
    cliente_ids = set(DimCliente.objects.values_list("pk", flat=True))
    workflow_ids = set(DimWorkflow.objects.values_list("pk", flat=True))
    nh_ids = set(DimNivelHierarquico.objects.values_list("pk", flat=True))
    min_date: date | None = result.min_projecao_sla_date

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

    update_fields = [
        "data_fim",
        "hora_inicio",
        "hora_fim",
        "duracao_atendimento",
        "sla_segundos",
        "flag_ajuste_sla",
        "sla_ajuste",
        "volume",
    ]
    to_create: list[ProjecaoSla] = []
    to_update: list[ProjecaoSla] = []
    rotated_ids: set[int] = set()

    def _track_min(di: date) -> None:
        nonlocal min_date
        min_date = di if min_date is None else min(min_date, di)

    for row in data.projecao_sla:
        cid = row["id_cliente"]
        wid = row["id_workflow"]
        nid = row["id_nivel_hierarquico"]
        if cid not in cliente_ids or wid not in workflow_ids or nid not in nh_ids:
            stats.skipped += 1
            if len(result.warnings) < 50:
                result.warnings.append(
                    f"Projeção SLA ignorada: cliente={cid} workflow={wid} nh={nid} (FK ausente)"
                )
            continue

        defaults = {
            "data_fim": row["data_fim"],
            "hora_inicio": row["hora_inicio"],
            "hora_fim": row["hora_fim"],
            "duracao_atendimento": row["duracao_atendimento"],
            "sla_segundos": row["sla_segundos"],
            "flag_ajuste_sla": row["flag_ajuste_sla"],
            "sla_ajuste": row["sla_ajuste"],
            "volume": row["volume"],
        }
        dias = str(row["dias_semana"] or "").strip()
        key = (cid, wid, nid, dias, row["data_inicio"])
        existing = existing_map.get(key)

        if existing:
            changed = False
            for field_name, value in defaults.items():
                if getattr(existing, field_name) != value:
                    setattr(existing, field_name, value)
                    changed = True
            if changed:
                to_update.append(existing)
                _track_min(row["data_inicio"])
            else:
                stats.skipped += 1
            continue

        vigente = find_vigente_sla(
            cliente_id=cid,
            workflow_id=wid,
            nivel_hierarquico_id=nid,
            dias_semana=dias,
        )
        if vigente and row["data_inicio"] > vigente.data_inicio and vigente.pk not in rotated_ids:
            _, novo = rotacionar_sla_ciclo(
                vigente,
                data_inicio_novo=row["data_inicio"],
                hora_inicio=defaults["hora_inicio"],
                hora_fim=defaults["hora_fim"],
                duracao_atendimento=defaults["duracao_atendimento"],
                sla_segundos=defaults["sla_segundos"],
                flag_ajuste_sla=defaults["flag_ajuste_sla"],
                sla_ajuste=defaults["sla_ajuste"],
                volume=defaults["volume"],
            )
            if defaults["data_fim"] is not None:
                novo.data_fim = defaults["data_fim"]
                novo.save(update_fields=["data_fim"])
            existing_map[key] = novo
            rotated_ids.add(vigente.pk)
            result.sla_rotated += 1
            stats.updated += 1
            _track_min(row["data_inicio"])
            continue

        to_create.append(
            ProjecaoSla(
                cliente_id=cid,
                workflow_id=wid,
                nivel_hierarquico_id=nid,
                dias_semana=dias,
                data_inicio=row["data_inicio"],
                **defaults,
            )
        )
        _track_min(row["data_inicio"])

    if to_create:
        ProjecaoSla.objects.bulk_create(to_create, batch_size=CHUNK)
        stats.created += len(to_create)
    if to_update:
        ProjecaoSla.objects.bulk_update(to_update, update_fields, batch_size=CHUNK)
        stats.updated += len(to_update)

    result.min_projecao_sla_date = min_date


def _sync_projecao_equipes(data: WorkbookData, result: ImportResult) -> None:
    stats = _stats_key("projecao_equipe", result)
    field_names = [
        "data_final",
        "nome_agente",
        "email_agente",
        "atividade",
        "matricula_lider",
        "nome_lider",
        "email_lider",
        "equipe",
        "matricula_facilitador",
        "id_operations",
        "horario",
        "turno",
        "localidade",
        "data_admissao",
        "funcao",
        "setor",
        "pcd",
        "desconto_meta",
        "matricula_oracle",
        "matricula_ponto",
        "observacao",
        "matricula_supervisor",
        "supervisor",
        "status",
        "banda",
        "powerapps_id",
    ]
    for row in data.projecao_equipes:
        existing = ProjecaoEquipe.objects.filter(
            matricula_agente=row["matricula_agente"],
            data_inicial=row["data_inicial"],
        ).first()
        defaults = {name: row.get(name) for name in field_names}
        if existing:
            changed = False
            for field_name, value in defaults.items():
                if getattr(existing, field_name) != value:
                    setattr(existing, field_name, value)
                    changed = True
            if changed:
                existing.save(update_fields=field_names)
                stats.updated += 1
            else:
                stats.skipped += 1
        else:
            ProjecaoEquipe.objects.create(
                matricula_agente=row["matricula_agente"],
                data_inicial=row["data_inicial"],
                **defaults,
            )
            stats.created += 1


def _bulk_replace(data: WorkbookData, result: ImportResult) -> None:
    _clear_all()

    if data.produtos:
        DimProduto.objects.bulk_create(
            [DimProduto(**row) for row in data.produtos],
            batch_size=CHUNK,
        )
        _stats_key("produto", result).created = len(data.produtos)

    if data.grupos_servico:
        DimGrupoServico.objects.bulk_create(
            [DimGrupoServico(id_grupo=r["id_grupo"], nome=r["nome"]) for r in data.grupos_servico],
            batch_size=CHUNK,
        )
        _stats_key("grupo_servico", result).created = len(data.grupos_servico)

    grupo_ids = set(DimGrupoServico.objects.values_list("pk", flat=True))
    if data.servicos:
        batch = [
            DimServico(
                id_servico=r["id_servico"],
                nome=r["nome"],
                meta_dia=r["meta_dia"],
                grupo_id=r["id_grupo_servico"] if r.get("id_grupo_servico") in grupo_ids else None,
            )
            for r in data.servicos
        ]
        DimServico.objects.bulk_create(batch, batch_size=CHUNK)
        _stats_key("servico", result).created = len(batch)

    if data.clientes:
        DimCliente.objects.bulk_create(
            [DimCliente(**row) for row in data.clientes],
            batch_size=CHUNK,
        )
        _stats_key("clientes", result).created = len(data.clientes)

    if data.niveis_hierarquicos:
        DimNivelHierarquico.objects.bulk_create(
            [DimNivelHierarquico(**row) for row in data.niveis_hierarquicos],
            batch_size=CHUNK,
        )
        _stats_key("nivel_hierarquico", result).created = len(data.niveis_hierarquicos)

    produto_ids = set(DimProduto.objects.values_list("pk", flat=True))
    if data.workflows:
        batch = [
            DimWorkflow(
                id_workflow=r["id_workflow"],
                nome=r["nome"],
                ind_considerar=r["ind_considerar"],
                produto_id=r["id_produto"] if r.get("id_produto") in produto_ids else None,
                tipo_atendimento=r["tipo_atendimento"],
            )
            for r in data.workflows
        ]
        DimWorkflow.objects.bulk_create(batch, batch_size=CHUNK)
        _stats_key("workflow", result).created = len(batch)

    if data.etapas:
        DimEtapa.objects.bulk_create(
            [DimEtapa(**row) for row in data.etapas],
            batch_size=CHUNK,
        )
        _stats_key("etapas", result).created = len(data.etapas)

    etapa_ids = set(DimEtapa.objects.values_list("pk", flat=True))
    servico_ids = set(DimServico.objects.values_list("pk", flat=True))
    if data.metas_etapa:
        batch = []
        for row in data.metas_etapa:
            if row["id_etapa"] not in etapa_ids:
                continue
            sid = row.get("id_servico")
            batch.append(
                MetaEtapa(
                    data_inicio=row["data_inicio"],
                    data_fim=row["data_fim"],
                    etapa_id=row["id_etapa"],
                    meta_dia=row["meta_dia"],
                    servico_id=sid if sid in servico_ids else None,
                )
            )
        if batch:
            MetaEtapa.objects.bulk_create(batch, batch_size=CHUNK)
        _stats_key("metas_etapa", result).created = len(batch)

    cliente_ids = set(DimCliente.objects.values_list("pk", flat=True))
    workflow_ids = set(DimWorkflow.objects.values_list("pk", flat=True))
    nh_ids = set(DimNivelHierarquico.objects.values_list("pk", flat=True))
    min_date: date | None = None
    if data.projecao_sla:
        batch = []
        for row in data.projecao_sla:
            cid, wid, nid = row["id_cliente"], row["id_workflow"], row["id_nivel_hierarquico"]
            if cid not in cliente_ids or wid not in workflow_ids or nid not in nh_ids:
                continue
            batch.append(
                ProjecaoSla(
                    cliente_id=cid,
                    data_inicio=row["data_inicio"],
                    data_fim=row["data_fim"],
                    workflow_id=wid,
                    nivel_hierarquico_id=nid,
                    dias_semana=row["dias_semana"],
                    hora_inicio=row["hora_inicio"],
                    hora_fim=row["hora_fim"],
                    duracao_atendimento=row["duracao_atendimento"],
                    sla_segundos=row["sla_segundos"],
                    flag_ajuste_sla=row["flag_ajuste_sla"],
                    sla_ajuste=row["sla_ajuste"],
                    volume=row["volume"],
                )
            )
            di = row["data_inicio"]
            min_date = di if min_date is None else min(min_date, di)
        if batch:
            ProjecaoSla.objects.bulk_create(batch, batch_size=CHUNK)
        _stats_key("projecao_sla", result).created = len(batch)
        result.min_projecao_sla_date = min_date

    if data.projecao_equipes:
        batch = [
            ProjecaoEquipe(
                matricula_agente=r["matricula_agente"],
                data_inicial=r["data_inicial"],
                data_final=r.get("data_final"),
                nome_agente=r.get("nome_agente", ""),
                email_agente=r.get("email_agente", ""),
                atividade=r.get("atividade", ""),
                matricula_lider=r.get("matricula_lider", ""),
                nome_lider=r.get("nome_lider", ""),
                email_lider=r.get("email_lider", ""),
                equipe=r.get("equipe", ""),
                matricula_facilitador=r.get("matricula_facilitador", ""),
                id_operations=r.get("id_operations"),
                horario=r.get("horario", ""),
                turno=r.get("turno", ""),
                localidade=r.get("localidade", ""),
                data_admissao=r.get("data_admissao"),
                funcao=r.get("funcao", ""),
                setor=r.get("setor", ""),
                pcd=r.get("pcd", ""),
                desconto_meta=r.get("desconto_meta", ""),
                matricula_oracle=r.get("matricula_oracle", ""),
                matricula_ponto=r.get("matricula_ponto", ""),
                observacao=r.get("observacao", ""),
                matricula_supervisor=r.get("matricula_supervisor", ""),
                supervisor=r.get("supervisor", ""),
                status=r.get("status", ""),
                banda=r.get("banda", ""),
                powerapps_id=r.get("powerapps_id", ""),
            )
            for r in data.projecao_equipes
        ]
        ProjecaoEquipe.objects.bulk_create(batch, batch_size=CHUNK)
        _stats_key("projecao_equipe", result).created = len(batch)


def persist_workbook_data(
    data: WorkbookData,
    *,
    mode: str = "sync",
    import_scope: ImportScope | str = "full",
) -> ImportResult:
    scope = normalize_import_scope(import_scope)
    scoped_data = filter_workbook_for_scope(data, scope)
    result = ImportResult(mode=mode, import_scope=scope)
    with transaction.atomic():
        if mode == "replace":
            if scope != "full":
                raise ValueError("mode=replace exige import_scope=full.")
            _bulk_replace(scoped_data, result)
        elif mode == "sync":
            if scoped_data.produtos:
                _sync_produtos(scoped_data, result)
            if scoped_data.grupos_servico:
                _sync_grupos_servico(scoped_data, result)
            if scoped_data.servicos:
                _sync_servicos(scoped_data, result)
            if scoped_data.clientes:
                _sync_clientes(scoped_data, result)
            if scoped_data.niveis_hierarquicos:
                _sync_niveis(scoped_data, result)
            if scoped_data.workflows:
                _sync_workflows(scoped_data, result)
            if scoped_data.etapas:
                _sync_etapas(scoped_data, result)
            if scoped_data.metas_etapa:
                _sync_metas_etapa(scoped_data, result)
            if scoped_data.projecao_sla:
                _sync_projecao_sla(scoped_data, result)
            if scoped_data.projecao_equipes:
                _sync_projecao_equipes(scoped_data, result)
        else:
            raise ValueError(f"Modo de import inválido: {mode}")
    return result
