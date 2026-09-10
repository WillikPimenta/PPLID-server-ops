# -*- coding: utf-8 -*-
"""Normalização de chaves para cadastros D-1 (paridade com automacoes/_normalizar_workflow)."""
from __future__ import annotations

import math
import re
import unicodedata

# Vocabulário único de status operacional (dashboard + reconciliação).
STATUS_PLANEJADO = "planejado"
STATUS_RECEBIDO = "recebido"
STATUS_REPLICADO = "replicado"
STATUS_PENDENTE = "pendente"
STATUS_FALHOU = "falhou"
STATUS_DIVERGENTE = "divergente"
STATUS_IGNORADO = "ignorado"

STATUS_OPERACIONAL_CHOICES = [
    (STATUS_PLANEJADO, "Planejado"),
    (STATUS_RECEBIDO, "Recebido"),
    (STATUS_REPLICADO, "Replicado"),
    (STATUS_PENDENTE, "Pendente"),
    (STATUS_FALHOU, "Falhou"),
    (STATUS_DIVERGENTE, "Divergente"),
    (STATUS_IGNORADO, "Ignorado"),
]

RESULTADO_PENDENTE = "pendente"
RESULTADO_PROCESSANDO = "processando"
RESULTADO_SALVO = "salvo"
RESULTADO_SEM_ALTERACAO = "sem_alteracao"
RESULTADO_PULADO = "pulado"
RESULTADO_INATIVO = "inativo"
RESULTADO_NAO_SALVO = "nao_salvo"
RESULTADO_FALHOU = "falhou"
RESULTADO_CANCELADO = "cancelado"

RESULTADO_CHOICES = [
    (RESULTADO_PENDENTE, "Pendente"),
    (RESULTADO_PROCESSANDO, "Processando"),
    (RESULTADO_SALVO, "Salvo"),
    (RESULTADO_SEM_ALTERACAO, "Sem alteração"),
    (RESULTADO_PULADO, "Pulado"),
    (RESULTADO_INATIVO, "Inativo"),
    (RESULTADO_NAO_SALVO, "Não salvo"),
    (RESULTADO_FALHOU, "Falhou"),
    (RESULTADO_CANCELADO, "Cancelado"),
]

SEVERIDADE_INFO = "info"
SEVERIDADE_AVISO = "aviso"
SEVERIDADE_ERRO = "erro"
SEVERIDADE_CHOICES = [
    (SEVERIDADE_INFO, "Informação"),
    (SEVERIDADE_AVISO, "Aviso"),
    (SEVERIDADE_ERRO, "Erro"),
]

_BRFLOW_OK = frozenset({"SALVO_OK", "UPLOAD_OK"})
_BRFLOW_FAIL = frozenset({"ERRO", "FALHOU", "FALHA", "TIMEOUT", "NAO_SALVO"})
MOTIVOS_BLOQUEANTES = frozenset(
    {
        "CSV_AUSENTE",
        "REGRA_AUSENTE",
        "WORKFLOW_AUSENTE",
        "WORKFLOW_AUSENTE_BRFLOW",
        "REGRA_DIVERGENTE",
        "SALVAMENTO_NAO_CONFIRMADO",
    }
)


