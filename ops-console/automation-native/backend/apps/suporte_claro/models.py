# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import models


def anexo_upload_to(instance, filename: str) -> str:
    registro_id = instance.registro_id or "pending"
    return f"suporte_claro/{registro_id}/{filename}"


class SuporteClaroRegistro(models.Model):
    STATUS_ABERTO = "aberto"
    STATUS_EM_ATENDIMENTO = "em_atendimento"
    STATUS_CONCLUIDO = "concluido"
    STATUS_CHOICES = [
        (STATUS_ABERTO, "Não iniciado"),
        (STATUS_EM_ATENDIMENTO, "Em andamento"),
        (STATUS_CONCLUIDO, "Concluído"),
    ]

    ORIGEM_TEAMS = "teams"
    ORIGEM_EMAIL = "email"
    ORIGEM_LIGACAO = "ligacao"
    ORIGEM_CHOICES = [
        (ORIGEM_TEAMS, "Teams"),
        (ORIGEM_EMAIL, "E-mail"),
        (ORIGEM_LIGACAO, "Ligação"),
    ]

    CHAMADO_JIRA = "jira"
    CHAMADO_SERVICE = "service"
    CHAMADO_SISTEMA_CHOICES = [
        (CHAMADO_JIRA, "Jira"),
        (CHAMADO_SERVICE, "ServiceNow"),
    ]

    CATEGORIA_DEMANDA = "demanda"
    CATEGORIA_INCIDENTE = "incidente"
    CATEGORIA_CHOICES = [
        (CATEGORIA_DEMANDA, "Demanda"),
        (CATEGORIA_INCIDENTE, "Incidente"),
    ]

    TIPO_INCIDENTE_LENTIDAO = "lentidao"
    TIPO_INCIDENTE_TRAVAMENTO = "travamento"
    TIPO_INCIDENTE_QUEDA = "queda"
    TIPO_INCIDENTE_ERRO = "erro"
    TIPO_INCIDENTE_OUTRO = "outro"
    TIPO_INCIDENTE_CHOICES = [
        (TIPO_INCIDENTE_LENTIDAO, "Lentidão"),
        (TIPO_INCIDENTE_TRAVAMENTO, "Travamento"),
        (TIPO_INCIDENTE_QUEDA, "Queda"),
        (TIPO_INCIDENTE_ERRO, "Erro"),
        (TIPO_INCIDENTE_OUTRO, "Outro"),
    ]

    titulo = models.CharField(max_length=255, blank=True, default="", db_index=True)
    protocolo = models.CharField(max_length=128, db_index=True)
    irregularidade = models.TextField(blank=True, default="")
    avaliacao = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(db_index=True)
    # Momento do retorno/conclusão (editável); se vazio, resolve_retorno_at usa o histórico.
    retorno_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sent_by = models.CharField(max_length=255, blank=True, default="")
    origem = models.CharField(
        max_length=16,
        choices=ORIGEM_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    categoria = models.CharField(
        max_length=16,
        choices=CATEGORIA_CHOICES,
        default=CATEGORIA_DEMANDA,
        db_index=True,
    )
    tipo_incidente = models.CharField(
        max_length=16,
        choices=TIPO_INCIDENTE_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_ABERTO,
        db_index=True,
    )
    chamado_sistema = models.CharField(
        max_length=16,
        choices=CHAMADO_SISTEMA_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )
    chamado_codigo = models.CharField(max_length=128, blank=True, default="")
    chamado_url = models.CharField(max_length=512, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="suporte_claro_registros",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "suporte_claro_registro"
        ordering = ["-created_at"]

    def __str__(self):
        display = self.titulo or self.protocolo
        return f"#{self.pk} {display}"


class SuporteClaroVinculoIncidente(models.Model):
    """Vínculo simétrico entre dois registros categoria=incidente (par ordenado a_id < b_id)."""

    from_registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="vinculos_from",
    )
    to_registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="vinculos_to",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="suporte_claro_vinculos",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_vinculo_incidente"
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["from_registro", "to_registro"],
                name="suporte_claro_vinculo_unique_pair",
            ),
            models.CheckConstraint(
                check=~models.Q(from_registro=models.F("to_registro")),
                name="suporte_claro_vinculo_no_self",
            ),
        ]

    def __str__(self):
        return f"#{self.pk} {self.from_registro_id}↔{self.to_registro_id}"


