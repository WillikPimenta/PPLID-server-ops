# -*- coding: utf-8 -*-
"""Popula dados de demonstração para Contestação (atividades + controles)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from random import Random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaAtividadeProtocoloEtapa,
    AuditoriaControleRegistro,
)
from apps.auditoria.services.atividade_protocolo_crud import sync_atividade_protocolo_metrics

User = get_user_model()

DEMO_PREFIX = "DEMO-CT-"
DEMO_SEED = 20260715

CLIENTES = ["PICPAY", "PAGSEGURO", "NUBANK", "MERCADO PAGO", "C6 BANK"]
WORKFLOWS = [
    "Risk Manager - PICPAY",
    "Risk Manager - PAGSEGURO",
    "Risk Manager - NUBANK",
    "Risk Manager - MERCADO PAGO",
    "Risk Manager - C6 BANK",
]
NIVEIS = [
    "Digitalizador",
    "Auditor",
    "Risk Manager - Digitalizador",
    "Risk Manager - Auditor",
]
RESULTADOS = ["SEM RISCO", "APROVADO", "REPROVADO", "SUSPEITO", "EM ANÁLISE"]
TIPOS_CONCLUSAO = ["Automática", "Manual", "Processual"]
AGENTES = ["c91763a", "c92928a", "c93123a", "c11078q", "ELTON MARQUES"]
TIPOS_FALHA = ["Processual", "Manual", "Automático"]
ETAPAS_FALHA = ["AUDITORIA", "DIGITALIZAÇÃO", "ANÁLISE DOCUMENTAL", "BIOMETRIA"]
MOTIVOS_FALHA = [
    "Documento ausente",
    "Qualidade de imagem",
    "Divergência cadastral",
    "Selfie inválida",
]
TIPOS_ACAO = ["Exclusão", "Inclusão", "Consulta", "Consulta/Exclusão", "Avaliação/Exclusão", "Avaliação"]
MOTIVOS_BASE = [
    "FORMATAÇÃO/FONTE ADULTERADA",
    "FOTO SOBREPOSTA",
    "RISCO NA SELFIE",
    "CPF DIVERGENTE",
    "FACE ENCONTRADA NA BASE",
    "SELFIE",
]

# Quantidade de protocolos por atividade (15 atividades, variado).
PROTOCOLS_PER_ACTIVITY = [3, 5, 8, 12, 2, 20, 15, 7, 4, 10, 6, 18, 9, 1, 14]


def _pick(rng: Random, items: list):
    return items[rng.randrange(len(items))]


def _iso_date(day: date) -> str:
    return day.isoformat()


class Command(BaseCommand):
    help = (
        "Popula Contestação com dados demo: 15 atividades (protocolos variados), "
        "20 remoções base negativa, 15 base positiva e 30 solicitações IDAS/BIO."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--purge",
            action="store_true",
            help=f"Remove registros demo anteriores (nome/protocolo começando com {DEMO_PREFIX}).",
        )
        parser.add_argument(
            "--username",
            default="",
            help="Usuário criador (default: primeiro superusuário ou staff).",
        )

    def handle(self, *args, **options):
        rng = Random(DEMO_SEED)
        user = self._resolve_user(options["username"])
        if options["purge"]:
            removed = self._purge_demo()
            self.stdout.write(self.style.WARNING(f"Purge: {removed} registros demo removidos."))

        with transaction.atomic():
            atividades, protocol_index = self._seed_atividades(user, rng)
            controles = self._seed_controles(user, rng, protocol_index)

        self.stdout.write(
            self.style.SUCCESS(
                "Demo Contestação criada:\n"
                f"  - {len(atividades)} atividades\n"
                f"  - {sum(a.total_protocolos for a in atividades)} protocolos\n"
                f"  - {controles['neg']} remoção base negativa\n"
                f"  - {controles['pos']} remoção base positiva\n"
                f"  - {controles['idas']} solicitações IDAS/BIO\n"
                f"  criador={user.username if user else '—'}"
            )
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

    def _purge_demo(self) -> int:
        atividades = AuditoriaAtividade.objects.filter(nome__startswith=DEMO_PREFIX)
        n_ativ = atividades.count()
        n_prot = AuditoriaAtividadeProtocolo.objects.filter(atividade__in=atividades).count()
        atividades.delete()

        ctrl_ids = set(
            AuditoriaControleRegistro.objects.filter(
                dados__protocolo__startswith=DEMO_PREFIX,
            ).values_list("id", flat=True)
        )
        for item in AuditoriaControleRegistro.objects.all().only("id", "dados"):
            dados = item.dados if isinstance(item.dados, dict) else {}
            origem = str(dados.get("demanda_origem") or "")
            if DEMO_PREFIX.lower() in origem.lower() or DEMO_PREFIX in str(dados.get("demanda_idas") or ""):
                ctrl_ids.add(item.id)
        n_ctrl = len(ctrl_ids)
        if ctrl_ids:
            AuditoriaControleRegistro.objects.filter(id__in=ctrl_ids).delete()
        return n_ativ + n_prot + n_ctrl

    def _seed_atividades(self, user, rng: Random):
        now = timezone.now()
        atividades: list[AuditoriaAtividade] = []
        protocol_index: list[tuple[str, str]] = []  # (protocolo, cliente)

        for idx, protocol_count in enumerate(PROTOCOLS_PER_ACTIVITY, start=1):
            cliente = _pick(rng, CLIENTES)
            workflow = WORKFLOWS[CLIENTES.index(cliente)] if cliente in CLIENTES else _pick(rng, WORKFLOWS)
            recepcao = date.today() - timedelta(days=rng.randint(1, 20))
            created_at = now - timedelta(days=rng.randint(0, 12), hours=rng.randint(0, 20))

            atividade = AuditoriaAtividade(
                nome=f"{DEMO_PREFIX}QI-{8000 + idx} - {cliente} - {recepcao.strftime('%d%m%Y')}",
                workflow=workflow,
                nivel_hierarquico=_pick(rng, NIVEIS),
                cliente=cliente,
                link_demanda=f"https://example.com/demanda/{DEMO_PREFIX.lower()}{idx}",
                data_recepcao=recepcao,
                status=AuditoriaAtividade.STATUS_PENDENTE,
                total_protocolos=0,
                created_by=user,
            )
            atividade.save()
            # Preserve demo created_at for dashboard date filters
            AuditoriaAtividade.objects.filter(pk=atividade.pk).update(created_at=created_at)
            atividade.refresh_from_db()

            protocolos_bulk: list[AuditoriaAtividadeProtocolo] = []
            for row in range(1, protocol_count + 1):
                protocolo = f"{DEMO_PREFIX}{idx:02d}{row:04d}"
                protocol_index.append((protocolo, cliente))
                protocolos_bulk.append(
                    AuditoriaAtividadeProtocolo(
                        atividade=atividade,
                        protocolo=protocolo,
                        workflow=workflow,
                        nivel_hierarquico=atividade.nivel_hierarquico,
                        resultado_contestado=_pick(rng, RESULTADOS),
                        excel_row=row,
                        status=AuditoriaAtividadeProtocolo.STATUS_PENDENTE,
                    )
                )
            AuditoriaAtividadeProtocolo.objects.bulk_create(protocolos_bulk)

            # Mix analysis outcomes for dashboard richness.
            saved = list(atividade.protocolos.order_by("excel_row"))
            for p_idx, protocolo in enumerate(saved):
                roll = rng.random()
                if roll < 0.35:
                    protocolo.status = AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO
                    protocolo.situacao = (
                        AuditoriaAtividadeProtocolo.SITUACAO_IMPROCEDENTE
                        if rng.random() < 0.55
                        else AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE
                    )
                    protocolo.tipo_conclusao = _pick(rng, TIPOS_CONCLUSAO)
                    protocolo.reanalisado = rng.random() < 0.25
                    protocolo.analisado_por = user
                    protocolo.analisado_em = created_at + timedelta(hours=rng.randint(1, 48))
                    protocolo.finalizado_em = protocolo.analisado_em
                    protocolo.save()
                    AuditoriaAtividadeProtocoloEtapa.objects.create(
                        protocolo=protocolo,
                        ordem=0,
                        resultado_correto=_pick(rng, RESULTADOS),
                        tipo_falha=_pick(rng, TIPOS_FALHA),
                        etapa_falha=_pick(rng, ETAPAS_FALHA),
                        agente=_pick(rng, AGENTES),
                        situacao=protocolo.situacao,
                        motivo_falha=_pick(rng, MOTIVOS_FALHA)
                        if protocolo.situacao == AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE
                        else "",
                    )
                elif roll < 0.55:
                    protocolo.status = AuditoriaAtividadeProtocolo.STATUS_EM_ANDAMENTO
                    protocolo.tipo_conclusao = _pick(rng, TIPOS_CONCLUSAO)
                    protocolo.analisado_por = user
                    protocolo.analisado_em = created_at + timedelta(hours=rng.randint(1, 24))
                    protocolo.save()
                    AuditoriaAtividadeProtocoloEtapa.objects.create(
                        protocolo=protocolo,
                        ordem=0,
                        resultado_correto=_pick(rng, RESULTADOS),
                        tipo_falha=_pick(rng, TIPOS_FALHA),
                        etapa_falha=_pick(rng, ETAPAS_FALHA),
                        agente=_pick(rng, AGENTES),
                        situacao=AuditoriaAtividadeProtocolo.SITUACAO_IMPROCEDENTE
                        if rng.random() < 0.5
                        else AuditoriaAtividadeProtocolo.SITUACAO_PROCEDENTE,
                        motivo_falha=_pick(rng, MOTIVOS_FALHA),
                    )
                # else: leave pending

            sync_atividade_protocolo_metrics(atividade)
            atividade.refresh_from_db()
            atividades.append(atividade)

        return atividades, protocol_index

    def _seed_controles(self, user, rng: Random, protocol_index: list[tuple[str, str]]):
        today = date.today()
        now = timezone.now()

        neg = self._create_controle_batch(
            user=user,
            rng=rng,
            tipo=AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA,
            count=20,
            today=today,
            now=now,
            protocol_index=protocol_index,
            modo="negativa",
        )
        pos = self._create_controle_batch(
            user=user,
            rng=rng,
            tipo=AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA,
            count=15,
            today=today,
            now=now,
            protocol_index=protocol_index,
            modo="positiva",
        )
        idas = self._create_controle_batch(
            user=user,
            rng=rng,
            tipo=AuditoriaControleRegistro.TIPO_SOLICITACOES_IDAS_BIO,
            count=30,
            today=today,
            now=now,
            protocol_index=protocol_index,
            modo="idas",
        )
        return {"neg": neg, "pos": pos, "idas": idas}

    def _create_controle_batch(
        self,
        *,
        user,
        rng: Random,
        tipo: str,
        count: int,
        today: date,
        now: datetime,
        protocol_index: list[tuple[str, str]],
        modo: str,
    ) -> int:
        created = 0
        for i in range(1, count + 1):
            proto, cliente = _pick(rng, protocol_index) if protocol_index else ("", _pick(rng, CLIENTES))
            abertura = today - timedelta(days=rng.randint(0, 18))
            retorno = abertura + timedelta(days=rng.randint(0, 10))
            created_at = now - timedelta(days=rng.randint(0, 14), hours=rng.randint(0, 20))

            if modo == "idas":
                situacao = _pick(rng, ["Nova", "Em andamento", "Finalizada"])
                dados = {
                    "cliente": cliente,
                    "workflow": _pick(rng, WORKFLOWS),
                    "solicitante": _pick(rng, AGENTES),
                    "responsavel": _pick(rng, AGENTES),
                    "demanda_origem": f"https://example.com/origem/{DEMO_PREFIX.lower()}idas-{i}",
                    "demanda_idas": f"{DEMO_PREFIX}IDAS-{17000 + i}",
                    "observacoes": f"Solicitação demo IDAS/BIO #{i}",
                    "data_abertura": _iso_date(abertura),
                    "data_conclusao": _iso_date(retorno) if situacao == "Finalizada" else "",
                    "situacao": situacao,
                }
            elif modo == "positiva":
                situacao = _pick(rng, ["Nova", "Finalizada"])
                dados = {
                    "tipo_acao": _pick(rng, TIPOS_ACAO),
                    "cpf": f"{rng.randint(10000000000, 99999999999)}",
                    "cliente": cliente,
                    "protocolo": proto,
                    "workflow": _pick(rng, WORKFLOWS),
                    "data_criacao": _iso_date(abertura - timedelta(days=rng.randint(1, 5))),
                    "demanda_origem": f"https://example.com/origem/{DEMO_PREFIX.lower()}pos-{i}",
                    "cliente_origem": cliente,
                    "motivo": _pick(rng, MOTIVOS_BASE),
                    "detalhamento": f"Higienização demo #{i}",
                    "observacoes": "Registro demo base positiva",
                    "demanda_bio": f"https://example.com/bio/{DEMO_PREFIX.lower()}{i}",
                    "data_abertura": _iso_date(abertura),
                    "data_retorno": _iso_date(retorno) if situacao == "Finalizada" else "",
                    "situacao": situacao,
                }
            else:
                situacao = _pick(rng, ["Nova", "Finalizada"])
                dados = {
                    "tipo_acao": _pick(rng, TIPOS_ACAO),
                    "cpf": f"{rng.randint(10000000000, 99999999999)}",
                    "cliente": cliente,
                    "protocolo": proto,
                    "workflow": _pick(rng, WORKFLOWS),
                    "demanda_origem": f"https://example.com/origem/{DEMO_PREFIX.lower()}neg-{i}",
                    "motivo": _pick(rng, MOTIVOS_BASE),
                    "observacoes": f"Registro demo base negativa #{i}",
                    "demanda_bio": f"https://example.com/bio/{DEMO_PREFIX.lower()}neg-{i}",
                    "data_abertura": _iso_date(abertura),
                    "data_retorno": _iso_date(retorno) if situacao == "Finalizada" else "",
                    "situacao": situacao,
                }

            item = AuditoriaControleRegistro.objects.create(
                tipo=tipo,
                dados=dados,
                situacao=situacao,
                created_by=user,
            )
            AuditoriaControleRegistro.objects.filter(pk=item.pk).update(created_at=created_at)
            created += 1
        return created