def normalizar_resultado_workflow(status_brflow: str, motivo_codigo: str = "") -> dict[str, str | bool]:
    """Normaliza o contrato bot→backend sem perder o status bruto/aliases.

    ``motivo_codigo`` participa da severidade para que o bridge não precise
    duplicar a taxonomia. Erros legados permanecem reconhecidos, enquanto
    cancelamento e skips são avisos, não falhas bloqueantes.
    """
    upper = (status_brflow or "").strip().upper()
    motivo = (motivo_codigo or "").strip().upper()

    if upper in _BRFLOW_OK:
        resultado, status_op, severidade = RESULTADO_SALVO, STATUS_RECEBIDO, SEVERIDADE_INFO
    elif upper == "SEM_ALTERACAO":
        resultado, status_op, severidade = (
            RESULTADO_SEM_ALTERACAO,
            STATUS_RECEBIDO,
            SEVERIDADE_AVISO,
        )
    elif upper == "PROCESSANDO":
        resultado, status_op, severidade = RESULTADO_PROCESSANDO, STATUS_PENDENTE, SEVERIDADE_INFO
    elif upper == "PULADO":
        resultado, status_op, severidade = RESULTADO_PULADO, STATUS_IGNORADO, SEVERIDADE_AVISO
    elif upper == "INATIVO":
        resultado, status_op, severidade = RESULTADO_INATIVO, STATUS_IGNORADO, SEVERIDADE_INFO
    elif upper == "CANCELADO":
        resultado, status_op, severidade = RESULTADO_CANCELADO, STATUS_IGNORADO, SEVERIDADE_AVISO
    elif upper == "NAO_SALVO":
        resultado, status_op, severidade = RESULTADO_NAO_SALVO, STATUS_FALHOU, SEVERIDADE_ERRO
    elif upper in {"ERRO", "FALHOU", "FALHA", "TIMEOUT"}:
        resultado, status_op, severidade = RESULTADO_FALHOU, STATUS_FALHOU, SEVERIDADE_ERRO
    else:
        resultado, status_op, severidade = RESULTADO_PENDENTE, STATUS_PENDENTE, SEVERIDADE_INFO

    # Um motivo explicitamente bloqueante promove um skip/estado desconhecido
    # para erro sem reescrever o resultado bruto que explica o que ocorreu.
    bloqueante_por_motivo = motivo in MOTIVOS_BLOQUEANTES or motivo.startswith(
        ("ERRO", "FALHA", "NAO_SALVO", "TIMEOUT")
    )
    falha_bloqueante = status_op == STATUS_FALHOU or bloqueante_por_motivo
    if falha_bloqueante:
        status_op = STATUS_FALHOU
        severidade = SEVERIDADE_ERRO

    return {
        "resultado": resultado,
        "status_operacional": status_op,
        "severidade": severidade,
        "falha_bloqueante": falha_bloqueante,
    }


def brflow_to_status_operacional(status_brflow: str) -> str:
    """Mapeia status bruto BRFlow para vocabulário operacional unificado."""
    return str(normalizar_resultado_workflow(status_brflow)["status_operacional"])


def brflow_erro_codigo(status_brflow: str) -> str:
    upper = (status_brflow or "").strip().upper()
    normalizado = normalizar_resultado_workflow(status_brflow)
    if not normalizado["falha_bloqueante"]:
        return ""
    if upper in _BRFLOW_FAIL:
        return f"BRFLOW_{upper}"
    return "BRFLOW_UNKNOWN"


