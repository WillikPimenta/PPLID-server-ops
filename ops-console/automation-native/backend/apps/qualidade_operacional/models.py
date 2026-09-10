# -*- coding: utf-8 -*-
"""Tabelas de Qualidade Operacional: auditados e falhas."""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class QualidadeAtivoManager(models.Manager):
    """Manager público: registros suprimidos nunca entram em indicadores."""

    def get_queryset(self):
        return super().get_queryset().filter(admin_suppressed=False)


class QualidadeRegistroAdministravelMixin(models.Model):
    """Estado materializado do ledger administrativo nos fatos de Qualidade."""

    admin_identity = models.CharField(max_length=96, blank=True, default="", db_index=True)
    admin_suppressed = models.BooleanField(default=False, db_index=True)
    admin_revision = models.PositiveIntegerField(default=0)

    objects = QualidadeAtivoManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True


class QualidadeAuditado(QualidadeRegistroAdministravelMixin):
    """Espelho de data/qualidade/tabela_auditado_com_tipo_de_conclusao_*.tsv."""

    data = models.DateField(null=True, blank=True, db_index=True)
    data_analise = models.DateField(null=True, blank=True)
    data_analise_intranet = models.DateField(null=True, blank=True)
    data_analise_origem = models.DateField(null=True, blank=True)
    data_criacao_origem = models.DateField(null=True, blank=True)
    data_conclusao_origem = models.DateField(null=True, blank=True)
    data_recepcao_contestacao = models.DateField(null=True, blank=True)
    data_encerramento_atividade_intranet = models.DateField(null=True, blank=True)
    id_cliente = models.IntegerField(null=True, blank=True)
    id_workflow = models.IntegerField(null=True, blank=True)
    tipo_analise = models.CharField(max_length=128, blank=True, default="")
    matricula = models.CharField(max_length=64, blank=True, default="", db_index=True)
    matricula_auditor = models.CharField(max_length=64, blank=True, default="")
    protocolo = models.CharField(max_length=100, blank=True, default="", db_index=True)
    cenario = models.CharField(max_length=512, blank=True, default="")
    etapa = models.CharField(max_length=256, blank=True, default="")
    status = models.CharField(max_length=128, blank=True, default="")
    irregularidades_apontadas = models.TextField(blank=True, default="")
    cadastrado_anteriormente = models.CharField(max_length=128, blank=True, default="")
    id_operations = models.IntegerField(null=True, blank=True)
    resultado_origem = models.CharField(max_length=128, blank=True, default="")
    resultado_destino = models.CharField(max_length=128, blank=True, default="")
    protocolo_destino = models.CharField(max_length=100, blank=True, default="")
    tipo_conclusao = models.CharField(max_length=64, blank=True, default="")
    tipo_registro = models.CharField(max_length=16, blank=True, default="", db_index=True)
    origem_tratado = models.CharField(max_length=16, blank=True, default="", db_index=True)
    tipo_falha_original = models.CharField(max_length=128, blank=True, default="", db_index=True)
    procedencia = models.CharField(max_length=32, blank=True, default="", db_index=True)
    localidade_documento = models.CharField(max_length=8, blank=True, default="")

    source_file = models.CharField(max_length=255, blank=True, default="")
    imported_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "qualidade_auditado"
        indexes = [
            models.Index(fields=["id_cliente", "id_workflow"], name="qo_aud_cli_wf_idx"),
            models.Index(fields=["tipo_analise"], name="qo_aud_tipo_idx"),
            models.Index(fields=["data", "protocolo"], name="qo_aud_data_prot_idx"),
            models.Index(fields=["tipo_conclusao"], name="qo_aud_tipo_conc_idx"),
            models.Index(fields=["data_analise"], name="qo_aud_analise_idx"),
            models.Index(fields=["matricula", "data"], name="qo_aud_mat_data_idx"),
            models.Index(
                fields=["matricula", "data_analise"],
                name="qo_aud_mat_ana_idx",
            ),
            models.Index(
                fields=["data", "id_cliente"],
                include=["id"],
                name="qo_aud_dt_cli_cov",
            ),
            models.Index(
                fields=["data_analise", "id_cliente"],
                include=["id"],
                name="qo_aud_an_cli_cov",
            ),
            models.Index(
                fields=["data", "matricula"],
                include=["id", "tipo_conclusao", "protocolo"],
                name="qo_aud_dt_mat_cov",
            ),
            models.Index(
                fields=["data_analise", "matricula"],
                include=["id", "tipo_conclusao", "protocolo"],
                name="qo_aud_an_mat_cov",
            ),
            models.Index(fields=["localidade_documento"], name="qo_aud_loc_doc_idx"),
            models.Index(
                fields=["data_recepcao_contestacao"],
                name="qo_aud_cont_rec_idx",
                condition=models.Q(data_recepcao_contestacao__isnull=False),
            ),
            models.Index(
                fields=["data", "matricula_auditor"],
                name="qo_aud_ret_dt_aud_idx",
                condition=models.Q(status="retirada"),
            ),
        ]

    def __str__(self) -> str:
        return f"Auditado {self.protocolo} ({self.data})"


