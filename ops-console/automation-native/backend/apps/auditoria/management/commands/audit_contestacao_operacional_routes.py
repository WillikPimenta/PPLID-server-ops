from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.auditoria.models import (
    ContestacaoOperacional,
    ContestacaoOperacionalHistorico,
)
from apps.auditoria.services.contestacao_operacional import (
    CATEGORIA_TO_DOMINIO,
    resolve_categoria_falha,
)


class Command(BaseCommand):
    help = (
        "Audita domínio/categoria das contestações operacionais. "
        "Por padrão não altera dados; use --apply para reclassificar divergências."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica reclassificações canônicas e registra a alteração no histórico.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        total = divergentes = invalidas = corrigidas = 0
        queryset = ContestacaoOperacional.objects.select_related(
            "falha", "falha__analise_origem"
        ).order_by("pk")

        for item in queryset.iterator(chunk_size=500):
            total += 1
            try:
                categoria = resolve_categoria_falha(item.falha)
            except ValidationError as exc:
                invalidas += 1
                self.stderr.write(
                    f"INVALIDA id={item.pk} falha_id={item.falha_id} motivo={exc}"
                )
                continue

            dominio = CATEGORIA_TO_DOMINIO[categoria]
            if item.categoria == categoria and item.dominio == dominio:
                continue

            divergentes += 1
            self.stdout.write(
                "DIVERGENTE "
                f"id={item.pk} falha_id={item.falha_id} "
                f"de={item.dominio}/{item.categoria} para={dominio}/{categoria}"
            )
            if apply_changes and self._apply(item.pk, categoria, dominio):
                corrigidas += 1

        modo = "apply" if apply_changes else "dry-run"
        self.stdout.write(
            self.style.SUCCESS(
                f"modo={modo} total={total} divergentes={divergentes} "
                f"invalidas={invalidas} corrigidas={corrigidas}"
            )
        )

    @transaction.atomic
    def _apply(self, contestacao_id: int, categoria: str, dominio: str) -> bool:
        item = (
            ContestacaoOperacional.objects.select_for_update()
            .select_related("falha")
            .get(pk=contestacao_id)
        )
        categoria_atual = resolve_categoria_falha(item.falha)
        dominio_atual = CATEGORIA_TO_DOMINIO[categoria_atual]
        if categoria_atual != categoria or dominio_atual != dominio:
            return False
        if item.categoria == categoria and item.dominio == dominio:
            return False

        anterior = f"{item.dominio}/{item.categoria}"
        novo = f"{dominio}/{categoria}"
        item.categoria = categoria
        item.dominio = dominio
        item.save(update_fields=["categoria", "dominio", "updated_at"])
        ContestacaoOperacionalHistorico.objects.create(
            contestacao=item,
            evento=ContestacaoOperacionalHistorico.EVENTO_RECLASSIFICADA,
            status_anterior=item.status,
            status_novo=item.status,
            falha_status_anterior=item.falha.status_falha,
            falha_status_novo=item.falha.status_falha,
            justificativa=f"Reclassificação canônica de {anterior} para {novo}.",
        )
        return True
