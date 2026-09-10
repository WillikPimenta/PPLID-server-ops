"""Orquestração do bot Produtividade Case Manager (DocumentDB → Excel)."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from app.bots.produtividade_case.dates import (
    MES_ABREV,
    TZ_BR,
    calc_dia_civil_br,
    calc_fechamento_mes_anterior,
    calc_periodo_consolidado,
    calc_periodo_hora,
    nome_arquivo_consolidado,
    pasta_mes_nome,
)
from app.bots.produtividade_case.mongo import get_collection, validate_docdb_config
from app.bots.produtividade_case.reports.consolidado import gerar_consolidado
from app.bots.produtividade_case.reports.fila import gerar_fila_aberta
from app.bots.produtividade_case.reports.hora import gerar_prod_hora, nome_arquivo_hora
from app.bots.produtividade_case.reports.tempo_logado import gerar_tempo_logado

log = logging.getLogger(__name__)

TAREFAS_VALIDAS = (
    "prod_hora",
    "tempo_logado_dia",
    "tempo_logado_mes",
    "consolidado",
    "fechamento_mes_anterior",
    "fila_aberta",
)

parar_event = threading.Event()
_status_cb = None
_progress_cb = None
_worker: threading.Thread | None = None


def set_status_callback(fn):
    global _status_cb
    _status_cb = fn


def set_progress_callback(fn):
    global _progress_cb
    _progress_cb = fn


def _status(msg: str) -> None:
    print(f"STATUS|{msg}", flush=True)
    if callable(_status_cb):
        try:
            _status_cb(msg)
        except Exception:
            pass


def _progress(pct: int, msg: str = "") -> None:
    print(f"PROGRESS|{int(pct)}|{msg}", flush=True)
    if callable(_progress_cb):
        try:
            _progress_cb(int(pct), msg)
        except Exception:
            pass


def _emit_saved(report_type: str, arquivo) -> None:
    path = Path(arquivo).resolve()
    print(f"PRODUTIVIDADE_CASE_SAVED|{report_type}|{path}", flush=True)


def _emit_fila_saved(arquivo) -> None:
    path = Path(arquivo).resolve()
    # Prefixo dedicado; robot_manager enfileira sync com report_type=fila_aberta.
    print(f"CASE_FILA_SAVED|{path}", flush=True)


def _resolve_reports_root(settings: dict) -> Path:
    env_root = str(os.environ.get("PRODUTIVIDADE_CASE_REPORTS_ROOT", "") or "").strip()
    if env_root:
        return Path(env_root)
    # Padrão do script legado (pasta sincronizada IDF Docs)
    return Path.home() / "EXPERIAN SERVICES CORP" / "IDF Docs - Relatórios"


def _resolve_dir(override: str, default: Path) -> Path:
    text = str(override or "").strip()
    return Path(text) if text else default


def _resolve_temp(settings: dict) -> Path:
    override = str(settings.get("dir_temp") or "").strip()
    if override:
        path = Path(override)
    else:
        env_temp = str(os.environ.get("PRODUTIVIDADE_CASE_TEMP_DIR", "") or "").strip()
        path = Path(env_temp) if env_temp else Path(r"C:\relatorios_temp")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tarefas_from_settings(settings: dict) -> list[str]:
    raw = settings.get("tarefas")
    if isinstance(raw, str):
        raw = [t.strip() for t in raw.split(",") if t.strip()]
    if not isinstance(raw, list) or not raw:
        return list(TAREFAS_VALIDAS)
    out = []
    for t in raw:
        key = str(t).strip().lower()
        if key in TAREFAS_VALIDAS and key not in out:
            out.append(key)
    return out or list(TAREFAS_VALIDAS)


def _should_stop() -> bool:
    return parar_event.is_set()


def executar_pipeline(settings: dict | None = None) -> None:
    settings = dict(settings or {})
    ok, msg = validate_docdb_config()
    if not ok:
        _status(f"Erro: {msg}")
        raise RuntimeError(msg)

    tarefas = _tarefas_from_settings(settings)
    reports_root = _resolve_reports_root(settings)
    dir_temp = _resolve_temp(settings)

    dir_hora_raiz = _resolve_dir(
        settings.get("dir_hora", ""),
        reports_root / "Relatório de Produtividade por Hora",
    )
    dir_tempo_raiz = _resolve_dir(
        settings.get("dir_tempo_logado", ""),
        reports_root / "Relatório de Tempo Logado",
    )
    dir_cons_raiz = _resolve_dir(
        settings.get("dir_consolidado", ""),
        reports_root / "Relatório de Produtividade Consolidado",
    )

    agora_br = datetime.now(TZ_BR)
    data_inicio, data_fim = calc_periodo_hora(agora_br)
    dia_civil = calc_dia_civil_br(agora_br)
    pasta_mes = pasta_mes_nome(dia_civil)
    timestamp_arquivo = agora_br.strftime("%Y-%m-%d_%H-%M")
    inicio_br, fim_nome_br, data_inicio_cons, data_fim_cons = calc_periodo_consolidado(
        agora_br, data_fim_dia=data_fim
    )
    mes_nome = MES_ABREV[dia_civil.month]

    total_steps = max(len(tarefas), 1)
    done = 0

    client = None
    try:
        _status("Conectando ao DocumentDB…")
        _progress(2, "Conectando")
        client, collection = get_collection()

        if "prod_hora" in tarefas and not _should_stop():
            _status("Gerando produtividade por hora…")
            dir_final = dir_hora_raiz / pasta_mes
            dir_final.mkdir(parents=True, exist_ok=True)
            arquivo = dir_final / nome_arquivo_hora(agora_br)
            sheet = f"Prod ({agora_br.strftime('%d-%m-%Y %H-%M')})"
            gerar_prod_hora(collection, data_inicio, data_fim, arquivo, sheet, dir_temp)
            print(f"[OK] Produtividade por Hora: {arquivo}", flush=True)
            _emit_saved("prod_hora", arquivo)
            done += 1
            _progress(int(5 + 90 * done / total_steps), "Produtividade por hora")

        if "tempo_logado_dia" in tarefas and not _should_stop():
            _status("Gerando tempo logado (dia)…")
            dir_final = dir_tempo_raiz / pasta_mes
            dir_final.mkdir(parents=True, exist_ok=True)
            arquivo = dir_final / f"tempo_logado_{timestamp_arquivo}.xlsx"
            sheet = f"Tempo Logado ({agora_br.strftime('%d-%m-%Y %H-%M')})"
            csv_temp = dir_temp / "tempo_logado_dia_protocolo.csv"
            gerar_tempo_logado(
                collection, data_inicio, data_fim, arquivo, sheet, dir_temp, csv_temp
            )
            print(f"[OK] Tempo Logado (dia): {arquivo}", flush=True)
            _emit_saved("tempo_logado", arquivo)
            done += 1
            _progress(int(5 + 90 * done / total_steps), "Tempo logado dia")

        if "tempo_logado_mes" in tarefas and not _should_stop():
            _status("Gerando tempo logado (mês)…")
            dir_final = dir_tempo_raiz / pasta_mes
            dir_final.mkdir(parents=True, exist_ok=True)
            arquivo = dir_final / (
                f"tempo_logado_mes_{inicio_br.strftime('%d')}-"
                f"{fim_nome_br.strftime('%d')}{mes_nome}.xlsx"
            )
            sheet = f"Tempo Logado Mês ({agora_br.strftime('%d-%m-%Y')})"
            csv_temp = dir_temp / "tempo_logado_mes_protocolo.csv"
            gerar_tempo_logado(
                collection,
                data_inicio_cons,
                data_fim_cons,
                arquivo,
                sheet,
                dir_temp,
                csv_temp,
            )
            print(f"[OK] Tempo Logado (mês): {arquivo}", flush=True)
            _emit_saved("tempo_logado", arquivo)
            done += 1
            _progress(int(5 + 90 * done / total_steps), "Tempo logado mês")

        if "consolidado" in tarefas and not _should_stop():
            _status("Gerando consolidado…")
            dir_final = dir_cons_raiz / pasta_mes
            dir_final.mkdir(parents=True, exist_ok=True)
            arquivo = dir_final / nome_arquivo_consolidado(inicio_br, fim_nome_br)
            gerar_consolidado(
                collection, data_inicio_cons, data_fim_cons, arquivo, label="Consolidado"
            )
            print(f"[OK] Produtividade Consolidado: {arquivo}", flush=True)
            _emit_saved("consolidado", arquivo)
            done += 1
            _progress(int(5 + 90 * done / total_steps), "Consolidado")

        if "fechamento_mes_anterior" in tarefas and not _should_stop():
            fechamento = calc_fechamento_mes_anterior(agora_br)
            if not fechamento:
                print(
                    "[INFO] Fechamento mês anterior ignorado (fora dos dias civis 1–2)",
                    flush=True,
                )
            else:
                ini_br, fim_nome_fech, ini_utc, fim_utc, pasta_ant = fechamento
                mes_nome_ant = MES_ABREV[ini_br.month]
                _status("Gerando fechamento mês anterior…")

                dir_cons_ant = dir_cons_raiz / pasta_ant
                dir_cons_ant.mkdir(parents=True, exist_ok=True)
                arquivo_cons = dir_cons_ant / nome_arquivo_consolidado(ini_br, fim_nome_fech)
                gerar_consolidado(
                    collection,
                    ini_utc,
                    fim_utc,
                    arquivo_cons,
                    label="Fechamento consolidado mês anterior",
                )
                print(f"[OK] Fechamento consolidado: {arquivo_cons}", flush=True)
                _emit_saved("consolidado", arquivo_cons)

                dir_tempo_ant = dir_tempo_raiz / pasta_ant
                dir_tempo_ant.mkdir(parents=True, exist_ok=True)
                arquivo_tempo = dir_tempo_ant / (
                    f"tempo_logado_mes_{ini_br.strftime('%d')}-"
                    f"{fim_nome_fech.strftime('%d')}{mes_nome_ant}.xlsx"
                )
                sheet = f"Tempo Logado Mês Fechamento ({fim_nome_fech.strftime('%d-%m-%Y')})"
                csv_temp = dir_temp / "tempo_logado_mes_fechamento_protocolo.csv"
                gerar_tempo_logado(
                    collection,
                    ini_utc,
                    fim_utc,
                    arquivo_tempo,
                    sheet,
                    dir_temp,
                    csv_temp,
                )
                print(f"[OK] Fechamento tempo logado: {arquivo_tempo}", flush=True)
                _emit_saved("tempo_logado", arquivo_tempo)
            done += 1
            _progress(int(5 + 90 * done / total_steps), "Fechamento")

        if "fila_aberta" in tarefas and not _should_stop():
            _status("Gerando snapshot da fila em aberto…")
            dir_fila = dir_temp / "fila_aberta" / pasta_mes
            dir_fila.mkdir(parents=True, exist_ok=True)
            arquivo = dir_fila / f"fila_aberta_{timestamp_arquivo}.json"
            payload = gerar_fila_aberta(collection, arquivo)
            total = int(payload.get("total_abertos") or 0)
            print(
                f"[OK] Fila em aberto: {total} protocolos → {arquivo}",
                flush=True,
            )
            _emit_fila_saved(arquivo)
            done += 1
            _progress(int(5 + 90 * done / total_steps), "Fila em aberto")

        if _should_stop():
            _status("Interrompido pelo usuário")
            _progress(100, "Interrompido")
            return

        _status("Execução finalizada com sucesso")
        _progress(100, "Concluído")
        print("[OK] Execução finalizada com sucesso.", flush=True)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def start(settings=None):
    """Inicia o pipeline em thread (contrato robot_runner)."""
    global _worker
    parar_event.clear()
    settings = dict(settings or {})

    def _run():
        try:
            executar_pipeline(settings)
        except Exception as exc:
            log.exception("produtividade_case falhou")
            _status(f"Erro: {type(exc).__name__}: {exc}")
            _progress(100, "Erro")
            raise

    _worker = threading.Thread(target=_run, name="produtividade_case", daemon=False)
    _worker.start()
    return _worker


def stop():
    parar_event.set()
    _status("Parada solicitada…")
