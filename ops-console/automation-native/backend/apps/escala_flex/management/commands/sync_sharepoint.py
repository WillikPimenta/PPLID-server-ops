"""Carga inicial / sync SharePoint → PostgreSQL."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.escala_flex.models import (
    AgentStatus,
    BreakTime,
    Escala,
    HierarchicalLevel,
    JobActivity,
    Location,
    Schedule,
    StatusType,
)
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.status_types_bootstrap import ensure_status_types
from apps.workforce.models import Agent, AgentHistory


class Command(BaseCommand):
    help = "Sincroniza dados do Escala Flex (SharePoint JSON ou seed demo)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed",
            action="store_true",
            help="Popula dados demo para desenvolvimento.",
        )
        parser.add_argument(
            "--status-types-only",
            action="store_true",
            help="Cadastra apenas os tipos de status (Disponível, Ausente, etc.).",
        )
        parser.add_argument(
            "--from-dir",
            type=str,
            help="Diretório com exports JSON das listas SharePoint.",
        )
        parser.add_argument(
            "--rebuild-today",
            action="store_true",
            help="Reconstrói escala do dia após importação.",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Importação completa (ignora delta).",
        )

    def handle(self, *args, **options):
        if options["status_types_only"]:
            count = ensure_status_types()
            self.stdout.write(
                self.style.SUCCESS(f"Tipos de status: {count} cadastrados.")
            )
            return

        if options["seed"]:
            self._seed_demo()
        elif options["from_dir"]:
            self._import_from_dir(Path(options["from_dir"]))
        else:
            self.stdout.write(
                "Use --seed para dados demo ou --from-dir=<path> para JSON exportado."
            )
            self.stdout.write(
                "SharePoint live sync: configure SHAREPOINT_SITE_URL e credenciais (fase operacional)."
            )
            return

        if options["rebuild_today"] or options["seed"]:
            count = ScheduleTodayService.build_for_date(timezone.localdate())
            self.stdout.write(self.style.SUCCESS(f"Escala do dia: {count} registros."))

    def _seed_status_types(self):
        ensure_status_types()

    def _seed_demo(self):
        ensure_status_types()
        HierarchicalLevel.objects.get_or_create(
            name="N1 Atendimento",
            defaults={"active": True},
        )
        HierarchicalLevel.objects.get_or_create(
            name="N2 Retenção",
            defaults={"active": True},
        )
        Location.objects.get_or_create(
            city_name="Brasília",
            defaults={"state_name": "DF", "display_name": "Brasília", "active": True},
        )
        Location.objects.get_or_create(
            city_name="São Carlos",
            defaults={"state_name": "SP", "display_name": "São Carlos", "active": True},
        )
        JobActivity.objects.get_or_create(name="BrFlow", defaults={"active": True})
        JobActivity.objects.get_or_create(name="Confer", defaults={"active": True})

        leader, _ = Agent.objects.get_or_create(
            user_lan_id="c91763a",
            defaults={
                "full_name": "Líder Demo",
                "email": "lider@experian.com",
                "active": True,
            },
        )
        agents_data = [
            ("c91763a", "Líder Demo", "Planejamento", "Coordenador"),
            ("agent001", "Agente Um", "Operacional BrFlow", "Agente Backoffice I"),
            ("agent002", "Agente Dois", "Operacional BrFlow", "Agente Backoffice I"),
            ("agent003", "Agente Três", "Operacional Confer", "Agente Backoffice I"),
        ]
        today = timezone.localdate()
        brasilia = Location.objects.filter(city_name="Brasília").first()
        brflow = JobActivity.objects.filter(name="BrFlow").first()
        for lan, name, team, title in agents_data:
            agent, _ = Agent.objects.get_or_create(
                user_lan_id=lan,
                defaults={"full_name": name, "active": True},
            )
            AgentHistory.objects.filter(agent=agent, active=True).update(active=False)
            AgentHistory.objects.create(
                agent=agent,
                leader=leader if lan != "c91763a" else None,
                location="Brasília",
                team=team,
                job_title=title,
                job_activity="BrFlow" if "BrFlow" in team else "Confer",
                journey="08:00-17:00",
                team_sector="Operação",
                start_date=today,
                active=True,
            )
            BreakTime.objects.get_or_create(
                agent_lan_id=lan,
                defaults={"week": "12:00-13:00", "weekend": "", "active": True},
            )
            Schedule.objects.update_or_create(
                agent=agent,
                date=today,
                defaults={
                    "work_schedule": "08:00-17:00",
                    "working_hour": 8,
                    "work_day": True,
                },
            )
            Escala.objects.update_or_create(
                agent=agent,
                data=today,
                defaults={
                    "leader": leader if lan != "c91763a" else None,
                    "job_activity": brflow,
                    "location": brasilia,
                    "equipe": team,
                    "horario": "08:00 - 17:00",
                    "dia_escala": "08:00 - 17:00",
                },
            )
            st = StatusType.objects.get(pk=3)
            AgentStatus.objects.update_or_create(
                agent=agent,
                defaults={"status": st},
            )

        self.stdout.write(self.style.SUCCESS("Seed demo Escala Flex concluído."))

    def _import_from_dir(self, directory: Path):
        if not directory.is_dir():
            self.stderr.write(f"Diretório não encontrado: {directory}")
            return
        mapping = {
            "dimStatusType.json": self._import_status_types,
            "dimLocation.json": self._import_locations,
            "dimJobActivity.json": self._import_job_activities,
            "dimNivelHierarquico.json": self._import_nh,
            "dimBreakTime.json": self._import_break_times,
            "tblSchedule.json": self._import_schedules,
            "tblStatus.json": self._import_statuses,
        }
        for filename, handler in mapping.items():
            path = directory / filename
            if path.exists():
                handler(json.loads(path.read_text(encoding="utf-8")))
                self.stdout.write(f"Importado: {filename}")
        self.stdout.write(self.style.SUCCESS("Importação JSON concluída."))

    def _import_status_types(self, rows: list):
        self._seed_status_types()
        for row in rows:
            sid = int(row.get("ID") or row.get("id") or 0)
            if not sid:
                continue
            StatusType.objects.update_or_create(
                pk=sid,
                defaults={
                    "name": row.get("StatusName") or row.get("name", ""),
                    "color": row.get("Color", ""),
                    "active": bool(row.get("Active", True)),
                    "logged_in": bool(row.get("LoggedIn", False)),
                    "sharepoint_id": sid,
                },
            )

    def _import_locations(self, rows: list):
        for row in rows:
            Location.objects.update_or_create(
                sharepoint_id=row.get("ID"),
                defaults={
                    "city_name": row.get("CityName") or row.get("Title", ""),
                    "state_name": row.get("StateName", ""),
                    "display_name": row.get("DisplayName", ""),
                    "active": bool(row.get("Active", True)),
                },
            )

    def _import_job_activities(self, rows: list):
        for row in rows:
            JobActivity.objects.update_or_create(
                sharepoint_id=row.get("ID"),
                defaults={
                    "name": row.get("JobActivityName") or row.get("Title", ""),
                    "active": bool(row.get("Active", True)),
                },
            )

    def _import_nh(self, rows: list):
        for row in rows:
            HierarchicalLevel.objects.update_or_create(
                sharepoint_id=row.get("ID"),
                defaults={
                    "name": row.get("Nivel Hierarquico") or row.get("Title", ""),
                    "active": bool(row.get("Active", True)),
                },
            )

    def _import_break_times(self, rows: list):
        for row in rows:
            lan = row.get("UserLanID", "")
            if not lan:
                continue
            BreakTime.objects.update_or_create(
                sharepoint_id=row.get("ID"),
                defaults={
                    "agent_lan_id": lan.lower(),
                    "week": row.get("Week", ""),
                    "weekend": row.get("Weekend", ""),
                    "active": bool(row.get("Active", True)),
                },
            )

    def _import_schedules(self, rows: list):
        today = timezone.localdate()
        for row in rows:
            lan = (row.get("UserLanID") or "").lower()
            agent = Agent.objects.filter(user_lan_id__iexact=lan).first()
            if not agent:
                continue
            date_str = row.get("Date") or str(today)
            if "T" in date_str:
                date_str = date_str.split("T")[0]
            Schedule.objects.update_or_create(
                sharepoint_id=row.get("ID"),
                defaults={
                    "agent": agent,
                    "date": date_str,
                    "work_schedule": row.get("WorkSchedule", ""),
                    "working_hour": row.get("WorkingHour") or 0,
                    "overtime": bool(row.get("Overtime", False)),
                    "work_day": bool(row.get("WorkDay", True)),
                },
            )

    def _import_statuses(self, rows: list):
        for row in rows:
            lan = (row.get("UserLanID") or "").lower()
            agent = Agent.objects.filter(user_lan_id__iexact=lan).first()
            if not agent:
                continue
            status_id = int(row.get("Status") or 3)
            status_type = StatusType.objects.filter(pk=status_id).first()
            AgentStatus.objects.update_or_create(
                agent=agent,
                defaults={
                    "status": status_type,
                    "current_activity": row.get("CurrentActivity", ""),
                    "sharepoint_id": row.get("ID"),
                },
            )
