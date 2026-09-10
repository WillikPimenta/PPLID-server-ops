from django.conf import settings
from django.db import models


class RotinaBrutoSyncLog(models.Model):
    REPORT_DETALHADO = "detalhado"
    REPORT_PROD = "prod"
    REPORT_MONITOR = "monitor"
    REPORT_CONFER_BUSCA = "confer_busca"
    REPORT_GED_DETALHADO = "ged_detalhado"
    REPORT_GED_IRREGULARIDADE = "ged_irregularidade"
    REPORT_G_AUDITORIA = "g_auditoria"
    REPORT_CHOICES = [
        (REPORT_DETALHADO, "Detalhado bruto"),
        (REPORT_PROD, "Produtividade D-1 bruto"),
        (REPORT_MONITOR, "Monitor tratado"),
        (REPORT_CONFER_BUSCA, "Confer busca protocolo tratado"),
        (REPORT_GED_DETALHADO, "GED detalhado tratado"),
        (REPORT_GED_IRREGULARIDADE, "GED irregularidade tratado"),
        (REPORT_G_AUDITORIA, "G Auditoria com etapas"),
    ]

    TRIGGER_USER = "user"
    TRIGGER_SYSTEM = "system"
    TRIGGER_CHOICES = [
        (TRIGGER_USER, "Usuário (portal/manual)"),
        (TRIGGER_SYSTEM, "Sistema (automático)"),
    ]

    report_type = models.CharField(max_length=20, choices=REPORT_CHOICES, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    trigger_source = models.CharField(
        max_length=20,
        choices=TRIGGER_CHOICES,
        default=TRIGGER_USER,
        db_index=True,
    )
    success = models.BooleanField(default=False)
    message = models.TextField(blank=True, default="")
    source_file = models.CharField(max_length=500, blank=True, default="")
    source_mtime = models.FloatField(null=True, blank=True)
    source_size = models.BigIntegerField(null=True, blank=True)
    report_date = models.DateField(null=True, blank=True, db_index=True)
    row_count = models.PositiveIntegerField(null=True, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, default="")
    mapping_version = models.PositiveSmallIntegerField(null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "rotina_bruto_sync_log"
        ordering = ["-started_at"]

    def __str__(self):
        return f"Rotina bruto {self.report_type} {self.started_at} ({'OK' if self.success else 'FAIL'})"


class RotinaDetalhadoBrutoRecord(models.Model):
    report_date = models.DateField(db_index=True)
    protocolo = models.BigIntegerField(null=True, blank=True, db_index=True)
    cliente = models.CharField(max_length=255, blank=True, default="")
    workflow = models.CharField(max_length=255, blank=True, default="")
    cpf = models.CharField(max_length=64, blank=True, default="")
    data_cadastro = models.DateField(null=True, blank=True)
    data_conclusao = models.DateField(null=True, blank=True)
    status_registro = models.CharField(max_length=255, blank=True, default="")
    resultado = models.CharField(max_length=255, blank=True, default="")
    nivel_hierarquico = models.CharField(max_length=255, blank=True, default="")
    matricula = models.CharField(max_length=64, blank=True, default="", db_index=True)
    data_primeira_conclusao = models.DateField(null=True, blank=True)
    data_analise = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "rotina_detalhado_bruto_record"
        ordering = ["-report_date", "protocolo"]
        indexes = [
            models.Index(fields=["report_date", "protocolo"]),
            models.Index(
                fields=["data_cadastro", "report_date"],
                name="rot_det_cad_report_idx",
            ),
        ]


class RotinaDetalhadoBrutoAlerta(models.Model):
    record = models.OneToOneField(
        RotinaDetalhadoBrutoRecord,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="alerta",
    )
    alertas = models.TextField(blank=True, default="")

    class Meta:
        db_table = "rotina_detalhado_bruto_alerta"


class RotinaProdBrutoRecord(models.Model):
    report_date = models.DateField(db_index=True)
    des_matricula = models.CharField(max_length=64, blank=True, default="", db_index=True)
    dat_analise = models.DateField(null=True, blank=True)
    num_tempo_analise = models.PositiveIntegerField(null=True, blank=True)
    nom_cliente = models.CharField(max_length=255, blank=True, default="")
    nom_workflow = models.CharField(max_length=255, blank=True, default="")
    nom_etapa = models.CharField(max_length=255, blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "rotina_prod_bruto_record"
        ordering = ["-report_date", "des_matricula"]
        indexes = [
            models.Index(fields=["report_date", "des_matricula"]),
        ]


class RotinaMonitorTratadoRecord(models.Model):
    report_date = models.DateField(db_index=True)
    data = models.DateField(db_index=True)
    hora = models.PositiveSmallIntegerField()
    matricula_usuario = models.CharField(max_length=64, blank=True, default="", db_index=True)
    data_evento = models.DateTimeField(null=True, blank=True, db_index=True)
    evento = models.CharField(max_length=255, blank=True, default="", db_index=True)
    data_segundo_evento = models.DateTimeField(null=True, blank=True)
    segundo_evento = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "rotina_monitor_tratado_record"
        ordering = ["-report_date", "-data_evento", "matricula_usuario"]
        indexes = [
            models.Index(fields=["report_date", "matricula_usuario"]),
            models.Index(fields=["report_date", "data", "hora"]),
        ]


class RotinaConferBuscaRecord(models.Model):
    report_date = models.DateField(db_index=True)
    periodo = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    data_hora_conferencia = models.DateTimeField(null=True, blank=True)
    tempo_por_minuto = models.CharField(max_length=64, blank=True, default="")
    protocolo = models.BigIntegerField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=255, blank=True, default="")
    ilha = models.CharField(max_length=255, blank=True, default="")
    etapa = models.CharField(max_length=255, blank=True, default="")
    matricula = models.CharField(max_length=64, blank=True, default="", db_index=True)
    nome = models.CharField(max_length=255, blank=True, default="")
    tipo_status_conferencia = models.CharField(max_length=255, blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "rotina_confer_busca_record"
        ordering = ["-report_date", "protocolo"]
        indexes = [
            models.Index(fields=["report_date", "periodo"]),
            models.Index(fields=["report_date", "protocolo"]),
        ]


class RotinaGedDetalhadoTratadoRecord(models.Model):
    report_date = models.DateField(db_index=True)
    periodo = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    protocolo = models.BigIntegerField(null=True, blank=True, db_index=True)
    data_recebimento = models.DateField(null=True, blank=True)
    tipo_servico_primario = models.CharField(max_length=255, blank=True, default="")
    data_venda = models.DateField(null=True, blank=True)
    data_batimento = models.DateField(null=True, blank=True)
    data_retorno_inspecao = models.DateField(null=True, blank=True)
    data_envio_inspe = models.DateField(null=True, blank=True)
    canal_ativacao = models.CharField(max_length=255, blank=True, default="")
    status_contrato = models.CharField(max_length=255, blank=True, default="")
    aceite_digital = models.CharField(max_length=255, blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "rotina_ged_detalhado_tratado_record"
        ordering = ["-report_date", "protocolo"]
        indexes = [
            models.Index(fields=["report_date", "periodo"]),
            models.Index(fields=["report_date", "protocolo"]),
        ]


class RotinaGedIrregularidadeTratadoRecord(models.Model):
    report_date = models.DateField(db_index=True)
    periodo = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    protocolo = models.BigIntegerField(null=True, blank=True, db_index=True)
    status_contrato = models.CharField(max_length=255, blank=True, default="")
    msisdn = models.CharField(max_length=512, blank=True, default="")
    cpf = models.CharField(max_length=64, blank=True, default="")
    data_recebimento = models.DateField(null=True, blank=True)
    data_contestacao = models.DateField(null=True, blank=True)
    data_resposta = models.DateField(null=True, blank=True)
    tipo_servico = models.CharField(max_length=255, blank=True, default="")
    regional = models.CharField(max_length=255, blank=True, default="")
    estado = models.CharField(max_length=64, blank=True, default="")
    canal_ativacao = models.CharField(max_length=255, blank=True, default="")
    cod_pdv = models.CharField(max_length=64, blank=True, default="")
    usuario = models.CharField(max_length=255, blank=True, default="")
    status_contestacao = models.CharField(max_length=255, blank=True, default="")
    matricula_inspetor = models.CharField(max_length=64, blank=True, default="", db_index=True)
    descricao_irregularidades = models.TextField(blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "rotina_ged_irregularidade_tratado_record"
        ordering = ["-report_date", "protocolo"]
        indexes = [
            models.Index(fields=["report_date", "periodo"]),
            models.Index(fields=["report_date", "protocolo"]),
        ]


class RotinaGAuditoriaRecord(models.Model):
    """Staging canônico e sem PII do relatório G Auditoria com etapas."""

    PRAZO_DENTRO = "dentro_prazo"
    PRAZO_FORA = "fora_prazo"
    PRAZO_ANALISE_AUSENTE = "data_analise_ausente"
    PRAZO_AUDITORIA_AUSENTE = "data_auditoria_ausente"
    PRAZO_DATAS_INVERTIDAS = "datas_invertidas"
    PRAZO_CHOICES = [
        (PRAZO_DENTRO, "Dentro do prazo"),
        (PRAZO_FORA, "Fora do prazo (120 dias ou mais)"),
        (PRAZO_ANALISE_AUSENTE, "Data de análise ausente"),
        (PRAZO_AUDITORIA_AUSENTE, "Data de auditoria ausente"),
        (PRAZO_DATAS_INVERTIDAS, "Datas invertidas"),
    ]

    report_date = models.DateField(db_index=True)
    source_file = models.CharField(max_length=500)
    source_key = models.CharField(max_length=64, unique=True)
    source_record_key = models.CharField(max_length=255, blank=True, default="")
    origin_transaction_id = models.CharField(max_length=255, blank=True, default="")
    origin_transaction_code = models.CharField(max_length=255, blank=True, default="")
    protocolo_origem = models.CharField(max_length=100, blank=True, default="", db_index=True)
    protocolo_normalizado = models.CharField(max_length=100, blank=True, default="", db_index=True)
    protocolo_destino = models.CharField(max_length=100, blank=True, default="")
    cliente_origem = models.CharField(max_length=255, blank=True, default="")
    workflow_origem = models.CharField(max_length=255, blank=True, default="")
    workflow_destino = models.CharField(max_length=255, blank=True, default="")
    matricula_agente = models.CharField(max_length=64, blank=True, default="", db_index=True)
    matricula_auditor = models.CharField(max_length=64, blank=True, default="")
    etapa = models.CharField(max_length=512, blank=True, default="")
    etapa_normalizada = models.CharField(max_length=512, blank=True, default="", db_index=True)
    data_analise = models.DateField(null=True, blank=True, db_index=True)
    data_auditoria = models.DateField(null=True, blank=True, db_index=True)
    resultado_origem = models.CharField(max_length=255, blank=True, default="")
    resultado_destino = models.CharField(max_length=255, blank=True, default="")
    status_destino = models.CharField(max_length=255, blank=True, default="")
    tipo_conclusao_destino = models.CharField(max_length=255, blank=True, default="")
    dias_prazo = models.IntegerField(null=True, blank=True)
    prazo_status = models.CharField(max_length=32, choices=PRAZO_CHOICES, db_index=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    validation_errors = models.JSONField(default=list, blank=True)
    is_quarantined = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "rotina_g_auditoria_record"
        ordering = ["-report_date", "protocolo_origem", "etapa"]
        indexes = [
            models.Index(
                fields=["protocolo_normalizado", "etapa_normalizada"],
                name="rot_gaud_prot_etapa_idx",
            ),
            models.Index(
                fields=["report_date", "is_active", "is_quarantined"],
                name="rot_gaud_date_state_idx",
            ),
            models.Index(
                fields=["data_auditoria", "data_analise"],
                name="rot_gaud_dates_idx",
            ),
        ]
