"""Tarefa monitor de eventos e ETL offline."""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import *
from app.bots.rotina.constants import COLUNAS_MONITOR_UNIFICADO, DOWNLOADS_TEMP_ROTINA
from app.bots.rotina.io import (
    _aguardar_download_completo,
    _ler_csv_tratamento,
    _limpar_downloads_temp_inicio,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
    parse_datetime,
    verifica_usuario,
)
from app.bots.rotina.state import _registrar_erro, _set_progress, _set_status

log = logging.getLogger("robots.bot_rotina")


def _pretratar_monitor_verifica_usuario(caminho_monitor: str) -> bool:
    """Aplica normalização e validação de usuário no monitor bruto antes do tratamento final."""
    try:
        df_monitor = _ler_csv_tratamento(caminho_monitor)
    except Exception as exc:
        log.warning(f"Pré-tratamento do monitor ignorado: falha ao ler arquivo ({exc})")
        return False

    if "Usuário" not in df_monitor.columns:
        log.warning("Pré-tratamento do monitor ignorado: coluna 'Usuário' ausente")
        return False

    usuarios_norm = (
        df_monitor["Usuário"]
        .astype(str)
        .str.split(" - ").str[0]
        .str.strip()
        .str[:7]
    )
    usuarios_validos = usuarios_norm.apply(verifica_usuario)

    total_antes = len(df_monitor)
    df_monitor = df_monitor[usuarios_validos].copy()
    df_monitor["Usuário"] = usuarios_norm[usuarios_validos].values
    removidas = total_antes - len(df_monitor)

    try:
        if str(caminho_monitor).lower().endswith('.parquet'):
            df_parquet = _preparar_dataframe_para_parquet(df_monitor)
            df_parquet.to_parquet(caminho_monitor, index=False, compression='snappy')
        else:
            df_monitor.to_csv(caminho_monitor, sep=";", index=False, encoding="1252")
        log.info(
            f"[MonitorPreTratamento] concluído | linhas={len(df_monitor):,} | "
            f"removidas_usuário_inválido={removidas:,} ✅ Destino: {os.path.abspath(caminho_monitor)}"
        )
        return True
    except Exception as exc:
        log.warning(f"Pré-tratamento do monitor falhou ao salvar arquivo: {exc}")
        return False


