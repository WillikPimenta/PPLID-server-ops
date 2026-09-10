from flask import Blueprint, jsonify, render_template, request, send_file

from app.config.constants import ROBOT_MODES_LEGACY, ROBOT_MODES_PRIMARY

from .services.robot_manager import MODE_LABELS, ROBOT_MODES, robot_manager


web_bp = Blueprint("web", __name__)
api_bp = Blueprint("api", __name__, url_prefix="/api")


@web_bp.get("/")
def index():
    return render_template(
        "index.html",
        modes=ROBOT_MODES,
        modes_primary=ROBOT_MODES_PRIMARY,
        modes_legacy=ROBOT_MODES_LEGACY,
        mode_labels=MODE_LABELS,
    )


@web_bp.get("/sobre")
def sobre():
    return render_template("about.html")


@api_bp.get("/robots/meta")
def robots_meta():
    return jsonify({"ok": True, "modes": list(ROBOT_MODES), "labels": MODE_LABELS})


@api_bp.get("/robots/config")
def robots_config_get():
    mode = str(request.args.get("mode", "")).strip().lower() or None
    configs = robot_manager.robot_configs(mode=mode)
    if mode and not configs:
        return jsonify({"ok": False, "message": "Modo inválido", "configs": {}}), 400
    return jsonify({"ok": True, "configs": configs})


@api_bp.post("/robots/config")
def robots_config_update():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "")).strip().lower()
    config = payload.get("config", {})
    ok, message, configs = robot_manager.update_robot_config(mode=mode, config=config)
    code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message, "configs": configs}), code


@api_bp.post("/system/select-folder")
def system_select_folder():
    payload = request.get_json(silent=True) or {}
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
        return jsonify({"ok": False, "message": f"Falha ao abrir seletor de pasta: {exc}", "path": ""}), 500

    if not selected:
        return jsonify({"ok": False, "message": "Seleção cancelada", "path": ""}), 200

    return jsonify({"ok": True, "message": "Pasta selecionada", "path": selected}), 200


@api_bp.post("/system/open-folder")
def system_open_folder():
    payload = request.get_json(silent=True) or {}
    folder_path = str(payload.get("path", "")).strip()
    ok, message = robot_manager.open_folder(folder_path)
    code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message}), code


@api_bp.get("/robots/status")
def robots_status():
    client_id = str(request.headers.get("X-Automacao-Client-Id") or request.args.get("client_id") or "").strip()[:80]
    return jsonify(
        {
            "ok": True,
            "robots": robot_manager.status(),
            "credentials": robot_manager.credentials_status(session_id=client_id or None),
        }
    )


@api_bp.get("/credentials/status")
def credentials_status():
    client_id = str(request.headers.get("X-Automacao-Client-Id") or request.args.get("client_id") or "").strip()[:80]
    return jsonify(
        {"ok": True, "credentials": robot_manager.credentials_status(session_id=client_id or None)}
    )


@api_bp.post("/credentials/validate")
def credentials_validate():
    payload = request.get_json(silent=True) or {}
    matricula = str(
        payload.get("matricula")
        or request.form.get("matricula")
        or request.args.get("matricula")
        or ""
    ).strip()
    senha = str(
        payload.get("senha")
        or request.form.get("senha")
        or request.args.get("senha")
        or ""
    )
    headless = bool(payload.get("headless", False))
    client_id = str(
        request.headers.get("X-Automacao-Client-Id")
        or payload.get("client_id")
        or ""
    ).strip()[:80]
    ok, message, credentials = robot_manager.start_okta_credentials_validation(
        matricula=matricula,
        senha=senha,
        headless=headless,
        session_id=client_id or None,
    )
    code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message, "credentials": credentials}), code


@api_bp.post("/credentials/validate/cancel")
def credentials_validate_cancel():
    payload = request.get_json(silent=True) or {}
    client_id = str(
        request.headers.get("X-Automacao-Client-Id")
        or payload.get("client_id")
        or ""
    ).strip()[:80]
    ok, message, credentials = robot_manager.cancel_okta_credentials_validation(
        session_id=client_id or None
    )
    return jsonify({"ok": ok, "message": message, "credentials": credentials}), 200


