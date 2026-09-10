# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from apps.rotina_bruto.models import (
    RotinaConferBuscaRecord,
    RotinaDetalhadoBrutoRecord,
    RotinaGedDetalhadoTratadoRecord,
    RotinaGedIrregularidadeTratadoRecord,
    RotinaProdBrutoRecord,
)
from apps.rotina_bruto.services.pii import hash_cpf
from apps.rotina_bruto.services.parsers import (
    parse_date,
    parse_date_series,
    parse_datetime_series,
    parse_protocolo,
    parse_tempo_segundos,
)

# Detalhado: smaller batches — alertas TEXT lives in a satellite table.
BATCH_SIZE = 2000
DETALHADO_CHUNK_SIZE = 2000

DETALHADO_COLUMN_MAP = {
    "Protocolo": "protocolo",
    "Cliente": "cliente",
    "Workflow": "workflow",
    "CPF": "cpf",
    "Data de Cadastro": "data_cadastro",
    "Data de Conclusão": "data_conclusao",
    "Status do Registro": "status_registro",
    "Resultado": "resultado",
    "Nível Hierárquico": "nivel_hierarquico",
    "matrícula": "matricula",
    "matricula": "matricula",
    "Data da Primeira Conclusão": "data_primeira_conclusao",
    "Data de Análise": "data_analise",
    "Alertas": "alertas",
}

PROD_KNOWN_COLUMNS = {
    "desMatricula": "des_matricula",
    "datAnalise": "dat_analise",
    "numTempoAnalise": "num_tempo_analise",
    "nomCliente": "nom_cliente",
    "nomWorkflow": "nom_workflow",
    "nomEtapa": "nom_etapa",
}

CONFER_BUSCA_COLUMN_MAP = {
    "Data/Hora da Conferência": "data_hora_conferencia",
    "Tempo por minuto": "tempo_por_minuto",
    "Protocolo": "protocolo",
    "Status": "status",
    "Ilha": "ilha",
    "Etapa": "etapa",
    "Matrícula": "matricula",
    "Nome": "nome",
    "Tipo/Status conferencia": "tipo_status_conferencia",
}

GED_DETALHADO_COLUMN_MAP = {
    "Protocolo": "protocolo",
    "Data do Recebimento": "data_recebimento",
    "Tipo de Serviço Primário": "tipo_servico_primario",
    "Data da Venda": "data_venda",
    "Data do Batimento": "data_batimento",
    "Data Retorno Inspeção": "data_retorno_inspecao",
    "Data Envio Inspe.": "data_envio_inspe",
    "Canal de Ativação": "canal_ativacao",
    "Status Contrato": "status_contrato",
    "Aceite Digital": "aceite_digital",
}

GED_IRREGULARIDADE_COLUMN_MAP = {
    "Protocolo": "protocolo",
    "Status Contrato": "status_contrato",
    "MSISDN": "msisdn",
    "CPF": "cpf",
    "Data do Recebimento": "data_recebimento",
    "Data Contestação": "data_contestacao",
    "Data da Resposta": "data_resposta",
    "Tipo de Serviço": "tipo_servico",
    "Regional": "regional",
    "Estado": "estado",
    "Canal de Ativação": "canal_ativacao",
    "Cód. PDV": "cod_pdv",
    "Usuário": "usuario",
    "Status da contestação": "status_contestacao",
    "Matrícula do Inspetor": "matricula_inspetor",
    "Descrição das Irregularidades": "descricao_irregularidades",
}

CSV_ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1"]


def _read_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in CSV_ENCODINGS:
        try:
            return pd.read_csv(path, sep=None, engine="python", dtype=str, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, sep=None, engine="python", dtype=str)


