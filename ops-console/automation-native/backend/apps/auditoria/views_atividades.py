from __future__ import annotations

import hashlib
import logging
import uuid
from time import perf_counter

from django.db import connection
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.access.permissions import HasPortalPermission
from apps.access.registry import (
    QUAL_AUDITORIA_CHANGE,
    QUAL_AUDITORIA_COMPLIANCE_CREATE,
    QUAL_AUDITORIA_CREATE,
    QUAL_AUDITORIA_VIEW,
)
from apps.access.resolve import user_has_permission
from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeImportStaging,
    AuditoriaComplianceImportStaging,
    AuditoriaAtividadeProtocolo,
    AuditoriaAtividadeProtocoloEtapa,
)
from apps.auditoria.services.atividade_crud import (
    ATIVIDADE_CONCLUIDA_MUTATION_ERROR,
    create_atividade,
    delete_atividade,
    list_headcount_responsaveis,
    resolve_responsavel_from_payload,
    update_atividade,
    validate_atividade_create_payload,
    validate_atividade_update_payload,
)
from apps.auditoria.services.atividade_brflow import (
    OpenAuditoriaAtividadeError,
    create_atividade_from_brflow,
    seed_falha_defaults_from_atividade,
    validate_brflow_atividade_payload,
)
from apps.auditoria.services.atividade_falhas import (
    create_falha_for_atividade,
    delete_falha_for_atividade,
    finalizar_atividade_auditoria,
    save_falhas_batch,
    update_atividade_trilha_brflow,
    update_falha_for_atividade,
)
from apps.auditoria.services.atividade_protocolo_crud import (
    create_protocolo,
    delete_protocolo,
    sync_atividade_protocolo_metrics,
    update_protocolo_brflow,
    validate_protocolo_create_payload,
)
from apps.auditoria.services.atividade_import import (
    confirm_import,
    create_import_staging,
    preview_import,
)
from apps.auditoria.services.reinspecao_atividade_import import (
    confirm_reinspecao_import,
    create_reinspecao_import_staging,
    preview_reinspecao_import,
)
from apps.auditoria.services.auditoria_compliance_atividade_import import (
    confirm_reinspecao_import as confirm_auditoria_compliance_import,
)
from apps.auditoria.services.contestacao_export import export_atividade_retorno
from apps.auditoria.services.atividade_serialization import (
    resolve_atividade_protocolo,
    serialize_atividade,
    serialize_atividade_protocolo,
)
from apps.auditoria.services.serialization import (
    serialize_falha_cadastro,
    serialize_falha_pendente_auditoria,
)
from apps.auditoria.services.atividade_sla import parse_data_recepcao
from apps.auditoria.services.protocolo_analise import (
    summarize_protocolo_situacao,
    validate_protocolo_analise_payload,
)
from apps.auditoria.services.suporte_operacional_link import resolve_and_link
from apps.auditoria.services.text_format import is_tipo_falha_automatico

logger = logging.getLogger(__name__)


class _DbQueryTimer:
    def __init__(self) -> None:
        self.count = 0
        self.elapsed_ms = 0.0

    def __call__(self, execute, sql, params, many, context):
        started_at = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.count += 1
            self.elapsed_ms += (perf_counter() - started_at) * 1000

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _validate_upload(uploaded_file) -> str | None:
    if not uploaded_file:
        return "Nenhum arquivo recebido pelo servidor. Selecione o arquivo novamente e tente outra vez."
    name = (uploaded_file.name or "").lower()
    if not (name.endswith(".xlsx") or name.endswith(".csv")):
        return "Envie um arquivo .xlsx ou .csv."
    if uploaded_file.size <= 0:
        return "Arquivo vazio."
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        return "Arquivo muito grande (máximo 20 MB)."
    return None


def _get_protocolo(atividade_id: int, protocolo_id: int) -> AuditoriaAtividadeProtocolo | None:
    return (
        AuditoriaAtividadeProtocolo.objects.select_related(
            "atividade",
            "analisado_por",
            "tratado_referencia",
            "tratado_referencia__atividade",
        )
        .prefetch_related("etapas")
        .filter(atividade_id=atividade_id, pk=protocolo_id)
        .first()
    )


TIPO_CONCLUSAO_ALLOWED = {"Automática", "Manual", "Processual"}


