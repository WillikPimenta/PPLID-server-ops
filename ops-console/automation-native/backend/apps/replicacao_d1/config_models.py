# -*- coding: utf-8 -*-
"""Modelos de configuração permanente do bot replicacao_auditoria_d1."""
from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.replicacao_d1.normalization import normalize_key


class ReplicacaoD1ConfigGeral(models.Model):
    """Singleton: configuração geral permanente do D-1."""

    SINGLETON_PK = 1

    META_CLIENTE_EXECUCAO = "execucao"
    META_CLIENTE_DATA_REFERENCIA = "data_referencia_d1"
    META_CLIENTE_CHOICES = [
        (META_CLIENTE_EXECUCAO, "Mês da execução"),
        (META_CLIENTE_DATA_REFERENCIA, "Mês da data de referência D-1"),
    ]

    fonte_banco_ativa = models.BooleanField(default=False)
    config_version = models.PositiveIntegerField(default=1)
    config_hash = models.CharField(max_length=64, blank=True, default="")

    agendamento_ativo = models.BooleanField(default=False)
    agendamento_hora_planejamento = models.CharField(max_length=5, default="07:00")
    agendamento_hora_execucao = models.CharField(max_length=5, default="12:00")

    path_config_base = models.CharField(max_length=1024, blank=True, default="")
    path_default_xlsx = models.CharField(max_length=1024, blank=True, default="")
    path_categoria_xlsx = models.CharField(max_length=1024, blank=True, default="")
    path_escala_csv = models.CharField(max_length=1024, blank=True, default="")

    limpar_planos_automatico = models.BooleanField(default=False)
    limpar_planos_ao_gerar = models.BooleanField(default=False)
    limpar_planos_apos_conclusao = models.BooleanField(default=False)
    manter_planos_ultimos_n = models.PositiveIntegerField(default=5)
    dias_retencao_planos = models.PositiveIntegerField(default=0)

    replicacao_cliente_destino = models.CharField(max_length=255, default="GAQ")
    replicacao_cliente_cod = models.CharField(max_length=32, default="751")
    replicacao_workflow_destino = models.CharField(
        max_length=255,
        default="G Auditoria - G Auditoria",
    )
    replicacao_workflow_cod = models.CharField(max_length=32, default="17047")
    replicacao_workflow_destino_31 = models.CharField(
        max_length=255,
        default="Documentoscopia 3.1 - Documentoscopia 3.1",
    )
    replicacao_workflow_cod_31 = models.CharField(max_length=32, default="17426")
    replicacao_workflow_destino_bio = models.CharField(
        max_length=255,
        default="Auditoria Biometria - Auditoria Biometria",
        blank=True,
    )
    replicacao_workflow_cod_bio = models.CharField(max_length=32, blank=True, default="")
    replicacao_workflow_destino_redoc = models.CharField(
        max_length=255,
        default="Auditoria Redoc - Auditoria Redoc",
        blank=True,
    )
    replicacao_workflow_cod_redoc = models.CharField(max_length=32, blank=True, default="")

    replicacao_destino_brflow_ativo = models.BooleanField(default=True)
    replicacao_destino_case31_ativo = models.BooleanField(default=True)
    replicacao_destino_bio_ativo = models.BooleanField(default=True)
    replicacao_destino_redoc_ativo = models.BooleanField(default=True)

    # Meta Produ por auditor/dia — BRFlow (fila G auditoria) e Case (fila 3.1) separados.
    meta_produ_diaria = models.DecimalField(max_digits=12, decimal_places=2, default=300)
    meta_produ_diaria_case = models.DecimalField(max_digits=12, decimal_places=2, default=300)
    meta_produ_diaria_bio = models.DecimalField(max_digits=12, decimal_places=2, default=300)
    meta_produ_diaria_redoc = models.DecimalField(max_digits=12, decimal_places=2, default=300)
    usar_escala_auditores = models.BooleanField(default=True)
    excluir_historico = models.BooleanField(default=True)
    dias_historico = models.PositiveIntegerField(default=30)
    sobrescrever = models.BooleanField(default=False)
    fallback_ultimo_parquet = models.BooleanField(default=True)
    fallback_parquet_dias_ausentes = models.BooleanField(
        default=False,
        help_text="Com fonte_banco_ativa, usa parquet tratado do dia exato quando a partição Rotina estiver vazia.",
    )
    seed = models.IntegerField(default=42)
    sincronizar_workflow_d1 = models.BooleanField(default=True)
    apenas_ativos = models.BooleanField(default=True)
    meta_cliente_ano_mes_ref = models.CharField(
        max_length=32,
        choices=META_CLIENTE_CHOICES,
        default=META_CLIENTE_EXECUCAO,
    )

    calculadora_params = models.JSONField(default=dict, blank=True)
    workflows_amostra_100 = models.JSONField(default=list, blank=True)
    workflows_amostra_pct = models.JSONField(default=dict, blank=True)

    # Mix Manual (matrícula=0) × Automático (matrícula=1) na seleção da amostra.
    usar_amostra_mix_manual_automatico = models.BooleanField(default=False)
    amostra_pct_manual = models.PositiveSmallIntegerField(default=70)
    amostra_pct_automatico = models.PositiveSmallIntegerField(default=30)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_config_geral_criadas",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_config_geral_alteradas",
    )

    class Meta:
        db_table = "replicacao_d1_config_geral"
        verbose_name = "Configuração geral D-1"
        verbose_name_plural = "Configuração geral D-1"

    def __str__(self) -> str:
        return f"ReplicacaoD1ConfigGeral v{self.config_version} (fonte_banco={self.fonte_banco_ativa})"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> ReplicacaoD1ConfigGeral:
        obj, _ = cls.objects.get_or_create(
            pk=cls.SINGLETON_PK,
            defaults={"fonte_banco_ativa": False},
        )
        return obj


