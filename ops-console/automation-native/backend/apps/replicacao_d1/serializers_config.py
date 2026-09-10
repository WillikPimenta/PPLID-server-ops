# -*- coding: utf-8 -*-
"""Serializers DRF para APIs de configuração D-1."""
from __future__ import annotations

from rest_framework import serializers

from apps.replicacao_d1.models import (
    ReplicacaoD1Categoria,
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1ConfigHistorico,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1MetaMensal,
    ReplicacaoD1RetroativoConfig,
    ReplicacaoD1Segmento,
    ReplicacaoD1Workflow,
)
from apps.replicacao_d1.normalization import normalize_key
from apps.replicacao_d1.services.config_dto import normalizar_calculadora_params
from apps.replicacao_d1.services.workflow_duplicate import find_conflicting_destino


def _assert_unique_chave(model, chave: str, *, instance=None, field_name: str = "nome") -> None:
    qs = model.objects.filter(chave_normalizada=chave)
    if instance is not None and instance.pk:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        raise serializers.ValidationError(
            {field_name: ["Já existe um registro com este nome (chave normalizada)."]}
        )


class ReplicacaoD1ConfigGeralSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplicacaoD1ConfigGeral
        exclude = ("created_by", "updated_by")
        read_only_fields = (
            "fonte_banco_ativa",
            "config_version",
            "config_hash",
            "created_at",
            "updated_at",
        )


class ReplicacaoD1CalculadoraSerializer(serializers.Serializer):
    calculadora_params = serializers.DictField(child=serializers.FloatField(), required=False)
    meta_produ_diaria = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    meta_produ_diaria_case = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    meta_produ_diaria_bio = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    meta_produ_diaria_redoc = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    usar_amostra_mix_manual_automatico = serializers.BooleanField(required=False)
    amostra_pct_manual = serializers.IntegerField(min_value=0, max_value=100, required=False)
    amostra_pct_automatico = serializers.IntegerField(min_value=0, max_value=100, required=False)

    def validate_calculadora_params(self, value):
        return normalizar_calculadora_params(value)

    def validate(self, attrs):
        manual = attrs.get("amostra_pct_manual")
        auto = attrs.get("amostra_pct_automatico")
        if manual is not None and auto is not None and int(manual) + int(auto) <= 0:
            raise serializers.ValidationError(
                {"amostra_pct_manual": ["Informe ao menos um percentual > 0 (Manual ou Automático)."]}
            )
        return attrs


class ReplicacaoD1SegmentoSerializer(serializers.ModelSerializer):
    categorias_count = serializers.IntegerField(read_only=True, default=0)
    clientes_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ReplicacaoD1Segmento
        fields = (
            "id",
            "nome",
            "chave_normalizada",
            "ativo",
            "created_at",
            "updated_at",
            "categorias_count",
            "clientes_count",
        )
        read_only_fields = ("chave_normalizada", "created_at", "updated_at")

    def validate_nome(self, value):
        chave = normalize_key(value)
        _assert_unique_chave(ReplicacaoD1Segmento, chave, instance=self.instance, field_name="nome")
        return value

    def create(self, validated_data):
        validated_data["chave_normalizada"] = normalize_key(validated_data.get("nome", ""))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "nome" in validated_data:
            validated_data["chave_normalizada"] = normalize_key(validated_data["nome"])
        return super().update(instance, validated_data)


class ReplicacaoD1CategoriaSerializer(serializers.ModelSerializer):
    segmento_nome = serializers.CharField(source="segmento.nome", read_only=True, default="")
    clientes_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ReplicacaoD1Categoria
        fields = (
            "id",
            "nome",
            "chave_normalizada",
            "segmento",
            "segmento_nome",
            "ativo",
            "created_at",
            "updated_at",
            "clientes_count",
        )
        read_only_fields = ("chave_normalizada", "created_at", "updated_at")

    def validate_nome(self, value):
        chave = normalize_key(value)
        _assert_unique_chave(ReplicacaoD1Categoria, chave, instance=self.instance, field_name="nome")
        return value

    def validate_segmento(self, value):
        if value is not None and not ReplicacaoD1Segmento.objects.filter(pk=value.pk).exists():
            raise serializers.ValidationError("Segmento não encontrado.")
        return value

    def create(self, validated_data):
        validated_data["chave_normalizada"] = normalize_key(validated_data.get("nome", ""))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "nome" in validated_data:
            validated_data["chave_normalizada"] = normalize_key(validated_data["nome"])
        return super().update(instance, validated_data)


