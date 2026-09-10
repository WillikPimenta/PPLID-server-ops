from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction

from apps.auditoria.models import AuditoriaAtividade, QualidadePendenteAuditoria
from apps.auditoria.services.atividade_sla import parse_data_recepcao
from apps.workforce.models import Agent


def _clean_optional_str(value, *, max_length: int | None = None) -> tuple[str | None, str | None]:
    if value is None:
        return "", None
    if not isinstance(value, str):
        return None, "Valor inválido."
    cleaned = value.strip()
    if max_length is not None and len(cleaned) > max_length:
        return None, f"Deve ter no máximo {max_length} caracteres."
    return cleaned, None


def resolve_responsavel_from_payload(payload: dict) -> tuple[Agent | None, str | None]:
    """Resolve responsável (Agent do headcount) a partir de responsavel_id."""
    if "responsavel_id" not in payload:
        return None, None

    raw = payload.get("responsavel_id")
    if raw in (None, "", 0, "0"):
        return None, None

    agent_id = str(raw).strip()
    if not agent_id:
        return None, None

    agent = Agent.objects.filter(pk=agent_id, active=True).first()
    if not agent:
        return None, "Responsável não encontrado no headcount."
    return agent, None


def list_headcount_responsaveis() -> list[dict[str, str]]:
    """Opções {id, label} dos colaboradores ativos do headcount."""
    results: list[dict[str, str]] = []
    for agent in (
        Agent.objects.filter(active=True)
        .exclude(full_name__exact="")
        .order_by("full_name", "user_lan_id")
        .only("id", "full_name", "user_lan_id")
    ):
        label = (agent.full_name or "").strip()
        if not label:
            continue
        lan = (agent.user_lan_id or "").strip()
        display = f"{label} ({lan})" if lan else label
        results.append({"id": str(agent.pk), "label": display, "matricula": lan})
    return results


def validate_atividade_create_payload(payload: dict) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not isinstance(payload, dict):
        return {"detail": "Payload inválido."}

    nome = payload.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        errors["nome"] = "O nome da atividade é obrigatório."
    elif len(nome.strip()) > 255:
        errors["nome"] = "O nome da atividade deve ter no máximo 255 caracteres."

    for field, max_length in (
        ("workflow", 255),
        ("nivel_hierarquico", 255),
        ("cliente", 255),
    ):
        _, field_error = _clean_optional_str(payload.get(field), max_length=max_length)
        if field_error:
            errors[field] = field_error

    link_demanda, link_error = _clean_optional_str(payload.get("link_demanda"), max_length=500)
    if link_error:
        errors["link_demanda"] = link_error
    elif link_demanda:
        try:
            URLValidator()(link_demanda)
        except ValidationError:
            errors["link_demanda"] = "Informe um link válido para a demanda."

    _, recepcao_error = parse_data_recepcao(payload.get("data_recepcao"))
    if recepcao_error:
        errors["data_recepcao"] = recepcao_error

    _, responsavel_error = resolve_responsavel_from_payload(payload)
    if responsavel_error:
        errors["responsavel_id"] = responsavel_error

    return errors


def validate_atividade_update_payload(payload: dict) -> dict[str, str]:
    """Validação parcial de PATCH: só exige/valida campos presentes no payload."""
    if not isinstance(payload, dict):
        return {"detail": "Payload inválido."}

    errors: dict[str, str] = {}

    if "nome" in payload:
        nome = payload.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            errors["nome"] = "O nome da atividade é obrigatório."
        elif len(nome.strip()) > 255:
            errors["nome"] = "O nome da atividade deve ter no máximo 255 caracteres."

    for field, max_length in (
        ("workflow", 255),
        ("nivel_hierarquico", 255),
        ("cliente", 255),
    ):
        if field not in payload:
            continue
        _, field_error = _clean_optional_str(payload.get(field), max_length=max_length)
        if field_error:
            errors[field] = field_error

    if "link_demanda" in payload:
        link_demanda, link_error = _clean_optional_str(payload.get("link_demanda"), max_length=500)
        if link_error:
            errors["link_demanda"] = link_error
        elif link_demanda:
            try:
                URLValidator()(link_demanda)
            except ValidationError:
                errors["link_demanda"] = "Informe um link válido para a demanda."

    if "data_recepcao" in payload:
        _, recepcao_error = parse_data_recepcao(payload.get("data_recepcao"))
        if recepcao_error:
            errors["data_recepcao"] = recepcao_error

    if "responsavel_id" in payload:
        _, responsavel_error = resolve_responsavel_from_payload(payload)
        if responsavel_error:
            errors["responsavel_id"] = responsavel_error

    if "observacao" in payload:
        observacao = payload.get("observacao")
        if observacao is not None and not isinstance(observacao, str):
            errors["observacao"] = "Valor inválido."

    return errors


