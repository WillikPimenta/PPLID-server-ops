"""Constantes e identificadores de tarefas da rotina."""
from __future__ import annotations

from app.config import PLAN_IDF_SERASA_BOTS

ROTINAS_PARA_BAIXAR = [
    "BRBR-4467 - Detalhado de registros Todos os clientes 1 Dia atrás",
    "BRBR-4467 - Detalhado de registros Todos os clientes 2 Dias atrás",
    "BRBR-4467 - Detalhado de registros Todos os clientes 3 Dias atrás",
    "BRBR-5336 > Detalhado de Produtividade D-1",
    "BRBR6121 G AUDITORIA Replicados D1 - PARTE 1",
    "G Auditoria com etapas",
]

TASK_ROTINAS_1d = "rotinas_1d"
TASK_ROTINAS_2d = "rotinas_2d"
TASK_ROTINAS_3d = "rotinas_3d"
TASK_PRODUTIVIDADE_D1 = "produtividade_d1"
TASK_AUDITORIA_REPLICADOS_D1 = "auditoria_replicados_d1"
TASK_AUDITORIA_ETAPAS = "auditoria_etapas"
TASK_IRREGULARIDADE = "irregularidade"
TASK_MONITOR_EVENTOS = "monitor_eventos"
TASK_CONFER_PRODUCAO = "confer_producao"
TASK_LOG_EVENTOS = "log_eventos"
TASK_PROD_UNIFICADO = "prod_unificado"
TASK_MONITOR_UNIFICADO = "monitor_unificado"

COLUNAS_MONITOR_UNIFICADO = [
    "Data",
    "Hora",
    "Usuário",
    "Data do Evento",
    "Evento",
    "Data segundo evento",
    "Segundo evento",
]

COLUNAS_AUDITORIA_REPLICADOS_D1 = [
    "Cliente Destino",
    "Data de Cadastro Destino",
    "Protocolo Destino",
    "Workflow Destino",
    "Protocolo Origem",
    "Cliente Origem",
    "Workflow Origem",
    "Nível Hierárquico Origem",
    "Data de Cadastro Origem",
    "Tipo de Conclusão de Análise Origem",
]

TASK_TO_ROTINA_DESCRICAO = {
    TASK_ROTINAS_1d: "BRBR-4467 - Detalhado de registros Todos os clientes 1 Dia atrás",
    TASK_ROTINAS_2d: "BRBR-4467 - Detalhado de registros Todos os clientes 2 Dias atrás",
    TASK_ROTINAS_3d: "BRBR-4467 - Detalhado de registros Todos os clientes 3 Dias atrás",
    TASK_PRODUTIVIDADE_D1: "BRBR-5336 > Detalhado de Produtividade D-1",
    TASK_AUDITORIA_REPLICADOS_D1: "BRBR6121 G AUDITORIA Replicados D1 - PARTE 1",
    TASK_AUDITORIA_ETAPAS: "G Auditoria com etapas",
}

TASK_DEPENDENCIES = {
    TASK_MONITOR_EVENTOS: [TASK_PRODUTIVIDADE_D1],
    TASK_LOG_EVENTOS: [TASK_CONFER_PRODUCAO],
    TASK_PROD_UNIFICADO: [TASK_PRODUTIVIDADE_D1, TASK_CONFER_PRODUCAO],
    TASK_MONITOR_UNIFICADO: [TASK_MONITOR_EVENTOS, TASK_LOG_EVENTOS],
}

TASK_DEFAULT_ALL = [
    TASK_ROTINAS_1d,
    TASK_ROTINAS_2d,
    TASK_ROTINAS_3d,
    TASK_PRODUTIVIDADE_D1,
    TASK_AUDITORIA_REPLICADOS_D1,
    TASK_AUDITORIA_ETAPAS,
    TASK_IRREGULARIDADE,
    TASK_MONITOR_EVENTOS,
    TASK_CONFER_PRODUCAO,
    TASK_LOG_EVENTOS,
    TASK_PROD_UNIFICADO,
    TASK_MONITOR_UNIFICADO,
]

HORA_EXECUCAO_ROTINA = 8
MINUTO_EXECUCAO_ROTINA = 0
DOWNLOADS_TEMP_BASE = PLAN_IDF_SERASA_BOTS / "downloads_temp"
DOWNLOADS_TEMP_ROTINA = DOWNLOADS_TEMP_BASE / "rotina"
DOWNLOADS_TEMP_GED_IRREGULARIDADE = DOWNLOADS_TEMP_ROTINA / "ged_irregularidade"