class ReplicacaoD1ClienteSerializer(serializers.ModelSerializer):
    workflows_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = ReplicacaoD1Cliente
        fields = (
            "id",
            "nome",
            "chave_normalizada",
            "segmento_nome",
            "categoria_nome",
            "segmento",
            "categoria",
            "meta_mensal",
            "ativo",
            "created_at",
            "updated_at",
            "workflows_count",
        )
        read_only_fields = ("chave_normalizada", "segmento_nome", "categoria_nome", "created_at", "updated_at")

    def validate_nome(self, value):
        chave = normalize_key(value)
        _assert_unique_chave(ReplicacaoD1Cliente, chave, instance=self.instance, field_name="nome")
        return value

    def _sync_legacy_names(self, validated_data, instance=None):
        if "segmento" in validated_data:
            seg = validated_data["segmento"]
            validated_data["segmento_nome"] = seg.nome if seg else ""
        elif instance is not None and instance.segmento_id:
            validated_data.setdefault("segmento_nome", instance.segmento.nome)

        if "categoria" in validated_data:
            cat = validated_data["categoria"]
            validated_data["categoria_nome"] = cat.nome if cat else ""
        elif instance is not None and instance.categoria_id:
            validated_data.setdefault("categoria_nome", instance.categoria.nome)

        # Clear incompatible categoria if segmento changed.
        seg = validated_data.get("segmento", getattr(instance, "segmento", None) if instance else None)
        cat = validated_data.get("categoria", getattr(instance, "categoria", None) if instance else None)
        if cat is not None and seg is not None and cat.segmento_id and cat.segmento_id != seg.pk:
            raise serializers.ValidationError(
                {"categoria": ["Categoria não pertence ao segmento selecionado."]}
            )
        if cat is not None and seg is None and "segmento" in validated_data:
            validated_data["categoria"] = None
            validated_data["categoria_nome"] = ""

    def create(self, validated_data):
        validated_data["chave_normalizada"] = normalize_key(validated_data.get("nome", ""))
        self._sync_legacy_names(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "nome" in validated_data:
            validated_data["chave_normalizada"] = normalize_key(validated_data["nome"])
        self._sync_legacy_names(validated_data, instance)
        return super().update(instance, validated_data)


class ReplicacaoD1WorkflowSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True, default="")
    segmento_nome = serializers.SerializerMethodField()
    categoria_nome = serializers.SerializerMethodField()
    workflow_origem_nome = serializers.CharField(
        source="workflow_origem.nome_canonico",
        read_only=True,
        default="",
    )

    class Meta:
        model = ReplicacaoD1Workflow
        fields = (
            "id",
            "nome_canonico",
            "nome_d1",
            "nome_selenium",
            "nome_regra_brflow",
            "fila",
            "cliente",
            "cliente_nome",
            "segmento_nome",
            "categoria_nome",
            "workflow_origem",
            "workflow_origem_nome",
            "chave_normalizada",
            "chave_d1_normalizada",
            "status",
            "amostra_pct_especial",
            "amostra_100",
            "usar_arquivo_csv",
            "nome_observado_original",
            "ativo",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "chave_normalizada",
            "chave_d1_normalizada",
            "ativo",
            "workflow_origem_nome",
            "created_at",
            "updated_at",
        )

    @staticmethod
    def _cliente_segmento_nome(cliente) -> str:
        if not cliente:
            return ""
        if cliente.segmento_id and cliente.segmento:
            return cliente.segmento.nome
        return cliente.segmento_nome or ""

    @staticmethod
    def _cliente_categoria_nome(cliente) -> str:
        if not cliente:
            return ""
        if cliente.categoria_id and cliente.categoria:
            return cliente.categoria.nome
        return cliente.categoria_nome or ""

    def get_segmento_nome(self, obj) -> str:
        return self._cliente_segmento_nome(obj.cliente if obj.cliente_id else None)

    def get_categoria_nome(self, obj) -> str:
        return self._cliente_categoria_nome(obj.cliente if obj.cliente_id else None)

    def validate_nome_canonico(self, value):
        chave = normalize_key(value)
        _assert_unique_chave(
            ReplicacaoD1Workflow,
            chave,
            instance=self.instance,
            field_name="nome_canonico",
        )
        return value

    def validate(self, attrs):
        fila = attrs.get("fila")
        if self.instance is not None and fila is None:
            fila = self.instance.fila
        nome_regra = attrs.get("nome_regra_brflow")
        if self.instance is not None and "nome_regra_brflow" not in attrs:
            nome_regra = self.instance.nome_regra_brflow
        if str(fila or "").strip().casefold() == "redoc" and not str(nome_regra or "").strip():
            raise serializers.ValidationError(
                {"nome_regra_brflow": ["Obrigatório para workflows na fila Redoc."]}
            )

        nome_d1 = attrs.get("nome_d1")
        nome_selenium = attrs.get("nome_selenium")
        status_val = attrs.get("status")
        if self.instance is not None:
            if nome_d1 is None:
                nome_d1 = self.instance.nome_d1
            if nome_selenium is None:
                nome_selenium = self.instance.nome_selenium
            if status_val is None:
                status_val = self.instance.status
        else:
            nome_canonico = attrs.get("nome_canonico", "")
            nome_d1 = nome_d1 or nome_canonico
            nome_selenium = nome_selenium or nome_d1 or nome_canonico

        if status_val == ReplicacaoD1Workflow.STATUS_ATIVO:
            chave_d1 = normalize_key(str(nome_d1 or ""))
            conflict = find_conflicting_destino(
                chave_d1=chave_d1,
                fila=str(fila or ""),
                nome_regra_brflow=str(nome_regra or ""),
                nome_selenium=str(nome_selenium or ""),
                exclude_pk=self.instance.pk if self.instance is not None else None,
            )
            if conflict:
                raise serializers.ValidationError(
                    {
                        "fila": [
                            f"Destino duplicado: já existe cadastro ativo "
                            f"({conflict.nome_canonico}) com o mesmo D-1, fila e regra."
                        ]
                    }
                )
        return attrs

    def create(self, validated_data):
        validated_data["chave_normalizada"] = normalize_key(validated_data.get("nome_canonico", ""))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "nome_canonico" in validated_data:
            validated_data["chave_normalizada"] = normalize_key(validated_data["nome_canonico"])
        return super().update(instance, validated_data)


