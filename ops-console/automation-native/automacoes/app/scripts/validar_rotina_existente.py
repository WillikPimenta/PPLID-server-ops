"""Valida a refatoração do bot rotina reprocessando arquivos brutos e comparando com os existentes.

Modo offline (padrão): reprocessa monitor/prod/unificado em pasta temporária e compara
com os parquets já salvos no SharePoint — sem sobrescrever produção.

Modo selenium (opcional): executa uma tarefa do bot com credenciais informadas no terminal.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import logging
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pandas as pd

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.config import (  # noqa: E402
    PASTA_MONITOR_BRUTA,
    PASTA_MONITOR_CONFER_TRATADO,
    PASTA_MONITOR_CONFER_UNIFICADO,
    PASTA_MONITOR_TRATADO,
    PASTA_MONITOR_UNIFICADO_BOTS,
    PASTA_PROD_TRATADO,
    PASTA_PRODUTIVIDADE_D1_BRUTA,
    PREFIXO_MONITOR_BRFLOW_BRUTO,
    PREFIXO_MONITOR_CONFER_TRATADO,
    PREFIXO_MONITOR_CONFER_UNIFICADO,
    PREFIXO_MONITOR_TRATADO,
    PREFIXO_PROD_D1,
    PREFIXO_PROD_TRATADA,
    PLAN_IDF_SERASA_BOTS,
)
from app.bots.rotina.tasks.monitor import (  # noqa: E402
    _pretratar_monitor_verifica_usuario,
    tratar_arquivo,
)
from app.bots.rotina.tasks.produtividade import tratar_produtividade  # noqa: E402
from app.bots.rotina.tasks.unificados import _juntar_monitor_tratado_com_monitor_confer_dia  # noqa: E402
from app.core.credentials import set_credentials  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


@dataclass
class ComparacaoResultado:
    nome: str
    data_ref: str
    existente: Path | None
    gerado: Path | None
    ok: bool
    linhas_existente: int = 0
    linhas_gerado: int = 0
    colunas_extras_existente: list[str] = field(default_factory=list)
    colunas_extras_gerado: list[str] = field(default_factory=list)
    hash_existente: str = ""
    hash_gerado: str = ""
    detalhe: str = ""


def _prompt_credenciais(nao_interativo: bool) -> tuple[str, str]:
    """Solicita matrícula e senha no terminal."""
    if nao_interativo:
        matricula = (
            os.getenv("NIVEL_USER")
            or os.getenv("MONITOR_USER")
            or os.getenv("OKTA_USER")
            or ""
        )
        senha = (
            os.getenv("NIVEL_PASS")
            or os.getenv("MONITOR_PASS")
            or os.getenv("OKTA_PASS")
            or ""
        )
        if matricula and senha:
            log.info("Credenciais carregadas das variáveis de ambiente.")
            return matricula, senha
        log.warning("Modo não interativo sem credenciais — apenas validação offline.")
        return "", ""

    print()
    print("=" * 60)
    print("Credenciais BRFlow (necessárias apenas para teste Selenium)")
    print("=" * 60)
    matricula = input("Matrícula: ").strip()
    senha = getpass.getpass("Senha: ")
    return matricula, senha


def _resolver_monitor_bruto(data_ref: str) -> Path | None:
    for ext in (".parquet", ".csv"):
        caminho = PASTA_MONITOR_BRUTA / f"{PREFIXO_MONITOR_BRFLOW_BRUTO}{data_ref}{ext}"
        if caminho.exists():
            return caminho
    return None


def _resolver_prod_bruto(data_ref: str) -> Path | None:
    caminho = PASTA_PRODUTIVIDADE_D1_BRUTA / f"{PREFIXO_PROD_D1}{data_ref}.parquet"
    return caminho if caminho.exists() else None


def _listar_datas_disponiveis(limite: int | None, data_inicio: str | None, data_fim: str | None) -> list[str]:
    pasta = Path(PASTA_MONITOR_CONFER_UNIFICADO)
    datas = sorted(
        p.stem.replace("monitor-unificado_", "")
        for p in pasta.glob("monitor-unificado_*.parquet")
    )
    if data_inicio:
        datas = [d for d in datas if d >= data_inicio]
    if data_fim:
        datas = [d for d in datas if d <= data_fim]
    if limite is not None and limite > 0:
        datas = datas[-limite:]
    return datas


def _df_fingerprint(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return hashlib.md5(b"<empty>").hexdigest()
    cols = sorted(df.columns)
    normalizado = df[cols].copy()
    for coluna in cols:
        normalizado[coluna] = normalizado[coluna].astype(str).fillna("<NA>")
    normalizado = normalizado.sort_values(by=cols, kind="stable").reset_index(drop=True)
    payload = normalizado.to_csv(index=False).encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def _comparar_parquets(nome: str, data_ref: str, existente: Path | None, gerado: Path | None) -> ComparacaoResultado:
    resultado = ComparacaoResultado(
        nome=nome,
        data_ref=data_ref,
        existente=existente,
        gerado=gerado,
        ok=False,
    )

    if existente is None or not existente.exists():
        resultado.detalhe = "Arquivo existente ausente"
        return resultado
    if gerado is None or not gerado.exists():
        resultado.detalhe = "Arquivo gerado ausente"
        return resultado

    df_existente = pd.read_parquet(existente)
    df_gerado = pd.read_parquet(gerado)

    resultado.linhas_existente = len(df_existente)
    resultado.linhas_gerado = len(df_gerado)
    resultado.colunas_extras_existente = sorted(set(df_existente.columns) - set(df_gerado.columns))
    resultado.colunas_extras_gerado = sorted(set(df_gerado.columns) - set(df_existente.columns))
    resultado.hash_existente = _df_fingerprint(df_existente)
    resultado.hash_gerado = _df_fingerprint(df_gerado)

    if resultado.hash_existente == resultado.hash_gerado:
        resultado.ok = True
        resultado.detalhe = "Conteúdo idêntico"
    elif resultado.linhas_existente != resultado.linhas_gerado:
        resultado.detalhe = f"Divergência de linhas ({resultado.linhas_existente} vs {resultado.linhas_gerado})"
    elif resultado.colunas_extras_existente or resultado.colunas_extras_gerado:
        resultado.detalhe = "Divergência de colunas"
    else:
        resultado.detalhe = "Conteúdo diferente (mesmas linhas/colunas)"

    return resultado


@contextmanager
def _patch_pastas(**substituicoes: Path) -> Iterator[None]:
    alvos = [
        "app.config.paths",
        "app.bots.rotina.tasks.monitor",
        "app.bots.rotina.tasks.produtividade",
        "app.bots.rotina.tasks.unificados",
    ]
    managers = [patch(f"{alvo}.{nome}", valor) for alvo in alvos for nome, valor in substituicoes.items()]
    with patch.multiple("app.config", **substituicoes):
        for manager in managers:
            manager.start()
        try:
            yield
        finally:
            for manager in reversed(managers):
                manager.stop()


def _copiar_entrada(origem: Path, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino)
    return destino


def validar_dia(data_ref: str, temp_base: Path) -> list[ComparacaoResultado]:
    resultados: list[ComparacaoResultado] = []
    monitor_bruto = _resolver_monitor_bruto(data_ref)
    prod_bruto = _resolver_prod_bruto(data_ref)

    if monitor_bruto is None or prod_bruto is None:
        motivo = []
        if monitor_bruto is None:
            motivo.append("monitor bruto")
        if prod_bruto is None:
            motivo.append("prod bruto")
        log.warning("[%s] Ignorado — faltam: %s", data_ref, ", ".join(motivo))
        return resultados

    temp_bruto = _copiar_entrada(
        monitor_bruto,
        temp_base / "entrada" / monitor_bruto.name,
    )
    temp_prod = _copiar_entrada(
        prod_bruto,
        temp_base / "entrada" / prod_bruto.name,
    )

    temp_monitor_tratado = temp_base / "saida" / "monitor_tratado"
    temp_prod_tratado = temp_base / "saida" / "prod_tratado"
    temp_monitor_unificado = temp_base / "saida" / "monitor_unificado"
    temp_monitor_unificado_espelho = temp_base / "saida" / "monitor_unificado_espelho"

    data_obj = datetime.strptime(data_ref, "%Y%m%d")

    # Monitor tratado
    with _patch_pastas(PASTA_MONITOR_TRATADO=temp_monitor_tratado):
        _pretratar_monitor_verifica_usuario(str(temp_bruto))
        saida_monitor = tratar_arquivo(str(temp_bruto), str(temp_prod), data_obj)

    existente_monitor = PASTA_MONITOR_TRATADO / f"{PREFIXO_MONITOR_TRATADO}{data_ref}.parquet"
    gerado_monitor = Path(saida_monitor) if saida_monitor else temp_monitor_tratado / f"{PREFIXO_MONITOR_TRATADO}{data_ref}.parquet"
    resultados.append(_comparar_parquets("monitor_tratado", data_ref, existente_monitor, gerado_monitor))

    # Prod tratado
    with _patch_pastas(PASTA_PROD_TRATADO=temp_prod_tratado):
        saida_prod = tratar_produtividade(str(temp_prod))

    existente_prod = PASTA_PROD_TRATADO / f"{PREFIXO_PROD_TRATADA}{data_ref}.parquet"
    gerado_prod = Path(saida_prod) if saida_prod else temp_prod_tratado / f"{PREFIXO_PROD_TRATADA}{data_ref}.parquet"
    resultados.append(_comparar_parquets("prod_tratado", data_ref, existente_prod, gerado_prod))

    # Monitor unificado
    data_confer = datetime.strptime(data_ref, "%Y%m%d").strftime("%d%m%Y")
    confer_existe = (
        PASTA_MONITOR_CONFER_TRATADO / f"{PREFIXO_MONITOR_CONFER_TRATADO}{data_confer}.parquet"
    ).exists()

    if confer_existe:
        with _patch_pastas(
            PASTA_MONITOR_TRATADO=temp_monitor_tratado,
            PASTA_MONITOR_UNIFICADO_BOTS=temp_monitor_unificado,
            PASTA_MONITOR_CONFER_UNIFICADO=temp_monitor_unificado_espelho,
        ):
            saida_unificado = _juntar_monitor_tratado_com_monitor_confer_dia(data_ref)

        existente_unificado = PASTA_MONITOR_CONFER_UNIFICADO / f"{PREFIXO_MONITOR_CONFER_UNIFICADO}{data_ref}.parquet"
        gerado_unificado = (
            Path(saida_unificado)
            if saida_unificado
            else temp_monitor_unificado / f"{PREFIXO_MONITOR_CONFER_UNIFICADO}{data_ref}.parquet"
        )
        resultados.append(
            _comparar_parquets("monitor_unificado", data_ref, existente_unificado, gerado_unificado)
        )
    else:
        log.info("[%s] Sem confer-tratado — unificado não comparado", data_ref)

    return resultados


def _imprimir_relatorio(resultados: list[ComparacaoResultado]) -> int:
    ok = sum(1 for r in resultados if r.ok)
    divergencias = [r for r in resultados if not r.ok]

    print()
    print("=" * 70)
    print(f"Validação offline | comparações={len(resultados)} | ok={ok} | divergências={len(divergencias)}")
    print("=" * 70)

    for resultado in resultados:
        status = "OK" if resultado.ok else "DIFF"
        print(
            f"[{status}] {resultado.data_ref} | {resultado.nome} | "
            f"linhas={resultado.linhas_existente}/{resultado.linhas_gerado} | {resultado.detalhe}"
        )
        if not resultado.ok and (resultado.colunas_extras_existente or resultado.colunas_extras_gerado):
            if resultado.colunas_extras_existente:
                print(f"       colunas só no existente: {resultado.colunas_extras_existente}")
            if resultado.colunas_extras_gerado:
                print(f"       colunas só no gerado: {resultado.colunas_extras_gerado}")

    print("=" * 70)
    return 0 if not divergencias else 1


def _executar_selenium(matricula: str, senha: str, tarefa: str) -> int:
    from app.bots.bot_rotina import start

    log.info("Iniciando teste Selenium | tarefa=%s", tarefa)
    settings = {
        "matricula": matricula,
        "senha": senha,
        "executar_imediatamente": True,
        "rotina_tarefas": [tarefa],
    }
    set_credentials(matricula, senha)
    start(settings=settings)
    log.info("Teste Selenium finalizado.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida refatoração do bot rotina comparando reprocessamento com arquivos existentes",
    )
    parser.add_argument("--limite", type=int, default=5, help="Últimos N dias com unificado (padrão: 5)")
    parser.add_argument("--data-inicio", help="Filtrar a partir de YYYYMMDD")
    parser.add_argument("--data-fim", help="Filtrar até YYYYMMDD")
    parser.add_argument(
        "--selenium",
        action="store_true",
        help="Após validação offline, executar uma tarefa Selenium com as credenciais",
    )
    parser.add_argument(
        "--tarefa",
        default="monitor",
        help="Tarefa para teste Selenium (padrão: monitor)",
    )
    parser.add_argument(
        "--nao-interativo",
        action="store_true",
        help="Não pedir credenciais no terminal (usa variáveis de ambiente)",
    )
    parser.add_argument(
        "--somente-selenium",
        action="store_true",
        help="Pular validação offline e rodar apenas Selenium",
    )
    args = parser.parse_args()

    matricula, senha = _prompt_credenciais(args.nao_interativo)
    if matricula and senha:
        set_credentials(matricula, senha)
        os.environ["NIVEL_USER"] = matricula
        os.environ["NIVEL_PASS"] = senha

    if args.somente_selenium:
        if not matricula or not senha:
            log.error("Credenciais obrigatórias para --somente-selenium")
            return 1
        return _executar_selenium(matricula, senha, args.tarefa)

    datas = _listar_datas_disponiveis(args.limite, args.data_inicio, args.data_fim)
    if not datas:
        log.error("Nenhuma data encontrada em %s", PASTA_MONITOR_CONFER_UNIFICADO)
        return 1

    log.info("Validação offline | datas=%s | temp em subpasta de %s", len(datas), PLAN_IDF_SERASA_BOTS)

    todos_resultados: list[ComparacaoResultado] = []
    with tempfile.TemporaryDirectory(prefix="validacao_rotina_", dir=str(PLAN_IDF_SERASA_BOTS)) as temp_dir:
        temp_base = Path(temp_dir)
        for idx, data_ref in enumerate(datas, start=1):
            log.info("[%s/%s] Validando %s", idx, len(datas), data_ref)
            todos_resultados.extend(validar_dia(data_ref, temp_base / data_ref))

    codigo = _imprimir_relatorio(todos_resultados)

    if args.selenium:
        if not matricula or not senha:
            log.error("Credenciais obrigatórias para --selenium")
            return 1
        if not args.nao_interativo:
            confirma = input("\nExecutar teste Selenium agora? [s/N]: ").strip().lower()
            if confirma not in ("s", "sim", "y", "yes"):
                log.info("Teste Selenium cancelado pelo usuário.")
                return codigo
        return max(codigo, _executar_selenium(matricula, senha, args.tarefa))

    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