class QualidadeFalha(QualidadeRegistroAdministravelMixin):
    """Espelho de data/qualidade/tabela_falhas.tsv (sem colunas TBGA_* vazias)."""

    nome_origem = models.CharField(max_length=128, blank=True, default="")
    frk_colaborador = models.CharField(max_length=64, blank=True, default="")
    protocolo = models.CharField(max_length=100, blank=True, default="", db_index=True)
    case_key = models.CharField(max_length=180, blank=True, default="", db_index=True)
    id_cliente = models.IntegerField(null=True, blank=True)
    id_workflow = models.IntegerField(null=True, blank=True)
    id_operations = models.IntegerField(null=True, blank=True)
    tipo_analise = models.CharField(max_length=128, blank=True, default="")
    frk_gerenciamento_fluxo = models.CharField(max_length=64, blank=True, default="")
    modulo = models.CharField(max_length=128, blank=True, default="")
    cenario = models.TextField(blank=True, default="")
    data = models.DateField(null=True, blank=True)
    usuario_auditor = models.CharField(max_length=64, blank=True, default="")
    matricula = models.CharField(max_length=64, blank=True, default="", db_index=True)
    data_analise = models.DateField(null=True, blank=True, db_index=True)
    data_analise_intranet = models.DateField(null=True, blank=True)
    data_analise_origem = models.DateField(null=True, blank=True)
    data_criacao_origem = models.DateField(null=True, blank=True)
    data_conclusao_origem = models.DateField(null=True, blank=True)
    data_recepcao_contestacao = models.DateField(null=True, blank=True)
    data_encerramento_atividade_intranet = models.DateField(null=True, blank=True)
    etapa = models.CharField(max_length=256, blank=True, default="")
    tipo_falha = models.CharField(max_length=128, blank=True, default="")
    uf = models.CharField(max_length=8, blank=True, default="")
    tipo_documento = models.CharField(max_length=128, blank=True, default="")
    nivel_dificuldade = models.CharField(max_length=128, blank=True, default="")
    des_problemas = models.TextField(blank=True, default="")
    tendencia = models.CharField(max_length=128, blank=True, default="")
    novo_resultado = models.CharField(max_length=128, blank=True, default="")
    tipo_solicitacao = models.CharField(max_length=128, blank=True, default="")
    tipo_modulo = models.CharField(max_length=128, blank=True, default="")
    prk_colaborador = models.CharField(max_length=64, blank=True, default="")
    resultado_analise = models.CharField(max_length=128, blank=True, default="")
    numero_solicitacao = models.CharField(max_length=64, blank=True, default="")
    qualidade_imagem = models.CharField(max_length=128, blank=True, default="")
    origem_analise = models.CharField(max_length=128, blank=True, default="")
    data_diferenca = models.CharField(max_length=64, blank=True, default="")
    tipo_modulo_2 = models.CharField(max_length=128, blank=True, default="")
    categoria_falha = models.CharField(max_length=128, blank=True, default="")
    localidade = models.CharField(max_length=128, blank=True, default="")
    localidade_documento = models.CharField(max_length=8, blank=True, default="")
    agente_ativo = models.CharField(max_length=64, blank=True, default="")
    lider = models.CharField(max_length=256, blank=True, default="")
    tipo_falha_oficial = models.CharField(max_length=128, blank=True, default="")
    nivel_dificuldade_confer = models.CharField(max_length=128, blank=True, default="")
    sub_segmento = models.CharField(max_length=128, blank=True, default="")
    segmento = models.CharField(max_length=128, blank=True, default="")
    condicao_metrica = models.CharField(max_length=128, blank=True, default="")
    tipo_registro = models.CharField(max_length=16, blank=True, default="", db_index=True)
    origem_tratado = models.CharField(max_length=16, blank=True, default="", db_index=True)
    tipo_falha_original = models.CharField(max_length=128, blank=True, default="", db_index=True)
    procedencia = models.CharField(max_length=32, blank=True, default="", db_index=True)

    source_file = models.CharField(max_length=255, blank=True, default="")
    imported_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "qualidade_falha"
        indexes = [
            models.Index(fields=["id_cliente", "id_workflow"], name="qo_fal_cli_wf_idx"),
            models.Index(fields=["tipo_falha"], name="qo_fal_tipo_idx"),
            models.Index(fields=["tipo_falha_oficial"], name="qo_fal_tipo_of_idx"),
            models.Index(fields=["localidade"], name="qo_fal_loc_idx"),
            models.Index(fields=["localidade_documento"], name="qo_fal_loc_doc_idx"),
            models.Index(fields=["data"], name="qo_fal_auditoria_idx"),
            models.Index(fields=["matricula", "data"], name="qo_fal_mat_data_idx"),
            models.Index(
                fields=["matricula", "data_analise"],
                name="qo_fal_mat_ana_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Falha {self.protocolo} ({self.data_analise})"


class QualidadeImportBatch(models.Model):
    """Upload mensal ou carga retroativa validada antes de substituir competências."""

    KIND_AUDITADOS = "auditados"
    KIND_FALHAS = "falhas"
    KIND_CHOICES = [(KIND_AUDITADOS, "Auditorias"), (KIND_FALHAS, "Falhas")]

    MODE_MONTHLY = "monthly"
    MODE_RETROACTIVE = "retroactive"
    MODE_CHOICES = [
        (MODE_MONTHLY, "Carga mensal"),
        (MODE_RETROACTIVE, "Carga retroativa"),
    ]

    STATUS_UPLOADING = "uploading"
    STATUS_VALIDATING = "validating"
    STATUS_VALIDATED = "validated"
    STATUS_PROCESSING = "processing"
    STATUS_CANCEL_REQUESTED = "cancel_requested"
    STATUS_CANCELLING = "cancelling"
    STATUS_CANCELLED = "cancelled"
    STATUS_COMPLETED = "completed"
    STATUS_RESTORING = "restoring"
    STATUS_RESTORED = "restored"
    STATUS_FAILED = "failed"
    STATUS_ROLLBACK_FAILED = "rollback_failed"
    STATUS_CHOICES = [
        (STATUS_UPLOADING, "Enviando"),
        (STATUS_VALIDATING, "Validando"),
        (STATUS_VALIDATED, "Validado"),
        (STATUS_PROCESSING, "Importando"),
        (STATUS_CANCEL_REQUESTED, "Cancelamento solicitado"),
        (STATUS_CANCELLING, "Cancelando"),
        (STATUS_CANCELLED, "Cancelado"),
        (STATUS_COMPLETED, "Concluído"),
        (STATUS_RESTORING, "Restaurando"),
        (STATUS_RESTORED, "Restaurado"),
        (STATUS_FAILED, "Falhou"),
        (STATUS_ROLLBACK_FAILED, "Rollback falhou"),
    ]

    ROLLBACK_NONE = ""
    ROLLBACK_PENDING = "pending"
    ROLLBACK_IN_PROGRESS = "in_progress"
    ROLLBACK_COMPLETED = "completed"
    ROLLBACK_FAILED = "failed"
    ROLLBACK_STATUS_CHOICES = [
        (ROLLBACK_NONE, "—"),
        (ROLLBACK_PENDING, "Pendente"),
        (ROLLBACK_IN_PROGRESS, "Em andamento"),
        (ROLLBACK_COMPLETED, "Concluído"),
        (ROLLBACK_FAILED, "Falhou"),
    ]

    CANCELABLE_STATUSES = frozenset(
        {
            STATUS_UPLOADING,
            STATUS_VALIDATING,
            STATUS_VALIDATED,
            STATUS_PROCESSING,
        }
    )
    CANCEL_PENDING_STATUSES = frozenset(
        {
            STATUS_CANCEL_REQUESTED,
            STATUS_CANCELLING,
        }
    )
    TERMINAL_STATUSES = frozenset(
        {
            STATUS_CANCELLED,
            STATUS_COMPLETED,
            STATUS_RESTORED,
            STATUS_FAILED,
            STATUS_ROLLBACK_FAILED,
        }
    )
    ACTIVE_WORKER_STATUSES = frozenset(
        {
            STATUS_UPLOADING,
            STATUS_VALIDATING,
            STATUS_PROCESSING,
            STATUS_RESTORING,
            STATUS_CANCEL_REQUESTED,
            STATUS_CANCELLING,
        }
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)
    import_mode = models.CharField(
        max_length=16, choices=MODE_CHOICES, default=MODE_MONTHLY, db_index=True
    )
    competencia = models.DateField(db_index=True, null=True, blank=True)
    filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qualidade_import_batches",
    )
    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES, default=STATUS_VALIDATING, db_index=True
    )
    phase = models.CharField(max_length=128, blank=True, default="")
    progress_percent = models.PositiveSmallIntegerField(default=0)
    source_path = models.TextField(blank=True, default="")
    backup_path = models.TextField(blank=True, default="")
    file_size = models.PositiveBigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    rows_total = models.PositiveIntegerField(default=0)
    rows_valid = models.PositiveIntegerField(default=0)
    rows_outside_period = models.PositiveIntegerField(default=0)
    rows_errors = models.PositiveIntegerField(default=0)
    distinct_protocols = models.PositiveIntegerField(default=0)
    duplicate_protocol_rows = models.PositiveIntegerField(default=0)
    case_key_conflicts = models.JSONField(default=list, blank=True)
    rows_importable = models.PositiveIntegerField(default=0)
    previous_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    failure_detail = models.TextField(blank=True, default="")
    failure_code = models.CharField(max_length=64, blank=True, default="")
    last_error_at = models.DateTimeField(null=True, blank=True)
    # Cancelamento / rollback
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qualidade_import_cancellations",
    )
    cancellation_reason = models.TextField(blank=True, default="")
    rollback_status = models.CharField(
        max_length=16, choices=ROLLBACK_STATUS_CHOICES, blank=True, default=ROLLBACK_NONE
    )
    rollback_detail = models.TextField(blank=True, default="")
    worker_token = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # Retroativa / upload retomável
    chunks_expected = models.PositiveIntegerField(default=0)
    chunks_received = models.PositiveIntegerField(default=0)
    bytes_received = models.PositiveBigIntegerField(default=0)
    upload_complete = models.BooleanField(default=False)
    expected_checksum = models.CharField(max_length=64, blank=True, default="")
    month_plan = models.JSONField(default=list, blank=True)
    current_competencia = models.CharField(max_length=7, blank=True, default="")
    months_done = models.PositiveIntegerField(default=0)
    months_total = models.PositiveIntegerField(default=0)
    # Telemetria de progresso (validação / importação)
    rows_processed = models.PositiveBigIntegerField(default=0)
    bytes_processed = models.PositiveBigIntegerField(default=0)
    heartbeat_at = models.DateTimeField(null=True, blank=True, db_index=True)
    processing_rate = models.FloatField(default=0)  # linhas/s
    estimated_seconds_remaining = models.PositiveIntegerField(null=True, blank=True)
    current_stage = models.CharField(max_length=32, blank=True, default="")
    progress_indeterminate = models.BooleanField(default=False)
    stage_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "qualidade_import_batch"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "competencia"],
                condition=models.Q(
                    import_mode="monthly",
                    status__in=["processing", "restoring"],
                    competencia__isnull=False,
                ),
                name="qo_import_active_kind_month_uniq",
            ),
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(
                    import_mode="retroactive",
                    status__in=[
                        "uploading",
                        "validating",
                        "processing",
                        "restoring",
                        "cancel_requested",
                        "cancelling",
                    ],
                ),
                name="qo_import_active_retro_kind_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["kind", "competencia", "status"],
                name="qo_import_kind_month_idx",
            ),
            models.Index(
                fields=["import_mode", "status"],
                name="qo_import_mode_status_idx",
            ),
        ]


