import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class News(models.Model):
    """Notícia/comunicado publicado no container de Comunicação."""

    class Category(models.TextChoices):
        QUALIDADE = "Qualidade", "Qualidade"
        OPERACAO = "Operação", "Operação"
        PLANEJAMENTO = "Planejamento", "Planejamento"
        PROCESSOS = "Processos", "Processos"
        INDICADORES = "Indicadores", "Indicadores"
        RH = "RH", "RH"
        GERAL = "Geral", "Geral"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField("título", max_length=255)
    category = models.CharField(
        "categoria",
        max_length=32,
        choices=Category.choices,
        default=Category.GERAL,
    )
    author = models.CharField("autor", max_length=255)
    summary = models.CharField("resumo", max_length=500)
    content = models.TextField("conteúdo")
    source_url = models.URLField("fonte", max_length=500, blank=True, default="")
    is_critical = models.BooleanField("crítica", default=False)
    published_at = models.DateTimeField("publicação", default=timezone.now)
    active = models.BooleanField("ativo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="news_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="news_updated",
    )

    class Meta:
        db_table = "news"
        verbose_name = "notícia"
        verbose_name_plural = "notícias"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["-published_at"]),
            models.Index(fields=["category"]),
            models.Index(fields=["active"]),
        ]

    def __str__(self):
        return self.title


class NewsImage(models.Model):
    """Imagem anexada a uma notícia."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    news = models.ForeignKey(
        News,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField("imagem", upload_to="news/%Y/%m/")
    sort_order = models.PositiveSmallIntegerField("ordem", default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "news_image"
        verbose_name = "imagem da notícia"
        verbose_name_plural = "imagens da notícia"
        ordering = ["sort_order", "created_at"]

    def __str__(self):
        return f"Imagem {self.sort_order} — {self.news.title}"


class NewsLike(models.Model):
    """Curtida de um usuário autenticado em uma notícia."""

    class Reaction(models.TextChoices):
        HEART = "heart", "Coração"
        ROCKET = "rocket", "Foguete"
        CLAP = "clap", "Palmas"
        PARTY = "party", "Festa"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    news = models.ForeignKey(
        News,
        on_delete=models.CASCADE,
        related_name="likes",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="news_likes",
    )
    reaction = models.CharField(
        max_length=16,
        choices=Reaction.choices,
        default=Reaction.HEART,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "news_like"
        verbose_name = "curtida"
        verbose_name_plural = "curtidas"
        constraints = [
            models.UniqueConstraint(
                fields=["news", "user"],
                name="news_like_unique_user",
            ),
        ]
        indexes = [
            models.Index(fields=["news"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"{self.user} curtiu {self.news.title} ({self.reaction})"


class NewsAcknowledgment(models.Model):
    """Aceite de leitura de notícia crítica por um usuário autenticado."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    news = models.ForeignKey(
        News,
        on_delete=models.CASCADE,
        related_name="acknowledgments",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="news_acknowledgments",
    )
    acknowledged_at = models.DateTimeField("aceite em", auto_now_add=True)

    class Meta:
        db_table = "news_acknowledgment"
        verbose_name = "aceite de notícia"
        verbose_name_plural = "aceites de notícias"
        constraints = [
            models.UniqueConstraint(
                fields=["news", "user"],
                name="news_ack_unique_user",
            ),
        ]
        indexes = [
            models.Index(fields=["news"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"{self.user} aceitou {self.news.title}"


class PortalFeedback(models.Model):
    """Mensagem enviada pelo canal global de ajuda do Onboarding Protection."""

    class Kind(models.TextChoices):
        SUGGESTION = "sugestao", "Sugestão"
        PROBLEM = "problema", "Problema"
        PRAISE = "elogio", "Elogio"
        QUESTION = "duvida", "Dúvida"

    class Status(models.TextChoices):
        NEW = "novo", "Novo"
        IN_REVIEW = "em_analise", "Em análise"
        ANSWERED = "respondido", "Respondido"
        COMPLETED = "concluido", "Concluído"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    protocol = models.CharField(max_length=32, unique=True, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    subject = models.CharField(max_length=120)
    description = models.TextField(max_length=2000)
    attachment = models.ImageField(upload_to="portal_feedback/%Y/%m/", blank=True, null=True)
    allow_contact = models.BooleanField(default=True)
    page_path = models.CharField(max_length=255, blank=True, default="")
    module_id = models.CharField(max_length=120, blank=True, default="")
    admin_note = models.TextField(max_length=2000, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="portal_feedbacks_created",
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portal_feedbacks_handled",
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "portal_feedback"
        verbose_name = "feedback do Onboarding Protection"
        verbose_name_plural = "feedbacks do Onboarding Protection"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.protocol:
            date_part = timezone.localdate().strftime("%Y%m%d")
            self.protocol = f"ONBP-{date_part}-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.protocol} — {self.subject}"
