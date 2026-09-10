"""Orquestração: start, stop, ciclo principal."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

from selenium.common.exceptions import TimeoutException, WebDriverException

from app.config import *
from app.core.common import MetricsContext, safe_close_driver
from app.infrastructure.selenium_helpers import create_driver, take_error_screenshot
from app.infrastructure.teams_notifier import notify
from app.bots.rotina.constants import (
    DOWNLOADS_TEMP_ROTINA,
    HORA_EXECUCAO_ROTINA,
    MINUTO_EXECUCAO_ROTINA,
    TAREFAS_DEMANDAS,
    TASK_DEFAULT_ALL,
    TASK_DEPENDENCIES,
)
from app.bots.rotina.io import _get_tz_br, _limpar_pasta, _resolver_datas_execucao
from app.bots.rotina.notifications import (
    _gerar_resumo_notificacao_erro,
    _gerar_resumo_notificacao_sucesso,
    _teams_notificacao_habilitada,
)
from app.bots.rotina.selenium_brflow import _fazer_login, _navegar_para_brflow
from app.bots.rotina.state import (
    _inicializar_status_tarefas,
    _registrar_erro,
    _registrar_status_tarefa,
    _reset_exec_summary,
    _reset_progress_state,
    _set_progress,
    _set_status,
    _timing_end_step,
    _timing_start_step,
    exec_summary,
    parar_event,
)
from app.bots.rotina.tasks import execute_task

log = logging.getLogger("robots.bot_rotina")


def start(settings=None):
    """
    Inicia o robô de rotina com agendamento diário às 08:00 ou execução imediata.
    
    Loop que:
    1. Se executar_imediatamente=True: executa uma vez e finaliza
    2. Se executar_imediatamente=False: aguarda até 08:00, executa e repete diariamente
    
    Args:
        settings (dict, optional): Configurações com:
            - 'matricula': matrícula do usuário
            - 'senha': senha do usuário  
            - 'executar_imediatamente': Se True, executa imediatamente. Se False, aguarda horário agendado.
            - 'rotina_data_inicio': data inicial (YYYY-MM-DD) para execução por período
            - 'rotina_data_fim': data final (YYYY-MM-DD) para execução por período
    """
    # Verificar se deve executar imediatamente
    executar_imediatamente = False
    if settings and isinstance(settings, dict):
        executar_imediatamente = settings.get('executar_imediatamente', False)
    
    hora_formatada = f"{HORA_EXECUCAO_ROTINA:02d}:{MINUTO_EXECUCAO_ROTINA:02d}"
    
    if executar_imediatamente:
        # MODO IMEDIATO: Executar uma vez e finalizar
        _set_status(f"🚀 Robô Rotina - Execução IMEDIATA")
        log.info(f"Robô Rotina iniciado em modo IMEDIATO (executa agora e finaliza)")
        
        try:
            log.info("Iniciando execução imediata da rotina de download")
            _set_status(f"Executando rotina de download AGORA...")
            executar_novo_bot(settings=settings)
            
            _set_status(f"✅ Rotina concluída - Robô finalizado")
            log.info("Rotina concluída com sucesso - Modo imediato finalizado")
            
        except Exception as e:
            log.error(f"Erro na execução imediata: {e}", exc_info=True)
            _set_status("❌ Erro na execução - Robô finalizado")
        
        return  # Finaliza após execução única
    
    # MODO AGENDADO: Loop infinito executando diariamente às 08:00
    _set_status(f"⏰ Robô Rotina - Modo AGENDADO (execução diária às {hora_formatada})")
    log.info(f"Robô Rotina iniciado em modo AGENDADO para execução diária às {hora_formatada}")
    # notify("Rotina", "info", "Robô Rotina Iniciado", f"Agendador ativo - Execução diária às {hora_formatada}")
    
    tz_br = _get_tz_br()
    ultima_execucao_data = None
    while not parar_event.is_set():
        try:
            # Aguardar o horário de execução e só prosseguir se não for cancelado
            if not _aguardar_horario_execucao():
                break  # Cancelado pelo usuário

            hoje = datetime.now(tz_br).date()
            if ultima_execucao_data == hoje:
                _set_status(f"Execução de hoje já realizada ({hora_formatada}) - Aguardando próximo dia")
                log.info("Execução do dia já realizada, aguardando próximo dia")
                time.sleep(60)
                continue

            log.info("Iniciando execução da rotina de download")
            _set_status(f"Executando rotina de download às {hora_formatada}")
            exec_settings = {**(settings or {})}
            if not exec_settings.get("rotina_data_inicio") and not exec_settings.get("rotina_data_fim"):
                exec_settings["rotina_data_inicio"] = hoje.isoformat()
                exec_settings["rotina_data_fim"] = hoje.isoformat()
            executar_novo_bot(settings=exec_settings)
            ultima_execucao_data = hoje

            if parar_event.is_set():
                break

            _set_status(f"Rotina concluída - Aguardando próximo dia ({hora_formatada})")
            log.info("Rotina concluída com sucesso - Aguardando próxima execução")
            time.sleep(60)

        except Exception as e:
            log.error(f"Erro no loop de agendamento: {e}", exc_info=True)
            try:
                notify("Rotina", "erro", "❌ Erro no agendador", f"Erro: {str(e)[:100]}")
            except Exception as exc:
                log.warning(f"Falha ao enviar notificação de erro do agendador: {exc}")
            if not parar_event.is_set():
                _set_status("Erro no agendador - Aguardando 5 minutos antes de tentar novamente")
                time.sleep(300)

    log.info("Robô Rotina finalizado pelo usuário")
    _set_status("Robô Rotina finalizado")


def _aguardar_horario_execucao():
    """
    Aguarda até às 08:00 do dia atual ou do próximo dia.
    Mostra contagem regressiva durante a espera.
    
    Returns:
        bool: True se alcançou horário, False se foi cancelado
    """
    tz_br = _get_tz_br()
    
    while not parar_event.is_set():
        agora = datetime.now(tz_br)
        horario_execucao = agora.replace(hour=HORA_EXECUCAO_ROTINA, minute=MINUTO_EXECUCAO_ROTINA, second=0, microsecond=0)
        
        # Se já passou da hora de execução hoje, aplicar tolerância
        # para não perder a execução caso o loop acorde alguns segundos depois.
        if agora > horario_execucao:
            atraso = (agora - horario_execucao).total_seconds()
            if atraso <= 60:
                _set_status(f"Horário de execução alcançado ({HORA_EXECUCAO_ROTINA:02d}:{MINUTO_EXECUCAO_ROTINA:02d}) - Iniciando rotina")
                log.info(f"Horário de execução ({HORA_EXECUCAO_ROTINA:02d}:{MINUTO_EXECUCAO_ROTINA:02d}) alcançado (atraso {int(atraso)}s)")
                return True
            horario_execucao += timedelta(days=1)
        
        diferenca = (horario_execucao - agora).total_seconds()
        
        # Se falta menos de 1 segundo, chegou a hora
        if diferenca <= 1:
            _set_status(f"Horário de execução alcançado ({HORA_EXECUCAO_ROTINA:02d}:{MINUTO_EXECUCAO_ROTINA:02d}) - Iniciando rotina")
            log.info(f"Horário de execução ({HORA_EXECUCAO_ROTINA:02d}:{MINUTO_EXECUCAO_ROTINA:02d}) alcançado")
            return True
        
        # Formatar contagem regressiva
        horas = int(diferenca // 3600)
        minutos = int((diferenca % 3600) // 60)
        segundos = int(diferenca % 60)
        
        msg = f"Aguardando até às {HORA_EXECUCAO_ROTINA:02d}:{MINUTO_EXECUCAO_ROTINA:02d} - Faltam {horas:02d}h:{minutos:02d}m:{segundos:02d}s"
        _set_status(msg)
        
        # Intervalo de atualização: 10s nos últimos 5 minutos, 30s antes disso
        intervalo = INTERVALO_ATUALIZACAO_FINAL_ROTINA if diferenca <= TEMPO_LIMITE_COUNTDOWN_ROTINA else INTERVALO_ATUALIZACAO_COUNTDOWN_ROTINA
        parar_event.wait(intervalo)
    
    return False  # Cancelado pelo usuário


def stop():
    parar_event.set()


# ============================================================================
# EXECUÇÃO DO CICLO PRINCIPAL
# ============================================================================



def executar_novo_bot(settings=None):
    """
    Executa uma ou mais iterações completas do robô de rotina.
    
    Args:
        settings (dict, optional): Configurações com 'matricula' e 'senha'
    """
    # Limpar pasta temporária de downloads antes de qualquer execução,
    # evitando mistura de arquivos de execuções anteriores.
    _pasta_temp_global = DOWNLOADS_TEMP_ROTINA
    try:
        _pasta_temp_global.mkdir(parents=True, exist_ok=True)
        _limpar_pasta(str(_pasta_temp_global))
        log.info(f"Pasta temporária da rotina limpa antes do início: {_pasta_temp_global}")
    except Exception as _e:
        log.warning(f"Não foi possível limpar pasta temporária da rotina antes do início: {_e}")

    datas_execucao = _resolver_datas_execucao(settings=settings)
    total_dias = len(datas_execucao)

    if total_dias > 1:
        periodo_inicio = datetime.strptime(datas_execucao[0], "%Y-%m-%d").strftime("%d/%m/%Y")
        periodo_fim = datetime.strptime(datas_execucao[-1], "%Y-%m-%d").strftime("%d/%m/%Y")
        _set_status(f"Execução por período: {periodo_inicio} até {periodo_fim} ({total_dias} dias)")
        log.info(f"Execução da rotina em período: {periodo_inicio} até {periodo_fim} ({total_dias} dias)")

    for indice_dia, data_execucao in enumerate(datas_execucao, start=1):
        if parar_event.is_set():
            break

        data_legivel = datetime.strptime(data_execucao, "%Y-%m-%d").strftime("%d/%m/%Y")
        _set_status(f"Processando dia {indice_dia}/{total_dias}: {data_legivel}")
        log.info(f"Iniciando execução do dia {indice_dia}/{total_dias}: {data_legivel}")
        _executar_iteracao_unica(settings=settings, data_execucao=data_execucao)


def _executar_iteracao_unica(settings=None, data_execucao=None):
    """Executa um único dia da rotina de ponta a ponta."""
    if data_execucao:
        os.environ["ROTINA_DATA_EXECUCAO"] = str(data_execucao)

    _reset_progress_state()
    _set_status("Iniciando bot rotina")
    _set_progress(0, "Rotina: iniciando execucao")
    _reset_exec_summary()
    
    with MetricsContext("bot_rotina") as exec_ctx:
        drv = None
        execucao_sucesso = True
        try:
            drv = _executar_ciclo(exec_ctx, settings, data_execucao=data_execucao)
        except (TimeoutException, WebDriverException) as e:
            execucao_sucesso = False
            log.error(f"Erro Selenium: {e}")
            _registrar_erro(f"❌ Erro Selenium: {str(e)[:100]}")
            if drv:
                try:
                    take_error_screenshot(drv, "log_screenshots", "bot_rotina_selenium_erro")
                except Exception:
                    pass
        except Exception as e:
            execucao_sucesso = False
            log.error(f"Erro inesperado: {e}", exc_info=True)
            _registrar_erro(f"❌ Erro inesperado: {str(e)[:100]}")
            if drv:
                try:
                    take_error_screenshot(drv, "log_screenshots", "bot_rotina_erro")
                except Exception:
                    pass
        finally:
            if drv:
                try:
                    safe_close_driver(drv)
                    log.info("Driver fechado com sucesso")
                except Exception as e:
                    log.error(f"Erro ao fechar driver: {e}")
            _set_status("Bot finalizado")
            _set_progress(100, "Rotina: processamento completo")
            erros = exec_summary.get("erros", [])
            if _teams_notificacao_habilitada(settings):
                if execucao_sucesso and not erros:
                    titulo, adaptive_card = _gerar_resumo_notificacao_sucesso()
                    try:
                        notify("Rotina", "sucesso", titulo, adaptive_card=adaptive_card)
                    except Exception as exc:
                        log.warning(f"Falha ao enviar notificação de sucesso: {exc}")
                else:
                    if not erros:
                        erros = ["Erro desconhecido - verifique os logs"]
                    titulo, adaptive_card = _gerar_resumo_notificacao_erro(erros)
                    try:
                        notify("Rotina", "erro", titulo, adaptive_card=adaptive_card)
                    except Exception as exc:
                        log.warning(f"Falha ao enviar notificação de erro: {exc}")
            else:
                log.info("Notificação Teams suprimida (execução imediata)")


def _parse_tarefas(settings):
    """
    Parse da lista de tarefas a partir da configuração.
    
    Se não houver lista explícita, executa TODAS as tarefas (compatível com versão anterior).
    
    Args:
        settings (dict): Configurações do robô
        
    Returns:
        list: Lista de tarefas selecionadas (IDs) ou [] se inválido
    """
    if not settings or not isinstance(settings, dict):
        # Sem settings = executar todas (comportamento padrão)
        return list(TASK_DEFAULT_ALL)
    
    tarefas_bruto = settings.get("tarefas", None)
    
    # Se não foi especificado nada, executar todas
    if tarefas_bruto is None:
        return list(TASK_DEFAULT_ALL)
    
    # Se for string "todas", executar todas
    if isinstance(tarefas_bruto, str) and tarefas_bruto.strip().lower() == "todas":
        return list(TASK_DEFAULT_ALL)
    
    # Converter para lista se for string única
    if isinstance(tarefas_bruto, str):
        tarefas_bruto = [tarefas_bruto]
    
    # Validar que é lista
    if not isinstance(tarefas_bruto, list):
        log.warning(f"tarefas deve ser lista, recebido {type(tarefas_bruto)}")
        return list(TASK_DEFAULT_ALL)

    # Lista vazia = usuário desmarcou tudo explicitamente
    if len(tarefas_bruto) == 0:
        log.warning("Lista de tarefas vazia — nenhuma tarefa será executada")
        return []
    
    # Normalizar e filtrar tarefas válidas
    tarefas_validas = [
        str(t).strip().lower()
        for t in tarefas_bruto
        if str(t).strip().lower() in TASK_DEFAULT_ALL
    ]
    
    if not tarefas_validas:
        log.warning(
            "Nenhuma tarefa válida em %s — nenhuma tarefa será executada",
            tarefas_bruto,
        )
        return []
    
    return tarefas_validas


def _resolve_deps(tarefas):
    """
    Resolve dependências automáticamente: adiciona tarefas dependidas à lista.
    
    Exemplo:
    - Se usuário quer TASK_MONITOR_EVENTOS, adiciona TASK_PRODUTIVIDADE_D1 automaticamente
    - Se usuário quer TASK_LOG_EVENTOS, adiciona TASK_CONFER_PRODUCAO automaticamente
    
    Args:
        tarefas (list): Lista de tarefas solicitadas
        
    Returns:
        list: Lista com tarefas + dependências resolvidas (sem duplicatas)
    """
    if not tarefas:
        return []
    
    # Conjunto com resultado (para evitar duplicatas)
    resultado = set(tarefas)
    
    # Filas para processar dependências
    pendentes = list(tarefas)
    processadas = set()
    
    while pendentes:
        tarefa = pendentes.pop(0)
        
        if tarefa in processadas:
            continue
        
        processadas.add(tarefa)
        
        # Buscar dependências desta tarefa
        deps = TASK_DEPENDENCIES.get(tarefa, [])
        
        for dep in deps:
            if dep not in resultado:
                resultado.add(dep)
                pendentes.append(dep)
        
        if tarefa in TASK_DEPENDENCIES:
            log.debug(f"Tarefa '{tarefa}' requer: {deps}")
    
    # Retornar em ordem original (TASK_DEFAULT_ALL) para manter sequência consistente
    resultado_ordenado = [t for t in TASK_DEFAULT_ALL if t in resultado]
    
    if resultado_ordenado != tarefas:
        deps_adicionadas = set(resultado_ordenado) - set(tarefas)
        log.info(f"⚙️ Dependências resolvidas automaticamente: {deps_adicionadas}")
    
    return resultado_ordenado


def _executar_ciclo(exec_ctx, settings, data_execucao=None):
    """
    Executa um ciclo completo: driver → login → navegação → download de rotinas.
    
    NOVO: Tarefas selecionáveis com resolução automática de dependências
    
    Args:
        exec_ctx: Contexto de execução
        settings (dict): Configurações com credenciais + tarefas
        data_execucao (str, optional): Data em formato YYYY-MM-DD
        
    Returns:
        WebDriver: Driver criado (para ser fechado pelo caller)
    """
    s = settings or {}
    tz_br = _get_tz_br()
    matricula = s.get("matricula") or os.getenv("NIVEL_USER")
    senha = s.get("senha") or os.getenv("NIVEL_PASS")

    # ============================================================================
    # PARSE E RESOLUÇÃO DE TAREFAS
    # ============================================================================
    tarefas_recebidas = settings.get("tarefas") if settings and isinstance(settings, dict) else None
    tarefas_solicitadas = _parse_tarefas(settings)
    tarefas_final = _resolve_deps(tarefas_solicitadas)
    log.info(
        "Tarefas recebidas: %s | solicitadas: %s | executáveis (após deps): %s",
        tarefas_recebidas,
        tarefas_solicitadas,
        tarefas_final,
    )

    if not tarefas_final:
        log.warning("Nenhuma tarefa para executar — ciclo abortado")
        _set_status("Nenhuma tarefa selecionada para execução")
        _set_progress(100, "Rotina: nenhuma tarefa selecionada")
        return None

    _inicializar_status_tarefas(tarefas_final)
    
    _set_status(f"Tarefas a executar: {len(tarefas_final)} (com dependências resolvidas)")
    log.info(f"Tarefas executáveis: {tarefas_final}")
    
    # ============================================================================
    # CRIAR WEBDRIVER
    # ============================================================================
    _timing_start_step("WebDriver Creation")
    download_folder = str(DOWNLOADS_TEMP_ROTINA)
    headless_raw = (os.getenv("ROTINA_HEADLESS") or os.getenv("ROBOT_HEADLESS") or os.getenv("HEADLESS") or "0").strip().lower()
    drv = create_driver(headless=headless_raw in ("1", "true", "yes", "on"), download_dir=download_folder)
    _timing_end_step("WebDriver Creation")
    exec_ctx.record_step("driver_creation", success=True)
    _set_progress(5, "Infra: driver iniciado")
    
    # ============================================================================
    # VALIDAR SELETORES
    # ============================================================================
    if okta is None or brflow is None:
        _set_status("Seletores (path.py) não configurados")
        exec_ctx.record_step("config_validation", success=False, error_msg="Seletores não configurados")
        return drv
    
    # ============================================================================
    # FAZER LOGIN
    # ============================================================================
    _timing_start_step("Okta Authentication")
    _fazer_login(drv, matricula, senha, tz_br)
    _timing_end_step("Okta Authentication")
    exec_ctx.record_step("authentication", success=True)
    _set_progress(22, "Autenticacao: Okta concluido")
    
    # ============================================================================
    # NAVEGAR PARA BRFLOW
    # ============================================================================
    _timing_start_step("BRFlow Navigation")
    _navegar_para_brflow(drv, data_execucao=data_execucao)
    _timing_end_step("BRFlow Navigation")
    exec_ctx.record_step("brflow_navigation", success=True)
    _set_progress(58, "BRFlow: menu de rotinas acessado")
    
    # ============================================================================
    # EXECUTAR TAREFAS SELECIONADAS (NOVO)
    # ============================================================================
    total_tarefas = len(tarefas_final)
    tarefas_executadas = 0
    
    for idx, tarefa_id in enumerate(tarefas_final, start=1):
        try:
            _set_progress(0, f"Tarefa {idx}/{total_tarefas}: {tarefa_id}")
            
            result = execute_task(drv, tarefa_id, settings)
            if result.should_break:
                break
            if not result.ok:
                continue

            tarefas_executadas += 1
            _set_status(f"✓ Tarefa {idx}/{total_tarefas} concluída: {tarefa_id}")
            time.sleep(2)
            
        except Exception as e:
            log.error(f"❌ Erro ao processar tarefa {idx}/{total_tarefas} ({tarefa_id}): {e}")
            _set_status(f"❌ Erro na tarefa {idx}: {str(e)[:80]}")
            _registrar_erro(f"❌ Erro tarefa {idx}/{total_tarefas} ({tarefa_id}): {str(e)[:100]}")
            if tarefa_id in TAREFAS_DEMANDAS:
                _registrar_status_tarefa(tarefa_id, False, str(e)[:30])
    
    # ============================================================================
    # FINALIZAR
    # ============================================================================
    _set_progress(98, "Rotina: finalizando processamento")
    _set_status(f"✓ Processamento concluído: {tarefas_executadas}/{total_tarefas} tarefas executadas com sucesso")
    log.info(f"Bot chegou ao menu do BRFlow e concluiu o processamento com sucesso! ({tarefas_executadas}/{total_tarefas} tarefas)")
    
    return drv

