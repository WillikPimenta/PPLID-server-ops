from django.conf import settings
from django.db import models


class SlaBreach(models.Model):
    """Snapshot ao vivo: fila BrFlow com idade ≥ alerta % do SLA vigente."""

    CRIT_BAIXO = "baixo"
    CRIT_MEDIO = "medio"
    CRIT_ALTO = "alto"
    CRIT_CRITICO = "critico"
    CRITICIDADE_CHOICES = (
        (CRIT_BAIXO, "Baixo"),
        (CRIT_MEDIO, "Médio"),
        (CRIT_ALTO, "Alto"),
        (CRIT_CRITICO, "Crítico"),
    )

    cod_cliente = models.PositiveIntegerField()
    nom_cliente = models.CharField(max_length=512)
    nom_workflow = models.CharField(max_length=512, blank=True, default="")
    id_workflow = models.PositiveIntegerField(null=True, blank=True)
    cod_nivel_hierarquico = models.PositiveIntegerField(null=True, blank=True)
    nom_fluxo = models.CharField(max_length=512)
    tipo_analise = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="Manual ou Automática (DimEtapa.manual por nome da etapa).",
    )
    qtd_registro = models.PositiveIntegerField(null=True, blank=True)
    qtd_fila = models.PositiveIntegerField(null=True, blank=True)
    dat_registro_antigo = models.DateTimeField()
    idade_segundos = models.PositiveIntegerField()
    sla_limite_segundos = models.PositiveIntegerField()
    excedente_segundos = models.PositiveIntegerField(default=0)
    pct_sla = models.FloatField(default=0)
    criticidade = models.CharField(
        max_length=16,
        choices=CRITICIDADE_CHOICES,
        default=CRIT_BAIXO,
        db_index=True,
    )
    first_detected_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "controle_sla_breach"
        ordering = ["-pct_sla", "nom_cliente", "nom_workflow"]
        constraints = [
            models.UniqueConstraint(
                fields=["cod_cliente", "id_workflow"],
                name="uq_controle_sla_breach_cliente_workflow",
            ),
        ]
        indexes = [
            models.Index(fields=["cod_cliente", "id_workflow"]),
            models.Index(fields=["last_seen_at"]),
            models.Index(fields=["criticidade", "-pct_sla"]),
            models.Index(fields=["dat_registro_antigo"]),
        ]

    def __str__(self) -> str:
        return f"{self.cod_cliente} {self.nom_workflow or self.nom_fluxo} ({self.criticidade} {self.pct_sla:.1f}%)"


class SlaBreachHistorico(models.Model):
    """
    Histórico append-only de estouro/acompanhamento SLA.
    Não é apagado quando o snapshot ao vivo some; reexecuções do bot
    atualizam a mesma linha (cliente + workflow + data mais antiga).
    """

    CRITICIDADE_CHOICES = SlaBreach.CRITICIDADE_CHOICES

    cod_cliente = models.PositiveIntegerField()
    nom_cliente = models.CharField(max_length=512)
    nom_workflow = models.CharField(max_length=512, blank=True, default="")
    id_workflow = models.PositiveIntegerField(null=True, blank=True)
    cod_nivel_hierarquico = models.PositiveIntegerField(null=True, blank=True)
    nom_fluxo = models.CharField(max_length=512)
    tipo_analise = models.CharField(max_length=16, blank=True, default="")
    qtd_registro = models.PositiveIntegerField(null=True, blank=True)
    qtd_fila = models.PositiveIntegerField(null=True, blank=True)
    dat_registro_antigo = models.DateTimeField()
    idade_segundos = models.PositiveIntegerField()
    sla_limite_segundos = models.PositiveIntegerField()
    excedente_segundos = models.PositiveIntegerField(default=0)
    pct_sla = models.FloatField(default=0)
    criticidade = models.CharField(
        max_length=16,
        choices=CRITICIDADE_CHOICES,
        default=SlaBreach.CRIT_BAIXO,
        db_index=True,
    )
    first_detected_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()

    class Meta:
        db_table = "controle_sla_breach_historico"
        ordering = ["-last_seen_at", "-pct_sla"]
        constraints = [
            models.UniqueConstraint(
                fields=["cod_cliente", "id_workflow", "dat_registro_antigo"],
                name="uq_controle_sla_breach_hist_key",
            ),
        ]
        indexes = [
            models.Index(fields=["cod_cliente", "id_workflow"]),
            models.Index(fields=["dat_registro_antigo"]),
            models.Index(fields=["first_detected_at"]),
            models.Index(fields=["last_seen_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"Hist {self.cod_cliente}/{self.id_workflow} "
            f"{self.dat_registro_antigo:%Y-%m-%d} ({self.pct_sla:.1f}%)"
        )


class EtapaGap(models.Model):
    """
    Etapa (nomFluxo) vista no BrFlow sem cadastro correspondente no Megazord
    (DimEtapa / Projeção SLA).
    """

    SIT_PENDENTE = "pendente"
    SIT_IGNORADA = "ignorada"
    SIT_CADASTRADA = "cadastrada"
    SITUACAO_CHOICES = (
        (SIT_PENDENTE, "Pendente"),
        (SIT_IGNORADA, "Ignorada"),
        (SIT_CADASTRADA, "Cadastrada"),
    )

    cod_cliente = models.PositiveIntegerField()
    nom_cliente = models.CharField(max_length=512)
    nom_workflow = models.CharField(max_length=512, blank=True, default="")
    id_workflow = models.PositiveIntegerField(null=True, blank=True)
    id_cliente_megazord = models.PositiveIntegerField(null=True, blank=True)
    cod_nivel_hierarquico = models.PositiveIntegerField(null=True, blank=True)
    nom_fluxo = models.CharField(max_length=512)
    qtd_fila = models.PositiveIntegerField(null=True, blank=True)
    dat_registro_antigo = models.DateTimeField(null=True, blank=True)
    situacao = models.CharField(
        max_length=16,
        choices=SITUACAO_CHOICES,
        default=SIT_PENDENTE,
        db_index=True,
    )
    ocorrencias = models.PositiveIntegerField(default=1)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    tratado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="controle_sla_gaps_tratados",
    )
    tratado_em = models.DateTimeField(null=True, blank=True)
    projecao_sla_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="ID da primeira regra Projeção SLA vinculada após cadastrar (legado).",
    )
    etapa_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="ID da DimEtapa vinculada após cadastrar (Nova etapa).",
    )

    class Meta:
        db_table = "controle_sla_etapa_gap"
        ordering = ["nom_cliente", "nom_fluxo"]
        constraints = [
            models.UniqueConstraint(
                fields=["cod_cliente", "nom_fluxo"],
                name="uq_controle_sla_etapa_gap_key",
            ),
        ]
        indexes = [
            models.Index(fields=["cod_cliente"]),
            models.Index(fields=["last_seen_at"]),
            models.Index(fields=["situacao", "last_seen_at"]),
        ]

    def __str__(self) -> str:
        return f"Gap {self.cod_cliente}: {self.nom_fluxo} ({self.situacao})"

    @property
    def situacao_label(self) -> str:
        return dict(self.SITUACAO_CHOICES).get(self.situacao, self.situacao)

    @property
    def pode_tratar(self) -> bool:
        return self.situacao == self.SIT_PENDENTE