def _atividade_bloqueada_response(atividade: AuditoriaAtividade):
    if atividade.status == AuditoriaAtividade.STATUS_CONCLUIDA:
        return Response(
            {"detail": ATIVIDADE_CONCLUIDA_MUTATION_ERROR},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _atividade_owner_response(request, atividade: AuditoriaAtividade):
    if atividade.created_by_id == request.user.id:
        return None
    if user_has_permission(request.user, QUAL_AUDITORIA_CHANGE):
        return None
    return Response(
        {"detail": "Somente o criador ou um administrador pode alterar esta atividade."},
        status=status.HTTP_403_FORBIDDEN,
    )


def _normalize_tipo_conclusao(value) -> str:
    raw = (value or "").strip() if isinstance(value, str) else ""
    if not raw:
        return ""
    for option in TIPO_CONCLUSAO_ALLOWED:
        if option.casefold() == raw.casefold():
            return option
        if option.casefold().replace("á", "a") == raw.casefold().replace("á", "a"):
            return option
    return ""


def _apply_protocolo_analise(protocolo: AuditoriaAtividadeProtocolo, payload: dict, user):
    protocolo.brflow_raw = (payload.get("brflow_raw") or "").strip()
    protocolo.brflow_parsed = payload.get("brflow_parsed") if isinstance(payload.get("brflow_parsed"), dict) else {}
    protocolo.consideracoes_finais = (payload.get("consideracoes_finais") or "").strip()
    if "tipo_conclusao" in payload:
        protocolo.tipo_conclusao = _normalize_tipo_conclusao(payload.get("tipo_conclusao"))
    reanalisado_raw = payload.get("reanalisado", False)
    if isinstance(reanalisado_raw, str):
        protocolo.reanalisado = reanalisado_raw.strip().lower() in {"1", "true", "sim", "yes"}
    else:
        protocolo.reanalisado = bool(reanalisado_raw)

    etapas_payload = payload.get("etapas") if isinstance(payload.get("etapas"), list) else []
    protocolo.etapas.all().delete()

    created_etapas: list[AuditoriaAtividadeProtocoloEtapa] = []
    for index, item in enumerate(etapas_payload):
        if not isinstance(item, dict):
            continue
        tipo_falha = (item.get("tipo_falha") or "").strip()
        etapa = AuditoriaAtividadeProtocoloEtapa(
            protocolo=protocolo,
            ordem=index,
            resultado_correto=(item.get("resultado_correto") or "").strip(),
            nivel_dificuldade=(item.get("nivel_dificuldade") or "").strip(),
            tipo_documento=(item.get("tipo_documento") or "").strip(),
            uf_documento=(item.get("uf_documento") or "").strip(),
            agente="" if is_tipo_falha_automatico(tipo_falha) else (item.get("agente") or "").strip(),
            tipo_falha=tipo_falha,
            etapa_falha=(item.get("etapa_falha") or "").strip(),
            tempo_analise=(item.get("tempo_analise") or "").strip(),
            cruzamento_bases=(item.get("cruzamento_bases") or "").strip(),
            qualidade_imagem=(item.get("qualidade_imagem") or "").strip(),
            situacao=(item.get("situacao") or "").strip().lower(),
            motivo_falha=(item.get("motivo_falha") or "").strip(),
        )
        tracked = (
            etapa.resultado_correto,
            etapa.nivel_dificuldade,
            etapa.tipo_documento,
            etapa.uf_documento,
            etapa.agente,
            etapa.tipo_falha,
            etapa.etapa_falha,
            etapa.tempo_analise,
            etapa.cruzamento_bases,
            etapa.qualidade_imagem,
            etapa.situacao,
            etapa.motivo_falha,
        )
        if not any(tracked):
            continue
        created_etapas.append(etapa)

    if created_etapas:
        AuditoriaAtividadeProtocoloEtapa.objects.bulk_create(created_etapas)

    protocolo.situacao = summarize_protocolo_situacao(created_etapas)
    finalizar = bool(payload.get("finalizar"))
    now = timezone.now()
    protocolo.status = (
        AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
        if finalizar
        else AuditoriaAtividadeProtocolo.STATUS_EM_ANDAMENTO
    )
    protocolo.analisado_por = user
    protocolo.analisado_em = now
    if finalizar:
        if protocolo.finalizado_em is None:
            protocolo.finalizado_em = now
    else:
        protocolo.finalizado_em = None
    protocolo.save()

    atividade = protocolo.atividade
    if finalizar and atividade.tipo == AuditoriaAtividade.TIPO_CONTESTACAO:
        from apps.auditoria.services.qualidade_promocao import promover_protocolo_contestacao

        # Promove para tabela central de tratados e remove o intermediário vivo.
        falha = promover_protocolo_contestacao(protocolo, user=user)
        sync_atividade_protocolo_metrics(atividade)
        return falha

    sync_atividade_protocolo_metrics(atividade)
    return None


class AuditoriaAtividadeImportValidateView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        error = _validate_upload(uploaded_file)
        if error:
            return Response({"errors": {"file": error}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            file_bytes = uploaded_file.read()
            preview = preview_import(file_bytes, uploaded_file.name)
            if preview.errors:
                return Response(
                    {"errors": {"file": preview.errors[0]}, "details": preview.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            staging = create_import_staging(
                user=request.user,
                file_bytes=file_bytes,
                filename=uploaded_file.name,
                preview=preview,
            )
        except Exception:
            logger.exception("Falha ao validar importação de contestação user=%s", request.user)
            return Response(
                {
                    "errors": {
                        "file": "Erro interno ao processar o arquivo Excel. Verifique o formato e tente novamente.",
                    }
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "import_token": str(staging.token),
                "preview": preview.to_dict(),
            }
        )


class AuditoriaAtividadeImportConfirmView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        token_raw = (payload.get("import_token") or "").strip()
        nome = (payload.get("nome") or "").strip()

        try:
            token = uuid.UUID(token_raw)
        except ValueError:
            return Response(
                {"errors": {"import_token": "Token de importação inválido."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        staging = (
            AuditoriaAtividadeImportStaging.objects.filter(token=token, created_by=request.user)
            .select_related("created_by")
            .first()
        )
        if not staging:
            return Response(
                {"errors": {"import_token": "Importação não encontrada ou expirada."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if staging.expires_at < timezone.now():
            staging.delete()
            return Response(
                {"errors": {"import_token": "A prévia da importação expirou. Envie o arquivo novamente."}},
                status=status.HTTP_410_GONE,
            )

        link_demanda = (payload.get("link_demanda") or "").strip()
        cliente = (payload.get("cliente") or "").strip()
        data_recepcao, recepcao_error = parse_data_recepcao(payload.get("data_recepcao"))
        if recepcao_error:
            return Response(
                {"errors": {"data_recepcao": recepcao_error}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        responsavel, responsavel_error = resolve_responsavel_from_payload(payload)
        if responsavel_error:
            return Response(
                {"errors": {"responsavel_id": responsavel_error}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if link_demanda:
            validator = URLValidator()
            try:
                validator(link_demanda)
            except ValidationError:
                return Response(
                    {"errors": {"link_demanda": "Informe um link válido para a demanda."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            atividade = confirm_import(
                staging=staging,
                user=request.user,
                nome=nome or None,
                cliente=cliente or None,
                link_demanda=link_demanda or None,
                data_recepcao=data_recepcao,
                responsavel=responsavel,
            )
        except ValueError as exc:
            return Response({"errors": {"file": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)

        return Response(serialize_atividade(atividade), status=status.HTTP_201_CREATED)


class AuditoriaReinspecaoImportValidateView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        error = _validate_upload(uploaded_file)
        if error:
            return Response({"errors": {"file": error}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            file_bytes = uploaded_file.read()
            preview = preview_reinspecao_import(file_bytes, uploaded_file.name)
            if preview.errors:
                return Response(
                    {"errors": {"file": preview.errors[0]}, "details": preview.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            file_sha256 = hashlib.sha256(file_bytes).hexdigest()
            staging, _ = create_reinspecao_import_staging(
                user=request.user,
                file_bytes=file_bytes,
                filename=uploaded_file.name,
                preview=preview,
                file_sha256=file_sha256,
            )
        except Exception:
            logger.exception("Falha ao validar importação de reinspeção user=%s", request.user)
            return Response(
                {
                    "errors": {
                        "file": "Erro interno ao processar o arquivo Excel. Verifique o formato e tente novamente.",
                    }
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "import_token": str(staging.token),
                "preview": preview.to_dict(),
            }
        )


class AuditoriaReinspecaoImportConfirmView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        token_raw = (payload.get("import_token") or "").strip()

        try:
            token = uuid.UUID(token_raw)
        except ValueError:
            return Response(
                {"errors": {"import_token": "Token de importação inválido."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        staging = (
            AuditoriaAtividadeImportStaging.objects.filter(token=token, created_by=request.user)
            .select_related("created_by")
            .first()
        )
        if not staging:
            return Response(
                {"errors": {"import_token": "Importação não encontrada ou expirada."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if staging.expires_at < timezone.now():
            staging.delete()
            return Response(
                {"errors": {"import_token": "A prévia da importação expirou. Envie o arquivo novamente."}},
                status=status.HTTP_410_GONE,
            )

        try:
            result = confirm_reinspecao_import(staging=staging, user=request.user)
        except ValueError as exc:
            return Response({"errors": {"file": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_201_CREATED)


class AuditoriaComplianceImportValidateView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_COMPLIANCE_CREATE
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        error = _validate_upload(uploaded_file)
        if error:
            return Response({"errors": {"file": error}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            protocolos_por_agente = int(
                str(request.data.get("protocolos_por_agente") or "").strip()
            )
        except ValueError:
            return Response(
                {
                    "errors": {
                        "protocolos_por_agente": (
                            "Informe um número válido para protocolos por agente (A)."
                        )
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        seed_raw = str(request.data.get("seed") or "").strip()
        rng = None
        if seed_raw:
            try:
                import random as random_mod

                rng = random_mod.Random(int(seed_raw))
            except ValueError:
                return Response(
                    {"errors": {"seed": "Seed de amostragem inválida."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            from apps.auditoria.services.auditoria_compliance_import import (
                create_compliance_import_staging,
                sample_compliance_import,
            )

            file_bytes = uploaded_file.read()
            preview = sample_compliance_import(
                file_bytes=file_bytes,
                filename=uploaded_file.name,
                protocolos_por_agente=protocolos_por_agente,
                rng=rng,
            )
            if preview.errors:
                return Response(
                    {"errors": {"file": preview.errors[0]}, "details": preview.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            staging = create_compliance_import_staging(
                user=request.user,
                file_bytes=file_bytes,
                filename=uploaded_file.name,
                preview=preview,
            )
        except Exception:
            logger.exception(
                "Falha ao validar importação de Auditoria Compliance user=%s",
                request.user,
            )
            return Response(
                {
                    "errors": {
                        "file": (
                            "Erro interno ao processar o arquivo Excel. "
                            "Verifique o formato e tente novamente."
                        )
                    }
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"import_token": str(staging.token), "preview": preview.to_dict()}
        )


class AuditoriaComplianceImportConfirmView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_COMPLIANCE_CREATE

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            token = uuid.UUID((payload.get("import_token") or "").strip())
        except ValueError:
            return Response(
                {"errors": {"import_token": "Token de importação inválido."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        staging = (
            AuditoriaComplianceImportStaging.objects.filter(
                token=token,
                created_by=request.user,
            )
            .select_related("created_by")
            .first()
        )
        if not staging:
            return Response(
                {"errors": {"import_token": "Importação não encontrada ou expirada."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if staging.expires_at < timezone.now():
            staging.delete()
            return Response(
                {
                    "errors": {
                        "import_token": (
                            "A prévia da importação expirou. Envie o arquivo novamente."
                        )
                    }
                },
                status=status.HTTP_410_GONE,
            )

        try:
            result = confirm_auditoria_compliance_import(
                staging=staging,
                user=request.user,
            )
        except ValueError as exc:
            return Response({"errors": {"file": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class AuditoriaAtividadeResponsaveisView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        return Response({"results": list_headcount_responsaveis()})


class AuditoriaAtividadeListView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method == "POST":
            self.portal_permission = QUAL_AUDITORIA_CREATE
        else:
            self.portal_permission = QUAL_AUDITORIA_VIEW
        super().initial(request, *args, **kwargs)

    def get(self, request):
        tipo = (request.query_params.get("tipo") or "").strip().lower()
        if tipo not in {
            AuditoriaAtividade.TIPO_AUDITORIA,
            AuditoriaAtividade.TIPO_CONTESTACAO,
            AuditoriaAtividade.TIPO_REINSPECAO,
        }:
            tipo = AuditoriaAtividade.TIPO_CONTESTACAO

        queryset = (
            AuditoriaAtividade.objects.select_related("created_by", "responsavel")
            .prefetch_related("protocolos")
            .filter(tipo=tipo)
            .annotate(
                protocolos_tratamento_pendentes=Count(
                    "protocolos",
                    filter=~Q(protocolos__status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO),
                ),
                protocolos_espelhados=Count(
                    "protocolos",
                    filter=Q(protocolos__tratado_referencia_id__isnull=False),
                ),
            )
            .order_by("-created_at")
        )

        protocolo_q = (request.query_params.get("protocolo") or "").strip()
        if protocolo_q:
            queryset = queryset.filter(
                Q(protocolos__protocolo__icontains=protocolo_q)
                | Q(falhas__protocolo__icontains=protocolo_q)
                | Q(brflow_parsed__protocolo__icontains=protocolo_q)
                | Q(nome__icontains=protocolo_q)
            ).distinct()

        if (request.query_params.get("mine") or "").strip().lower() in {"1", "true", "yes"}:
            queryset = queryset.filter(created_by=request.user)

        if (request.query_params.get("andamento") or "").strip().lower() in {"1", "true", "yes"}:
            queryset = queryset.exclude(status=AuditoriaAtividade.STATUS_CONCLUIDA)

        return Response({"results": [serialize_atividade(item) for item in queryset]})

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        errors = validate_atividade_create_payload(payload)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        atividade = create_atividade(request.user, payload)
        return Response(serialize_atividade(atividade), status=status.HTTP_201_CREATED)


class AuditoriaAtividadeDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method in ("PATCH", "DELETE"):
            self.portal_permission = QUAL_AUDITORIA_CREATE
        else:
            self.portal_permission = QUAL_AUDITORIA_VIEW
        super().initial(request, *args, **kwargs)

    def get(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.select_related("created_by", "responsavel").filter(pk=pk).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_atividade(atividade))

    def patch(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.select_related("created_by", "responsavel").filter(pk=pk).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        payload = request.data if isinstance(request.data, dict) else {}
        errors = validate_atividade_update_payload(payload)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            atividade = update_atividade(atividade, payload)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_atividade(atividade))

    def delete(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        try:
            delete_atividade(atividade)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuditoriaAtividadeRetornoExportView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request, pk: int):
        atividade = (
            AuditoriaAtividade.objects.filter(pk=pk)
            .prefetch_related("protocolos__etapas")
            .first()
        )
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            content, filename = export_atividade_retorno(atividade)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class AuditoriaAtividadeProtocoloListView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method == "POST":
            self.portal_permission = QUAL_AUDITORIA_CREATE
        super().initial(request, *args, **kwargs)

    def get(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        protocolos = (
            atividade.protocolos.select_related("tratado_referencia", "tratado_referencia__atividade")
            .prefetch_related("etapas")
            .order_by("excel_row", "protocolo")
        )
        return Response(
            {
                "atividade_id": atividade.id,
                "results": [serialize_atividade_protocolo(item) for item in protocolos],
            }
        )

    def post(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        payload = request.data if isinstance(request.data, dict) else {}
        errors = validate_protocolo_create_payload(payload, atividade=atividade)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        protocolo = create_protocolo(atividade, payload)
        return Response(serialize_atividade_protocolo(protocolo), status=status.HTTP_201_CREATED)


class AuditoriaAtividadeProtocoloDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method in ("DELETE", "PATCH"):
            self.portal_permission = QUAL_AUDITORIA_CREATE
        else:
            self.portal_permission = QUAL_AUDITORIA_VIEW
        super().initial(request, *args, **kwargs)

    def get(self, request, pk: int, protocolo_pk: int):
        protocolo = _get_protocolo(pk, protocolo_pk)
        if not protocolo:
            return Response(status=status.HTTP_404_NOT_FOUND)
        resolve_and_link(protocolo)
        return Response(serialize_atividade_protocolo(protocolo))

    def patch(self, request, pk: int, protocolo_pk: int):
        protocolo = _get_protocolo(pk, protocolo_pk)
        if not protocolo:
            return Response(status=status.HTTP_404_NOT_FOUND)

        blocked = _atividade_bloqueada_response(protocolo.atividade)
        if blocked:
            return blocked
        if protocolo.tratado_id:
            return Response(
                {"detail": "Protocolo já tratado; o histórico não pode ser alterado."},
                status=status.HTTP_409_CONFLICT,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        brflow_raw = payload.get("brflow_raw")
        if not isinstance(brflow_raw, str) or not brflow_raw.strip():
            return Response(
                {"errors": {"brflow_raw": "Cole os dados do Brflow."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        brflow_parsed = payload.get("brflow_parsed") if isinstance(payload.get("brflow_parsed"), dict) else None
        protocolo = update_protocolo_brflow(
            protocolo,
            brflow_raw=brflow_raw,
            brflow_parsed=brflow_parsed,
        )
        return Response(serialize_atividade_protocolo(protocolo))

    def delete(self, request, pk: int, protocolo_pk: int):
        protocolo = _get_protocolo(pk, protocolo_pk)
        if not protocolo:
            return Response(status=status.HTTP_404_NOT_FOUND)

        blocked = _atividade_bloqueada_response(protocolo.atividade)
        if blocked:
            return blocked
        if protocolo.tratado_id:
            return Response(
                {"detail": "Protocolo já tratado; o histórico não pode ser excluído."},
                status=status.HTTP_409_CONFLICT,
            )

        delete_protocolo(protocolo)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuditoriaAtividadeProtocoloAnaliseView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def patch(self, request, pk: int, protocolo_pk: int):
        protocolo = _get_protocolo(pk, protocolo_pk)
        if not protocolo:
            return Response(status=status.HTTP_404_NOT_FOUND)

        blocked = _atividade_bloqueada_response(protocolo.atividade)
        if blocked:
            return blocked
        if protocolo.tratado_id:
            return Response(
                {"detail": "Protocolo já tratado; o histórico não pode ser alterado."},
                status=status.HTTP_409_CONFLICT,
            )
        if protocolo.tratado_referencia_id:
            return Response(
                {"detail": "Protocolo duplicado; a análise não pode ser alterada."},
                status=status.HTTP_409_CONFLICT,
            )

        payload = request.data if isinstance(request.data, dict) else {}
        errors = validate_protocolo_analise_payload(payload)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        result = _apply_protocolo_analise(protocolo, payload, request.user)
        if result is not None:
            return Response(
                {
                    **serialize_falha_cadastro(result),
                    "promovido": True,
                    "protocolo_origem": (result.protocolo or "").strip(),
                }
            )
        protocolo.refresh_from_db()
        return Response(serialize_atividade_protocolo(protocolo))


class AuditoriaAtividadeFromBrflowView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        errors = validate_brflow_atividade_payload(payload)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            atividade = create_atividade_from_brflow(request.user, payload)
        except OpenAuditoriaAtividadeError as exc:
            open_atividade = exc.atividade
            return Response(
                {
                    "errors": {
                        "detail": (
                            "Finalize a auditoria em andamento antes de iniciar uma nova."
                        ),
                    },
                    "atividade_em_andamento": serialize_atividade(open_atividade),
                },
                status=status.HTTP_409_CONFLICT,
            )

        first_protocolo = atividade.protocolos.order_by("excel_row", "id").first()
        return Response(
            {
                **serialize_atividade(atividade),
                "falha_defaults": seed_falha_defaults_from_atividade(atividade),
                "protocolo_id": first_protocolo.id if first_protocolo else None,
            },
            status=status.HTTP_201_CREATED,
        )


class AuditoriaAtividadeFalhasView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def initial(self, request, *args, **kwargs):
        if request.method in ("POST", "DELETE"):
            self.portal_permission = QUAL_AUDITORIA_CREATE
        else:
            self.portal_permission = QUAL_AUDITORIA_VIEW
        super().initial(request, *args, **kwargs)

    def get(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk, tipo=AuditoriaAtividade.TIPO_AUDITORIA).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if atividade.status == AuditoriaAtividade.STATUS_CONCLUIDA:
            falhas = atividade.tratados.select_related("created_by", "atividade").order_by("-created_at")
            serializer = serialize_falha_cadastro
        else:
            falhas = atividade.falhas.select_related("created_by", "atividade").order_by("-created_at")
            serializer = serialize_falha_pendente_auditoria
        return Response(
            {
                "results": [serializer(item) for item in falhas],
                "falha_defaults": seed_falha_defaults_from_atividade(atividade),
            }
        )

    def post(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk, tipo=AuditoriaAtividade.TIPO_AUDITORIA).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        payload = request.data if isinstance(request.data, dict) else {}
        try:
            record = create_falha_for_atividade(request.user, atividade, payload)
        except ValueError as exc:
            errors = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"detail": str(exc)}
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            serialize_falha_pendente_auditoria(record),
            status=status.HTTP_201_CREATED,
        )


class AuditoriaAtividadeFalhasBatchView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.filter(
            pk=pk,
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
        ).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)
        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied
        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        payload = request.data if isinstance(request.data, dict) else {}
        rows = payload.get("falhas")
        if not isinstance(rows, list) or not rows or not all(isinstance(item, dict) for item in rows):
            return Response(
                {"errors": {"falhas": "Informe ao menos uma falha valida."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            atividade, saved = save_falhas_batch(
                request.user,
                atividade,
                rows,
                finalizar=bool(payload.get("finalizar")),
            )
        except (TypeError, ValueError) as exc:
            errors = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"detail": str(exc)}
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "atividade": serialize_atividade(atividade),
                "results": [serialize_falha_pendente_auditoria(item) for item in saved],
            }
        )


class AuditoriaAtividadeTrilhaBrflowView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def patch(self, request, pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk, tipo=AuditoriaAtividade.TIPO_AUDITORIA).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        payload = request.data if isinstance(request.data, dict) else {}
        trilha_raw = payload.get("trilha_raw")
        if not isinstance(trilha_raw, str) or not trilha_raw.strip():
            return Response(
                {"errors": {"trilha_raw": "Cole os dados da Trilha de Análise do Brflow."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        extracted = payload.get("extracted_users")
        extracted_users = (
            [str(item or "").strip() for item in extracted if str(item or "").strip()]
            if isinstance(extracted, list)
            else None
        )
        etapas_raw = payload.get("etapas_por_usuario")
        etapas_por_usuario: dict[str, str] | None = None
        if isinstance(etapas_raw, dict):
            etapas_por_usuario = {
                str(key or "").strip(): str(value or "").strip()
                for key, value in etapas_raw.items()
                if str(key or "").strip()
            }
        hits_raw = payload.get("trilha_hits")
        trilha_hits = None
        if isinstance(hits_raw, list):
            trilha_hits = [
                {
                    "usuario": str(item.get("usuario") or "").strip(),
                    "etapa_falha": str(item.get("etapa_falha") or "").strip(),
                    "tempo_analise": str(item.get("tempo_analise") or "").strip(),
                }
                for item in hits_raw
                if isinstance(item, dict) and str(item.get("usuario") or "").strip()
            ]
        try:
            atividade = update_atividade_trilha_brflow(
                atividade,
                trilha_raw=trilha_raw,
                extracted_users=extracted_users,
                user=request.user,
                etapas_por_usuario=etapas_por_usuario,
                trilha_hits=trilha_hits,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_atividade(atividade))


class AuditoriaAtividadeFinalizarView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def post(self, request, pk: int):
        started_at = perf_counter()
        atividade = AuditoriaAtividade.objects.filter(pk=pk, tipo=AuditoriaAtividade.TIPO_AUDITORIA).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        db_timer = _DbQueryTimer()
        try:
            with connection.execute_wrapper(db_timer):
                atividade = finalizar_atividade_auditoria(
                    atividade,
                    finalizador=request.user,
                )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        logger.info(
            "qualidade.finalizar atividade_id=%s elapsed_ms=%.1f "
            "db_queries=%s db_ms=%.1f tratados=%s",
            atividade.pk,
            (perf_counter() - started_at) * 1000,
            db_timer.count,
            db_timer.elapsed_ms,
            getattr(atividade, "_promovidos_count", 0),
        )
        return Response(serialize_atividade(atividade))


class AuditoriaAtividadeFalhaDetailView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_CREATE

    def patch(self, request, pk: int, falha_pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk, tipo=AuditoriaAtividade.TIPO_AUDITORIA).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        payload = request.data if isinstance(request.data, dict) else {}
        try:
            record = update_falha_for_atividade(atividade, falha_pk, payload)
        except ValueError as exc:
            errors = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"detail": str(exc)}
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        if not record:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_falha_pendente_auditoria(record))

    def delete(self, request, pk: int, falha_pk: int):
        atividade = AuditoriaAtividade.objects.filter(pk=pk, tipo=AuditoriaAtividade.TIPO_AUDITORIA).first()
        if not atividade:
            return Response(status=status.HTTP_404_NOT_FOUND)

        denied = _atividade_owner_response(request, atividade)
        if denied:
            return denied

        blocked = _atividade_bloqueada_response(atividade)
        if blocked:
            return blocked

        if not delete_falha_for_atividade(atividade, falha_pk):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