class QualidadeImportChunk(models.Model):
    """Bloco de upload retomável de uma carga (mensal grande ou retroativa)."""

    id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(
        QualidadeImportBatch,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    index = models.PositiveIntegerField()
    size = models.PositiveIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    path = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qualidade_import_chunk"
        ordering = ["index"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "index"],
                name="qo_import_chunk_batch_idx_uniq",
            )
        ]


class QualidadeIntranetProjection(models.Model):
    """Ponte idempotente Intranet (`auditoria_falha_cadastro`) → fatos EO."""

    STATUS_OK = "ok"
    STATUS_SKIPPED = "skipped"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_OK, "OK"),
        (STATUS_SKIPPED, "Ignorado"),
        (STATUS_ERROR, "Erro"),
    ]

    source = models.OneToOneField(
        "auditoria.AuditoriaFalhaCadastro",
        on_delete=models.CASCADE,
        related_name="qualidade_projection",
    )
    auditado = models.OneToOneField(
        QualidadeAuditado,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intranet_projection",
    )
    falha = models.OneToOneField(
        QualidadeFalha,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intranet_projection",
    )
    mapping_version = models.PositiveSmallIntegerField(default=1)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_fingerprint = models.CharField(max_length=64, blank=True, default="", db_index=True)
    synced_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sync_status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_OK, db_index=True
    )
    sync_error = models.TextField(blank=True, default="")
    warnings = models.JSONField(default=list, blank=True)
    g_auditoria_projection = models.ForeignKey(
        "QualidadeGAuditoriaProjection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="intranet_audit_matches",
    )
    g_auditoria_match_status = models.CharField(
        max_length=16,
        choices=[
            ("unmatched", "Não localizado"),
            ("matched", "Localizado"),
            ("ambiguous", "Ambíguo"),
        ],
        blank=True,
        default="",
        db_index=True,
    )
    g_auditoria_match_observation = models.CharField(
        max_length=128,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "qualidade_intranet_projection"
        indexes = [
            models.Index(fields=["sync_status", "synced_at"], name="qo_intra_sync_idx"),
            models.Index(fields=["mapping_version"], name="qo_intra_mapver_idx"),
        ]

    def __str__(self) -> str:
        return f"Projection source={self.source_id} status={self.sync_status}"


class QualidadeRegistroEstado(models.Model):
    """Tombstone/override durável, independente do ciclo de vida do fato importado."""

    KIND_AUDITADO = "auditado"
    KIND_FALHA = "falha"
    KIND_CHOICES = [(KIND_AUDITADO, "Auditado"), (KIND_FALHA, "Falha")]

    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    identity_key = models.CharField(max_length=96)
    current_object_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    suppressed = models.BooleanField(default=False, db_index=True)
    overrides = models.JSONField(default=dict, blank=True)
    revision = models.PositiveIntegerField(default=0)
    suppression_parent_key = models.CharField(max_length=96, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="qualidade_registros_atualizados",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qualidade_registro_estado"
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "identity_key"],
                name="qo_reg_estado_kind_ident_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["kind", "suppressed"], name="qo_reg_estado_kind_sup_idx"),
        ]


