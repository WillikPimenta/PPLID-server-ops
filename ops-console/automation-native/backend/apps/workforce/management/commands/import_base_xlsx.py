import csv
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.workforce.excel_utils import (
    AGENT_COLUMN_ALIASES,
    AGENT_NAME_ALIASES,
    DEFAULT_XLSX,
    FACILITATOR_COLUMN_ALIASES,
    LEADER_COLUMN_ALIASES,
    SHEET_HISTORY_ALIASES,
    SHEET_USERS_ALIASES,
    is_empty,
    normalize_name,
    parse_bool,
    parse_date,
    parse_decimal,
    pick_column,
    read_sheet,
)
from apps.workforce.models import Agent, AgentHistory, UserProfile


class Command(BaseCommand):
    help = (
        "Importa data/BASE.xlsx: USERS -> agent, USER_HISTORY -> agent_history. "
        "Resolve leader_id e facilitador_id (nomes) para FK em agent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=str(DEFAULT_XLSX),
            help="Caminho do Excel (padrão: data/BASE.xlsx)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Valida sem gravar no banco",
        )
        parser.add_argument(
            "--report",
            default=str(DEFAULT_XLSX.parent / "import_report.csv"),
            help="CSV com linhas ignoradas ou com avisos",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"Arquivo não encontrado: {path}"))
            return

        report_path = Path(options["report"])
        report_rows: list[dict] = []

        users_df = read_sheet(path, SHEET_USERS_ALIASES)
        history_df = read_sheet(path, SHEET_HISTORY_ALIASES)

        agent_lan_col = pick_column(history_df, AGENT_COLUMN_ALIASES)
        agent_name_col = pick_column(history_df, AGENT_NAME_ALIASES)
        leader_col = pick_column(history_df, LEADER_COLUMN_ALIASES)
        facilitator_col = pick_column(history_df, FACILITATOR_COLUMN_ALIASES)

        if not leader_col:
            self.stderr.write(self.style.ERROR("Coluna leader_id não encontrada no histórico."))
            return

        self.stdout.write(f"Colaboradores: {len(users_df)} linhas")
        self.stdout.write(f"Histórico: {len(history_df)} linhas")

        if options["dry_run"]:
            self._dry_run(users_df, history_df, agent_lan_col, agent_name_col, leader_col)
            return

        with transaction.atomic():
            self._clear_data()
            by_lan, by_name = self._import_agents(users_df, report_rows)
            self._import_history(
                history_df,
                by_lan,
                by_name,
                agent_lan_col,
                agent_name_col,
                leader_col,
                facilitator_col,
                users_df,
                report_rows,
            )

        self._write_report(report_path, report_rows)
        self.stdout.write(self.style.SUCCESS("Importação concluída."))
        self.stdout.write(f"Agentes: {Agent.objects.count()}")
        self.stdout.write(f"Históricos: {AgentHistory.objects.count()}")
        if report_rows:
            self.stdout.write(
                self.style.WARNING(f"{len(report_rows)} avisos em {report_path}")
            )

    def _clear_data(self):
        AgentHistory.objects.all().delete()
        UserProfile.objects.all().delete()
        Agent.objects.all().delete()
        User.objects.all().delete()
        self.stdout.write("Tabelas limpas (substituição total).")

    def _import_agents(self, users_df, report_rows: list[dict]):
        by_lan: dict[str, Agent] = {}
        by_name: dict[str, list[Agent]] = {}

        for idx, row in users_df.iterrows():
            lan = row.get("user_lan_id")
            full_name = row.get("full_name")
            if is_empty(lan) or is_empty(full_name):
                report_rows.append(
                    {
                        "sheet": "USERS",
                        "row": idx + 2,
                        "issue": "user_lan_id ou full_name vazio",
                    }
                )
                continue

            lan_key = str(lan).strip().lower()
            agent = Agent(
                full_name=str(full_name).strip(),
                user_lan_id=lan_key,
                email="" if is_empty(row.get("email")) else str(row.get("email")).strip(),
                hire_date=parse_date(row.get("hire_date")),
                time_tracking_id=self._str_or_blank(row.get("time_tracking_id")),
                oracle_id=self._str_or_blank(row.get("oracle_id")),
                active=parse_bool(row.get("active")),
            )
            agent.save()

            by_lan[lan_key] = agent
            name_key = normalize_name(agent.full_name)
            by_name.setdefault(name_key, []).append(agent)

        return by_lan, by_name

    def _import_history(
        self,
        history_df,
        by_lan: dict[str, Agent],
        by_name: dict[str, list[Agent]],
        agent_lan_col: str | None,
        agent_name_col: str | None,
        leader_col: str,
        facilitator_col: str | None,
        users_df,
        report_rows: list[dict],
    ):
        zip_map = self._build_zip_agent_map(history_df, facilitator_col, users_df, by_lan)
        leader_col_for_agent = leader_col

        created = 0
        for idx, row in history_df.iterrows():
            excel_row = idx + 2
            agent = self._resolve_row_agent(
                row,
                agent_lan_col,
                agent_name_col,
                zip_map,
                by_lan,
                by_name,
                excel_row,
                report_rows,
                leader_col_for_agent,
                facilitator_col,
            )
            if not agent:
                continue

            leader = self._resolve_agent_fk(
                row.get(leader_col),
                by_name,
                excel_row,
                "leader",
                report_rows,
            )
            facilitator = None
            if facilitator_col:
                facilitator = self._resolve_agent_fk(
                    row.get(facilitator_col),
                    by_name,
                    excel_row,
                    "facilitator",
                    report_rows,
                )

            if leader and agent and leader.pk == agent.pk:
                leader = None

            start_date = parse_date(row.get("start_date"))
            if not start_date:
                report_rows.append(
                    {
                        "sheet": "USER_HISTORY",
                        "row": excel_row,
                        "issue": "start_date inválida",
                    }
                )
                continue

            is_active = self._parse_active(row.get("active"))
            final_date = parse_date(row.get("final_date"))
            if is_active and final_date is None:
                AgentHistory.objects.filter(
                    agent=agent, active=True, final_date__isnull=True
                ).update(active=False)

            history = AgentHistory(
                agent=agent,
                leader=leader,
                facilitator=facilitator,
                location=self._str_or_blank(row.get("location")),
                team=self._str_or_blank(row.get("team")),
                job_title=self._str_or_blank(row.get("job_title")),
                job_activity=self._str_or_blank(
                    row.get("jobactivity") or row.get("job_activity")
                ),
                journey=self._str_or_blank(row.get("journey")),
                team_sector=self._str_or_blank(row.get("team_sector")),
                job_title_sector=self._str_or_blank(row.get("job_title_sector")),
                job_title_activity=self._str_or_blank(row.get("job_title_activity")),
                journey_shift=self._str_or_blank(row.get("journey_shift")),
                inss_type=self._str_or_blank(row.get("inss_type")),
                external_movement_type=self._str_or_blank(
                    row.get("external_movement_type")
                ),
                start_date=start_date,
                final_date=final_date,
                productivity_discount=parse_decimal(row.get("productivity_discount")),
                pcd=parse_bool(row.get("pcd")),
                jira=self._str_or_blank(row.get("jira")),
                formalization=self._str_or_blank(row.get("formalization")),
                band=self._str_or_blank(row.get("band")),
                active=is_active,
            )
            history.save()
            created += 1

        self._normalize_active_histories()
        self.stdout.write(f"Históricos inseridos: {created}")

    def _build_zip_agent_map(
        self, history_df, facilitator_col: str | None, users_df, by_lan: dict[str, Agent]
    ) -> dict[int, Agent]:
        """712 linhas sem facilitador: associa 1:1 com colaboradores ordenados por user_lan_id."""
        if not facilitator_col:
            return {}

        empty_mask = history_df[facilitator_col].apply(is_empty)
        empty_rows = history_df[empty_mask].sort_values("start_date")
        users_sorted = users_df.sort_values("user_lan_id")

        mapping: dict[int, Agent] = {}
        for hist_idx, (_, hist_row) in enumerate(empty_rows.iterrows()):
            if hist_idx >= len(users_sorted):
                break
            user_row = users_sorted.iloc[hist_idx]
            lan = str(user_row["user_lan_id"]).strip().lower()
            agent = by_lan.get(lan)
            if agent:
                mapping[int(hist_row.name)] = agent
        return mapping

    def _resolve_row_agent(
        self,
        row,
        agent_lan_col: str | None,
        agent_name_col: str | None,
        zip_map: dict[int, Agent],
        by_lan: dict[str, Agent],
        by_name: dict[str, list[Agent]],
        excel_row: int,
        report_rows: list[dict],
        leader_col: str | None = None,
        facilitator_col: str | None = None,
    ) -> Agent | None:
        if agent_lan_col and not is_empty(row.get(agent_lan_col)):
            lan = str(row.get(agent_lan_col)).strip().lower()
            agent = by_lan.get(lan)
            if agent:
                return agent
            report_rows.append(
                {
                    "sheet": "USER_HISTORY",
                    "row": excel_row,
                    "issue": f"user_lan_id não encontrado: {lan}",
                }
            )
            return None

        if agent_name_col and not is_empty(row.get(agent_name_col)):
            return self._resolve_agent_fk(
                row.get(agent_name_col),
                by_name,
                excel_row,
                "agent",
                report_rows,
            )

        row_index = int(row.name)
        if row_index in zip_map:
            return zip_map[row_index]

        has_facilitator = facilitator_col and not is_empty(row.get(facilitator_col))
        if has_facilitator and leader_col and not is_empty(row.get(leader_col)):
            agent = self._resolve_agent_fk(
                row.get(leader_col),
                by_name,
                excel_row,
                "agent (via leader_id)",
                report_rows,
                required=False,
            )
            if agent:
                return agent

        report_rows.append(
            {
                "sheet": "USER_HISTORY",
                "row": excel_row,
                "issue": "colaborador não identificado (adicione user_lan_id na planilha)",
            }
        )
        return None

    def _resolve_agent_fk(
        self,
        value,
        by_name: dict[str, list[Agent]],
        excel_row: int,
        role: str,
        report_rows: list[dict],
        required: bool = True,
    ) -> Agent | None:
        if is_empty(value):
            return None

        key = normalize_name(str(value))
        matches = by_name.get(key, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            if required:
                report_rows.append(
                    {
                        "sheet": "USER_HISTORY",
                        "row": excel_row,
                        "issue": f"{role}: nome ambíguo ({value})",
                    }
                )
            return None

        if required:
            report_rows.append(
                {
                    "sheet": "USER_HISTORY",
                    "row": excel_row,
                    "issue": f"{role}: nome não encontrado em USERS ({value})",
                }
            )
        return None

    def _normalize_active_histories(self):
        """Garante no máximo um período aberto (active=True, final_date null) por agente."""
        for agent in Agent.objects.iterator():
            open_rows = list(
                agent.history.filter(active=True, final_date__isnull=True).order_by(
                    "-start_date"
                )
            )
            if len(open_rows) <= 1:
                continue
            for extra in open_rows[1:]:
                extra.active = False
                if extra.final_date is None and open_rows[0].start_date:
                    extra.final_date = open_rows[0].start_date
                extra.save(update_fields=["active", "final_date"])

    def _parse_active(self, value) -> bool:
        if value is None:
            return False
        try:
            import pandas as pd

            if pd.isna(value):
                return False
        except (TypeError, ValueError, ImportError):
            pass
        return parse_bool(value)

    def _str_or_blank(self, value) -> str:
        if is_empty(value):
            return ""
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value).strip()

    def _dry_run(self, users_df, history_df, agent_lan_col, agent_name_col, leader_col):
        self.stdout.write(self.style.WARNING("Modo dry-run — nada será gravado."))
        if agent_lan_col:
            self.stdout.write(f"Coluna de agente: {agent_lan_col}")
        elif agent_name_col:
            self.stdout.write(f"Coluna de nome do agente: {agent_name_col}")
        else:
            empty_facil = 0
            facil_col = pick_column(history_df, FACILITATOR_COLUMN_ALIASES)
            if facil_col:
                empty_facil = history_df[facil_col].apply(is_empty).sum()
            self.stdout.write(
                f"Sem coluna de agente; será usado pareamento para {empty_facil} "
                f"linhas sem facilitador (1:1 com USERS ordenado por user_lan_id)."
            )
            self.stdout.write(
                f"Linhas restantes ({len(history_df) - empty_facil}) precisam de "
                "user_lan_id/full_name ou serão ignoradas."
            )
        leaders = history_df[leader_col].dropna().nunique()
        self.stdout.write(f"Líderes distintos no histórico: {leaders}")

    def _write_report(self, path: Path, rows: list[dict]):
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["sheet", "row", "issue"])
            writer.writeheader()
            writer.writerows(rows)


def pd_is_na(value) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(value))
    except ImportError:
        return False
