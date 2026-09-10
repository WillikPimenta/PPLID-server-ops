import unicodedata
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ReinspecaoMappingMetadataMixin(models.Model):
    """Metadados auditáveis do enriquecimento pela matriz de Reinspeção."""

    MAPPING_MATCHED = "matched"
    MAPPING_UNMATCHED = "unmatched"
    MAPPING_AMBIGUOUS = "ambiguous"
    MAPPING_PREEXISTING = "preexisting"
    MAPPING_CONFLICT = "conflict"
    MAPPING_STATUS_CHOICES = [
        (MAPPING_MATCHED, "Mapeado"),
        (MAPPING_UNMATCHED, "Não mapeado"),
        (MAPPING_AMBIGUOUS, "Ambíguo"),
        (MAPPING_PREEXISTING, "Valor preexistente preservado"),
        (MAPPING_CONFLICT, "Conflito com valor preexistente"),
    ]

    codigo_irregularidade = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    mapping_scenario_status = models.CharField(
        max_length=16,
        choices=MAPPING_STATUS_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    mapping_stage_status = models.CharField(
        max_length=16,
        choices=MAPPING_STATUS_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    mapping_version = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    mapping_source_hash = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    mapping_applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class ReinspecaoIrregularidadeMapping(models.Model):
    """Uma variante versionada da matriz de irregularidades de Reinspeção."""

    REVIEW_PENDING = "pending"
    REVIEW_APPROVED = "approved"
    REVIEW_REJECTED = "rejected"
    REVIEW_STATUS_CHOICES = [
        (REVIEW_PENDING, "Pendente"),
        (REVIEW_APPROVED, "Aprovado"),
        (REVIEW_REJECTED, "Rejeitado"),
    ]

    codigo_original = models.CharField(max_length=64)
    codigo_normalizado = models.CharField(max_length=64, db_index=True)
    classificacao = models.CharField(max_length=64, blank=True, default="")
    descricao_original = models.TextField()
    descricao_normalizada = models.TextField()
    ilha = models.CharField(max_length=255, blank=True, default="")
    etapa = models.CharField(max_length=255, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    categoria = models.CharField(max_length=128, blank=True, default="")
    cenario = models.CharField(max_length=255, blank=True, default="")
    active = models.BooleanField(default=True, db_index=True)
    mapping_version = models.CharField(max_length=64, db_index=True)
    source_hash = models.CharField(max_length=64, db_index=True)
    source_row = models.PositiveIntegerField()
    review_status = models.CharField(
        max_length=16,
        choices=REVIEW_STATUS_CHOICES,
        default=REVIEW_PENDING,
        db_index=True,
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reinspecao_mappings_importados",
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reinspecao_irregularidade_mapping"
        ordering = ["mapping_version", "source_row"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_hash", "source_row"],
                name="reinspecao_map_hash_row_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["active", "review_status", "codigo_normalizado"],
                name="reins_map_active_code_idx",
            ),
            models.Index(
                fields=["mapping_version", "active"],
                name="reins_map_version_act_idx",
            ),
        ]


class ReinspecaoMappingRun(models.Model):
    KIND_IMPORT = "import"
    KIND_BACKFILL = "backfill"
    KIND_ROLLBACK = "rollback"
    KIND_CHOICES = [
        (KIND_IMPORT, "Importação"),
        (KIND_BACKFILL, "Backfill"),
        (KIND_ROLLBACK, "Rollback"),
    ]
    STATUS_RUNNING = "running"
    STATUS_APPLIED = "applied"
    STATUS_FAILED = "failed"
    STATUS_ROLLED_BACK = "rolled_back"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Em execução"),
        (STATUS_APPLIED, "Aplicado"),
        (STATUS_FAILED, "Falhou"),
        (STATUS_ROLLED_BACK, "Revertido"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        db_index=True,
    )
    mapping_version = models.CharField(max_length=64, db_index=True)
    source_hash = models.CharField(max_length=64, db_index=True)
    preview_token = models.CharField(max_length=64, blank=True, default="")
    manifest = models.JSONField(default=dict, blank=True)
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reinspecao_mapping_runs",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reinspecao_mapping_run"
        ordering = ["-started_at"]


class ReinspecaoMappingChange(models.Model):
    TARGET_PENDING = "pending"
    TARGET_TREATED = "treated"
    TARGET_CHOICES = [
        (TARGET_PENDING, "Pendente"),
        (TARGET_TREATED, "Tratado"),
    ]

    run = models.ForeignKey(
        ReinspecaoMappingRun,
        on_delete=models.PROTECT,
        related_name="changes",
    )
    target_type = models.CharField(max_length=16, choices=TARGET_CHOICES)
    target_id = models.BigIntegerField()
    before_values = models.JSONField(default=dict)
    after_values = models.JSONField(default=dict)
    filled_fields = models.JSONField(default=list)
    rolled_back_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reinspecao_mapping_change"
        constraints = [
            models.UniqueConstraint(
                fields=["run", "target_type", "target_id"],
                name="reins_map_change_target_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["target_type", "target_id"],
                name="reins_map_change_target_idx",
            ),
        ]


class AuditoriaCatalogItem(models.Model):
    CATALOG_MODULO = "modulo"
    CATALOG_TIPO_FALHA = "tipo_falha"
    CATALOG_NOVO_RESULTADO = "novo_resultado"
    CATALOG_SINALIZACAO = "sinalizacao"
    CATALOG_ETAPA_FALHA = "etapa_falha"
    CATALOG_NIVEL_DIFICULDADE = "nivel_dificuldade"
    CATALOG_TIPO_DOCUMENTO = "tipo_documento"
    CATALOG_UF_DOCUMENTO = "uf_documento"
    CATALOG_CRUZAMENTO_BASES = "cruzamento_bases"
    CATALOG_QUALIDADE_IMAGEM = "qualidade_imagem"
    CATALOG_TIPO_ACAO_CONTROLE = "tipo_acao_controle"
    CATALOG_MOTIVO_BASE_NEGATIVA = "motivo_base_negativa"
    CATALOG_IRREGULARIDADES_CONFER = "irregularidades_confer"
    CATALOG_DUVIDA_SUPORTE_OPERACIONAL = "duvida_suporte_operacional"

    CATALOG_CHOICES = [
        (CATALOG_MODULO, "Módulo"),
        (CATALOG_TIPO_FALHA, "Tipo de falha"),
        (CATALOG_NOVO_RESULTADO, "Novo resultado"),
        (CATALOG_SINALIZACAO, "Sinalização"),
        (CATALOG_ETAPA_FALHA, "Etapa da falha"),
        (CATALOG_NIVEL_DIFICULDADE, "Nível de dificuldade"),
        (CATALOG_TIPO_DOCUMENTO, "Tipo de documento"),
        (CATALOG_UF_DOCUMENTO, "UF do documento"),
        (CATALOG_CRUZAMENTO_BASES, "Cruzamento de bases"),
        (CATALOG_QUALIDADE_IMAGEM, "Qualidade da imagem"),
        (CATALOG_TIPO_ACAO_CONTROLE, "Tipo de ação (controles)"),
        (CATALOG_MOTIVO_BASE_NEGATIVA, "Motivo (base negativa)"),
        (CATALOG_IRREGULARIDADES_CONFER, "Irregularidades Confer"),
        (CATALOG_DUVIDA_SUPORTE_OPERACIONAL, "Dúvida do suporte operacional"),
    ]

    catalog = models.CharField(max_length=32, choices=CATALOG_CHOICES, db_index=True)
    value = models.CharField(max_length=255)
    label = models.CharField(max_length=255, blank=True, default="")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_catalog_item"
        ordering = ["catalog", "sort_order", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["catalog", "value"],
                name="uniq_auditoria_catalog_value",
            ),
        ]

    def __str__(self):
        return f"{self.catalog}: {self.value}"


class AuditoriaMotivoFalha(models.Model):
    motivo = models.CharField(max_length=255, unique=True)
    criticidade = models.CharField(max_length=64)
    segmentos = models.CharField(max_length=128)
    subsegmento = models.CharField(max_length=128)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_motivo_falha"
        ordering = ["sort_order", "motivo"]
        verbose_name = "Cenário"
        verbose_name_plural = "Cenários"

    def __str__(self):
        return self.motivo


class QualidadeConfiguracaoAlteracao(models.Model):
    tabela_origem = models.CharField(max_length=128, db_index=True)
    registro_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    mudancas = models.JSONField(default=dict)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qualidade_configuracao_alteracoes",
    )
    reverte = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversoes",
    )
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "qualidade_configuracao_alteracao"
        ordering = ["-criado_em", "-id"]


