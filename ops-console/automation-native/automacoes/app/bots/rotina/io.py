"""I/O de arquivos, parquet, downloads e utilitários."""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.config import *
from app.infrastructure.file_mirror import espelhar_arquivo
from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA, EXPECTED_COLUMNS
from app.bots.rotina.csv_merge import CSVReader, MergeInteligente
from app.bots.rotina.notifications import _contar_linhas_arquivo
from app.bots.rotina.state import (
    _registrar_arquivo_resumo,
    _registrar_duplicatas_internas,
    _registrar_erro,
    _set_progress,
    _set_status,
)

log = logging.getLogger("robots.bot_rotina")

def _get_tz_br():
    """
    Retorna timezone de Brasília.
    Usa zoneinfo quando disponível (Python 3.9+), senão offset fixo -03:00.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Sao_Paulo")
    except Exception:
        return timezone(timedelta(hours=-3))


def _criar_pastas_necessarias(*pastas):
    """Cria múltiplas pastas se não existirem."""
    for pasta in pastas:
        Path(pasta).mkdir(parents=True, exist_ok=True)


def _limpar_pasta(pasta):
    """Remove todos os arquivos de uma pasta."""
    for arquivo in glob.glob(os.path.join(pasta, "*")):
        try:
            os.remove(arquivo)
            log.debug(f"Arquivo removido: {arquivo}")
        except Exception as e:
            log.warning(f"Erro ao remover {arquivo}: {e}")


def _limpar_downloads_temp_inicio(contexto: str = "extração"):
    """Limpa apenas a subpasta temporária da rotina para evitar mistura com outros robôs."""
    pasta_temp = DOWNLOADS_TEMP_ROTINA
    try:
        pasta_temp.mkdir(parents=True, exist_ok=True)
        _limpar_pasta(str(pasta_temp))
        log.info(f"{contexto}: pasta temporária da rotina limpa antes do download ({pasta_temp})")
    except Exception as exc:
        log.warning(f"{contexto}: falha ao limpar pasta temporária da rotina ({pasta_temp}): {exc}")


# ============================================================================
# PROCESSAMENTO DE ARQUIVOS CSV
# ============================================================================

def _fix_single_column_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """xxx
    Se o DataFrame tem apenas 1 coluna com valores separados por vírgula ou ponto e vírgula,
    faz o split inteligente respeitando quoted strings e recria as colunas com os nomes esperados.
    """
    if len(df.columns) != 1:
        log.debug(f"_fix_single_column_dataframe: DataFrame tem {len(df.columns)} colunas, sem necessidade de correção")
        return df
    
    col_name = df.columns[0]
    if len(df) == 0:
        log.warning("_fix_single_column_dataframe: DataFrame vazio")
        return df
    
    first_value = str(df[col_name].iloc[0])
    
    # Detectar o delimitador correto
    delimiter = ','
    
    # Contar delimitadores
    semicolon_count = first_value.count(';')
    comma_count = first_value.count(',')
    tab_count = first_value.count('\t')
    pipe_count = first_value.count('|')
    
    # Escolher o delimitador mais provável
    if semicolon_count >= len(EXPECTED_COLUMNS) - 1:
        delimiter = ';'
    elif comma_count >= len(EXPECTED_COLUMNS) - 1:
        delimiter = ','
    elif tab_count >= len(EXPECTED_COLUMNS) - 1:
        delimiter = '\t'
    elif pipe_count >= len(EXPECTED_COLUMNS) - 1:
        delimiter = '|'
    else:
        log.error(f"Nenhum delimitador adequado encontrado para {len(EXPECTED_COLUMNS)} colunas")
        return df
    
    log.info(f"CSV convertido com delimitador detectado")
    
    try:
        import re
        
        def split_csv_line(line: str, delim: str):
            """
            Split respeitando quoted strings.
            Separa pelo delimitador especificado, mas não separa se estiver dentro de aspas duplas.
            """
            line = str(line)
            parts = []
            current = ""
            in_quotes = False
            i = 0
            
            while i < len(line):
                char = line[i]
                
                if char == '"':
                    # Verificar escape (duas aspas seguidas = aspas literais)
                    if i + 1 < len(line) and line[i + 1] == '"':
                        current += '""'
                        i += 2
                        continue
                    else:
                        in_quotes = not in_quotes
                        current += char
                elif char == delim and not in_quotes:
                    # Delimitador fora de aspas = separador
                    parts.append(current.strip())
                    current = ""
                else:
                    current += char
                
                i += 1
            
            if current or len(parts) > 0:
                parts.append(current.strip())
            
            return parts
        
        # Converter coluna em lista de linhas com split inteligente usando o delimitador detectado
        split_data = df[col_name].apply(lambda x: split_csv_line(x, delimiter))
        
        # Máximo de colunas detectado
        max_cols = split_data.apply(len).max()
        
        # Padronizar para o número esperado de colunas
        if max_cols >= len(EXPECTED_COLUMNS):
            # Se tem mais colunas que esperado, juntar as extras na última coluna
            standardized = []
            for row in split_data:
                if len(row) >= len(EXPECTED_COLUMNS):
                    # Usar o delimitador correto para juntar as extras
                    standardized_row = row[:len(EXPECTED_COLUMNS)-1] + [delimiter.join(row[len(EXPECTED_COLUMNS)-1:])]
                else:
                    standardized_row = row + [''] * (len(EXPECTED_COLUMNS) - len(row))
                standardized.append(standardized_row)
            split_data = pd.Series(standardized)
            log.debug(f"Colunas extras foram juntadas na última coluna")
        elif max_cols == len(EXPECTED_COLUMNS):
            # Número exato de colunas - ótimo!
            log.debug("Número exato de colunas")
            pass
        else:
            # Menos colunas que esperado
            log.warning(f"_fix_single_column_dataframe: Menos colunas que esperado: {max_cols} vs {len(EXPECTED_COLUMNS)}")
            return df
        
        # Criar novo DataFrame com as colunas nomeadas
        result_df = pd.DataFrame(split_data.tolist(), columns=EXPECTED_COLUMNS)
        
        # Remover aspas duplas escapadas e limpar espaços (também remove aspas simples extras)
        result_df = result_df.applymap(
            lambda x: str(x).replace('""', '"').strip().strip('"') if pd.notna(x) else x
        )
        
        return result_df
    except Exception as e:
        log.error(f"Erro ao fazer split da coluna única: {e}")
        return df

def verifica_usuario(usuario) -> bool:
    """Valida usuário no padrão de matrícula: c + dígitos + letra (ex.: c91123a)."""
    valor_norm = re.sub(r'[^A-Za-z0-9]', '', str(usuario or '')).lower().strip()
    return bool(re.match(r'^c\d+[a-z]$', valor_norm))


def parse_datetime(valor):
    """Converte valores diversos para datetime de forma tolerante."""
    dt = pd.to_datetime(valor, errors="coerce", dayfirst=True)
    return dt


def _corrigir_texto_mojibake(valor):
    """Corrige texto com dupla decodificação (ex.: AutenticaÃ§Ã£o -> Autenticação)."""
    if not isinstance(valor, str):
        return valor

    texto = valor.replace("\ufeff", "").replace("ï»¿", "").strip()
    if not texto:
        return texto

    marcadores = ("Ã", "Â", "ï»¿")
    if any(m in texto for m in marcadores):
        try:
            convertido = texto.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
            if convertido:
                score_atual = sum(texto.count(m) for m in marcadores)
                score_convertido = sum(convertido.count(m) for m in marcadores)
                if score_convertido <= score_atual:
                    texto = convertido
        except Exception:
            pass

    return texto


def _normalizar_nome_coluna(coluna) -> str:
    """Normaliza nome de coluna removendo BOM, aspas extras e mojibake."""
    texto = _corrigir_texto_mojibake(str(coluna or ""))
    return texto.strip().strip('"').strip("'").strip()


def _ler_csv_tratamento(caminho: str) -> pd.DataFrame:
    """Lê CSV/Parquet com fallback para cenários BRFlow."""
    if str(caminho).lower().endswith('.parquet'):
        return pd.read_parquet(caminho)

    tentativas = [
        {"encoding": "utf-8-sig", "sep": ";"},
        {"encoding": "utf-8", "sep": ";"},
        {"encoding": "1252", "sep": ";"},
    ]
    ultimo_erro = None
    for params in tentativas:
        try:
            df = pd.read_csv(caminho, **params)
            df.columns = [_normalizar_nome_coluna(col) for col in df.columns]
            if "Evento" in df.columns:
                df["Evento"] = df["Evento"].apply(_corrigir_texto_mojibake)
            return df
        except Exception as exc:
            ultimo_erro = exc
    raise ultimo_erro

def _aguardar_download_completo(pasta_destino_final, timeout=TIMEOUT_DOWNLOAD_ROTINA):
    """
    Aguarda que um arquivo seja completamente baixado e move para pasta de destino.
    
    Args:
        pasta_destino_final (str): Pasta de destino dos arquivos
        timeout (int): Timeout em segundos (padrão: 180s)
        
    Returns:
        str: Caminho do arquivo movido, ou None se timeout
    """
    pasta_downloads = str(DOWNLOADS_TEMP_ROTINA)
    inicio = time.time()
    tamanho_anterior = {}
    
    # Criar pastas necessárias
    _criar_pastas_necessarias(pasta_destino_final, pasta_downloads)
    
    while (time.time() - inicio) < timeout:
        try:
            if not os.path.exists(pasta_downloads):
                log.warning(f"Pasta de downloads não existe: {pasta_downloads}")
                time.sleep(1)
                continue
            
            arquivos = os.listdir(pasta_downloads)
            
            # Verificar se ainda está baixando (.crdownload)
            if any(arq.endswith('.crdownload') for arq in arquivos):
                time.sleep(2)
                continue
            
            # Procurar arquivo completo (CSV, XLSX, XLS)
            arquivo_completo = None
            for arquivo in arquivos:
                caminho_arquivo = os.path.join(pasta_downloads, arquivo)
                if os.path.isdir(caminho_arquivo):
                    continue
                if arquivo.endswith(('.csv', '.xlsx', '.xls')):
                    arquivo_completo = (arquivo, caminho_arquivo)
                    break
            
            if not arquivo_completo:
                time.sleep(1)
                continue
            
            arquivo, caminho_arquivo = arquivo_completo
            
            try:
                tamanho_atual = os.path.getsize(caminho_arquivo)
                
                # Primeira detecção - aguardar confirmação
                if arquivo not in tamanho_anterior:
                    tamanho_anterior[arquivo] = tamanho_atual
                    time.sleep(5)
                    continue
                
                # Verificar estabilidade do tamanho
                time.sleep(5)
                tamanho_novo = os.path.getsize(caminho_arquivo)
                
                if tamanho_novo == tamanho_atual:
                    # Download completo
                    try:
                        caminho_final = os.path.join(pasta_destino_final, arquivo)
                        shutil.move(caminho_arquivo, caminho_final)
                        log.debug(f"Download concluído: {arquivo}")
                        return caminho_final
                    except Exception as e:
                        log.error(f"Erro ao mover arquivo: {e}")
                        return caminho_arquivo
                else:
                    # Ainda em download
                    tamanho_anterior[arquivo] = tamanho_novo
                    time.sleep(2)
                    
            except Exception as e:
                log.error(f"Erro ao verificar arquivo: {e}")
                time.sleep(2)
        
        except PermissionError as e:
            log.error(f"Erro de permissão: {e}")
            time.sleep(2)
        except Exception as e:
            log.error(f"Erro ao verificar download: {e}")
            time.sleep(1)
    
    # Timeout alcançado
    tempo_decorrido = int(time.time() - inicio)
    log.error(f"TIMEOUT: Nenhum arquivo detectado em {tempo_decorrido}s (limite: {timeout}s)")
    log.error(f"Pasta verificada: {pasta_downloads}")
    _registrar_erro(f"❌ Timeout no download: nenhum arquivo em {tempo_decorrido}s. Pasta: {pasta_downloads}")
    try:
        conteudo = os.listdir(pasta_downloads)
        log.error(f"Conteúdo de Downloads: {conteudo}")
    except Exception as e:
        log.error(f"Erro ao listar pasta: {e}")
    
    return None



def _obter_data_base_execucao():
    """Obtém a data base da execução atual a partir do ambiente ou usa a data atual."""
    data_base_raw = str(os.getenv("ROTINA_DATA_EXECUCAO", "")).strip()
    if data_base_raw:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(data_base_raw, fmt)
            except ValueError:
                continue
    return datetime.now()


def _listar_arquivos_baixados_validos(pasta):
    """Lista arquivos baixados válidos (csv/xlsx/xls) com tamanho maior que zero."""
    return [
        arq for arq in (
            glob.glob(os.path.join(str(pasta), "*.csv")) +
            glob.glob(os.path.join(str(pasta), "*.xlsx")) +
            glob.glob(os.path.join(str(pasta), "*.xls"))
        )
        if os.path.isfile(arq) and os.path.getsize(arq) > 0
    ]

def _extrair_dias_atras_da_descricao(descricao):
    # Extrai quantos dias atrás a rotina se refere a partir da descrição.
    import re
    
    descricao_lower = descricao.lower()
    # Aceita "atrás" e "atras" (com ou sem acento)
    match = re.search(r'(\d+)\s+dia[s]?\s+atr(?:a|á)s', descricao_lower)
    if match:
        return int(match.group(1))
    if "hoje" in descricao_lower:
        return 0
    return 0


def _gerar_nome_arquivo_com_data(dias_atras=0, numero_parte=1):
    # Gera nome de arquivo no formato AnoMesDia-ParteArquivo.
    if dias_atras < 0:
        dias_atras = 0

    data_base_raw = str(os.getenv("ROTINA_DATA_EXECUCAO", "")).strip()
    data_base = None
    if data_base_raw:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                data_base = datetime.strptime(data_base_raw, fmt)
                break
            except ValueError:
                continue

    if data_base is None:
        data_base = datetime.now()

    data = data_base - timedelta(days=dias_atras)
    
    return data.strftime("%Y%m%d") + f"-{numero_parte}"


def _merge_com_arquivo_existente(df_novo, arquivo_final, chaves=['Protocolo', 'CPF']):
    """
    Mescla dados novos com arquivo existente usando estratégia inteligente.
    
    Estratégia: 
    1. Concatena arquivo antigo + novo
    2. Remove duplicatas priorizando status "Concluído"
    3. Chave: Protocolo + CPF (identifica registros únicos por protocolo e pessoa)
    
    Args:
        df_novo: DataFrame com novos dados
        arquivo_final: Caminho do arquivo consolidado existente
        chaves: Lista de colunas para identificar duplicatas (padrão: Protocolo + CPF)
        
    Returns:
        DataFrame mesclado e deduplicado
    """
    try:
        # Fazer merge usando a classe MergeInteligente
        df_merged = MergeInteligente.merge(df_novo, arquivo_final, chaves, priorizar_concluido=True)
        
        # Notificação de sucesso
        _set_status(f"✅ Merge concluído: {len(df_merged)} linhas finais")
        
        return df_merged
        
    except Exception as e:
        log.error(f"Erro ao fazer merge: {e}")
        return df_novo


def _preparar_dataframe_para_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza colunas para escrita robusta em parquet."""
    if df is None or len(df) == 0:
        return df

    df_parquet = df.copy()

    # Identificadores devem permanecer textuais no parquet para evitar mistura int/str.
    colunas_identificadoras = ["Protocolo", "CPF", "Workflow", "Cliente", "matrícula"]
    colunas_convertidas = []

    for coluna in colunas_identificadoras:
        if coluna in df_parquet.columns:
            df_parquet[coluna] = df_parquet[coluna].astype("string")
            colunas_convertidas.append(coluna)

    # Qualquer coluna object restante pode conter mistura de tipos que quebra no pyarrow.
    for coluna in df_parquet.select_dtypes(include=["object"]).columns:
        if coluna not in colunas_convertidas:
            df_parquet[coluna] = df_parquet[coluna].astype("string")
            colunas_convertidas.append(coluna)

    if colunas_convertidas:
        log.info(f"Parquet: colunas normalizadas para string: {', '.join(colunas_convertidas)}")

    return df_parquet


