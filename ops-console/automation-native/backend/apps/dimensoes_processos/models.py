"""Dimensões e regras de negócio (Identificação dos Processos)."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class DimProduto(models.Model):
    id_produto = models.PositiveSmallIntegerField(primary_key=True)
    tipo_produto = models.CharField(max_length=64)

    class Meta:
        db_table = "dim_produto"
        ordering = ["id_produto"]

    def __str__(self) -> str:
        return f"{self.id_produto} — {self.tipo_produto}"


class DimGrupoServico(models.Model):
    id_grupo = models.PositiveIntegerField(primary_key=True)
    nome = models.CharField(max_length=255)

    class Meta:
        db_table = "dim_grupo_servico"
        ordering = ["id_grupo"]
        verbose_name = "Grupo de serviço"
        verbose_name_plural = "Grupos de serviço"

    def __str__(self) -> str:
        return f"{self.id_grupo} — {self.nome}"


class DimServico(models.Model):
    id_servico = models.PositiveIntegerField(primary_key=True)
    nome = models.CharField(max_length=255)
    meta_dia = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    grupo = models.ForeignKey(
        DimGrupoServico,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="servicos",
        db_column="id_grupo_servico",
    )

    class Meta:
        db_table = "dim_servico"
        ordering = ["id_servico"]
        verbose_name = "Serviço"
        verbose_name_plural = "Serviços"

    def __str__(self) -> str:
        return f"{self.id_servico} — {self.nome}"


class DimCliente(models.Model):
    id_cliente = models.PositiveIntegerField(primary_key=True)
    nome = models.CharField(max_length=512)
    operations = models.BooleanField(default=True)
    id_classificacao = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "dim_cliente"
        ordering = ["id_cliente"]

    def __str__(self) -> str:
        return f"{self.id_cliente} — {self.nome}"


class DimWorkflow(models.Model):
    id_workflow = models.PositiveIntegerField(primary_key=True)
    nome = models.CharField(max_length=512)
    ind_considerar = models.BooleanField(default=True)
    produto = models.ForeignKey(
        DimProduto,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflows",
        db_column="id_produto",
    )
    tipo_atendimento = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "dim_workflow"
        ordering = ["id_workflow"]

    def __str__(self) -> str:
        return f"{self.id_workflow} — {self.nome}"


class DimNivelHierarquico(models.Model):
    id_nh = models.PositiveIntegerField(primary_key=True)
    nome = models.CharField(max_length=512)
    ind_considerar = models.BooleanField(default=True)

    class Meta:
        db_table = "dim_nivel_hierarquico"
        ordering = ["id_nh"]
        verbose_name = "Nível hierárquico"
        verbose_name_plural = "Níveis hierárquicos"

    def __str__(self) -> str:
        return f"{self.id_nh} — {self.nome}"


class DimEtapa(models.Model):
    id_etapa = models.PositiveIntegerField(primary_key=True)
    nome = models.CharField(max_length=512)
    # True = análise Manual; False = Automática (Megazord → Etapas).
    manual = models.BooleanField(default=True)

    class Meta:
        db_table = "dim_etapa"
        ordering = ["id_etapa"]

    def __str__(self) -> str:
        return f"{self.id_etapa} — {self.nome}"


class MetaEtapa(models.Model):
    """Meta diária por etapa com vigência (aba Metas_Etapa)."""

    data_inicio = models.DateField()
    data_fim = models.DateField(null=True, blank=True)
    etapa = models.ForeignKey(
        DimEtapa,
        on_delete=models.CASCADE,
        related_name="metas",
        db_column="id_etapa",
    )
    meta_dia = models.DecimalField(max_digits=14, decimal_places=2)
    servico = models.ForeignKey(
        DimServico,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="metas_etapa",
        db_column="id_servico",
    )

    class Meta:
        db_table = "meta_etapa"
        ordering = ["-data_inicio", "etapa_id"]
        indexes = [
            models.Index(fields=["data_inicio", "data_fim"]),
            models.Index(fields=["etapa"]),
        ]

    def __str__(self) -> str:
        return f"Etapa {self.etapa_id} meta={self.meta_dia} @ {self.data_inicio}"


class ProjecaoSla(models.Model):
    """Regra de SLA / janela de atendimento (aba Projeção_SLA)."""

    cliente = models.ForeignKey(
        DimCliente,
        on_delete=models.CASCADE,
        related_name="projecoes_sla",
        db_column="id_cliente",
    )
    data_inicio = models.DateField()
    data_fim = models.DateField(null=True, blank=True)
    workflow = models.ForeignKey(
        DimWorkflow,
        on_delete=models.CASCADE,
        related_name="projecoes_sla",
        db_column="id_workflow",
    )
    nivel_hierarquico = models.ForeignKey(
        DimNivelHierarquico,
        on_delete=models.CASCADE,
        related_name="projecoes_sla",
        db_column="id_nivel_hierarquico",
    )
    dias_semana = models.CharField(max_length=32, help_text="Ex.: {0..4}, {5}, {10}")
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fim = models.TimeField(null=True, blank=True)
    duracao_atendimento = models.FloatField(null=True, blank=True)
    sla_segundos = models.PositiveIntegerField(null=True, blank=True)
    flag_ajuste_sla = models.FloatField(null=True, blank=True)
    sla_ajuste = models.PositiveIntegerField(null=True, blank=True)
    volume = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "projecao_sla"
        ordering = ["-data_inicio", "cliente_id", "workflow_id"]
        indexes = [
            models.Index(fields=["data_inicio", "data_fim"]),
            models.Index(fields=["cliente", "workflow"]),
            models.Index(fields=["nivel_hierarquico"]),
        ]
        verbose_name = "Projeção SLA"
        verbose_name_plural = "Projeções SLA"

    def __str__(self) -> str:
        return f"SLA cli={self.cliente_id} wf={self.workflow_id} {self.dias_semana}"


class ProjecaoEquipe(models.Model):
    """Alocação / projeção de equipes (aba Projeção_Equipes) — histórico completo."""

    data_inicial = models.DateField()
    data_final = models.DateField(null=True, blank=True)
    matricula_agente = models.CharField(max_length=32, db_index=True)
    nome_agente = models.CharField(max_length=255, blank=True, default="")
    email_agente = models.CharField(max_length=255, blank=True, default="")
    atividade = models.CharField(max_length=255, blank=True, default="")
    matricula_lider = models.CharField(max_length=32, blank=True, default="")
    nome_lider = models.CharField(max_length=255, blank=True, default="")
    email_lider = models.CharField(max_length=255, blank=True, default="")
    equipe = models.CharField(max_length=128, blank=True, default="", db_index=True)
    matricula_facilitador = models.CharField(max_length=32, blank=True, default="")
    id_operations = models.PositiveIntegerField(null=True, blank=True)
    horario = models.CharField(max_length=64, blank=True, default="")
    turno = models.CharField(max_length=64, blank=True, default="")
    localidade = models.CharField(max_length=64, blank=True, default="")
    data_admissao = models.DateField(null=True, blank=True)
    funcao = models.CharField(max_length=128, blank=True, default="")
    setor = models.CharField(max_length=255, blank=True, default="")
    pcd = models.CharField(max_length=16, blank=True, default="")
    desconto_meta = models.CharField(max_length=64, blank=True, default="")
    matricula_oracle = models.CharField(max_length=64, blank=True, default="")
    matricula_ponto = models.CharField(max_length=64, blank=True, default="")
    observacao = models.CharField(max_length=512, blank=True, default="")
    matricula_supervisor = models.CharField(max_length=32, blank=True, default="")
    supervisor = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="", db_index=True)
    banda = models.CharField(max_length=16, blank=True, default="")
    powerapps_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "projecao_equipe"
        ordering = ["-data_inicial", "matricula_agente"]
        indexes = [
            models.Index(fields=["matricula_agente", "data_inicial"]),
            models.Index(fields=["status", "equipe"]),
        ]
        verbose_name = "Projeção equipe"
        verbose_name_plural = "Projeções equipe"

    def __str__(self) -> str:
        return f"{self.matricula_agente} @ {self.data_inicial}"


class DerivacaoEtapaImportRun(models.Model):
    """Controle de scan/import CSV derivacao_etapa/FINALIZADO_*.csv."""

    KIND_SCAN = "scan"
    KIND_IMPORT = "import"
    KIND_PURGE = "purge"
    KIND_CHOICES = [
        (KIND_SCAN, "Scan comparativo"),
        (KIND_IMPORT, "Importação"),
        (KIND_PURGE, "Apagar base"),
    ]

    STATUS_RUNNING = "running"
    STATUS_OK = "ok"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Em execução"),
        (STATUS_OK, "OK"),
        (STATUS_ERROR, "Erro"),
    ]

    run_kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_IMPORT)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    files_processed = models.PositiveIntegerField(default=0)
    rows_inserted = models.PositiveIntegerField(default=0)
    rows_skipped_total = models.PositiveIntegerField(default=0)
    rows_rejected = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True, default="")
    metrics = models.JSONField(default=dict, blank=True)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    source_file_filter = models.CharField(max_length=255, blank=True, default="")
    source_manifest = models.JSONField(default=list, blank=True)
    source_fingerprint = models.CharField(max_length=64, blank=True, default="")
    reviewed_scan = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="import_runs",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="derivacao_etapa_import_runs",
    )

    class Meta:
        db_table = "derivacao_etapa_import_run"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"DerivacaoEtapaImportRun#{self.pk} {self.run_kind} {self.status}"


def derivacao_etapa_upload_to(instance, filename: str) -> str:
    batch_token = getattr(instance, "batch_id", None) or "unknown"
    return f"derivacao_etapa/staging/{batch_token}/{filename}"


class DerivacaoEtapaUploadBatch(models.Model):
    """Staging temporário de CSVs enviados pelo portal (upload por sessão)."""

    STATUS_ACTIVE = "active"
    STATUS_CONSUMED = "consumed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Ativo"),
        (STATUS_CONSUMED, "Consumido"),
        (STATUS_EXPIRED, "Expirado"),
    ]

    token = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="derivacao_etapa_upload_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    file_manifest = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "derivacao_etapa_upload_batch"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"DerivacaoEtapaUploadBatch {self.token} ({self.status})"


class DerivacaoEtapaUploadFile(models.Model):
    batch = models.ForeignKey(
        DerivacaoEtapaUploadBatch,
        on_delete=models.CASCADE,
        related_name="files",
    )
    file = models.FileField(upload_to=derivacao_etapa_upload_to)
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "derivacao_etapa_upload_file"
        ordering = ["original_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "original_name"],
                name="uniq_deriv_upload_batch_name",
            )
        ]

    def __str__(self) -> str:
        return self.original_name


class DerivacaoEtapaDiaria(models.Model):
    """Fato diário: protocolos por etapa (derivacao_etapa CSV → Megazord)."""

    data = models.DateField(db_index=True)
    cliente = models.ForeignKey(
        DimCliente,
        on_delete=models.PROTECT,
        related_name="derivacao_etapa_diaria",
        db_column="id_cliente",
    )
    workflow = models.ForeignKey(
        DimWorkflow,
        on_delete=models.PROTECT,
        related_name="derivacao_etapa_diaria",
        db_column="id_workflow",
    )
    etapa = models.ForeignKey(
        DimEtapa,
        on_delete=models.PROTECT,
        related_name="derivacao_etapa_diaria",
        db_column="id_etapa",
    )
    registros = models.PositiveIntegerField(default=0)
    percentual = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    cliente_nome_origem = models.CharField(max_length=512, blank=True, default="")
    workflow_nome_origem = models.CharField(max_length=512, blank=True, default="")
    etapa_nome_origem = models.CharField(max_length=512, blank=True, default="")
    source_file = models.CharField(max_length=255, blank=True, default="")
    import_run = models.ForeignKey(
        DerivacaoEtapaImportRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linhas",
    )
    manual_override = models.BooleanField(default=False)
    override_notas = models.CharField(max_length=512, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="derivacao_etapa_diaria_edits",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "derivacao_etapa_diaria"
        ordering = ["-data", "cliente_id", "workflow_id", "etapa_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["data", "cliente", "workflow", "etapa"],
                name="uniq_derivacao_etapa_dia_cwe",
            )
        ]
        indexes = [
            models.Index(fields=["data", "cliente", "workflow"], name="deriv_etapa_d_cw_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.data} c={self.cliente_id} w={self.workflow_id} e={self.etapa_id} r={self.registros}"


class DimNomeAlias(models.Model):
    """Alias manual CSV → dimensão Megazord (correções pontuais)."""

    DIM_CLIENTE = "cliente"
    DIM_WORKFLOW = "workflow"
    DIM_ETAPA = "etapa"
    DIM_CHOICES = [
        (DIM_CLIENTE, "Cliente"),
        (DIM_WORKFLOW, "Workflow"),
        (DIM_ETAPA, "Etapa"),
    ]

    CLASS_PRODUCAO = "producao"
    CLASS_ETAPA_AUTOMATICA = "etapa_automatica"
    CLASS_POC_TESTE = "poc_teste"
    CLASSIFICACAO_CHOICES = [
        (CLASS_PRODUCAO, "Produção"),
        (CLASS_ETAPA_AUTOMATICA, "Etapa automática"),
        (CLASS_POC_TESTE, "POC / teste"),
    ]

    dimensao = models.CharField(max_length=16, choices=DIM_CHOICES)
    nome_origem = models.CharField(max_length=512)
    nome_origem_key = models.CharField(max_length=512)
    cliente = models.ForeignKey(
        DimCliente,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="nome_aliases",
        db_column="id_cliente",
    )
    workflow = models.ForeignKey(
        DimWorkflow,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="nome_aliases",
        db_column="id_workflow",
    )
    etapa = models.ForeignKey(
        DimEtapa,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="nome_aliases",
        db_column="id_etapa",
    )
    ativo = models.BooleanField(default=True)
    classificacao = models.CharField(
        max_length=24,
        choices=CLASSIFICACAO_CHOICES,
        default=CLASS_PRODUCAO,
    )
    notas = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dim_nome_aliases_created",
    )

    class Meta:
        db_table = "dim_nome_alias"
        ordering = ["dimensao", "nome_origem"]
        constraints = [
            models.UniqueConstraint(
                fields=["dimensao", "nome_origem_key"],
                name="uniq_dim_nome_alias_dim_key",
            )
        ]

    def __str__(self) -> str:
        return f"{self.dimensao}: {self.nome_origem}"


class CapacityFamiliaAlias(models.Model):
    """Alias editável de prefixo de etapa → família canônica no Capacity."""

    alias_key = models.CharField(max_length=512, unique=True)
    alias_origem = models.CharField(max_length=512)
    familia_canonica = models.CharField(max_length=512)
    ativo = models.BooleanField(default=True)
    notas = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="capacity_familia_aliases_created",
    )

    class Meta:
        db_table = "capacity_familia_alias"
        ordering = ["familia_canonica", "alias_origem"]

    def __str__(self) -> str:
        return f"{self.alias_origem} → {self.familia_canonica}"


class CapacityDailySnapshot(models.Model):
    """Resultado compacto pre-calculado para montar periodos sem recalculo online."""

    SCENARIO_PLANEJAMENTO = "planejamento"
    SCENARIO_OPERACAO = "operacao"
    SCENARIO_MANUAL = "manual"
    SCENARIO_CHOICES = [
        (SCENARIO_PLANEJAMENTO, "Planejamento"),
        (SCENARIO_OPERACAO, "Operacao"),
        (SCENARIO_MANUAL, "Simulacao"),
    ]

    calculation_date = models.DateField()
    scenario_id = models.CharField(
        max_length=32,
        choices=SCENARIO_CHOICES,
        default=SCENARIO_PLANEJAMENTO,
    )
    metric_version = models.CharField(max_length=64, default="legacy")
    source_fingerprint = models.CharField(max_length=64, default="legacy-unversioned")
    payload = models.JSONField(default=dict)
    generated_at = models.DateTimeField()
    valid_until = models.DateTimeField(db_index=True)
    generation_id = models.UUIDField(db_index=True)

    class Meta:
        db_table = "capacity_daily_snapshot"
        ordering = ["calculation_date", "scenario_id", "metric_version"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "calculation_date",
                    "scenario_id",
                    "metric_version",
                    "source_fingerprint",
                ],
                name="uniq_capacity_snapshot_identity",
            )
        ]

    def is_compatible(self, *, metric_version: str, source_fingerprint: str, at=None) -> bool:
        """Confirma identidade e validade antes de servir um snapshot."""

        from django.utils import timezone

        reference_time = at or timezone.now()
        return (
            self.valid_until > reference_time
            and self.metric_version == metric_version
            and self.source_fingerprint == source_fingerprint
        )

    def __str__(self) -> str:
        return (
            f"CapacityDailySnapshot {self.calculation_date} "
            f"[{self.scenario_id}/{self.metric_version}] @ {self.generated_at.isoformat()}"
        )


class CapacityHourlyProfileSnapshot(models.Model):
    """Perfil horário trimestral materializado para fallback rápido do Capacity."""

    SCOPE_WORKFLOW = "workflow"
    SCOPE_CLIENT = "client"
    SCOPE_CHOICES = [
        (SCOPE_WORKFLOW, "Cliente + workflow"),
        (SCOPE_CLIENT, "Cliente"),
    ]

    quarter_from = models.DateField()
    quarter_to = models.DateField()
    weekday = models.PositiveSmallIntegerField(help_text="Segunda=0 ... domingo=6")
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    id_cliente = models.PositiveIntegerField()
    id_workflow = models.PositiveIntegerField(default=0)
    hourly_volumes = models.JSONField(default=list)
    trusted_volume = models.BigIntegerField(default=0)
    generated_at = models.DateTimeField()

    class Meta:
        db_table = "capacity_hourly_profile_snapshot"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "quarter_from",
                    "quarter_to",
                    "weekday",
                    "scope",
                    "id_cliente",
                    "id_workflow",
                ],
                name="uniq_capacity_hourly_profile",
            )
        ]
        indexes = [
            models.Index(
                fields=["quarter_to", "weekday", "id_cliente", "id_workflow"],
                name="cap_hour_profile_lookup_idx",
            )
        ]


class DerivacaoEtapaComparativo(models.Model):
    """Distinct CSV vs Megazord (scan comparativo)."""

    STATUS_OK = "ok"
    STATUS_UNMATCHED = "unmatched"
    STATUS_AMBIGUOUS = "ambiguous"
    STATUS_ALIAS = "alias"
    STATUS_CHOICES = [
        (STATUS_OK, "OK"),
        (STATUS_UNMATCHED, "Sem match"),
        (STATUS_AMBIGUOUS, "Ambíguo"),
        (STATUS_ALIAS, "Alias"),
    ]

    scan_run = models.ForeignKey(
        DerivacaoEtapaImportRun,
        on_delete=models.CASCADE,
        related_name="comparativo_linhas",
    )
    dimensao = models.CharField(max_length=16, choices=DimNomeAlias.DIM_CHOICES)
    nome_origem = models.CharField(max_length=512)
    nome_origem_key = models.CharField(max_length=512)
    linhas_csv = models.PositiveIntegerField(default=0)
    registros_total = models.PositiveIntegerField(default=0)
    id_resolvido = models.PositiveIntegerField(null=True, blank=True)
    nome_megazord = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_UNMATCHED)
    match_strategy = models.CharField(max_length=32, blank=True, default="")
    sugestoes = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "derivacao_etapa_comparativo"
        ordering = ["dimensao", "-linhas_csv", "nome_origem"]
        constraints = [
            models.UniqueConstraint(
                fields=["scan_run", "dimensao", "nome_origem_key"],
                name="uniq_deriv_comp_run_dim_key",
            )
        ]
        indexes = [
            models.Index(
                fields=["scan_run", "dimensao", "status"],
                name="deriv_comp_run_dim_st_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dimensao} {self.nome_origem} → {self.id_resolvido or '?'}"


def identificacao_processos_upload_to(instance, filename: str) -> str:
    token = getattr(instance, "token", None) or "unknown"
    return f"identificacao_processos/staging/{token}/{filename}"


class IdentificacaoProcessosUpload(models.Model):
    """Staging temporário do workbook Identificação dos Processos."""

    STATUS_ACTIVE = "active"
    STATUS_CONSUMED = "consumed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Ativo"),
        (STATUS_CONSUMED, "Consumido"),
        (STATUS_EXPIRED, "Expirado"),
    ]

    token = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="identificacao_processos_uploads",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField(default=0)
    file = models.FileField(upload_to=identificacao_processos_upload_to)
    preflight_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "identificacao_processos_upload"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"IdentificacaoProcessosUpload {self.token} ({self.status})"


class IdentificacaoProcessosImportRun(models.Model):
    """Execução de import/sync do workbook Identificação dos Processos."""

    MODE_SYNC = "sync"
    MODE_REPLACE = "replace"
    MODE_CHOICES = [
        (MODE_SYNC, "Sincronizar"),
        (MODE_REPLACE, "Substituir"),
    ]

    SCOPE_CADASTROS = "cadastros"
    SCOPE_PROJECAO_SLA = "projecao_sla"
    SCOPE_FULL = "full"
    SCOPE_CHOICES = [
        (SCOPE_CADASTROS, "Só cadastros"),
        (SCOPE_PROJECAO_SLA, "Cadastros + Projeção SLA"),
        (SCOPE_FULL, "Completo"),
    ]

    STATUS_RUNNING = "running"
    STATUS_OK = "ok"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Em execução"),
        (STATUS_OK, "OK"),
        (STATUS_ERROR, "Erro"),
    ]

    upload = models.ForeignKey(
        IdentificacaoProcessosUpload,
        on_delete=models.CASCADE,
        related_name="import_runs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="identificacao_processos_import_runs",
    )
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_SYNC)
    import_scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, default=SCOPE_FULL)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    summary = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "identificacao_processos_import_run"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"IdentificacaoProcessosImportRun#{self.pk} {self.mode} {self.status}"
