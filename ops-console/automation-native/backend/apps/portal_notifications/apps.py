from django.apps import AppConfig


class PortalNotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portal_notifications"
    label = "portal_notifications"
    verbose_name = "Notificações do portal"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(
            sync_default_notification_rules,
            sender=self,
            dispatch_uid="portal_notifications.sync_default_rules",
        )


def sync_default_notification_rules(**kwargs):
    from .services import sync_notification_rule_catalog

    sync_notification_rule_catalog()