def normalize_protocolo(valor: str | int | float | None) -> str:
    """Normaliza ID de protocolo para join (espaços, zeros à esquerda, caixa)."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    texto = unicodedata.normalize("NFKC", str(valor)).strip()
    if not texto:
        return ""
    if re.fullmatch(r"\d+", texto):
        return str(int(texto))
    return texto.casefold()


def normalize_key(nome: str) -> str:
    """Normaliza nome para comparação (acentos, maiúsculas, espaços, hífens unicode)."""
    if nome is None or (isinstance(nome, float) and math.isnan(nome)):
        return ""
    texto = unicodedata.normalize("NFKC", str(nome))
    texto = texto.replace("\u2013", "-").replace("\u2014", "-")
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"\s+", " ", texto.strip())
    return texto.casefold()


WORKFLOW_DESTINO_G_AUDITORIA = "g_auditoria"
WORKFLOW_DESTINO_DOC_31 = "doc_31"
WORKFLOW_DESTINO_BIO = "bio"
WORKFLOW_DESTINO_REDOC = "redoc"
WORKFLOW_DESTINO_OUTROS = "outros"
WORKFLOW_DESTINO_SEM_INFORMACAO = "sem_informacao"

WORKFLOW_DESTINO_LABELS = {
    WORKFLOW_DESTINO_G_AUDITORIA: "G Auditoria",
    WORKFLOW_DESTINO_DOC_31: "Analise Direcionada",
    WORKFLOW_DESTINO_BIO: "Auditoria Biometria",
    WORKFLOW_DESTINO_REDOC: "Auditoria Redoc",
    WORKFLOW_DESTINO_OUTROS: "Outros",
    WORKFLOW_DESTINO_SEM_INFORMACAO: "Sem informação",
}

CANAL_DESTINO_TO_BUCKET = {
    "brflow": WORKFLOW_DESTINO_G_AUDITORIA,
    "case manager": WORKFLOW_DESTINO_DOC_31,
    "brflow bio": WORKFLOW_DESTINO_BIO,
    "brflow redoc": WORKFLOW_DESTINO_REDOC,
}

PROJECTION_CANAL_TO_BUCKET = {
    "g auditoria": WORKFLOW_DESTINO_G_AUDITORIA,
    "case": WORKFLOW_DESTINO_DOC_31,
    "bio": WORKFLOW_DESTINO_BIO,
    "redoc": WORKFLOW_DESTINO_REDOC,
}

FILA_TO_DESTINO_BUCKET = {
    "g auditoria": WORKFLOW_DESTINO_G_AUDITORIA,
    "3.1": WORKFLOW_DESTINO_DOC_31,
    "bio": WORKFLOW_DESTINO_BIO,
    "redoc": WORKFLOW_DESTINO_REDOC,
}

WORKFLOW_DESTINO_ORDER = (
    WORKFLOW_DESTINO_G_AUDITORIA,
    WORKFLOW_DESTINO_DOC_31,
    WORKFLOW_DESTINO_BIO,
    WORKFLOW_DESTINO_REDOC,
    WORKFLOW_DESTINO_OUTROS,
    WORKFLOW_DESTINO_SEM_INFORMACAO,
)


def classify_workflow_destino(raw: str | None) -> tuple[str, str]:
    """Classifica workflow destino do relatório BRFlow em bucket canônico do dashboard."""
    text = str(raw or "").strip()
    if not text:
        return WORKFLOW_DESTINO_SEM_INFORMACAO, WORKFLOW_DESTINO_LABELS[WORKFLOW_DESTINO_SEM_INFORMACAO]

    normalized = normalize_key(text)
    if "g auditoria" in normalized:
        return WORKFLOW_DESTINO_G_AUDITORIA, WORKFLOW_DESTINO_LABELS[WORKFLOW_DESTINO_G_AUDITORIA]
    if (
        "analise direcionada" in normalized
        or "análise direcionada" in normalized
        or "documentoscopia" in normalized
        or "3.1" in normalized
        or "3 1" in normalized
    ):
        return WORKFLOW_DESTINO_DOC_31, WORKFLOW_DESTINO_LABELS[WORKFLOW_DESTINO_DOC_31]
    if "biometria" in normalized:
        return WORKFLOW_DESTINO_BIO, WORKFLOW_DESTINO_LABELS[WORKFLOW_DESTINO_BIO]
    if "redoc" in normalized:
        return WORKFLOW_DESTINO_REDOC, WORKFLOW_DESTINO_LABELS[WORKFLOW_DESTINO_REDOC]
    return WORKFLOW_DESTINO_OUTROS, WORKFLOW_DESTINO_LABELS[WORKFLOW_DESTINO_OUTROS]


def destino_bucket_for_canal(canal: str) -> str | None:
    return CANAL_DESTINO_TO_BUCKET.get(normalize_key(canal))


def destino_bucket_for_projection_canal(canal: str) -> str | None:
    key = normalize_key(canal)
    if key in PROJECTION_CANAL_TO_BUCKET:
        return PROJECTION_CANAL_TO_BUCKET[key]
    return destino_bucket_for_canal(canal)


def destino_bucket_for_fila(fila: str) -> str | None:
    return FILA_TO_DESTINO_BUCKET.get(normalize_key(fila))


def workflow_destino_q_for_bucket(bucket_key: str):
    """Filtro SQL alinhado aos valores reais do CSV/brflow-replicadosd1-tratado."""
    from django.db.models import Q

    if bucket_key == WORKFLOW_DESTINO_G_AUDITORIA:
        return Q(workflow_destino__icontains="G Auditoria")
    if bucket_key == WORKFLOW_DESTINO_DOC_31:
        return (
            Q(workflow_destino__icontains="Analise Direcionada")
            | Q(workflow_destino__icontains="Análise Direcionada")
            | Q(workflow_destino__icontains="Documentoscopia")
            | Q(workflow_destino__icontains="3.1")
        )
    if bucket_key == WORKFLOW_DESTINO_BIO:
        return Q(workflow_destino__icontains="Biometria")
    if bucket_key == WORKFLOW_DESTINO_REDOC:
        return Q(workflow_destino__icontains="Redoc")
    if bucket_key == WORKFLOW_DESTINO_SEM_INFORMACAO:
        return Q(workflow_destino="")
    if bucket_key == WORKFLOW_DESTINO_OUTROS:
        known = workflow_destino_q_for_bucket(WORKFLOW_DESTINO_G_AUDITORIA)
        known |= workflow_destino_q_for_bucket(WORKFLOW_DESTINO_DOC_31)
        known |= workflow_destino_q_for_bucket(WORKFLOW_DESTINO_BIO)
        known |= workflow_destino_q_for_bucket(WORKFLOW_DESTINO_REDOC)
        return ~known & ~Q(workflow_destino="")
    return Q(pk__in=[])