class QualidadeAnaliseOrigem(models.Model):
    """Fonte imutável de Brflow e trilha compartilhada pelos registros tratados."""

    protocolo = models.CharField(max_length=100, blank=True, default="", db_index=True)
    brflow_raw = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    trilha_raw = models.TextField(blank=True, default="")
    trilha_parsed = models.JSONField(default=dict, blank=True)
    contexto = models.JSONField(default=dict, blank=True)
    protocolo_criado_em = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Data de criação do protocolo no sistema de origem.",
    )
    protocolo_analisado_em = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Data de análise do protocolo no sistema de origem.",
    )
    protocolo_concluido_em = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Data de conclusão do protocolo no sistema de origem.",
    )
    conteudo_hash = models.CharField(max_length=64, unique=True)
    schema_version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_analise_origem"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Origem de análise {self.protocolo or self.pk}"


class AuditoriaFalhaCadastro(ReinspecaoMappingMetadataMixin):
    REGISTRO_AUDITORIA = "auditoria"
    REGISTRO_CONTESTACAO = "contestacao"
    REGISTRO_REINSPECAO = "reinspecao"
    REGISTRO_CHOICES = [
        (REGISTRO_AUDITORIA, "Auditoria"),
        (REGISTRO_CONTESTACAO, "Contestação"),
        (REGISTRO_REINSPECAO, "Reinspeção"),
    ]

    atividade = models.ForeignKey(
        "AuditoriaAtividade",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tratados",
    )
    protocolo_origem = models.ForeignKey(
        "AuditoriaAtividadeProtocolo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tratados_etapas",
        help_text="Protocolo operacional que originou esta etapa tratada.",
    )
    etapa_origem = models.OneToOneField(
        "AuditoriaAtividadeProtocoloEtapa",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tratado",
        help_text="Etapa operacional promovida para a base central.",
    )
    analise_origem = models.ForeignKey(
        QualidadeAnaliseOrigem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tratados",
        help_text="Fonte compartilhada do Brflow e da trilha desta análise.",
    )
    protocolo = models.CharField(max_length=100, db_index=True)
    brflow_raw = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    modulo = models.CharField(max_length=255, blank=True, default="")
    demanda_url = models.URLField(max_length=500, blank=True, default="")
    tipo_falha = models.CharField(max_length=32)
    usuario = models.CharField(max_length=255)
    agente_ref = models.ForeignKey(
        "workforce.Agent",
        db_column="agente_id",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="falhas_atribuidas",
        help_text="Agente relacionado ao valor legado de usuario, inclusive inativos e SISTEMA.",
    )
    resultado_cliente = models.TextField(blank=True, default="")
    novo_resultado = models.TextField(blank=True, default="")
    sinalizacao = models.CharField(max_length=255, blank=True, default="")
    motivo_falha = models.CharField(max_length=255, blank=True, default="")
    etapa_falha = models.CharField(max_length=255, blank=True, default="")
    etapa_chave = models.CharField(
        max_length=160,
        null=True,
        blank=True,
        unique=True,
        help_text="Chave idempotente e imutável da etapa tratada.",
    )
    ordem_etapa = models.PositiveIntegerField(null=True, blank=True)
    etapa_criada_em = models.DateTimeField(null=True, blank=True)
    etapa_atualizada_em = models.DateTimeField(null=True, blank=True)
    tempo_analise = models.CharField(max_length=16, blank=True, default="")
    cruzamento_bases = models.CharField(max_length=255, blank=True, default="")
    nivel_dificuldade = models.CharField(max_length=100, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    uf_documento = models.CharField(max_length=50, blank=True, default="")
    qualidade_imagem = models.CharField(max_length=255, blank=True, default="")
    # Campos legados mantidos para compatibilidade. Novos fluxos devem usar os
    # campos explícitos abaixo e a FK analise_origem para datas do BrFlow.
    data_contestacao = models.DateTimeField(null=True, blank=True, db_index=True)
    data_analise = models.DateTimeField(null=True, blank=True, db_index=True)
    data_analise_intranet = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Momento em que o caso foi analisado ou concluído na Intranet.",
    )
    data_recepcao_contestacao = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Momento em que a contestação entrou no fluxo de auditoria.",
    )
    data_encerramento_atividade_intranet = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Momento em que o lote ou atividade foi encerrado na Intranet.",
    )
    descricao_irregularidades = models.TextField(blank=True, default="")
    cliente = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=128, blank=True, default="")
    observacao = models.TextField(blank=True, default="")
    auditor = models.CharField(max_length=255, blank=True, default="")
    auditor_responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="falhas_como_auditor_responsavel",
    )
    auditor_ref = models.ForeignKey(
        "workforce.Agent",
        db_column="auditor_id",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="falhas_auditadas",
        help_text="Auditor que efetivamente finalizou e registrou a analise.",
    )
    data_resposta = models.DateTimeField(null=True, blank=True, db_index=True)
    # Fila de distribuição (reinspeção)
    ANALISE_NAO_ATRIBUIDO = "nao_atribuido"
    ANALISE_AGUARDANDO = "aguardando_analise"
    ANALISE_EM_ANALISE = "em_analise"
    ANALISE_CONCLUIDO = "concluido"
    ANALISE_STATUS_CHOICES = [
        (ANALISE_NAO_ATRIBUIDO, "Não atribuído"),
        (ANALISE_AGUARDANDO, "Aguardando análise"),
        (ANALISE_EM_ANALISE, "Em análise"),
        (ANALISE_CONCLUIDO, "Concluído"),
    ]
    RESULTADO_COM_FALHA = "com_falha"
    RESULTADO_SEM_FALHA = "sem_falha"
    RESULTADO_NAO_CLASSIFICADO = "nao_classificado"
    RESULTADO_QUALIDADE_CHOICES = [
        (RESULTADO_COM_FALHA, "Com falha"),
        (RESULTADO_SEM_FALHA, "Sem falha"),
        (RESULTADO_NAO_CLASSIFICADO, "Não classificado"),
    ]
    resultado_qualidade = models.CharField(
        max_length=32,
        choices=RESULTADO_QUALIDADE_CHOICES,
        default=RESULTADO_NAO_CLASSIFICADO,
        db_index=True,
        help_text="Resultado consolidado e automático da análise finalizada.",
    )
    resultado_qualidade_override = models.CharField(
        max_length=32,
        choices=RESULTADO_QUALIDADE_CHOICES,
        null=True,
        blank=True,
        default=None,
        help_text="Resultado definido manualmente por uma alteração auditada.",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_protocolos_responsavel",
    )
    atribuido_em = models.DateTimeField(null=True, blank=True, db_index=True)
    analise_iniciada_em = models.DateTimeField(null=True, blank=True)
    analise_concluida_em = models.DateTimeField(null=True, blank=True)
    FILA_ORIGEM_AUTO = "auto"
    FILA_ORIGEM_DIRECIONADO = "direcionado"
    FILA_ORIGEM_CHOICES = [
        (FILA_ORIGEM_AUTO, "Atribuição automática"),
        (FILA_ORIGEM_DIRECIONADO, "Direcionamento manual"),
    ]
    fila_origem = models.CharField(
        max_length=16,
        choices=FILA_ORIGEM_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    tipo_registro = models.CharField(
        max_length=16,
        choices=REGISTRO_CHOICES,
        default=REGISTRO_AUDITORIA,
        db_index=True,
    )
    # Origem do registro tratado (única tabela de falhas/auditados).
    ORIGEM_AUDITORIA = "auditoria"
    ORIGEM_CONTESTACAO = "contestacao"
    ORIGEM_REINSPECAO = "reinspecao"
    ORIGEM_INTRANET = "intranet"
    ORIGEM_CHOICES = [
        (ORIGEM_AUDITORIA, "Auditoria"),
        (ORIGEM_CONTESTACAO, "Contestação"),
        (ORIGEM_REINSPECAO, "Reinspeção"),
        (ORIGEM_INTRANET, "Intranet"),
    ]
    origem = models.CharField(
        max_length=16,
        choices=ORIGEM_CHOICES,
        blank=True,
        default="",
        db_index=True,
        help_text="De onde o registro tratado veio (auditoria, contestação, reinspeção, intranet).",
    )
    # Resultado após contestação pela liderança (não sobrescreve `status` de reinspeção).
    STATUS_FALHA_ATIVA = "ativa"
    STATUS_FALHA_RETIRADA = "retirada"
    STATUS_FALHA_MANTIDA = "mantida"
    STATUS_FALHA_EM_VALIDACAO = "em_validacao"
    STATUS_FALHA_NAO_CONFORME = "nao_conforme"
    STATUS_FALHA_CHOICES = [
        (STATUS_FALHA_ATIVA, "Ativa"),
        (STATUS_FALHA_RETIRADA, "Retirada"),
        (STATUS_FALHA_MANTIDA, "Mantida"),
        (STATUS_FALHA_EM_VALIDACAO, "Em validação"),
        (STATUS_FALHA_NAO_CONFORME, "Não conforme"),
    ]
    status_falha = models.CharField(
        max_length=16,
        choices=STATUS_FALHA_CHOICES,
        default=STATUS_FALHA_ATIVA,
        blank=True,
        db_index=True,
        help_text="Estado operacional da falha após contestação da liderança.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="auditoria_falha_cadastros",
    )
    operational_support_request = models.ForeignKey(
        "suporte_operacional.OperationalSupportRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_falhas_vinculadas",
        db_index=True,
        help_text="Solicitação de suporte operacional respondida vinculada a este tratado.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_falha_cadastro"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["origem", "protocolo"], name="aud_fal_ori_prot_idx"),
            models.Index(
                fields=["origem", "usuario", "analise_concluida_em"],
                name="aud_fal_ori_usr_dt_idx",
            ),
        ]

    @staticmethod
    def _normalizar_resultado_valor(value: str | None) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        return " ".join(text.encode("ascii", "ignore").decode("ascii").lower().split())

    @classmethod
    def inferir_resultado_qualidade(
        cls,
        *,
        status: str | None,
        tipo_falha: str | None,
        origem: str | None = None,
        tipo_registro: str | None = None,
        status_falha: str | None = None,
        brflow_parsed: dict | None = None,
    ) -> str:
        """Consolida o resultado sem confundir estado da fila com resultado final."""
        situacao_payload = (
            brflow_parsed.get("situacao")
            if isinstance(brflow_parsed, dict)
            else ""
        )
        status_normalizado = cls._normalizar_resultado_valor(status or situacao_payload)
        tipo_normalizado = cls._normalizar_resultado_valor(tipo_falha)
        fluxo = cls._normalizar_resultado_valor(origem or tipo_registro)
        estado_falha = cls._normalizar_resultado_valor(status_falha)

        if estado_falha == cls.STATUS_FALHA_EM_VALIDACAO:
            return cls.RESULTADO_NAO_CLASSIFICADO

        # Uma contestação operacional posterior prevalece sobre o resultado original.
        if estado_falha == cls.STATUS_FALHA_RETIRADA:
            return cls.RESULTADO_SEM_FALHA
        if estado_falha == cls.STATUS_FALHA_MANTIDA:
            return cls.RESULTADO_COM_FALHA
        if estado_falha == cls.STATUS_FALHA_NAO_CONFORME:
            return cls.RESULTADO_SEM_FALHA

        # Reinspeção usa a semântica inversa da Contestação Fraud:
        # procedente = correto; improcedente = falha confirmada.
        if fluxo == cls.ORIGEM_REINSPECAO:
            if status_normalizado == "procedente":
                return cls.RESULTADO_SEM_FALHA
            if status_normalizado == "improcedente":
                return cls.RESULTADO_COM_FALHA

        if status_normalizado in {"procedente", "nao conforme"}:
            return cls.RESULTADO_COM_FALHA
        if status_normalizado in {"improcedente", "conforme"}:
            return cls.RESULTADO_SEM_FALHA
        if tipo_normalizado == "sem falha":
            return cls.RESULTADO_SEM_FALHA
        if tipo_normalizado and tipo_normalizado not in {
            "auditoria",
            "contestacao",
            "reinspecao",
        }:
            return cls.RESULTADO_COM_FALHA
        return cls.RESULTADO_NAO_CLASSIFICADO

    def save(self, *args, **kwargs):
        self.resultado_qualidade = (
            self.resultado_qualidade_override
            or self.inferir_resultado_qualidade(
                status=self.status,
                tipo_falha=self.tipo_falha,
                origem=self.origem,
                tipo_registro=self.tipo_registro,
                status_falha=self.status_falha,
                brflow_parsed=self.brflow_parsed,
            )
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"resultado_qualidade"}
        return super().save(*args, **kwargs)

    @property
    def analise_status(self) -> str:
        """Compatibilidade: toda linha da tabela central representa uma conclusão."""
        return self.ANALISE_CONCLUIDO

    @analise_status.setter
    def analise_status(self, _value: str) -> None:
        # Aceita payloads legados sem recriar a coluna redundante.
        return None

    def __str__(self):
        return f"{self.protocolo} ({self.tipo_falha})"


