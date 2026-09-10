from pathlib import Path

from django.core.management.base import BaseCommand

from apps.workforce.excel_utils import (
    DEFAULT_XLSX,
    SHEET_HISTORY_ALIASES,
    SHEET_USERS_ALIASES,
    read_sheet,
)
from apps.workforce.models import Agent, AgentHistory


class Command(BaseCommand):
    help = "Inspeciona BASE.xlsx e compara colunas com os modelos Django."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=str(DEFAULT_XLSX),
            help="Caminho do arquivo Excel (padrão: data/BASE.xlsx)",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"Arquivo não encontrado: {path}"))
            return

        users_df = read_sheet(path, SHEET_USERS_ALIASES)
        history_df = read_sheet(path, SHEET_HISTORY_ALIASES)

        self.stdout.write(self.style.SUCCESS(f"Arquivo: {path}"))
        self.stdout.write(f"\nAba colaboradores ({len(users_df)} linhas):")
        for col in users_df.columns:
            mapped = self._map_agent_column(col)
            self.stdout.write(f"  - {col} -> {mapped or '(sem mapeamento)'}")

        self.stdout.write(f"\nAba histórico ({len(history_df)} linhas):")
        model_fields = {f.name for f in AgentHistory._meta.fields}
        for col in history_df.columns:
            mapped = self._map_history_column(col)
            extra = ""
            if mapped and mapped not in model_fields and mapped not in (
                "leader",
                "facilitator",
                "agent",
            ):
                extra = " [NOVO CAMPO NECESSÁRIO]"
            self.stdout.write(f"  - {col} -> {mapped or '(sem mapeamento)'}{extra}")

        self.stdout.write("\nCampos do modelo Agent:")
        for f in Agent._meta.fields:
            if not f.auto_created:
                self.stdout.write(f"  - {f.name}")

        self.stdout.write("\nCampos do modelo AgentHistory:")
        for f in AgentHistory._meta.fields:
            if not f.auto_created and f.name not in ("id", "created_at"):
                self.stdout.write(f"  - {f.name}")

    def _map_agent_column(self, col: str) -> str | None:
        mapping = {
            "full_name": "full_name",
            "user_lan_id": "user_lan_id",
            "email": "email",
            "hire_date": "hire_date",
            "time_tracking_id": "time_tracking_id",
            "oracle_id": "oracle_id",
            "active": "active",
        }
        return mapping.get(col)

    def _map_history_column(self, col: str) -> str | None:
        mapping = {
            "user_lan_id": "agent (FK via user_lan_id)",
            "full_name": "agent (FK via full_name)",
            "leader_id": "leader (FK via nome)",
            "leader": "leader (FK via nome)",
            "facilitador_id": "facilitator (FK via nome)",
            "facilitator_id": "facilitator (FK via nome)",
            "location": "location",
            "team": "team",
            "job_title": "job_title",
            "jobactivity": "job_activity",
            "journey": "journey",
            "start_date": "start_date",
            "final_date": "final_date",
            "productivity_discount": "productivity_discount",
            "pcd": "pcd",
            "jira": "jira",
            "team_sector": "team_sector",
            "job_title_sector": "job_title_sector",
            "job_title_activity": "job_title_activity",
            "journey_shift": "journey_shift",
            "formalization": "formalization",
            "band": "band",
            "active": "active",
            "inss_type": "inss_type",
            "external_movement_type": "external_movement_type",
        }
        return mapping.get(col)
