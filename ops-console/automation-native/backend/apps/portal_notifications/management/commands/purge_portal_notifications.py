from django.core.management.base import BaseCommand

from apps.portal_notifications.services import purge_expired_notifications


class Command(BaseCommand):
    help = "Exclui definitivamente notificações do portal com retenção expirada."

    def handle(self, *args, **options):
        deleted = purge_expired_notifications()
        self.stdout.write(self.style.SUCCESS(f"Notificações expurgadas: {deleted}"))