class AuditoriaFalhaAlteracao(models.Model):
    """Historico imutavel de alteracoes em uma falha finalizada."""

    SCHEMA_VERSION = 1

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    falha = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.PROTECT,
        related_name="alteracoes",
    )
    alterado_por = models.ForeignKey(
        "workforce.Agent",
        on_delete=models.PROTECT,
        related_name="alteracoes_de_falha",
    )
    origem = models.CharField(
        max_length=16,
        choices=AuditoriaFalhaCadastro.ORIGEM_CHOICES,
        db_index=True,
    )
    versao = models.PositiveIntegerField()
    dados_anteriores = models.JSONField(default=dict)
    dados_alterados = models.JSONField(default=dict)
    campos_alterados = models.JSONField(default=list)
    justificativa = models.TextField()
    schema_version = models.PositiveSmallIntegerField(default=SCHEMA_VERSION)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "auditoria_falha_alteracao"
        ordering = ["-created_at", "-versao"]
        constraints = [
            models.UniqueConstraint(
                fields=["falha", "versao"],
                name="uniq_aud_falha_alteracao_versao",
            ),
        ]
        indexes = [
            models.Index(fields=["falha", "created_at"], name="aud_fal_alt_fal_dt_idx"),
            models.Index(
                fields=["alterado_por", "created_at"],
                name="aud_fal_alt_aut_dt_idx",
            ),
        ]

    def __str__(self):
        return f"Falha {self.falha_id} - alteracao {self.versao}"


