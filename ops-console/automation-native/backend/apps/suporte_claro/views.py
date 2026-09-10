# -*- coding: utf-8 -*-

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status

from rest_framework.parsers import FormParser, MultiPartParser

from rest_framework.response import Response

from rest_framework.views import APIView

from config.api_exceptions import secure_error_payload



from apps.suporte_claro.models import (
    SuporteClaroAnexo,
    SuporteClaroComentarioEtapa,
    SuporteClaroHistorico,
    SuporteClaroRegistro,
)
from apps.suporte_claro.services.jira_auto import JiraAutoCreateError, create_and_link_jira_for_registro
from apps.suporte_claro.services.jira_rest import (
    delete_formalized_jira_comments,
    formalizar_texto_em_externos,
    parse_formalizar_externos_payload,
    sync_registro_to_jira,
)

from apps.suporte_claro.services.analytics import build_attention_points, build_report_stats, serialize_report_stats

from apps.suporte_claro.services.audit import log_registro_change
from apps.suporte_claro.services.chamados_externos import (
    parse_chamados_externos_payload,
    save_chamados_externos,
)
from apps.suporte_claro.services.protocolos import (
    format_protocolo_legacy,
    parse_protocolos_payload,
    save_protocolos,
)
from apps.suporte_claro.services.emails import parse_emails_payload, save_emails

from apps.suporte_claro.services.export_service import export_registros_xlsx
from apps.suporte_claro.services.import_service import (
    apply_import,
    preview_import,
    serialize_apply_result,
    serialize_preview,
)
from apps.suporte_claro.services.import_template import build_import_template_xlsx
from apps.suporte_claro.services.rachel_import import (
    RACHEL_USERNAME,
    apply_rachel_import,
    preview_rachel_import,
    serialize_rachel_apply,
    serialize_rachel_preview,
)

from apps.suporte_claro.services.filters import (
    apply_categoria_filter,
    apply_created_by_filter,
    apply_period_filters,
    apply_search_filter,
    apply_status_filter,
    apply_tipo_incidente_filter,
    build_cadastradores_meta,
    parse_list_filters,
    period_filename_suffix,
    period_label,
)

from apps.suporte_claro.services.pdf_registro import export_registro_pdf, registro_pdf_filename
from apps.suporte_claro.services.pdf_report import export_registros_pdf
from apps.suporte_claro.services.incidentes_analytics import (
    build_incidentes_mom_report,
    parse_month_param,
)
from apps.suporte_claro.services.pdf_incidentes_report import (
    export_incidentes_mom_pdf,
    incidentes_mom_pdf_filename,
)

from apps.suporte_claro.permissions import (
    CanCreateSuporteClaro,
    CanViewSuporteClaro,
    IsAuthenticatedPortal,
    SuporteClaroPermissionMixin,
)

from apps.suporte_claro.services.serialization import comentarios_etapa_enabled, serialize_registro

from apps.suporte_claro.services.validation import (

    MAX_ANEXOS,

    parse_categoria,

    parse_chamado_sistema,

    parse_origem,

    parse_received_at,

    parse_status,

    parse_tipo_incidente,

    validate_categoria_tipo,

    validate_chamado_externo,

    validate_image_upload,

)

from apps.suporte_claro.services.vinculos import parse_vinculos_ids, set_vinculos





def _registro_queryset(request):
    from django.db.models import Exists, OuterRef, Prefetch

    from apps.suporte_claro.models import SuporteClaroImportRef, SuporteClaroVinculoIncidente

    vinculo_qs = SuporteClaroVinculoIncidente.objects.select_related(
        "from_registro", "to_registro"
    )
    return (
        SuporteClaroRegistro.objects.select_related("created_by")
        .prefetch_related(
            "anexos",
            "chamados_externos",
            "protocolos",
            "emails",
            Prefetch("vinculos_from", queryset=vinculo_qs),
            Prefetch("vinculos_to", queryset=vinculo_qs),
        )
        .annotate(
            _is_imported=Exists(
                SuporteClaroImportRef.objects.filter(registro_id=OuterRef("pk"))
            )
        )
    )


def _filtered_queryset(request):
    list_filters, err = parse_list_filters(request.query_params)
    if err:
        return None, None, None, None, err

    base_qs = _registro_queryset(request)
    period_qs = apply_period_filters(base_qs, list_filters.period)
    cadastradores = build_cadastradores_meta(period_qs)
    period_incidentes = period_qs.filter(
        categoria=SuporteClaroRegistro.CATEGORIA_INCIDENTE
    ).count()

    qs = apply_created_by_filter(period_qs, list_filters.created_by, request.user)
    qs = apply_status_filter(qs, list_filters.status)
    qs = apply_categoria_filter(qs, list_filters.categoria)
    qs = apply_tipo_incidente_filter(qs, list_filters.tipo_incidente)
    qs = apply_search_filter(qs, list_filters.q)

    return qs, list_filters, cadastradores, period_incidentes, None


def _parse_chamado_payload(data) -> tuple[str, str, str, str | None]:
    raw_sistema = str(
        data.get("chamado_sistema") or data.get("chamadoSistema") or ""
    ).strip()
    if raw_sistema:
        sistema = parse_chamado_sistema(raw_sistema)
        if sistema is None:
            return "", "", "", "Sistema de chamado invalido."
    else:
        sistema = ""
    codigo = str(data.get("chamado_codigo") or data.get("chamadoCodigo") or "").strip()
    url = str(data.get("chamado_url") or data.get("chamadoUrl") or "").strip()
    err = validate_chamado_externo(sistema, codigo, url)
    if err:
        return "", "", "", err
    return sistema, codigo, url, None