def read_parquet(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return _read_csv(path)
    return pd.read_parquet(path)


def iter_parquet_dataframe_batches(
    path: Path, batch_rows: int
) -> Iterator[pd.DataFrame]:
    """Yield DataFrame batches without holding the full parquet in memory.

    CSV fallback still loads the full file then slices (GED prod is parquet).
    """
    if batch_rows < 1:
        raise ValueError("batch_rows must be >= 1")
    path = Path(path)
    if path.suffix.lower() == ".csv":
        df = read_parquet(path)
        if df.empty:
            return
        for start in range(0, len(df), batch_rows):
            yield df.iloc[start : start + batch_rows].copy()
        return

    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    metadata = pf.metadata
    if metadata is not None and metadata.num_rows == 0:
        return
    for batch in pf.iter_batches(batch_size=batch_rows):
        yield batch.to_pandas()


def _cell_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _row_extra(row: pd.Series, known_source_cols: set[str]) -> dict[str, str]:
    extra: dict[str, str] = {}
    for col in row.index:
        col_name = str(col)
        if col_name in known_source_cols:
            continue
        extra[col_name] = _cell_str(row[col])
    return extra


def _detalhado_row_payload(row: pd.Series) -> dict[str, str]:
    payload: dict[str, str] = {}
    for src, dst in DETALHADO_COLUMN_MAP.items():
        if src in row.index:
            payload[dst] = _cell_str(row[src])
    return payload


def _build_detalhado_record(
    payload: dict[str, str],
    report_date: date,
) -> RotinaDetalhadoBrutoRecord:
    return RotinaDetalhadoBrutoRecord(
        report_date=report_date,
        protocolo=parse_protocolo(payload.get("protocolo")),
        cliente=payload.get("cliente", ""),
        workflow=payload.get("workflow", ""),
        cpf=hash_cpf(payload.get("cpf", "")),
        data_cadastro=parse_date(payload.get("data_cadastro")),
        data_conclusao=parse_date(payload.get("data_conclusao")),
        status_registro=payload.get("status_registro", ""),
        resultado=payload.get("resultado", ""),
        nivel_hierarquico=payload.get("nivel_hierarquico", ""),
        matricula=payload.get("matricula", ""),
        data_primeira_conclusao=parse_date(payload.get("data_primeira_conclusao")),
        data_analise=parse_date(payload.get("data_analise")),
    )


def _series_or_empty(df: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([None] * len(df), index=df.index)


def _str_series(series: pd.Series) -> list[str]:
    out: list[str] = []
    for value in series:
        out.append(_cell_str(value))
    return out


def _extras_list(df: pd.DataFrame, known_source_cols: set[str]) -> list[dict[str, str]]:
    """Monta extra JSON por linha sem iterrows (colunas fora do mapa conhecido)."""
    n = len(df)
    if n == 0:
        return []
    unknown = [str(c) for c in df.columns if str(c) not in known_source_cols]
    if not unknown:
        return [{} for _ in range(n)]
    col_values = {col: _str_series(df[col]) for col in unknown}
    extras: list[dict[str, str]] = []
    for i in range(n):
        extras.append({col: col_values[col][i] for col in unknown})
    return extras


def _mapped_str_columns(
    df: pd.DataFrame, column_map: dict[str, str]
) -> dict[str, list[str]]:
    """Extrai colunas do mapa como listas de str (chave = destino canônico)."""
    out: dict[str, list[str]] = {}
    for src, dst in column_map.items():
        if dst in out:
            continue
        out[dst] = _str_series(_series_or_empty(df, src))
    return out


def _build_detalhado_chunk_vectorized(
    slice_df: pd.DataFrame,
    report_date: date,
) -> tuple[list[RotinaDetalhadoBrutoRecord], list[str]]:
    """Monta chunk de records com datas parseadas em lote (sem parse_date por célula)."""
    if slice_df.empty:
        return [], []

    protocolos = _str_series(_series_or_empty(slice_df, "Protocolo"))
    clientes = _str_series(_series_or_empty(slice_df, "Cliente"))
    workflows = _str_series(_series_or_empty(slice_df, "Workflow"))
    cpfs = _str_series(_series_or_empty(slice_df, "CPF"))
    status_list = _str_series(_series_or_empty(slice_df, "Status do Registro"))
    resultados = _str_series(_series_or_empty(slice_df, "Resultado"))
    niveis = _str_series(
        _series_or_empty(slice_df, "Nível Hierárquico", "Nivel Hierarquico")
    )
    matriculas = _str_series(_series_or_empty(slice_df, "matrícula", "matricula"))
    alertas = _str_series(_series_or_empty(slice_df, "Alertas"))

    data_cadastro = parse_date_series(_series_or_empty(slice_df, "Data de Cadastro"))
    data_conclusao = parse_date_series(
        _series_or_empty(slice_df, "Data de Conclusão", "Data de Conclusao")
    )
    data_primeira = parse_date_series(
        _series_or_empty(
            slice_df, "Data da Primeira Conclusão", "Data da Primeira Conclusao"
        )
    )
    data_analise = parse_date_series(
        _series_or_empty(slice_df, "Data de Análise", "Data de Analise")
    )

    n = len(slice_df)
    records: list[RotinaDetalhadoBrutoRecord] = []
    for i in range(n):
        records.append(
            RotinaDetalhadoBrutoRecord(
                report_date=report_date,
                protocolo=parse_protocolo(protocolos[i]),
                cliente=clientes[i][:255],
                workflow=workflows[i][:255],
                cpf=hash_cpf(cpfs[i]),
                data_cadastro=data_cadastro[i],
                data_conclusao=data_conclusao[i],
                status_registro=status_list[i][:255],
                resultado=resultados[i][:255],
                nivel_hierarquico=niveis[i][:255],
                matricula=matriculas[i][:64],
                data_primeira_conclusao=data_primeira[i],
                data_analise=data_analise[i],
            )
        )
    return records, alertas


def build_detalhado_records(
    df: pd.DataFrame,
    report_date: date,
) -> tuple[list[RotinaDetalhadoBrutoRecord], list[str]]:
    """Retorna (mains, alerta_texts) alinhados por índice. Sem campo alertas no main."""
    return _build_detalhado_chunk_vectorized(df, report_date)


def iter_detalhado_chunks(
    df: pd.DataFrame,
    report_date: date,
    chunk_size: int = DETALHADO_CHUNK_SIZE,
) -> Iterator[tuple[list[RotinaDetalhadoBrutoRecord], list[str]]]:
    """Yields (main_chunk, alerta_texts) without holding the full ORM list."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    n = len(df)
    for start in range(0, n, chunk_size):
        slice_df = df.iloc[start : start + chunk_size]
        main_chunk, alerta_chunk = _build_detalhado_chunk_vectorized(
            slice_df, report_date
        )
        if main_chunk:
            yield main_chunk, alerta_chunk


def build_prod_records(df: pd.DataFrame, report_date: date) -> list[RotinaProdBrutoRecord]:
    if df.empty:
        return []
    known = set(PROD_KNOWN_COLUMNS.keys())
    des_matricula = _str_series(_series_or_empty(df, "desMatricula"))
    nom_cliente = _str_series(_series_or_empty(df, "nomCliente"))
    nom_workflow = _str_series(_series_or_empty(df, "nomWorkflow"))
    nom_etapa = _str_series(_series_or_empty(df, "nomEtapa"))
    dat_analise = parse_date_series(_series_or_empty(df, "datAnalise"))
    tempo_raw = _series_or_empty(df, "numTempoAnalise")
    extras = _extras_list(df, known)

    n = len(df)
    records: list[RotinaProdBrutoRecord] = []
    for i in range(n):
        records.append(
            RotinaProdBrutoRecord(
                report_date=report_date,
                des_matricula=des_matricula[i][:64],
                dat_analise=dat_analise[i],
                num_tempo_analise=parse_tempo_segundos(tempo_raw.iloc[i]),
                nom_cliente=nom_cliente[i][:255],
                nom_workflow=nom_workflow[i][:255],
                nom_etapa=nom_etapa[i][:255],
                extra=extras[i],
            )
        )
    return records


def build_confer_busca_records(
    df: pd.DataFrame,
    report_date: date,
    *,
    periodo: int | None = None,
) -> list[RotinaConferBuscaRecord]:
    if df.empty:
        return []
    known = set(CONFER_BUSCA_COLUMN_MAP.keys())
    cols = _mapped_str_columns(df, CONFER_BUSCA_COLUMN_MAP)
    data_hora = parse_datetime_series(
        _series_or_empty(df, "Data/Hora da Conferência")
    )
    extras = _extras_list(df, known)

    n = len(df)
    records: list[RotinaConferBuscaRecord] = []
    for i in range(n):
        records.append(
            RotinaConferBuscaRecord(
                report_date=report_date,
                periodo=periodo,
                data_hora_conferencia=data_hora[i],
                tempo_por_minuto=cols["tempo_por_minuto"][i][:64],
                protocolo=parse_protocolo(cols["protocolo"][i]),
                status=cols["status"][i][:255],
                ilha=cols["ilha"][i][:255],
                etapa=cols["etapa"][i][:255],
                matricula=cols["matricula"][i][:64],
                nome=cols["nome"][i][:255],
                tipo_status_conferencia=cols["tipo_status_conferencia"][i][:255],
                extra=extras[i],
            )
        )
    return records


def build_ged_detalhado_records(
    df: pd.DataFrame,
    report_date: date,
    *,
    periodo: int | None = None,
) -> list[RotinaGedDetalhadoTratadoRecord]:
    if df.empty:
        return []
    known = set(GED_DETALHADO_COLUMN_MAP.keys())
    cols = _mapped_str_columns(df, GED_DETALHADO_COLUMN_MAP)
    data_recebimento = parse_date_series(
        _series_or_empty(df, "Data do Recebimento")
    )
    data_venda = parse_date_series(_series_or_empty(df, "Data da Venda"))
    data_batimento = parse_date_series(_series_or_empty(df, "Data do Batimento"))
    data_retorno = parse_date_series(
        _series_or_empty(df, "Data Retorno Inspeção", "Data Retorno Inspecao")
    )
    data_envio = parse_date_series(
        _series_or_empty(df, "Data Envio Inspe.", "Data Envio Inspe")
    )
    extras = _extras_list(df, known)

    n = len(df)
    records: list[RotinaGedDetalhadoTratadoRecord] = []
    for i in range(n):
        records.append(
            RotinaGedDetalhadoTratadoRecord(
                report_date=report_date,
                periodo=periodo,
                protocolo=parse_protocolo(cols["protocolo"][i]),
                data_recebimento=data_recebimento[i],
                tipo_servico_primario=cols["tipo_servico_primario"][i][:255],
                data_venda=data_venda[i],
                data_batimento=data_batimento[i],
                data_retorno_inspecao=data_retorno[i],
                data_envio_inspe=data_envio[i],
                canal_ativacao=cols["canal_ativacao"][i][:255],
                status_contrato=cols["status_contrato"][i][:255],
                aceite_digital=cols["aceite_digital"][i][:255],
                extra=extras[i],
            )
        )
    return records


def build_ged_irregularidade_records(
    df: pd.DataFrame,
    report_date: date,
    *,
    periodo: int | None = None,
) -> list[RotinaGedIrregularidadeTratadoRecord]:
    if df.empty:
        return []
    known = set(GED_IRREGULARIDADE_COLUMN_MAP.keys())
    cols = _mapped_str_columns(df, GED_IRREGULARIDADE_COLUMN_MAP)
    data_recebimento = parse_date_series(
        _series_or_empty(df, "Data do Recebimento")
    )
    data_contestacao = parse_date_series(
        _series_or_empty(df, "Data Contestação", "Data Contestacao")
    )
    data_resposta = parse_date_series(_series_or_empty(df, "Data da Resposta"))
    extras = _extras_list(df, known)

    n = len(df)
    records: list[RotinaGedIrregularidadeTratadoRecord] = []
    for i in range(n):
        records.append(
            RotinaGedIrregularidadeTratadoRecord(
                report_date=report_date,
                periodo=periodo,
                protocolo=parse_protocolo(cols["protocolo"][i]),
                status_contrato=cols["status_contrato"][i][:255],
                msisdn=cols["msisdn"][i][:512],
                cpf=hash_cpf(cols["cpf"][i]),
                data_recebimento=data_recebimento[i],
                data_contestacao=data_contestacao[i],
                data_resposta=data_resposta[i],
                tipo_servico=cols["tipo_servico"][i][:255],
                regional=cols["regional"][i][:255],
                estado=cols["estado"][i][:64],
                canal_ativacao=cols["canal_ativacao"][i][:255],
                cod_pdv=cols["cod_pdv"][i][:64],
                usuario=cols["usuario"][i][:255],
                status_contestacao=cols["status_contestacao"][i][:255],
                matricula_inspetor=cols["matricula_inspetor"][i][:64],
                descricao_irregularidades=cols["descricao_irregularidades"][i],
                extra=extras[i],
            )
        )
    return records