class AuditoriaFalhaValidacaoHistorico(models.Model):
    """Decisão imutável da Capacitação sobre uma falha em validação."""

    RESULTADO_CONFORME = "conforme"
    RESULTADO_NAO_CONFORME = "nao_conforme"
    RESULTADO_CHOICES = [
        (RESULTADO_CONFORME, "Conforme"),
        (RESULTADO_NAO_CONFORME, "Não conforme"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    falha = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.PROTECT,
        related_name="validacoes_capacitacao",
    )
    status_de = models.CharField(
        max_length=16,
        choices=AuditoriaFalhaCadastro.STATUS_FALHA_CHOICES,
    )
    status_para = models.CharField(
        max_length=16,
        choices=AuditoriaFalhaCadastro.STATUS_FALHA_CHOICES,
        db_index=True,
    )
    resultado = models.CharField(max_length=16, choices=RESULTADO_CHOICES, db_index=True)
    observacao = models.TextField()
    snapshot_inicial = models.JSONField(default=dict)
    snapshot_final = models.JSONField(default=dict)
    ator = models.ForeignKey(
        "workforce.Agent",
        on_delete=models.PROTECT,
        related_name="validacoes_de_falha",
    )
    alteracao = models.OneToOneField(
        AuditoriaFalhaAlteracao,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="validacao_capacitacao",
    )
    idempotency_key = models.UUIDField(unique=True, editable=False)
    idempotency_payload_hash = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_falha_validacao_historico"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status_de=AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO
                ),
                name="aud_fal_val_status_de_check",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        resultado="conforme",
                        status_para=AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                        alteracao__isnull=True,
                    )
                    | models.Q(
                        resultado="nao_conforme",
                        status_para__in=(
                            AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                            AuditoriaFalhaCadastro.STATUS_FALHA_NAO_CONFORME,
                        ),
                        alteracao__isnull=False,
                    )
                ),
                name="aud_fal_val_resultado_check",
            ),
        ]
        indexes = [
            models.Index(fields=["status_para", "created_at"], name="aud_fal_val_status_dt_idx"),
            models.Index(fields=["ator", "created_at"], name="aud_fal_val_actor_dt_idx"),
        ]

    def __str__(self):
        return f"Falha {self.falha_id} - {self.resultado}"


# Status operacional da falha tratada após contestação pela liderança.
# Separado de `status` (texto livre de reinspeção Procedente/Improcedente).
class ContestacaoOperacional(models.Model):
    """Contestação de falha tratada, aberta pela liderança operacional.

    Não vive em `auditoria_falha_cadastro` enquanto pendente — apenas referencia
    o tratado original. Domínio (fraud/compliance) é independente de `origem`.
    """

    DOMINIO_FRAUD = "fraud"
    DOMINIO_COMPLIANCE = "compliance"
    DOMINIO_CHOICES = [
        (DOMINIO_FRAUD, "Fraud"),
        (DOMINIO_COMPLIANCE, "Compliance"),
    ]

    CATEGORIA_AUDITORIA_FRAUD = "auditoria_fraud"
    CATEGORIA_AUDITORIA_COMPLIANCE = "auditoria_compliance"
    CATEGORIA_CONTESTACAO_FRAUD = "contestacao_fraud"
    CATEGORIA_REINSPECAO_COMPLIANCE = "reinspecao_compliance"
    CATEGORIA_CHOICES = [
        (CATEGORIA_AUDITORIA_FRAUD, "Auditoria Fraud"),
        (CATEGORIA_AUDITORIA_COMPLIANCE, "Auditoria Compliance"),
        (CATEGORIA_CONTESTACAO_FRAUD, "Contestação Fraud"),
        (CATEGORIA_REINSPECAO_COMPLIANCE, "Reinspeção Compliance"),
    ]

    STATUS_PENDENTE = "pendente"
    STATUS_EM_ANALISE = "em_analise"
    STATUS_PROCEDENTE = "procedente"
    STATUS_IMPROCEDENTE = "improcedente"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_EM_ANALISE, "Em análise"),
        (STATUS_PROCEDENTE, "Procedente"),
        (STATUS_IMPROCEDENTE, "Improcedente"),
    ]
    STATUS_ATIVOS = (STATUS_PENDENTE, STATUS_EM_ANALISE)

    FALHA_STATUS_ATIVA = "ativa"
    FALHA_STATUS_RETIRADA = "retirada"
    FALHA_STATUS_MANTIDA = "mantida"
    FALHA_STATUS_CHOICES = [
        (FALHA_STATUS_ATIVA, "Ativa"),
        (FALHA_STATUS_RETIRADA, "Retirada"),
        (FALHA_STATUS_MANTIDA, "Mantida"),
    ]
    DESTINO_FALHA_CHOICES = [
        (FALHA_STATUS_RETIRADA, "Retirar falha"),
        (FALHA_STATUS_MANTIDA, "Manter falha"),
    ]

    falha = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.PROTECT,
        related_name="contestacoes_operacionais",
    )
    dominio = models.CharField(max_length=16, choices=DOMINIO_CHOICES, db_index=True)
    origem_tecnica = models.CharField(
        max_length=16,
        blank=True,
        default="",
        db_index=True,
        help_text="Espelho de falha.origem (auditoria|contestacao|reinspecao).",
    )
    categoria = models.CharField(max_length=32, choices=CATEGORIA_CHOICES, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
        db_index=True,
    )
    justificativa_lider = models.TextField()
    parecer_interno = models.TextField(blank=True, default="")
    destino_falha = models.CharField(
        max_length=16,
        choices=DESTINO_FALHA_CHOICES,
        blank=True,
        default="",
    )
    cenario_original = models.CharField(max_length=255, blank=True, default="")
    nivel_original = models.CharField(max_length=255, blank=True, default="")
    etapa_original = models.CharField(max_length=255, blank=True, default="")
    cenario_decisao = models.CharField(max_length=255, blank=True, default="")
    nivel_decisao = models.CharField(max_length=255, blank=True, default="")
    etapa_decisao = models.CharField(max_length=255, blank=True, default="")
    protocolo = models.CharField(max_length=100, db_index=True)
    agente_usuario = models.CharField(max_length=255, db_index=True)
    atribuida_em = models.DateTimeField(
        db_index=True,
        help_text="Data de atribuição da falha ao agente (não a criação desta contestação).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="contestacoes_operacionais_criadas",
    )
    analista = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestacoes_operacionais_analisadas",
    )
    analista_decisao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestacoes_operacionais_decididas",
        help_text="Analista que registrou a decisão original (preservado em revisões).",
    )
    falhas_manter_ids = models.JSONField(default=list, blank=True)
    parecer_revisao = models.TextField(blank=True, default="")
    iniciada_em = models.DateTimeField(null=True, blank=True)
    decidida_em = models.DateTimeField(null=True, blank=True)
    operational_support_request = models.ForeignKey(
        "suporte_operacional.OperationalSupportRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestacoes_operacionais_vinculadas",
        db_index=True,
        help_text="Solicitação de suporte operacional respondida vinculada a esta contestação.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_contestacao_operacional"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["dominio", "status"], name="qco_dominio_status_idx"),
            models.Index(fields=["categoria", "status"], name="qco_categoria_status_idx"),
            models.Index(fields=["created_by", "created_at"], name="qco_lider_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["falha"],
                condition=models.Q(status__in=["pendente", "em_analise"]),
                name="qco_uma_ativa_por_falha",
            ),
        ]

    def __str__(self):
        return f"Contestação operacional {self.pk} ({self.protocolo})"