class ReplicacaoD1Segmento(models.Model):
    nome = models.CharField(max_length=255)
    chave_normalizada = models.CharField(max_length=255, unique=True)
    ativo = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_segmentos_criados",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_segmentos_alterados",
    )

    class Meta:
        db_table = "replicacao_d1_segmento"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["chave_normalizada"],
                name="replicacao_d1_segmento_chave_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        self.chave_normalizada = normalize_key(self.nome)
        super().save(*args, **kwargs)


class ReplicacaoD1Categoria(models.Model):
    nome = models.CharField(max_length=255)
    chave_normalizada = models.CharField(max_length=255, unique=True)
    segmento = models.ForeignKey(
        ReplicacaoD1Segmento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="categorias",
    )
    ativo = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_categorias_criadas",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_categorias_alteradas",
    )

    class Meta:
        db_table = "replicacao_d1_categoria"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["chave_normalizada"],
                name="replicacao_d1_categoria_chave_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        self.chave_normalizada = normalize_key(self.nome)
        super().save(*args, **kwargs)


class ReplicacaoD1Cliente(models.Model):
    nome = models.CharField(max_length=255)
    chave_normalizada = models.CharField(max_length=255, unique=True, db_index=True)
    segmento_nome = models.CharField(max_length=255, blank=True, default="")
    categoria_nome = models.CharField(max_length=255, blank=True, default="")
    segmento = models.ForeignKey(
        ReplicacaoD1Segmento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clientes",
    )
    categoria = models.ForeignKey(
        ReplicacaoD1Categoria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clientes",
    )
    meta_mensal = models.IntegerField(null=True, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_clientes_criados",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_clientes_alterados",
    )

    class Meta:
        db_table = "replicacao_d1_cliente"
        ordering = ["nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["chave_normalizada"],
                name="replicacao_d1_cliente_chave_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        self.chave_normalizada = normalize_key(self.nome)
        if self.segmento_id:
            self.segmento_nome = self.segmento.nome
        if self.categoria_id:
            self.categoria_nome = self.categoria.nome
        super().save(*args, **kwargs)


class ReplicacaoD1Workflow(models.Model):
    STATUS_ATIVO = "ATIVO"
    STATUS_INATIVO = "INATIVO"
    STATUS_PENDENTE = "PENDENTE"
    STATUS_CHOICES = [
        (STATUS_ATIVO, "Ativo"),
        (STATUS_INATIVO, "Inativo"),
        (STATUS_PENDENTE, "Pendente"),
    ]

    nome_canonico = models.CharField(max_length=255)
    nome_d1 = models.CharField(max_length=255, blank=True, default="")
    nome_selenium = models.CharField(max_length=255, blank=True, default="")
    nome_regra_brflow = models.CharField(max_length=255, blank=True, default="")
    fila = models.CharField(max_length=64, default="G auditoria")
    cliente = models.ForeignKey(
        ReplicacaoD1Cliente,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workflows",
    )
    chave_normalizada = models.CharField(max_length=255, unique=True)
    chave_d1_normalizada = models.CharField(max_length=255, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_ATIVO,
        db_index=True,
    )
    amostra_pct_especial = models.IntegerField(null=True, blank=True)
    amostra_100 = models.BooleanField(default=False)
    usar_arquivo_csv = models.BooleanField(default=True)
    nome_observado_original = models.CharField(max_length=255, blank=True, default="")
    workflow_origem = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="destinos_replicacao",
        help_text="Workflow base quando este cadastro é uma cópia para outra fila/destino BRFlow.",
    )
    ativo = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_workflows_criados",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_workflows_alterados",
    )

    class Meta:
        db_table = "replicacao_d1_workflow"
        ordering = ["nome_canonico"]
        constraints = [
            models.UniqueConstraint(
                fields=["chave_normalizada"],
                name="replicacao_d1_workflow_chave_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["cliente"], name="replicacao_d1_wf_cfg_cliente"),
        ]

    def __str__(self) -> str:
        return self.nome_canonico

    def save(self, *args, **kwargs):
        self.chave_normalizada = normalize_key(self.nome_canonico)
        self.chave_d1_normalizada = normalize_key(self.nome_d1) if self.nome_d1 else ""
        self.ativo = self.status == self.STATUS_ATIVO
        super().save(*args, **kwargs)


class ReplicacaoD1MetaMensal(models.Model):
    cliente = models.ForeignKey(
        ReplicacaoD1Cliente,
        on_delete=models.PROTECT,
        related_name="metas_mensais",
    )
    competencia = models.CharField(max_length=7)
    meta = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_metas_criadas",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_metas_alteradas",
    )

    class Meta:
        db_table = "replicacao_d1_meta_mensal"
        ordering = ["-competencia", "cliente"]
        constraints = [
            models.UniqueConstraint(
                fields=["cliente", "competencia"],
                name="replicacao_d1_meta_cli_comp_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.cliente} / {self.competencia}: {self.meta}"


class ReplicacaoD1EscalaDia(models.Model):
    data = models.DateField(unique=True, db_index=True)
    auditores_brflow = models.PositiveIntegerField(default=0)
    auditores_case = models.PositiveIntegerField(default=0)
    auditores_bio = models.PositiveIntegerField(default=0)
    auditores_redoc = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "replicacao_d1_escala_dia"
        ordering = ["-data"]
        constraints = [
            models.UniqueConstraint(
                fields=["data"],
                name="replicacao_d1_escala_data_uniq",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.data} BRFlow={self.auditores_brflow} Case={self.auditores_case} "
            f"Bio={self.auditores_bio} Redoc={self.auditores_redoc}"
        )


class ReplicacaoD1LedgerConsumo(models.Model):
    competencia = models.CharField(max_length=7, db_index=True)
    cliente = models.ForeignKey(
        ReplicacaoD1Cliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_consumos",
    )
    cliente_nome = models.CharField(max_length=255, blank=True, default="")
    workflow = models.ForeignKey(
        ReplicacaoD1Workflow,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_consumos",
    )
    workflow_nome = models.CharField(max_length=255, blank=True, default="")
    workflow_chave = models.CharField(max_length=255, blank=True, default="")
    run_id = models.CharField(max_length=64, db_index=True)
    data_execucao = models.DateTimeField()
    protocolos = models.IntegerField()
    origem = models.CharField(max_length=64)
    observacao = models.TextField(blank=True, default="")
    ajuste = models.IntegerField(default=0)
    snapshot_hash = models.CharField(max_length=64, blank=True, default="")
    config_version = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "replicacao_d1_ledger_consumo"
        ordering = ["-data_execucao", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["run_id", "competencia", "workflow_chave", "origem"],
                name="replicacao_d1_ledger_idempot_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["cliente"], name="replicacao_d1_ledger_cliente"),
        ]

    def __str__(self) -> str:
        return f"{self.competencia} / {self.workflow_chave} / {self.run_id} ({self.origem})"


class ReplicacaoD1ConfigHistorico(models.Model):
    entidade = models.CharField(max_length=128, db_index=True)
    entidade_id = models.CharField(max_length=64)
    operacao = models.CharField(max_length=32)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_config_historicos",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    valores_anteriores = models.JSONField(default=dict, blank=True)
    valores_novos = models.JSONField(default=dict, blank=True)
    lote_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "replicacao_d1_config_historico"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.operacao} {self.entidade}#{self.entidade_id}"


class ReplicacaoD1RetroativoConfig(models.Model):
    """Singleton: clientes e período retroativo para ampliar pool do planejamento."""

    SINGLETON_PK = 1
    MAX_DIAS_PERIODO = 31

    id = models.PositiveSmallIntegerField(
        default=SINGLETON_PK,
        editable=False,
        primary_key=True,
    )
    retroativo_ativo = models.BooleanField(default=False)
    retroativo_data_inicio = models.DateField(null=True, blank=True)
    retroativo_data_fim = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_retroativo_alterado",
    )

    class Meta:
        db_table = "replicacao_d1_retroativo_config"
        verbose_name = "Configuração retroativa D-1"
        verbose_name_plural = "Configuração retroativa D-1"

    def __str__(self) -> str:
        return f"Retroativo D-1 ativo={self.retroativo_ativo}"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> ReplicacaoD1RetroativoConfig:
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK, defaults={"retroativo_ativo": False})
        return obj


