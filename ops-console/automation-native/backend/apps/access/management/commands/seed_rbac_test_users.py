"""Cria um usuário de teste local por perfil RBAC (ALL_ROLES)."""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access.constants import (
    ALL_ROLES,
    ROLE_ADM_PORTAL,
    ROLE_OP_AGENTE,
    ROLE_OP_GERENCIA,
    ROLE_OP_LIDER,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_ASSISTENTE,
    ROLE_PLAN_GERENCIA,
    ROLE_PROC_USUARIO,
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_AUDITORIA_FRAUD,
    ROLE_QUAL_CAPACITACAO,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_FRAUD,
    ROLE_QUAL_GERENCIA,
    is_role_group,
    role_group_name,
)
from apps.access.resolve import ensure_role_groups_exist
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()

DEFAULT_PASSWORD = "test12345"

# Um usuário fictício por perfil RBAC — LAN/username estáveis para login local.
RBAC_TEST_USERS: tuple[dict, ...] = (
    {
        "role": ROLE_PLAN_GERENCIA,
        "username": "teste.plan.gerencia",
        "full_name": "Teste Planejamento Gerência",
        "team": "Planejamento",
        "job_title": "Gerente de Planejamento",
        "area": "Planejamento",
    },
    {
        "role": ROLE_PLAN_ANALISTA,
        "username": "teste.plan.analista",
        "full_name": "Teste Planejamento Analista",
        "team": "Planejamento",
        "job_title": "Analista de Planejamento",
        "area": "Planejamento",
    },
    {
        "role": ROLE_PLAN_ASSISTENTE,
        "username": "teste.plan.assistente",
        "full_name": "Teste Planejamento Assistente",
        "team": "Planejamento",
        "job_title": "Assistente de Planejamento",
        "area": "Planejamento",
    },
    {
        "role": ROLE_OP_GERENCIA,
        "username": "teste.op.gerencia",
        "full_name": "Teste Operação Gerência",
        "team": "Operacional/Alpha",
        "job_title": "Gerente de Operação",
        "area": "Operação",
    },
    {
        "role": ROLE_OP_LIDER,
        "username": "teste.op.lider",
        "full_name": "Teste Operação Líder",
        "team": "Operacional/Alpha",
        "job_title": "Líder de Operação",
        "area": "Operação",
    },
    {
        "role": ROLE_OP_AGENTE,
        "username": "teste.op.agente",
        "full_name": "Teste Operação Agente",
        "team": "Operacional/Alpha",
        "job_title": "Agente Backoffice I",
        "area": "Operação",
    },
    {
        "role": ROLE_PROC_USUARIO,
        "username": "teste.proc.usuario",
        "full_name": "Teste Processos Usuário",
        "team": "Processos",
        "job_title": "Analista de Processos",
        "area": "Processos",
    },
    {
        "role": ROLE_QUAL_AUDITORIA_FRAUD,
        "username": "teste.qual.aud.fraud",
        "full_name": "Teste Qual. Auditoria Fraud",
        "team": "Qualidade",
        "job_title": "Auditor Fraud",
        "area": "Qualidade",
    },
    {
        "role": ROLE_QUAL_AUDITORIA_COMPLIANCE,
        "username": "teste.qual.aud.comp",
        "full_name": "Teste Qual. Auditoria Compliance",
        "team": "Qualidade",
        "job_title": "Auditor Compliance",
        "area": "Qualidade",
    },
    {
        "role": ROLE_QUAL_CONTESTACAO_FRAUD,
        "username": "teste.qual.cont.fraud",
        "full_name": "Teste Qual. Contestação Fraud",
        "team": "Qualidade",
        "job_title": "Analista Contestação Fraud",
        "area": "Qualidade",
    },
    {
        "role": ROLE_QUAL_CONTESTACAO_COMPLIANCE,
        "username": "teste.qual.cont.comp",
        "full_name": "Teste Qual. Contestação Compliance",
        "team": "Qualidade",
        "job_title": "Analista Contestação Compliance",
        "area": "Qualidade",
    },
    {
        "role": ROLE_QUAL_CAPACITACAO,
        "username": "teste.qual.capacitacao",
        "full_name": "Teste Qual. Capacitação",
        "team": "Qualidade",
        "job_title": "Assistente de Capacitação",
        "area": "Qualidade",
    },
    {
        "role": ROLE_QUAL_GERENCIA,
        "username": "teste.qual.gerencia",
        "full_name": "Teste Qualidade Gerência",
        "team": "Qualidade",
        "job_title": "Gerente de Qualidade",
        "area": "Qualidade",
    },
    {
        "role": ROLE_ADM_PORTAL,
        "username": "teste.adm.portal",
        "full_name": "Teste Administração Portal",
        "team": "TI",
        "job_title": "Administrador do Portal",
        "area": "TI / ADM",
    },
)

RBAC_TEST_USERNAMES = frozenset(item["username"] for item in RBAC_TEST_USERS)