def _salvar_detalhado_bruto_parquet(df: pd.DataFrame, data_ref: str) -> None:
    """Publica a fonte da Replicação D-1 no banco; parquet fica apenas no modo legado."""
    try:
        from app.bots.replicacao_d1_db_bridge import (
            ingest_source_dataframe_db,
            is_fonte_banco_ativa,
        )

        if is_fonte_banco_ativa():
            result = ingest_source_dataframe_db(df, data_ref)
            log.info(
                "Detalhado bruto publicado no banco | lote=%s | válidas=%s | rejeitadas=%s",
                result["source_batch_id"],
                result["rows_valid"],
                result["rows_rejected"],
            )
            print(
                f"REPLICACAO_D1_SOURCE_DB|{result['source_batch_id']}|{result['content_hash']}",
                flush=True,
            )
            return
        PASTA_DETALHADO_BRUTO.mkdir(parents=True, exist_ok=True)
        destino = PASTA_DETALHADO_BRUTO / f"{PREFIXO_DETALHADO_BRUTO}{data_ref}.parquet"
        df_bruto = _preparar_dataframe_para_parquet(df)
        df_bruto.to_parquet(str(destino), index=False, compression="snappy")
        log.info(f"Detalhado bruto salvo: {destino.name} ({len(df_bruto)} linhas)")
        print(f"ROTINA_BRUTO_SAVED|detalhado|{destino.resolve()}", flush=True)
    except Exception as exc:
        log.warning(f"Falha ao salvar detalhado bruto ({data_ref}): {exc}")