class ReplicacaoD1RetroativoCliente(models.Model):
    config = models.ForeignKey(
        ReplicacaoD1RetroativoConfig,
        on_delete=models.CASCADE,
        related_name="clientes",
    )
    cliente = models.ForeignKey(
        ReplicacaoD1Cliente,
        on_delete=models.CASCADE,
        related_name="retroativo_vinculos",
    )
    ativo = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "replicacao_d1_retroativo_cliente"
        constraints = [
            models.UniqueConstraint(
                fields=["config", "cliente"],
                name="replicacao_d1_retroativo_cli_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"Retroativo: {self.cliente_id}"


class ReplicacaoD1RetroativoWorkflow(models.Model):
    config = models.ForeignKey(
        ReplicacaoD1RetroativoConfig,
        on_delete=models.CASCADE,
        related_name="workflows",
    )
    workflow = models.ForeignKey(
        ReplicacaoD1Workflow,
        on_delete=models.CASCADE,
        related_name="retroativo_vinculos",
    )
    ativo = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "replicacao_d1_retroativo_workflow"
        constraints = [
            models.UniqueConstraint(
                fields=["config", "workflow"],
                name="replicacao_d1_retroativo_wf_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"Retroativo WF: {self.workflow_id}"


class ReplicacaoD1ConfigSnapshot(models.Model):
    run_id = models.CharField(max_length=64, unique=True, db_index=True)
    config_version = models.PositiveIntegerField()
    config_hash = models.CharField(max_length=64)
    snapshot_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "replicacao_d1_config_snapshot"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["run_id"],
                name="replicacao_d1_snapshot_run_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"Snapshot {self.run_id} v{self.config_version}"