# Legado: período fixo substituído por quinzenas calendário (ver irregularidade.py).
IRREGULARIDADE_DIAS_PERIODO = 15

IRREGULARIDADE_COLUNAS_MAP = {
    "Protocolo": "Protocolo",
    "Status Contrato": "Status Contrato",
    "MSISDN": "MSISDN",
    "CPF": "CPF",
    "Data Recebimento": "Data do Recebimento",
    "Data da contestação": "Data Contestação",
    "Data de resposta": "Data da Resposta",
    "Tipo de serviço": "Tipo de Serviço",
    "Regional": "Regional",
    "Estado": "Estado",
    "Canal de Ativação": "Canal de Ativação",
    "Cód. PDV": "Cód. PDV",
    "Usuario": "Usuário",
    "Status Contestação": "Status da contestação",
    "Matricula do Inspetor": "Matrícula do Inspetor",
    "Descrição das Irregularidades": "Descrição das Irregularidades",
}

IRREGULARIDADE_COLUNAS_SAIDA = list(IRREGULARIDADE_COLUNAS_MAP.values())

BUSCA_PROTOCOLO_COLUNAS_MAP = {
    "Data/Hora da Conferência": "Data/Hora da Conferência",
    "Tempo de Análise": "Tempo por minuto",
    "Protocolo": "Protocolo",
    "Status": "Status",
    "Ilha": "Ilha",
    "Etapa": "Etapa",
    "Matrícula do Colaborador": "Matrícula",
    "Nome do Colaborador": "Nome",
    "Tipo/Status conferencia": "Tipo/Status conferencia",
}

BUSCA_PROTOCOLO_COLUNAS_SAIDA = list(BUSCA_PROTOCOLO_COLUNAS_MAP.values())
BUSCA_PROTOCOLO_ETAPA_FILTRO = "Reclassificação"
BUSCA_PROTOCOLO_ABA_GERENCIAL = "1"

PRODUTIVIDADE_D1_DESCRICAO = "BRBR-5336 > Detalhado de Produtividade D-1"
AUDITORIA_ETAPAS_DESCRICAO = "G Auditoria com etapas"

DEMANDAS_TABELA = [
    ("detalhado", "Detalhado", [TASK_ROTINAS_1d, TASK_ROTINAS_2d, TASK_ROTINAS_3d]),
    ("prod-unificado", "Prod unificado", [TASK_PROD_UNIFICADO]),
    ("monitor-unificado", "Monitor unificado", [TASK_MONITOR_UNIFICADO]),
    ("replicados-d1", "Replicados D1", [TASK_AUDITORIA_REPLICADOS_D1]),
    ("G-auditoria", "G Auditoria etapas", [TASK_AUDITORIA_ETAPAS]),
    ("irregularidade", "Irregularidade GED", [TASK_IRREGULARIDADE]),
]
TAREFAS_DEMANDAS = [tid for _, _, tasks in DEMANDAS_TABELA for tid in tasks]

OBS_HUMANIZADA = {
    "sem arquivo": "Arquivo não encontrado",
    "dados inválidos": "Dados inválidos",
    "dados invalidos": "Dados inválidos",
    "pasta inacessível": "Pasta de destino indisponível",
    "pasta inacessivel": "Pasta de destino indisponível",
    "arquivo inválido": "Arquivo inválido",
    "arquivo invalido": "Arquivo inválido",
    "cancelado": "Execução cancelada",
    "rotina não": "Rotina indisponível",
    "rotina nao": "Rotina indisponível",
    "validar": "Requer validação",
    "erro": "Falha na execução",
    "ok": "Concluída",
    "arquivos ausentes": "Arquivos de entrada ausentes",
    "falha na união": "Falha na união dos arquivos",
    "união vazia": "União sem dados",
    "colunas ausentes": "Colunas ausentes no arquivo",
}

EXPECTED_COLUMNS = [
    "Protocolo", "Cliente", "Workflow", "CPF", "Data de Cadastro",
    "Data de Conclusão", "Status do Registro",
    "Resultado", "Nível Hierárquico",
    "matrícula", "Data da Primeira Conclusão", "Data de Análise",
    "Alertas",
]

CSV_ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1"]
CSV_SEPARATORS = [";", ",", "\t", "|"]
CSV_CHUNK_SIZE = 50000
