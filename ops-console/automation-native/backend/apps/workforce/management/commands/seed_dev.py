from datetime import date, timedelta

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.falhas_criticas.constants import GROUP_GLOBAL
from apps.workforce.models import Agent, AgentHistory, UserProfile

# Apenas contas/colaboradores fictícios de desenvolvimento — nunca LAN IDs reais.
DEV_USERNAMES = frozenset({"gerente.dev"})
DEV_AGENT_LAN_IDS = frozenset({"gerente001", "operador001"})


class Command(BaseCommand):
    help = (
        "Cria dados mínimos de desenvolvimento (login gerente.dev + 2 colaboradores fictícios). "
        "Idempotente e seguro: não altera headcount real já importado."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Cria/recria colaboradores fictícios de dev "
                f"({', '.join(sorted(DEV_AGENT_LAN_IDS))}). "
                "Não altera LAN IDs reais."
            ),
        )

    def handle(self, *args, **options):
        force = options["force"]
        real_agent_count = Agent.objects.exclude(
            user_lan_id__in=DEV_AGENT_LAN_IDS
        ).count()

        with transaction.atomic():
            self._ensure_dev_login()

        if real_agent_count > 0 and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"Headcount real detectado ({real_agent_count} colaboradores). "
                    "Colaboradores fictícios (gerente001/operador001) não foram criados/alterados.\n"
                    "Para adicionar os fictícios de dev: python manage.py seed_dev --force\n"
                    "Para restaurar snapshot completo: python manage.py restore_db_snapshot --flush"
                )
            )
            self.stdout.write(self.style.SUCCESS("Login gerente.dev verificado."))
            return

        with transaction.atomic():
            self._ensure_dev_agents(force=force or real_agent_count == 0)

        self.stdout.write(self.style.SUCCESS("Seed de desenvolvimento concluído."))

    def _ensure_dev_login(self) -> None:
        global_group, _ = Group.objects.get_or_create(name=GROUP_GLOBAL)

        manager_user, created = User.objects.get_or_create(
            username="gerente.dev",
            defaults={
                "email": "gerente.dev@empresa.local",
                "is_staff": True,
            },
        )
        if created:
            manager_user.set_password("dev12345")
            manager_user.save()
            self.stdout.write(self.style.SUCCESS("Usuário gerente.dev criado."))
        else:
            self.stdout.write("Usuário gerente.dev já existe (senha preservada).")

        manager_user.groups.add(global_group)

        manager_agent = Agent.objects.filter(user_lan_id="gerente001").first()
        if manager_agent:
            UserProfile.objects.get_or_create(
                user=manager_user,
                defaults={"agent": manager_agent},
            )

    def _ensure_dev_agents(self, *, force: bool) -> None:
        if not force and Agent.objects.exclude(user_lan_id__in=DEV_AGENT_LAN_IDS).exists():
            raise CommandError("Chamada interna inválida: headcount real sem permissão.")

        today = date.today()
        past_start = today - timedelta(days=365)
        past_end = today - timedelta(days=30)
        current_start = today - timedelta(days=29)

        manager_agent, agent_created = Agent.objects.get_or_create(
            user_lan_id="gerente001",
            defaults={
                "full_name": "Ana Gerente Silva",
                "email": "ana.gerente@empresa.local",
                "hire_date": date(2020, 3, 15),
                "time_tracking_id": "TT-1001",
                "oracle_id": "ORA-1001",
                "active": True,
            },
        )
        if agent_created:
            self.stdout.write(self.style.SUCCESS("Colaborador fictício gerente001 criado."))

        manager_user = User.objects.get(username="gerente.dev")
        UserProfile.objects.get_or_create(
            user=manager_user,
            defaults={"agent": manager_agent},
        )

        operator_agent, operator_created = Agent.objects.get_or_create(
            user_lan_id="operador001",
            defaults={
                "full_name": "Carlos Operador Santos",
                "email": "carlos.operador@empresa.local",
                "hire_date": date(2022, 8, 1),
                "time_tracking_id": "TT-2001",
                "oracle_id": "ORA-2001",
                "active": True,
            },
        )
        if operator_created:
            self.stdout.write(self.style.SUCCESS("Colaborador fictício operador001 criado."))

        if force:
            self._reset_dev_fixture_histories(
                manager_agent=manager_agent,
                operator_agent=operator_agent,
                past_start=past_start,
                past_end=past_end,
                current_start=current_start,
                today=today,
            )
        else:
            self._ensure_minimal_dev_histories(
                manager_agent=manager_agent,
                operator_agent=operator_agent,
                current_start=current_start,
            )

    def _ensure_minimal_dev_histories(
        self,
        *,
        manager_agent: Agent,
        operator_agent: Agent,
        current_start: date,
    ) -> None:
        if not AgentHistory.objects.filter(
            agent=manager_agent,
            active=True,
            final_date__isnull=True,
        ).exists():
            AgentHistory.objects.create(
                agent=manager_agent,
                start_date=current_start,
                location="Brasília",
                team="Planejamento",
                job_title="Coordenador",
                job_activity="BrFlow",
                journey="08:00-17:00",
                team_sector="Operação",
                final_date=None,
                active=True,
            )

        if not AgentHistory.objects.filter(
            agent=operator_agent,
            active=True,
            final_date__isnull=True,
        ).exists():
            AgentHistory.objects.create(
                agent=operator_agent,
                start_date=current_start,
                leader=manager_agent,
                location="São Paulo",
                team="Operações Beta",
                job_title="Analista Pl",
                journey="CLT 8h",
                band="B2",
                inss_type="CLT",
                final_date=None,
                active=True,
                jira=True,
            )

    def _reset_dev_fixture_histories(
        self,
        *,
        manager_agent: Agent,
        operator_agent: Agent,
        past_start: date,
        past_end: date,
        current_start: date,
        today: date,
    ) -> None:
        AgentHistory.objects.filter(
            agent=manager_agent,
            active=True,
            final_date__isnull=True,
        ).update(active=False, final_date=today - timedelta(days=1))

        AgentHistory.objects.update_or_create(
            agent=manager_agent,
            start_date=current_start,
            defaults={
                "location": "Brasília",
                "team": "Planejamento",
                "job_title": "Coordenador",
                "job_activity": "BrFlow",
                "journey": "08:00-17:00",
                "team_sector": "Operação",
                "final_date": None,
                "active": True,
            },
        )

        AgentHistory.objects.update_or_create(
            agent=operator_agent,
            start_date=past_start,
            defaults={
                "leader": manager_agent,
                "facilitator": manager_agent,
                "location": "São Paulo",
                "team": "Operações Alpha",
                "job_title": "Analista Jr",
                "journey": "CLT 8h",
                "band": "B1",
                "inss_type": "CLT",
                "start_date": past_start,
                "final_date": past_end,
                "active": False,
                "formalization": True,
            },
        )

        AgentHistory.objects.filter(
            agent=operator_agent,
            active=True,
            final_date__isnull=True,
        ).exclude(start_date=current_start).update(
            active=False,
            final_date=past_end,
        )

        AgentHistory.objects.update_or_create(
            agent=operator_agent,
            start_date=current_start,
            defaults={
                "leader": manager_agent,
                "location": "São Paulo",
                "team": "Operações Beta",
                "job_title": "Analista Pl",
                "journey": "CLT 8h",
                "band": "B2",
                "inss_type": "CLT",
                "final_date": None,
                "active": True,
                "jira": True,
            },
        )
