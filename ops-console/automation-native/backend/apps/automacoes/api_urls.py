from django.urls import path

from . import views

urlpatterns = [
    path("meta/", views.meta_view, name="automacoes-meta"),
    path("status/", views.status_view, name="automacoes-status"),
    path("execution-trends/", views.execution_trends_view, name="automacoes-execution-trends"),
    path("start/", views.start_view, name="automacoes-start"),
    path("reveal-process/", views.reveal_process_view, name="automacoes-reveal-process"),
    path("stop/", views.stop_view, name="automacoes-stop"),
    path("stop-all/", views.stop_all_view, name="automacoes-stop-all"),
    path("config/", views.config_view, name="automacoes-config"),
    path("logs/", views.logs_view, name="automacoes-logs"),
    path("logs/download/", views.logs_download_view, name="automacoes-logs-download"),
    path("logs/clear/", views.logs_clear_view, name="automacoes-logs-clear"),
    path("sync-jobs/", views.sync_jobs_view, name="automacoes-sync-jobs"),
    path("sync-jobs/<int:job_id>/cancel/", views.sync_job_cancel_view, name="automacoes-sync-job-cancel"),
    path("sync-config/", views.sync_config_view, name="automacoes-sync-config"),
    path("credentials/status/", views.credentials_status_view, name="automacoes-credentials-status"),
    path("credentials/validate/", views.credentials_validate_view, name="automacoes-credentials-validate"),
    path("credentials/validate/cancel/", views.credentials_validate_cancel_view, name="automacoes-credentials-validate-cancel"),
    path("system/select-folder/", views.select_folder_view, name="automacoes-select-folder"),
    path("system/open-folder/", views.open_folder_view, name="automacoes-open-folder"),
    path("replicacao/validar-plano/", views.replicacao_validar_plano_view, name="automacoes-replicacao-validar"),
    path("replicacao/runs/", views.replicacao_runs_view, name="automacoes-replicacao-runs"),
    path("replicacao/workflows/", views.replicacao_workflows_view, name="automacoes-replicacao-workflows"),
    path("replicacao-d1/validar-plano/", views.replicacao_d1_validar_plano_view, name="automacoes-replicacao-d1-validar"),
    path("replicacao-d1/runs/", views.replicacao_d1_runs_view, name="automacoes-replicacao-d1-runs"),
    path("replicacao-d1/workflows/", views.replicacao_d1_workflows_view, name="automacoes-replicacao-d1-workflows"),
    path("replicacao-d1/meta-mensal/", views.replicacao_d1_meta_mensal_get_view, name="automacoes-replicacao-d1-meta"),
    path(
        "replicacao-d1/meta-mensal/recalcular/",
        views.replicacao_d1_meta_mensal_recalcular_view,
        name="automacoes-replicacao-d1-meta-recalcular",
    ),
    path(
        "replicacao-d1/meta-mensal/ajuste/",
        views.replicacao_d1_meta_mensal_ajuste_view,
        name="automacoes-replicacao-d1-meta-ajuste",
    ),
    path(
        "replicacao-d1/runs/<str:run_id>/ledger/",
        views.replicacao_d1_run_ledger_view,
        name="automacoes-replicacao-d1-ledger",
    ),
    path("replicacao-d1/limpar-planos/", views.replicacao_d1_limpar_planos_view, name="automacoes-replicacao-d1-limpar"),
]
