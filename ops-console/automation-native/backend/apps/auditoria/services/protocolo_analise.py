from __future__ import annotations

from apps.auditoria.models import AuditoriaAtividadeProtocolo, AuditoriaAtividadeProtocoloEtapa
from apps.auditoria.services.catalog_items import get_valid_tipo_falha_values
from apps.auditoria.services.text_format import is_tipo_falha_automatico, is_tipo_falha_colaborador

VALID_SITUACAO = {
    AuditoriaAtividadeProtocoloEtapa.SITUACAO_IMPROCEDENTE,
    AuditoriaAtividadeProtocoloEtapa.SITUACAO_PROCEDENTE,
}


def _is_row_partial(row: dict) -> bool:
    tracked_fields = (
        "resultado_correto",
        "nivel_dificuldade",
        "tipo_documento",
        "uf_documento",
        "agente",
        "tipo_falha",
        "etapa_falha",
        "tempo_analise",
        "cruzamento_bases",
        "qualidade_imagem",
        "situacao",
        "motivo_falha",
    )
    return any((row.get(field) or "").strip() for field in tracked_fields)


def _is_row_complete(row: dict) -> bool:
    situacao = (row.get("situacao") or "").strip().lower()
    tipo_falha = (row.get("tipo_falha") or "").strip()
    etapa_falha = (row.get("etapa_falha") or "").strip()
    agente = (row.get("agente") or "").strip()
    motivo = (row.get("motivo_falha") or "").strip()

    if situacao not in VALID_SITUACAO:
        return False
    if not tipo_falha or not etapa_falha:
        return False
    if not is_tipo_falha_automatico(tipo_falha) and not agente:
        return False
    if situacao == AuditoriaAtividadeProtocoloEtapa.SITUACAO_PROCEDENTE and not motivo:
        return False
    return True


def validate_protocolo_analise_payload(data: dict) -> dict[str, str]:
    errors: dict[str, str] = {}
    etapas = data.get("etapas")
    if not isinstance(etapas, list) or not etapas:
        errors["etapas"] = "Adicione ao menos uma etapa de análise."
        return errors

    partial_indexes: list[str] = []
    complete_count = 0

    for index, item in enumerate(etapas, start=1):
        if not isinstance(item, dict):
            errors["etapas"] = "Formato de etapas inválido."
            return errors

        row = {key: (value or "") for key, value in item.items()}
        if not _is_row_partial(row):
            continue
        if _is_row_complete(row):
            complete_count += 1
            continue
        partial_indexes.append(str(index))

    if partial_indexes:
        errors["etapas"] = (
            "Preencha todos os campos obrigatórios nas etapas iniciadas: "
            + ", ".join(partial_indexes)
            + "."
        )
        return errors

    if complete_count == 0:
        errors["etapas"] = "Adicione ao menos uma etapa completa para salvar."

    valid_types = get_valid_tipo_falha_values()
    has_colaborador = False
    for index, item in enumerate(etapas, start=1):
        if not isinstance(item, dict) or not _is_row_complete(item):
            continue
        tipo_falha = (item.get("tipo_falha") or "").strip()
        has_colaborador = has_colaborador or is_tipo_falha_colaborador(tipo_falha)
        if tipo_falha not in valid_types:
            errors.setdefault("etapas", f"Tipo de falha inválido na etapa {index}.")

    if has_colaborador and not (data.get("consideracoes_finais") or "").strip():
        errors["consideracoes_finais"] = (
            "Informe a observação para falhas do tipo Colaborador."
        )

    return errors


def summarize_protocolo_situacao(etapas: list[AuditoriaAtividadeProtocoloEtapa]) -> str:
    if not etapas:
        return ""
    if any(item.situacao == AuditoriaAtividadeProtocoloEtapa.SITUACAO_PROCEDENTE for item in etapas):
        return AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE
    if all(item.situacao == AuditoriaAtividadeProtocoloEtapa.SITUACAO_IMPROCEDENTE for item in etapas):
        return AuditoriaAtividadeProtocolo.SITUACAO_IMPROCEDENTE
    return ""
