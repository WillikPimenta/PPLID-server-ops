# -*- coding: utf-8 -*-
"""Flags de exibição do relatório diário (compartilhadas pelo legado e html_main)."""

# === PARÂMETROS (ligar/desligar seções) ===
INCLUIR_CONSOLIDADO     = True
INCLUIR_GRAFICO_EVOLUCAO = True
INCLUIR_GRAFICO_DIARIO   = True
MOSTRAR_KPI_NOVOS_NO_MES = True
ADICIONAR_COL_NOME_AGENTE = True
# Visao unica: quando True, remove colunas/blocos FN e FP e expande as tabelas.
APENAS_TOTAL = True

# Reincidência por Turno (HC)
SEPARAR_REINC_POR_TURNO = True   # mostra tabelas separadas por turno
EXIBIR_REINC_GERAL = False      # manter False: NÃO mostra tabela geral

# Debug do filtro de Localidade (mostra contagens).
DEBUG_LOCALIDADE = False



# Debug do cálculo de Tempo na Etapa (imprime caminho/decisões no console).
DEBUG_TEMPO_ETAPA = False
# Informe matrículas para rastrear (valores brutos ou normalizados). Vazio = rastreia todas (não recomendado).
DEBUG_MATS_ALVO = set()  # ex.: {'c11023q', '136254965'}
# ======== Top N (visões) ========
TOP_TIPOS_TRIPLET = 5  # Top tipos/UF nos cards
TOP_UFS_TRIPLET = 5
TOP_TIPOS_MATRIX = 5   # Top tipos na matriz
TOP_UFS_MATRIX = 10   # Top UFs na matriz
TOP_CENARIOS_POR_GRUPO = 3  # Top cenários por Tipo/UF
