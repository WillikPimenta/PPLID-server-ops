"""Importa agent_history a partir de export SharePoint (XLSX), sem recriar agentes."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.workforce.excel_utils import DEFAULT_XLSX
from apps.workforce.services.agent_history_xlsx import (
    build_preflight,
    preflight_to_dict,
    replace_agent_history,
    write_backup,
)


class Command(BaseCommand):
    help = (
        "Atualiza somente agent_history a partir de XLSX SharePoint/tbHeadcount. "
        "Não cria nem exclui Agent/User/UserProfile. Use --dry-run antes de gravar."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=str(DEFAULT_XLSX.parent / "agent_history.xlsx"),
            help="Caminho do Excel (export SharePoint agent_history / query).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Somente preflight — não grava nem apaga.",
        )
        parser.add_argument(
            "--backup-dir",
            default=str(DEFAULT_XLSX.parent / "backups"),
            help="Pasta para backup JSON de agent_history antes da carga.",
        )
        parser.add_argument(
            "--report",
            default="",
            help="Caminho JSON do relatório de preflight (opcional).",
        )
        parser.add_argument(
            "--allow-partial",
            action="store_true",
            help="Permite gravar mesmo com matrículas de agente não resolvidas (linhas órfãs ficam de fora).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Confirma substituição total de agent_history (obrigatório fora de dry-run).",
        )
        parser.add_argument(
            "--align-agent-active",
            action="store_true",
            help=(
                "Após substituir históricos, alinha Agent.active com vigências abertas "
                "(stale_active→False, orphan_open→True)."
            ),
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"Arquivo não encontrado: {path}")

        report, resolved, rejected = build_preflight(path)
        report_dict = preflight_to_dict(report)

        self.stdout.write(self.style.MIGRATE_HEADING("Preflight agent_history"))
        self.stdout.write(f"Arquivo: {report.file_path}")
        self.stdout.write(f"SHA-256: {report.file_sha256}")
        self.stdout.write(f"Modificado: {report.file_mtime}")
        self.stdout.write(f"Aba: {report.sheet_name}")
        self.stdout.write(f"Linhas lidas: {report.rows_read}")
        self.stdout.write(f"Linhas convertidas: {report.rows_converted}")
        self.stdout.write(f"Com erro: {report.rows_with_errors}")
        self.stdout.write(f"Históricos atuais: {report.current_history_count}")
        self.stdout.write(f"Após substituição: {report.expected_after_replace}")
        self.stdout.write(
            f"UUID reuso/novo: {report.expected_uuid_reuse}/{report.expected_uuid_new}"
        )
        self.stdout.write(
            f"Agents/UserProfiles (inalterados): "
            f"{report.agent_count_unchanged}/{report.user_profile_count_unchanged}"
        )

        if report.unresolved_agent_lans:
            self.stdout.write(
                self.style.WARNING(
                    f"Matrículas de agente não resolvidas ({len(report.unresolved_agent_lans)}): "
                    + ", ".join(report.unresolved_agent_lans[:20])
                    + ("…" if len(report.unresolved_agent_lans) > 20 else "")
                )
            )
        if report.unresolved_leader_lans:
            self.stdout.write(
                self.style.WARNING(
                    f"Líderes LAN não resolvidos: {len(report.unresolved_leader_lans)}"
                )
            )
        if report.unresolved_facilitator_lans:
            self.stdout.write(
                self.style.WARNING(
                    f"Facilitadores LAN não resolvidos: {len(report.unresolved_facilitator_lans)}"
                )
            )
        if report.type_or_size_errors:
            self.stdout.write(
                self.style.ERROR(
                    f"Erros de tipo/tamanho: {len(report.type_or_size_errors)}"
                )
            )
        if report.temporal_warnings:
            self.stdout.write(
                self.style.WARNING(
                    f"Avisos temporais: {len(report.temporal_warnings)}"
                )
            )
        if report.name_mismatches:
            self.stdout.write(
                self.style.WARNING(
                    f"Divergências de nome (só conferência): {len(report.name_mismatches)}"
                )
            )

        consistency = report.agent_active_consistency or {}
        if consistency:
            self.stdout.write(self.style.MIGRATE_HEADING("Consistência Agent.active (pós-carga prevista)"))
            self.stdout.write(
                f"stale_active após import: {consistency.get('agents_stale_active_after_import', 0)}"
            )
            self.stdout.write(
                f"orphan_open após import: {consistency.get('agents_orphan_open_after_import', 0)}"
            )
            self.stdout.write(
                f"delta ativos (cairiam se alinhasse stale): "
                f"{consistency.get('agents_active_delta', 0)}"
            )
            self.stdout.write(
                f"ativos agora / abertos pós-carga / ativos após align: "
                f"{consistency.get('active_agents_now', 0)} / "
                f"{consistency.get('open_histories_after_import', 0)} / "
                f"{consistency.get('active_after_align_estimate', 0)}"
            )

        report_path = Path(options["report"]) if options["report"] else None
        if not report_path:
            stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
            report_path = (
                Path(options["backup_dir"]) / f"agent_history_preflight_{stamp}.json"
            )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "preflight": report_dict,
                    "rejected_sample": [
                        {
                            "row": d.excel_row,
                            "agent_lan_id": d.agent_lan_id,
                            "errors": d.errors,
                        }
                        for d in rejected[:100]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.stdout.write(f"Relatório: {report_path}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run — nenhuma alteração gravada."))
            return

        if report.unresolved_agent_lans and not options["allow_partial"]:
            raise CommandError(
                "Há matrículas de agente não resolvidas. "
                "Corrija o cadastro ou use --allow-partial (linhas órfãs ficam de fora)."
            )

        if not options["force"]:
            raise CommandError(
                "Substituição total de agent_history exige --force "
                "(depois de revisar o dry-run)."
            )

        if not resolved:
            raise CommandError("Nenhum registro válido para inserir.")

        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        backup_path = Path(options["backup_dir"]) / f"agent_history_backup_{stamp}.json"
        # Backup explícito antes da troca (também reforçado em replace_agent_history).
        write_backup(backup_path)
        self.stdout.write(f"Backup: {backup_path}")

        try:
            result = replace_agent_history(
                resolved,
                backup_path=None,  # já gravado acima
                allow_unresolved_agents=options["allow_partial"],
            )
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Falha na carga: {exc}"))
            self.stderr.write(
                self.style.WARNING(
                    f"Estado anterior preservado pela transação. "
                    f"Backup disponível em: {backup_path}"
                )
            )
            self.stderr.write(
                "Para restaurar manualmente: "
                f"python manage.py restore_agent_history_backup --file={backup_path}"
            )
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("Substituição concluída."))
        for key, value in result.items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write(
            "Caches de Qualidade invalidados (versão + índice de responsabilidade)."
        )

        if options["align_agent_active"]:
            from apps.workforce.services.agent_active_reconcile import (
                apply_align_agent_active,
            )

            align_report = apply_align_agent_active(dry_run=False)
            self.stdout.write(self.style.SUCCESS("Agent.active alinhado com vigências abertas."))
            self.stdout.write(
                f"  would_change/applied: {align_report.get('would_change', 0)} "
                f"(deactivate={align_report.get('deactivate_count', 0)}, "
                f"activate={align_report.get('activate_count', 0)})"
            )
