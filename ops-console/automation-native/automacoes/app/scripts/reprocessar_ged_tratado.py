"""Reprocessa arquivos brutos e consolida saídas GED no modo noturno."""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.config import DEFAULT_SHAREPOINT_BOTS, PREFIXO_GED_TRATADO
from app.bots import bot_ged

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Sufixo noturno: {dia_ini}-{dia_fim}{mm}{aaaa} ex.: 29-30042026
_PERIOD_SUFFIX_RE = re.compile(r"^(\d{2})-(\d{2})(\d{2})(\d{4})$")
_RAW_PERIOD_RE = re.compile(
    r"^ged-detalhado[_-](\d{2})-(\d{2})(\d{2})(\d{4})$",
    re.IGNORECASE,
)
_WEEKLY_TRATADO_RE = re.compile(
    rf"^{re.escape(PREFIXO_GED_TRATADO)}(\d{{2}})-(\d{{2}})(\d{{2}})(\d{{4}})\.parquet$",
    re.IGNORECASE,
)
_MONTHLY_RE = re.compile(
    rf"^{re.escape(PREFIXO_GED_TRATADO)}(\d{{6}})\.parquet$",
    re.IGNORECASE,
)
_DIURNO_RE = re.compile(
    rf"^{re.escape(PREFIXO_GED_TRATADO)}(\d+)sem\.parquet$",
    re.IGNORECASE,
)
_RAW_EXTS = {".csv", ".xlsx", ".xls"}


def _pasta_padrao() -> Path:
    return DEFAULT_SHAREPOINT_BOTS / "ged-detalhado-tratado"


def _periodo_de_sufixo(sufixo: str) -> tuple[str, str] | None:
    m = _PERIOD_SUFFIX_RE.match(sufixo.strip())
    if not m:
        return None
    dia_ini, dia_fim, mes, ano = m.group(1), m.group(2), m.group(3), m.group(4)
    return (
        f"{int(dia_ini):02d}/{mes}/{ano}",
        f"{int(dia_fim):02d}/{mes}/{ano}",
    )


def _periodo_de_nome_bruto(stem: str) -> tuple[str, str] | None:
    m = _RAW_PERIOD_RE.match(stem.strip())
    if not m:
        return None
    return _periodo_de_sufixo(f"{m.group(1)}-{m.group(2)}{m.group(3)}{m.group(4)}")


def _periodo_de_nome_tratado(stem: str) -> tuple[str, str] | None:
    prefixo = PREFIXO_GED_TRATADO
    if not stem.lower().startswith(prefixo.lower()):
        return None
    return _periodo_de_sufixo(stem[len(prefixo) :])


def _classificar_arquivos(pasta: Path) -> dict[str, list[Path]]:
    grupos: dict[str, list[Path]] = {
        "brutos": [],
        "semanais_tratados": [],
        "mensais": [],
        "diurnos": [],
        "outros": [],
    }
    for caminho in sorted(pasta.iterdir()):
        if not caminho.is_file():
            continue
        nome = caminho.name
        if _DIURNO_RE.match(nome):
            grupos["diurnos"].append(caminho)
            continue
        if _MONTHLY_RE.match(nome):
            grupos["mensais"].append(caminho)
            continue
        if _WEEKLY_TRATADO_RE.match(nome):
            grupos["semanais_tratados"].append(caminho)
            continue
        stem = caminho.stem
        # CSV espelho de parquet já tratado — ignorar
        if caminho.suffix.lower() == ".csv":
            parquet_irmao = caminho.with_suffix(".parquet")
            if parquet_irmao.exists():
                continue
            if _MONTHLY_RE.match(parquet_irmao.name) or nome.endswith(".parquet"):
                continue
        if caminho.suffix.lower() == ".csv" and _DIURNO_RE.match(
            caminho.with_suffix(".parquet").name
        ):
            continue
        if _RAW_PERIOD_RE.match(stem) and caminho.suffix.lower() in _RAW_EXTS:
            grupos["brutos"].append(caminho)
            continue
        if caminho.suffix.lower() in _RAW_EXTS and stem.lower().startswith("pos_venda"):
            grupos["brutos"].append(caminho)
            continue
        grupos["outros"].append(caminho)
    return grupos


def _chave_mes_de_parquet(caminho: Path) -> str | None:
    m = _WEEKLY_TRATADO_RE.match(caminho.name)
    if m:
        return f"{m.group(4)}{m.group(3)}"
    m = _MONTHLY_RE.match(caminho.name)
    if m:
        return m.group(1)
    return None