def _salvar_arquivo_final_parquet(
    df: pd.DataFrame,
    destino_primario: Path,
    destinos_copia: list[Path] | None = None,
):
    """Salva parquet tratado na pasta Bots e espelha para destinos gerenciais."""
    destino_primario = Path(destino_primario)
    destino_primario.parent.mkdir(parents=True, exist_ok=True)

    df_parquet = _preparar_dataframe_para_parquet(df)
    df_parquet.to_parquet(str(destino_primario), index=False, compression="snappy")
    log.info(f"Arquivo final salvo em Parquet | Destino: {os.path.abspath(destino_primario)}")

    copias = [Path(pasta) / destino_primario.name for pasta in (destinos_copia or [])]
    if copias:
        espelhar_arquivo(destino_primario, copias, log=log)


def _mover_arquivos_para_pasta_final(pasta_temp, eh_incremental=False):
    """
    Move arquivos consolidados da pasta temporária para a pasta final.
    Se incremental (2+ dias atrás), faz merge com arquivo existente.
    
    Args:
        pasta_temp: Caminho da pasta temporária
        eh_incremental: Se True, faz merge com arquivos existentes
    """
    try:
        pasta_final = PASTA_DETALHADO_D1
        pasta_final.mkdir(parents=True, exist_ok=True)
        pasta_copia_gerencial = DEFAULT_SHAREPOINT_ROTINA_PARQUET
        
        # Proteção: se pasta_temp está dentro de DEFAULT_SHAREPOINT_BOTS, não processar
        # (arquivos já estão em seu local final correto)
        pasta_temp_path = Path(pasta_temp).resolve()
        try:
            pasta_temp_path.relative_to(DEFAULT_SHAREPOINT_BOTS.resolve())
            log.debug(f"Ignorando pasta {pasta_temp} pois está dentro de DEFAULT_SHAREPOINT_BOTS - arquivos já em local final")
            return
        except ValueError:
            # pasta_temp não está dentro de DEFAULT_SHAREPOINT_BOTS, pode prosseguir
            pass
        
        # Encontrar arquivos consolidados (sem "_parte" no nome - são os processados)
        arquivos_consolidados = []
        for arquivo in glob.glob(os.path.join(str(pasta_temp), "*.csv")):
            nome = os.path.basename(arquivo)
            # Verificar se é um arquivo consolidado (tem apenas a data YYYYMMDD.csv)
            if len(nome) == 12 and nome[:8].isdigit() and nome.endswith('.csv'):
                arquivos_consolidados.append(arquivo)
        
        if not arquivos_consolidados:
            log.warning("Nenhum arquivo consolidado encontrado para mover")
            return
        
        log.debug(f"Movendo {len(arquivos_consolidados)} arquivo(s) consolidado(s)")
        modo = "(INCREMENTAL - merge com existentes)" if eh_incremental else "(COMPLETO)"
        _set_status(f"Movendo {len(arquivos_consolidados)} arquivo(s) consolidado(s) para pasta final {modo}")
        
        for arquivo in arquivos_consolidados:
            try:
                nome = os.path.basename(arquivo)
                destino = pasta_final / f"{PREFIXO_DETALHADO_FINAL}{Path(nome).stem}.parquet"
                destino_copia_gerencial = pasta_copia_gerencial / destino.name
                data_ref = Path(nome).stem
                pasta_final.mkdir(parents=True, exist_ok=True)
                log.debug(f"Movendo arquivo consolidado {modo}")

                destino_existente = destino if destino.exists() else (
                    destino_copia_gerencial if destino_copia_gerencial.exists() else None
                )
                
                # SEMPRE fazer merge se o arquivo destino já existe (incremental ou completo)
                if destino_existente is not None:
                    # Arquivo existe - fazer merge independente do modo
                    log.info(f"Arquivo existente detectado - mesclando com {nome} {modo}")
                    _set_status(f"Mesclando dados com arquivo existente: {nome}")

                    # Ler arquivo novo (da pasta temp) e aplicar limpeza BI antes do merge
                    df_novo = pd.read_csv(arquivo, encoding='utf-8-sig', sep=';', quotechar='"')
                    linhas_novas = len(df_novo)
                    _salvar_detalhado_bruto_parquet(df_novo, data_ref)
                    df_novo = _aplicar_limpeza_bi(df_novo)

                    # Ler arquivo existente para estatísticas
                    linhas_existentes = _contar_linhas_arquivo(str(destino_existente))
                    
                    # Merge: parquet existente já está limpo; não reaplicar limpeza no merged
                    df_merged = _merge_com_arquivo_existente(df_novo, str(destino_existente))
                    _log_contagem_matricula_flag(df_merged, contexto=f"pós-merge {destino.name}")
                    linhas_finais = len(df_merged)
                    duplicatas_merge = max(0, (linhas_existentes + linhas_novas) - linhas_finais)
                    
                    # Salvar resultado final tratado (Bots primário + cópia gerencial)
                    _salvar_arquivo_final_parquet(
                        df_merged,
                        destino,
                        destinos_copia=[pasta_copia_gerencial],
                    )
                    
                    # Remover arquivo temporário
                    os.remove(arquivo)

                    _registrar_arquivo_resumo(
                        nome_arquivo=destino.name,
                        linhas_finais=linhas_finais,
                        duplicatas_merge=duplicatas_merge,
                        tipo="merge"
                    )
                    
                    log.info(f"✓ Merge concluído e arquivo atualizado: {destino.name}")
                    _set_status(f"✓ Arquivo mesclado e atualizado: {destino.name}")
                else:
                    # Download completo ou arquivo novo - aplicar limpeza e salvar
                    df_novo = pd.read_csv(arquivo, encoding='utf-8-sig', sep=';', quotechar='"')
                    _salvar_detalhado_bruto_parquet(df_novo, data_ref)
                    df_novo = _aplicar_limpeza_bi(df_novo)
                    linhas_finais = len(df_novo)
                    _salvar_arquivo_final_parquet(
                        df_novo,
                        destino,
                        destinos_copia=[pasta_copia_gerencial],
                    )
                    os.remove(arquivo)
                    _registrar_arquivo_resumo(
                        nome_arquivo=destino.name,
                        linhas_finais=linhas_finais,
                        duplicatas_merge=0,
                        tipo="novo"
                    )
                    _set_status(f"✓ Arquivo consolidado movido: {destino.name}")
                    log.debug(f"Arquivo movido com sucesso")
                
            except Exception as e:
                log.error(f"Erro ao processar arquivo consolidado {nome}: {e}")
                _set_status(f"Erro ao processar arquivo consolidado: {e}")
                _registrar_erro(f"❌ Erro ao processar consolidado {nome}: {str(e)[:80]}")
        
        _set_progress(65, "Consolidacao: arquivos movidos para pasta final")
        _set_status("Arquivos consolidados processados com sucesso!")
        
    except Exception as e:
        log.error(f"Erro ao processar arquivos para pasta final: {e}", exc_info=True)
        _set_status(f"Erro ao processar arquivos para pasta final: {e}")


