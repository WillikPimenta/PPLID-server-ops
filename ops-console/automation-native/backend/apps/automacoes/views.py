from time import perf_counter

from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from apps.automacoes.permissions import AUTOMACAO_ANY
from rest_framework.response import Response

from config.api_exceptions import secure_error_payload

from .permissions import can_access_automacoes, can_configure_automacoes
from .services import AutomacoesPackageNotInstalledError, get_mode_labels, get_robot_manager


def _merge_ops_runtime_context(robots: dict) -> dict:
    """Annotate pilot bots started by the independent Ops supervisor."""
    import json
    import os
    from pathlib import Path

    root = Path(
        os.environ.get("OPS_AUTOMATION_RUNTIME_ROOT")
        or Path(os.environ.get("PPLID_BASE_DIR") or "C:/PPLID") / "ops" / "data" / "automation-runtime"
    )
    for mode, info in robots.items():
        if not isinstance(info, dict):
            continue
        info.setdefault("controller", "portal" if info.get("running") else None)
        if mode not in {"production", "rotina"} or not info.get("running"):
            continue
        try:
            state = json.loads((root / "state" / f"{mode}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if str(state.get("runnerPid") or "") != str(info.get("pid") or ""):
            continue
        info.update(
            {
                "controller": "ops",
                "target_environment": state.get("targetEnvironment"),
                "runtime_version": (state.get("bundle") or {}).get("id"),
                "supervisor_pid": state.get("supervisorPid"),
            }
        )
    return robots

OKTA_CLIENT_HEADER = "X-Automacao-Client-Id"


def _deny_if_no_access(request):
    if not can_access_automacoes(request.user):
        return Response(
            {
                "ok": False,
                "message": "Sem permissão para acessar automações.",
                "detail": "Sem permissão para acessar automações.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _deny_if_no_configure(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    if not can_configure_automacoes(request.user):
        return Response(
            {
                "ok": False,
                "message": "Sem permissão para configurar sync.",
                "detail": "Sem permissão para configurar sync.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _okta_client_id(request) -> str:
    """Identifica a aba/navegador para isolar modal/estado da validação Okta."""
    header = request.headers.get(OKTA_CLIENT_HEADER) or ""
    payload = request.data if hasattr(request, "data") and request.data is not None else {}
    if not isinstance(payload, dict):
        payload = {}
    raw = (
        str(header).strip()
        or str(payload.get("client_id", "")).strip()
        or str(request.query_params.get("client_id", "")).strip()
    )
    return raw[:80]


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def meta_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    modes, labels = get_mode_labels()
    return Response({"ok": True, "modes": list(modes), "labels": labels})


@api_view(["GET", "POST"])
@permission_classes(AUTOMACAO_ANY)
def config_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    if request.method == "GET":
        started_at = perf_counter()
        mode = str(request.query_params.get("mode", "")).strip().lower() or None
        configs = robot_manager.robot_configs(mode=mode)
        if mode in (None, "replicacao_auditoria_d1"):
            try:
                from apps.replicacao_d1.services.robot_config_bridge import merge_db_config_for_robot_manager

                merged = merge_db_config_for_robot_manager(
                    robot_manager,
                    mode="replicacao_auditoria_d1",
                    active_only=True,
                )
                if merged is not None:
                    configs = dict(configs)
                    configs["replicacao_auditoria_d1"] = merged
            except Exception:
                pass
        if mode and not configs:
            return Response(
                {"ok": False, "message": "Modo inválido", "configs": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        response = Response({"ok": True, "configs": configs})
        response["Server-Timing"] = f'automacoes_config;dur={(perf_counter() - started_at) * 1000:.1f}'
        return response

    payload = request.data or {}
    mode = str(payload.get("mode", "")).strip().lower()
    config = payload.get("config", {})
    if mode == "replicacao_auditoria_d1":
        try:
            from apps.replicacao_d1.services.config_snapshot import is_fonte_banco_ativa
            from apps.replicacao_d1.services.robot_config_bridge import (
                apply_robot_config_patch_to_db,
                split_robot_config_patch,
            )

            if is_fonte_banco_ativa():
                permanent, run_only = split_robot_config_patch(config if isinstance(config, dict) else {})
                ok = True
                message = f"Configuração do robô '{mode}' atualizada"
                if permanent:
                    denied_cfg = _deny_if_no_configure(request)
                    if denied_cfg:
                        return denied_cfg
                    apply_robot_config_patch_to_db(permanent, request.user)
                if run_only:
                    ok, message, configs = robot_manager.update_robot_config(mode=mode, config=run_only)
                else:
                    from apps.replicacao_d1.services.robot_config_bridge import merge_db_config_for_robot_manager

                    merged = merge_db_config_for_robot_manager(robot_manager, mode=mode)
                    configs = {mode: merged}
                code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
                return Response({"ok": ok, "message": message, "configs": configs}, status=code)
        except Exception as exc:
            return Response(
                secure_error_payload(
                    exc,
                    operation="automacoes.update_config",
                    message_field="message",
                    extra={"ok": False, "configs": {}},
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    ok, message, configs = robot_manager.update_robot_config(mode=mode, config=config)
    if ok and mode == "controle_sla":
        try:
            from apps.automacoes.controle_sla_bridge import apply_controle_sla_config

            saved = configs.get(mode) if isinstance(configs, dict) else None
            apply_controle_sla_config(saved if isinstance(saved, dict) else config)
        except Exception:
            pass
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response({"ok": ok, "message": message, "configs": configs}, status=code)


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def select_folder_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    payload = request.data or {}
    initial_path = str(payload.get("initial_path", "")).strip()
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="Selecione a pasta de saída",
            initialdir=initial_path or None,
            mustexist=False,
        )
        root.destroy()
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.select_folder",
                message_field="message",
                extra={"ok": False, "path": ""},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if not selected:
        return Response({"ok": False, "message": "Seleção cancelada", "path": ""})
    return Response({"ok": True, "message": "Pasta selecionada", "path": selected})


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def open_folder_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    payload = request.data or {}
    folder_path = str(payload.get("path", "")).strip()
    ok, message = robot_manager.open_folder(folder_path)
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response({"ok": ok, "message": message}, status=code)


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def status_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    client_id = _okta_client_id(request)
    from apps.automacoes.controle_sla_bridge import merge_robots_status

    return Response(
        {
            "ok": True,
            "robots": _merge_ops_runtime_context(merge_robots_status(robot_manager.status())),
            "credentials": robot_manager.credentials_status(session_id=client_id or None),
        }
    )


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def execution_trends_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    try:
        limite = int(request.query_params.get("limite", 20))
    except (TypeError, ValueError):
        limite = 20
    modes_raw = str(request.query_params.get("modes", "")).strip()
    modes = [m.strip().lower() for m in modes_raw.split(",") if m.strip()] if modes_raw else None
    trends = robot_manager.execution_trends(limite=limite, modes=modes)
    return Response({"ok": True, **trends})


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def credentials_status_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    client_id = _okta_client_id(request)
    return Response(
        {"ok": True, "credentials": robot_manager.credentials_status(session_id=client_id or None)}
    )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def credentials_validate_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    payload = request.data or {}
    matricula = str(payload.get("matricula", "")).strip()
    senha = str(payload.get("senha", ""))
    headless = _as_bool(payload.get("headless"), False)
    client_id = _okta_client_id(request)
    try:
        robot_manager = get_robot_manager()
    except AutomacoesPackageNotInstalledError as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.credentials_validate",
                message_field="message",
                extra={
                    "ok": False,
                    "credentials": {
                        "validated": False,
                        "checking": False,
                        "message": "Serviço de automação indisponível.",
                        "last_checked_at": None,
                    },
                },
            ),
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    ok, message, credentials = robot_manager.start_okta_credentials_validation(
        matricula=matricula,
        senha=senha,
        headless=headless,
        session_id=client_id or None,
    )
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response({"ok": ok, "message": message, "credentials": credentials}, status=code)


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def credentials_validate_cancel_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    client_id = _okta_client_id(request)
    try:
        robot_manager = get_robot_manager()
    except AutomacoesPackageNotInstalledError as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.credentials_validate_cancel",
                message_field="message",
                extra={
                    "ok": False,
                    "credentials": {
                        "validated": False,
                        "checking": False,
                        "message": "Serviço de automação indisponível.",
                        "last_checked_at": None,
                    },
                },
            ),
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    ok, message, credentials = robot_manager.cancel_okta_credentials_validation(
        session_id=client_id or None
    )
    return Response({"ok": ok, "message": message, "credentials": credentials}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def logs_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    mode = str(request.query_params.get("mode", "")).strip().lower() or None
    tail = int(request.query_params.get("tail", 80))
    logs_data = robot_manager.logs(mode=mode, tail=tail)
    if mode and not logs_data:
        return Response(
            {"ok": False, "message": "Modo inválido", "logs": {}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response({"ok": True, "logs": logs_data})


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def logs_download_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    mode = str(request.query_params.get("mode", "")).strip().lower()
    file_path = robot_manager.log_file_path(mode)
    if not file_path:
        return Response({"ok": False, "message": "Modo inválido"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        return FileResponse(
            open(file_path, "rb"),
            as_attachment=True,
            filename=f"robot_{mode}.log",
        )
    except OSError as exc:
        secure_error_payload(exc, operation="automacoes.logs_download")
        raise Http404("Arquivo não encontrado.") from exc


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def logs_clear_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    payload = request.data or {}
    mode = str(payload.get("mode", "")).strip().lower()
    ok, message = robot_manager.clear_logs(mode)
    logs_data = robot_manager.logs(mode=mode, tail=60) if ok else {}
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response({"ok": ok, "message": message, "logs": logs_data}, status=code)


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_validar_plano_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    try:
        payload = request.data or {}
        robot_config = payload.get("robot_config", {})
        if not isinstance(robot_config, dict):
            robot_config = {}
        ok, message, detalhes = robot_manager.validar_plano_replicacao(robot_config)
        code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
        return Response({"ok": ok, "message": message, "detalhes": detalhes}, status=code)
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.replicacao_validar_plano",
                message_field="message",
                extra={"ok": False, "detalhes": {"tipo": "erro_servidor"}},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_runs_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    limite = int(request.query_params.get("limite", 15))
    runs = robot_manager.listar_runs_replicacao(limite=max(1, min(limite, 50)))
    return Response({"ok": True, "runs": runs})


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_workflows_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    ok, message, workflows = robot_manager.listar_workflows_replicacao(d1=False)
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response({"ok": ok, "message": message, "workflows": workflows}, status=code)


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_validar_plano_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    try:
        payload = request.data or {}
        robot_config = payload.get("robot_config", {})
        if not isinstance(robot_config, dict):
            robot_config = {}
        ok, message, detalhes = robot_manager.validar_plano_replicacao_d1(robot_config)
        code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
        return Response({"ok": ok, "message": message, "detalhes": detalhes}, status=code)
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.replicacao_d1_validar_plano",
                message_field="message",
                extra={"ok": False, "detalhes": {"tipo": "erro_servidor"}},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_runs_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    limite = int(request.query_params.get("limite", 15))
    runs = robot_manager.listar_runs_replicacao_d1(limite=max(1, min(limite, 50)))
    return Response({"ok": True, "runs": runs})


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_workflows_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    ok, message, workflows = robot_manager.listar_workflows_replicacao(d1=True)
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response({"ok": ok, "message": message, "workflows": workflows}, status=code)


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_meta_mensal_get_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    try:
        ano_mes = str(request.query_params.get("ano_mes", "") or "").strip()
        robot_config = {}
        ok, message, detalhes = robot_manager.obter_meta_mensal_replicacao_d1(
            robot_config, ano_mes=ano_mes or None
        )
        code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
        return Response({"ok": ok, "message": message, **detalhes}, status=code)
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.replicacao_d1_meta_mensal",
                message_field="message",
                extra={"ok": False, "clientes": []},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_meta_mensal_recalcular_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    try:
        payload = request.data or {}
        robot_config = payload.get("robot_config", {}) if isinstance(payload.get("robot_config"), dict) else {}
        ano_mes = str(payload.get("ano_mes", "") or "").strip()
        ok, message, detalhes = robot_manager.recalcular_meta_mensal_replicacao_d1(
            robot_config, ano_mes=ano_mes or None
        )
        code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
        return Response({"ok": ok, "message": message, **detalhes}, status=code)
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.replicacao_d1_meta_recalcular",
                message_field="message",
                extra={"ok": False},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_meta_mensal_ajuste_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    try:
        payload = request.data or {}
        robot_config = payload.get("robot_config", {}) if isinstance(payload.get("robot_config"), dict) else {}
        ano_mes = str(payload.get("ano_mes", "") or "").strip()
        workflow = str(payload.get("workflow", "") or "").strip()
        cliente = str(payload.get("cliente", "") or "").strip()
        consumo = payload.get("consumo_acumulado", payload.get("consumo"))
        if not ano_mes or not (workflow or cliente) or consumo is None:
            return Response(
                {
                    "ok": False,
                    "message": "ano_mes, workflow e consumo_acumulado são obrigatórios",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        ok, message, detalhes = robot_manager.ajustar_meta_mensal_replicacao_d1(
            robot_config,
            ano_mes=ano_mes,
            workflow=workflow,
            cliente=cliente,
            consumo_acumulado=int(consumo),
        )
        code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
        return Response({"ok": ok, "message": message, **detalhes}, status=code)
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.replicacao_d1_meta_ajuste",
                message_field="message",
                extra={"ok": False},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_run_ledger_view(request, run_id: str):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    try:
        from app.bots.meta_cliente_mensal import run_id_registrado_no_ledger

        meses = run_id_registrado_no_ledger(run_id)
        return Response({"ok": True, "run_id": run_id, "no_ledger": bool(meses), "meses": meses})
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.replicacao_d1_ledger",
                message_field="message",
                extra={"ok": False, "no_ledger": False, "meses": []},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def replicacao_d1_limpar_planos_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    try:
        payload = request.data or {}
        robot_config = payload.get("robot_config", {})
        if not isinstance(robot_config, dict):
            robot_config = {}
        run_ids = payload.get("run_ids")
        if run_ids is not None and not isinstance(run_ids, list):
            run_ids = None
        forcar = bool(payload.get("forcar", False))
        remover_ledger = bool(payload.get("remover_ledger", False))
        ok, message, detalhes = robot_manager.limpar_planos_replicacao_d1(
            robot_config,
            run_ids=run_ids,
            forcar=forcar,
            remover_ledger=remover_ledger,
        )
        code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
        return Response({"ok": ok, "message": message, **detalhes}, status=code)
    except Exception as exc:
        return Response(
            secure_error_payload(
                exc,
                operation="automacoes.replicacao_d1_limpar_planos",
                message_field="message",
                extra={"ok": False, "removidos": [], "ignorados": [], "erros": []},
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def start_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    payload = request.data or {}
    mode = str(payload.get("mode", "")).strip().lower()
    matricula = str(payload.get("matricula", "")).strip()
    senha = str(payload.get("senha", ""))
    executar_imediatamente = bool(payload.get("executar_imediatamente", False))
    rotina_data_inicio = str(payload.get("rotina_data_inicio", "")).strip()
    rotina_data_fim = str(payload.get("rotina_data_fim", "")).strip()
    robot_config = payload.get("robot_config", {})
    if not isinstance(robot_config, dict):
        robot_config = {}
    require_okta_validation = bool(payload.get("require_okta_validation", True))
    modo_segundo_plano = bool(payload.get("modo_segundo_plano", False))
    client_id = _okta_client_id(request)

    if mode == "replicacao_auditoria_d1" and not bool(robot_config.get("apenas_planejamento")):
        agendamento_ativo = bool(robot_config.get("agendamento_ativo"))
        if not agendamento_ativo:
            try:
                from apps.replicacao_d1.config_models import ReplicacaoD1ConfigGeral

                agendamento_ativo = bool(ReplicacaoD1ConfigGeral.get_solo().agendamento_ativo)
            except Exception:
                agendamento_ativo = False
        if not agendamento_ativo:
            from apps.replicacao_d1.services.plan_validation import (
                PlanNotApprovedError,
                ensure_plan_approved,
            )

            run_id = str(robot_config.get("run_id") or "").strip()
            if not run_id:
                from apps.replicacao_d1.models import ReplicacaoD1PlanDeletion, ReplicacaoD1Run

                deleted_run_ids = ReplicacaoD1PlanDeletion.objects.values_list("run_id", flat=True)
                run_id = str(
                    ReplicacaoD1Run.objects.exclude(plan_hash="")
                    .exclude(run_id__in=deleted_run_ids)
                    .filter(validation_status=ReplicacaoD1Run.VALIDATION_APPROVED)
                    .order_by("-created_at", "-id")
                    .values_list("run_id", flat=True)
                    .first()
                    or ""
                )
                robot_config["run_id"] = run_id
            try:
                ensure_plan_approved(run_id)
            except PlanNotApprovedError as exc:
                return Response(
                    {
                        "ok": False,
                        "code": exc.code,
                        "message": str(exc),
                        "run_id": run_id,
                        "next_action": "Abra Configuração > Validar plano e aprove o plano antes da execução.",
                    },
                    status=status.HTTP_409_CONFLICT,
                )

    from apps.automacoes.controle_sla_bridge import (
        CONTROLE_SLA_MODE,
        merge_robots_status,
        start_controle_sla,
    )

    if mode == CONTROLE_SLA_MODE:
        ok, message = start_controle_sla(
            matricula=matricula,
            senha=senha,
            robot_config=robot_config if isinstance(robot_config, dict) else {},
            require_okta_validation=require_okta_validation,
            okta_session_id=client_id or None,
        )
    else:
        ok, message = robot_manager.start(
            mode=mode,
            matricula=matricula,
            senha=senha,
            executar_imediatamente=executar_imediatamente,
            rotina_data_inicio=rotina_data_inicio,
            rotina_data_fim=rotina_data_fim,
            robot_config=robot_config,
            require_okta_validation=require_okta_validation,
            modo_segundo_plano=modo_segundo_plano,
            okta_session_id=client_id or None,
        )
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response(
        {
            "ok": ok,
            "message": message,
            "robots": merge_robots_status(robot_manager.status()),
            "credentials": robot_manager.credentials_status(session_id=client_id or None),
        },
        status=code,
    )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def reveal_process_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    payload = request.data or {}
    mode = str(payload.get("mode", "falhas_criticas")).strip().lower()
    if mode != "falhas_criticas":
        return Response(
            {"ok": False, "message": "Reveal disponível apenas para Falhas Críticas"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    ok, message = robot_manager.reveal_falhas_process()
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    client_id = _okta_client_id(request)
    return Response(
        {
            "ok": ok,
            "message": message,
            "robots": robot_manager.status(),
            "credentials": robot_manager.credentials_status(session_id=client_id or None),
        },
        status=code,
    )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def stop_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    payload = request.data or {}
    mode = str(payload.get("mode", "")).strip().lower()
    client_id = _okta_client_id(request)

    from apps.automacoes.controle_sla_bridge import (
        CONTROLE_SLA_MODE,
        merge_robots_status,
        stop_controle_sla,
    )

    if mode == CONTROLE_SLA_MODE:
        ok, message = stop_controle_sla()
    else:
        ok, message = robot_manager.stop(mode)
    code = status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST
    return Response(
        {
            "ok": ok,
            "message": message,
            "robots": merge_robots_status(robot_manager.status()),
            "credentials": robot_manager.credentials_status(session_id=client_id or None),
        },
        status=code,
    )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def stop_all_view(request):
    denied = _deny_if_no_access(request)
    if denied:
        return denied
    robot_manager = get_robot_manager()
    client_id = _okta_client_id(request)
    from apps.automacoes.controle_sla_bridge import (
        CONTROLE_SLA_MODE,
        merge_robots_status,
        stop_controle_sla,
    )

    results = robot_manager.stop_all()
    sla_ok, sla_msg = stop_controle_sla()
    results[CONTROLE_SLA_MODE] = {"ok": sla_ok, "message": sla_msg}
    return Response(
        {
            "ok": True,
            "results": results,
            "robots": merge_robots_status(robot_manager.status()),
            "credentials": robot_manager.credentials_status(session_id=client_id or None),
        }
    )


@api_view(["GET"])
@permission_classes(AUTOMACAO_ANY)
def sync_jobs_view(request):
    """Fila bot→banco: resumo + jobs recentes para o painel da UI."""
    denied = _deny_if_no_access(request)
    if denied:
        return denied

    from django.db.models import Count

    from apps.common.bot_db_sync_queue import (
        reconcile_bot_db_sync_jobs,
        serialize_bot_db_sync_job,
        spawn_drain_worker,
        sync_lane_panel_snapshot,
    )
    from apps.common.models import BotDbSyncJob

    # Sempre validar running órfão/stale no poll da UI (não depende de novo enqueue).
    reconcile_bot_db_sync_jobs(spawn_if_requeued=True)

    # Um job pode entrar enquanto o drain anterior ainda está terminando. Nesse
    # caso o enqueue pode ter sido ignorado pelo cooldown de spawn, deixando o
    # job pending sem um novo processo para consumi-lo. O polling da UI é uma
    # oportunidade segura para rearmar o drain; o próprio worker/lock/cooldown
    # evita duplicação.
    if BotDbSyncJob.objects.filter(
        status=BotDbSyncJob.STATUS_PENDING
    ).exists():
        spawn_drain_worker()

    try:
        limit = int(request.query_params.get("limit", 40))
    except (TypeError, ValueError):
        limit = 40
    limit = max(1, min(limit, 100))

    job_id = None
    raw_job_id = request.query_params.get("job_id")
    if raw_job_id not in (None, ""):
        try:
            job_id = int(raw_job_id)
        except (TypeError, ValueError):
            job_id = None

    counts = {
        BotDbSyncJob.STATUS_PENDING: 0,
        BotDbSyncJob.STATUS_RUNNING: 0,
        BotDbSyncJob.STATUS_DONE: 0,
        BotDbSyncJob.STATUS_FAILED: 0,
        BotDbSyncJob.STATUS_SKIPPED: 0,
    }
    for row in BotDbSyncJob.objects.values("status").order_by().annotate(n=Count("id")):
        st = row["status"]
        if st in counts:
            counts[st] = int(row["n"] or 0)

    if job_id is not None:
        qs = BotDbSyncJob.objects.filter(pk=job_id)
    else:
        qs = BotDbSyncJob.objects.order_by("-created_at", "-id")[:limit]
    jobs = [serialize_bot_db_sync_job(job) for job in qs]
    lane_snapshot = sync_lane_panel_snapshot()

    pending = counts[BotDbSyncJob.STATUS_PENDING]
    running = counts[BotDbSyncJob.STATUS_RUNNING]
    return Response(
        {
            "ok": True,
            "summary": {
                "pending": pending,
                "running": running,
                "done": counts[BotDbSyncJob.STATUS_DONE],
                "failed": counts[BotDbSyncJob.STATUS_FAILED],
                "skipped": counts[BotDbSyncJob.STATUS_SKIPPED],
                "queue_busy": (pending + running) > 0,
            },
            "jobs": jobs,
            "limit": limit,
            **lane_snapshot,
        }
    )


@api_view(["GET", "PUT"])
@permission_classes(AUTOMACAO_ANY)
def sync_config_view(request):
    """Lê/grava knobs de sync bot→banco (chunk, batch, stale, drain, wait)."""
    from apps.common.bot_db_sync_runtime import (
        runtime_config_payload,
        update_bot_db_sync_runtime_config,
    )

    if request.method == "GET":
        denied = _deny_if_no_access(request)
        if denied:
            return denied
        return Response({"ok": True, "config": runtime_config_payload()})

    denied = _deny_if_no_configure(request)
    if denied:
        return denied

    payload = request.data if isinstance(request.data, dict) else {}
    try:
        updated = update_bot_db_sync_runtime_config(
            chunk_size=payload.get("chunk_size"),
            batch_size=payload.get("batch_size"),
            stale_minutes=payload.get("stale_minutes"),
            drain_max_jobs=payload.get("drain_max_jobs"),
            queue_wait_s=payload.get("queue_wait_s"),
            high_concurrency=payload.get("high_concurrency"),
            mid_concurrency=payload.get("mid_concurrency"),
            low_concurrency=payload.get("low_concurrency"),
        )
    except (TypeError, ValueError) as exc:
        return Response(
            {"ok": False, "message": str(exc), "detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            "ok": True,
            "message": "Configuração de sync salva.",
            "config": runtime_config_payload(),
            "values": {
                "chunk_size": updated.chunk_size,
                "batch_size": updated.batch_size,
                "stale_minutes": updated.stale_minutes,
                "drain_max_jobs": updated.drain_max_jobs,
                "queue_wait_s": updated.queue_wait_s,
                "high_concurrency": updated.high_concurrency,
                "mid_concurrency": updated.mid_concurrency,
                "low_concurrency": updated.low_concurrency,
            },
        }
    )


@api_view(["POST"])
@permission_classes(AUTOMACAO_ANY)
def sync_job_cancel_view(request, job_id: int):
    """Cancela job pendente (pending → skipped)."""
    denied = _deny_if_no_configure(request)
    if denied:
        return denied

    from apps.common.bot_db_sync_queue import cancel_pending_job
    from apps.common.models import BotDbSyncJob

    try:
        job = cancel_pending_job(int(job_id))
    except LookupError as exc:
        return Response(
            {"ok": False, "message": str(exc), "detail": str(exc)},
            status=status.HTTP_404_NOT_FOUND,
        )
    except ValueError as exc:
        return Response(
            {"ok": False, "message": str(exc), "detail": str(exc)},
            status=status.HTTP_409_CONFLICT,
        )

    status_labels = dict(BotDbSyncJob.STATUS_CHOICES)
    return Response(
        {
            "ok": True,
            "message": "Job cancelado.",
            "job": {
                "id": job.pk,
                "status": job.status,
                "status_label": status_labels.get(job.status, job.status),
                "message": job.message,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            },
        }
    )
