from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.workforce.models import Agent, AgentHistory

# Padrões gravados por versões antigas de seed_dev que tocavam colaboradores reais.
REAL_LAN_TOUCHED_BY_LEGACY_SEED = "c91763a"
LEGACY_SEED_AGENT_DEFAULTS = {
    "email": "matheus.mendes@experian.com",
    "hire_date": date(2018, 1, 15),
}
LEGACY_SEED_HISTORY_SIGNATURE = {
    "team": "Planejamento",
    "job_title": "Coordenador",
    "job_activity": "BrFlow",
}


class Command(BaseCommand):
    help = (
        "Reverte alterações conhecidas de seed_dev em colaboradores reais "
        "(ex.: c91763a) e remove histórico fictício criado pelo seed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas mostra o que seria corrigido.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        repairs = self._plan_repairs()

        if not repairs:
            self.stdout.write(self.style.SUCCESS("Nenhum dano conhecido de seed_dev detectado."))
            return

        for line in repairs:
            self.stdout.write(line)

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run: nenhuma alteração aplicada."))
            return

        with transaction.atomic():
            self._apply_repairs()

        self.stdout.write(self.style.SUCCESS("Reparo concluído."))
        self.stdout.write(
            "Se a senha do usuário real foi redefinida pelo seed antigo, "
            "use reset de senha em /configuracoes/usuarios ou seed_portal_users --reset-passwords."
        )

    def _plan_repairs(self) -> list[str]:
        lines: list[str] = []
        agent = Agent.objects.filter(user_lan_id=REAL_LAN_TOUCHED_BY_LEGACY_SEED).first()
        if not agent:
            return lines

        fake_histories = list(
            AgentHistory.objects.filter(
                agent=agent,
                final_date__isnull=True,
                active=True,
                **LEGACY_SEED_HISTORY_SIGNATURE,
            )
        )
        if fake_histories:
            lines.append(
                f"- Remover histórico fictício de seed ({len(fake_histories)} registro(s)) "
                f"em {REAL_LAN_TOUCHED_BY_LEGACY_SEED}"
            )

        if agent.email == LEGACY_SEED_AGENT_DEFAULTS["email"]:
            lines.append(f"- Restaurar e-mail de {REAL_LAN_TOUCHED_BY_LEGACY_SEED}")

        if agent.hire_date == LEGACY_SEED_AGENT_DEFAULTS["hire_date"]:
            lines.append(f"- Restaurar hire_date de {REAL_LAN_TOUCHED_BY_LEGACY_SEED}")

        closed = (
            AgentHistory.objects.filter(
                agent=agent,
                active=False,
                final_date__isnull=False,
                job_title__icontains="Analista de Planejamento",
            )
            .order_by("-start_date")
            .first()
        )
        if closed and fake_histories:
            lines.append(
                f"- Reativar histórico {closed.job_title} "
                f"({closed.start_date} - aberto)"
            )

        user = User.objects.filter(username=REAL_LAN_TOUCHED_BY_LEGACY_SEED).first()
        if user and user.is_superuser:
            lines.append(f"- Remover flags Django superuser/staff de {REAL_LAN_TOUCHED_BY_LEGACY_SEED}")

        return lines

    def _apply_repairs(self) -> None:
        agent = Agent.objects.get(user_lan_id=REAL_LAN_TOUCHED_BY_LEGACY_SEED)

        fake_qs = AgentHistory.objects.filter(
            agent=agent,
            final_date__isnull=True,
            active=True,
            **LEGACY_SEED_HISTORY_SIGNATURE,
        )
        had_fake = fake_qs.exists()
        fake_qs.delete()

        if agent.email == LEGACY_SEED_AGENT_DEFAULTS["email"]:
            agent.email = "matheus.msilva@experian.com"
        if agent.hire_date == LEGACY_SEED_AGENT_DEFAULTS["hire_date"]:
            agent.hire_date = date(2021, 6, 21)
        agent.save()

        if had_fake:
            closed = (
                AgentHistory.objects.filter(
                    agent=agent,
                    active=False,
                    final_date__isnull=False,
                    job_title__icontains="Analista de Planejamento",
                )
                .order_by("-start_date")
                .first()
            )
            if closed:
                closed.final_date = None
                closed.active = True
                closed.save(update_fields=["final_date", "active"])

        user = User.objects.filter(username=REAL_LAN_TOUCHED_BY_LEGACY_SEED).first()
        if user and (user.is_superuser or user.is_staff):
            user.is_superuser = False
            user.is_staff = False
            user.save(update_fields=["is_superuser", "is_staff"])