def _combinar_arquivos_em_um(pasta_saida):
    """Combina múltiplos arquivos CSV em um arquivo único por dia."""
    try:
        _set_status("Combinando arquivos por dia")
        _set_progress(66, "Consolidacao: processando dados")
        
        # Encontrar arquivos CSV
        arquivos_csv = glob.glob(os.path.join(str(pasta_saida), "*.csv"))
        
        if not arquivos_csv:
            log.warning("Nenhum arquivo encontrado para combinar")
            _registrar_erro("⚠️ Nenhum arquivo para combinar: pasta vazia")
            return
        
        log.debug(f"Encontrados {len(arquivos_csv)} arquivo(s)")
        
        # Agrupar arquivos por data (YYYYMMDD)
        arquivos_por_data = _agrupar_arquivos_por_data(arquivos_csv)
        log.debug(f"Agrupados em {len(arquivos_por_data)} dia(s)")
        
        # Processar cada dia
        for data, arquivos_dia in arquivos_por_data.items():
            _processar_arquivos_do_dia(data, arquivos_dia, pasta_saida)
        
        _set_progress(67, "Consolidacao: concluida")
        _set_status("Consolidação completa!")
        
    except Exception as e:
        log.error(f"Erro ao combinar arquivos: {e}", exc_info=True)
        _set_status(f"Erro ao combinar: {e}")
        _registrar_erro(f"❌ Erro na consolidação: {str(e)[:100]}")