class ReplicacaoD1WorkflowDuplicateSerializer(serializers.Serializer):
    fila = serializers.CharField(max_length=64)
    nome_regra_brflow = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    nome_canonico = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    nome_selenium = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    status = serializers.ChoiceField(
        choices=ReplicacaoD1Workflow.STATUS_CHOICES,
        required=False,
    )

    def validate(self, attrs):
        if str(attrs.get("fila") or "").strip().casefold() == "redoc":
            if not str(attrs.get("nome_regra_brflow") or "").strip():
                raise serializers.ValidationError(
                    {"nome_regra_brflow": ["Obrigatório para fila Redoc."]}
                )
        return attrs


class ReplicacaoD1EscalaDiaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplicacaoD1EscalaDia
        fields = (
            "id",
            "data",
            "auditores_brflow",
            "auditores_case",
            "auditores_bio",
            "auditores_redoc",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")


class ReplicacaoD1MetaMensalSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)

    class Meta:
        model = ReplicacaoD1MetaMensal
        fields = ("id", "cliente", "cliente_nome", "competencia", "meta", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")


class ReplicacaoD1LedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplicacaoD1LedgerConsumo
        fields = (
            "id",
            "competencia",
            "cliente",
            "cliente_nome",
            "workflow",
            "workflow_nome",
            "workflow_chave",
            "run_id",
            "data_execucao",
            "protocolos",
            "origem",
            "observacao",
            "ajuste",
            "snapshot_hash",
            "config_version",
            "created_at",
        )
        read_only_fields = fields


class ReplicacaoD1LedgerAjusteSerializer(serializers.Serializer):
    competencia = serializers.RegexField(regex=r"^\d{4}-\d{2}$")
    workflow_chave = serializers.CharField(max_length=255)
    consumo_acumulado = serializers.IntegerField(min_value=0)
    cliente_nome = serializers.CharField(required=False, allow_blank=True, default="")
    observacao = serializers.CharField(required=False, allow_blank=True, default="")


class ReplicacaoD1LedgerPurgeRunsSerializer(serializers.Serializer):
    run_ids = serializers.ListField(
        child=serializers.CharField(max_length=64),
        allow_empty=False,
    )


class ReplicacaoD1WorkflowBulkStatusSerializer(serializers.Serializer):
    workflow_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=500,
    )
    status = serializers.ChoiceField(
        choices=(
            ReplicacaoD1Workflow.STATUS_ATIVO,
            ReplicacaoD1Workflow.STATUS_INATIVO,
        )
    )