def tratar_arquivo(
    caminho_monitor,
    caminho_detalhado_produtividade,
    data,
    snapshot_at=None,
):
    """Trata monitor de eventos usando detalhado de produtividade como fallback de logout.
       Regra extra: valida a primeira sessão do dia com base no primeiro protocolo do dia (opção 2).
    """
    try:
        import os
        import time
        import pandas as pd
        from datetime import datetime

        inicio_tratamento = time.perf_counter()

        # ------------------------------------------------------------------
        # Trata data (aceita datetime ou string em múltiplos formatos)
        # ------------------------------------------------------------------
        data_obj = data if hasattr(data, "strftime") else None
        if data_obj is None and isinstance(data, str):
            for fmt in ("%Y%m%d", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    data_obj = datetime.strptime(data, fmt)
                    break
                except ValueError:
                    continue

        data_legivel = data_obj.strftime("%d/%m/%Y") if data_obj is not None else str(data)
        data_arquivo = (
            data_obj.strftime("%Y%m%d")
            if data_obj is not None
            else str(data).replace("/", "").replace("-", "")
        )

        # No fluxo H/H, a extração pode ocorrer enquanto a última sessão do
        # dia ainda está ativa. O horário do snapshot representa o limite até o
        # qual sabemos que a pessoa continuava logada; produtividade não é logout.
        snapshot_ts = (
            pd.to_datetime(snapshot_at, errors="coerce")
            if snapshot_at is not None
            else pd.NaT
        )
        if pd.notna(snapshot_ts) and getattr(snapshot_ts, "tzinfo", None) is not None:
            snapshot_ts = snapshot_ts.tz_localize(None)

        log.info(f"[MonitorTratamento] Iniciando tratamento para data={data}")
        _set_status(f"Tratando monitor para {data_legivel}...")
        os.makedirs(PASTA_MONITOR_TRATADO, exist_ok=True)

        # ------------------------------------------------------------------
        # Leitura
        # ------------------------------------------------------------------
        monitor_eventos_tratar = _ler_csv_tratamento(str(caminho_monitor))
        dt_detalhado_produtividade = _ler_csv_tratamento(str(caminho_detalhado_produtividade))

        log.info(
            f"[MonitorTratamento] Arquivos lidos | monitor={len(monitor_eventos_tratar):,} linhas | "
            f"produtividade={len(dt_detalhado_produtividade):,} linhas"
        )
        _set_status(
            f"Arquivos lidos: monitor={len(monitor_eventos_tratar):,} linhas, "
            f"produtividade={len(dt_detalhado_produtividade):,} linhas"
        )

        # ------------------------------------------------------------------
        # Validação de colunas
        # ------------------------------------------------------------------
        monitor_cols = {"Usuário", "Data do Evento", "Evento", "ID Sessão", "Objeto"}
        prod_cols = {"numTempoAnalise", "datAnalise", "desMatricula"}

        falt_monitor = [c for c in monitor_cols if c not in monitor_eventos_tratar.columns]
        falt_prod = [c for c in prod_cols if c not in dt_detalhado_produtividade.columns]

        if falt_monitor:
            log.warning(f"Monitor tratado ignorado, colunas ausentes no monitor: {falt_monitor}")
            _set_status(f"Aviso: Monitor ignorado, colunas ausentes: {falt_monitor}")
            return None

        if falt_prod:
            log.warning(f"Monitor tratado ignorado, colunas ausentes na produtividade: {falt_prod}")
            _set_status(f"Aviso: Monitor ignorado, colunas ausentes: {falt_prod}")
            return None

        # ------------------------------------------------------------------
        # Monitor: normalização e filtros iniciais
        # ------------------------------------------------------------------
        monitor_eventos_tratar = monitor_eventos_tratar.sort_values(by=["Usuário", "Data do Evento"]).copy()

        usuario_normalizado = monitor_eventos_tratar["Usuário"].astype(str).str.strip().str[:7]
        usuario_valido = usuario_normalizado.apply(verifica_usuario)

        mask_monitor_valido = usuario_valido & (monitor_eventos_tratar["Evento"] != "Falha na autenticação")

        removidas_usuario = len(monitor_eventos_tratar) - int(mask_monitor_valido.sum())
        monitor_eventos_tratar = monitor_eventos_tratar[mask_monitor_valido].copy()
        monitor_eventos_tratar["Usuário"] = usuario_normalizado[mask_monitor_valido].values

        if removidas_usuario:
            log.info(f"[MonitorTratamento] {removidas_usuario:,} linha(s) removidas por Usuário inválido")

        # parse datetime no monitor + blindagem
        monitor_eventos_tratar["Data do Evento"] = monitor_eventos_tratar["Data do Evento"].apply(parse_datetime)
        monitor_eventos_tratar["Data do Evento"] = pd.to_datetime(monitor_eventos_tratar["Data do Evento"], errors="coerce")
        monitor_eventos_tratar = monitor_eventos_tratar.dropna(subset=["Data do Evento"]).copy()
        monitor_eventos_tratar = monitor_eventos_tratar.sort_values(["Usuário", "Data do Evento"]).copy()

        log.info(f"[MonitorTratamento] Monitor após filtros iniciais: {len(monitor_eventos_tratar):,} linhas")
        _set_status(f"Monitor após filtros iniciais: {len(monitor_eventos_tratar):,} linhas")

        colunas_filtradas = ["Usuário", "Data do Evento", "Evento", "ID Sessão", "Objeto"]

        # ------------------------------------------------------------------
        # Produtividade: índice auxiliar de protocolos por usuário (datAnalise original)
        # ------------------------------------------------------------------
        produtividade_protocolo = dt_detalhado_produtividade[["desMatricula", "datAnalise"]].copy()
        produtividade_protocolo["Usuário"] = (
            produtividade_protocolo["desMatricula"]
            .astype(str)
            .str.split(" - ").str[0]
            .str.strip()
            .str[:7]
        )
        produtividade_protocolo = produtividade_protocolo[
            produtividade_protocolo["Usuário"].apply(verifica_usuario)
        ].copy()

        produtividade_protocolo["datAnalise"] = produtividade_protocolo["datAnalise"].apply(parse_datetime)
        produtividade_protocolo["datAnalise"] = pd.to_datetime(produtividade_protocolo["datAnalise"], errors="coerce")
        produtividade_protocolo = produtividade_protocolo.dropna(subset=["datAnalise"]).copy()

        # Series por usuário com protocolos (datetime garantido)
        protocolos_por_usuario = {
            usuario: pd.to_datetime(grupo["datAnalise"], errors="coerce").dropna().sort_values().reset_index(drop=True)
            for usuario, grupo in produtividade_protocolo.groupby("Usuário", sort=False)
        }

        # ------------------------------------------------------------------
        # Produtividade: normalização principal e criação de "Logout" sintético
        # ------------------------------------------------------------------
        dt_detalhado_produtividade = dt_detalhado_produtividade.copy()

        dt_detalhado_produtividade["numTempoAnalise"] = pd.to_timedelta(
            dt_detalhado_produtividade["numTempoAnalise"], errors="coerce"
        ).fillna(pd.Timedelta(seconds=0))

        dt_detalhado_produtividade["datAnalise"] = dt_detalhado_produtividade["datAnalise"].apply(parse_datetime)
        dt_detalhado_produtividade["datAnalise"] = pd.to_datetime(dt_detalhado_produtividade["datAnalise"], errors="coerce")
        dt_detalhado_produtividade = dt_detalhado_produtividade.dropna(subset=["datAnalise"]).copy()

        dt_detalhado_produtividade["desMatricula"] = (
            dt_detalhado_produtividade["desMatricula"]
            .astype(str)
            .str.split(" - ").str[0]
            .str.strip()
            .str[:7]
        )
        dt_detalhado_produtividade = dt_detalhado_produtividade[
            dt_detalhado_produtividade["desMatricula"].apply(verifica_usuario)
        ].copy()

        # datAnalise original = horário do protocolo
        dt_detalhado_produtividade["DataProtocolo"] = pd.to_datetime(dt_detalhado_produtividade["datAnalise"], errors="coerce")

        # Logout sintético = fim da análise
        dt_detalhado_produtividade["datAnalise"] = dt_detalhado_produtividade["datAnalise"] + dt_detalhado_produtividade["numTempoAnalise"]
        dt_detalhado_produtividade["datAnalise"] = pd.to_datetime(dt_detalhado_produtividade["datAnalise"], errors="coerce")

        dt_detalhado_produtividade = dt_detalhado_produtividade.dropna(subset=["DataProtocolo", "datAnalise"]).copy()

        dt_detalhado_produtividade.rename(columns={"datAnalise": "Data do Evento"}, inplace=True)
        dt_detalhado_produtividade.rename(columns={"desMatricula": "Usuário"}, inplace=True)

        # AJUSTE 1: capar logout sintético no fim do dia do protocolo (não virar dia seguinte)
        fim_dia_protocolo = (
            pd.to_datetime(dt_detalhado_produtividade["DataProtocolo"].dt.date)
            + pd.Timedelta(hours=23, minutes=59, seconds=59)
        )
        dt_detalhado_produtividade["Data do Evento"] = dt_detalhado_produtividade["Data do Evento"].where(
            dt_detalhado_produtividade["Data do Evento"] <= fim_dia_protocolo,
            fim_dia_protocolo
        )

        dt_detalhado_produtividade["Evento"] = "Logout"
        dt_detalhado_produtividade["ID Sessão"] = "Detalhado Produtividade"
        dt_detalhado_produtividade["Objeto"] = "Autenticacao via Detalhado Produtividade"

        # Índices de produtividade por usuário (para fallback: pegar fim do último protocolo na janela)
        prod_por_usuario = {
            usuario: grupo.copy()
            for usuario, grupo in dt_detalhado_produtividade.groupby("Usuário", sort=False)
        }

        log.info(f"[MonitorTratamento] Produtividade normalizada: {len(dt_detalhado_produtividade):,} linhas")
        _set_status(f"Produtividade normalizada: {len(dt_detalhado_produtividade):,} linhas")

        # ------------------------------------------------------------------
        # Monitor: criação de sequências (mantido)
        # ------------------------------------------------------------------
        monitor_eventos_tratar["Evento_Anterior"] = monitor_eventos_tratar.groupby("Usuário")["Evento"].shift(1)
        monitor_eventos_tratar["Sequencia"] = (
            (monitor_eventos_tratar["Evento"] == "Autenticação com sucesso")
            & (monitor_eventos_tratar["Evento_Anterior"] == "Autenticação com sucesso")
        )

        monitor_eventos_tratar_1 = monitor_eventos_tratar[monitor_eventos_tratar["Sequencia"]].copy()

        final_result = pd.DataFrame()
        sem_correspondencia = pd.DataFrame()

        # ------------------------------------------------------------------
        # Loop 1: sequências auth repetidas -> busca logout anterior mais próximo na produtividade
        # ------------------------------------------------------------------
        total_loop1 = len(monitor_eventos_tratar_1)
        for indice, (_, row) in enumerate(monitor_eventos_tratar_1.iterrows(), start=1):
            usuario = row["Usuário"]
            data_evento = row["Data do Evento"]

            user_events = prod_por_usuario.get(usuario)
            if user_events is None or user_events.empty:
                sem_correspondencia = pd.concat([sem_correspondencia, row.to_frame().T], ignore_index=True)
                continue

            user_events_anteriores = user_events[user_events["Data do Evento"] < data_evento].copy()
            if not user_events_anteriores.empty:
                user_events_anteriores["Time_Diff"] = (data_evento - user_events_anteriores["Data do Evento"])
                closest_event = user_events_anteriores.loc[user_events_anteriores["Time_Diff"].idxmin()]
                final_result = pd.concat(
                    [final_result, closest_event.to_frame().T, row.to_frame().T],
                    ignore_index=True
                )
            else:
                sem_correspondencia = pd.concat([sem_correspondencia, row.to_frame().T], ignore_index=True)

            if indice % 500 == 0:
                log.info(
                    f"[MonitorTratamento] Loop 1 progresso: {indice:,}/{total_loop1:,} "
                    f"({(indice / max(total_loop1, 1)) * 100:.1f}%)"
                )
                _set_status(
                    f"Loop 1 progresso: {indice:,}/{total_loop1:,} "
                    f"({(indice / max(total_loop1, 1)) * 100:.1f}%)"
                )

        if "Time_Diff" in final_result.columns:
            final_result.drop(columns=["Time_Diff"], inplace=True)

        # Normaliza final_result para ficar só com Logout (da produtividade)
        if not final_result.empty and {"Usuário", "Data do Evento", "Evento"}.issubset(final_result.columns):
            final_result = final_result.sort_values(by=["Usuário", "Data do Evento"]).copy()
            final_result["Evento_Anterior"] = final_result.groupby("Usuário")["Evento"].shift()
            final_result = final_result[final_result["Evento"] != final_result["Evento_Anterior"]].copy()
            final_result.drop(columns=["Evento_Anterior"], inplace=True)

            final_result = final_result[final_result["Evento"] == "Logout"].copy()
            final_result = final_result[colunas_filtradas]

        monitor_eventos_tratar = monitor_eventos_tratar[colunas_filtradas]

        # ------------------------------------------------------------------
        # Concatena monitor + logouts sintéticos encontrados (Loop 1)
        # ------------------------------------------------------------------
        concatenado = pd.concat([final_result, monitor_eventos_tratar], ignore_index=True)
        concatenado = concatenado.sort_values(by=["Usuário", "Data do Evento"]).reset_index(drop=True)

        # remove duplicatas simples
        concatenado = concatenado.drop_duplicates(subset=["Usuário", "Data do Evento", "Evento"], keep="first").copy()

        # ------------------------------------------------------------------
        # BLINDAGEM CRÍTICA: garantir datetime após concat (evita erro .dt)
        # ------------------------------------------------------------------
        concatenado["Data do Evento"] = pd.to_datetime(concatenado["Data do Evento"], errors="coerce")
        concatenado = concatenado.dropna(subset=["Data do Evento"]).copy()
        concatenado = concatenado.sort_values(["Usuário", "Data do Evento"]).reset_index(drop=True)

        # ------------------------------------------------------------------
        # Loop 1b: logouts consecutivos -> Auth sintética da produtividade no gap (L1, L2)
        # Somente adiciona sessão nova; não reatribui L1 nem altera sessões curtas existentes.
        # ------------------------------------------------------------------
        def _inserir_auth_sintetica_logouts_consecutivos(conc, protocolos_map):
            conc = conc.copy()
            conc["Data do Evento"] = pd.to_datetime(conc["Data do Evento"], errors="coerce")
            conc = conc.dropna(subset=["Data do Evento"]).copy()

            if "ID Sessão" in conc.columns:
                monitor_only = conc[conc["ID Sessão"] != "Detalhado Produtividade"].copy()
            else:
                monitor_only = conc.copy()

            if monitor_only.empty:
                return conc, {}

            monitor_only = monitor_only.sort_values(["Usuário", "Data do Evento"]).reset_index(drop=True)
            monitor_only["Evento_Anterior"] = monitor_only.groupby("Usuário")["Evento"].shift(1)
            monitor_only["Data_Anterior"] = monitor_only.groupby("Usuário")["Data do Evento"].shift(1)

            pares = monitor_only[
                (monitor_only["Evento"] == "Logout")
                & (monitor_only["Evento_Anterior"] == "Logout")
            ]

            novos = []
            reservas_l2 = {}
            loop1b_pares = 0
            loop1b_sinteticas = 0
            loop1b_ignorados = 0

            for _, row in pares.iterrows():
                loop1b_pares += 1
                usuario = row["Usuário"]
                l1 = pd.Timestamp(row["Data_Anterior"])
                l2 = pd.Timestamp(row["Data do Evento"])

                protocolos = protocolos_map.get(usuario)
                if protocolos is None or len(protocolos) == 0:
                    loop1b_ignorados += 1
                    continue

                protocolos = pd.to_datetime(protocolos, errors="coerce").dropna()
                prot_gap = protocolos[(protocolos > l1) & (protocolos < l2)]
                if prot_gap.empty:
                    loop1b_ignorados += 1
                    continue

                auth_time = prot_gap.min()
                novos.append({
                    "Usuário": usuario,
                    "Data do Evento": auth_time,
                    "Evento": "Autenticação com sucesso",
                    "ID Sessão": "Produtividade Auth",
                    "Objeto": "Autenticação via Detalhado Produtividade",
                })
                reservas_l2[(usuario, l2)] = auth_time
                loop1b_sinteticas += 1

            if novos:
                conc = pd.concat([conc, pd.DataFrame(novos)], ignore_index=True)
                conc["Data do Evento"] = pd.to_datetime(conc["Data do Evento"], errors="coerce")
                conc = conc.dropna(subset=["Data do Evento"]).copy()
                conc = conc.sort_values(["Usuário", "Data do Evento"]).reset_index(drop=True)

            log.info(
                f"[MonitorTratamento] Loop 1b resumo | pares={loop1b_pares:,} | "
                f"sinteticas={loop1b_sinteticas:,} | ignorados_sem_protocolo={loop1b_ignorados:,}"
            )
            return conc, reservas_l2

        concatenado, logouts_reservados_l2 = _inserir_auth_sintetica_logouts_consecutivos(
            concatenado, protocolos_por_usuario
        )

        # ------------------------------------------------------------------
        # AJUSTE (MADRUGADA / PRIMEIRA SESSÃO DO DIA) - OPÇÃO 2
        # Se existir protocolo no dia antes do primeiro evento do MONITOR, cria Auth sintética
        # no horário do PRIMEIRO protocolo do dia (min) que esteja < primeiro evento do monitor.
        # ------------------------------------------------------------------
        def _inserir_primeira_auth_do_dia_por_prod(conc, protocolos_map, dia_alvo=None):
            # blindagem (de novo, por segurança)
            conc["Data do Evento"] = pd.to_datetime(conc["Data do Evento"], errors="coerce")
            conc = conc.dropna(subset=["Data do Evento"]).copy()

            # Referência do 1º evento: somente MONITOR (exclui Detalhado Produtividade)
            if "ID Sessão" in conc.columns:
                monitor_only = conc[conc["ID Sessão"] != "Detalhado Produtividade"].copy()
            else:
                monitor_only = conc.copy()

            if monitor_only.empty:
                return conc

            monitor_only["Dia"] = monitor_only["Data do Evento"].dt.date
            conc["Dia"] = conc["Data do Evento"].dt.date

            if dia_alvo is not None:
                monitor_only = monitor_only[monitor_only["Dia"] == dia_alvo].copy()
                if monitor_only.empty:
                    conc.drop(columns=["Dia"], inplace=True, errors="ignore")
                    return conc

            primeiros = (
                monitor_only.sort_values(["Usuário", "Data do Evento"])
                .groupby(["Usuário", "Dia"], sort=False)["Data do Evento"]
                .first()
                .reset_index()
                .rename(columns={"Data do Evento": "PrimeiroEventoMonitor"})
            )

            novos = []
            for _, r in primeiros.iterrows():
                usuario = r["Usuário"]
                dia = r["Dia"]
                t0 = r["PrimeiroEventoMonitor"]

                protocolos = protocolos_map.get(usuario)
                if protocolos is None or len(protocolos) == 0:
                    continue

                # Protocolos do MESMO DIA e ANTES do primeiro evento do monitor
                # (opção 2 pede auth_time = primeiro protocolo do dia)
                protocolos = pd.to_datetime(protocolos, errors="coerce").dropna()
                prot_dia_antes = protocolos[(protocolos.dt.date == dia) & (protocolos < t0)]
                if prot_dia_antes.empty:
                    continue

                # Se já existe Auth antes do primeiro evento do monitor, não cria
                auths_antes = conc[
                    (conc["Usuário"] == usuario)
                    & (conc["Dia"] == dia)
                    & (conc["Evento"] == "Autenticação com sucesso")
                    & (conc["Data do Evento"] < t0)
                ]
                if not auths_antes.empty:
                    continue

                auth_time = prot_dia_antes.min()  # OPÇÃO 2

                novos.append({
                    "Usuário": usuario,
                    "Data do Evento": auth_time,
                    "Evento": "Autenticação com sucesso",
                    "ID Sessão": "Produtividade Inicial",
                    "Objeto": "Autenticação via Detalhado Produtividade",
                })

            if novos:
                conc = pd.concat([conc, pd.DataFrame(novos)], ignore_index=True)
                conc["Data do Evento"] = pd.to_datetime(conc["Data do Evento"], errors="coerce")
                conc = conc.dropna(subset=["Data do Evento"]).copy()
                conc = conc.sort_values(["Usuário", "Data do Evento"]).reset_index(drop=True)

            conc.drop(columns=["Dia"], inplace=True, errors="ignore")
            return conc

        from datetime import date as date_type
        if isinstance(data_obj, datetime):
            dia_alvo = data_obj.date()
        elif isinstance(data_obj, date_type):
            dia_alvo = data_obj
        else:
            dia_alvo = None
        concatenado = _inserir_primeira_auth_do_dia_por_prod(concatenado, protocolos_por_usuario, dia_alvo=dia_alvo)

        # ------------------------------------------------------------------
        # Loop 4: monta pares (Auth -> Logout)
        # Prioridade:
        #   1) Logout do MONITOR dentro da janela (Auth -> próxima Auth)
        #   2) Se não houver, usar PROD (fim do último protocolo dentro da janela)
        # Regras:
        #   - Limitar tudo à próxima Auth (não pegar logout da próxima sessão)
        #   - Logout do monitor é suficiente (não exige protocolo na janela)
        #   - Protocolo na janela só é exigido no fallback de produtividade (anti-fantasma)
        #   - AJUSTE 2: capar segundo evento em 23:59:59 do dia da auth
        # ------------------------------------------------------------------
        concatenado["Data segundo evento"] = pd.NaT
        concatenado["Segundo evento"] = ""

        total_loop4 = len(concatenado)
        sessoes_monitor = 0
        sessoes_snapshot = 0
        sessoes_prod_fallback = 0
        sessoes_descartadas = 0

        for i, row in concatenado.iterrows():
            if row["Evento"] != "Autenticação com sucesso":
                continue

            usuario = row["Usuário"]
            data_auth = row["Data do Evento"]

            # limite: próxima Auth do mesmo usuário
            prox_auth = concatenado[
                (concatenado["Usuário"] == usuario)
                & (concatenado["Data do Evento"] > data_auth)
                & (concatenado["Evento"] == "Autenticação com sucesso")
            ]
            limite = prox_auth.iloc[0]["Data do Evento"] if not prox_auth.empty else None

            # AJUSTE 2: cap do fim do dia da auth
            fim_do_dia_auth = pd.Timestamp(data_auth.date()) + pd.Timedelta(hours=23, minutes=59, seconds=59)

            # (1) Logout do MONITOR (exclui Detalhado Produtividade), dentro da janela
            filtro_logout_monitor = (
                (concatenado["Usuário"] == usuario)
                & (concatenado["Data do Evento"] > data_auth)
                & (concatenado["Evento"] == "Logout")
            )
            if "ID Sessão" in concatenado.columns:
                filtro_logout_monitor &= (concatenado["ID Sessão"] != "Detalhado Produtividade")

            if limite is not None:
                filtro_logout_monitor &= (concatenado["Data do Evento"] < limite)

            logouts_monitor = concatenado[filtro_logout_monitor].sort_values("Data do Evento")

            id_sessao_auth = str(row.get("ID Sessão", "") or "").strip()
            if logouts_reservados_l2 and not logouts_monitor.empty:
                def _logout_disponivel(logout_row):
                    logout_ts = pd.Timestamp(logout_row["Data do Evento"])
                    chave_reserva = (usuario, logout_ts)
                    if chave_reserva not in logouts_reservados_l2:
                        return True
                    auth_reservada = logouts_reservados_l2[chave_reserva]
                    return (
                        id_sessao_auth == "Produtividade Auth"
                        and pd.Timestamp(data_auth) == pd.Timestamp(auth_reservada)
                    )

                logouts_monitor = logouts_monitor[logouts_monitor.apply(_logout_disponivel, axis=1)]

            if not logouts_monitor.empty:
                data_seg = logouts_monitor.iloc[0]["Data do Evento"]
                if pd.notna(data_seg) and data_seg > fim_do_dia_auth:
                    data_seg = fim_do_dia_auth
                concatenado.at[i, "Data segundo evento"] = data_seg
                concatenado.at[i, "Segundo evento"] = "Logout"
                sessoes_monitor += 1
                continue

            # Última sessão do dia corrente ainda aberta no momento da extração:
            # fecha apenas o recorte do snapshot. Não usa o fim da produtividade,
            # pois ausência de produção não significa logout.
            if (
                limite is None
                and pd.notna(snapshot_ts)
                and pd.Timestamp(data_auth).date() == pd.Timestamp(snapshot_ts).date()
                and pd.Timestamp(snapshot_ts) > pd.Timestamp(data_auth)
            ):
                data_seg = min(pd.Timestamp(snapshot_ts), fim_do_dia_auth)
                concatenado.at[i, "Data segundo evento"] = data_seg
                concatenado.at[i, "Segundo evento"] = "Logout"
                sessoes_snapshot += 1
                continue

            # (2) Fallback: PROD (fim do último protocolo dentro da janela)
            # Exige protocolo na janela para evitar sessão fantasma
            protocolos_user = protocolos_por_usuario.get(usuario)
            tem_protocolo = False
            if protocolos_user is not None and len(protocolos_user) > 0:
                protocolos_user = pd.to_datetime(protocolos_user, errors="coerce").dropna()

                if limite is not None:
                    tem_protocolo = not protocolos_user[
                        (protocolos_user >= data_auth) & (protocolos_user < limite)
                    ].empty
                else:
                    tem_protocolo = not protocolos_user[
                        protocolos_user >= data_auth
                    ].empty

            if not tem_protocolo:
                sessoes_descartadas += 1
                continue

            prod_user = prod_por_usuario.get(usuario)
            if prod_user is None or prod_user.empty:
                sessoes_descartadas += 1
                continue

            if limite is not None:
                protocolos_validos = prod_user[
                    (prod_user["Data do Evento"] > data_auth)
                    & (prod_user["Data do Evento"] < limite)
                ].copy()
            else:
                protocolos_validos = prod_user[
                    (prod_user["Data do Evento"] > data_auth)
                ].copy()

            if protocolos_validos.empty:
                sessoes_descartadas += 1
                continue

            data_seg = protocolos_validos["Data do Evento"].max()

            if pd.notna(data_seg) and data_seg > fim_do_dia_auth:
                data_seg = fim_do_dia_auth

            concatenado.at[i, "Data segundo evento"] = data_seg
            concatenado.at[i, "Segundo evento"] = "Logout"
            sessoes_prod_fallback += 1

            if i > 0 and i % 2000 == 0:
                log.info(
                    f"[MonitorTratamento] Loop 4 progresso: {i:,}/{total_loop4:,} "
                    f"({(i / max(total_loop4, 1)) * 100:.1f}%)"
                )

        log.info(
            f"[MonitorTratamento] Loop 4 resumo | monitor={sessoes_monitor:,} | "
            f"snapshot={sessoes_snapshot:,} | "
            f"prod_fallback={sessoes_prod_fallback:,} | descartadas={sessoes_descartadas:,}"
        )

        # ------------------------------------------------------------------
        # Saída: somente Auth com logout preenchido
        # ------------------------------------------------------------------
        concatenado = concatenado[
            (concatenado["Evento"] == "Autenticação com sucesso")
            & (concatenado["Data segundo evento"].notna())
        ].copy()

        # Ajustes finais e escrita parquet
        concatenado["Data do Evento"] = pd.to_datetime(concatenado["Data do Evento"], errors="coerce")
        concatenado = concatenado.dropna(subset=["Data do Evento"]).copy()
        concatenado["Data"] = concatenado["Data do Evento"].dt.date
        concatenado["Hora"] = concatenado["Data do Evento"].dt.hour

        concatenado["Evento"] = "Autenticação com sucesso"
        concatenado["Segundo evento"] = "Logout"

        concatenado = concatenado[
            ["Data", "Hora", "Usuário", "Data do Evento", "Evento", "Data segundo evento", "Segundo evento"]
        ]

        caminho_out = os.path.join(
            str(PASTA_MONITOR_TRATADO), f"{PREFIXO_MONITOR_TRATADO}{data_arquivo}.parquet"
        )
        concatenado_parquet = _preparar_dataframe_para_parquet(concatenado)
        concatenado_parquet.to_parquet(str(caminho_out), index=False, compression="snappy")

        tempo_total = time.perf_counter() - inicio_tratamento
        log.info(
            f"[MonitorTratamento] Concluído | saída={caminho_out} | linhas={len(concatenado):,} | "
            f"sem_correspondência={len(sem_correspondencia):,} | tempo={tempo_total:.2f}s ✅ "
            f"Destino: {os.path.abspath(caminho_out)}"
        )
        _set_status(f"✅ Monitor tratado salvo | Destino: {os.path.abspath(caminho_out)}")
        return caminho_out

    except Exception as exc:
        log.error(f"Erro ao tratar monitor de eventos: {exc}", exc_info=True)
        _set_status(f"Erro ao tratar monitor: {exc}")
        return None

def _normalizar_df_monitor_sessoes(df: pd.DataFrame) -> pd.DataFrame:
    """Mantém apenas linhas de sessão válidas e o schema padrão do monitor unificado."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUNAS_MONITOR_UNIFICADO)

    df = df.copy()
    if "matricula" in df.columns and "Usuário" not in df.columns:
        df = df.rename(columns={"matricula": "Usuário"})

    evento = df.get("Evento", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    segundo = df.get("Segundo evento", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    data_evt = pd.to_datetime(df.get("Data do Evento"), errors="coerce")
    data_seg = pd.to_datetime(df.get("Data segundo evento"), errors="coerce")

    mask = (
        evento.str.contains("Autentica", case=False, na=False)
        & segundo.str.lower().eq("logout")
        & data_evt.notna()
        & data_seg.notna()
    )
    df = df[mask].copy()

    for coluna in COLUNAS_MONITOR_UNIFICADO:
        if coluna not in df.columns:
            df[coluna] = pd.NA

    return df[COLUNAS_MONITOR_UNIFICADO].reset_index(drop=True)

def _baixar_monitor_eventos_d1(drv):
    """
    Ao final das rotinas, volta ao menu, acessa o Monitor de Eventos,
    filtra em d-1 e baixa o arquivo bruto para DEFAULT_SHAREPOINT_BOTS.
    """
    try:
        wait = WebDriverWait(drv, TIMEOUT_DRIVER_ROTINA)
        wait_short = WebDriverWait(drv, TIMEOUT_SHORT_ROTINA)
        pasta_temp = DOWNLOADS_TEMP_ROTINA
        pasta_destino = PASTA_MONITOR_BRUTA
        pasta_destino.mkdir(parents=True, exist_ok=True)

        def _clicar_com_retry(locator, by=By.XPATH, tentativas=3, pausa=2, descricao="elemento"):
            ultimo_erro = None
            for tentativa in range(1, tentativas + 1):
                try:
                    elemento = WebDriverWait(drv, TIMEOUT_DRIVER_ROTINA).until(
                        EC.presence_of_element_located((by, locator))
                    )
                    drv.execute_script("arguments[0].scrollIntoView({behavior: 'auto', block: 'center'});", elemento)
                    time.sleep(0.5)
                    try:
                        elemento.click()
                    except Exception:
                        drv.execute_script("arguments[0].click();", elemento)
                    return True
                except Exception as exc:
                    ultimo_erro = exc
                    log.warning(f"Falha ao clicar em {descricao} (tentativa {tentativa}/{tentativas}): {exc}")
                    time.sleep(pausa)
            raise ultimo_erro

        _set_status("Voltando ao menu para baixar Monitor de Eventos")
        _set_progress(72, "Monitor de eventos: acessando tela")
        _limpar_downloads_temp_inicio("Extração Monitor de Eventos D-1")

        try:
            _clicar_com_retry(brflow.B_menu_rotina, by=By.XPATH, descricao="menu de rotinas")
            time.sleep(2)
        except Exception as exc:
            log.warning(f"Não foi possível voltar pelo menu de rotinas: {exc}. Tentando abrir monitor diretamente.")

        _clicar_com_retry(brflow.B_monitor, by=By.XPATH, descricao="menu monitor")

        wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_M_pesquisar)))

        data_d1 = _obter_data_base_execucao() - timedelta(days=1)
        data_monitor = data_d1.strftime("%d/%m/%Y")

        try:
            data_element = wait_short.until(EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_M_data)))
            try:
                data_element.click()
                data_element.send_keys(Keys.CONTROL, "a")
                data_element.send_keys(Keys.DELETE)
            except Exception:
                pass

            try:
                drv.execute_script(
                    "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles: true})); arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                    data_element,
                    data_monitor,
                )
            except Exception:
                data_element.send_keys(data_monitor)
        except Exception as exc:
            log.warning(f"Não foi possível preencher a data do monitor: {exc}")

        try:
            data_element_final = wait_short.until(EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_M_data_final)))
            try:
                data_element_final.click()
                data_element_final.send_keys(Keys.CONTROL, "a")
                data_element_final.send_keys(Keys.DELETE)
            except Exception:
                pass

            try:
                drv.execute_script(
                    "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', {bubbles: true})); arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                    data_element_final,
                    data_monitor,
                )
            except Exception:
                data_element_final.send_keys(data_monitor)
        except Exception as exc:
            log.warning(f"Não foi possível preencher a data final do monitor: {exc}")

        try:
            elemento_objeto = wait_short.until(EC.presence_of_element_located((By.CSS_SELECTOR, brflow.B_M_objeto)))
            drv.execute_script("arguments[0].click();", elemento_objeto)
        except Exception:
            log.debug("Objeto do monitor não encontrado ou não disponível")

        _limpar_downloads_temp_inicio("Extração Monitor de Eventos D-1")

        try:
            _clicar_com_retry(brflow.B_M_csv, by=By.ID, descricao="formato csv")
        except Exception:
            elemento_csv = wait.until(EC.element_to_be_clickable((By.ID, brflow.B_M_csv)))
            drv.execute_script("arguments[0].click();", elemento_csv)

        try:
            _clicar_com_retry(brflow.B_M_pesquisar, by=By.XPATH, descricao="pesquisar monitor")
        except Exception:
            elemento_pesquisar = wait.until(EC.element_to_be_clickable((By.XPATH, brflow.B_M_pesquisar)))
            drv.execute_script("arguments[0].click();", elemento_pesquisar)

        _set_status(f"Baixando monitor de eventos de {data_monitor}")
        _set_progress(76, f"Monitor de eventos: baixando arquivo de {data_monitor}")
        arquivo_baixado = _aguardar_download_completo(str(pasta_destino), timeout=120)

        if not arquivo_baixado:
            log.warning("Nenhum arquivo do monitor foi baixado")
            _set_status("Monitor de eventos: nenhum arquivo encontrado")
            _registrar_erro("⚠️ Monitor sem arquivo: nenhum arquivo retornado pelo monitor de eventos")
            return False

        caminho_baixado = Path(arquivo_baixado)
        nome_final = f"{PREFIXO_MONITOR_BRFLOW_BRUTO}{data_d1.strftime('%Y%m%d')}{caminho_baixado.suffix}"
        caminho_final = pasta_destino / nome_final

        if caminho_baixado.resolve() != caminho_final.resolve():
            if caminho_final.exists():
                caminho_final.unlink()
            shutil.move(str(caminho_baixado), str(caminho_final))

        log.info(f"✓ Monitor de eventos salvo: {caminho_final}")
        _set_status(f"Monitor de eventos salvo: {caminho_final.name}")
        caminho_produtividade = PASTA_PRODUTIVIDADE_D1_BRUTA / f"{PREFIXO_PROD_D1}{data_d1.strftime('%Y%m%d')}.parquet"
        caminho_tratado = None
        if caminho_produtividade.exists():
            _pretratar_monitor_verifica_usuario(str(caminho_final))
            caminho_tratado = tratar_arquivo(str(caminho_final), str(caminho_produtividade), data_d1)
        else:
            log.warning(f"Monitor tratado não executado: arquivo de produtividade não encontrado em {caminho_produtividade}")

        if caminho_final.suffix.lower() == ".csv" and caminho_final.exists():
            try:
                df_monitor_final = _ler_csv_tratamento(str(caminho_final))
                caminho_final_parquet = caminho_final.with_suffix('.parquet')
                df_monitor_final = _preparar_dataframe_para_parquet(df_monitor_final)
                df_monitor_final.to_parquet(str(caminho_final_parquet), index=False, compression='snappy')
                log.info(f"✅ Monitor bruto convertido para Parquet | Destino: {os.path.abspath(caminho_final_parquet)}")
                caminho_final.unlink()
                caminho_final = caminho_final_parquet
            except Exception as conv_exc:
                log.warning(f"Falha ao converter monitor bruto para parquet: {conv_exc}")

        _set_progress(78, "Monitor de eventos: concluido")
        if caminho_tratado and Path(caminho_tratado).is_file():
            _set_status(f"Monitor tratado salvo: {Path(caminho_tratado).name}")
            print(f"ROTINA_BRUTO_SAVED|monitor|{Path(caminho_tratado).resolve()}", flush=True)
        else:
            log.warning("Monitor tratado ausente — sync PostgreSQL não disparado")
            _set_status(f"Monitor bruto salvo (sem tratado): {caminho_final.name}")
        return True

    except Exception as e:
        log.error(f"Erro ao baixar monitor de eventos: {e}", exc_info=True)
        _set_status(f"Erro no monitor de eventos: {e}")
        _registrar_erro(f"❌ Erro Monitor de Eventos: {str(e)[:120]}")
        return False