ATIVIDADE_CONCLUIDA_MUTATION_ERROR = (
    "Atividades finalizadas não podem ser alteradas ou excluídas."
)


def ensure_atividade_mutavel(atividade: AuditoriaAtividade) -> None:
    """Bloqueia mutações em atividades com status concluída."""
    if atividade.status == AuditoriaAtividade.STATUS_CONCLUIDA:
        raise ValueError(ATIVIDADE_CONCLUIDA_MUTATION_ERROR)


def _atividade_fields_from_payload(payload: dict) -> dict:
    data_recepcao, _ = parse_data_recepcao(payload.get("data_recepcao"))
    fields = {
        "nome": (payload.get("nome") or "").strip(),
        "workflow": (payload.get("workflow") or "").strip(),
        "nivel_hierarquico": (payload.get("nivel_hierarquico") or "").strip(),
        "cliente": (payload.get("cliente") or "").strip(),
        "link_demanda": (payload.get("link_demanda") or "").strip(),
        "data_recepcao": data_recepcao,
    }
    if "responsavel_id" in payload:
        responsavel, _ = resolve_responsavel_from_payload(payload)
        fields["responsavel"] = responsavel
    return fields


@transaction.atomic
def create_atividade(user, payload: dict) -> AuditoriaAtividade:
    fields = _atividade_fields_from_payload(payload)
    tipo = (payload.get("tipo") or "").strip().lower()
    if tipo not in {
        AuditoriaAtividade.TIPO_AUDITORIA,
        AuditoriaAtividade.TIPO_CONTESTACAO,
        AuditoriaAtividade.TIPO_REINSPECAO,
    }:
        tipo = AuditoriaAtividade.TIPO_CONTESTACAO
    return AuditoriaAtividade.objects.create(
        **fields,
        tipo=tipo,
        status=AuditoriaAtividade.STATUS_PENDENTE,
        total_protocolos=0,
        created_by=user,
        nome_arquivo_original="",
    )


@transaction.atomic
def update_atividade(atividade: AuditoriaAtividade, payload: dict) -> AuditoriaAtividade:
    ensure_atividade_mutavel(atividade)
    fields: dict = {}
    if "nome" in payload:
        fields["nome"] = (payload.get("nome") or "").strip()
    if "workflow" in payload:
        fields["workflow"] = (payload.get("workflow") or "").strip()
    if "nivel_hierarquico" in payload:
        fields["nivel_hierarquico"] = (payload.get("nivel_hierarquico") or "").strip()
    if "cliente" in payload:
        fields["cliente"] = (payload.get("cliente") or "").strip()
    if "link_demanda" in payload:
        fields["link_demanda"] = (payload.get("link_demanda") or "").strip()
    if "data_recepcao" in payload:
        data_recepcao, _ = parse_data_recepcao(payload.get("data_recepcao"))
        fields["data_recepcao"] = data_recepcao
    if "responsavel_id" in payload:
        responsavel, _ = resolve_responsavel_from_payload(payload)
        fields["responsavel"] = responsavel
    if "observacao" in payload:
        observacao = payload.get("observacao")
        fields["observacao"] = observacao.strip() if isinstance(observacao, str) else ""
    for key, value in fields.items():
        setattr(atividade, key, value)
    if fields:
        atividade.save(update_fields=[*fields.keys(), "updated_at"])
    return atividade


@transaction.atomic
def delete_atividade(atividade: AuditoriaAtividade) -> None:
    ensure_atividade_mutavel(atividade)
    arquivo = atividade.arquivo_original
    if atividade.tipo == AuditoriaAtividade.TIPO_AUDITORIA:
        QualidadePendenteAuditoria.objects.filter(atividade=atividade).delete()
    atividade.delete()
    if arquivo:
        arquivo.delete(save=False)
