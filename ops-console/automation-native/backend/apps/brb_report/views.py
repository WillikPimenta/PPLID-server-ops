# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponse
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_exceptions import secure_error_payload

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from apps.brb_report.models import BrbCsDataSource, BrbReportGeneration
from apps.brb_report.services.client_catalog import list_report_clients
from apps.dimensoes_processos.models import DimCliente
from apps.brb_report.services.report_service import (
    artifact_content_type,
    generate_reports,
    preview_client_from_db,
    preview_uploaded_workbook,
    regenerate_reports,
    report_runtime_config,
    resolve_artifact_path,
    resolve_source_workbook,
    store_cs_source_workbook,
    store_portfolio_supplement_workbook,
)
from apps.brb_report.services.cs_dashboard import build_cs_dashboard, prepare_cs_source_snapshot
from apps.brb_report.services.executive_portfolio_serve import (
    executive_portfolio_busy_response,
    serve_executive_client_rca_pdf,
    serve_executive_portfolio_html,
)

ViewPerm = portal_perm(R.INDICADORES_RELATORIO_BRB_VIEW)


def _parse_date(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Data inválida: {value}")


def _save_upload_temp(upload) -> Path:
    fd, name = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    path = Path(name)
    with path.open("wb") as out:
        for chunk in upload.chunks():
            out.write(chunk)
    return path


def _cleanup_temp(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


def _validate_upload(upload, *, required: bool = True) -> str | None:
    if not upload:
        if required:
            return "Envie o arquivo .xlsx no campo file."
        return None
    name = (upload.name or "").lower()
    if not name.endswith(".xlsx"):
        return "Formato inválido. Use .xlsx."
    if upload.size <= 0:
        return "Arquivo vazio."
    max_mb = 80
    if upload.size > max_mb * 1024 * 1024:
        return f"Arquivo excede {max_mb} MB."
    return None


def _validate_portfolio_workbook(path: Path) -> str | None:
    from report_brb.brb_loaders import validate_portfolio_contestacao_workbook

    try:
        validate_portfolio_contestacao_workbook(path)
    except ValueError as exc:
        return str(exc)
    return None


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _parse_executive_portfolio_params(source) -> tuple[date, date, int, list[str] | None, Response | None]:
    try:
        inicio = _parse_date(source.get("date_from") or source.get("inicio"))
        fim = _parse_date(source.get("date_to") or source.get("fim"))
    except ValueError as exc:
        return None, None, 0, None, Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    today = datetime.now().date()
    start = inicio or datetime(today.year, 1, 1).date()
    end = fim or today
    if start > end:
        return (
            None,
            None,
            0,
            None,
            Response(
                {"detail": "A data inicial não pode ser posterior à data final."},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )

    try:
        top_n = max(1, min(50, int(source.get("top") or 10)))
    except (TypeError, ValueError):
        return (
            None,
            None,
            0,
            None,
            Response({"detail": "Parâmetro top inválido."}, status=status.HTTP_400_BAD_REQUEST),
        )

    raw_clients = source.getlist("client") if hasattr(source, "getlist") else []
    if not raw_clients and source.get("client"):
        raw_clients = [source.get("client")]
    client_slugs = [
        slug.strip().lower()
        for slug in raw_clients
        if slug and str(slug).strip()
    ] or None

    return start, end, top_n, client_slugs, None


def _resolve_portfolio_workbook(
    *,
    supplement_key: str | None = None,
    workbook_path: Path | None = None,
    workbook_fingerprint: str | None = None,
) -> tuple[Path | None, str | None, Response | None]:
    key = (supplement_key or "").strip()
    if key:
        try:
            path = resolve_source_workbook(key)
        except FileNotFoundError as exc:
            return None, None, Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return path, f"sup:{key}", None
    return workbook_path, workbook_fingerprint, None


def _executive_portfolio_html_response(
    *,
    start: date,
    end: date,
    top_n: int,
    client_slugs: list[str] | None,
    workbook_path: Path | None = None,
    workbook_fingerprint: str | None = None,
    standalone_client_slug: str | None = None,
    supplement_key: str | None = None,
    request=None,
):
    export_api_base = ""
    if request is not None:
        export_api_base = request.build_absolute_uri("/api/v1/brb-report/").rstrip("/")
    html, error = serve_executive_portfolio_html(
        inicio=start,
        fim=end,
        top_n=top_n,
        client_slugs=client_slugs,
        workbook_path=workbook_path,
        workbook_fingerprint=workbook_fingerprint,
        standalone_client_slug=standalone_client_slug,
        export_api_base=export_api_base,
        supplement_key=supplement_key,
    )
    if error:
        body, status_code, headers = executive_portfolio_busy_response(error)
        return Response(body, status=status_code, headers=headers)

    if standalone_client_slug:
        filename = f"portfolio_cliente_{standalone_client_slug}_{start.isoformat()}_{end.isoformat()}.html"
    else:
        filename = f"portfolio_executivo_{start.isoformat()}_{end.isoformat()}.html"
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


def _generation_options(request) -> dict:
    client_slug = (request.data.get("cliente") or request.data.get("client_slug") or "brb").strip().lower()
    try:
        inicio = _parse_date(request.data.get("inicio") or request.data.get("date_from"))
        fim = _parse_date(request.data.get("fim") or request.data.get("date_to"))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    enrich = str(request.data.get("enriquecer", "true")).lower() not in ("0", "false", "no")
    include_dashboard = str(request.data.get("dashboard", "false")).lower() in ("1", "true", "yes")
    include_excel_cliente = str(request.data.get("excel_cliente", "true")).lower() not in ("0", "false", "no")
    include_excel_interno = str(request.data.get("excel_interno", "false")).lower() in ("1", "true", "yes")
    return {
        "client_slug": client_slug,
        "inicio": inicio,
        "fim": fim,
        "enrich": enrich,
        "include_dashboard": include_dashboard,
        "include_excel_cliente": include_excel_cliente,
        "include_excel_interno": include_excel_interno,
    }


def _finalize_generation(request, result: dict, inicio, fim) -> dict:
    BrbReportGeneration.objects.create(
        user=request.user if request.user.is_authenticated else None,
        client_slug=result["client_slug"],
        period_start=inicio,
        period_end=fim,
        storage_key=result["storage_key"],
        artifacts=result["artifacts"],
    )
    base = request.build_absolute_uri("/api/v1/brb-report/artifacts/").rstrip("/")
    result["downloads"] = {
        key: f"{base}/{result['storage_key']}/{filename}/"
        for key, filename in result["artifacts"].items()
    }
    return result


class BrbReportClientsView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        clients = list_report_clients()
        dim_count = DimCliente.objects.count()
        setup_hint = None
        if dim_count == 0:
            setup_hint = (
                "DimCliente vazio — a lista fica limitada ao registry (BRB, PicPay, BMG, Claro). "
                "Execute: python manage.py import_identificacao_processos"
            )
        return Response(
            {
                "clients": clients,
                "clientes_source": "brb_report.client_catalog",
                "clientes_total": len(clients),
                "dim_cliente_count": dim_count,
                "setup_hint": setup_hint,
                **report_runtime_config(),
            }
        )


class BrbReportPreviewDbView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        client_slug = (request.query_params.get("client") or request.query_params.get("cliente") or "brb").strip().lower()
        try:
            inicio = _parse_date(request.query_params.get("inicio") or request.query_params.get("date_from"))
            fim = _parse_date(request.query_params.get("fim") or request.query_params.get("date_to"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            data = preview_client_from_db(client_slug, inicio=inicio, fim=fim)
        except KeyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(data)


class BrbReportCsDashboardView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        client_slug = (request.query_params.get("client") or "brb").strip().lower()
        try:
            date_from = _parse_date(request.query_params.get("date_from"))
            date_to = _parse_date(request.query_params.get("date_to"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if date_from and date_to and date_from > date_to:
            return Response(
                {"detail": "A data inicial não pode ser posterior à data final."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            payload = build_cs_dashboard(
                client_slug=client_slug,
                storage_key=(request.query_params.get("storage_key") or "").strip(),
                date_from=date_from,
                date_to=date_to,
                result=(request.query_params.get("result") or "").strip(),
                failure_type=(request.query_params.get("failure_type") or "").strip(),
                stage=(request.query_params.get("stage") or "").strip(),
                search=(request.query_params.get("q") or "").strip(),
                critical_only=(request.query_params.get("critical_only") or "").lower()
                in ("1", "true", "yes"),
            )
        except FileNotFoundError:
            return Response(
                {"available": False, "detail": "Base não encontrada.", "client_slug": client_slug}
            )
        except (ValueError, KeyError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                secure_error_payload(exc, operation="brb_report.cs_dashboard"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(payload)


class BrbReportCsSourceUploadView(APIView):
    permission_classes = [ViewPerm]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        eo_mode = report_runtime_config()["eo_db_mode"]
        err = _validate_upload(upload, required=not eo_mode)
        if err:
            if eo_mode and not upload:
                err = "Envie a planilha suplemento (.xlsx) com NA e Treinamentos."
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        client_slug = (request.data.get("client") or request.data.get("cliente") or "brb").strip().lower()
        tmp_path = _save_upload_temp(upload) if upload else None
        try:
            storage_key = store_cs_source_workbook(tmp_path, user=request.user if request.user.is_authenticated else None)
            snapshot = prepare_cs_source_snapshot(storage_key, client_slug)
            source = BrbCsDataSource.objects.create(
                user=request.user if request.user.is_authenticated else None,
                client_slug=client_slug,
                storage_key=storage_key,
                original_filename=upload.name or "source.xlsx",
            )
        except FileNotFoundError:
            return Response({"detail": "Arquivo não encontrado."}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, KeyError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                secure_error_payload(exc, operation="brb_report.cs_source_upload"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            if tmp_path:
                _cleanup_temp(tmp_path)
        return Response(
            {
                "storage_key": source.storage_key,
                "client_slug": source.client_slug,
                "filename": source.original_filename,
                "updated_at": source.created_at,
                **snapshot,
            },
            status=status.HTTP_201_CREATED,
        )


class BrbReportPreviewView(APIView):
    permission_classes = [ViewPerm]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        err = _validate_upload(upload, required=not report_runtime_config()["eo_db_mode"])
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        client_slug = (request.data.get("cliente") or request.data.get("client_slug") or "brb").strip().lower()
        try:
            inicio = _parse_date(request.data.get("inicio") or request.data.get("date_from"))
            fim = _parse_date(request.data.get("fim") or request.data.get("date_to"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        tmp_path = _save_upload_temp(upload) if upload else None
        try:
            if tmp_path:
                data = preview_uploaded_workbook(
                    tmp_path, client_slug=client_slug, inicio=inicio, fim=fim
                )
            else:
                data = preview_client_from_db(client_slug, inicio=inicio, fim=fim)
            return Response(data)
        except FileNotFoundError:
            return Response({"detail": "Arquivo não encontrado."}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, KeyError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            if tmp_path:
                _cleanup_temp(tmp_path)


class BrbReportGenerateView(APIView):
    permission_classes = [ViewPerm]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        err = _validate_upload(upload, required=not report_runtime_config()["eo_db_mode"])
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        try:
            opts = _generation_options(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        tmp_path = _save_upload_temp(upload) if upload else None

        try:
            result = generate_reports(
                workbook_path=tmp_path,
                client_slug=opts["client_slug"],
                inicio=opts["inicio"],
                fim=opts["fim"],
                enrich=opts["enrich"],
                include_pulse=True,
                include_excel_cliente=opts["include_excel_cliente"],
                include_excel_interno=opts["include_excel_interno"],
                include_dashboard=opts["include_dashboard"],
            )
        except FileNotFoundError:
            return Response({"detail": "Arquivo não encontrado."}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, KeyError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                secure_error_payload(exc, operation="brb_report.generate"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            if tmp_path:
                _cleanup_temp(tmp_path)

        result = _finalize_generation(request, result, opts["inicio"], opts["fim"])
        return Response(result, status=status.HTTP_201_CREATED)


class BrbReportRegenerateView(APIView):
    permission_classes = [ViewPerm]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, storage_key: str):
        try:
            opts = _generation_options(request)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = regenerate_reports(
                source_storage_key=storage_key,
                client_slug=opts["client_slug"],
                inicio=opts["inicio"],
                fim=opts["fim"],
                enrich=opts["enrich"],
                include_pulse=True,
                include_excel_cliente=opts["include_excel_cliente"],
                include_excel_interno=opts["include_excel_interno"],
                include_dashboard=opts["include_dashboard"],
            )
        except FileNotFoundError:
            return Response({"detail": "Arquivo não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        except (ValueError, KeyError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                secure_error_payload(exc, operation="brb_report.regenerate"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        result = _finalize_generation(request, result, opts["inicio"], opts["fim"])
        return Response(result, status=status.HTTP_201_CREATED)


class BrbReportArtifactDownloadView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request, storage_key: str, filename: str):
        try:
            path = resolve_artifact_path(storage_key, filename)
        except FileNotFoundError as exc:
            raise Http404(str(exc)) from exc
        as_attachment = request.query_params.get("download", "0") in ("1", "true", "yes")
        response = FileResponse(
            open(path, "rb"),
            content_type=artifact_content_type(filename),
            as_attachment=as_attachment,
            filename=filename,
        )
        return response


class BrbReportHistoryView(APIView):
    permission_classes = [ViewPerm]

    def get(self, request):
        qs = BrbReportGeneration.objects.all()[:20]
        items = []
        base = request.build_absolute_uri("/api/v1/brb-report/artifacts/").rstrip("/")
        for row in qs:
            downloads = {
                key: f"{base}/{row.storage_key}/{filename}/"
                for key, filename in (row.artifacts or {}).items()
            }
            items.append(
                {
                    "id": row.id,
                    "client_slug": row.client_slug,
                    "period_start": row.period_start,
                    "period_end": row.period_end,
                    "created_at": row.created_at,
                    "artifacts": row.artifacts,
                    "downloads": downloads,
                }
            )
        return Response({"items": items})


class BrbReportExecutivePortfolioView(APIView):
    """HTML consolidado multi-cliente para CS (portfólio executivo)."""

    permission_classes = [ViewPerm]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        start, end, top_n, client_slugs, err = _parse_executive_portfolio_params(request.query_params)
        if err:
            return err
        workbook_path, workbook_fingerprint, wb_err = _resolve_portfolio_workbook(
            supplement_key=request.query_params.get("supplement_key"),
        )
        if wb_err:
            return wb_err
        return _executive_portfolio_html_response(
            start=start,
            end=end,
            top_n=top_n,
            client_slugs=client_slugs,
            workbook_path=workbook_path,
            workbook_fingerprint=workbook_fingerprint,
            supplement_key=(request.query_params.get("supplement_key") or "").strip() or None,
            request=request,
        )

    def post(self, request):
        start, end, top_n, client_slugs, err = _parse_executive_portfolio_params(request.data)
        if err:
            return err

        upload = request.FILES.get("file")
        err_msg = _validate_upload(upload, required=False)
        if err_msg:
            return Response({"detail": err_msg}, status=status.HTTP_400_BAD_REQUEST)

        tmp_path: Path | None = None
        workbook_fingerprint = "eo"
        try:
            if upload:
                tmp_path = _save_upload_temp(upload)
                sheet_err = _validate_portfolio_workbook(tmp_path)
                if sheet_err:
                    return Response({"detail": sheet_err}, status=status.HTTP_400_BAD_REQUEST)
                workbook_fingerprint = _file_fingerprint(tmp_path)

            return _executive_portfolio_html_response(
                start=start,
                end=end,
                top_n=top_n,
                client_slugs=client_slugs,
                workbook_path=tmp_path,
                workbook_fingerprint=workbook_fingerprint,
                supplement_key=(request.data.get("supplement_key") or "").strip() or None,
                request=request,
            )
        finally:
            if tmp_path:
                _cleanup_temp(tmp_path)


class BrbReportExecutivePortfolioSupplementView(APIView):
    """Upload da planilha de contestação antes de gerar o portfólio (GET + supplement_key)."""

    permission_classes = [ViewPerm]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        err_msg = _validate_upload(upload, required=True)
        if err_msg:
            return Response({"detail": err_msg}, status=status.HTTP_400_BAD_REQUEST)

        tmp_path = _save_upload_temp(upload)
        try:
            sheet_err = _validate_portfolio_workbook(tmp_path)
            if sheet_err:
                return Response({"detail": sheet_err}, status=status.HTTP_400_BAD_REQUEST)
            storage_key = store_portfolio_supplement_workbook(tmp_path)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            _cleanup_temp(tmp_path)

        return Response({"storage_key": storage_key}, status=status.HTTP_201_CREATED)


class BrbReportExecutivePortfolioClientView(APIView):
    """HTML do portfólio executivo restrito a um único cliente."""

    permission_classes = [ViewPerm]

    def get(self, request):
        client_slug = (request.query_params.get("client") or "").strip().lower()
        if not client_slug:
            return Response(
                {"detail": "Informe o parâmetro client (slug do cliente)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        start, end, top_n, _, err = _parse_executive_portfolio_params(request.query_params)
        if err:
            return err
        workbook_path, workbook_fingerprint, wb_err = _resolve_portfolio_workbook(
            supplement_key=request.query_params.get("supplement_key"),
        )
        if wb_err:
            return wb_err
        return _executive_portfolio_html_response(
            start=start,
            end=end,
            top_n=top_n,
            client_slugs=[client_slug],
            workbook_path=workbook_path,
            workbook_fingerprint=workbook_fingerprint,
            standalone_client_slug=client_slug,
            supplement_key=(request.query_params.get("supplement_key") or "").strip() or None,
            request=request,
        )


class BrbReportExecutivePortfolioClientRcaView(APIView):
    """RCA em PDF para um cliente no recorte do portfólio executivo."""

    permission_classes = [ViewPerm]

    def get(self, request):
        client_slug = (request.query_params.get("client") or "").strip().lower()
        if not client_slug:
            return Response(
                {"detail": "Informe o parâmetro client (slug do cliente)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        start, end, _, _, err = _parse_executive_portfolio_params(request.query_params)
        if err:
            return err
        workbook_path, workbook_fingerprint, wb_err = _resolve_portfolio_workbook(
            supplement_key=request.query_params.get("supplement_key"),
        )
        if wb_err:
            return wb_err
        pdf_bytes, error = serve_executive_client_rca_pdf(
            client_slug=client_slug,
            inicio=start,
            fim=end,
            workbook_path=workbook_path,
            workbook_fingerprint=workbook_fingerprint,
        )
        if error == "invalid_client":
            return Response({"detail": "Cliente inválido."}, status=status.HTTP_400_BAD_REQUEST)
        if error:
            body, status_code, headers = executive_portfolio_busy_response(error)
            return Response(body, status=status_code, headers=headers)
        filename = f"rca_{client_slug}_{start.isoformat()}_{end.isoformat()}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response