class ReplicacaoD1RetroativoWorkflowReadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nome_canonico = serializers.CharField()
    nome_d1 = serializers.CharField()
    chave_normalizada = serializers.CharField()
    chave_d1_normalizada = serializers.CharField()
    cliente_nome = serializers.CharField()
    ativo = serializers.BooleanField()


class ReplicacaoD1RetroativoSerializer(serializers.Serializer):
    retroativo_ativo = serializers.BooleanField(required=False, default=False)
    retroativo_data_inicio = serializers.DateField(required=False, allow_null=True)
    retroativo_data_fim = serializers.DateField(required=False, allow_null=True)
    workflow_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )
    workflows = ReplicacaoD1RetroativoWorkflowReadSerializer(many=True, read_only=True)
    workflows_total = serializers.IntegerField(read_only=True, required=False)
    periodo_expirado = serializers.BooleanField(read_only=True, required=False)
    aviso_periodo = serializers.CharField(read_only=True, required=False, allow_blank=True)

    def validate(self, attrs):
        inicio = attrs.get("retroativo_data_inicio")
        fim = attrs.get("retroativo_data_fim")
        if inicio and fim and inicio > fim:
            raise serializers.ValidationError(
                {"retroativo_data_fim": ["A data fim deve ser igual ou posterior à data início."]}
            )
        if inicio and fim:
            dias = (fim - inicio).days + 1
            if dias > ReplicacaoD1RetroativoConfig.MAX_DIAS_PERIODO:
                raise serializers.ValidationError(
                    {
                        "retroativo_data_fim": [
                            f"O período retroativo não pode exceder "
                            f"{ReplicacaoD1RetroativoConfig.MAX_DIAS_PERIODO} dias."
                        ]
                    }
                )
        workflow_ids = attrs.get("workflow_ids")
        if workflow_ids is not None:
            found = set(
                ReplicacaoD1Workflow.objects.filter(
                    pk__in=workflow_ids,
                    ativo=True,
                    status=ReplicacaoD1Workflow.STATUS_ATIVO,
                ).values_list("pk", flat=True)
            )
            missing = sorted(set(workflow_ids) - found)
            if missing:
                raise serializers.ValidationError(
                    {"workflow_ids": [f"Workflow(s) inexistente(s) ou inativo(s): {', '.join(map(str, missing))}."]}
                )
        return attrs


class ReplicacaoD1HistoricoSerializer(serializers.ModelSerializer):
    usuario_username = serializers.CharField(source="usuario.username", read_only=True, default="")

    class Meta:
        model = ReplicacaoD1ConfigHistorico
        fields = (
            "id",
            "entidade",
            "entidade_id",
            "operacao",
            "usuario",
            "usuario_username",
            "created_at",
            "valores_anteriores",
            "valores_novos",
            "lote_id",
        )


class ImportPreviewRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["default_xlsx", "categoria_xlsx", "clientes_csv", "workflows_csv", "escala_csv", "ledger_csv"])
    file = serializers.FileField()


class ImportApplyRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["default_xlsx", "categoria_xlsx", "clientes_csv", "workflows_csv", "escala_csv", "ledger_csv"])
    preview_token = serializers.CharField(max_length=128)


class FonteAtivarSerializer(serializers.Serializer):
    force = serializers.BooleanField(default=False)


class ProjecaoMensalQuerySerializer(serializers.Serializer):
    competencia = serializers.RegexField(regex=r"^[1-9]\d{3}-(0[1-9]|1[0-2])$")


class SnapshotPreviewQuerySerializer(serializers.Serializer):
    competencia_meta = serializers.RegexField(regex=r"^\d{4}-\d{2}$", required=False, allow_blank=True)
    run_id = serializers.CharField(required=False, allow_blank=True)
    data_ref = serializers.CharField(required=False, allow_blank=True)