def _meses_com_semanais(pasta: Path) -> dict[str, list[Path]]:
    por_mes: dict[str, list[Path]] = {}
    for caminho in pasta.glob(f"{PREFIXO_GED_TRATADO}*.parquet"):
        if _DIURNO_RE.match(caminho.name) or _MONTHLY_RE.match(caminho.name):
            continue
        if not _WEEKLY_TRATADO_RE.match(caminho.name):
            continue
        chave = _chave_mes_de_parquet(caminho)
        if chave:
            por_mes.setdefault(chave, []).append(caminho)
    return por_mes


def _limpar_csv_semanais_orfaos(pasta: Path) -> int:
    """Remove CSV semanal quando o parquet já foi consolidado/removido."""
    removidos = 0
    for csv_path in pasta.glob(f"{PREFIXO_GED_TRATADO}*.csv"):
        if _MONTHLY_RE.match(csv_path.with_suffix(".parquet").name):
            continue
        if _DIURNO_RE.match(csv_path.with_suffix(".parquet").name):
            continue
        if not _periodo_de_nome_tratado(csv_path.stem):
            continue
        if csv_path.with_suffix(".parquet").exists():
            continue
        try:
            csv_path.unlink(missing_ok=True)
            removidos += 1
            log.info("CSV semanal órfão removido: %s", csv_path.name)
        except OSError as exc:
            log.warning("Falha ao remover %s: %s", csv_path.name, exc)
    return removidos


def _consolidar_mes(
    pasta: Path,
    chave_mes: str,
    semanais: list[Path],
    incluir_mensal_existente: bool = True,
) -> Path | None:
    if not semanais and not incluir_mensal_existente:
        return None

    partes: list[pd.DataFrame] = []
    mensal_path = pasta / f"{PREFIXO_GED_TRATADO}{chave_mes}.parquet"
    if incluir_mensal_existente and mensal_path.exists() and not semanais:
        partes.append(pd.read_parquet(mensal_path))
    elif incluir_mensal_existente and mensal_path.exists() and semanais:
        # Cenário B: mensal antigo sem os semanais na pasta — funde sem duplicar intervalos já cobertos
        log.info(
            "Mes %s: mesclando mensal existente com %d semanal(is) (cenário B)",
            chave_mes,
            len(semanais),
        )
        partes.append(pd.read_parquet(mensal_path))

    for arquivo in sorted(semanais):
        partes.append(pd.read_parquet(arquivo))

    if not partes:
        return None

    df_mes = pd.concat(partes, ignore_index=True)
    if not df_mes.empty:
        df_mes = df_mes.drop_duplicates().reset_index(drop=True)

    data_ref = datetime.strptime(chave_mes, "%Y%m").date()
    caminho_saida = pasta / bot_ged._montar_nome_saida_ged_mensal(data_ref)
    bot_ged._salvar_saida_parquet(df_mes, caminho_saida)

    removidos = 0
    for arquivo in semanais:
        try:
            arquivo.unlink(missing_ok=True)
            arquivo.with_suffix(".csv").unlink(missing_ok=True)
            removidos += 1
        except OSError as exc:
            log.warning("Falha ao remover %s: %s", arquivo.name, exc)

    _limpar_csv_semanais_orfaos(pasta)

    log.info(
        "Consolidado %s (%d linhas) | semanais removidos %d/%d",
        caminho_saida.name,
        len(df_mes),
        removidos,
        len(semanais),
    )
    return caminho_saida


def _restaurar_parquet_de_csv_tratado(pasta: Path) -> list[Path]:
    """Gera .parquet ausente a partir de CSV já no padrão tratado."""
    restaurados: list[Path] = []
    for csv_path in sorted(pasta.glob(f"{PREFIXO_GED_TRATADO}*.csv")):
        if _MONTHLY_RE.match(csv_path.with_suffix(".parquet").name):
            continue
        if _DIURNO_RE.match(csv_path.with_suffix(".parquet").name):
            continue
        parquet_path = csv_path.with_suffix(".parquet")
        if parquet_path.exists():
            continue
        periodo = _periodo_de_nome_tratado(csv_path.stem)
        if not periodo:
            continue
        try:
            df = bot_ged._carregar_dataframe_ged(csv_path)
            df.to_parquet(parquet_path, index=False)
            restaurados.append(parquet_path)
            log.info("Parquet restaurado: %s", parquet_path.name)
        except Exception as exc:
            log.error("Falha ao restaurar %s: %s", csv_path.name, exc)
    return restaurados