class QualidadeRegistroHistorico(models.Model):
    """Ledger imutável das intervenções administrativas."""

    ACTION_UPDATE = "update"
    ACTION_SUPPRESS = "suppress"
    ACTION_RESTORE = "restore"
    ACTION_CHOICES = [
        (ACTION_UPDATE, "Editar"),
        (ACTION_SUPPRESS, "Suprimir"),
        (ACTION_RESTORE, "Restaurar"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    estado = models.ForeignKey(
        QualidadeRegistroEstado,
        on_delete=models.PROTECT,
        related_name="historico",
    )
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField()
    ticket = models.CharField(max_length=128)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="qualidade_registros_historico",
    )
    idempotency_key = models.UUIDField(unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "qualidade_registro_historico"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["estado", "revision"],
                name="qo_reg_hist_estado_rev_uniq",
            )
        ]


class QualidadeDimAlias(models.Model):
    """Alias auditável de nome abreviado → id_cliente/id_workflow."""

    KIND_CLIENTE = "cliente"
    KIND_WORKFLOW = "workflow"
    KIND_CHOICES = [
        (KIND_CLIENTE, "Cliente"),
        (KIND_WORKFLOW, "Workflow"),
    ]

    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)
    alias_key = models.CharField(max_length=255, db_index=True)
    target_id = models.IntegerField()
    evidence = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_dim_alias"
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "alias_key"],
                name="qo_dim_alias_kind_key_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "active"], name="qo_dim_alias_kind_act"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.alias_key}→{self.target_id}"


