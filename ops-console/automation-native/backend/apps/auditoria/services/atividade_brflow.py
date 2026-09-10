from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import AuditoriaAtividade
from apps.auditoria.services.atividade_crud import resolve_responsavel_from_payload
from apps.auditoria.services.atividade_protocolo_crud import create_protocolo
from apps.auditoria.services.text_format import format_auditoria_label


class OpenAuditoriaAtividadeError(Exception):
    def __init__(self, atividade: AuditoriaAtividade):
        self.atividade = atividade
        super().__init__("Auditoria em andamento.")


def find_open_auditoria_atividade_for_user(user) -> AuditoriaAtividade | None:
    return (
        AuditoriaAtividade.objects.filter(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            created_by=user,
        )
        .exclude(status=AuditoriaAtividade.STATUS_CONCLUIDA)
        .order_by("-created_at")
        .first()
    )


def validate_brflow_atividade_payload(payload: dict) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not isinstance(payload, dict):
        return {"detail": "Payload inválido."}

    brflow_raw = (payload.get("brflow_raw") or "").strip()
    if not brflow_raw:
        errors["brflow_raw"] = "Cole os dados do Brflow."

    brflow_parsed = payload.get("brflow_parsed")
    if brflow_parsed is not None and not isinstance(brflow_parsed, dict):
        errors["brflow_parsed"] = "Dados parseados inválidos."

    parsed = brflow_parsed if isinstance(brflow_parsed, dict) else {}
    protocolo = str(payload.get("protocolo") or parsed.get("protocolo") or "").strip()
    if not protocolo and brflow_raw:
        errors["protocolo"] = "Não foi possível identificar o protocolo no Brflow."

    _, responsavel_error = resolve_responsavel_from_payload(payload)
    if responsavel_error:
        errors["responsavel_id"] = responsavel_error

    return errors


def _nome_from_brflow(parsed: dict, protocolo: str) -> str:
    cliente = str(parsed.get("cliente") or "").strip()
    if cliente and protocolo:
        return f"{cliente} — {protocolo}"
    if protocolo:
        return f"Auditoria {protocolo}"
    return f"Auditoria {date.today().isoformat()}"


@transaction.atomic
def create_atividade_from_brflow(user, payload: dict) -> AuditoriaAtividade:
    # Serializa a criação por usuário sem impor uma constraint retroativa que
    # invalidaria auditorias legadas já abertas. A linha do usuário existe para
    # toda requisição autenticada e é compartilhada por todos os processos.
    get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
    open_atividade = find_open_auditoria_atividade_for_user(user)
    if open_atividade:
        raise OpenAuditoriaAtividadeError(open_atividade)

    parsed = payload.get("brflow_parsed") if isinstance(payload.get("brflow_parsed"), dict) else {}
    protocolo = str(payload.get("protocolo") or parsed.get("protocolo") or "").strip()
    cliente = str(payload.get("cliente") or parsed.get("cliente") or "").strip()
    workflow = str(payload.get("workflow") or parsed.get("workflow") or "").strip()
    nivel = str(
        payload.get("nivel_hierarquico") or parsed.get("nivel_hierarquico") or ""
    ).strip()
    link = str(payload.get("link_demanda") or "").strip()
    nome = str(payload.get("nome") or "").strip() or _nome_from_brflow(parsed, protocolo)
    responsavel, _ = resolve_responsavel_from_payload(payload)
    resultado = str(parsed.get("resultado_analise") or "").strip()

    atividade = AuditoriaAtividade.objects.create(
        tipo=AuditoriaAtividade.TIPO_AUDITORIA,
        nome=nome[:255],
        cliente=cliente[:255],
        workflow=workflow[:255],
        nivel_hierarquico=nivel[:255],
        link_demanda=link[:500],
        brflow_raw=(payload.get("brflow_raw") or "").strip(),
        brflow_parsed=parsed,
        data_recepcao=timezone.now(),
        status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
        total_protocolos=0,
        created_by=user,
        responsavel=responsavel,
        nome_arquivo_original="",
    )

    if protocolo:
        create_protocolo(
            atividade,
            {
                "protocolo": protocolo,
                "workflow": workflow,
                "nivel_hierarquico": nivel,
                "resultado_contestado": format_auditoria_label(resultado) if resultado else "",
            },
        )
        # Persist Brflow no protocolo para a análise de etapas.
        proto = atividade.protocolos.filter(protocolo=protocolo).first()
        if proto:
            proto.brflow_raw = atividade.brflow_raw
            proto.brflow_parsed = parsed
            proto.save(update_fields=["brflow_raw", "brflow_parsed", "updated_at"])

    from apps.auditoria.services.qualidade_promocao import link_pendente_auditoria_from_atividade

    link_pendente_auditoria_from_atividade(atividade)
    return atividade


def seed_falha_defaults_from_atividade(atividade: AuditoriaAtividade) -> dict:
    """Pré-preenche campos de falha a partir do Brflow da atividade."""
    parsed = atividade.brflow_parsed if isinstance(atividade.brflow_parsed, dict) else {}
    resultado = str(parsed.get("resultado_analise") or "").strip()
    return {
        "protocolo": str(parsed.get("protocolo") or "").strip(),
        "brflow_raw": atividade.brflow_raw or "",
        "brflow_parsed": parsed,
        "resultado_cliente": format_auditoria_label(resultado) if resultado else "",
        "demanda_url": atividade.link_demanda or "",
    }
