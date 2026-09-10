# -*- coding: utf-8 -*-
"""Popula contestações operacionais em volume para testes de indicadores de auditores."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from random import Random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    ContestacaoOperacional,
    ContestacaoOperacionalHistorico,
)
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.workforce.models import Agent

User = get_user_model()

SEED_PREFIX = "BULK-CT-"
QUALITY_AUD_PREFIX = "BULK-QA-"
QUALITY_FAL_PREFIX = "BULK-QF-"
QUALITY_SOURCE = "seed_contestacao_bulk"
AUDITOR_PREFIX = "audbulk"
SEED = 20260825

START_DATE = date(2026, 8, 1)
END_DATE = date(2026, 8, 25)

ETAPAS = (
    "Conferência",
    "Digitalização",
    "Análise Documental",
    "Biometria",
    "Validação",
)

AUDITOR_NAMES: tuple[tuple[str, str], ...] = (
    ("Ana", "Silva"),
    ("Bruno", "Santos"),
    ("Carla", "Oliveira"),
    ("Daniel", "Costa"),
    ("Elena", "Ferreira"),
    ("Felipe", "Almeida"),
    ("Gabriela", "Lima"),
    ("Henrique", "Rocha"),
    ("Isabela", "Martins"),
    ("Juliano", "Pereira"),
    ("Karina", "Souza"),
    ("Lucas", "Barbosa"),
    ("Mariana", "Carvalho"),
    ("Nicolas", "Araujo"),
    ("Olivia", "Mendes"),
    ("Paulo", "Nascimento"),
    ("Renata", "Castro"),
    ("Samuel", "Dias"),
    ("Tatiana", "Gomes"),
    ("Vitor", "Ribeiro"),
    ("Amanda", "Correia"),
    ("Bernardo", "Teixeira"),
    ("Camila", "Monteiro"),
    ("Diego", "Cardoso"),
    ("Fabiana", "Cavalcanti"),
    ("Gustavo", "Moreira"),
    ("Helena", "Freitas"),
    ("Igor", "Campos"),
    ("Julia", "Azevedo"),
    ("Leandro", "Pinto"),
)

# (status, status_falha, destino_falha, with_history, weight)
OUTCOMES: tuple[tuple[str, str, str, bool, int], ...] = (
    (
        ContestacaoOperacional.STATUS_PROCEDENTE,
        AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
        ContestacaoOperacional.FALHA_STATUS_RETIRADA,
        True,
        35,
    ),
    (
        ContestacaoOperacional.STATUS_PROCEDENTE,
        AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA,
        ContestacaoOperacional.FALHA_STATUS_MANTIDA,
        True,
        25,
    ),
    (
        ContestacaoOperacional.STATUS_IMPROCEDENTE,
        AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA,
        ContestacaoOperacional.FALHA_STATUS_MANTIDA,
        True,
        15,
    ),
    (
        ContestacaoOperacional.STATUS_IMPROCEDENTE,
        AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
        ContestacaoOperacional.FALHA_STATUS_RETIRADA,
        True,
        10,
    ),
    (
        ContestacaoOperacional.STATUS_PENDENTE,
        AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        "",
        False,
        10,
    ),
    (
        ContestacaoOperacional.STATUS_EM_ANALISE,
        AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        "",
        False,
        5,
    ),
)


def _pick_weighted(rng: Random, items: tuple[tuple, ...]):
    total = sum(item[-1] for item in items)
    roll = rng.randrange(total)
    acc = 0
    for item in items:
        acc += item[-1]
        if roll < acc:
            return item[:-1]
    return items[-1][:-1]


def _random_day(rng: Random) -> date:
    span = (END_DATE - START_DATE).days
    return START_DATE + timedelta(days=rng.randint(0, span))


def _aware_dt(day: date, rng: Random) -> datetime:
    tz = timezone.get_current_timezone()
    hour = rng.randint(8, 18)
    minute = rng.randint(0, 59)
    return timezone.make_aware(datetime.combine(day, time(hour, minute)), tz)


class Command(BaseCommand):
    help = (
        "Insere contestações operacionais em volume (padrão 100k) com auditores "
        f"nomeados, histórico variado e base oficial de Qualidade ({SEED_PREFIX}*)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=100_000,
            help="Quantidade de contestações a criar (default: 100000; use 0 para pular).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5_000,
            help="Tamanho do lote bulk_create (default: 5000).",
        )
        parser.add_argument(
            "--no-quality",
            action="store_true",
            help="Não popula qualidade_auditado/qualidade_falha (default: popula).",
        )
        parser.add_argument(
            "--quality-auditados",
            type=int,
            default=200_000,
            help="Quantidade de auditados oficiais (default: 200000).",
        )
        parser.add_argument(
            "--quality-erro-ratio",
            type=float,
            default=0.32,
            help="Proporção de erros sobre auditados (default: 0.32).",
        )
        parser.add_argument(
            "--fix-dates",
            action="store_true",
            help=f"Redistribui created_at das contestações {SEED_PREFIX}* no período alvo.",
        )
        parser.add_argument(
            "--purge",
            action="store_true",
            help=f"Remove registros anteriores bulk ({SEED_PREFIX}*, {QUALITY_AUD_PREFIX}*, etc.).",
        )
        parser.add_argument(
            "--only-purge",
            action="store_true",
            help="Apenas remove dados bulk sem inserir.",
        )
        parser.add_argument(
            "--username",
            default="",
            help="Usuário criador das contestações (default: primeiro superuser/staff).",
        )

    def handle(self, *args, **options):
        count = max(0, int(options["count"]))
        batch_size = max(500, int(options["batch_size"]))
        with_quality = not options["no_quality"]
        quality_auditados = max(0, int(options["quality_auditados"]))
        quality_erro_ratio = max(0.0, min(1.0, float(options["quality_erro_ratio"])))

        if options["purge"] or options["only_purge"]:
            removed = self._purge()
            self.stdout.write(self.style.WARNING(f"Purge: {removed} registro(s) removido(s)."))
            if options["only_purge"]:
                bump_quality_cache_version()
                return

        creator = self._resolve_user(options["username"])
        rng = Random(SEED)
        auditors = self._ensure_auditors()

        if options["fix_dates"]:
            fixed = self._backfill_contestacao_dates()
            self.stdout.write(self.style.SUCCESS(f"Datas de contestação redistribuídas: {fixed:,}"))

        inserted = 0
        history_count = 0
        if count:
            self.stdout.write(
                f"Inserindo {count:,} contestações | auditores={len(auditors)} | "
                f"lote={batch_size:,} | período={START_DATE}..{END_DATE}"
            )
            sequence = 0
            while inserted < count:
                current_batch = min(batch_size, count - inserted)
                batch_hist = self._insert_batch(
                    rng=rng,
                    auditors=auditors,
                    creator=creator,
                    batch_size=current_batch,
                    sequence_start=sequence,
                )
                inserted += current_batch
                history_count += batch_hist
                sequence += current_batch
                self.stdout.write(f"  … {inserted:,}/{count:,} contestações")

        quality_counts = (0, 0)
        if with_quality and quality_auditados:
            quality_counts = self._seed_quality(
                rng=rng,
                auditors=auditors,
                auditados_count=quality_auditados,
                erro_ratio=quality_erro_ratio,
                batch_size=batch_size,
            )

        bump_quality_cache_version()
        if inserted:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Contestações: {inserted:,} | históricos: {history_count:,} | prefixo={SEED_PREFIX}"
                )
            )
        if quality_counts != (0, 0):
            self.stdout.write(
                self.style.SUCCESS(
                    f"Qualidade oficial: {quality_counts[0]:,} auditados | {quality_counts[1]:,} erros"
                )
            )
        self.stdout.write(
            "Visualize em /secao/indicadores/qualidade/auditores com "
            "start_date=2026-08-01&end_date=2026-08-25&grain=etapa&date_axis=auditoria"
        )

    def _resolve_user(self, username: str):
        if username:
            user = User.objects.filter(username__iexact=username).first()
            if not user:
                raise SystemExit(f"Usuário '{username}' não encontrado.")
            return user
        return (
            User.objects.filter(is_superuser=True).order_by("id").first()
            or User.objects.filter(is_staff=True).order_by("id").first()
            or User.objects.order_by("id").first()
        )

    def _purge(self) -> int:
        contest_qs = ContestacaoOperacional.objects.filter(protocolo__startswith=SEED_PREFIX)
        n_contest = contest_qs.count()
        contest_qs.delete()
        falha_qs = AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=SEED_PREFIX)
        n_falha = falha_qs.count()
        falha_qs.delete()
        aud_qs = QualidadeAuditado.objects.filter(source_file=QUALITY_SOURCE)
        n_aud = aud_qs.count()
        aud_qs.delete()
        fal_qs = QualidadeFalha.objects.filter(source_file=QUALITY_SOURCE)
        n_fal = fal_qs.count()
        fal_qs.delete()
        return n_contest + n_falha + n_aud + n_fal

    def _ensure_auditors(self) -> list[User]:
        auditors: list[User] = []
        for index, (first_name, last_name) in enumerate(AUDITOR_NAMES, start=1):
            username = f"{AUDITOR_PREFIX}{index:02d}"
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_active": True,
                },
            )
            if not user.first_name:
                user.first_name = first_name
                user.last_name = last_name
                user.save(update_fields=["first_name", "last_name"])
            Agent.objects.get_or_create(
                user_lan_id=username,
                defaults={"full_name": f"{first_name} {last_name}", "active": True},
            )
            auditors.append(user)
        return auditors

    def _backfill_contestacao_dates(self) -> int:
        """bulk_create ignora auto_now_add; redistribui coorte no período alvo."""
        tz_name = timezone.get_current_timezone().key
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE qualidade_contestacao_operacional AS q
                SET
                    created_at = (
                        TIMESTAMPTZ '2026-08-01 08:00:00' AT TIME ZONE %s
                        + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 25) * INTERVAL '1 day')
                        + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 10) * INTERVAL '1 hour')
                        + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 60) * INTERVAL '1 minute')
                    ),
                    atribuida_em = (
                        TIMESTAMPTZ '2026-08-01 08:00:00' AT TIME ZONE %s
                        + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 25) * INTERVAL '1 day')
                        + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 10) * INTERVAL '1 hour')
                        + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 60) * INTERVAL '1 minute')
                        - INTERVAL '6 hours'
                    ),
                    decidida_em = CASE
                        WHEN q.status IN ('procedente', 'improcedente') THEN
                            TIMESTAMPTZ '2026-08-01 08:00:00' AT TIME ZONE %s
                            + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 25) * INTERVAL '1 day')
                            + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 10) * INTERVAL '1 hour')
                            + ((SUBSTRING(q.protocolo FROM 9)::INTEGER %% 60) * INTERVAL '1 minute')
                            + INTERVAL '12 hours'
                        ELSE NULL
                    END
                WHERE q.protocolo LIKE %s
                """,
                [tz_name, tz_name, tz_name, f"{SEED_PREFIX}%"],
            )
            updated = cursor.rowcount
            cursor.execute(
                f"""
                UPDATE qualidade_contestacao_operacional_hist AS h
                SET created_at = q.created_at + INTERVAL '8 hours'
                FROM qualidade_contestacao_operacional AS q
                WHERE h.contestacao_id = q.id
                  AND q.protocolo LIKE %s
                  AND h.evento = %s
                """,
                [f"{SEED_PREFIX}%", ContestacaoOperacionalHistorico.EVENTO_DECIDIDA],
            )
        return updated

    def _seed_quality(
        self,
        *,
        rng: Random,
        auditors: list[User],
        auditados_count: int,
        erro_ratio: float,
        batch_size: int,
    ) -> tuple[int, int]:
        existing = QualidadeAuditado.objects.filter(source_file=QUALITY_SOURCE).count()
        if existing:
            self.stdout.write(
                self.style.WARNING(
                    f"Qualidade bulk já presente ({existing:,} auditados). "
                    "Use --purge para recriar."
                )
            )
            falhas_existing = QualidadeFalha.objects.filter(source_file=QUALITY_SOURCE).count()
            return existing, falhas_existing

        erros_target = int(round(auditados_count * erro_ratio))
        self.stdout.write(
            f"Inserindo qualidade oficial: {auditados_count:,} auditados | "
            f"~{erros_target:,} erros | lote={batch_size:,}"
        )

        inserted_aud = 0
        inserted_fal = 0
        sequence = 0
        while inserted_aud < auditados_count:
            current_batch = min(batch_size, auditados_count - inserted_aud)
            batch_fal = self._insert_quality_batch(
                rng=rng,
                auditors=auditors,
                batch_size=current_batch,
                sequence_start=sequence,
                erros_remaining=max(0, erros_target - inserted_fal),
                auditados_remaining=auditados_count - inserted_aud,
            )
            inserted_aud += current_batch
            inserted_fal += batch_fal
            sequence += current_batch
            self.stdout.write(f"  … {inserted_aud:,}/{auditados_count:,} auditados")

        return inserted_aud, inserted_fal

    def _insert_quality_batch(
        self,
        *,
        rng: Random,
        auditors: list[User],
        batch_size: int,
        sequence_start: int,
        erros_remaining: int,
        auditados_remaining: int,
    ) -> int:
        auditados: list[QualidadeAuditado] = []
        falhas: list[QualidadeFalha] = []
        erros_in_batch = 0
        if auditados_remaining > 0:
            erros_in_batch = min(
                batch_size,
                max(0, int(round(erros_remaining * (batch_size / auditados_remaining)))),
            )

        for offset in range(batch_size):
            seq = sequence_start + offset + 1
            auditor = auditors[rng.randrange(len(auditors))]
            auditor_key = auditor.username.lower()
            etapa = ETAPAS[rng.randrange(len(ETAPAS))]
            day = _random_day(rng)
            data_analise = day + timedelta(days=rng.randint(0, 2))
            agente = f"agente{rng.randint(1, 8000):05d}"
            protocolo = f"{QUALITY_AUD_PREFIX}{seq:08d}"

            auditados.append(
                QualidadeAuditado(
                    data=day,
                    data_analise=data_analise,
                    id_cliente=6,
                    id_workflow=144,
                    matricula=agente,
                    matricula_auditor=auditor_key,
                    protocolo=protocolo,
                    tipo_analise="Auditoria Compliance",
                    etapa=etapa,
                    source_file=QUALITY_SOURCE,
                )
            )

            if erros_in_batch > 0 and offset < erros_in_batch:
                falhas.append(
                    QualidadeFalha(
                        data=day,
                        data_analise=data_analise,
                        id_cliente=6,
                        id_workflow=144,
                        matricula=agente,
                        usuario_auditor=auditor_key,
                        protocolo=f"{QUALITY_FAL_PREFIX}{seq:08d}",
                        tipo_analise="Auditoria Compliance",
                        etapa=etapa,
                        tipo_falha="Manual",
                        source_file=QUALITY_SOURCE,
                    )
                )

        with transaction.atomic():
            QualidadeAuditado.objects.bulk_create(auditados, batch_size=1000)
            if falhas:
                QualidadeFalha.objects.bulk_create(falhas, batch_size=1000)
        return len(falhas)

    def _insert_batch(
        self,
        *,
        rng: Random,
        auditors: list[User],
        creator,
        batch_size: int,
        sequence_start: int,
    ) -> int:
        falhas: list[AuditoriaFalhaCadastro] = []
        meta: list[tuple] = []

        for offset in range(batch_size):
            seq = sequence_start + offset + 1
            auditor = auditors[rng.randrange(len(auditors))]
            etapa = ETAPAS[rng.randrange(len(ETAPAS))]
            status, status_falha, destino_falha, with_history = _pick_weighted(rng, OUTCOMES)
            day = _random_day(rng)
            created_at = _aware_dt(day, rng)
            dominio = (
                ContestacaoOperacional.DOMINIO_FRAUD
                if rng.random() < 0.35
                else ContestacaoOperacional.DOMINIO_COMPLIANCE
            )
            categoria = (
                ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD
                if dominio == ContestacaoOperacional.DOMINIO_FRAUD
                else ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE
            )
            protocolo = f"{SEED_PREFIX}{seq:08d}"
            agente = f"agente{rng.randint(1, 5000):05d}"

            falhas.append(
                AuditoriaFalhaCadastro(
                    protocolo=protocolo,
                    tipo_falha="Colaborador",
                    usuario=agente,
                    etapa_falha=etapa,
                    auditor_responsavel=auditor,
                    status_falha=status_falha,
                    origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
                    tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
                    created_by=creator,
                )
            )
            meta.append(
                (
                    protocolo,
                    status,
                    destino_falha,
                    with_history,
                    status_falha,
                    created_at,
                    dominio,
                    categoria,
                    agente,
                    etapa,
                )
            )

        with transaction.atomic():
            AuditoriaFalhaCadastro.objects.bulk_create(falhas, batch_size=1000)
            falha_by_protocolo = {
                row.protocolo: row
                for row in AuditoriaFalhaCadastro.objects.filter(
                    protocolo__in=[item[0] for item in meta]
                ).only("id", "protocolo")
            }

            contestacoes: list[ContestacaoOperacional] = []
            for row in meta:
                (
                    protocolo,
                    status,
                    destino_falha,
                    _with_history,
                    _status_falha,
                    created_at,
                    dominio,
                    categoria,
                    agente,
                    _etapa,
                ) = row
                falha = falha_by_protocolo[protocolo]
                decidida_em = (
                    created_at + timedelta(hours=rng.randint(2, 72))
                    if status
                    in (
                        ContestacaoOperacional.STATUS_PROCEDENTE,
                        ContestacaoOperacional.STATUS_IMPROCEDENTE,
                    )
                    else None
                )
                contestacoes.append(
                    ContestacaoOperacional(
                        falha=falha,
                        dominio=dominio,
                        categoria=categoria,
                        status=status,
                        justificativa_lider="Contestação bulk para teste de indicadores.",
                        destino_falha=destino_falha,
                        protocolo=protocolo,
                        agente_usuario=agente,
                        atribuida_em=created_at - timedelta(hours=rng.randint(1, 48)),
                        created_by=creator,
                        created_at=created_at,
                        decidida_em=decidida_em,
                    )
                )

            ContestacaoOperacional.objects.bulk_create(contestacoes, batch_size=1000)
            contest_by_protocolo = {
                row.protocolo: row
                for row in ContestacaoOperacional.objects.filter(
                    protocolo__in=[item[0] for item in meta]
                )
            }

            contest_updates: list[ContestacaoOperacional] = []
            for row in meta:
                protocolo, status, _destino, _with_history, _status_falha, created_at, *_rest = row
                contest = contest_by_protocolo[protocolo]
                contest.created_at = created_at
                contest.atribuida_em = created_at - timedelta(hours=rng.randint(1, 48))
                if status in (
                    ContestacaoOperacional.STATUS_PROCEDENTE,
                    ContestacaoOperacional.STATUS_IMPROCEDENTE,
                ):
                    contest.decidida_em = created_at + timedelta(hours=rng.randint(2, 72))
                contest_updates.append(contest)
            ContestacaoOperacional.objects.bulk_update(
                contest_updates,
                ["created_at", "atribuida_em", "decidida_em"],
                batch_size=1000,
            )

            historico: list[ContestacaoOperacionalHistorico] = []
            for row in meta:
                protocolo, status, _destino, with_history, status_falha, created_at, *_rest = row
                if not with_history:
                    continue
                contest = contest_by_protocolo[protocolo]
                historico.append(
                    ContestacaoOperacionalHistorico(
                        contestacao=contest,
                        evento=ContestacaoOperacionalHistorico.EVENTO_DECIDIDA,
                        status_anterior=ContestacaoOperacional.STATUS_EM_ANALISE,
                        status_novo=status,
                        falha_status_anterior=AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                        falha_status_novo=status_falha,
                        actor=creator,
                        created_at=created_at + timedelta(hours=rng.randint(1, 48)),
                    )
                )

            if historico:
                ContestacaoOperacionalHistorico.objects.bulk_create(historico, batch_size=1000)
                ContestacaoOperacionalHistorico.objects.bulk_update(
                    historico,
                    ["created_at"],
                    batch_size=1000,
                )

        return len(historico)
