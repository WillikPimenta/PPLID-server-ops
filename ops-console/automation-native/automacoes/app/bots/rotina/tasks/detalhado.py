"""Tarefa BRBR-4467 detalhado (rotinas 1d/2d/3d)."""
from __future__ import annotations

import glob
import logging
import os
import shutil
import time
from pathlib import Path

from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA
from app.bots.rotina.io import (
    _combinar_arquivos_em_um,
    _extrair_dias_atras_da_descricao,
    _gerar_nome_arquivo_com_data,
    _limpar_downloads_temp_inicio,
    _mover_arquivos_para_pasta_final,
)
from app.bots.rotina.notifications import _task_id_por_descricao_rotina
from app.bots.rotina.selenium_brflow import _baixar_arquivos_rotina
from app.bots.rotina.state import (
    _registrar_erro,
    _registrar_status_tarefa,
    _set_progress,
    _set_status,
    parar_event,
)

log = logging.getLogger("robots.bot_rotina")

def _baixar_e_combinar_rotinas(drv, descricao="BRBR-4467 - Detalhado de registros Todos os clientes 1 Dia atrás"):
    #  Baixa arquivos de rotina, renomeia e processa na pasta temporária, depois move para pasta final.
    task_id = _task_id_por_descricao_rotina(descricao)
    try:
        pasta_temp = DOWNLOADS_TEMP_ROTINA
        _set_status(f"Iniciando download de rotinas: {descricao}")
        _set_progress(60, "Rotinas: procurando arquivos para download")
        dias_atras = _extrair_dias_atras_da_descricao(descricao)
        # Regra de negócio: 2 e 3 dias atrás devem mesclar com arquivos já existentes
        eh_incremental = dias_atras >= 2
        
        log.info(f"Rotina refere-se a {dias_atras} dias atrás - {'INCREMENTAL' if eh_incremental else 'COMPLETO'}")
        _set_status(f"Modo de download: {'INCREMENTAL (merge com existentes)' if eh_incremental else 'COMPLETO (substituir tudo)'}")
        _limpar_downloads_temp_inicio(f"Extração Rotina BRBR-4467 ({descricao})")
        
        # ====================================================================
        # LOOP COM RETRY PARA DOWNLOADS COMPLETOS COM PARTES FALTANTES
        # ====================================================================
        max_tentativas = 3
        tentativa = 0
        tempo_espera_retry = 3600  # 1 hora em segundos
        
        while tentativa < max_tentativas:
            tentativa += 1
            
            if tentativa > 1:
                # Retry: aguardar 1 hora antes de tentar novamente
                log.info(f"Tentativa {tentativa}/{max_tentativas} após aguardar 1 hora")
                _set_status(f"Aguardando 1 hora antes de tentar novamente... (Tentativa {tentativa}/{max_tentativas})")
                _set_progress(60, f"Rotinas: aguardando retry (tentativa {tentativa}/{max_tentativas})")
                
                # Aguardar 1 hora com pausas para verificar cancelamento
                tempo_decorrido = 0
                intervalo_pausa = 60  # Verificar a cada 1 minuto
                while tempo_decorrido < tempo_espera_retry:
                    if parar_event.is_set():
                        log.info("Download cancelado durante período de retry")
                        if task_id:
                            _registrar_status_tarefa(task_id, False, "cancelado")
                        return
                    
                    tempo_faltante = tempo_espera_retry - tempo_decorrido
                    minutos_faltantes = tempo_faltante // 60
                    _set_status(f"Aguardando retry... Faltam {minutos_faltantes} minutos (Tentativa {tentativa}/{max_tentativas})")
                    
                    time.sleep(min(intervalo_pausa, tempo_faltante))
                    tempo_decorrido += intervalo_pausa
            
            # Limpar pasta temporária para novo download
            _limpar_downloads_temp_inicio(f"Extração Rotina BRBR-4467 ({descricao})")
            
            arquivos = _baixar_arquivos_rotina(drv, descricao, str(pasta_temp))
            time.sleep(3)
            arquivos_na_pasta = []
            for arquivo in glob.glob(os.path.join(str(pasta_temp), "*.csv")) + \
                           glob.glob(os.path.join(str(pasta_temp), "*.xlsx")) + \
                           glob.glob(os.path.join(str(pasta_temp), "*.xls")):
                if os.path.isfile(arquivo):
                    tamanho = os.path.getsize(arquivo)
                    if tamanho > 0:
                        arquivos_na_pasta.append(arquivo)
            
            if not arquivos_na_pasta:
                _set_status("Nenhum arquivo encontrado na pasta")
                if not eh_incremental:
                    # Para completo, tentar novamente
                    if tentativa < max_tentativas:
                        continue
                if task_id:
                    _registrar_status_tarefa(task_id, False, "sem arquivo")
                return
            
            # Verificar se faltam partes - comportamento diferente para completo vs incremental
            num_arquivos = len(arquivos_na_pasta)
            if not eh_incremental and num_arquivos < 4:
                # COMPLETO: espera 4 partes
                partes_faltantes = 4 - num_arquivos
                
                if tentativa < max_tentativas:
                    # Ainda há tentativas disponíveis - retry
                    log.warning(f"Faltam {partes_faltantes} parte(s) da rotina: {descricao} - Tentando novamente em 1 hora (Tentativa {tentativa}/{max_tentativas})")
                    _set_status(f"⚠️ Faltam {partes_faltantes} parte(s) do download COMPLETO! Tentando novamente em 1 hora...")
                    continue  # Voltar ao topo do loop para retry
                else:
                    # Sem mais tentativas - desistir
                    log.error(f"Faltam {partes_faltantes} parte(s) após {max_tentativas} tentativas. Desistindo.")
                    _set_status(f"❌ Faltam {partes_faltantes} parte(s) após {max_tentativas} tentativas. Desistindo.")
                    _registrar_erro(f"❌ Download incompleto: {descricao} - faltam {partes_faltantes}/4 partes após {max_tentativas} tentativas")
                    if task_id:
                        _registrar_status_tarefa(task_id, False, "rotina não")
                    return
            elif eh_incremental:
                # INCREMENTAL: qualquer número de partes é esperado
                log.info(f"Download incremental: {num_arquivos} arquivo(s) encontrado(s) (podem ser apenas as partes que faltavam)")
                _set_status(f"Download incremental: processando {num_arquivos} arquivo(s)")
            
            # Se chegou aqui, o download está OK - sair do loop
            break
        
        # Ordenar arquivos por data de criação para manter sequência correta (1, 2, 3, 4)
        arquivos_na_pasta.sort(key=lambda x: os.path.getctime(x))
        _set_status(f"Processando {len(arquivos_na_pasta)} arquivo(s)")
        _set_progress(62, f"Rotinas: processando {len(arquivos_na_pasta)} arquivo(s)")
        
        # Renomear arquivos NA PASTA TEMPORÁRIA (não mover ainda)
        arquivos_renomeados = []
        for idx, arquivo_origem in enumerate(arquivos_na_pasta, start=1):
            try:
                # Gerar nome simples sem número de parte
                nome_novo = _gerar_nome_arquivo_com_data(dias_atras=dias_atras)
                ext = Path(arquivo_origem).suffix
                # Usar um nome único para evitar conflitos
                nome_novo_completo = f"{nome_novo}_parte{idx}{ext}"
                caminho_novo = pasta_temp / nome_novo_completo
                log.info(f"Renomeando: {os.path.basename(arquivo_origem)} → {nome_novo_completo}")
                shutil.move(arquivo_origem, str(caminho_novo))
                _set_status(f"Arquivo {idx} renomeado: {nome_novo_completo}")
                log.info(f"✓ Arquivo renomeado com sucesso: {caminho_novo}")
                arquivos_renomeados.append(str(caminho_novo))
                
            except Exception as e:
                log.error(f"Erro ao renomear arquivo {idx}: {e}")
                _set_status(f"Erro ao renomear arquivo {idx}: {e}")
                _registrar_erro(f"❌ Erro ao renomear arquivo {idx}: {str(e)[:80]}")
        
        _set_progress(63, "Rotinas: renomeando e consolidando arquivos")
        _set_status(f"Todos os {len(arquivos_na_pasta)} arquivo(s) renomeados! Combinando dados...")
        
        # Combinar arquivos NA PASTA TEMPORÁRIA
        _combinar_arquivos_em_um(pasta_temp)
        
        # APÓS PROCESSAR, mover arquivos consolidados para pasta final
        _set_progress(64, "Rotinas: movendo arquivos para pasta final")
        _set_status("Movendo arquivos consolidados para pasta final...")
        _mover_arquivos_para_pasta_final(pasta_temp, eh_incremental=eh_incremental)
        if task_id:
            _registrar_status_tarefa(task_id, True)
    
    except Exception as e:
        log.error(f"Erro ao processar rotinas: {e}", exc_info=True)
        _set_status(f"Erro no processamento de rotinas: {e}")
        if task_id:
            _registrar_status_tarefa(task_id, False, str(e)[:30])