def _agrupar_arquivos_por_data(arquivos_csv: list) -> dict:
    """Agrupa lista de arquivos CSV por data (YYYYMMDD)."""
    arquivos_por_data = {}
    for arquivo in arquivos_csv:
        nome = os.path.basename(arquivo)
        if len(nome) >= 8 and nome[:8].isdigit():
            data = nome[:8]
            arquivos_por_data.setdefault(data, []).append(arquivo)
    return arquivos_por_data


def _processar_arquivos_do_dia(data: str, arquivos_dia: list, pasta_saida: Path):
    """
    Processa e consolida todos os arquivos de um dia específico.
    
    IMPORTANTE: NÃO remove duplicatas aqui! A remoção acontece no merge com arquivo existente.
    Isso garante que a deduplicação considere TODOS os dados (antigos + novos) juntos.
    """
    try:
        arquivos_dia.sort(key=lambda x: os.path.basename(x))
        log.info(f"Processando dia {data} ({len(arquivos_dia)} arquivo(s))")
        
        # Ler todos os arquivos do dia
        dfs = []
        for arquivo in arquivos_dia:
            df = _ler_arquivo_csv_robusto(arquivo)
            if df is not None and len(df) > 0:
                dfs.append(df)
        
        if not dfs:
            log.warning(f"Nenhum arquivo válido encontrado para dia {data}")
            return
        
        # Consolidar DataFrames
        df_dia = pd.concat(dfs, ignore_index=True)
        log.info(f"✓ Consolidado: {len(df_dia)} linhas de {len(dfs)} arquivo(s)")
        
        # Corrigir colunas se necessário
        df_dia = _corrigir_colunas_dataframe(df_dia)
        
        # NÃO remover duplicatas aqui - será feito no merge!
        # Apenas remover duplicatas INTERNAS dos arquivos baixados (se houver partes duplicadas)
        if len(arquivos_dia) > 1:
            # Só remove duplicatas internas se veio de múltiplos arquivos (partes)
            linhas_antes = len(df_dia)
            if "Protocolo" in df_dia.columns and "CPF" in df_dia.columns:
                df_dia = df_dia.drop_duplicates(subset=["Protocolo", "CPF"], keep='first')
                dup_internas = linhas_antes - len(df_dia)
                if dup_internas > 0:
                    _registrar_duplicatas_internas(dup_internas)
                    log.info(f"Dia {data}: {dup_internas} duplicatas INTERNAS removidas (entre as partes)")
        
        log.info(f"Dia {data}: {len(df_dia)} linhas prontas para merge")
        
        # Salvar arquivo consolidado
        _salvar_arquivo_consolidado(df_dia, data, pasta_saida)
        
        # Limpar arquivos individuais
        for arquivo in arquivos_dia:
            try:
                os.remove(arquivo)
            except Exception as e:
                log.warning(f"Erro ao remover arquivo: {e}")
        
    except Exception as e:
        log.error(f"Erro ao processar dia {data}: {e}")
        _set_status(f"Erro ao processar dia {data}: {e}")