class ContestacaoOperacionalHistorico(models.Model):
    """Trilha de auditoria da contestação operacional."""

    EVENTO_CRIADA = "criada"
    EVENTO_ANALISE_INICIADA = "analise_iniciada"
    EVENTO_DECIDIDA = "decidida"
    EVENTO_DEVOLVIDA_FILA = "devolvida_fila"
    EVENTO_RECLASSIFICADA = "reclassificada"
    EVENTO_RESULTADO_ALTERADO = "resultado_alterado"
    EVENTO_CHOICES = [
        (EVENTO_CRIADA, "Criada"),
        (EVENTO_ANALISE_INICIADA, "Análise iniciada"),
        (EVENTO_DECIDIDA, "Decidida"),
        (EVENTO_DEVOLVIDA_FILA, "Devolvida à fila"),
        (EVENTO_RECLASSIFICADA, "Reclassificada"),
        (EVENTO_RESULTADO_ALTERADO, "Resultado alterado"),
    ]

    contestacao = models.ForeignKey(
        ContestacaoOperacional,
        on_delete=models.CASCADE,
        related_name="historico",
    )
    evento = models.CharField(max_length=32, choices=EVENTO_CHOICES, db_index=True)
    status_anterior = models.CharField(max_length=16, blank=True, default="")
    status_novo = models.CharField(max_length=16, blank=True, default="")
    falha_status_anterior = models.CharField(max_length=16, blank=True, default="")
    falha_status_novo = models.CharField(max_length=16, blank=True, default="")
    justificativa = models.TextField(blank=True, default="")
    parecer = models.TextField(blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestacao_operacional_historico",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "qualidade_contestacao_operacional_hist"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.evento} contestação={self.contestacao_id}"


def atividade_arquivo_upload_to(instance, filename: str) -> str:
    safe_name = (filename or "importacao.xlsx").replace("\\", "_").replace("/", "_")
    return f"auditoria/atividades/{instance.pk}/{safe_name}"


class AuditoriaAtividade(models.Model):
    STATUS_PENDENTE = "pendente"
    STATUS_EM_ANDAMENTO = "em_andamento"
    STATUS_CONCLUIDA = "concluida"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_EM_ANDAMENTO, "Em andamento"),
        (STATUS_CONCLUIDA, "Concluída"),
    ]

    TIPO_CONTESTACAO = "contestacao"
    TIPO_AUDITORIA = "auditoria"
    TIPO_REINSPECAO = "reinspecao"
    TIPO_CHOICES = [
        (TIPO_CONTESTACAO, "Contestação"),
        (TIPO_AUDITORIA, "Auditoria"),
        (TIPO_REINSPECAO, "Reinspeção"),
    ]

    tipo = models.CharField(
        max_length=16,
        choices=TIPO_CHOICES,
        default=TIPO_CONTESTACAO,
        db_index=True,
    )
    nome = models.CharField(max_length=255)
    arquivo_original = models.FileField(
        upload_to=atividade_arquivo_upload_to,
        blank=True,
        null=True,
    )
    nome_arquivo_original = models.CharField(max_length=255, blank=True, default="")
    workflow = models.CharField(max_length=255, blank=True, default="")
    nivel_hierarquico = models.CharField(max_length=255, blank=True, default="")
    cliente = models.CharField(max_length=255, blank=True, default="")
    link_demanda = models.URLField(max_length=500, blank=True, default="")
    brflow_raw = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    observacao = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
        db_index=True,
    )
    data_recepcao = models.DateTimeField(null=True, blank=True, db_index=True)
    encerrado_em = models.DateTimeField(null=True, blank=True)
    total_protocolos = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="auditoria_atividades",
    )
    responsavel = models.ForeignKey(
        "workforce.Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_atividades_responsavel",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_atividade"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["tipo", "created_at"],
                name="aud_ativ_tipo_created_idx",
            ),
        ]

    def __str__(self):
        return self.nome


class AuditoriaAtividadeProtocolo(models.Model):
    STATUS_PENDENTE = "pendente"
    STATUS_EM_ANDAMENTO = "em_andamento"
    STATUS_CONCLUIDO = "concluido"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_EM_ANDAMENTO, "Em andamento"),
        (STATUS_CONCLUIDO, "Concluído"),
    ]

    atividade = models.ForeignKey(
        AuditoriaAtividade,
        on_delete=models.CASCADE,
        related_name="protocolos",
    )
    protocolo = models.CharField(max_length=100, db_index=True)
    workflow = models.CharField(max_length=255, blank=True, default="")
    nivel_hierarquico = models.CharField(max_length=255, blank=True, default="")
    resultado_contestado = models.TextField(blank=True, default="")
    numero_contrato = models.CharField(max_length=128, blank=True, default="")
    resultado_pos_auditoria = models.TextField(blank=True, default="")
    tipo_conclusao = models.CharField(max_length=255, blank=True, default="")
    tipo_falha = models.CharField(max_length=255, blank=True, default="")
    cenario = models.CharField(max_length=255, blank=True, default="")
    detalhamento = models.TextField(blank=True, default="")
    conclusao_contestacao = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
        db_index=True,
    )
    excel_row = models.PositiveIntegerField()
    brflow_raw = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    resultado_correto = models.TextField(blank=True, default="")
    nivel_dificuldade = models.CharField(max_length=100, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    uf_documento = models.CharField(max_length=50, blank=True, default="")
    agente = models.CharField(max_length=255, blank=True, default="")
    tipo_falha_analise = models.CharField(max_length=32, blank=True, default="")
    etapa_falha = models.CharField(max_length=255, blank=True, default="")
    cruzamento_bases = models.CharField(max_length=255, blank=True, default="")
    qualidade_imagem = models.CharField(max_length=255, blank=True, default="")
    SITUACAO_IMPROCEDENTE = "improcedente"
    SITUACAO_PROCEDENTE = "procedente"
    SITUACAO_CHOICES = [
        (SITUACAO_IMPROCEDENTE, "Improcedente"),
        (SITUACAO_PROCEDENTE, "Procedente"),
    ]
    situacao = models.CharField(max_length=20, choices=SITUACAO_CHOICES, blank=True, default="")
    motivo_falha = models.CharField(max_length=255, blank=True, default="")
    reanalisado = models.BooleanField(default=False)
    consideracoes_finais = models.TextField(blank=True, default="")
    analisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_protocolos_analisados",
    )
    analisado_em = models.DateTimeField(null=True, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    tratado = models.OneToOneField(
        AuditoriaFalhaCadastro,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestacao_protocolo_origem",
        help_text="Cópia consolidada deste protocolo na base única de tratados.",
    )
    tratado_referencia = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestacao_protocolos_espelhados",
        help_text="Tratado anterior espelhado neste protocolo (contestação duplicada).",
    )
    operational_support_request = models.ForeignKey(
        "suporte_operacional.OperationalSupportRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_protocolos_vinculados",
        db_index=True,
        help_text="Solicitação de suporte operacional respondida vinculada a este protocolo.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_atividade_protocolo"
        ordering = ["excel_row", "protocolo"]
        constraints = [
            models.UniqueConstraint(
                fields=["atividade", "protocolo"],
                name="uniq_auditoria_atividade_protocolo",
            ),
        ]

    def __str__(self):
        return f"{self.protocolo} ({self.atividade_id})"


class AuditoriaAtividadeProtocoloEtapa(models.Model):
    SITUACAO_IMPROCEDENTE = "improcedente"
    SITUACAO_PROCEDENTE = "procedente"
    SITUACAO_CHOICES = [
        (SITUACAO_IMPROCEDENTE, "Improcedente"),
        (SITUACAO_PROCEDENTE, "Procedente"),
    ]

    protocolo = models.ForeignKey(
        AuditoriaAtividadeProtocolo,
        on_delete=models.CASCADE,
        related_name="etapas",
    )
    ordem = models.PositiveIntegerField(default=0)
    resultado_correto = models.TextField(blank=True, default="")
    nivel_dificuldade = models.CharField(max_length=100, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    uf_documento = models.CharField(max_length=50, blank=True, default="")
    agente = models.CharField(max_length=255, blank=True, default="")
    tipo_falha = models.CharField(max_length=32, blank=True, default="")
    etapa_falha = models.CharField(max_length=255, blank=True, default="")
    tempo_analise = models.CharField(max_length=16, blank=True, default="")
    cruzamento_bases = models.CharField(max_length=255, blank=True, default="")
    qualidade_imagem = models.CharField(max_length=255, blank=True, default="")
    situacao = models.CharField(max_length=20, choices=SITUACAO_CHOICES, blank=True, default="")
    motivo_falha = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_atividade_protocolo_etapa"
        ordering = ["ordem", "id"]

    def __str__(self):
        return f"{self.protocolo.protocolo} - {self.etapa_falha or self.ordem}"


class AuditoriaAtividadeImportStaging(models.Model):
    token = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    arquivo = models.FileField(upload_to="auditoria/import_staging/%Y/%m/")
    nome_arquivo = models.CharField(max_length=255)
    preview = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auditoria_import_stagings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "auditoria_atividade_import_staging"
        ordering = ["-created_at"]


def controle_selfie_upload_to(instance, filename: str) -> str:
    tipo = getattr(instance, "tipo", None) or "controle"
    return f"auditoria/controles/{tipo}/selfies/{filename}"


class AuditoriaControleRegistro(models.Model):
    TIPO_REMOCAO_BASE_NEGATIVA = "remocao_base_negativa"
    TIPO_REMOCAO_BASE_POSITIVA = "remocao_base_positiva"
    TIPO_SOLICITACOES_IDAS_BIO = "solicitacoes_idas_bio"
    TIPO_CHOICES = [
        (TIPO_REMOCAO_BASE_NEGATIVA, "Remoção - Base negativa"),
        (TIPO_REMOCAO_BASE_POSITIVA, "Remoção - Base positiva"),
        (TIPO_SOLICITACOES_IDAS_BIO, "Solicitações IDAS e BIO"),
    ]

    tipo = models.CharField(max_length=64, choices=TIPO_CHOICES, db_index=True)
    dados = models.JSONField(default=dict, blank=True)
    situacao = models.CharField(max_length=64, blank=True, default="", db_index=True)
    selfie = models.ImageField(upload_to=controle_selfie_upload_to, blank=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_controles_criados",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_controle_registro"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["created_at"], name="aud_ctrl_created_idx"),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} #{self.pk}"


class ReinspecaoAuditorPresence(models.Model):
    CONTEXTO_REINSPECAO = "reinspecao"
    CONTEXTO_AUDITORIA_COMPLIANCE = "auditoria_compliance"
    CONTEXTO_CHOICES = [
        (CONTEXTO_REINSPECAO, "Reinspeção"),
        (CONTEXTO_AUDITORIA_COMPLIANCE, "Auditoria Compliance"),
    ]
    STATUS_ONLINE = "online"
    STATUS_OFFLINE = "offline"
    STATUS_AUSENTE = "ausente"
    STATUS_CHOICES = [
        (STATUS_ONLINE, "Online"),
        (STATUS_OFFLINE, "Offline"),
        (STATUS_AUSENTE, "Ausente"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reinspecao_presences",
    )
    contexto = models.CharField(
        max_length=32,
        choices=CONTEXTO_CHOICES,
        default=CONTEXTO_REINSPECAO,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OFFLINE,
        db_index=True,
    )
    status_changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_assigned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reinspecao_auditor_presence"
        ordering = ["user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "contexto"],
                name="reinspecao_presence_user_contexto_uniq",
            )
        ]

    def __str__(self):
        return f"{self.user_id}:{self.status}"


class ReinspecaoFilaHistorico(models.Model):
    TIPO_ATRIBUICAO_AUTO = "atribuicao_auto"
    TIPO_DIRECIONAMENTO = "direcionamento_manual"
    TIPO_REATRIBUICAO = "reatribuicao"
    TIPO_LIBERACAO_STATUS = "liberacao_status"
    TIPO_LIBERACAO_TIMEOUT = "liberacao_timeout"
    TIPO_INICIO_ANALISE = "inicio_analise"
    TIPO_CONCLUSAO = "conclusao"
    TIPO_STATUS_AUDITOR = "status_auditor"
    TIPO_CHOICES = [
        (TIPO_ATRIBUICAO_AUTO, "Atribuição automática"),
        (TIPO_DIRECIONAMENTO, "Direcionamento manual"),
        (TIPO_REATRIBUICAO, "Reatribuição"),
        (TIPO_LIBERACAO_STATUS, "Liberação por status"),
        (TIPO_LIBERACAO_TIMEOUT, "Liberação por timeout"),
        (TIPO_INICIO_ANALISE, "Início da análise"),
        (TIPO_CONCLUSAO, "Conclusão"),
        (TIPO_STATUS_AUDITOR, "Alteração de status do auditor"),
    ]
    contexto = models.CharField(
        max_length=32,
        choices=ReinspecaoAuditorPresence.CONTEXTO_CHOICES,
        default=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
        db_index=True,
    )

    falha = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reinspecao_historico",
    )
    pendente = models.ForeignKey(
        "QualidadePendenteReinspecao",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reinspecao_historico",
    )
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_historico_auditor",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_historico_actor",
    )
    tipo = models.CharField(max_length=32, choices=TIPO_CHOICES, db_index=True)
    justificativa = models.TextField(blank=True, default="")
    detalhe = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "reinspecao_fila_historico"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.tipo}#{self.pk}"