class QualidadeGAuditoriaProjection(models.Model):
    """Ponte idempotente staging G Auditoria → auditado EO."""

    staging = models.OneToOneField(
        "rotina_bruto.RotinaGAuditoriaRecord",
        on_delete=models.CASCADE,
        related_name="qualidade_projection",
    )
    auditado = models.OneToOneField(
        QualidadeAuditado,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="g_auditoria_projection",
    )
    mapping_version = models.PositiveSmallIntegerField(default=1)
    source_fingerprint = models.CharField(max_length=64, blank=True, default="", db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    warnings = models.JSONField(default=list, blank=True)
    projected_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "qualidade_g_auditoria_projection"
        indexes = [
            models.Index(
                fields=["is_active", "projected_at"],
                name="qo_gaud_active_proj_idx",
            ),
            models.Index(
                fields=["mapping_version"],
                name="qo_gaud_mapver_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"G Auditoria staging={self.staging_id} auditado={self.auditado_id}"


class QualidadeGAuditoriaFailureReconciliation(models.Model):
    """Vínculo independente entre falha da Intranet e auditado do Parquet."""

    STATUS_MATCHED = "matched"
    STATUS_UNMATCHED = "unmatched"
    STATUS_AMBIGUOUS = "ambiguous"
    STATUS_CHOICES = [
        (STATUS_MATCHED, "Localizada"),
        (STATUS_UNMATCHED, "Não localizada"),
        (STATUS_AMBIGUOUS, "Correspondência ambígua"),
    ]

    falha = models.OneToOneField(
        QualidadeFalha,
        on_delete=models.CASCADE,
        related_name="g_auditoria_reconciliation",
    )
    projection = models.ForeignKey(
        QualidadeGAuditoriaProjection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failure_reconciliations",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, db_index=True)
    observation = models.CharField(max_length=128, blank=True, default="")
    mapping_version = models.PositiveSmallIntegerField(default=1)
    original_payload = models.JSONField(default=dict, blank=True)
    differences = models.JSONField(default=dict, blank=True)
    candidate_count = models.PositiveIntegerField(default=0)
    reconciled_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "qualidade_g_auditoria_failure_reconciliation"
        indexes = [
            models.Index(
                fields=["status", "reconciled_at"],
                name="qo_gaud_fail_rec_idx",
            ),
            models.Index(
                fields=["mapping_version"],
                name="qo_gaud_fail_map_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Falha={self.falha_id} status={self.status}"


class QualidadeFiltroOpcao(models.Model):
    """Catálogo pequeno para não executar DISTINCT sobre as tabelas de fatos."""

    dimensao = models.CharField(max_length=32)
    valor = models.CharField(max_length=256)

    class Meta:
        db_table = "qualidade_filtro_opcao"
        ordering = ["dimensao", "valor"]
        constraints = [
            models.UniqueConstraint(
                fields=["dimensao", "valor"],
                name="qo_filtro_dim_val_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["dimensao", "valor"], name="qo_filtro_dim_val_idx")
        ]

    def __str__(self) -> str:
        return f"{self.dimensao}: {self.valor}"


class QualidadeAgenteAcao(models.Model):
    """Ação de acompanhamento do líder para um operador (modal Agentes)."""

    class Tipo(models.TextChoices):
        RECICLAGEM = "reciclagem", "Reciclagem de procedimento"
        CHECKLIST = "checklist", "Reforço de checklist"
        MONITORIA = "monitoria", "Monitoria dirigida"
        CALIBRACAO = "calibracao", "Calibração com qualidade"
        ACOMPANHAMENTO = "acompanhamento", "Acompanhamento periódico"
        AMOSTRAGEM = "amostragem", "Nova amostragem de auditorias"
        FEEDBACK = "feedback", "Feedback individual"
        OUTRO = "outro", "Outro"

    class Prioridade(models.TextChoices):
        BAIXA = "baixa", "Baixa"
        MEDIA = "media", "Média"
        ALTA = "alta", "Alta"
        CRITICA = "critica", "Crítica"

    class Status(models.TextChoices):
        ABERTA = "aberta", "Aberta"
        EM_ANDAMENTO = "em_andamento", "Em andamento"
        CONCLUIDA = "concluida", "Concluída"
        CANCELADA = "cancelada", "Cancelada"

    class Frequencia(models.TextChoices):
        UNICA = "unica", "Única"
        DIARIA = "diaria", "Diária"
        SEMANAL = "semanal", "Semanal"
        QUINZENAL = "quinzenal", "Quinzenal"

    matricula = models.CharField(max_length=64, db_index=True)
    titulo = models.CharField(max_length=255)
    descricao = models.TextField(blank=True, default="")
    tipo = models.CharField(
        max_length=32, choices=Tipo.choices, default=Tipo.OUTRO, db_index=True
    )
    responsavel = models.CharField(max_length=255, blank=True, default="")
    prioridade = models.CharField(
        max_length=16, choices=Prioridade.choices, default=Prioridade.MEDIA
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ABERTA, db_index=True
    )
    prazo = models.DateField(null=True, blank=True)
    categoria_falha = models.CharField(max_length=255, blank=True, default="")
    auditorias_vinculadas = models.JSONField(default=list, blank=True)
    criterio_sucesso = models.TextField(blank=True, default="")
    frequencia = models.CharField(
        max_length=16, choices=Frequencia.choices, default=Frequencia.UNICA
    )
    observacoes = models.TextField(blank=True, default="")
    created_by = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    concluida_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "qualidade_agente_acao"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["matricula", "status"], name="qo_acao_mat_status_idx"),
            models.Index(fields=["matricula", "-created_at"], name="qo_acao_mat_created_idx"),
        ]

    def __str__(self) -> str:
        return f"Ação {self.pk} · {self.matricula} · {self.titulo}"
