from django.apps import AppConfig


class AutomacoesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.automacoes"
    verbose_name = "Automações"

    def ready(self) -> None:
        from .productivity_hooks import register_production_sync_hook
        from .rotina_bruto_hooks import register_rotina_bruto_sync_hook
        from .monitor_eventos_hooks import register_monitor_eventos_sync_hook
        from .replicacao_d1_hooks import register_replicacao_d1_sync_hook
        from .produtividade_case_hooks import register_produtividade_case_sync_hook
        from .prioridades_nh_hooks import register_prioridades_nh_sync_hook
        from .reinspecao_ged_hooks import register_production_ged_irregularidade_sync_hook

        register_production_sync_hook()
        register_rotina_bruto_sync_hook()
        register_monitor_eventos_sync_hook()
        register_replicacao_d1_sync_hook()
        register_produtividade_case_sync_hook()
        register_prioridades_nh_sync_hook()
        register_production_ged_irregularidade_sync_hook()