def _ler_arquivo_csv_robusto(arquivo: str) -> pd.DataFrame:
    """Lê arquivo CSV usando múltiplas estratégias."""
    try:
        log.debug(f"Lendo: {os.path.basename(arquivo)}")
        file_size = os.path.getsize(arquivo)
        log.info(f"  Tamanho: {file_size:,} bytes")
        
        # Usar classe CSVReader
        df = CSVReader.ler_csv(arquivo, esperado_colunas=len(EXPECTED_COLUMNS))
        
        if df is not None and len(df) > 0:
            log.info(f"✓ Arquivo carregado: {os.path.basename(arquivo)}")
            return df
        
        # Fallback: tentar como Excel
        try:
            df = pd.read_excel(arquivo)
            if df is not None and len(df) > 0:
                log.info(f"✓ Arquivo Excel carregado: {os.path.basename(arquivo)}")
                return df
        except:
            pass
        
        log.error(f"Falha ao ler arquivo: {os.path.basename(arquivo)}")
        return None
        
    except Exception as e:
        log.error(f"Erro ao ler arquivo {os.path.basename(arquivo)}: {e}")
        return None


def _corrigir_colunas_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Corrige problemas de colunas em DataFrame consolidado."""
    # Normalizar nomes de colunas
    df.columns = [str(c).replace('\ufeff', '').strip() for c in df.columns]
    
    # Se tem apenas 1 coluna, tentar corrigir
    if len(df.columns) == 1:
        log.warning("DataFrame tem apenas 1 coluna, tentando corrigir...")
        df = _fix_single_column_dataframe(df)
    
    # Se tem ponto e vírgula no nome da primeira coluna
    if len(df.columns) > 0:
        first_col = str(df.columns[0])
        if ';' in first_col:
            log.warning("Detectado delimitador no nome da coluna, reaplicando correção...")
            df_temp = pd.DataFrame({'data': df.apply(lambda row: str(row.iloc[0]) if pd.notna(row.iloc[0]) else '', axis=1)})
            df = _fix_single_column_dataframe(df_temp)
    
    # Forçar colunas esperadas se tiver número correto
    if len(df.columns) == len(EXPECTED_COLUMNS):
        df.columns = EXPECTED_COLUMNS
        log.info(f"✅ DataFrame com {len(EXPECTED_COLUMNS)} colunas corretas")
    else:
        log.warning(f"DataFrame tem {len(df.columns)} colunas (esperado: {len(EXPECTED_COLUMNS)})")
    
    return df


def _extrair_celula_matricula(valor):
    """Garante valor escalar (apply/map pode receber Series se a coluna estiver duplicada)."""
    if isinstance(valor, pd.Series):
        return None if valor.empty else valor.iloc[0]
    if isinstance(valor, pd.DataFrame):
        return None if valor.size == 0 else valor.iloc[0, 0]
    if hasattr(valor, "item") and not isinstance(valor, (str, bytes, dict, list, tuple)):
        try:
            return valor.item()
        except (ValueError, AttributeError):
            pass
    return valor


def _coluna_matricula_series(df: pd.DataFrame):
    """Retorna Series única da coluna matrícula (trata nomes duplicados no CSV)."""
    if df is None or "matrícula" not in df.columns:
        return None
    col = df["matrícula"]
    if isinstance(col, pd.DataFrame):
        log.warning("Coluna 'matrícula' duplicada no DataFrame; usando a primeira ocorrência")
        col = col.iloc[:, 0]
    return col


def _matricula_ja_e_flag(valor_matricula) -> bool:
    """True se o valor já é flag BI (0 ou 1), inclusive após leitura do parquet."""
    valor_matricula = _extrair_celula_matricula(valor_matricula)
    if valor_matricula is None or (isinstance(valor_matricula, float) and pd.isna(valor_matricula)):
        return False
    if isinstance(valor_matricula, bool):
        return valor_matricula in (False, True)
    if isinstance(valor_matricula, int) and valor_matricula in (0, 1):
        return True
    if isinstance(valor_matricula, float) and valor_matricula in (0.0, 1.0):
        return True
    s = str(valor_matricula).strip()
    return s in ("0", "1", "0.0", "1.0")


def _flag_matricula_para_int(valor_matricula) -> int:
    """Converte flag já existente (0/1) para int sem int(Series)."""
    valor = _extrair_celula_matricula(valor_matricula)
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return 1
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(valor)
    s = str(valor).strip()
    if not s:
        return 1
    return int(float(s))


def _classificar_matricula_flag(valor_matricula) -> int:
    if _matricula_ja_e_flag(valor_matricula):
        return _flag_matricula_para_int(valor_matricula)

    letras_validas = {"A", "C", "EM", "ET", "Q"}
    v_matricula = str(valor_matricula or "").upper().strip()
    for sufixo in ("@BR.EXPERIAN.COM.BR", "@BR.EXPERIAN.COM", "@BRFLOW.COM.BR", "@BRFLOW.COM"):
        v_matricula = v_matricula.replace(sufixo, "")
    v_matricula = v_matricula.replace(".", "")

    v_serasa = False
    if len(v_matricula) == 7:
        inicio_1 = v_matricula[:1]
        meio_5 = v_matricula[1:6]
        fim_1 = v_matricula[-1:]
        v_serasa = (inicio_1 in letras_validas) and meio_5.isdigit() and (fim_1 in letras_validas)

    v_antiga = False
    if len(v_matricula) == 7:
        inicio_2 = v_matricula[:2]
        fim_5 = v_matricula[-5:]
        v_antiga = (inicio_2 in letras_validas) and fim_5.isdigit()

    return 0 if (v_antiga or v_serasa) else 1


def _log_contagem_matricula_flag(df: pd.DataFrame, contexto: str = "") -> None:
    """Registra contagem de flags 0/1 na coluna matrícula para validação pós-merge."""
    serie = _coluna_matricula_series(df)
    if serie is None or len(serie) == 0:
        return
    flags = pd.to_numeric(serie, errors="coerce")
    flag_0 = int((flags == 0).sum())
    flag_1 = int((flags == 1).sum())
    outros = len(serie) - flag_0 - flag_1
    sufixo = f" ({contexto})" if contexto else ""
    log.info(
        f"Matrícula flag{sufixo}: válidas(0)={flag_0:,} | demais(1)={flag_1:,} | não classificadas={outros:,}"
    )


def _aplicar_limpeza_bi(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica limpeza para consumo no BI sem alterar o esquema atual."""
    if df is None or len(df) == 0:
        return df

    # Remove colunas não desejadas no arquivo final (CSV e Parquet)
    colunas_remover_normalizadas = {
        "Tempo de Análise",
        "Usuário",
        "N. do Contrato/Proposta",
        "Tipo de Conclusão de Análise",
    }
    colunas_para_remover = []
    for coluna in df.columns:
        if str(coluna).strip() in colunas_remover_normalizadas:
            colunas_para_remover.append(coluna)

    if colunas_para_remover:
        df = df.drop(columns=colunas_para_remover, errors="ignore")
        log.info(f"Limpeza BI: colunas removidas do resultado final: {', '.join(map(str, colunas_para_remover))}")

    col_matricula = _coluna_matricula_series(df)
    if col_matricula is not None:
        df = df.copy()
        df["matrícula"] = col_matricula.map(_classificar_matricula_flag)
        log.info("Limpeza BI: coluna 'matrícula' convertida para flag (0=matrícula/e-mail corporativo, 1=demais)")
    else:
        log.warning("Coluna ausente para limpeza BI (ignorada): matrícula")

    # Em Alertas, mantém apenas linhas com o código alvo e normaliza o valor
    if "Alertas" in df.columns:
        padrao_alerta = r"gc\s*-\s*9u1zd6wwxu"
        serie_alertas = df["Alertas"].astype("string")
        mask_alerta = serie_alertas.str.contains(padrao_alerta, case=False, na=False, regex=True)
        linhas_normalizadas = int(mask_alerta.sum())
        linhas_limpas = int((~mask_alerta).sum())
        df.loc[mask_alerta, "Alertas"] = "GC - 9U1ZD6WWXU"
        df.loc[~mask_alerta, "Alertas"] = pd.NA
        log.info(
            f"Limpeza BI (Alertas): {linhas_normalizadas} linha(s) normalizada(s) para padrão GC; "
            f"{linhas_limpas} linha(s) limpa(s) para NA"
        )
    else:
        log.warning("Coluna ausente para limpeza BI (ignorada): Alertas")

    return df


