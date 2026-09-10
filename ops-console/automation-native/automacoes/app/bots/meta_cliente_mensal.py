"""Persistência e consulta de meta mensal por workflow (ledger + snapshot).

A meta é configurada por cliente em Categoria.xlsx e replicada para cada workflow do cliente.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.bots import replicacao_aud_planning as rap
from app.config import (
    COLUNA_CONFIG_CLIENTE,
    COLUNA_CONFIG_META_CLIENTE,
    COLUNA_CONFIG_WORKFLOW,
    META_CLIENTE_ANO_MES_REF_DEFAULT,
    META_CLIENTE_LEDGER_ARQUIVO,
    META_CLIENTE_MENSAL_ARQUIVO,
    PASTA_REPLICACAO_AUD_D1_CONFIG,
)

log = logging.getLogger("robots.meta_cliente_mensal")

_normalizar_workflow = rap._normalizar_workflow
_as_int = rap._as_int

_LEDGER_COLS = [
    "ano_mes",
    "workflow",
    "cliente",
    "run_id",
    "data_execucao",
    "protocolos",
    "origem",
    "observacao",
]
_SNAPSHOT_COLS = [
    "ano_mes",
    "workflow",
    "cliente",
    "meta_mensal",
    "consumo_acumulado",
    "headroom",
    "atualizado_em",
]


def _chave_workflow_ledger(df: pd.DataFrame) -> pd.Series:
    """Chave normalizada: coluna workflow (novo) ou cliente (legado)."""
    if "workflow" in df.columns:
        wf = df["workflow"].astype(str).str.strip()
        if wf.ne("").any():
            return wf.map(_normalizar_workflow)
    return df["cliente"].map(_normalizar_workflow)

def _resolver_config_base(settings: Optional[dict] = None) -> Path:
    settings = settings or {}
    base = str(settings.get("replicacao_config_base", "") or "").strip()
    if base:
        return Path(base)
    return PASTA_REPLICACAO_AUD_D1_CONFIG


def caminho_ledger(settings: Optional[dict] = None) -> Path:
    return _resolver_config_base(settings) / META_CLIENTE_LEDGER_ARQUIVO


def caminho_snapshot(settings: Optional[dict] = None) -> Path:
    return _resolver_config_base(settings) / META_CLIENTE_MENSAL_ARQUIVO


def ano_mes_de_data(data: Optional[datetime] = None) -> str:
    """Chave mensal YYYY-MM baseada na data de execução."""
    data = data or datetime.now()
    return data.strftime("%Y-%m")


def resolver_ano_mes(
    settings: Optional[dict] = None,
    *,
    data_exec: Optional[datetime] = None,
    data_referencia_d1: Optional[datetime] = None,
) -> str:
    """Resolve chave mensal conforme setting meta_cliente_ano_mes_ref."""
    settings = settings or {}
    ref = str(settings.get("meta_cliente_ano_mes_ref", META_CLIENTE_ANO_MES_REF_DEFAULT) or META_CLIENTE_ANO_MES_REF_DEFAULT)
    if ref == "data_referencia_d1" and data_referencia_d1 is not None:
        return data_referencia_d1.strftime("%Y-%m")
    return ano_mes_de_data(data_exec)


def _ler_csv_semicolon(path: Path, cols: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    except Exception as exc:
        log.warning("Falha ao ler %s: %s", path.name, exc)
        return pd.DataFrame(columns=cols)
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols].copy()


def _gravar_csv_semicolon(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def carregar_ledger(settings: Optional[dict] = None) -> pd.DataFrame:
    return _ler_csv_semicolon(caminho_ledger(settings), _LEDGER_COLS)


def carregar_snapshot(settings: Optional[dict] = None) -> pd.DataFrame:
    return _ler_csv_semicolon(caminho_snapshot(settings), _SNAPSHOT_COLS)


def _fonte_banco_ativa(settings: Optional[dict] = None) -> bool:
    try:
        from app.bots.replicacao_d1_db_bridge import is_fonte_banco_ativa

        return is_fonte_banco_ativa(settings)
    except Exception:
        return False


def carregar_consumo_meta_mensal(
    ano_mes: str,
    settings: Optional[dict] = None,
) -> Dict[str, int]:
    """Retorna consumo acumulado por workflow (_wf_key) no mês."""
    if _fonte_banco_ativa(settings):
        from app.bots.replicacao_d1_db_bridge import carregar_consumo_meta_mensal_db

        return carregar_consumo_meta_mensal_db(str(ano_mes))

    ledger = carregar_ledger(settings)
    if ledger.empty:
        return {}
    sub = ledger[ledger["ano_mes"].astype(str) == str(ano_mes)]
    if sub.empty:
        return {}
    sub = sub.copy()
    sub["protocolos"] = pd.to_numeric(sub["protocolos"], errors="coerce").fillna(0).astype(int)
    sub["_wf_key"] = _chave_workflow_ledger(sub)
    agg = sub.groupby("_wf_key", as_index=False)["protocolos"].sum()
    return {str(row["_wf_key"]): int(row["protocolos"]) for _, row in agg.iterrows()}


def carregar_metas_por_cliente(df_categorias: pd.DataFrame) -> Dict[str, int]:
    """Mapa _cli_key -> meta mensal (fonte em Categoria.xlsx, por cliente)."""
    metas: Dict[str, int] = {}
    if df_categorias is None or df_categorias.empty:
        return metas
    col = COLUNA_CONFIG_META_CLIENTE
    if col not in df_categorias.columns:
        return metas
    for _, row in df_categorias.iterrows():
        cli_key = str(row.get("_cli_key", "") or "")
        if not cli_key:
            cli_key = _normalizar_workflow(str(row.get(COLUNA_CONFIG_CLIENTE, "") or ""))
        if not cli_key:
            continue
        val = pd.to_numeric(row.get(col), errors="coerce")
        if pd.isna(val) or float(val) < 0:
            continue
        metas[cli_key] = int(round(float(val)))
    return metas


def carregar_metas_por_workflow(df_config: pd.DataFrame) -> Dict[str, int]:
    """Mapa _wf_key -> meta mensal (coluna herdada do cliente no config)."""
    metas: Dict[str, int] = {}
    if df_config is None or df_config.empty:
        return metas
    col = COLUNA_CONFIG_META_CLIENTE
    if col not in df_config.columns:
        return metas
    for _, row in df_config.iterrows():
        wf_key = _normalizar_workflow(str(row.get(COLUNA_CONFIG_WORKFLOW, "") or ""))
        if not wf_key:
            continue
        val = pd.to_numeric(row.get(col), errors="coerce")
        if pd.isna(val) or float(val) < 0:
            continue
        metas[wf_key] = int(round(float(val)))
    return metas


def expandir_metas_cliente_para_workflows(
    df_workflows: pd.DataFrame,
    df_categorias: pd.DataFrame,
) -> Dict[str, int]:
    """Propaga meta do cliente (Categoria) para cada workflow do mapa/config."""
    metas_cli = carregar_metas_por_cliente(df_categorias)
    metas_wf: Dict[str, int] = {}
    if df_workflows is None or df_workflows.empty:
        return metas_wf
    for _, row in df_workflows.iterrows():
        wf_key = _normalizar_workflow(str(row.get(COLUNA_CONFIG_WORKFLOW, "") or ""))
        cli_key = _normalizar_workflow(str(row.get(COLUNA_CONFIG_CLIENTE, "") or ""))
        if not wf_key or cli_key not in metas_cli:
            continue
        metas_wf[wf_key] = metas_cli[cli_key]
    return metas_wf


def carregar_headroom_meta_mensal(
    ano_mes: str,
    metas: Dict[str, int],
    settings: Optional[dict] = None,
    *,
    sem_meta_ilimitado: bool = True,
) -> Dict[str, int]:
    """
    Headroom por _wf_key para redistribuição.
    Workflows sem meta configurada recebem headroom ilimitado (valor alto).
    """
    consumo = carregar_consumo_meta_mensal(ano_mes, settings=settings)
    ilimitado = 10**9 if sem_meta_ilimitado else 0
    chaves = set(metas) | set(consumo)
    headroom: Dict[str, int] = {}
    for wf in chaves:
        meta = metas.get(wf)
        if meta is None:
            headroom[wf] = ilimitado
            continue
        headroom[wf] = max(0, int(meta) - int(consumo.get(wf, 0)))
    return headroom


def run_id_registrado_no_ledger(
    run_id: str,
    settings: Optional[dict] = None,
    *,
    origem: str = "confirmado",
) -> List[str]:
    """Retorna lista de ano_mes em que o run_id possui linhas no ledger."""
    if not run_id:
        return []
    ledger = carregar_ledger(settings)
    if ledger.empty:
        return []
    mask = (
        (ledger["run_id"].astype(str) == str(run_id))
        & (ledger["origem"].astype(str) == str(origem))
    )
    if not mask.any():
        return []
    return sorted(ledger.loc[mask, "ano_mes"].astype(str).unique().tolist())


def _remover_linhas_run_ledger(
    ledger: pd.DataFrame,
    run_id: str,
    *,
    origem: Optional[str] = "confirmado",
    ano_mes: Optional[str] = None,
) -> tuple[pd.DataFrame, List[str]]:
    if ledger.empty or not run_id:
        return ledger, []
    mask = ledger["run_id"].astype(str) == str(run_id)
    if origem is not None:
        mask &= ledger["origem"].astype(str) == str(origem)
    if ano_mes is not None:
        mask &= ledger["ano_mes"].astype(str) == str(ano_mes)
    meses = sorted(ledger.loc[mask, "ano_mes"].astype(str).unique().tolist())
    return ledger.loc[~mask].copy(), meses


def remover_consumo_meta_run(
    run_id: str,
    settings: Optional[dict] = None,
    *,
    origem: str = "confirmado",
    ano_mes: Optional[str] = None,
) -> dict:
    """Remove linhas do ledger para run_id e recalcula snapshot dos meses afetados."""
    resultado = {"run_id": run_id, "removido": False, "meses": [], "linhas": 0}
    if not run_id:
        return resultado
    path = caminho_ledger(settings)
    ledger = carregar_ledger(settings)
    antes = len(ledger)
    ledger, meses = _remover_linhas_run_ledger(ledger, run_id, origem=origem, ano_mes=ano_mes)
    removidas = antes - len(ledger)
    if removidas <= 0:
        return resultado
    _gravar_csv_semicolon(path, ledger)
    for mes in meses:
        recalcular_snapshot_mensal(mes, settings=settings)
    resultado["removido"] = True
    resultado["meses"] = meses
    resultado["linhas"] = removidas
    log.info(
        "Ledger meta cliente: removido run=%s | %d linha(s) | meses=%s",
        run_id,
        removidas,
        ",".join(meses),
    )
    return resultado


def registrar_consumo_meta_run(
    ano_mes: str,
    run_id: str,
    consumo_por_workflow: Dict[str, int],
    *,
    cliente_por_workflow: Optional[Dict[str, str]] = None,
    origem: str = "confirmado",
    observacao: str = "",
    data_execucao: Optional[datetime] = None,
    settings: Optional[dict] = None,
    substituir: bool = True,
) -> bool:
    """
    Grava consumo no ledger e recalcula snapshot.
    Idempotente por (ano_mes, run_id, origem): substitui linhas anteriores do run.
    """
    if not run_id:
        return False

    if _fonte_banco_ativa(settings):
        from app.bots.replicacao_d1_db_bridge import registrar_consumo_meta_run_db

        settings = settings or {}
        snap = settings.get("_execution_snapshot") or {}
        return registrar_consumo_meta_run_db(
            competencia=str(ano_mes),
            run_id=str(run_id),
            consumo_por_workflow=consumo_por_workflow or {},
            cliente_por_workflow=cliente_por_workflow,
            origem=str(origem),
            observacao=str(observacao or ""),
            data_execucao=data_execucao,
            snapshot_hash=str(snap.get("config_hash") or settings.get("config_hash") or ""),
            config_version=settings.get("config_version") or snap.get("config_version"),
        )

    path = caminho_ledger(settings)
    ledger = carregar_ledger(settings)
    if substituir:
        ledger, _ = _remover_linhas_run_ledger(ledger, run_id, origem=origem, ano_mes=ano_mes)

    agora = (data_execucao or datetime.now()).isoformat(timespec="seconds")
    cliente_por_workflow = cliente_por_workflow or {}
    linhas = []
    for wf_key, qtd in (consumo_por_workflow or {}).items():
        qtd = _as_int(qtd)
        if qtd <= 0:
            continue
        linhas.append(
            {
                "ano_mes": str(ano_mes),
                "workflow": str(wf_key),
                "cliente": str(cliente_por_workflow.get(wf_key, "") or ""),
                "run_id": str(run_id),
                "data_execucao": agora,
                "protocolos": str(qtd),
                "origem": str(origem),
                "observacao": str(observacao or ""),
            }
        )
    if not linhas:
        if substituir:
            _gravar_csv_semicolon(path, ledger)
            recalcular_snapshot_mensal(ano_mes, settings=settings)
        return False

    ledger = pd.concat([ledger, pd.DataFrame(linhas)], ignore_index=True)
    _gravar_csv_semicolon(path, ledger)
    log.info(
        "Ledger meta cliente: %d linha(s) | run=%s | mês=%s | origem=%s | substituir=%s",
        len(linhas),
        run_id,
        ano_mes,
        origem,
        substituir,
    )
    recalcular_snapshot_mensal(ano_mes, settings=settings)
    return True


def recalcular_snapshot_mensal(
    ano_mes: str,
    metas: Optional[Dict[str, int]] = None,
    settings: Optional[dict] = None,
) -> Path:
    """Atualiza snapshot do mês a partir do ledger."""
    if _fonte_banco_ativa(settings):
        path = caminho_snapshot(settings)
        log.debug("fonte_banco_ativa: recalcular_snapshot_mensal ignorado (ledger no PostgreSQL)")
        return path

    path = caminho_snapshot(settings)
    ledger = carregar_ledger(settings)
    snapshot = carregar_snapshot(settings)
    snapshot = snapshot[snapshot["ano_mes"].astype(str) != str(ano_mes)]

    consumo = carregar_consumo_meta_mensal(ano_mes, settings=settings)
    if metas is None:
        metas = {}
        if consumo:
            for wf in consumo:
                metas.setdefault(wf, 0)

    chaves = set(metas) | set(consumo)
    agora = datetime.now().isoformat(timespec="seconds")
    linhas = []
    for wf in sorted(chaves):
        meta_val = int(metas.get(wf, 0))
        cons = int(consumo.get(wf, 0))
        linhas.append(
            {
                "ano_mes": str(ano_mes),
                "workflow": str(wf),
                "cliente": "",
                "meta_mensal": str(meta_val),
                "consumo_acumulado": str(cons),
                "headroom": str(max(0, meta_val - cons)),
                "atualizado_em": agora,
            }
        )
    if linhas:
        snapshot = pd.concat([snapshot, pd.DataFrame(linhas)], ignore_index=True)
    _gravar_csv_semicolon(path, snapshot)
    return path


def carregar_snapshot_mes(
    ano_mes: str,
    settings: Optional[dict] = None,
) -> List[dict]:
    """Retorna linhas do snapshot para um mês."""
    snap = carregar_snapshot(settings)
    if snap.empty:
        return []
    sub = snap[snap["ano_mes"].astype(str) == str(ano_mes)]
    return [_linha_snapshot_json_safe(row) for row in sub.to_dict("records")]


def _json_safe_cell(val: Any) -> Any:
    """Evita NaN/NA do pandas no JSON (quebra o parse no browser)."""
    try:
        if val is None or pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    if hasattr(val, "item") and not isinstance(val, (bytes, str)):
        try:
            val = val.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(val, float) and (val != val):  # NaN
        return ""
    return val


def _linha_snapshot_json_safe(row: dict) -> dict:
    return {str(k): _json_safe_cell(v) for k, v in (row or {}).items()}


def _parse_ano_mes(ano_mes: str) -> tuple[int, int] | None:
    text = str(ano_mes or "").strip()
    if len(text) < 7 or text[4] != "-":
        return None
    try:
        year = int(text[:4])
        month = int(text[5:7])
    except ValueError:
        return None
    if month < 1 or month > 12:
        return None
    return year, month


def _dias_uteis_no_mes(year: int, month: int) -> list[date]:
    """Dias úteis (seg–sex) do mês civil."""
    last = monthrange(year, month)[1]
    out: list[date] = []
    for day in range(1, last + 1):
        d = date(year, month, day)
        if d.weekday() < 5:
            out.append(d)
    return out


def _data_referencia_projecao(ano_mes: str, hoje: Optional[date] = None) -> date:
    """Último dia já 'passado' no mês para contagem de decorridos."""
    parsed = _parse_ano_mes(ano_mes)
    hoje = hoje or date.today()
    if not parsed:
        return hoje
    year, month = parsed
    last = date(year, month, monthrange(year, month)[1])
    first = date(year, month, 1)
    if hoje < first:
        return first - timedelta(days=1)
    if hoje > last:
        return last
    return hoje


def _dias_com_execucao_ledger(
    ano_mes: str,
    settings: Optional[dict] = None,
) -> set[date]:
    """Datas civis com consumo > 0 no ledger do mês (via data_execucao)."""
    ledger = carregar_ledger(settings)
    if ledger.empty:
        return set()
    sub = ledger[ledger["ano_mes"].astype(str) == str(ano_mes)].copy()
    if sub.empty:
        return set()
    sub["protocolos"] = pd.to_numeric(sub["protocolos"], errors="coerce").fillna(0).astype(int)
    sub = sub[sub["protocolos"] > 0]
    out: set[date] = set()
    for raw in sub["data_execucao"].astype(str):
        text = (raw or "").strip()
        if not text:
            continue
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            out.add(dt.date())
        except ValueError:
            try:
                out.add(datetime.strptime(text[:10], "%Y-%m-%d").date())
            except ValueError:
                continue
    return out


def calcular_projecao_fim_mes(
    ano_mes: str,
    linhas: Optional[List[dict]] = None,
    settings: Optional[dict] = None,
    *,
    hoje: Optional[date] = None,
) -> Dict[str, Any]:
    """Estima volume até o fim do mês: ritmo observado × dias úteis restantes.

    Os limites de balanceamento não limitam nem compõem esta projeção.
    Ritmo = consumo_total / dias_com_execucao (fallback: dias úteis decorridos).
    """
    settings = settings or {}
    rows = list(linhas if linhas is not None else carregar_snapshot_mes(ano_mes, settings=settings))
    consumo_total = sum(int(pd.to_numeric(r.get("consumo_acumulado", 0), errors="coerce") or 0) for r in rows)

    parsed = _parse_ano_mes(ano_mes)
    ref = _data_referencia_projecao(ano_mes, hoje=hoje)
    if parsed:
        year, month = parsed
        uteis = _dias_uteis_no_mes(year, month)
        dias_uteis_mes = len(uteis)
        dias_uteis_decorridos = sum(1 for d in uteis if d <= ref)
        dias_uteis_restantes = max(0, dias_uteis_mes - dias_uteis_decorridos)
    else:
        dias_uteis_mes = 0
        dias_uteis_decorridos = 0
        dias_uteis_restantes = 0

    dias_exec = _dias_com_execucao_ledger(ano_mes, settings=settings)
    dias_com_execucao = len(dias_exec)
    base_ritmo = dias_com_execucao if dias_com_execucao > 0 else dias_uteis_decorridos
    ritmo_diario = (float(consumo_total) / base_ritmo) if base_ritmo > 0 else 0.0
    projecao_ritmo = int(round(consumo_total + ritmo_diario * dias_uteis_restantes))

    enriquecidas: List[dict] = []
    for r in rows:
        cons = int(pd.to_numeric(r.get("consumo_acumulado", 0), errors="coerce") or 0)
        ritmo_wf = (float(cons) / base_ritmo) if base_ritmo > 0 else 0.0
        proj_wf = int(round(cons + ritmo_wf * dias_uteis_restantes))
        row = _linha_snapshot_json_safe(dict(r))
        row["ritmo_diario"] = round(ritmo_wf, 1)
        row["projecao_fim_mes"] = int(proj_wf)
        enriquecidas.append(row)

    return {
        "ano_mes": str(ano_mes),
        "consumo_total": consumo_total,
        "dias_uteis_mes": dias_uteis_mes,
        "dias_uteis_decorridos": dias_uteis_decorridos,
        "dias_uteis_restantes": dias_uteis_restantes,
        "dias_com_execucao": dias_com_execucao,
        "ritmo_diario": round(ritmo_diario, 1),
        "projecao_ritmo": projecao_ritmo,
        "projecao_fim_mes": projecao_ritmo,
        "metodo": (
            "Ritmo = consumo ÷ dias com execução (ou dias úteis decorridos); "
            "projeção = consumo + ritmo × dias úteis restantes (seg–sex). "
            "Limites de balanceamento não participam do cálculo."
        ),
        "clientes": enriquecidas,
    }


def aplicar_ajuste_manual_consumo(
    ano_mes: str,
    workflow: str,
    consumo_acumulado: int,
    settings: Optional[dict] = None,
    *,
    cliente: str = "",
) -> bool:
    """Substitui consumo do workflow no mês por valor informado (ledger origem=manual)."""
    wf_key = _normalizar_workflow(workflow)
    if not wf_key or consumo_acumulado < 0:
        return False
    path = caminho_ledger(settings)
    ledger = carregar_ledger(settings)
    norm = _chave_workflow_ledger(ledger).astype(str)
    mask = (ledger["ano_mes"].astype(str) == str(ano_mes)) & (norm == wf_key)
    ledger = ledger.loc[~mask].copy()
    if consumo_acumulado > 0:
        agora = datetime.now().isoformat(timespec="seconds")
        run_id = f"manual_{ano_mes}_{wf_key}"
        linha = pd.DataFrame(
            [
                {
                    "ano_mes": str(ano_mes),
                    "workflow": wf_key,
                    "cliente": str(cliente or ""),
                    "run_id": run_id,
                    "data_execucao": agora,
                    "protocolos": str(int(consumo_acumulado)),
                    "origem": "manual",
                    "observacao": "ajuste_manual",
                }
            ]
        )
        ledger = pd.concat([ledger, linha], ignore_index=True)
    _gravar_csv_semicolon(path, ledger)
    recalcular_snapshot_mensal(ano_mes, settings=settings)
    return True


def calcular_consumo_por_workflow_resumo(
    resumo: list[dict],
    *,
    apenas_salvo_ok: bool = False,
    workflows_ok: Optional[set] = None,
) -> Dict[str, int]:
    """Agrega Protocolos Salvos por workflow a partir das linhas de resumo."""
    totais: Dict[str, int] = {}
    workflows_ok = workflows_ok or set()
    for row in resumo:
        if str(row.get("Workflow", "") or "") == "TOTAL":
            continue
        wf = str(row.get("Workflow", "") or "")
        if apenas_salvo_ok and workflows_ok and wf not in workflows_ok:
            continue
        wf_key = _normalizar_workflow(wf)
        if not wf_key:
            continue
        qtd = _as_int(row.get("Protocolos Salvos", 0))
        if qtd <= 0:
            continue
        totais[wf_key] = totais.get(wf_key, 0) + qtd
    return totais


def _cliente_por_workflow_resumo(resumo: list[dict]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in resumo:
        wf = _normalizar_workflow(str(row.get("Workflow", "") or ""))
        if not wf:
            continue
        cli = str(row.get(COLUNA_CONFIG_CLIENTE, "") or "").strip()
        if cli:
            out[wf] = cli
    return out


def calcular_consumo_por_cliente_resumo(
    resumo: list[dict],
    *,
    apenas_salvo_ok: bool = False,
    workflows_ok: Optional[set] = None,
) -> Dict[str, int]:
    """Compat: delega para consumo por workflow."""
    return calcular_consumo_por_workflow_resumo(
        resumo, apenas_salvo_ok=apenas_salvo_ok, workflows_ok=workflows_ok
    )


def calcular_consumo_por_workflow_confirmado(
    plano,
    estado: Optional[dict],
) -> Dict[str, int]:
    """Consumo oficial: protocolos salvos ou quantidade replicada por workflow."""
    resumo = list(getattr(plano, "resumo", None) or [])
    workflows_estado = (estado or {}).get("workflows", {})
    ok_status = {"SALVO_OK", "SEM_ALTERACAO"}
    ok = {
        wf
        for wf, info in workflows_estado.items()
        if str(info.get("status", "") or "") in ok_status
    }
    totais = calcular_consumo_por_workflow_resumo(
        resumo,
        apenas_salvo_ok=True,
        workflows_ok=ok,
    )
    qtd_map = getattr(plano, "qtd_por_workflow", None) or {}
    for wf, info in workflows_estado.items():
        if str(info.get("status", "") or "") not in ok_status:
            continue
        if str(info.get("modo", "") or "") != "qtd":
            continue
        wf_key = _normalizar_workflow(wf)
        if not wf_key:
            continue
        qtd = _as_int(info.get("qtd_aplicada") or info.get("qtd_calculada") or qtd_map.get(wf, 0))
        if qtd <= 0:
            continue
        totais[wf_key] = qtd
    return totais


def calcular_consumo_por_cliente_confirmado(
    plano,
    estado: Optional[dict],
) -> Dict[str, int]:
    """Compat: consumo por workflow."""
    return calcular_consumo_por_workflow_confirmado(plano, estado)


def sincronizar_consumo_meta_run(
    plano,
    estado: Optional[dict],
    settings: Optional[dict] = None,
    *,
    data_execucao: Optional[datetime] = None,
    data_referencia_d1: Optional[datetime] = None,
) -> bool:
    """Atualiza consumo confirmado do run (substitui linha agregada — idempotente)."""
    run_id = str(getattr(plano, "run_id", "") or "")
    if not run_id:
        return False
    data_exec = data_execucao or datetime.now()
    ano_mes = resolver_ano_mes(
        settings,
        data_exec=data_exec,
        data_referencia_d1=data_referencia_d1,
    )
    consumo = calcular_consumo_por_workflow_confirmado(plano, estado)
    clientes = _cliente_por_workflow_resumo(list(getattr(plano, "resumo", None) or []))
    return registrar_consumo_meta_run(
        ano_mes,
        run_id,
        consumo,
        cliente_por_workflow=clientes,
        origem="confirmado",
        data_execucao=data_exec,
        settings=settings,
        substituir=True,
    )


def registrar_consumo_meta_plano_concluido(
    plano,
    estado: Optional[dict],
    settings: Optional[dict] = None,
    *,
    data_execucao: Optional[datetime] = None,
) -> bool:
    """Registra consumo confirmado do run no ledger mensal."""
    data_ref = None
    data_ref_fmt = str(getattr(plano, "data_referencia_d1_fmt", "") or "")
    if data_ref_fmt:
        try:
            data_ref = datetime.strptime(data_ref_fmt, "%d/%m/%Y")
        except ValueError:
            data_ref = None
    return sincronizar_consumo_meta_run(
        plano,
        estado,
        settings=settings,
        data_execucao=data_execucao,
        data_referencia_d1=data_ref,
    )