@api_bp.get("/robots/logs")
def robots_logs():
    mode = str(request.args.get("mode", "")).strip().lower() or None
    tail = request.args.get("tail", default=80, type=int)
    logs_data = robot_manager.logs(mode=mode, tail=tail)
    if mode and not logs_data:
        return jsonify({"ok": False, "message": "Modo inválido", "logs": {}}), 400
    return jsonify({"ok": True, "logs": logs_data})


@api_bp.get("/robots/logs/download")
def robots_logs_download():
    mode = str(request.args.get("mode", "")).strip().lower()
    file_path = robot_manager.log_file_path(mode)
    if not file_path:
        return jsonify({"ok": False, "message": "Modo inválido"}), 400
    return send_file(file_path, as_attachment=True, download_name=f"robot_{mode}.log")


@api_bp.post("/robots/logs/clear")
def robots_logs_clear():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "")).strip().lower()
    ok, message = robot_manager.clear_logs(mode)
    code = 200 if ok else 400
    logs_data = robot_manager.logs(mode=mode, tail=60) if ok else {}
    return jsonify({"ok": ok, "message": message, "logs": logs_data}), code


@api_bp.post("/robots/replicacao/validar-plano")
def replicacao_validar_plano():
    try:
        payload = request.get_json(silent=True) or {}
        robot_config = payload.get("robot_config", {})
        if not isinstance(robot_config, dict):
            robot_config = {}
        ok, message, detalhes = robot_manager.validar_plano_replicacao(robot_config)
        code = 200 if ok else 400
        return jsonify({"ok": ok, "message": message, "detalhes": detalhes}), code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "detalhes": {"tipo": "erro_servidor"}}), 500


@api_bp.get("/robots/replicacao/runs")
def replicacao_listar_runs():
    limite = int(request.args.get("limite", 15))
    runs = robot_manager.listar_runs_replicacao(limite=max(1, min(limite, 50)))
    return jsonify({"ok": True, "runs": runs})


@api_bp.get("/robots/replicacao/workflows")
def replicacao_listar_workflows():
    ok, message, workflows = robot_manager.listar_workflows_replicacao(d1=False)
    code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message, "workflows": workflows}), code


@api_bp.post("/robots/replicacao-d1/validar-plano")
def replicacao_d1_validar_plano():
    try:
        payload = request.get_json(silent=True) or {}
        robot_config = payload.get("robot_config", {})
        if not isinstance(robot_config, dict):
            robot_config = {}
        ok, message, detalhes = robot_manager.validar_plano_replicacao_d1(robot_config)
        code = 200 if ok else 400
        return jsonify({"ok": ok, "message": message, "detalhes": detalhes}), code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "detalhes": {"tipo": "erro_servidor"}}), 500


@api_bp.get("/robots/replicacao-d1/runs")
def replicacao_d1_listar_runs():
    limite = int(request.args.get("limite", 15))
    runs = robot_manager.listar_runs_replicacao_d1(limite=max(1, min(limite, 50)))
    return jsonify({"ok": True, "runs": runs})


@api_bp.get("/robots/replicacao-d1/workflows")
def replicacao_d1_listar_workflows():
    ok, message, workflows = robot_manager.listar_workflows_replicacao(d1=True)
    code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message, "workflows": workflows}), code


@api_bp.get("/robots/replicacao-d1/meta-mensal")
def replicacao_d1_meta_mensal_get():
    try:
        ano_mes = str(request.args.get("ano_mes", "") or "").strip()
        robot_config = {}
        ok, message, detalhes = robot_manager.obter_meta_mensal_replicacao_d1(
            robot_config, ano_mes=ano_mes or None
        )
        code = 200 if ok else 400
        return jsonify({"ok": ok, "message": message, **detalhes}), code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "clientes": []}), 500


@api_bp.post("/robots/replicacao-d1/meta-mensal/recalcular")
def replicacao_d1_meta_mensal_recalcular():
    try:
        payload = request.get_json(silent=True) or {}
        robot_config = payload.get("robot_config", {}) if isinstance(payload.get("robot_config"), dict) else {}
        ano_mes = str(payload.get("ano_mes", "") or "").strip()
        ok, message, detalhes = robot_manager.recalcular_meta_mensal_replicacao_d1(
            robot_config, ano_mes=ano_mes or None
        )
        code = 200 if ok else 400
        return jsonify({"ok": ok, "message": message, **detalhes}), code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@api_bp.post("/robots/replicacao-d1/meta-mensal/ajuste")