def tratar_brutos(pasta: Path, modo: str, remover_bruto_apos_ok: bool = True) -> list[Path]:
    grupos = _classificar_arquivos(pasta)
    saidas: list[Path] = []
    for caminho in grupos["brutos"]:
        periodo = _periodo_de_nome_bruto(caminho.stem)
        if not periodo:
            log.error(
                "Não foi possível inferir período de %s; informe --inicio/--fim manualmente via teste.py",
                caminho.name,
            )
            continue
        data_inicio, data_fim = periodo
        try:
            saida = bot_ged._tratar_arquivo_ged(
                caminho,
                data_inicio=data_inicio,
                data_fim=data_fim,
            )
            saidas.append(saida)
            log.info("OK: %s -> %s", caminho.name, saida.name)
            if remover_bruto_apos_ok:
                try:
                    caminho.unlink(missing_ok=True)
                    log.info("Bruto removido: %s", caminho.name)
                except OSError as exc:
                    log.warning("Não foi possível remover bruto %s: %s", caminho.name, exc)
        except Exception as exc:
            log.error("ERRO ao tratar %s: %s", caminho.name, exc)
    return saidas


def consolidar_legado(pasta: Path, meses: list[str] | None = None) -> list[Path]:
    """Migra arquivos semanais/período legados para o padrão mensal YYYYMM."""
    bot_ged.PASTA_GED_TRATADO = pasta
    pasta.mkdir(parents=True, exist_ok=True)

    por_mes = _meses_com_semanais(pasta)
    if meses:
        por_mes = {k: v for k, v in por_mes.items() if k in meses}

    consolidados: list[Path] = []
    for chave_mes, semanais in por_mes.items():
        mensal = pasta / f"{PREFIXO_GED_TRATADO}{chave_mes}.parquet"
        if mensal.exists() or semanais:
            out = _consolidar_mes(pasta, chave_mes, semanais, incluir_mensal_existente=mensal.exists())
            if out:
                consolidados.append(out)

    diurnos = [p for p in pasta.glob(f"{PREFIXO_GED_TRATADO}*sem.parquet") if _DIURNO_RE.match(p.name)]
    if diurnos:
        chave = date.today().strftime("%Y%m")
        if not meses or chave in meses:
            out = _consolidar_mes(pasta, chave, diurnos, incluir_mensal_existente=True)
            if out:
                consolidados.append(out)

    return consolidados


def main() -> int:
    parser = argparse.ArgumentParser(description="Reprocessa pasta GED tratado (modo noturno)")
    parser.add_argument(
        "--pasta",
        default=str(_pasta_padrao()),
        help="Pasta ged-detalhado-tratado",
    )
    parser.add_argument(
        "--modo",
        choices=("noturno", "diurno"),
        default="noturno",
        help="Modo de nomenclatura de saída (padrão: noturno)",
    )
    parser.add_argument(
        "--somente-consolidar",
        action="store_true",
        help="Pula tratamento de brutos e só consolida meses",
    )
    parser.add_argument(
        "--meses",
        default="",
        help="Meses YYYYMM para consolidar (vírgula). Vazio = todos com semanais",
    )
    parser.add_argument(
        "--inventario",
        action="store_true",
        help="Lista classificação e encerra",
    )
    args = parser.parse_args()

    pasta = Path(args.pasta).expanduser()
    if not pasta.exists():
        log.error("Pasta não encontrada: %s", pasta)
        return 1

    bot_ged.PASTA_GED_TRATADO = pasta

    grupos = _classificar_arquivos(pasta)
    log.info("Pasta: %s", pasta)
    log.info(
        "Inventário: %d bruto(s), %d semanal(is), %d mensal(is), %d diurno(s), %d outro(s)",
        len(grupos["brutos"]),
        len(grupos["semanais_tratados"]),
        len(grupos["mensais"]),
        len(grupos["diurnos"]),
        len(grupos["outros"]),
    )
    for chave, itens in grupos.items():
        for item in itens:
            log.info("  [%s] %s", chave, item.name)

    if args.inventario:
        return 0

    erros = 0
    if not args.somente_consolidar:
        try:
            tratar_brutos(pasta, args.modo)
        except Exception:
            erros += 1

    try:
        _restaurar_parquet_de_csv_tratado(pasta)
    except Exception as exc:
        log.error("Restauração de parquets falhou: %s", exc)
        erros += 1

    meses = [m.strip() for m in args.meses.split(",") if m.strip()] or None
    try:
        consolidados = consolidar_legado(pasta, meses=meses)
        if consolidados:
            log.info("Consolidação gerou %d mensal(is)", len(consolidados))
        n_csv = _limpar_csv_semanais_orfaos(pasta)
        if n_csv:
            log.info("Removidos %d CSV semanal(is) órfão(s)", n_csv)
    except Exception as exc:
        log.error("Consolidação falhou: %s", exc, exc_info=True)
        erros += 1

    grupos_final = _classificar_arquivos(pasta)
    if grupos_final["brutos"]:
        log.warning(
            "Ainda há %d arquivo(s) bruto(s): %s",
            len(grupos_final["brutos"]),
            [p.name for p in grupos_final["brutos"]],
        )
        erros += 1

    return 0 if erros == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