class QualidadePendenteReinspecao(ReinspecaoMappingMetadataMixin):
    """Protocolos de reinspeção aguardando análise (ID P…). Ao finalizar → falha (F)."""

    ANALISE_NAO_ATRIBUIDO = "nao_atribuido"
    ANALISE_AGUARDANDO = "aguardando_analise"
    ANALISE_EM_ANALISE = "em_analise"
    ANALISE_STATUS_CHOICES = [
        (ANALISE_NAO_ATRIBUIDO, "Não atribuído"),
        (ANALISE_AGUARDANDO, "Aguardando análise"),
        (ANALISE_EM_ANALISE, "Em análise"),
    ]
    FILA_ORIGEM_AUTO = "auto"
    FILA_ORIGEM_DIRECIONADO = "direcionado"
    FILA_ORIGEM_CHOICES = [
        (FILA_ORIGEM_AUTO, "Atribuição automática"),
        (FILA_ORIGEM_DIRECIONADO, "Direcionamento manual"),
    ]
    contexto = models.CharField(
        max_length=32,
        choices=ReinspecaoAuditorPresence.CONTEXTO_CHOICES,
        default=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
        db_index=True,
    )

    protocolo = models.CharField(max_length=100, db_index=True)
    usuario = models.CharField(max_length=255, blank=True, default="")
    descricao_irregularidades = models.TextField(blank=True, default="")
    data_contestacao = models.DateTimeField(null=True, blank=True, db_index=True)
    data_analise = models.DateTimeField(null=True, blank=True, db_index=True)
    cliente = models.CharField(max_length=255, blank=True, default="")
    modulo = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=128, blank=True, default="")
    observacao = models.TextField(blank=True, default="")
    auditor = models.CharField(max_length=255, blank=True, default="")
    tipo_falha = models.CharField(max_length=32, blank=True, default="reinspecao")
    etapa_falha = models.CharField(max_length=255, blank=True, default="")
    motivo_falha = models.CharField(max_length=255, blank=True, default="")
    nivel_dificuldade = models.CharField(max_length=100, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    uf_documento = models.CharField(max_length=50, blank=True, default="")
    novo_resultado = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    data_resposta = models.DateTimeField(null=True, blank=True, db_index=True)
    analise_concluida_em = models.DateTimeField(null=True, blank=True)
    # Compat: fila ainda referencia ANALISE_CONCLUIDO em filtros; pendentes não ficam concluídos
    # (são promovidos à tabela de tratados).
    ANALISE_CONCLUIDO = "concluido"
    payload = models.JSONField(default=dict, blank=True)
    analise_status = models.CharField(
        max_length=32,
        choices=ANALISE_STATUS_CHOICES,
        default=ANALISE_NAO_ATRIBUIDO,
        blank=True,
        db_index=True,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_pendentes_responsavel",
    )
    atribuido_em = models.DateTimeField(null=True, blank=True, db_index=True)
    analise_iniciada_em = models.DateTimeField(null=True, blank=True)
    fila_origem = models.CharField(
        max_length=16,
        choices=FILA_ORIGEM_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_pendentes_criados",
    )
    operational_support_request = models.ForeignKey(
        "suporte_operacional.OperationalSupportRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_pendentes_vinculados",
        db_index=True,
        help_text="Solicitação de suporte operacional respondida vinculada a este pendente.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_pendente_reinspecao"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pendente reinspeção {self.protocolo} ({self.pk})"


class ReinspecaoOcorrencia(models.Model):
    """Ledger idempotente das ocorrencias recebidas pelos fluxos de Reinspecao."""

    STATUS_RESERVADA = "reservada"
    STATUS_PENDENTE = "pendente"
    STATUS_TRATADA = "tratada"
    STATUS_CHOICES = [
        (STATUS_RESERVADA, "Reservada"),
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_TRATADA, "Tratada"),
    ]

    contexto = models.CharField(
        max_length=32,
        choices=ReinspecaoAuditorPresence.CONTEXTO_CHOICES,
        default=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
        db_index=True,
    )
    occurrence_key = models.CharField(max_length=64, unique=True)
    protocolo = models.CharField(max_length=100, db_index=True)
    descricao_irregularidades = models.TextField()
    irregularidade_normalizada = models.TextField()
    data_contestacao = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=64, blank=True, default="", db_index=True)
    last_source_file = models.CharField(max_length=255, blank=True, default="")
    last_source_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RESERVADA,
        db_index=True,
    )
    pendente = models.OneToOneField(
        QualidadePendenteReinspecao,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ocorrencia_ledger",
    )
    tratado = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ocorrencias_reinspecao",
    )
    first_seen_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField(db_index=True)
    seen_count = models.PositiveIntegerField(default=1)
    linked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reinspecao_ocorrencia"
        ordering = ["-last_seen_at", "-id"]
        indexes = [
            models.Index(
                fields=["contexto", "status", "last_seen_at"],
                name="reins_ocorr_ctx_status_idx",
            ),
            models.Index(
                fields=["protocolo", "data_contestacao"],
                name="reins_ocorr_proto_data_idx",
            ),
        ]

    def __str__(self):
        return f"{self.protocolo} ({self.status})"