class Command(BaseCommand):
    help = (
        "Cria usuários de teste locais (1 por perfil RBAC em ALL_ROLES). "
        "Idempotente; não altera colaboradores/LAN IDs reais."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help=f"Senha comum dos usuários de teste (padrão: {DEFAULT_PASSWORD})",
        )
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Redefine a senha mesmo se a conta já existir",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista o que seria criado/atualizado sem gravar",
        )

    def handle(self, *args, **options):
        password = options["password"]
        reset_passwords = options["reset_passwords"]
        dry_run = options["dry_run"]

        if dry_run:
            for item in RBAC_TEST_USERS:
                exists = User.objects.filter(username=item["username"]).exists()
                action = "atualizar" if exists else "criar"
                self.stdout.write(
                    f"[dry-run] {action}: {item['username']} → role:{item['role']} ({item['area']})"
                )
            return

        with transaction.atomic():
            ensure_role_groups_exist()
            agents = self._ensure_agents()
            created = updated = 0
            for item in RBAC_TEST_USERS:
                was_created = self._ensure_user(
                    item=item,
                    agent=agents[item["username"]],
                    password=password,
                    reset_password=reset_passwords,
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f"Usuários RBAC de teste: {created} criados, {updated} atualizados."))
        self.stdout.write("")
        self.stdout.write("Logins (senha comum abaixo):")
        for item in RBAC_TEST_USERS:
            self.stdout.write(
                f"  {item['username']:<24}  role:{item['role']:<16}  {item['area']}"
            )
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(f"Senha: {password}"))
        self.stdout.write("must_change_password=False (prontos para login local).")

    def _ensure_agents(self) -> dict[str, Agent]:
        today = date.today()
        start = today - timedelta(days=90)
        agents: dict[str, Agent] = {}

        for item in RBAC_TEST_USERS:
            lan = item["username"]
            agent, created = Agent.objects.get_or_create(
                user_lan_id=lan,
                defaults={
                    "full_name": item["full_name"],
                    "email": f"{lan}@teste.local",
                    "hire_date": date(2024, 1, 1),
                    "active": True,
                },
            )
            if not created:
                agent.full_name = item["full_name"]
                agent.email = f"{lan}@teste.local"
                agent.active = True
                agent.save(update_fields=["full_name", "email", "active"])
            agents[lan] = agent

            history = (
                AgentHistory.objects.filter(agent=agent, active=True, final_date__isnull=True)
                .order_by("-start_date")
                .first()
            )
            history_defaults = {
                "team": item["team"],
                "job_title": item["job_title"],
                "location": "Brasília",
                "journey": "08:00-17:00",
                "team_sector": item["area"],
                "active": True,
                "final_date": None,
            }
            if history is None:
                AgentHistory.objects.create(agent=agent, start_date=start, **history_defaults)
            else:
                for field, value in history_defaults.items():
                    setattr(history, field, value)
                history.save(update_fields=list(history_defaults.keys()))

        # Relação líder → agente (escopo team)
        lider = agents.get("teste.op.lider")
        agente = agents.get("teste.op.agente")
        if lider and agente:
            hist = (
                AgentHistory.objects.filter(agent=agente, active=True, final_date__isnull=True)
                .order_by("-start_date")
                .first()
            )
            if hist and hist.leader_id != lider.id:
                hist.leader = lider
                hist.save(update_fields=["leader"])

        return agents

    def _ensure_user(
        self,
        *,
        item: dict,
        agent: Agent,
        password: str,
        reset_password: bool,
    ) -> bool:
        username = item["username"]
        email = f"{username}@teste.local"
        user = User.objects.filter(username=username).first()
        created = False

        if user is None:
            user = User(
                username=username,
                email=email,
                is_active=True,
                is_staff=item["role"] == ROLE_ADM_PORTAL,
                must_change_password=False,
            )
            user.set_password(password)
            user.save()
            created = True
        else:
            user.email = email
            user.is_active = True
            user.is_staff = item["role"] == ROLE_ADM_PORTAL
            user.must_change_password = False
            if reset_password:
                user.set_password(password)
                user.save(
                    update_fields=[
                        "email",
                        "is_active",
                        "is_staff",
                        "must_change_password",
                        "password",
                        "updated_at",
                    ]
                )
            else:
                user.save(
                    update_fields=[
                        "email",
                        "is_active",
                        "is_staff",
                        "must_change_password",
                        "updated_at",
                    ]
                )

        profile = UserProfile.objects.filter(user=user).first()
        if profile is None:
            # Libera o agente se estiver preso a outro user de teste antigo
            UserProfile.objects.filter(agent=agent).exclude(user=user).delete()
            UserProfile.objects.create(user=user, agent=agent)
        elif profile.agent_id != agent.id:
            UserProfile.objects.filter(agent=agent).exclude(user=user).delete()
            profile.agent = agent
            profile.save(update_fields=["agent"])

        role_groups = {g for g in user.groups.all() if is_role_group(g.name)}
        target = Group.objects.get(name=role_group_name(item["role"]))
        for group in role_groups:
            if group.id != target.id:
                user.groups.remove(group)
        user.groups.add(target)

        return created
