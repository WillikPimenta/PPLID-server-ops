"""Leitura CSV e merge inteligente BRFlow."""
from __future__ import annotations

import logging
import os

import pandas as pd

from app.bots.rotina.constants import CSV_CHUNK_SIZE, CSV_ENCODINGS, CSV_SEPARATORS

log = logging.getLogger("robots.bot_rotina")

class CSVReader:
    """Classe para leitura robusta de arquivos CSV com múltiplas estratégias."""
    
    @staticmethod
    def ler_csv(arquivo: str, esperado_colunas: int = None) -> pd.DataFrame:
        """
        Tenta ler CSV usando múltiplas estratégias até ter sucesso.
        
        Args:
            arquivo: Caminho do arquivo CSV
            esperado_colunas: Número de colunas esperado (opcional)
            
        Returns:
            DataFrame lido ou None se falhar
        """
        # Estratégia 1: Python csv.DictReader (mais robusto)
        df = CSVReader._tentar_csv_dictreader(arquivo, esperado_colunas)
        if df is not None:
            return df
        
        # Estratégia 2: Pandas com chunks
        df = CSVReader._tentar_pandas_chunks(arquivo, esperado_colunas)
        if df is not None:
            return df
        
        # Estratégia 3: Pandas simples
        df = CSVReader._tentar_pandas_simples(arquivo)
        return df
    
    @staticmethod
    def _tentar_csv_dictreader(arquivo: str, esperado_colunas: int = None) -> pd.DataFrame:
        """Tenta ler usando csv.DictReader nativo do Python."""
        import csv as csv_module
        
        for encoding in CSV_ENCODINGS:
            for delimiter in CSV_SEPARATORS:
                try:
                    with open(arquivo, 'r', encoding=encoding) as csvfile:
                        reader = csv_module.DictReader(csvfile, delimiter=delimiter, quotechar='"')
                        chunks = []
                        current_chunk = []
                        
                        for row in reader:
                            current_chunk.append(row)
                            if len(current_chunk) >= CSV_CHUNK_SIZE:
                                chunks.append(pd.DataFrame(current_chunk))
                                current_chunk = []
                        
                        if current_chunk:
                            chunks.append(pd.DataFrame(current_chunk))
                        
                        if chunks:
                            df = pd.concat(chunks, ignore_index=True)
                            if esperado_colunas is None or len(df.columns) >= 1:
                                log.debug(f"CSV lido com DictReader: {len(df)} linhas, {len(df.columns)} colunas")
                                return df
                except:
                    continue
        return None
    
    @staticmethod
    def _tentar_pandas_chunks(arquivo: str, esperado_colunas: int = None) -> pd.DataFrame:
        """Tenta ler usando pandas com chunks."""
        for sep in CSV_SEPARATORS:
            try:
                chunks = []
                for chunk in pd.read_csv(
                    arquivo, sep=sep, encoding='utf-8-sig', engine='python',
                    on_bad_lines='skip', chunksize=CSV_CHUNK_SIZE,
                    low_memory=False, na_filter=False, quotechar='"'
                ):
                    chunks.append(chunk)
                
                if chunks:
                    df = pd.concat(chunks, ignore_index=True)
                    if esperado_colunas is None or len(df.columns) >= 1:
                        log.debug(f"CSV lido com pandas chunks: {len(df)} linhas")
                        return df
            except:
                continue
        return None
    
    @staticmethod
    def _tentar_pandas_simples(arquivo: str) -> pd.DataFrame:
        """Tenta ler usando pandas de forma simples."""
        for encoding in CSV_ENCODINGS:
            for sep in CSV_SEPARATORS:
                try:
                    df = pd.read_csv(arquivo, encoding=encoding, sep=sep, quotechar='"', engine='python', on_bad_lines='skip')
                    if len(df) > 0:
                        log.debug(f"CSV lido com pandas simples: {len(df)} linhas")
                        return df
                except:
                    continue
        return None


