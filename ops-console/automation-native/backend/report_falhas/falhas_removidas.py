# -*- coding: utf-8 -*-
"""Leitura e aplicação da aba Falhas removidas (log SharePoint tblFailuresRemoved)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from report_falhas.config_report import (
    COL_DATA_ANALISE,
    COL_MATRICULA,
    COL_PROTOCOLO,
    FALHAS_REMOVIDAS_SHEET_ALIASES,
    FALHAS_REMOVIDAS_TYPE_DELETE,
    FALHAS_REMOVIDAS_TYPE_EDIT,
)
from report_falhas.io.data_loader import (
    normalize_text,
    norm_matricula,
    norm_protocolo,
    safe_str,
    safe_to_datetime,
)
from report_falhas.matricula_utils import clean_matricula_unified, format_cenario_text
from report_falhas.utils_report import MESES_ABREV_BR


_removidas_resumo_df = pd.DataFrame()
_removidas_detalhe_df = pd.DataFrame()
_removidas_protocolos_removidos: set[str] = set()


def parse_tbga_json(raw: str) -> dict[str, str]:
    """Converte array JSON Header/Value em dict."""
    s = safe_str(raw)
    if not s:
        return {}
    try:
        data = json.loads(s)
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        header = safe_str(item.get("Header", item.get("header", "")))
        value = item.get("Value", item.get("value"))
        if value is None:
            value = ""
        else:
            value = safe_str(value)
        if header:
            out[header] = value
    return out


def extract_failure_fingerprint(tbga: dict[str, str]) -> dict[str, str]:
    """Extrai chaves de match a partir do dict TBGA."""
    prot = norm_protocolo(tbga.get("TBGA_PROTOCOLO", ""))
    falha_mat = safe_str(tbga.get("TBGA_APP_FALHA_MATRICULA", ""))
    audit_mat = safe_str(tbga.get("TBGA_APP_AUDITORIA_MATRICULA", ""))
    raw_mat = falha_mat if falha_mat else audit_mat
    mat, _ = clean_matricula_unified(raw_mat)
    mat_norm = norm_matricula(mat if mat else raw_mat)
    motivo = format_cenario_text(tbga.get("TBGA_APP_MOTIVO_FALHA", ""))
    return {
        "protocolo": prot,
        "matricula": mat if mat else raw_mat,
        "matricula_norm": mat_norm,
        "motivo": motivo,
        "motivo_norm": normalize_text(motivo),
        "etapa": safe_str(tbga.get("TBGA_APP_ETAPA", "")),
        "etapa_norm": normalize_text(tbga.get("TBGA_APP_ETAPA", "")),
        "cliente": safe_str(tbga.get("TBGA_CLIENTE", "")),
        "workflow": safe_str(tbga.get("TBGA_WORKFLOW", "")),
        "modulo": safe_str(tbga.get("Modulo", "")),
        "data_analise": safe_str(tbga.get("TBGA_APP_AUDITORIA_DATA", "")),
    }


def classify_removal_type(type_str: str) -> str:
    """Retorna 'delete', 'edit' ou 'unknown'."""
    t = normalize_text(type_str)
    if not t:
        return "unknown"
    if t in FALHAS_REMOVIDAS_TYPE_DELETE or any(x in t for x in ("exclus", "delete", "remoc")):
        return "delete"
    if t in FALHAS_REMOVIDAS_TYPE_EDIT or any(x in t for x in ("edicao", "edit", "alter", "change")):
        return "edit"
    return "unknown"


def _find_sheet_case_insensitive(path: str, aliases: list[str]) -> str | None:
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
        norm_alias = {normalize_text(a) for a in aliases}
        for s in xl.sheet_names:
            if normalize_text(str(s)) in norm_alias:
                return s
        for s in xl.sheet_names:
            ns = normalize_text(str(s))
            if "falhas removidas" in ns or "failuresremoved" in ns:
                return s
    except Exception:
        return None
    return None


def _normalize_removidas_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df.rename(columns=lambda c: safe_str(c), inplace=True)
    cmap = {normalize_text(str(c).strip()): str(c).strip() for c in df.columns}

    aliases = {
        "failuredetailjson": "FailureDetailJSON",
        "beforejson": "BeforeJSON",
        "dateremoved": "DateRemoved",
        "userremove": "UserRemove",
        "user/service": "UserRemove",
        "type": "Type",
        "deletemotive": "DeleteMotive",
        "delete/motivo": "DeleteMotive",
        "changemotive": "ChangeMotive",
        "change/motivo": "ChangeMotive",
        "id": "ID",
        "created": "Created",
        "created by": "Created By",
        "item type": "Item Type",
        "path": "Path",
    }

    rename_map = {}
    for k, target in aliases.items():
        if k in cmap and cmap[k] != target:
            rename_map[cmap[k]] = target
    if rename_map:
        df.rename(columns=rename_map, inplace=True)

    preferred = [
        "FailureDetailJSON", "BeforeJSON", "DateRemoved", "UserRemove", "Type",
        "DeleteMotive", "ChangeMotive", "ID", "Created", "Created By", "Item Type", "Path",
    ]
    for c in preferred:
        if c not in df.columns:
            df[c] = ""

    for c in preferred:
        if c in ("DateRemoved", "Created"):
            df[c] = safe_to_datetime(df[c])
        else:
            df[c] = df[c].apply(safe_str)

    rows = []
    for _, row in df.iterrows():
        kind = classify_removal_type(row.get("Type", ""))
        if kind == "delete":
            json_src = row.get("FailureDetailJSON", "")
        elif kind == "edit":
            json_src = row.get("BeforeJSON", "")
        else:
            json_src = ""

        tbga = parse_tbga_json(json_src)
        fp = extract_failure_fingerprint(tbga) if tbga else {}
        motive = safe_str(row.get("DeleteMotive", "")) or safe_str(row.get("ChangeMotive", ""))

        rows.append({
            **{c: row.get(c, "") for c in preferred},
            "__KIND__": kind,
            "__JSON_SRC__": json_src,
            "__PROTOCOLO__": fp.get("protocolo", ""),
            "__MATRICULA__": fp.get("matricula", ""),
            "__MATRICULA_NORM__": fp.get("matricula_norm", ""),
            "__MOTIVO__": fp.get("motivo", ""),
            "__MOTIVO_NORM__": fp.get("motivo_norm", ""),
            "__ETAPA__": fp.get("etapa", ""),
            "__ETAPA_NORM__": fp.get("etapa_norm", ""),
            "__CLIENTE__": fp.get("cliente", ""),
            "__WORKFLOW__": fp.get("workflow", ""),
            "__MODULO__": fp.get("modulo", ""),
            "__MOTIVO_LOG__": motive,
            "__APLICAVEL__": kind in ("delete", "edit") and bool(fp.get("protocolo")),
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=preferred + ["__KIND__", "__APLICAVEL__"])
    return out


def _read_falhas_removidas(path: str) -> pd.DataFrame:
    sh = _find_sheet_case_insensitive(path, FALHAS_REMOVIDAS_SHEET_ALIASES)
    if not sh:
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name=sh, engine="openpyxl")
    except Exception:
        return pd.DataFrame()
    return _normalize_removidas_cols(df)


def _base_protocolo_col(df_base: pd.DataFrame) -> str | None:
    for c in [COL_PROTOCOLO, "Protocolo"]:
        if c in df_base.columns:
            return c
    return None


def _cenario_col(df: pd.DataFrame) -> str | None:
    for c in ("Cenário Unificado", "Novo cenário", "Cenário"):
        if c in df.columns:
            return c
    return None


def match_base_rows(
    base: pd.DataFrame,
    prot_col: str,
    fingerprint: dict[str, str],
) -> tuple[pd.DataFrame, str]:
    """Retorna (matches, status) onde status é removido|sem_match|ambiguo|sem_protocolo."""
    prot = safe_str(fingerprint.get("protocolo", ""))
    if not prot:
        return pd.DataFrame(), "sem_protocolo"

    matches = base[base[prot_col] == prot].copy()
    if matches.empty:
        return matches, "sem_match"

    mat_norm = safe_str(fingerprint.get("matricula_norm", ""))
    if mat_norm and "_mat_norm" in matches.columns:
        mat_matches = matches[matches["_mat_norm"] == mat_norm]
        if not mat_matches.empty:
            matches = mat_matches

    if len(matches) == 1:
        return matches, "removido"

    motivo_norm = safe_str(fingerprint.get("motivo_norm", ""))
    if motivo_norm:
        ccol = _cenario_col(matches)
        if ccol:
            mask = matches[ccol].apply(lambda x: normalize_text(safe_str(x)) == motivo_norm)
            refined = matches[mask]
            if len(refined) == 1:
                return refined, "removido"
            if not refined.empty:
                matches = refined

    etapa_norm = safe_str(fingerprint.get("etapa_norm", ""))
    if etapa_norm and "Etapa" in matches.columns:
        mask = matches["Etapa"].apply(lambda x: normalize_text(safe_str(x)) == etapa_norm)
        refined = matches[mask]
        if len(refined) == 1:
            return refined, "removido"
        if not refined.empty:
            matches = refined

    if len(matches) == 1:
        return matches, "removido"
    if len(matches) > 1:
        return matches, "ambiguo"
    return pd.DataFrame(), "sem_match"


def _detail_from_match(
    rem_row: pd.Series,
    base_row: pd.Series | None,
    status: str,
) -> dict:
    brow = base_row if base_row is not None else pd.Series(dtype=object)
    return {
        "ID": safe_str(rem_row.get("ID", "")),
        "Type": safe_str(rem_row.get("Type", "")),
        "DateRemoved": rem_row.get("DateRemoved", pd.NaT),
        "UserRemove": safe_str(rem_row.get("UserRemove", "")),
        "Motivo": safe_str(rem_row.get("__MOTIVO_LOG__", "")),
        "Protocolo": safe_str(rem_row.get("__PROTOCOLO__", "")),
        "Matrícula Log": safe_str(rem_row.get("__MATRICULA__", "")),
        "Motivo Falha Log": safe_str(rem_row.get("__MOTIVO__", "")),
        "Etapa Log": safe_str(rem_row.get("__ETAPA__", "")),
        "Status Match": status,
        "Data de Análise Base": brow.get(COL_DATA_ANALISE, pd.NaT) if not brow.empty else pd.NaT,
        "Cliente Base": safe_str(brow.get("Cliente", "")) if not brow.empty else "",
        "Workflow Base": safe_str(brow.get("Workflow", "")) if not brow.empty else "",
        "Localidade Base": safe_str(brow.get("Localidade", "")) if not brow.empty else "",
        "Matrícula Agente Base": safe_str(brow.get(COL_MATRICULA, "")) if not brow.empty else "",
        "Etapa Base": safe_str(brow.get("Etapa", "")) if not brow.empty else "",
        "Novo cenário Base": safe_str(brow.get("Novo cenário", brow.get("Cenário Unificado", ""))) if not brow.empty else "",
        "Módulo Base": safe_str(brow.get("Módulo", "")) if not brow.empty else "",
        "Path": safe_str(rem_row.get("Path", "")),
    }


def _build_removidas_globals_empty() -> None:
    global _removidas_resumo_df, _removidas_detalhe_df, _removidas_protocolos_removidos

    _removidas_resumo_df = pd.DataFrame({
        "Registros log": [0],
        "Aplicáveis": [0],
        "Protocolos removidos": [0],
        "Linhas removidas": [0],
        "Sem match": [0],
        "Ambíguos": [0],
        "Ignorados (type desconhecido)": [0],
        "Registros base antes": [0],
        "Registros base depois": [0],
    })
    _removidas_detalhe_df = pd.DataFrame(columns=[
        "ID", "Type", "DateRemoved", "UserRemove", "Motivo", "Protocolo",
        "Matrícula Log", "Motivo Falha Log", "Etapa Log", "Status Match",
        "Data de Análise Base", "Cliente Base", "Workflow Base", "Localidade Base",
        "Matrícula Agente Base", "Etapa Base", "Novo cenário Base", "Módulo Base", "Path",
    ])
    _removidas_protocolos_removidos = set()


def _apply_falhas_removidas_to_base(df_base: pd.DataFrame, df_removidas: pd.DataFrame):
    global _removidas_resumo_df, _removidas_detalhe_df, _removidas_protocolos_removidos

    if df_base is None or df_base.empty:
        _build_removidas_globals_empty()
        return df_base, _removidas_resumo_df, _removidas_detalhe_df

    prot_col = _base_protocolo_col(df_base)
    if not prot_col:
        _build_removidas_globals_empty()
        return df_base.copy(), _removidas_resumo_df, _removidas_detalhe_df

    base = df_base.copy()
    base[prot_col] = base[prot_col].apply(norm_protocolo)
    base = base[base[prot_col].apply(safe_str) != ""].copy()
    if COL_MATRICULA in base.columns:
        base["_mat_norm"] = base[COL_MATRICULA].apply(norm_matricula)
    else:
        base["_mat_norm"] = ""

    if df_removidas is None or df_removidas.empty:
        _build_removidas_globals_empty()
        resumo = _removidas_resumo_df.copy()
        resumo["Registros base antes"] = [int(len(base))]
        resumo["Registros base depois"] = [int(len(base))]
        _removidas_resumo_df = resumo
        return base.drop(columns=["_mat_norm"], errors="ignore"), resumo, _removidas_detalhe_df

    detalhe_rows = []
    removed_indices: set[int] = set()
    sem_match = 0
    ambiguos = 0
    ignorados = 0
    aplicaveis = 0

    for _, rrow in df_removidas.iterrows():
        kind = safe_str(rrow.get("__KIND__", ""))
        if kind == "unknown":
            ignorados += 1
            detalhe_rows.append(_detail_from_match(rrow, None, "ignorado_type"))
            continue
        if not rrow.get("__APLICAVEL__", False):
            ignorados += 1
            detalhe_rows.append(_detail_from_match(rrow, None, "ignorado_sem_protocolo"))
            continue

        aplicaveis += 1
        fingerprint = {
            "protocolo": safe_str(rrow.get("__PROTOCOLO__", "")),
            "matricula_norm": safe_str(rrow.get("__MATRICULA_NORM__", "")),
            "motivo_norm": safe_str(rrow.get("__MOTIVO_NORM__", "")),
            "etapa_norm": safe_str(rrow.get("__ETAPA_NORM__", "")),
        }
        matches, status = match_base_rows(base, prot_col, fingerprint)

        if status == "removido":
            idx = matches.index[0]
            removed_indices.add(idx)
            detalhe_rows.append(_detail_from_match(rrow, base.loc[idx], status))
        elif status == "ambiguo":
            ambiguos += 1
            detalhe_rows.append(_detail_from_match(rrow, None, status))
        elif status == "sem_match":
            sem_match += 1
            detalhe_rows.append(_detail_from_match(rrow, None, status))
        else:
            ignorados += 1
            detalhe_rows.append(_detail_from_match(rrow, None, status))

    detalhe = pd.DataFrame(detalhe_rows)
    protocolos_removidos: set[str] = set()
    if removed_indices:
        protocolos_removidos = set(
            base.loc[list(removed_indices), prot_col].dropna().astype(str).tolist()
        )

    base_before = int(len(base))
    base_after_df = base.drop(index=list(removed_indices)).copy() if removed_indices else base.copy()
    base_after = int(len(base_after_df))

    if "_mat_norm" in base_after_df.columns:
        base_after_df = base_after_df.drop(columns=["_mat_norm"])

    resumo = pd.DataFrame({
        "Registros log": [int(len(df_removidas))],
        "Aplicáveis": [int(aplicaveis)],
        "Protocolos removidos": [int(len(protocolos_removidos))],
        "Linhas removidas": [int(base_before - base_after)],
        "Sem match": [int(sem_match)],
        "Ambíguos": [int(ambiguos)],
        "Ignorados (type desconhecido)": [int(ignorados)],
        "Registros base antes": [base_before],
        "Registros base depois": [base_after],
    })

    _removidas_resumo_df = resumo
    _removidas_detalhe_df = detalhe if not detalhe.empty else _removidas_detalhe_df
    _removidas_protocolos_removidos = protocolos_removidos
    return base_after_df, resumo, detalhe


def get_removidas_state() -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    return _removidas_resumo_df.copy(), _removidas_detalhe_df.copy(), set(_removidas_protocolos_removidos)


def filter_removidas_scope(df: pd.DataFrame, scope_name: str = "") -> pd.DataFrame:
    if df is None or df.empty or not safe_str(scope_name):
        return df.copy() if df is not None else pd.DataFrame()
    dfx = df.copy()
    if "Localidade Base" not in dfx.columns:
        return dfx
    scope_norm = normalize_text(scope_name)
    mask = dfx["Localidade Base"].apply(normalize_text).str.contains(scope_norm, na=False)
    if mask.any():
        return dfx[mask].copy()
    return dfx


def filter_removidas_period(df: pd.DataFrame, cur_start: date, cur_end: date) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    dfx = df.copy()
    if "DateRemoved" not in dfx.columns:
        return dfx
    dfx["DateRemoved"] = safe_to_datetime(dfx["DateRemoved"])
    mask = (dfx["DateRemoved"] >= pd.Timestamp(cur_start)) & (dfx["DateRemoved"] <= pd.Timestamp(cur_end))
    return dfx[mask].copy()


def dedupe_removidas_detail(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    key_cols = ["Protocolo", "Data de Análise Base", "Matrícula Agente Base"]
    dfx = df.copy()
    for col in key_cols:
        if col not in dfx.columns:
            dfx[col] = ""
    return dfx.drop_duplicates(subset=key_cols).copy()


def summarize_removidas_period(
    det: pd.DataFrame,
    cur_start: date,
) -> dict[str, int | str]:
    """Resumo de retificações com match removido no período (após filtros escopo/período)."""
    out: dict[str, int | str] = {
        "eventos": 0,
        "unicas": 0,
        "retroativas": 0,
        "meses_retroativos_txt": "",
    }
    if det is None or det.empty:
        return out
    sub = det[det["Status Match"].astype(str) == "removido"].copy()
    if sub.empty:
        return out
    out["eventos"] = int(len(sub))
    unicas = dedupe_removidas_detail(sub)
    out["unicas"] = int(len(unicas))
    if unicas.empty or "Data de Análise Base" not in unicas.columns:
        return out
    unicas["_data_falha"] = safe_to_datetime(unicas["Data de Análise Base"])
    retroativas = unicas[unicas["_data_falha"] < pd.Timestamp(cur_start)].copy()
    out["retroativas"] = int(len(retroativas))
    if retroativas.empty:
        return out
    retroativas["_mes"] = retroativas["_data_falha"].dt.month
    parts = []
    for mes in sorted(retroativas["_mes"].dropna().unique()):
        cnt = int((retroativas["_mes"] == mes).sum())
        label = MESES_ABREV_BR.get(int(mes), str(int(mes))).lower()
        parts.append(f"{label} {cnt}")
    out["meses_retroativos_txt"] = " · ".join(parts)
    return out


def _snapshot_removidas_reported_path(out_dir) -> Path:
    return Path(out_dir) / "_snapshot_removidas_reported.pkl"


def load_removidas_reported_snapshot(out_dir) -> set[tuple]:
    """Fingerprints de retificações já exibidas no e-mail em execuções anteriores."""
    if not out_dir:
        return set()
    p = _snapshot_removidas_reported_path(out_dir)
    if not p.is_file():
        return set()
    try:
        data = pd.read_pickle(p)
        if isinstance(data, set):
            return set(data)
        if isinstance(data, (list, tuple)):
            return set(data)
    except Exception:
        pass
    return set()


def mark_removidas_reported(out_dir, fingerprints: set[tuple]) -> None:
    if not out_dir or not fingerprints:
        return
    prev = load_removidas_reported_snapshot(out_dir)
    prev.update(fingerprints)
    p = _snapshot_removidas_reported_path(out_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(prev, p)
    except Exception:
        pass


def removidas_row_fingerprint(row) -> tuple:
    """Chave estável para deduplicar retificações já reportadas no e-mail."""
    proto = safe_str(row.get("Protocolo"))
    mat = safe_str(row.get("Matrícula Agente Base"))
    dt = row.get("Data de Análise Base")
    dt_s = ""
    try:
        ts = safe_to_datetime(pd.Series([dt])).iloc[0]
        if pd.notna(ts):
            dt_s = ts.date().isoformat()
    except Exception:
        pass
    return (proto, dt_s, mat)


def _filter_removidas_by_fingerprints(df: pd.DataFrame, fingerprints: set[tuple]) -> pd.DataFrame:
    if df is None or df.empty or not fingerprints:
        return pd.DataFrame(columns=df.columns if df is not None else None)
    mask = df.apply(lambda r: removidas_row_fingerprint(r) in fingerprints, axis=1)
    return df[mask].copy()


def build_removidas_email_blurb(
    cur_start: date,
    cur_end: date,
    scope_name: str = "",
    out_dir=None,
    only_new: bool = True,
) -> str:
    """Card HTML neutro para o e-mail — só retificações novas do período (Plano B)."""
    _, det, _ = get_removidas_state()
    if det is None or det.empty:
        return ""
    sub = filter_removidas_scope(det, scope_name)
    sub = filter_removidas_period(sub, cur_start, cur_end)
    sub_rem = sub[sub["Status Match"].astype(str) == "removido"].copy()
    unicas = dedupe_removidas_detail(sub_rem)
    if unicas.empty:
        return ""

    new_fps: set[tuple] = set()
    if only_new and out_dir is not None:
        reported = load_removidas_reported_snapshot(out_dir)
        new_fps = {
            fp
            for _, row in unicas.iterrows()
            if (fp := removidas_row_fingerprint(row)) not in reported
        }
        if not new_fps:
            return ""
        sub_rem = _filter_removidas_by_fingerprints(sub_rem, new_fps)
    else:
        new_fps = {removidas_row_fingerprint(row) for _, row in unicas.iterrows()}

    stats = summarize_removidas_period(sub_rem, cur_start)
    if int(stats["unicas"]) <= 0:
        return ""

    eventos = int(stats["eventos"])
    unicas = int(stats["unicas"])
    retroativas = int(stats["retroativas"])
    meses_txt = safe_str(stats.get("meses_retroativos_txt", ""))

    if eventos == unicas:
        contagem = f"<b>{unicas}</b> retificação(ões) registrada(s) no log SharePoint/BI"
    else:
        contagem = (
            f"<b>{eventos}</b> retificação(ões) no log → "
            f"<b>{unicas}</b> falha(s) distinta(s) deixaram de compor os indicadores"
        )

    retroativo_html = ""
    if retroativas > 0:
        extra = f" ({meses_txt})" if meses_txt else ""
        retroativo_html = (
            f" <b>{retroativas}</b> referem-se a meses anteriores{extra}."
        )

    html = (
        "<div style='margin:10px 0 14px;background:#F9FAFB;border:1px solid #E5E7EB;"
        "border-radius:12px;padding:10px 12px;'>"
        "<div style='font-family:Segoe UI,Roboto,Arial,Helvetica,sans-serif;font-size:12px;"
        "font-weight:800;color:#B91C1C;margin:0 0 6px;'>Ajustes de base (log SharePoint/BI)</div>"
        "<div style='font-family:Segoe UI,Roboto,Arial,Helvetica,sans-serif;font-size:12px;"
        "color:#4B5563;line-height:1.55;'>"
        f"Neste período: {contagem}.{retroativo_html} "
        "Os indicadores abaixo <b>já excluem</b> essas falhas. "
        "Ajustes em meses anteriores explicam diferenças em relação a reports antigos, "
        "<b>não refletem erro de carga da planilha</b>."
        "</div></div>"
    )

    if out_dir is not None and new_fps:
        mark_removidas_reported(out_dir, new_fps)

    return html


__all__ = [
    "parse_tbga_json",
    "extract_failure_fingerprint",
    "classify_removal_type",
    "match_base_rows",
    "_read_falhas_removidas",
    "_apply_falhas_removidas_to_base",
    "_build_removidas_globals_empty",
    "get_removidas_state",
    "filter_removidas_scope",
    "filter_removidas_period",
    "dedupe_removidas_detail",
    "summarize_removidas_period",
    "load_removidas_reported_snapshot",
    "mark_removidas_reported",
    "removidas_row_fingerprint",
    "build_removidas_email_blurb",
]
