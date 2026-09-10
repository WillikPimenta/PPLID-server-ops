"""Rebuild brflow-prod-bruto/tratado + prod-unificado from DB CSVs.

Usage:
  python tools/rebuild_prod_unificado_from_db_csv.py <pasta_csvs_DDMMYYYY>
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bots.rotina.io import (  # noqa: E402
    _corrigir_colunas_dataframe,
    _preparar_dataframe_para_parquet,
    verifica_usuario,
)
from app.config import (  # noqa: E402
    PASTA_CONFER_PROD_UNIFICADO,
    PASTA_PROD_TRATADO,
    PASTA_PROD_UNIFICADO_BOTS,
    PASTA_PRODUCAO_CONFER_TRATADO,
    PASTA_PRODUTIVIDADE_D1_BRUTA,
    PREFIXO_CONF_PROD_UNIFICADO,
    PREFIXO_CONF_TRATADO,
    PREFIXO_PROD_D1,
    PREFIXO_PROD_TRATADA,
)


def _ddmmyyyy_to_yyyymmdd(stem: str) -> str:
    if len(stem) != 8 or not stem.isdigit():
        raise ValueError(f"Nome de arquivo invalido (esperado DDMMYYYY): {stem}")
    return stem[4:8] + stem[2:4] + stem[0:2]


def ler_csv_db(path: Path) -> pd.DataFrame:
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(path, encoding=enc)
            if len(df.columns) >= 6:
                return df
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"Falha ao ler {path}: {last_err}")


def normalizar_matricula(df: pd.DataFrame) -> pd.DataFrame:
    if "desMatricula" not in df.columns:
        return df
    antes = len(df)
    matricula_raw = df["desMatricula"].astype(str).str.strip().str[:7]
    valida = matricula_raw.apply(verifica_usuario)
    out = df[valida].copy()
    out["desMatricula"] = matricula_raw[valida].values
    print(f"  matricula: {antes:,} -> {len(out):,} (removidas {antes - len(out):,})")
    return out


def unir_dia(data_ref: str) -> dict:
    conf = PASTA_PRODUCAO_CONFER_TRATADO / f"{PREFIXO_CONF_TRATADO}{data_ref}.parquet"
    prod = PASTA_PROD_TRATADO / f"{PREFIXO_PROD_TRATADA}{data_ref}.parquet"
    out: dict = {
        "conf_rows": None,
        "prod_rows": None,
        "uni_rows": None,
        "conf_ok": conf.exists(),
        "prod_ok": prod.exists(),
    }
    if not prod.exists():
        return out

    df_prod = pd.read_parquet(prod)
    out["prod_rows"] = len(df_prod)
    if "desMatricula" in df_prod.columns and "matricula" not in df_prod.columns:
        df_prod = df_prod.rename(columns={"desMatricula": "matricula"})

    parts = [df_prod]
    if conf.exists():
        df_conf = pd.read_parquet(conf)
        out["conf_rows"] = len(df_conf)
        parts.insert(0, df_conf)

    df_u = pd.concat(parts, ignore_index=True, sort=False)
    cols = [c for c in ["Data", "Hora", "matricula"] if c in df_u.columns]
    if cols:
        df_u = df_u.sort_values(by=cols, kind="stable").reset_index(drop=True)

    saida = PASTA_PROD_UNIFICADO_BOTS / f"{PREFIXO_CONF_PROD_UNIFICADO}{data_ref}.parquet"
    df_u = _preparar_dataframe_para_parquet(df_u)
    PASTA_PROD_UNIFICADO_BOTS.mkdir(parents=True, exist_ok=True)
    df_u.to_parquet(saida, index=False, compression="snappy")

    try:
        PASTA_CONFER_PROD_UNIFICADO.mkdir(parents=True, exist_ok=True)
        mirror = PASTA_CONFER_PROD_UNIFICADO / saida.name
        df_u.to_parquet(mirror, index=False, compression="snappy")
        out["mirror"] = str(mirror)
    except Exception as exc:  # noqa: BLE001
        out["mirror_err"] = str(exc)

    out["uni_rows"] = len(df_u)
    out["saida"] = str(saida)
    return out


def _data_esperada(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def filtrar_dia(df: pd.DataFrame, yyyymmdd: str) -> pd.DataFrame:
    """Garante que só entram linhas do dia do nome do arquivo."""
    if "datAnalise" not in df.columns:
        raise ValueError("Coluna datAnalise ausente")
    dt = pd.to_datetime(df["datAnalise"], dayfirst=True, errors="coerce")
    alvo = pd.Timestamp(_data_esperada(yyyymmdd)).date()
    mask = dt.dt.date == alvo
    out = df.loc[mask].copy()
    datas = sorted({str(x) for x in dt.dt.date.dropna().unique()})
    print(f"  datas no CSV: {datas} | filtro {alvo}: {mask.sum():,}/{len(df):,}")
    return out


def tratar_para_data(bruto_path: Path, yyyymmdd: str) -> Path | None:
    """Trata produtividade forçando o nome/data do arquivo (não a menor data do DF)."""
    df = pd.read_parquet(bruto_path) if bruto_path.suffix.lower() == ".parquet" else ler_csv_db(bruto_path)
    obrigatorias = {"datAnalise", "numTempoAnalise", "desMatricula", "nomCliente", "nomWorkflow", "nomEtapa"}
    faltantes = [c for c in obrigatorias if c not in df.columns]
    if faltantes:
        print(f"  tratado ignorado, colunas ausentes: {faltantes}")
        return None

    df = df.copy()
    df["datAnalise"] = pd.to_datetime(df["datAnalise"], dayfirst=True, errors="coerce")
    df = df[df["datAnalise"].notna()].copy()
    alvo = pd.Timestamp(_data_esperada(yyyymmdd)).date()
    df["Data"] = df["datAnalise"].dt.date
    df = df[df["Data"] == alvo].copy()
    if df.empty:
        print(f"  tratado ignorado: sem linhas para {alvo}")
        return None

    df["Hora"] = df["datAnalise"].dt.hour
    df_sorted = df.sort_values(by=["desMatricula", "datAnalise"]).copy()
    df_sorted["numTempoAnalise"] = (
        pd.to_timedelta(df_sorted["numTempoAnalise"], errors="coerce").fillna(pd.Timedelta(seconds=0))
    )
    df_sorted["numTempoAnaliseSeconds"] = df_sorted["numTempoAnalise"].dt.total_seconds()
    df_sorted["datConclusao"] = df_sorted["datAnalise"] + pd.to_timedelta(
        df_sorted["numTempoAnaliseSeconds"], unit="s"
    )
    df_sorted = df_sorted.drop(columns=["numTempoAnaliseSeconds"])
    df_sorted["gap"] = df_sorted["datAnalise"].shift(-1) - df_sorted["datConclusao"]
    df_sorted.loc[
        (df_sorted["desMatricula"] != df_sorted["desMatricula"].shift(-1))
        | (df_sorted["Data"] != df_sorted["Data"].shift(-1)),
        "gap",
    ] = pd.Timedelta(seconds=0)
    df_sorted.loc[
        (df_sorted["desMatricula"] == df_sorted["desMatricula"].shift(-1))
        & (df_sorted["datConclusao"] > df_sorted["datAnalise"].shift(-1)),
        "gap",
    ] = pd.Timedelta(seconds=0)
    df_sorted.loc[df_sorted["gap"] > pd.Timedelta(seconds=19800), "gap"] = pd.Timedelta(seconds=0)

    resultado = (
        df_sorted.groupby(["Data", "Hora", "desMatricula", "nomCliente", "nomWorkflow", "nomEtapa"])
        .agg(tempoAnalise=("numTempoAnalise", "sum"), contagem=("desMatricula", "count"))
        .reset_index()
    )
    resultado["tempoAnalise"] = resultado["tempoAnalise"].dt.total_seconds().astype(int)

    caminho_out = PASTA_PROD_TRATADO / f"{PREFIXO_PROD_TRATADA}{yyyymmdd}.parquet"
    PASTA_PROD_TRATADO.mkdir(parents=True, exist_ok=True)
    _preparar_dataframe_para_parquet(resultado).to_parquet(caminho_out, index=False, compression="snappy")
    return caminho_out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    pasta = Path(sys.argv[1])
    if not pasta.is_dir():
        print(f"Pasta nao encontrada: {pasta}")
        return 1

    resumo = []
    for csv_path in sorted(pasta.glob("*.csv")):
        yyyymmdd = _ddmmyyyy_to_yyyymmdd(csv_path.stem)
        print(f"\n=== {csv_path.name} -> {yyyymmdd} ===")
        df = ler_csv_db(csv_path)
        print(f"  lido: {len(df):,} linhas")
        df = _corrigir_colunas_dataframe(df)
        df = filtrar_dia(df, yyyymmdd)
        if df.empty:
            print("  SKIP: CSV nao contem dados do dia do nome do arquivo")
            resumo.append({"dia": yyyymmdd, "status": "skip_data_mismatch"})
            continue

        df = normalizar_matricula(df)
        bruto_path = PASTA_PRODUTIVIDADE_D1_BRUTA / f"{PREFIXO_PROD_D1}{yyyymmdd}.parquet"
        PASTA_PRODUTIVIDADE_D1_BRUTA.mkdir(parents=True, exist_ok=True)
        df_pq = _preparar_dataframe_para_parquet(df)
        df_pq.to_parquet(bruto_path, index=False, compression="snappy")
        print(f"  bruto: {bruto_path.name} ({len(df_pq):,})")

        tratado = tratar_para_data(bruto_path, yyyymmdd)
        print(f"  tratado: {tratado}")

        u = unir_dia(yyyymmdd)
        print(
            "  unificado:"
            f" conf={u.get('conf_rows')} prod={u.get('prod_rows')}"
            f" uni={u.get('uni_rows')} conf_ok={u.get('conf_ok')}"
        )
        resumo.append(
            {
                "dia": yyyymmdd,
                "bruto": len(df_pq),
                "conf_rows": u.get("conf_rows"),
                "prod_rows": u.get("prod_rows"),
                "uni_rows": u.get("uni_rows"),
                "conf_ok": u.get("conf_ok"),
                "status": "ok",
            }
        )

    print("\n===== RESUMO =====")
    for row in resumo:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