class RegistroListCreateView(SuporteClaroPermissionMixin, APIView):

    parser_classes = [MultiPartParser, FormParser]



    def get(self, request):

        qs, list_filters, cadastradores, period_incidentes, err = _filtered_queryset(request)

        if err:

            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        period = list_filters.period

        items = [serialize_registro(r, request) for r in qs]

        stats = build_report_stats(qs)
        stats_payload = serialize_report_stats(stats)
        stats_payload["incidentes"] = period_incidentes

        return Response(

            {

                "items": items,

                "total": len(items),

                "meta": {

                    "date_from": period.date_from.isoformat() if period.date_from else None,

                    "date_to": period.date_to.isoformat() if period.date_to else None,

                    "period_label": period_label(period),

                    "created_by": list_filters.created_by,

                    "status": list_filters.status,

                    "categoria": list_filters.categoria,

                    "tipo_incidente": list_filters.tipo_incidente,

                    "cadastradores": cadastradores,

                    "stats": stats_payload,

                    "attention_points": build_attention_points(stats),

                    "comentarios_etapa_enabled": comentarios_etapa_enabled(),

                },

            }

        )



    def post(self, request):

        titulo = (request.data.get("titulo") or "").strip()
        if not titulo:
            return Response({"detail": "Titulo obrigatorio."}, status=status.HTTP_400_BAD_REQUEST)

        categoria = parse_categoria(str(request.data.get("categoria") or ""))
        if categoria is None:
            return Response(
                {"detail": "Categoria inválida. Use demanda ou incidente."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tipo_incidente = parse_tipo_incidente(str(request.data.get("tipo_incidente") or ""))
        if tipo_incidente is None:
            return Response(
                {"detail": "tipo_incidente inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cat_err = validate_categoria_tipo(categoria, tipo_incidente)
        if cat_err:
            return Response({"detail": cat_err}, status=status.HTTP_400_BAD_REQUEST)
        if categoria != SuporteClaroRegistro.CATEGORIA_INCIDENTE:
            tipo_incidente = ""

        allow_empty_protocolo = True
        if categoria == SuporteClaroRegistro.CATEGORIA_INCIDENTE:
            # Protocolo do incidente é sempre INC-{id}; ignora payload do client.
            protocolo_items = []
            protocolo = ""
        else:
            protocolo_items, protocolo_err = parse_protocolos_payload(
                request.data, allow_empty=allow_empty_protocolo
            )
            if protocolo_err:
                return Response({"detail": protocolo_err}, status=status.HTTP_400_BAD_REQUEST)

            protocolo = format_protocolo_legacy(protocolo_items)

        email_items, email_err = parse_emails_payload(request.data, allow_empty=True)
        if email_err:
            return Response({"detail": email_err}, status=status.HTTP_400_BAD_REQUEST)
        has_email_payload = (
            "emails" in request.data
            or "sentBy" in request.data
            or "sent_by" in request.data
        )
        sent_by_initial = email_items[0][0][:255] if email_items else ""

        received_raw = request.data.get("received_at") or request.data.get("receivedAt") or ""

        received_at = parse_received_at(str(received_raw))

        if received_at is None:

            return Response(

                {"detail": "Hora de recebimento inválida ou ausente."},

                status=status.HTTP_400_BAD_REQUEST,

            )

        retorno_raw = request.data.get("retorno_at") or request.data.get("retornoAt") or ""
        retorno_at = parse_received_at(str(retorno_raw)) if retorno_raw else None
        if retorno_raw and retorno_at is None:
            return Response(
                {"detail": "Data e hora do retorno ao cliente inválida."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if retorno_at and retorno_at < received_at:
            return Response(
                {"detail": "O retorno ao cliente não pode ser anterior ao recebimento da atividade."},
                status=status.HTTP_400_BAD_REQUEST,
            )



        uploads = request.FILES.getlist("images") or request.FILES.getlist("anexos")

        if len(uploads) > MAX_ANEXOS:

            return Response(

                {"detail": f"Máximo de {MAX_ANEXOS} anexos por cadastro."},

                status=status.HTTP_400_BAD_REQUEST,

            )



        for uploaded in uploads:

            error = validate_image_upload(uploaded)

            if error:

                return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)



        avaliacao = (request.data.get("avaliacao") or "").strip()
        raw_status = str(request.data.get("status") or "").strip()
        if raw_status:
            reg_status = parse_status(raw_status)
            if reg_status is None:
                return Response(
                    {"detail": "Status invalido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            reg_status = SuporteClaroRegistro.STATUS_ABERTO

        if reg_status == SuporteClaroRegistro.STATUS_CONCLUIDO and not avaliacao:
            return Response(
                {
                    "detail": (
                        "Descreva o retorno do suporte para marcar como concluida."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        origem = parse_origem(str(request.data.get("origem") or ""))

        if origem is None:

            return Response(

                {"detail": "Canal de origem obrigatório (Teams, E-mail ou Ligação)."},

                status=status.HTTP_400_BAD_REQUEST,

            )

        vinculos_ids, vinculos_err = parse_vinculos_ids(request.data)
        if vinculos_err:
            return Response({"detail": vinculos_err}, status=status.HTTP_400_BAD_REQUEST)
        chamado_items, chamado_err = parse_chamados_externos_payload(request.data)
        if chamado_err:
            return Response({"detail": chamado_err}, status=status.HTTP_400_BAD_REQUEST)

        jira_auto = None

        try:
            with transaction.atomic():
                registro = SuporteClaroRegistro.objects.create(
                    titulo=titulo,
                    protocolo=protocolo or "",
                    irregularidade=(request.data.get("irregularidade") or "").strip(),
                    avaliacao=avaliacao,
                    received_at=received_at,
                    retorno_at=retorno_at or (timezone.now() if avaliacao else None),
                    sent_by=sent_by_initial,
                    origem=origem,
                    categoria=categoria,
                    tipo_incidente=tipo_incidente,
                    status=reg_status,
                    created_by=request.user,
                )

                if protocolo_items:
                    save_protocolos(registro, protocolo_items, user=request.user)
                elif categoria == SuporteClaroRegistro.CATEGORIA_INCIDENTE:
                    auto_code = f"INC-{registro.id}"
                    registro.protocolo = auto_code
                    registro.save(update_fields=["protocolo", "updated_at"])
                    save_protocolos(registro, [(auto_code, "")], user=request.user)

                if has_email_payload:
                    save_emails(registro, email_items, user=request.user)

                if chamado_items:
                    save_chamados_externos(registro, chamado_items, user=request.user)
                else:
                    registro.refresh_from_db()

                for uploaded in uploads:
                    SuporteClaroAnexo.objects.create(
                        registro=registro,
                        file=uploaded,
                        original_name=uploaded.name or "",
                        content_type=(uploaded.content_type or "")[:128],
                        size_bytes=int(uploaded.size or 0),
                    )

                if vinculos_ids is not None:
                    vinculo_err = set_vinculos(registro, vinculos_ids, user=request.user)
                    if vinculo_err:
                        raise ValueError(vinculo_err)

                # Incidentes ficam só no portal (sem formalização Jira automática).
                if categoria != SuporteClaroRegistro.CATEGORIA_INCIDENTE:
                    jira_auto = create_and_link_jira_for_registro(registro, request.user)
                    registro.refresh_from_db()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except JiraAutoCreateError as exc:
            return Response(
                {"detail": exc.message},
                status=exc.status_code if exc.status_code in (400, 502) else status.HTTP_502_BAD_GATEWAY,
            )

        issue_key = (jira_auto or {}).get("issue_key") if jira_auto else None
        if categoria == SuporteClaroRegistro.CATEGORIA_INCIDENTE:
            message = "Incidente registrado com sucesso (somente portal)."
        else:
            message = "Suporte N1 registrado com sucesso."
        if issue_key:
            message = f"Suporte N1 registrado e formalizado no Jira ({issue_key})."

        payload = {
            "message": message,
            "registro": serialize_registro(registro, request, detailed=True),
        }
        if jira_auto is not None:
            payload["jira_auto"] = {
                "ok": bool(jira_auto.get("ok")),
                "issue_key": issue_key,
                "skipped": bool(jira_auto.get("skipped")),
                "attachments": jira_auto.get("attachments"),
            }
            if jira_auto.get("jira_sync") is not None:
                payload["jira_sync"] = jira_auto["jira_sync"]

        return Response(payload, status=status.HTTP_201_CREATED)





class RegistroExportView(APIView):

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]



    def get(self, request):

        qs, list_filters, _cadastradores, _period_incidentes, err = _filtered_queryset(request)

        if err:

            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        period = list_filters.period

        suffix = period_filename_suffix(period)

        content = export_registros_xlsx(qs)

        response = HttpResponse(

            content,

            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

        )

        response["Content-Disposition"] = f'attachment; filename="suporte_claro_{suffix}.xlsx"'

        return response





class RegistroReportPdfView(APIView):

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]



    def get(self, request):

        qs, list_filters, _cadastradores, _period_incidentes, err = _filtered_queryset(request)

        if err:

            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        period = list_filters.period

        stats = build_report_stats(qs)

        content = export_registros_pdf(

            qs,

            stats,

            period_text=period_label(period),

            generated_by=request.user.get_full_name() or request.user.username,

        )

        suffix = period_filename_suffix(period)

        response = HttpResponse(content, content_type="application/pdf")

        response["Content-Disposition"] = f'attachment; filename="suporte_claro_relatorio_{suffix}.pdf"'

        return response


class IncidentesMomReportPdfView(APIView):
    """PDF mensal de incidentes: mês vs mês anterior, agrupado por motivo."""

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def get(self, request):
        list_filters, err = parse_list_filters(request.query_params)
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        month_raw = (request.query_params.get("month") or "").strip()
        if not month_raw:
            date_to = getattr(list_filters.period, "date_to", None)
            if date_to:
                month_raw = date_to.strftime("%Y-%m")

        ticket_scope = (request.query_params.get("ticket_scope") or "todos").strip()
        if ticket_scope not in {"todos", "com_servicenow", "sem_servicenow"}:
            return Response(
                {"detail": "Filtro de ServiceNow inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            history_months = int(request.query_params.get("history_months") or 6)
        except (TypeError, ValueError):
            history_months = 0
        if history_months not in {3, 6, 12}:
            return Response(
                {"detail": "Janela histórica inválida. Use 3, 6 ou 12 meses."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        filters_applied = ["Todos os registros da categoria Incidente"]
        status_labels = {
            "pendentes": "Status: pendentes",
            "concluido": "Status: concluídos",
        }
        if list_filters.status:
            filters_applied.append(status_labels.get(list_filters.status, list_filters.status))
        if list_filters.tipo_incidente:
            tipo_labels = dict(SuporteClaroRegistro.TIPO_INCIDENTE_CHOICES)
            filters_applied.append(
                f"Tipo: {tipo_labels.get(list_filters.tipo_incidente, list_filters.tipo_incidente)}"
            )
        if list_filters.created_by:
            filters_applied.append(
                "Cadastrado por: usuário atual"
                if list_filters.created_by == "me"
                else f"Cadastrado por: {list_filters.created_by}"
            )
        if list_filters.q:
            filters_applied.append(f"Busca: {list_filters.q}")
        if ticket_scope == "com_servicenow":
            filters_applied.append("Somente com chamado ServiceNow")
        elif ticket_scope == "sem_servicenow":
            filters_applied.append("Somente sem chamado ServiceNow")
        filters_applied.append(f"Histórico: {history_months} meses")

        ref = parse_month_param(month_raw)
        report = build_incidentes_mom_report(
            ref_month=ref,
            created_by=request.user if list_filters.created_by == "me" else None,
            created_by_username=(
                list_filters.created_by
                if list_filters.created_by and list_filters.created_by != "me"
                else None
            ),
            status_filter=list_filters.status,
            tipo_incidente=list_filters.tipo_incidente,
            search=list_filters.q,
            ticket_scope=ticket_scope,
            filters_applied=filters_applied,
            history_months=history_months,
        )
        content = export_incidentes_mom_pdf(
            report,
            generated_by=request.user.get_full_name() or request.user.username,
        )
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{incidentes_mom_pdf_filename(report)}"'
        )
        return response


class RegistroFichaPdfView(APIView):

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def get(self, request, registro_id: int):

        registro = (

            _registro_queryset(request)

            .filter(pk=registro_id)

            .prefetch_related("anexos")

            .first()

        )

        if not registro:

            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        content = export_registro_pdf(

            registro,

            generated_by=request.user.get_full_name() or request.user.username,

        )

        response = HttpResponse(content, content_type="application/pdf")

        response["Content-Disposition"] = f'attachment; filename="{registro_pdf_filename(registro)}"'

        return response





class RegistroDetailView(APIView):

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]



    def get_queryset(self, request):

        return _registro_queryset(request)



    def get_object(self, request, registro_id: int):

        return self.get_queryset(request).filter(pk=registro_id).first()



    def get(self, request, registro_id: int):

        registro = self.get_object(request, registro_id)

        if not registro:

            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        return Response(serialize_registro(registro, request, detailed=True))



    def patch(self, request, registro_id: int):

        registro = self.get_object(request, registro_id)

        if not registro:

            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        has_avaliacao = "avaliacao" in request.data
        has_status = "status" in request.data
        if has_avaliacao and not has_status:
            return Response(
                {
                    "detail": (
                        "Informe o status junto com a avaliação/retorno "
                        "(o status é sincronizado com o Jira)."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        editable_str = ("titulo", "irregularidade", "avaliacao")
        for field in editable_str:
            if field not in request.data:
                continue
            new_val = (request.data.get(field) or "").strip()
            old_val = getattr(registro, field) or ""
            if field == "titulo" and not new_val:
                return Response({"detail": "Titulo obrigatorio."}, status=status.HTTP_400_BAD_REQUEST)
            log_registro_change(
                registro=registro,
                user=request.user,
                action=SuporteClaroHistorico.ACTION_EDIT,
                field_name=field,
                old_value=old_val,
                new_value=new_val,
            )
            setattr(registro, field, new_val)

        if "protocolos" in request.data or "protocolo" in request.data:
            target_categoria = registro.categoria
            if "categoria" in request.data:
                parsed_cat = parse_categoria(str(request.data.get("categoria") or ""))
                if parsed_cat is not None:
                    target_categoria = parsed_cat
            if target_categoria == SuporteClaroRegistro.CATEGORIA_INCIDENTE:
                # Mantém/gera INC-{id}; ignora protocolos enviados pelo client.
                auto_code = f"INC-{registro.id}"
                save_protocolos(registro, [(auto_code, "")], user=request.user)
            else:
                protocolo_items, protocolo_err = parse_protocolos_payload(
                    request.data, allow_empty=True
                )
                if protocolo_err:
                    return Response({"detail": protocolo_err}, status=status.HTTP_400_BAD_REQUEST)
                save_protocolos(registro, protocolo_items, user=request.user)

        if "emails" in request.data or "sent_by" in request.data or "sentBy" in request.data:
            email_items, email_err = parse_emails_payload(request.data, allow_empty=True)
            if email_err:
                return Response({"detail": email_err}, status=status.HTTP_400_BAD_REQUEST)
            save_emails(registro, email_items, user=request.user)

        if "origem" in request.data:

            origem = parse_origem(str(request.data.get("origem") or ""))

            if origem is None:

                return Response({"detail": "Canal invalido."}, status=status.HTTP_400_BAD_REQUEST)

            log_registro_change(

                registro=registro,

                user=request.user,

                action=SuporteClaroHistorico.ACTION_EDIT,

                field_name="origem",

                old_value=registro.origem or "",

                new_value=origem,

            )

            registro.origem = origem



        if "categoria" in request.data or "tipo_incidente" in request.data:
            new_categoria = registro.categoria
            if "categoria" in request.data:
                parsed_cat = parse_categoria(str(request.data.get("categoria") or ""))
                if parsed_cat is None:
                    return Response(
                        {"detail": "Categoria inválida. Use demanda ou incidente."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                new_categoria = parsed_cat
            new_tipo = registro.tipo_incidente or ""
            if "tipo_incidente" in request.data:
                parsed_tipo = parse_tipo_incidente(str(request.data.get("tipo_incidente") or ""))
                if parsed_tipo is None:
                    return Response(
                        {"detail": "tipo_incidente inválido."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                new_tipo = parsed_tipo
            if new_categoria != SuporteClaroRegistro.CATEGORIA_INCIDENTE:
                new_tipo = ""
            cat_err = validate_categoria_tipo(new_categoria, new_tipo)
            if cat_err:
                return Response({"detail": cat_err}, status=status.HTTP_400_BAD_REQUEST)
            if new_categoria != registro.categoria:
                log_registro_change(
                    registro=registro,
                    user=request.user,
                    action=SuporteClaroHistorico.ACTION_EDIT,
                    field_name="categoria",
                    old_value=registro.categoria or "",
                    new_value=new_categoria,
                )
                registro.categoria = new_categoria
            if new_tipo != (registro.tipo_incidente or ""):
                log_registro_change(
                    registro=registro,
                    user=request.user,
                    action=SuporteClaroHistorico.ACTION_EDIT,
                    field_name="tipo_incidente",
                    old_value=registro.tipo_incidente or "",
                    new_value=new_tipo,
                )
                registro.tipo_incidente = new_tipo

        vinculos_ids, vinculos_err = parse_vinculos_ids(request.data)
        if vinculos_err:
            return Response({"detail": vinculos_err}, status=status.HTTP_400_BAD_REQUEST)

        status_changed = False
        if "status" in request.data:

            new_status = parse_status(str(request.data.get("status") or ""))

            if new_status is None:

                return Response({"detail": "Status invalido."}, status=status.HTTP_400_BAD_REQUEST)

            if registro.status != new_status:
                status_changed = True
                log_registro_change(

                    registro=registro,

                    user=request.user,

                    action=SuporteClaroHistorico.ACTION_EDIT,

                    field_name="status",

                    old_value=registro.status,

                    new_value=new_status,

                )

                registro.status = new_status



        if "received_at" in request.data or "receivedAt" in request.data:

            raw = request.data.get("received_at") or request.data.get("receivedAt") or ""

            received_at = parse_received_at(str(raw))

            if received_at is None:

                return Response({"detail": "Data de recebimento invalida."}, status=status.HTTP_400_BAD_REQUEST)

            old_iso = registro.received_at.isoformat() if registro.received_at else ""

            log_registro_change(

                registro=registro,

                user=request.user,

                action=SuporteClaroHistorico.ACTION_EDIT,

                field_name="received_at",

                old_value=old_iso,

                new_value=received_at.isoformat(),

            )

            registro.received_at = received_at

        if "retorno_at" in request.data or "retornoAt" in request.data:
            raw = request.data.get("retorno_at")
            if raw is None and "retornoAt" in request.data:
                raw = request.data.get("retornoAt")
            if raw in (None, ""):
                if registro.retorno_at is not None:
                    log_registro_change(
                        registro=registro,
                        user=request.user,
                        action=SuporteClaroHistorico.ACTION_EDIT,
                        field_name="retorno_at",
                        old_value=registro.retorno_at.isoformat(),
                        new_value="",
                    )
                    registro.retorno_at = None
            else:
                retorno_at = parse_received_at(str(raw))
                if retorno_at is None:
                    return Response(
                        {"detail": "Data de conclusão inválida."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if registro.received_at and retorno_at < registro.received_at:
                    return Response(
                        {"detail": "A conclusão não pode ser anterior ao recebimento."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                old_iso = registro.retorno_at.isoformat() if registro.retorno_at else ""
                log_registro_change(
                    registro=registro,
                    user=request.user,
                    action=SuporteClaroHistorico.ACTION_EDIT,
                    field_name="retorno_at",
                    old_value=old_iso,
                    new_value=retorno_at.isoformat(),
                )
                registro.retorno_at = retorno_at

        chamado_keys = (
            "chamado_sistema",
            "chamadoSistema",
            "chamado_codigo",
            "chamadoCodigo",
            "chamado_url",
            "chamadoUrl",
            "chamados_externos",
            "chamadosExternos",
        )
        if any(key in request.data for key in chamado_keys):
            chamado_items, chamado_err = parse_chamados_externos_payload(request.data)
            if chamado_err:
                return Response({"detail": chamado_err}, status=status.HTTP_400_BAD_REQUEST)
            save_chamados_externos(registro, chamado_items, user=request.user)

        if (
            registro.status == SuporteClaroRegistro.STATUS_CONCLUIDO
            and not (registro.avaliacao or "").strip()
        ):
            return Response(
                {
                    "detail": (
                        "Descreva o retorno do suporte para marcar como concluida."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Primeiro retorno: se veio avaliação e ainda não há conclusão explícita, marca agora.
        if (
            (registro.avaliacao or "").strip()
            and registro.retorno_at is None
            and "retorno_at" not in request.data
            and "retornoAt" not in request.data
        ):
            registro.retorno_at = timezone.now()

        registro.save()

        if vinculos_ids is not None:
            vinculo_err = set_vinculos(registro, vinculos_ids, user=request.user)
            if vinculo_err:
                return Response({"detail": vinculo_err}, status=status.HTTP_400_BAD_REQUEST)
            log_registro_change(
                registro=registro,
                user=request.user,
                action=SuporteClaroHistorico.ACTION_EDIT,
                field_name="vinculos",
                old_value="",
                new_value=",".join(str(i) for i in vinculos_ids),
            )

        formalizar, formalizar_err = parse_formalizar_externos_payload(request.data)
        if formalizar_err:
            return Response({"detail": formalizar_err}, status=status.HTTP_400_BAD_REQUEST)

        jira_sync = None
        if status_changed or (has_avaliacao and has_status):
            jira_sync = sync_registro_to_jira(
                registro,
                registro.status,
                request.user,
            )

        formalizacao_externos = None
        if formalizar:
            texto = (formalizar.get("texto") or "").strip()
            if not texto:
                texto = (registro.avaliacao or "").strip()
            if not texto:
                return Response(
                    {"detail": "Informe o texto para formalizar nos chamados externos."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            formalizacao_externos = formalizar_texto_em_externos(
                registro,
                request.user,
                chamado_ids=formalizar.get("chamado_ids") or [],
                texto=texto,
            )
            if formalizacao_externos.get("error") and not formalizacao_externos.get("items"):
                return Response(
                    {"detail": formalizacao_externos["error"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        payload = {
            "message": "Suporte N1 atualizado.",
            "registro": serialize_registro(registro, request, detailed=True),
        }
        if jira_sync is not None:
            payload["jira_sync"] = jira_sync
        if formalizacao_externos is not None:
            payload["formalizacao_externos"] = formalizacao_externos

        return Response(payload)






class RegistroAnexosView(APIView):
    """Anexa arquivos a uma demanda já existente (edição)."""

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, registro_id: int):
        registro = _registro_queryset(request).filter(pk=registro_id).first()
        if not registro:
            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        uploads = request.FILES.getlist("images") or request.FILES.getlist("anexos")
        if not uploads:
            return Response({"detail": "Envie ao menos um anexo."}, status=status.HTTP_400_BAD_REQUEST)

        current_count = registro.anexos.count()
        if current_count + len(uploads) > MAX_ANEXOS:
            remaining = max(0, MAX_ANEXOS - current_count)
            return Response(
                {
                    "detail": (
                        f"Máximo de {MAX_ANEXOS} anexos por demanda. "
                        f"Restam {remaining} vaga(s)."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        for uploaded in uploads:
            error = validate_image_upload(uploaded)
            if error:
                return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        created = []
        for uploaded in uploads:
            anexo = SuporteClaroAnexo.objects.create(
                registro=registro,
                file=uploaded,
                original_name=uploaded.name or "",
                content_type=(uploaded.content_type or "")[:128],
                size_bytes=int(uploaded.size or 0),
            )
            created.append(anexo)

        names = ", ".join((a.original_name or f"anexo-{a.pk}") for a in created)
        log_registro_change(
            registro=registro,
            user=request.user,
            action=SuporteClaroHistorico.ACTION_EDIT,
            field_name="anexos",
            old_value=str(current_count),
            new_value=f"{current_count + len(created)} ({names})",
        )

        # Anexos ficam só no portal — não espelhar automaticamente no Jira (interno/externo).
        message = f"{len(created)} anexo(s) adicionado(s)."

        registro = _registro_queryset(request).filter(pk=registro_id).first()
        payload = {
            "message": message,
            "registro": serialize_registro(registro, request, detailed=True),
        }

        return Response(payload, status=status.HTTP_201_CREATED)


class RegistroStatusView(APIView):

    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]



    def patch(self, request, registro_id: int):

        registro = _registro_queryset(request).filter(pk=registro_id).first()

        if not registro:

            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)



        new_status = (request.data.get("status") or "").strip()

        valid = {choice[0] for choice in SuporteClaroRegistro.STATUS_CHOICES}

        if new_status not in valid:

            return Response(

                {"detail": f"Status inválido. Use: {', '.join(sorted(valid))}."},

                status=status.HTTP_400_BAD_REQUEST,

            )



        if registro.status == new_status:

            return Response({

                "message": "Status já está atualizado.",

                "registro": serialize_registro(registro, request, detailed=True),

            })

        if (
            new_status == SuporteClaroRegistro.STATUS_CONCLUIDO
            and not (registro.avaliacao or "").strip()
        ):
            return Response(
                {
                    "detail": (
                        "Descreva o retorno do suporte para marcar como concluida."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        log_registro_change(

            registro=registro,

            user=request.user,

            action=SuporteClaroHistorico.ACTION_STATUS,

            field_name="status",

            old_value=registro.status,

            new_value=new_status,

        )

        registro.status = new_status

        registro.save(update_fields=["status", "updated_at"])

        formalizar, formalizar_err = parse_formalizar_externos_payload(request.data)
        if formalizar_err:
            return Response({"detail": formalizar_err}, status=status.HTTP_400_BAD_REQUEST)

        formalizacao_externos = None
        if formalizar:
            texto = (formalizar.get("texto") or "").strip()
            if not texto:
                return Response(
                    {"detail": "Informe o texto para formalizar nos chamados externos."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            formalizacao_externos = formalizar_texto_em_externos(
                registro,
                request.user,
                chamado_ids=formalizar.get("chamado_ids") or [],
                texto=texto,
            )
            if formalizacao_externos.get("error") and not formalizacao_externos.get("items"):
                return Response(
                    {"detail": formalizacao_externos["error"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        jira_sync = sync_registro_to_jira(registro, new_status, request.user)

        payload = {
            "message": "Status atualizado.",
            "registro": serialize_registro(registro, request, detailed=True),
            "jira_sync": jira_sync,
        }
        if formalizacao_externos is not None:
            payload["formalizacao_externos"] = formalizacao_externos
        return Response(payload)


class RegistroImportTemplateView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]

    def get(self, request):
        content = build_import_template_xlsx()
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="suporte_claro_import_template.xlsx"'
        return response


class RegistroImportPreviewView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "Envie o arquivo .xlsx no campo file."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            preview = preview_import(upload.read())
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                secure_error_payload(exc, operation="suporte_claro.import_preview"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(serialize_preview(preview))


class RegistroImportApplyView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanViewSuporteClaro]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "Envie o arquivo .xlsx no campo file."}, status=status.HTTP_400_BAD_REQUEST)
        file_bytes = upload.read()
        try:
            preview = preview_import(file_bytes)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                secure_error_payload(exc, operation="suporte_claro.import_apply_preview"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        if preview.valid <= 0:
            return Response(
                {
                    "detail": "Nenhuma linha válida para importar.",
                    "preview": serialize_preview(preview),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = apply_import(file_bytes, request.user)
        except Exception as exc:
            return Response(
                secure_error_payload(exc, operation="suporte_claro.import_apply"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                "message": f"{result.created} demanda(s) importada(s).",
                **serialize_apply_result(result),
            },
            status=status.HTTP_201_CREATED,
        )


class RegistroRachelImportPreviewView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanCreateSuporteClaro]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response(
                {"detail": "Selecione a planilha DEMANDAS FY27 (.xlsx)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not upload.name.lower().endswith(".xlsx"):
            return Response({"detail": "O arquivo precisa estar no formato .xlsx."}, status=400)
        if upload.size > 15 * 1024 * 1024:
            return Response({"detail": "A planilha excede o limite de 15 MB."}, status=400)
        try:
            result = preview_rachel_import(upload.read())
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_rachel_preview(result))


class RegistroRachelImportApplyView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanCreateSuporteClaro]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response(
                {"detail": "Selecione a planilha DEMANDAS FY27 (.xlsx)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not upload.name.lower().endswith(".xlsx"):
            return Response({"detail": "O arquivo precisa estar no formato .xlsx."}, status=400)
        if upload.size > 15 * 1024 * 1024:
            return Response({"detail": "A planilha excede o limite de 15 MB."}, status=400)

        record_owner = get_user_model().objects.filter(username__iexact=RACHEL_USERNAME).first()
        if record_owner is None:
            return Response(
                {
                    "detail": (
                        f"A usuária Rachel ({RACHEL_USERNAME}) não foi encontrada. "
                        "Cadastre/sincronize a usuária antes da importação."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = apply_rachel_import(
                upload.read(), imported_by=request.user, record_owner=record_owner
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if result.valid <= 0:
            return Response(
                {
                    "detail": "Nenhum incidente novo para importar.",
                    "preview": serialize_rachel_preview(result),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "message": (
                    f"{result.created} incidente(s) da Rachel importado(s); "
                    f"{result.skipped} já existente(s)."
                ),
                **serialize_rachel_apply(result),
            },
            status=status.HTTP_201_CREATED,
        )


def _comentarios_etapa_unavailable():
    return Response(
        {"detail": "Comentários por etapa não estão habilitados."},
        status=status.HTTP_404_NOT_FOUND,
    )


class RegistroComentariosEtapaView(SuporteClaroPermissionMixin, APIView):
    """GET/POST comentários passo a passo (portal-only; feature flag)."""

    def get(self, request, registro_id: int):
        if not comentarios_etapa_enabled():
            return _comentarios_etapa_unavailable()
        registro = _registro_queryset(request).filter(pk=registro_id).first()
        if not registro:
            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        from apps.suporte_claro.services.serialization import serialize_comentario_etapa
        from apps.access.resolve import user_has_permission
        from apps.access import registry as R

        can_change = user_has_permission(request.user, R.PROCESSOS_SUPORTE_CLARO_CHANGE_STATUS)
        items = []
        for c in registro.comentarios_etapa.select_related("created_by").all():
            payload = serialize_comentario_etapa(c)
            payload["can_delete"] = bool(
                can_change
                or (c.created_by_id and c.created_by_id == getattr(request.user, "id", None))
            )
            items.append(payload)
        return Response({"items": items, "total": len(items)})

    def post(self, request, registro_id: int):
        if not comentarios_etapa_enabled():
            return _comentarios_etapa_unavailable()
        registro = _registro_queryset(request).filter(pk=registro_id).first()
        if not registro:
            return Response({"detail": "Registro não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        texto = (request.data.get("texto") or "").strip()
        if not texto:
            return Response({"detail": "Informe o texto do comentário."}, status=status.HTTP_400_BAD_REQUEST)

        visibilidade_raw = str(
            request.data.get("visibilidade") or SuporteClaroComentarioEtapa.VISIBILIDADE_INTERNO
        ).strip().lower()
        if visibilidade_raw not in {
            SuporteClaroComentarioEtapa.VISIBILIDADE_INTERNO,
            SuporteClaroComentarioEtapa.VISIBILIDADE_EXTERNO,
        }:
            return Response(
                {"detail": "Visibilidade inválida. Use interno ou externo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        formalizar, formalizar_err = parse_formalizar_externos_payload(request.data)
        if formalizar_err:
            return Response({"detail": formalizar_err}, status=status.HTTP_400_BAD_REQUEST)

        # Formalização só com intenção explícita de comentário externo + seleção de chamados.
        if visibilidade_raw == SuporteClaroComentarioEtapa.VISIBILIDADE_INTERNO:
            formalizar = None
        elif not formalizar:
            return Response(
                {
                    "detail": (
                        "Para comentário externo, selecione ao menos um chamado Jira "
                        "para formalizar o texto."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.suporte_claro.services.serialization import serialize_comentario_etapa

        comentario = SuporteClaroComentarioEtapa.objects.create(
            registro=registro,
            texto=texto,
            etapa_status=registro.status,
            visibilidade=visibilidade_raw,
            created_by=request.user,
        )

        response_body = {
            "message": "Comentário registrado.",
            "comentario": None,
        }

        if formalizar:
            formalizacao = formalizar_texto_em_externos(
                registro,
                request.user,
                chamado_ids=formalizar.get("chamado_ids") or [],
                texto=(formalizar.get("texto") or texto).strip() or texto,
            )
            if formalizacao.get("error") and not formalizacao.get("items"):
                comentario.delete()
                return Response(
                    {"detail": formalizacao["error"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ok_keys = [
                str(i.get("issue_key") or "").strip().upper()
                for i in (formalizacao.get("items") or [])
                if i.get("ok") and i.get("issue_key")
            ]
            jira_refs = [
                {
                    "issue_key": str(i.get("issue_key") or "").strip().upper(),
                    "comment_id": str(i.get("comment_id") or "").strip(),
                }
                for i in (formalizacao.get("items") or [])
                if i.get("ok") and i.get("issue_key") and i.get("comment_id")
            ]
            if ok_keys:
                comentario.formalizado_externo = True
                comentario.visibilidade = SuporteClaroComentarioEtapa.VISIBILIDADE_EXTERNO
                comentario.formalizado_issue_keys = ",".join(ok_keys)
                comentario.formalizado_jira_refs = jira_refs
                comentario.save(
                    update_fields=[
                        "formalizado_externo",
                        "visibilidade",
                        "formalizado_issue_keys",
                        "formalizado_jira_refs",
                    ]
                )
            response_body["formalizacao_externos"] = formalizacao

        payload_comentario = serialize_comentario_etapa(comentario)
        payload_comentario["can_delete"] = True
        response_body["comentario"] = payload_comentario

        return Response(response_body, status=status.HTTP_201_CREATED)


class RegistroComentarioEtapaDetailView(APIView):
    """DELETE comentário por etapa (autor ou quem tem change_status)."""

    def get_permissions(self):
        return [IsAuthenticatedPortal(), CanViewSuporteClaro()]

    def delete(self, request, registro_id: int, comentario_id: int):
        if not comentarios_etapa_enabled():
            return _comentarios_etapa_unavailable()
        from apps.access.resolve import user_has_permission
        from apps.access import registry as R

        comentario = (
            SuporteClaroComentarioEtapa.objects.select_related("created_by", "registro")
            .filter(pk=comentario_id, registro_id=registro_id)
            .first()
        )
        if not comentario:
            return Response({"detail": "Comentário não encontrado."}, status=status.HTTP_404_NOT_FOUND)

        is_author = comentario.created_by_id and comentario.created_by_id == getattr(
            request.user, "id", None
        )
        can_change = user_has_permission(request.user, R.PROCESSOS_SUPORTE_CLARO_CHANGE_STATUS)
        if not (is_author or can_change):
            return Response(
                {"detail": "Sem permissão para excluir este comentário."},
                status=status.HTTP_403_FORBIDDEN,
            )

        jira_removal = None
        if comentario.formalizado_externo:
            jira_removal = delete_formalized_jira_comments(comentario, request.user)

        comentario.delete()
        body = {"message": "Comentário removido."}
        if jira_removal is not None:
            body["jira_removal"] = jira_removal
            if jira_removal.get("skipped") and jira_removal.get("error"):
                body["warning"] = jira_removal["error"]
            elif jira_removal.get("error") and not jira_removal.get("ok"):
                body["warning"] = (
                    "Comentário removido no portal, mas houve falha ao excluir no Jira: "
                    f"{jira_removal['error']}"
                )
        return Response(body)