class MergeInteligente:
    """
    Classe para fazer merge inteligente de DataFrames com deduplicação.
    
    LÓGICA DE PRIORIZAÇÃO (Ordem de Decisão):
    ------------------------------------------
    Quando há duplicatas (mesmo Protocolo + Workflow):
    
    1. **CONCLUÍDO (novo) vs EM ANÁLISE (antigo)** → Mantém CONCLUÍDO (novo)
    2. **EM ANÁLISE (novo) vs CONCLUÍDO (antigo)** → Mantém CONCLUÍDO (antigo)
    3. **CONCLUÍDO (novo) vs CONCLUÍDO (antigo)** → Mantém NOVO (atualização)
    4. **EM ANÁLISE (novo) vs EM ANÁLISE (antigo)** → Mantém NOVO (atualização)
    5. **OUTROS STATUS** → Mantém NOVO (mais recente)
    
    RESUMO: Prioriza "Concluído" > "Em análise", e em caso de empate, mantém o MAIS RECENTE.
    
    EXEMPLOS REAIS:
    ---------------
    Cenário 1: Status mudou de Em análise → Concluído
    | Origem  | Protocolo | Workflow | Status          | Resultado |
    |---------|-----------|----------|-----------------|-----------|
    | ANTIGO  | 123       | WF-1     | Em análise      | ❌ Remove |
    | NOVO    | 123       | WF-1     | Concluído       | ✅ Mantém |
    
    Cenário 2: Ambos Concluído (atualização de dados)
    | Origem  | Protocolo | Workflow | Status          | Data        | Resultado |
    |---------|-----------|----------|-----------------|-------------|-----------|
    | ANTIGO  | 806861968 | WF-1     | Concluído       | 2026-03-03  | ❌ Remove |
    | NOVO    | 806861968 | WF-1     | Concluído       | 2026-03-05  | ✅ Mantém |
    
    Cenário 3: Novo está Em análise, mas antigo já Concluído
    | Origem  | Protocolo | Workflow | Status          | Resultado |
    |---------|-----------|----------|-----------------|-----------|
    | ANTIGO  | 456       | WF-2     | Concluído       | ✅ Mantém |
    | NOVO    | 456       | WF-2     | Em análise      | ❌ Remove |
    """
    
    @staticmethod
    def merge(df_novo: pd.DataFrame, arquivo_existente: str, 
              chaves: list = None, priorizar_concluido: bool = True) -> pd.DataFrame:
        """
        Mescla dados novos com arquivo existente.
        
        FLUXO:
        1. Lê arquivo existente (se houver)
        2. Concatena antigos + novos
        3. Remove duplicatas priorizando "Concluído"
        
        Args:
            df_novo: DataFrame com novos dados
            arquivo_existente: Caminho do arquivo existente
            chaves: Colunas para identificar duplicatas
            priorizar_concluido: Se True, prioriza status "Concluído"
            
        Returns:
            DataFrame mesclado e deduplicado
        """
        if chaves is None:
            chaves = ['Protocolo', 'Workflow']
        
        # Ler arquivo existente
        df_existente = MergeInteligente._ler_arquivo_existente(arquivo_existente, chaves)
        
        if df_existente is None or len(df_existente) == 0:
            log.info(f"Nenhum arquivo existente - retornando dados novos sem modificação")
            return df_novo
        
        # Concatenar tudo
        log.info(f"📊 Preparando merge: {len(df_existente)} antigas + {len(df_novo)} novas = {len(df_existente) + len(df_novo)} total")
        df_merged = pd.concat([df_existente, df_novo], ignore_index=True)
        linhas_antes_dedup = len(df_merged)
        
        log.debug(f"DataFrame após concatenação: {linhas_antes_dedup} linhas, {len(df_merged.columns)} colunas")
        log.debug(f"Memória estimada: {df_merged.memory_usage(deep=True).sum() / (1024**2):.2f} MB")
        
        # Aplicar estratégia de deduplicação
        tem_status = "Status do Registro" in df_novo.columns and "Status do Registro" in df_existente.columns
        
        if tem_status and priorizar_concluido:
            log.info(f"Aplicando deduplicação inteligente (prioriza Concluídos)...")
            log.debug(f"Iniciando deduplicação com {linhas_antes_dedup} linhas")
            df_merged = MergeInteligente._deduplica_com_prioridade_status(df_merged, chaves)
            log.debug(f"Deduplicação concluída: {len(df_merged)} linhas restantes")
        else:
            log.info(f"Aplicando deduplicação padrão...")
            df_merged = df_merged.drop_duplicates(subset=chaves, keep='first')
        
        # Logs finais
        duplicatas = linhas_antes_dedup - len(df_merged)
        log.info(f"📊 Merge completo: {len(df_existente)} antigas + {len(df_novo)} novas - {duplicatas} duplicatas = {len(df_merged)} finais")
        
        return df_merged
    
    @staticmethod
    def _ler_arquivo_existente(arquivo: str, chaves: list) -> pd.DataFrame:
        """Lê arquivo existente com validação de chaves."""
        if not os.path.exists(arquivo):
            return None

        if str(arquivo).lower().endswith('.parquet'):
            df = pd.read_parquet(arquivo)
        else:
            df = CSVReader.ler_csv(arquivo)

        if df is None or len(df) == 0:
            return None
        
        # Validar chaves
        if not all(chave in df.columns for chave in chaves):
            log.warning(f"Chaves não encontradas no arquivo existente")
            return None
        
        return df
    
    @staticmethod
    def _deduplica_com_prioridade_status(df: pd.DataFrame, chaves: list) -> pd.DataFrame:
        """
        Remove duplicatas priorizando status 'Concluído' e dados MAIS RECENTES.
        
        SUPER OTIMIZADO para DataFrames MUITO grandes (500K+):
        - Usa itertuples (fast row iteration) + dicionário hash (O(1) lookup)
        - Uma única passagem pelos dados: O(n)
        - Evita .iloc, groupby, sort_values (todos lentos em DataFrames grandes)
        
        Lógica:
        1. Itera pelos dados RAPIDAMENTE com itertuples()
        2. Cria hash das chaves (Protocolo + Workflow)
        3. Compara prioridade (Concluído > Em análise) + recência (índice maior)
        4. Mantém apenas os índices dos melhores registros
        5. Filtra DataFrame FINAL uma única vez
        """
        def prioridade_status(status_str):
            status = str(status_str).strip().lower()
            if "concluído" in status:
                return 1
            elif "em análise" in status:
                return 2
            else:
                return 3
        
        linhas_antes = len(df)
        log.info(f"Aplicando deduplicação (hash table) em {linhas_antes} linhas...")
        
        # Preparar índices das colunas para acesso rápido
        idx_chaves = [df.columns.get_loc(col) for col in chaves]
        idx_status = df.columns.get_loc('Status do Registro')
        
        # Dicionário para rastrear melhor registro por chave
        melhores = {}
        
        # Estatísticas
        duplicatas_encontradas = 0
        concluidos_mantidos = 0
        mesmo_status_removidos = 0  # Duplicatas com mesmo status
        
        log.debug(f"Iterando rapidamente sobre os dados...")
        
        # Iterar com itertuples (MUITO mais rápido que iloc)
        for idx, row in enumerate(df.itertuples(index=False)):
            # Criar chave composta COM NORMALIZAÇÃO dos valores
            # Normalizar: remover espaços, converter para string, tratar NaN
            # Para CPF: remover pontos, hífens, espaços
            chave = tuple(
                '' if pd.isna(row[i]) else str(row[i]).strip().upper().replace('.', '').replace('-', '').replace(' ', '')
                for i in idx_chaves
            )
            prioridade = prioridade_status(row[idx_status])
            
            if chave in melhores:
                # Duplicata encontrada
                duplicatas_encontradas += 1
                idx_atual, prio_atual = melhores[chave]
                
                # Decidir qual manter
                if prioridade < prio_atual:
                    # Novo é melhor (prioridade menor = Concluído > Em análise)
                    melhores[chave] = (idx, prioridade)
                    if prioridade == 1:
                        concluidos_mantidos += 1
                elif prioridade == prio_atual and idx > idx_atual:
                    # MESMO STATUS: manter apenas o mais recente (remover antigo)
                    melhores[chave] = (idx, prioridade)
                    mesmo_status_removidos += 1
                # else: mantém o atual (é melhor ou igual mas mais antigo)
            else:
                # Primeira ocorrência
                melhores[chave] = (idx, prioridade)
        
        # Extrair índices e filtrar (ÚLTIMO acesso massivo ao DataFrame)
        log.debug(f"Filtrando DataFrame...")
        indices_manter = sorted([idx for idx, _ in melhores.values()])
        df_resultado = df.iloc[indices_manter].copy()
        
        linhas_depois = len(df_resultado)
        removidas = linhas_antes - linhas_depois
        
        # Log detalhado
        if removidas > 0:
            log.info(f"✅ Deduplicação concluída: {removidas} registros removidos")
            log.info(f"   • Duplicatas detectadas: {duplicatas_encontradas}")
            log.info(f"   • Concluídos priorizados: {concluidos_mantidos}")
            log.info(f"   • Mesmo status (mantido mais recente): {mesmo_status_removidos}")
            log.info(f"   • Total final: {linhas_depois} registros únicos")
        else:
            log.info(f"✅ Nenhuma duplicata encontrada - {linhas_depois} registros únicos")
        
        return df_resultado