def _remover_duplicatas(df: pd.DataFrame, data: str) -> pd.DataFrame:
    """
    Remove duplicatas baseado em Protocolo e Workflow.
    Prioriza registros com status "Concluído" sobre "Em análise".
    """
    try:
        linhas_antes = len(df)
        
        if "Protocolo" not in df.columns or "CPF" not in df.columns:
            log.warning(f"Colunas 'Protocolo' e/ou 'CPF' não encontradas para {data}")
            return df
        
        # Verificar se tem coluna Status do Registro para lógica inteligente
        tem_status = "Status do Registro" in df.columns
        
        if tem_status:
            # Usar lógica inteligente (prioriza Concluído)
            log.info(f"Dia {data}: Aplicando deduplicação inteligente (prioriza Concluídos)")
            df = MergeInteligente._deduplica_com_prioridade_status(df, ['Protocolo', 'CPF'])
        else:
            # Sem coluna Status, usar deduplicação padrão
            log.info(f"Dia {data}: Aplicando deduplicação padrão (mantém primeiro)")
            df = df.drop_duplicates(subset=["Protocolo", "CPF"], keep='first')
        
        duplicatas = linhas_antes - len(df)
        
        if duplicatas > 0:
            log.info(f"📊 Dia {data}: {duplicatas} duplicatas removidas (total final: {len(df)} linhas)")
            _set_status(f"Dia {data}: {duplicatas} duplicatas removidas")
        else:
            log.info(f"Dia {data}: nenhuma duplicata encontrada")
        
        return df
        
    except Exception as e:
        log.error(f"Erro ao remover duplicatas para {data}: {e}")
        return df