def replicacao_d1_meta_mensal_ajuste():
    try:
        payload = request.get_json(silent=True) or {}
        robot_config = payload.get("robot_config", {}) if isinstance(payload.get("robot_config"), dict) else {}
        ano_mes = str(payload.get("ano_mes", "") or "").strip()
        workflow = str(payload.get("workflow", "") or "").strip()
        cliente = str(payload.get("cliente", "") or "").strip()
        consumo = payload.get("consumo_acumulado", payload.get("consumo"))
        if not ano_mes or not (workflow or cliente) or consumo is None:
            return jsonify({
                "ok": False,
                "message": "ano_mes, workflow e consumo_acumulado são obrigatórios",
            }), 400
        ok, message, detalhes = robot_manager.ajustar_meta_mensal_replicacao_d1(
            robot_config,
            ano_mes=ano_mes,
            workflow=workflow,
            cliente=cliente,
            consumo_acumulado=int(consumo),
        )
        code = 200 if ok else 400
        return jsonify({"ok": ok, "message": message, **detalhes}), code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@api_bp.get("/robots/replicacao-d1/runs/<run_id>/ledger")
def replicacao_d1_run_ledger_status(run_id: str):
    try:
        from app.bots.meta_cliente_mensal import run_id_registrado_no_ledger

        meses = run_id_registrado_no_ledger(run_id)
        return jsonify({"ok": True, "run_id": run_id, "no_ledger": bool(meses), "meses": meses})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "no_ledger": False, "meses": []}), 500


@api_bp.post("/robots/replicacao-d1/limpar-planos")
def replicacao_d1_limpar_planos():
    try:
        payload = request.get_json(silent=True) or {}
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
        code = 200 if ok else 400
        return jsonify({"ok": ok, "message": message, **detalhes}), code
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "removidos": [], "ignorados": [], "erros": []}), 500


@api_bp.post("/robots/start")
def robots_start():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "")).strip().lower()
    matricula = str(payload.get("matricula", "")).strip()
    senha = str(payload.get("senha", ""))
    executar_imediatamente = bool(payload.get("executar_imediatamente", False))
    rotina_data_inicio = str(payload.get("rotina_data_inicio", "")).strip()
    rotina_data_fim = str(payload.get("rotina_data_fim", "")).strip()
    robot_config = payload.get("robot_config", {})

    require_okta_validation = bool(payload.get("require_okta_validation", True))
    modo_segundo_plano = bool(payload.get("modo_segundo_plano", False))
    client_id = str(
        request.headers.get("X-Automacao-Client-Id") or payload.get("client_id") or ""
    ).strip()[:80]

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
    code = 200 if ok else 400
    return jsonify(
        {
            "ok": ok,
            "message": message,
            "robots": robot_manager.status(),
            "credentials": robot_manager.credentials_status(session_id=client_id or None),
        }
    ), code


@api_bp.post("/robots/reveal-process")
def robots_reveal_process():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "falhas_criticas")).strip().lower()
    if mode != "falhas_criticas":
        return jsonify({"ok": False, "message": "Reveal disponível apenas para Falhas Críticas"}), 400
    ok, message = robot_manager.reveal_falhas_process()
    code = 200 if ok else 400
    return jsonify(
        {
            "ok": ok,
            "message": message,
            "robots": robot_manager.status(),
            "credentials": robot_manager.credentials_status(),
        }
    ), code


@api_bp.post("/robots/stop")
def robots_stop():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "")).strip().lower()
    ok, message = robot_manager.stop(mode)
    code = 200 if ok else 400
    return jsonify(
        {
            "ok": ok,
            "message": message,
            "robots": robot_manager.status(),
            "credentials": robot_manager.credentials_status(),
        }
    ), code


@api_bp.post("/robots/stop-all")
def robots_stop_all():
    return jsonify(
        {
            "ok": True,
            "results": robot_manager.stop_all(),
            "robots": robot_manager.status(),
            "credentials": robot_manager.credentials_status(),
        }
    )
