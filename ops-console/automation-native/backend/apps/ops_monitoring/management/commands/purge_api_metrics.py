from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ops_monitoring.models import ApiRequestMetric, ApiTrafficBucket, ApiTrafficUserBucket


class Command(BaseCommand):
    help = "Remove metricas de API mais antigas que N dias (padrao: 7)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="Dias de retencao")

    def handle(self, *args, **options):
        days = max(1, int(options["days"]))
        cutoff = timezone.now() - timedelta(days=days)
        sampled, _ = ApiRequestMetric.objects.filter(recorded_at__lt=cutoff).delete()
        traffic, _ = ApiTrafficBucket.objects.filter(bucket_start__lt=cutoff).delete()
        users, _ = ApiTrafficUserBucket.objects.filter(bucket_start__lt=cutoff).delete()
        deleted = sampled + traffic + users
        self.stdout.write(
            self.style.SUCCESS(
                f"Removidos {deleted} registros anteriores a {cutoff.isoformat()} "
                f"(amostras={sampled}, trafego={traffic}, usuarios={users})"
            )
        )
