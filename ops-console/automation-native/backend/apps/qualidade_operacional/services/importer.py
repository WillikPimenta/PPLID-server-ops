# -*- coding: utf-8 -*-
"""Importação chunked dos TSV de Qualidade Operacional."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from django.db import transaction
from django.utils import timezone

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.falha_import_dedupe import prepare_falhas_for_import
from apps.qualidade_operacional.services.case_key import build_case_key_str
from apps.qualidade_operacional.services.filter_catalog import refresh_filter_catalog
from apps.qualidade_operacional.services.localidade_documento import (
    normalize_localidade_documento,
)
from apps.qualidade_operacional.services.normalize import (
    clean_text,
    is_processual_tipificacao,
    normalize_matricula,
    normalize_tipo_conclusao,
    parse_date_br,
    parse_int,
)

BULK_CHUNK_SIZE = 5000

AUDITADO_FILES = (
    "tabela_auditado_com_tipo_de_conclusao_1.tsv",
    "tabela_auditado_com_tipo_de_conclusao_2.tsv",
    "tabela_auditado_com_tipo_de_conclusao_3.tsv",
)
FALHAS_FILE = "tabela_falhas.tsv"

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "qualidade"


@dataclass
class ImportStats:
    files: dict[str, dict[str, int]] = field(default_factory=dict)
    ok: int = 0
    skipped: int = 0
    errors: int = 0

    def bump(self, filename: str, *, ok: int = 0, skipped: int = 0, errors: int = 0) -> None:
        bucket = self.files.setdefault(filename, {"ok": 0, "skipped": 0, "errors": 0})
        bucket["ok"] += ok
        bucket["skipped"] += skipped
        bucket["errors"] += errors
        self.ok += ok
        self.skipped += skipped
        self.errors += errors

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "errors": self.errors,
            "files": self.files,
        }


def _open_tsv(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            yield { (k or "").replace("\ufeff", "").strip(): (v or "") for k, v in row.items() }


def _get(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        if key in row:
            return row.get(key) or ""
    # case-insensitive fallback
    lower_map = {k.lower(): k for k in row}
    for key in keys:
        real = lower_map.get(key.lower())
        if real is not None:
            return row.get(real) or ""
    return ""


def row_to_auditado(row: dict[str, str], *, source_file: str) -> QualidadeAuditado | None:
    protocolo = clean_text(_get(row, "Protocolo"), max_len=64)
    data = parse_date_br(_get(row, "Data"))
    if not protocolo and data is None:
        return None
    return QualidadeAuditado(
        data=data,
        data_analise=parse_date_br(_get(row, "Data análise", "Data analise")),
        id_cliente=parse_int(_get(row, "ID_Cliente")),
        id_workflow=parse_int(_get(row, "ID_Workflow")),
        tipo_analise=clean_text(_get(row, "Tipo de análise", "Tipo de analise"), max_len=128),
        matricula=normalize_matricula(_get(row, "Matricula", "Matrícula")),
        matricula_auditor=normalize_matricula(_get(row, "Matricula auditor", "Matrícula auditor")),
        protocolo=protocolo,
        cenario=clean_text(_get(row, "Cenário", "Cenario"), max_len=512),
        etapa=clean_text(_get(row, "Etapa"), max_len=256),
        status=clean_text(_get(row, "STATUS", "Status"), max_len=128),
        irregularidades_apontadas=clean_text(_get(row, "IRREGULARIDADES_APONTADAS")),
        cadastrado_anteriormente=clean_text(
            _get(row, "Cadastrado anteriormente"), max_len=128
        ),
        id_operations=parse_int(_get(row, "ID_Operations")),
        resultado_origem=clean_text(_get(row, "Resultado Origem"), max_len=128),
        resultado_destino=clean_text(_get(row, "Resultado Destino"), max_len=128),
        protocolo_destino=clean_text(_get(row, "Protocolo Destino"), max_len=64),
        tipo_conclusao=normalize_tipo_conclusao(
            _get(row, "Tipo de conclusão", "Tipo de conclusao")
        ),
        source_file=source_file,
        imported_at=timezone.now(),
    )


def row_to_falha(row: dict[str, str], *, source_file: str) -> QualidadeFalha | None:
    protocolo = clean_text(_get(row, "Protocolo"), max_len=64)
    data_analise = parse_date_br(_get(row, "Data de Análise", "Data de Analise", "Data análise"))
    if not protocolo and data_analise is None:
        return None
    tipo_falha = clean_text(_get(row, "Tipo de Falha"), max_len=128)
    matricula = normalize_matricula(_get(row, "Matrícula", "Matricula"))
    if is_processual_tipificacao(tipo_falha):
        matricula = ""
    return QualidadeFalha(
        nome_origem=clean_text(_get(row, "Nome da Origem"), max_len=128),
        frk_colaborador=clean_text(_get(row, "FRKCOLABORADOR"), max_len=64),
        protocolo=protocolo,
        case_key=build_case_key_str(protocolo, matricula),
        id_cliente=parse_int(_get(row, "ID_Cliente")),
        id_workflow=parse_int(_get(row, "ID_Workflow")),
        id_operations=parse_int(_get(row, "ID_Operations")),
        tipo_analise=clean_text(_get(row, "Tipo de análise", "Tipo de analise"), max_len=128),
        frk_gerenciamento_fluxo=clean_text(_get(row, "FRK_GERENCIAMENTO_FLUXO"), max_len=64),
        modulo=clean_text(_get(row, "Módulo", "Modulo"), max_len=128),
        cenario=clean_text(_get(row, "Cenário", "Cenario")),
        data=parse_date_br(_get(row, "Data")),
        usuario_auditor=normalize_matricula(_get(row, "Usuário do Auditor", "Usuario do Auditor")),
        matricula=matricula,
        data_analise=data_analise,
        etapa=clean_text(_get(row, "Etapa"), max_len=256),
        tipo_falha=tipo_falha,
        uf=normalize_localidade_documento(_get(row, "UF")),
        localidade_documento=normalize_localidade_documento(_get(row, "UF")),
        tipo_documento=clean_text(_get(row, "Tipo de documento"), max_len=128),
        nivel_dificuldade=clean_text(_get(row, "Nível de Dificuldade", "Nivel de Dificuldade"), max_len=128),
        des_problemas=clean_text(_get(row, "des_problemas")),
        tendencia=clean_text(_get(row, "Tendência", "Tendencia"), max_len=128),
        novo_resultado=clean_text(_get(row, "Novo Resultado"), max_len=128),
        tipo_solicitacao=clean_text(_get(row, "Tipo de solicitação", "Tipo de solicitacao"), max_len=128),
        tipo_modulo=clean_text(_get(row, "Tipo_modulo"), max_len=128),
        prk_colaborador=clean_text(_get(row, "PRKCOLABORADOR"), max_len=64),
        resultado_analise=clean_text(_get(row, "Resultado da Análise", "Resultado da Analise"), max_len=128),
        numero_solicitacao=clean_text(_get(row, "Número da solicitação", "Numero da solicitacao"), max_len=64),
        qualidade_imagem=clean_text(_get(row, "Qualidade da Imagem"), max_len=128),
        origem_analise=clean_text(_get(row, "ORIGEM ANÁLISE", "ORIGEM ANALISE"), max_len=128),
        data_diferenca=clean_text(_get(row, "Data diferença", "Data diferenca"), max_len=64),
        tipo_modulo_2=clean_text(_get(row, "tipo_modulo_2"), max_len=128),
        categoria_falha=clean_text(_get(row, "Categoria falha"), max_len=128),
        localidade=clean_text(_get(row, "Localidade"), max_len=128),
        agente_ativo=clean_text(_get(row, "agente_ativo"), max_len=64),
        lider=clean_text(_get(row, "Líder", "Lider"), max_len=256),
        tipo_falha_oficial=clean_text(_get(row, "tipo_falha_oficial"), max_len=128),
        nivel_dificuldade_confer=clean_text(
            _get(row, "nivel_de_dificuldade_confer"), max_len=128
        ),
        sub_segmento=clean_text(_get(row, "Sub Segmento"), max_len=128),
        segmento=clean_text(_get(row, "Segmento"), max_len=128),
        condicao_metrica=clean_text(_get(row, "CondicaoMetrica"), max_len=128),
        source_file=source_file,
        imported_at=timezone.now(),
    )


def _bulk_insert(model, objs: list, stats: ImportStats, filename: str) -> None:
    if not objs:
        return
    from apps.qualidade_operacional.services.record_admin import apply_states_to_instances

    kind = "falha" if model is QualidadeFalha else "auditado"
    objs = apply_states_to_instances(objs, kind)
    model.all_objects.bulk_create(objs, batch_size=BULK_CHUNK_SIZE)
    stats.bump(filename, ok=len(objs))


def _import_file(
    path: Path,
    *,
    model,
    mapper: Callable[[dict[str, str]], object | None],
    stats: ImportStats,
    replace_file: bool,
) -> None:
    filename = path.name
    if not path.exists():
        # Partições FY podem não existir em ambientes/fixtures — só ignora.
        stats.bump(filename, skipped=0)
        return

    if replace_file:
        model.all_objects.filter(source_file=filename).delete()

    buffer: list = []
    for row in _open_tsv(path):
        try:
            obj = mapper(row)
        except Exception:
            stats.bump(filename, errors=1)
            continue
        if obj is None:
            stats.bump(filename, skipped=1)
            continue
        buffer.append(obj)
        if len(buffer) >= BULK_CHUNK_SIZE:
            chunk = buffer
            if model is QualidadeFalha:
                chunk, _, dedupe_stats = prepare_falhas_for_import(chunk)
                stats.bump(
                    filename,
                    skipped=dedupe_stats.get("skipped_file", 0)
                    + dedupe_stats.get("skipped_db", 0),
                )
            _bulk_insert(model, chunk, stats, filename)
            buffer = []
    if buffer:
        if model is QualidadeFalha:
            buffer, _, dedupe_stats = prepare_falhas_for_import(buffer)
            stats.bump(
                filename,
                skipped=dedupe_stats.get("skipped_file", 0)
                + dedupe_stats.get("skipped_db", 0),
            )
        _bulk_insert(model, buffer, stats, filename)


def import_qualidade_operacional(
    data_dir: Path | str | None = None,
    *,
    mode: str = "upsert",
    only: str | None = None,
) -> ImportStats:
    """
    mode:
      - replace: truncate tables then load
      - upsert: delete by source_file then reload each file
    only: "auditados" | "falhas" | None (both)
    """
    root = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    stats = ImportStats()
    replace = mode == "replace"
    load_auditados = only in (None, "auditados")
    load_falhas = only in (None, "falhas")

    with transaction.atomic():
        if replace:
            if load_auditados:
                QualidadeAuditado.all_objects.all().delete()
            if load_falhas:
                QualidadeFalha.all_objects.all().delete()

        if load_auditados:
            for name in AUDITADO_FILES:
                path = root / name
                _import_file(
                    path,
                    model=QualidadeAuditado,
                    mapper=lambda row, n=name: row_to_auditado(row, source_file=n),
                    stats=stats,
                    replace_file=not replace,
                )

        if load_falhas:
            path = root / FALHAS_FILE
            _import_file(
                path,
                model=QualidadeFalha,
                mapper=lambda row, n=FALHAS_FILE: row_to_falha(row, source_file=n),
                stats=stats,
                replace_file=not replace,
            )

    refresh_filter_catalog()
    return stats