class ReinspecaoGedDivergencia(models.Model):
    """Ocorrencia tratada no PPLID que ainda consta elegivel no GED."""

    STATUS_ABERTA = "aberta"
    STATUS_RECONHECIDA = "reconhecida"
    STATUS_RESOLVIDA = "resolvida"
    STATUS_CHOICES = [
        (STATUS_ABERTA, "Aberta"),
        (STATUS_RECONHECIDA, "Reconhecida"),
        (STATUS_RESOLVIDA, "Resolvida"),
    ]

    contexto = models.CharField(
        max_length=32,
        choices=ReinspecaoAuditorPresence.CONTEXTO_CHOICES,
        default=ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO,
        db_index=True,
    )
    occurrence_key = models.CharField(max_length=64, unique=True)
    protocolo = models.CharField(max_length=100, db_index=True)
    descricao_irregularidades = models.TextField()
    irregularidade_normalizada = models.TextField()
    data_contestacao = models.DateTimeField(db_index=True)
    ocorrencia = models.OneToOneField(
        ReinspecaoOcorrencia,
        on_delete=models.PROTECT,
        related_name="divergencia_ged",
    )
    tratado = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.PROTECT,
        related_name="divergencias_ged",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_ABERTA,
        db_index=True,
    )
    first_seen_at = models.DateTimeField(db_index=True)
    last_seen_at = models.DateTimeField(db_index=True)
    seen_count = models.PositiveIntegerField(default=1)
    reopened_count = models.PositiveIntegerField(default=0)
    last_source_file = models.CharField(max_length=255, blank=True, default="")
    last_source_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    reconhecida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_ged_divergencias_reconhecidas",
    )
    reconhecida_em = models.DateTimeField(null=True, blank=True)
    reconhecimento_observacao = models.TextField(blank=True, default="")
    resolvida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_ged_divergencias_resolvidas",
    )
    resolvida_em = models.DateTimeField(null=True, blank=True)
    resolucao_motivo = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reinspecao_ged_divergencia"
        ordering = ["-last_seen_at", "-id"]
        indexes = [
            models.Index(
                fields=["contexto", "status", "last_seen_at"],
                name="reins_ged_div_ctx_status_idx",
            ),
            models.Index(
                fields=["protocolo", "data_contestacao"],
                name="reins_ged_div_proto_data_idx",
            ),
        ]

    def __str__(self):
        return f"{self.protocolo} ({self.status})"


class ReinspecaoGedDivergenciaEvento(models.Model):
    TIPO_DETECTADA = "detectada"
    TIPO_RECONHECIDA = "reconhecida"
    TIPO_RESOLVIDA = "resolvida"
    TIPO_REABERTA = "reaberta"
    TIPO_CHOICES = [
        (TIPO_DETECTADA, "Detectada"),
        (TIPO_RECONHECIDA, "Reconhecida"),
        (TIPO_RESOLVIDA, "Resolvida"),
        (TIPO_REABERTA, "Reaberta"),
    ]

    divergencia = models.ForeignKey(
        ReinspecaoGedDivergencia,
        on_delete=models.PROTECT,
        related_name="eventos",
    )
    tipo = models.CharField(max_length=16, choices=TIPO_CHOICES, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reinspecao_ged_divergencia_eventos",
    )
    source_file = models.CharField(max_length=255, blank=True, default="")
    source_hash = models.CharField(max_length=64, blank=True, default="")
    detalhe = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "reinspecao_ged_divergencia_evento"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.tipo}#{self.divergencia_id}"


class AuditoriaComplianceAuditorPresence(models.Model):
    """Presença exclusiva dos auditores da fila de Auditoria Compliance."""

    STATUS_ONLINE = "online"
    STATUS_OFFLINE = "offline"
    STATUS_AUSENTE = "ausente"
    STATUS_CHOICES = ReinspecaoAuditorPresence.STATUS_CHOICES

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auditoria_compliance_presences",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OFFLINE,
        db_index=True,
    )
    status_changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_assigned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auditoria_compliance_auditor_presence"
        ordering = ["user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                name="aud_comp_presence_user_uniq",
            )
        ]

    @property
    def contexto(self) -> str:
        return ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE


class QualidadePendenteAuditoriaCompliance(models.Model):
    """Protocolos exclusivos da fila operacional de Auditoria Compliance."""

    ANALISE_NAO_ATRIBUIDO = QualidadePendenteReinspecao.ANALISE_NAO_ATRIBUIDO
    ANALISE_AGUARDANDO = QualidadePendenteReinspecao.ANALISE_AGUARDANDO
    ANALISE_EM_ANALISE = QualidadePendenteReinspecao.ANALISE_EM_ANALISE
    ANALISE_CONCLUIDO = QualidadePendenteReinspecao.ANALISE_CONCLUIDO
    ANALISE_STATUS_CHOICES = QualidadePendenteReinspecao.ANALISE_STATUS_CHOICES
    FILA_ORIGEM_AUTO = QualidadePendenteReinspecao.FILA_ORIGEM_AUTO
    FILA_ORIGEM_DIRECIONADO = QualidadePendenteReinspecao.FILA_ORIGEM_DIRECIONADO
    FILA_ORIGEM_CHOICES = QualidadePendenteReinspecao.FILA_ORIGEM_CHOICES

    protocolo = models.CharField(max_length=100, db_index=True)
    usuario = models.CharField(max_length=255, blank=True, default="")
    descricao_irregularidades = models.TextField(blank=True, default="")
    data_contestacao = models.DateTimeField(null=True, blank=True, db_index=True)
    data_analise = models.DateTimeField(null=True, blank=True, db_index=True)
    cliente = models.CharField(max_length=255, blank=True, default="")
    modulo = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=128, blank=True, default="")
    observacao = models.TextField(blank=True, default="")
    auditor = models.CharField(max_length=255, blank=True, default="")
    tipo_falha = models.CharField(max_length=32, blank=True, default="auditoria")
    etapa_falha = models.CharField(max_length=255, blank=True, default="")
    motivo_falha = models.CharField(max_length=255, blank=True, default="")
    nivel_dificuldade = models.CharField(max_length=100, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    uf_documento = models.CharField(max_length=50, blank=True, default="")
    novo_resultado = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    data_resposta = models.DateTimeField(null=True, blank=True, db_index=True)
    analise_concluida_em = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    analise_status = models.CharField(
        max_length=32,
        choices=ANALISE_STATUS_CHOICES,
        default=ANALISE_NAO_ATRIBUIDO,
        blank=True,
        db_index=True,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_compliance_pendentes_responsavel",
    )
    atribuido_em = models.DateTimeField(null=True, blank=True, db_index=True)
    analise_iniciada_em = models.DateTimeField(null=True, blank=True)
    fila_origem = models.CharField(
        max_length=16,
        choices=FILA_ORIGEM_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_compliance_pendentes_criados",
    )
    operational_support_request = models.ForeignKey(
        "suporte_operacional.OperationalSupportRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_compliance_pendentes_vinculados",
        db_index=True,
        help_text="Solicitação de suporte operacional respondida vinculada a este pendente.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_pendente_auditoria_compliance"
        ordering = ["-created_at"]

    @property
    def contexto(self) -> str:
        return ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE


class AuditoriaComplianceFilaHistorico(models.Model):
    """Eventos exclusivos da fila de Auditoria Compliance."""

    TIPO_ATRIBUICAO_AUTO = ReinspecaoFilaHistorico.TIPO_ATRIBUICAO_AUTO
    TIPO_DIRECIONAMENTO = ReinspecaoFilaHistorico.TIPO_DIRECIONAMENTO
    TIPO_REATRIBUICAO = ReinspecaoFilaHistorico.TIPO_REATRIBUICAO
    TIPO_LIBERACAO_STATUS = ReinspecaoFilaHistorico.TIPO_LIBERACAO_STATUS
    TIPO_LIBERACAO_TIMEOUT = ReinspecaoFilaHistorico.TIPO_LIBERACAO_TIMEOUT
    TIPO_INICIO_ANALISE = ReinspecaoFilaHistorico.TIPO_INICIO_ANALISE
    TIPO_CONCLUSAO = ReinspecaoFilaHistorico.TIPO_CONCLUSAO
    TIPO_STATUS_AUDITOR = ReinspecaoFilaHistorico.TIPO_STATUS_AUDITOR
    TIPO_CHOICES = ReinspecaoFilaHistorico.TIPO_CHOICES

    falha = models.ForeignKey(
        AuditoriaFalhaCadastro,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="auditoria_compliance_historico",
    )
    pendente = models.ForeignKey(
        QualidadePendenteAuditoriaCompliance,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="auditoria_compliance_historico",
    )
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_compliance_historico_auditor",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_compliance_historico_actor",
    )
    tipo = models.CharField(max_length=32, choices=TIPO_CHOICES, db_index=True)
    justificativa = models.TextField(blank=True, default="")
    detalhe = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "auditoria_compliance_fila_historico"
        ordering = ["-created_at", "-id"]

    @property
    def contexto(self) -> str:
        return ReinspecaoAuditorPresence.CONTEXTO_AUDITORIA_COMPLIANCE


class AuditoriaComplianceImportStaging(models.Model):
    token = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    arquivo = models.FileField(upload_to="auditoria/compliance/import_staging/%Y/%m/")
    nome_arquivo = models.CharField(max_length=255)
    preview = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auditoria_compliance_import_stagings",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "auditoria_compliance_import_staging"
        ordering = ["-created_at"]


def auditoria_compliance_import_arquivo_upload_to(instance, filename: str) -> str:
    safe_name = (filename or "importacao.xlsx").replace("\\", "_").replace("/", "_")
    created = instance.created_at or timezone.now()
    return f"qualidade/auditoria-compliance-imports/{created:%Y/%m/%d}/{instance.pk}_{safe_name}"


class AuditoriaComplianceImportArquivo(models.Model):
    arquivo = models.FileField(upload_to=auditoria_compliance_import_arquivo_upload_to)
    nome_arquivo_original = models.CharField(max_length=255)
    content_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    protocolos_importados = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_compliance_import_arquivos",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "auditoria_compliance_import_arquivo"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["created_at"], name="aud_comp_imp_created_idx"),
        ]


class QualidadePendenteAuditoria(models.Model):
    """Cadastros de auditoria avulsa aguardando registro de falha."""

    STATUS_PENDENTE = "pendente"
    STATUS_EM_ANDAMENTO = "em_andamento"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_EM_ANDAMENTO, "Em andamento"),
    ]

    protocolo = models.CharField(max_length=100, db_index=True, blank=True, default="")
    nome = models.CharField(max_length=255, blank=True, default="")
    cliente = models.CharField(max_length=255, blank=True, default="")
    workflow = models.CharField(max_length=255, blank=True, default="")
    nivel_hierarquico = models.CharField(max_length=255, blank=True, default="")
    link_demanda = models.URLField(max_length=500, blank=True, default="")
    brflow_raw = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
        db_index=True,
    )
    # Ponte temporária para UI atual baseada em AuditoriaAtividade.
    atividade = models.ForeignKey(
        "AuditoriaAtividade",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pendente_auditoria",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auditoria_pendentes_criados",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_pendente_auditoria"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pendente auditoria {self.protocolo or self.pk}"


class QualidadePendenteAuditoriaFalha(models.Model):
    """Rascunho de falha da auditoria; nunca compõe a base de tratados."""

    atividade = models.ForeignKey(
        "AuditoriaAtividade",
        on_delete=models.CASCADE,
        related_name="falhas",
    )
    protocolo = models.CharField(max_length=100, db_index=True)
    brflow_raw = models.TextField(blank=True, default="")
    brflow_parsed = models.JSONField(default=dict, blank=True)
    modulo = models.CharField(max_length=255, blank=True, default="")
    demanda_url = models.URLField(max_length=500, blank=True, default="")
    tipo_falha = models.CharField(max_length=32)
    usuario = models.CharField(max_length=255)
    resultado_cliente = models.TextField(blank=True, default="")
    novo_resultado = models.TextField(blank=True, default="")
    sinalizacao = models.CharField(max_length=255, blank=True, default="")
    motivo_falha = models.CharField(max_length=255, blank=True, default="")
    etapa_falha = models.CharField(max_length=255, blank=True, default="")
    tempo_analise = models.CharField(max_length=32, blank=True, default="")
    nivel_dificuldade = models.CharField(max_length=100, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    uf_documento = models.CharField(max_length=50, blank=True, default="")
    qualidade_imagem = models.CharField(max_length=255, blank=True, default="")
    observacao = models.TextField(blank=True, default="")
    tipo_registro = models.CharField(
        max_length=16,
        choices=AuditoriaFalhaCadastro.REGISTRO_CHOICES,
        default=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="auditoria_falhas_pendentes_criadas",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_pendente_auditoria_falha"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Rascunho auditoria {self.protocolo} ({self.tipo_falha})"


class QualidadePendenteContestacao(models.Model):
    """Lotes de contestação aguardando análise."""

    STATUS_PENDENTE = "pendente"
    STATUS_EM_ANDAMENTO = "em_andamento"
    STATUS_CHOICES = [
        (STATUS_PENDENTE, "Pendente"),
        (STATUS_EM_ANDAMENTO, "Em andamento"),
    ]

    nome = models.CharField(max_length=255, blank=True, default="")
    cliente = models.CharField(max_length=255, blank=True, default="")
    nome_arquivo_original = models.CharField(max_length=255, blank=True, default="")
    data_recepcao = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDENTE,
        db_index=True,
    )
    total_protocolos = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict, blank=True)
    atividade = models.ForeignKey(
        "AuditoriaAtividade",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pendente_contestacao",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestacao_pendentes_criados",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "qualidade_pendente_contestacao"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pendente contestação {self.nome or self.pk}"


def compliance_import_arquivo_upload_to(instance, filename: str) -> str:
    from django.utils import timezone

    safe_name = (filename or "importacao.xlsx").replace("\\", "_").replace("/", "_")
    created = instance.created_at or timezone.now()
    date_part = created.strftime("%Y/%m/%d")
    return (
        f"qualidade/compliance-imports/{instance.contexto}/{date_part}/"
        f"{instance.pk}_{safe_name}"
    )


class QualidadeComplianceImportArquivo(models.Model):
    CONTEXTO_REINSPECAO = "reinspecao"
    CONTEXTO_AUDITORIA_COMPLIANCE = "auditoria_compliance"
    CONTEXTO_CHOICES = [
        (CONTEXTO_REINSPECAO, "Reinspeção"),
        (CONTEXTO_AUDITORIA_COMPLIANCE, "Auditoria Compliance"),
    ]

    contexto = models.CharField(max_length=32, choices=CONTEXTO_CHOICES, db_index=True)
    arquivo = models.FileField(upload_to=compliance_import_arquivo_upload_to)
    nome_arquivo_original = models.CharField(max_length=255)
    content_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    protocolos_importados = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="qualidade_compliance_import_arquivos",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "qualidade_compliance_import_arquivo"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["contexto", "created_at"],
                name="qual_comp_imp_ctx_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.nome_arquivo_original} ({self.contexto})"