def _salvar_arquivo_consolidado(df: pd.DataFrame, data: str, pasta_saida: Path):
    """Salva DataFrame consolidado em arquivo CSV."""
    try:
        arquivo_final = pasta_saida / f"{data}.csv"
        df.to_csv(str(arquivo_final), index=False, encoding='utf-8-sig', 
                  sep=';', quotechar='"', quoting=1)
        
        log.info(f"✓ Consolidado salvo: {data}.csv ({len(df)} linhas)")
        _set_status(f"Arquivo do dia {data} criado: {data}.csv ({len(df)} linhas)")
        
    except Exception as e:
        log.error(f"Erro ao salvar arquivo consolidado para {data}: {e}")
        raise

# ============================================================================
# PONTO DE ENTRADA PRINCIPAL
# ============================================================================

def _normalizar_data_para_iso(raw_value):
    valor = str(raw_value or "").strip()
    if not valor:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _resolver_datas_execucao(settings=None):
    s = settings or {}
    inicio_iso = _normalizar_data_para_iso(
        s.get("rotina_data_inicio")
        or os.getenv("ROTINA_DATA_INICIO")
    )
    fim_iso = _normalizar_data_para_iso(
        s.get("rotina_data_fim")
        or os.getenv("ROTINA_DATA_FIM")
    )

    hoje_iso = datetime.now(_get_tz_br()).strftime("%Y-%m-%d")
    if not inicio_iso and not fim_iso:
        return [hoje_iso]

    if not inicio_iso:
        inicio_iso = fim_iso
    if not fim_iso:
        fim_iso = inicio_iso

    try:
        data_inicio = datetime.strptime(inicio_iso, "%Y-%m-%d").date()
        data_fim = datetime.strptime(fim_iso, "%Y-%m-%d").date()
    except ValueError:
        log.warning("Período recebido inválido para rotina; usando data de hoje")
        return [hoje_iso]

    if data_inicio > data_fim:
        data_inicio, data_fim = data_fim, data_inicio

    datas = []
    atual = data_inicio
    while atual <= data_fim:
        datas.append(atual.strftime("%Y-%m-%d"))
        atual += timedelta(days=1)

    return datas or [hoje_iso]
