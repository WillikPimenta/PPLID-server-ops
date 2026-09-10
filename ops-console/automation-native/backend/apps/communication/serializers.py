from rest_framework import serializers
from django.utils import timezone

from .html_sanitize import sanitize_news_html
from .image_utils import MAX_UPLOAD_BYTES, process_uploaded_image
from .models import News, NewsImage, PortalFeedback

MAX_IMAGES_PER_NEWS = 5


def _author_from_user(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return "Usuário"
    parts = [
        (getattr(user, "first_name", None) or "").strip(),
        (getattr(user, "last_name", None) or "").strip(),
    ]
    full = " ".join(part for part in parts if part)
    if full:
        return full
    username = (getattr(user, "username", None) or "").strip()
    return username or "Usuário"


class NewsLikeResponseSerializer(serializers.Serializer):
    like_count = serializers.IntegerField()
    liked_by_me = serializers.BooleanField()
    my_reaction = serializers.CharField(allow_null=True)
    reaction_counts = serializers.DictField(
        child=serializers.IntegerField(min_value=0),
    )


class NewsAcknowledgeResponseSerializer(serializers.Serializer):
    acknowledged_by_me = serializers.BooleanField()
    acknowledged_at = serializers.DateTimeField()


class NewsImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = NewsImage
        fields = ["id", "url", "sort_order"]
        read_only_fields = fields

    def get_url(self, obj):
        if not obj.image:
            return None
        return obj.image.url


class NewsSerializer(serializers.ModelSerializer):
    images = NewsImageSerializer(many=True, read_only=True)
    like_count = serializers.IntegerField(read_only=True, default=0)
    liked_by_me = serializers.BooleanField(read_only=True, default=False)
    my_reaction = serializers.CharField(read_only=True, allow_null=True, default=None)
    reaction_counts = serializers.SerializerMethodField()
    acknowledged_by_me = serializers.BooleanField(read_only=True, default=False)
    acknowledged_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_by_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = News
        fields = [
            "id",
            "title",
            "category",
            "author",
            "summary",
            "content",
            "source_url",
            "is_critical",
            "published_at",
            "active",
            "created_at",
            "updated_at",
            "created_by_id",
            "images",
            "like_count",
            "liked_by_me",
            "my_reaction",
            "reaction_counts",
            "acknowledged_by_me",
            "acknowledged_at",
        ]
        read_only_fields = [
            "id",
            "author",
            "active",
            "created_at",
            "updated_at",
            "created_by_id",
            "images",
            "like_count",
            "liked_by_me",
            "my_reaction",
            "reaction_counts",
            "acknowledged_by_me",
            "acknowledged_at",
        ]

    def get_reaction_counts(self, obj):
        from .models import NewsLike

        return {
            choice.value: int(getattr(obj, f"reaction_{choice.value}", 0) or 0)
            for choice in NewsLike.Reaction
        }

    def validate_source_url(self, value):
        if value is None:
            return ""
        return value.strip()

    def validate_content(self, value):
        cleaned = sanitize_news_html(value or "")
        if not cleaned.strip():
            raise serializers.ValidationError("Informe o conteúdo.")
        return cleaned

    def validate_is_critical(self, value):
        if value is None:
            return False
        return bool(value)

    def _get_request_data(self):
        request = self.context.get("request")
        if not request:
            return None
        return getattr(request, "data", request.POST)

    def _get_uploaded_files(self):
        request = self.context.get("request")
        if not request:
            return []
        return request.FILES.getlist("images")

    def _get_remove_image_ids(self):
        data = self._get_request_data()
        if data is None:
            return []
        if hasattr(data, "getlist"):
            return data.getlist("remove_image_ids")
        raw = data.get("remove_image_ids")
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        return [raw]

    def _validate_image_count(self, news=None):
        uploaded = self._get_uploaded_files()
        remove_ids = set(self._get_remove_image_ids())
        existing_count = 0
        if news:
            existing_count = news.images.exclude(id__in=remove_ids).count()
        total = existing_count + len(uploaded)
        if total > MAX_IMAGES_PER_NEWS:
            raise serializers.ValidationError(
                {
                    "images": (
                        f"Máximo de {MAX_IMAGES_PER_NEWS} imagens por notícia."
                    )
                }
            )
        for uploaded_file in uploaded:
            if uploaded_file.size > MAX_UPLOAD_BYTES:
                raise serializers.ValidationError(
                    {
                        "images": (
                            f"Cada imagem deve ter no máximo "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
                        )
                    }
                )

    def validate(self, attrs):
        news = self.instance
        self._validate_image_count(news)
        return attrs

    def _sync_images(self, news):
        remove_ids = self._get_remove_image_ids()
        if remove_ids:
            for image in news.images.filter(id__in=remove_ids):
                if image.image:
                    image.image.delete(save=False)
                image.delete()

        uploaded_files = self._get_uploaded_files()
        if not uploaded_files:
            return

        next_order = (
            news.images.order_by("-sort_order")
            .values_list("sort_order", flat=True)
            .first()
        )
        next_order = (next_order + 1) if next_order is not None else 0

        for uploaded_file in uploaded_files:
            try:
                processed = process_uploaded_image(uploaded_file)
            except Exception as exc:
                from django.core.exceptions import ValidationError as DjangoValidationError

                if isinstance(exc, DjangoValidationError):
                    messages = getattr(exc, "messages", None) or [str(exc)]
                    raise serializers.ValidationError({"images": list(messages)})
                raise serializers.ValidationError({"images": str(exc)})

            NewsImage.objects.create(
                news=news,
                image=processed,
                sort_order=next_order,
            )
            next_order += 1

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        validated_data["author"] = _author_from_user(user)
        # Permite agendar até o minuto: se não vier published_at, usa agora.
        validated_data.setdefault("published_at", timezone.now())
        news = super().create(validated_data)
        self._sync_images(news)
        return News.objects.prefetch_related("images").get(pk=news.pk)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        validated_data["author"] = _author_from_user(user)
        news = super().update(instance, validated_data)
        self._sync_images(news)
        return News.objects.prefetch_related("images").get(pk=news.pk)


class PortalFeedbackCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PortalFeedback
        fields = [
            "id", "protocol", "kind", "subject", "description", "attachment",
            "allow_contact", "page_path", "module_id", "created_at",
        ]
        read_only_fields = ["id", "protocol", "created_at"]

    def validate_attachment(self, uploaded_file):
        if not uploaded_file:
            return uploaded_file
        try:
            return process_uploaded_image(uploaded_file)
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(exc, DjangoValidationError):
                messages = getattr(exc, "messages", None) or [str(exc)]
                raise serializers.ValidationError(messages)
            raise serializers.ValidationError(str(exc))


class PortalFeedbackSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    attachment_url = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    created_by_username = serializers.SerializerMethodField()
    created_by_email = serializers.SerializerMethodField()
    handled_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PortalFeedback
        fields = [
            "id", "protocol", "kind", "kind_label", "status", "status_label",
            "subject", "description", "attachment_url", "allow_contact", "page_path",
            "module_id", "admin_note", "created_by_name", "created_by_username",
            "created_by_email", "handled_by_name", "handled_at", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "protocol", "kind", "kind_label", "status_label", "subject",
            "description", "attachment_url", "allow_contact", "page_path", "module_id",
            "created_by_name", "created_by_username", "created_by_email", "handled_by_name",
            "handled_at", "created_at", "updated_at",
        ]

    def get_attachment_url(self, obj):
        if not obj.attachment:
            return None
        request = self.context.get("request")
        url = obj.attachment.url
        return request.build_absolute_uri(url) if request else url

    @staticmethod
    def _display_name(user):
        if not user:
            return None
        return user.get_full_name().strip() or user.username

    def get_created_by_name(self, obj):
        return self._display_name(obj.created_by)

    def get_created_by_username(self, obj):
        return obj.created_by.username if obj.created_by else None

    def get_created_by_email(self, obj):
        return obj.created_by.email if obj.created_by else None

    def get_handled_by_name(self, obj):
        return self._display_name(obj.handled_by)
