from django.conf import settings
from django.db import models


class FalhasAgent(models.Model):
    """Agente do módulo Falhas (snapshot Excel HC) — distinto de workforce.Agent."""

    matricula_norm = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=255, blank=True, default="")
    admissao_date = models.DateField(null=True, blank=True)
    tempo_casa = models.CharField(max_length=100, blank=True, default="")
    tempo_etapa = models.CharField(max_length=100, blank=True, default="")
    atividade_atual = models.CharField(max_length=255, blank=True, default="")
    turno_atual = models.CharField(max_length=100, blank=True, default="")
    localidade = models.CharField(max_length=100, blank=True, default="", db_index=True)
    leader_nome = models.CharField(max_length=255, blank=True, default="")
    job_title = models.CharField(max_length=255, blank=True, default="")
    team = models.CharField(max_length=255, blank=True, default="")
    team_categoria = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "falhas_agent"
        verbose_name = "agente (falhas)"
        verbose_name_plural = "agentes (falhas)"

    def __str__(self):
        return f"{self.matricula_norm} - {self.name or 'Sem Nome'}"


class Failure(models.Model):
    protocolo = models.CharField(max_length=100)
    data_analise = models.DateField(db_index=True)
    data_auditoria = models.DateField(null=True, blank=True)
    etapa = models.CharField(max_length=255, blank=True, default="")
    categoria = models.CharField(max_length=255, blank=True, default="")
    cenario = models.TextField(blank=True, default="")
    novo_cenario = models.TextField(blank=True, default="")
    localidade = models.CharField(max_length=100, db_index=True)
    cliente = models.CharField(max_length=255, blank=True, default="")
    workflow = models.CharField(max_length=255, blank=True, default="")
    dificuldade = models.CharField(max_length=100, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    uf_documento = models.CharField(max_length=50, blank=True, default="")
    tipo_falha = models.CharField(max_length=50, default="OUTROS")
    modulo = models.CharField(max_length=255, blank=True, default="")
    agent = models.ForeignKey(
        FalhasAgent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="failures",
    )

    class Meta:
        db_table = "falhas_failure"

    def __str__(self):
        return f"{self.protocolo} - {self.cenario[:30]}"


class Support(models.Model):
    protocolo = models.CharField(max_length=100, blank=True, default="")
    data = models.DateField(null=True, blank=True, db_index=True)
    cliente = models.CharField(max_length=255, blank=True, default="")
    workflow = models.CharField(max_length=255, blank=True, default="")
    tipo_solicitacao = models.CharField(max_length=255, blank=True, default="")
    conformidade = models.CharField(max_length=100, blank=True, default="")
    conformidade_regra = models.CharField(max_length=100, blank=True, default="")
    dificuldade = models.CharField(max_length=100, blank=True, default="")
    critico = models.CharField(max_length=100, blank=True, default="")
    uf_emissao = models.CharField(max_length=50, blank=True, default="")
    localidade = models.CharField(max_length=100, blank=True, default="", db_index=True)
    agente_nome_planilha = models.CharField(max_length=255, blank=True, default="")
    duvida = models.TextField(blank=True, default="")
    conclusao = models.TextField(blank=True, default="")
    lider_solicitante = models.CharField(max_length=255, blank=True, default="")
    tipo_documento = models.CharField(max_length=255, blank=True, default="")
    agent = models.ForeignKey(
        FalhasAgent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supports",
    )

    class Meta:
        db_table = "falhas_support"

    def __str__(self):
        return f"Suporte {self.protocolo} - {self.cliente}"


class Training(models.Model):
    titulo = models.CharField(max_length=500, blank=True, default="")
    tipo_acao = models.CharField(max_length=255, blank=True, default="")
    tipo_acao_prefixo = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=100, blank=True, default="")
    situacao = models.CharField(max_length=100, blank=True, default="")
    situacao_calculada = models.CharField(max_length=100, blank=True, default="")
    data_limite = models.DateField(null=True, blank=True)
    data_atribuicao = models.DateField(null=True, blank=True)
    data_assinatura = models.DateField(null=True, blank=True)
    data_inicio_sessao = models.DateField(null=True, blank=True)
    data_fim_sessao = models.DateField(null=True, blank=True)
    localidade = models.CharField(max_length=100, blank=True, default="", db_index=True)
    matricula = models.CharField(max_length=50, blank=True, default="", db_index=True)
    nome_agente = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "falhas_training"

    def __str__(self):
        return self.titulo or self.matricula


class Contestation(models.Model):
    protocolo = models.CharField(max_length=100, blank=True, default="")
    data = models.DateField(null=True, blank=True)
    fonte = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=255, blank=True, default="")
    localidade = models.CharField(max_length=100, blank=True, default="", db_index=True)
    agent = models.ForeignKey(
        FalhasAgent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contestations",
    )

    class Meta:
        db_table = "falhas_contestation"

    def __str__(self):
        return f"Contestação {self.protocolo}"


class PortalTicket(models.Model):
    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED = "resolved"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Aberto"),
        (STATUS_IN_PROGRESS, "Em andamento"),
        (STATUS_RESOLVED, "Resolvido"),
        (STATUS_CLOSED, "Fechado"),
    ]

    title = models.CharField(max_length=500)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    localidade = models.CharField(max_length=100, blank=True, default="")
    module = models.CharField(max_length=64, blank=True, default="")
    jira_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    jira_status_name = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="falhas_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "falhas_portal_ticket"
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.title[:40]}"


class PortalTicketComment(models.Model):
    ticket = models.ForeignKey(PortalTicket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="falhas_ticket_comments",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "falhas_portal_ticket_comment"
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment #{self.pk} on ticket {self.ticket_id}"


class ExecutiveReportArchive(models.Model):
    period_start = models.DateField()
    period_end = models.DateField()
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    file_path = models.CharField(max_length=500)

    class Meta:
        db_table = "falhas_executive_report_archive"
        ordering = ["-generated_at"]

    def __str__(self):
        return f"Executivo {self.period_start} a {self.period_end}"


class SyncAuditLog(models.Model):
    TRIGGER_USER = "user"
    TRIGGER_SYSTEM = "system"
    TRIGGER_CHOICES = [
        (TRIGGER_USER, "Usuário (portal/manual)"),
        (TRIGGER_SYSTEM, "Sistema (automático)"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
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
    path = models.CharField(max_length=500, blank=True, default="")
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "falhas_sync_audit_log"
        ordering = ["-started_at"]

    def __str__(self):
        src = "auto" if self.trigger_source == self.TRIGGER_SYSTEM else "manual"
        return f"Sync {self.started_at} [{src}] ({'OK' if self.success else 'FAIL'})"
