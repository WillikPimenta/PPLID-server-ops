# -*- coding: utf-8 -*-
"""Serializadores públicos da API operacional D-1 (sem campos de infraestrutura)."""
from rest_framework import serializers

from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Replicado,
    ReplicacaoD1Run,
    ReplicacaoD1WorkflowDia,
)


class ReplicacaoD1RunSerializer(serializers.ModelSerializer):
    """Run/plano — somente metadados operacionais (sem paths locais)."""

    referencia_dados = serializers.CharField(source="parquet_referencia", read_only=True)

    class Meta:
        model = ReplicacaoD1Run
        fields = (
            "run_id",
            "data_referencia_d1",
            "data_execucao",
            "referencia_dados",
            "status_canonical",
            "auditores_ativos_brflow",
            "auditores_ativos_case",
            "protocolos_total",
            "workflows_total",
            "workflows_salvo_ok",
            "started_at",
            "finished_at",
            "attempt_number",
            "duration_seconds",
            "erro_codigo",
            "erro_resumo",
            "synced_at",
            "created_at",
        )


class ReplicacaoD1WorkflowDiaSerializer(serializers.ModelSerializer):
    run_id = serializers.CharField(read_only=True)

    class Meta:
        model = ReplicacaoD1WorkflowDia
        fields = (
            "id",
            "run_id",
            "data_referencia_d1",
            "workflow_config",
            "workflow_d1",
            "workflow_brflow",
            "canal_destino",
            "cliente",
            "segmento",
            "categoria",
            "fila",
            "modo_replicacao",
            "amostra_diaria",
            "amostra_solicitada",
            "amostra_efetiva",
            "protocolos_salvos",
            "pct_atingido",
            "disponivel_d1",
            "status_amostra",
            "status_brflow",
            "resultado",
            "severidade",
            "motivo_codigo",
            "motivo_resumo",
            "fase_execucao",
            "quantidade_alvo",
            "quantidade_encontrada",
            "status_operacional",
            "data_hora_upload_brflow",
            "upload_em",
            "protocolos_planejados",
            "protocolos_enviados",
            "protocolos_aceitos",
            "started_at",
            "finished_at",
            "attempt_number",
            "erro_codigo",
            "erro_resumo",
            "faixa_horaria",
        )


class ReplicacaoD1ProtocoloSerializer(serializers.ModelSerializer):
    run_id = serializers.CharField(read_only=True)

    class Meta:
        model = ReplicacaoD1Protocolo
        fields = (
            "id",
            "run_id",
            "data_referencia_d1",
            "protocolo",
            "workflow_config",
            "workflow_d1",
            "data_analise",
            "hora",
            "canal_destino",
            "status_brflow",
            "status_operacional",
            "replicado_em",
            "erro_resumido",
        )


class ReplicacaoD1ReplicadoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplicacaoD1Replicado
        fields = (
            "id",
            "report_date",
            "cliente_destino",
            "data_cadastro_destino",
            "protocolo_destino",
            "workflow_destino",
            "protocolo_origem",
            "cliente_origem",
            "workflow_origem",
            "data_cadastro_origem",
            "tipo_conclusao_analise_origem",
        )
