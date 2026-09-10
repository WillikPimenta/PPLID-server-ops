# -*- coding: utf-8 -*-
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.suporte_claro.models import SuporteClaroRegistro

User = get_user_model()

DEMO_PREFIX = "DEMO-"

DEMO_ROWS = [
    {
        "protocolo": f"{DEMO_PREFIX}20260703001",
        "status": SuporteClaroRegistro.STATUS_ABERTO,
        "origem": SuporteClaroRegistro.ORIGEM_TEAMS,
        "sent_by": "Ana Costa (Teams)",
        "irregularidade": "Cliente reportou divergência no prazo de retorno do protocolo.",
        "avaliacao": "",
        "days_ago": 0,
    },
    {
        "protocolo": f"{DEMO_PREFIX}20260703002",
        "status": SuporteClaroRegistro.STATUS_ABERTO,
        "origem": SuporteClaroRegistro.ORIGEM_EMAIL,
        "sent_by": "suporte.cliente@empresa.com",
        "irregularidade": "E-mail com print anexo — documento ilegível na contestação.",
        "avaliacao": "",
        "days_ago": 1,
    },
    {
        "protocolo": f"{DEMO_PREFIX}20260703003",
        "status": SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
        "origem": SuporteClaroRegistro.ORIGEM_LIGACAO,
        "sent_by": "Carlos Mendes",
        "irregularidade": "Ligação informando atraso na análise BSB — cliente insatisfeito.",
        "avaliacao": "Em contato com a operação para validar SLA. Aguardando retorno do líder.",
        "days_ago": 1,
    },
    {
        "protocolo": f"{DEMO_PREFIX}20260703004",
        "status": SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
        "origem": SuporteClaroRegistro.ORIGEM_TEAMS,
        "sent_by": "Patricia Lima",
        "irregularidade": "Solicitação de reanálise — cenário de fraude não aplicado.",
        "avaliacao": "Consultando histórico no BRFlow. Previsão de retorno em 1h.",
        "days_ago": 2,
    },
    {
        "protocolo": f"{DEMO_PREFIX}20260703005",
        "status": SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
        "origem": SuporteClaroRegistro.ORIGEM_EMAIL,
        "sent_by": "ged.operacao@empresa.com",
        "irregularidade": "Contestação GED — irregularidade de documento duplicado.",
        "avaliacao": "Escalado para equipe de qualidade.",
        "days_ago": 3,
    },
    {
        "protocolo": f"{DEMO_PREFIX}20260703006",
        "status": SuporteClaroRegistro.STATUS_CONCLUIDO,
        "origem": SuporteClaroRegistro.ORIGEM_LIGACAO,
        "sent_by": "Roberto Alves",
        "irregularidade": "Cliente questionou indicador de falha crítica do mês.",
        "avaliacao": "Explicado critério de Data de Análise vs Auditoria. Cliente concordou com o fechamento.",
        "days_ago": 4,
    },
    {
        "protocolo": f"{DEMO_PREFIX}20260703007",
        "status": SuporteClaroRegistro.STATUS_CONCLUIDO,
        "origem": SuporteClaroRegistro.ORIGEM_TEAMS,
        "sent_by": "Fernanda Rocha",
        "irregularidade": "Dúvida sobre workflow replicado incorretamente.",
        "avaliacao": "Correção aplicada na base. Retorno enviado ao cliente via Teams.",
        "days_ago": 5,
    },
    {
        "protocolo": f"{DEMO_PREFIX}20260703008",
        "status": SuporteClaroRegistro.STATUS_CONCLUIDO,
        "origem": SuporteClaroRegistro.ORIGEM_EMAIL,
        "sent_by": "cliente.vip@empresa.com",
        "irregularidade": "Pedido de evidência para auditoria externa.",
        "avaliacao": "Evidências anexadas e compartilhadas por e-mail. Caso encerrado.",
        "days_ago": 6,
    },
]


class Command(BaseCommand):
    help = "Cria ou remove registros fictícios do Suporte Claro (prefixo DEMO-)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove todos os registros com protocolo DEMO-*",
        )

    def _resolve_user(self):
        user = User.objects.filter(is_superuser=True, is_active=True).first()
        if user:
            return user
        user = User.objects.filter(is_staff=True, is_active=True).first()
        if user:
            return user
        user = User.objects.filter(is_active=True).first()
        if user:
            return user
        return User.objects.create_user(
            username="suporte.claro.demo",
            email="suporte.claro.demo@local",
            password="dev12345",
            is_staff=True,
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = SuporteClaroRegistro.objects.filter(protocolo__startswith=DEMO_PREFIX).delete()
            self.stdout.write(self.style.SUCCESS(f"Removidos {deleted} registro(s) DEMO."))
            return

        user = self._resolve_user()
        now = timezone.now()
        created = 0
        skipped = 0

        for row in DEMO_ROWS:
            if SuporteClaroRegistro.objects.filter(protocolo=row["protocolo"]).exists():
                skipped += 1
                continue
            SuporteClaroRegistro.objects.create(
                protocolo=row["protocolo"],
                irregularidade=row["irregularidade"],
                avaliacao=row["avaliacao"],
                received_at=now - timedelta(days=row["days_ago"]),
                sent_by=row["sent_by"],
                origem=row["origem"],
                status=row["status"],
                created_by=user,
            )
            created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Criados {created} registro(s) DEMO (usuário: {user.username}). "
                f"Ignorados {skipped} já existentes. "
                f"Para apagar: python manage.py seed_suporte_claro_demo --clear"
            )
        )