class SuporteClaroProtocolo(models.Model):
    registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="protocolos",
    )
    numero = models.CharField(max_length=64)
    comentario = models.TextField(blank=True, default="")
    ordem = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_protocolo"
        ordering = ["ordem", "id"]

    def __str__(self):
        return f"#{self.pk} {self.numero}"


class SuporteClaroEmail(models.Model):
    registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="emails",
    )
    endereco = models.CharField(max_length=254)
    comentario = models.TextField(blank=True, default="")
    ordem = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_email"
        ordering = ["ordem", "id"]

    def __str__(self):
        return f"#{self.pk} {self.endereco}"


class SuporteClaroHistorico(models.Model):
    ACTION_STATUS = "status"
    ACTION_EDIT = "edit"
    ACTION_IMPORT = "import"
    ACTION_CHOICES = [
        (ACTION_STATUS, "Status"),
        (ACTION_EDIT, "Edicao"),
        (ACTION_IMPORT, "Importacao"),
    ]

    registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="historico",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="suporte_claro_historico",
    )
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    field_name = models.CharField(max_length=64, blank=True, default="")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_historico"
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.registro_id} {self.action}"


class SuporteClaroAnexo(models.Model):
    registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="anexos",
    )
    file = models.FileField(upload_to=anexo_upload_to)
    original_name = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_anexo"
        ordering = ["id"]

    def __str__(self):
        return self.original_name or str(self.pk)


class SuporteClaroChamadoExterno(models.Model):
    NATUREZA_INTERNO = "interno"
    NATUREZA_EXTERNO = "externo"
    NATUREZA_CHOICES = [
        (NATUREZA_INTERNO, "Interno"),
        (NATUREZA_EXTERNO, "Externo"),
    ]

    registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="chamados_externos",
    )
    sistema = models.CharField(
        max_length=16,
        choices=SuporteClaroRegistro.CHAMADO_SISTEMA_CHOICES,
        db_index=True,
    )
    codigo = models.CharField(max_length=128, blank=True, default="")
    url = models.CharField(max_length=512, blank=True, default="")
    # Quem está tratando a demanda Jira (texto livre).
    tratado_por = models.CharField(max_length=255, blank=True, default="")
    natureza = models.CharField(
        max_length=16,
        choices=NATUREZA_CHOICES,
        default=NATUREZA_EXTERNO,
        db_index=True,
    )
    ordem = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_chamado_externo"
        ordering = ["ordem", "id"]

    def __str__(self):
        return f"#{self.pk} {self.natureza} {self.sistema} {self.codigo}"


class SuporteClaroImportRef(models.Model):
    """Vínculo idempotente Linha_ID (planilha offline) → registro no portal."""

    linha_id = models.CharField(max_length=128, unique=True, db_index=True)
    registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="import_refs",
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="suporte_claro_imports",
    )
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_import_ref"
        ordering = ["-imported_at"]

    def __str__(self):
        return f"{self.linha_id} → #{self.registro_id}"


class SuporteClaroComentarioEtapa(models.Model):
    """Comentário da demanda no portal; formalização externa é opt-in explícito."""

    VISIBILIDADE_INTERNO = "interno"
    VISIBILIDADE_EXTERNO = "externo"
    VISIBILIDADE_CHOICES = [
        (VISIBILIDADE_INTERNO, "Interno"),
        (VISIBILIDADE_EXTERNO, "Externo (formalizado)"),
    ]

    registro = models.ForeignKey(
        SuporteClaroRegistro,
        on_delete=models.CASCADE,
        related_name="comentarios_etapa",
    )
    texto = models.TextField()
    etapa_status = models.CharField(
        max_length=32,
        choices=SuporteClaroRegistro.STATUS_CHOICES,
        db_index=True,
    )
    visibilidade = models.CharField(
        max_length=16,
        choices=VISIBILIDADE_CHOICES,
        default=VISIBILIDADE_INTERNO,
        db_index=True,
    )
    formalizado_externo = models.BooleanField(default=False, db_index=True)
    formalizado_issue_keys = models.CharField(max_length=512, blank=True, default="")
    # [{"issue_key": "PPLID-1", "comment_id": "12345"}, ...] para exclusão espelhada no Jira.
    formalizado_jira_refs = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="suporte_claro_comentarios_etapa",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "suporte_claro_comentario_etapa"
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"#{self.pk} reg={self.registro_id} ({self.etapa_status})"
