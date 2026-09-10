from django.db.models import Case, CharField, Count, DateTimeField, Exists, IntegerField, OuterRef, Q, Subquery, Value, When
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.access.constants import ROLE_ADM_PORTAL
from apps.access.resolve import resolve_user_access
from apps.access.permission_classes import portal_perm
from apps.access.view_mixins import NewsPortalPermissionMixin
from apps.access import registry as R
from .models import News, NewsAcknowledgment, NewsLike, PortalFeedback
from .serializers import (
    NewsAcknowledgeResponseSerializer,
    NewsLikeResponseSerializer,
    NewsSerializer,
    PortalFeedbackCreateSerializer,
    PortalFeedbackSerializer,
)


def _empty_reaction_counts():
    return {choice.value: 0 for choice in NewsLike.Reaction}


def _reaction_counts_for_news(news_id):
    counts = _empty_reaction_counts()
    rows = (
        NewsLike.objects.filter(news_id=news_id)
        .values("reaction")
        .annotate(total=Count("id"))
    )
    for row in rows:
        key = row["reaction"]
        if key in counts:
            counts[key] = row["total"]
    return counts


def _reaction_count_annotations():
    return {
        f"reaction_{choice.value}": Count(
            "likes",
            filter=Q(likes__reaction=choice.value),
            distinct=True,
        )
        for choice in NewsLike.Reaction
    }


class NewsViewSet(NewsPortalPermissionMixin, viewsets.ModelViewSet):
    serializer_class = NewsSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        "category": ["exact"],
        "published_at": ["gte", "lte", "exact"],
    }
    search_fields = ["title", "category", "author", "summary", "content"]
    ordering_fields = ["published_at", "created_at", "title"]
    ordering = ["-published_at"]

    def get_queryset(self):
        user = self.request.user
        liked_subquery = NewsLike.objects.filter(
            news_id=OuterRef("pk"),
            user=user,
        )
        my_reaction_subquery = NewsLike.objects.filter(
            news_id=OuterRef("pk"),
            user=user,
        ).values("reaction")[:1]
        ack_subquery = NewsAcknowledgment.objects.filter(
            news_id=OuterRef("pk"),
            user=user,
        )
        ack_at_subquery = NewsAcknowledgment.objects.filter(
            news_id=OuterRef("pk"),
            user=user,
        ).values("acknowledged_at")[:1]
        reaction_annots = _reaction_count_annotations()

        queryset = News.objects.filter(active=True).prefetch_related("images")
        # Feed do agente: só notícias já publicadas. Gestores veem também agendadas.
        from apps.access.resolve import user_has_any_permission

        if not (
            user.is_authenticated
            and user_has_any_permission(user, R.COMUNICACAO_NOTICIAS_MANAGE)
        ):
            queryset = queryset.filter(published_at__lte=timezone.now())

        if user.is_authenticated:
            queryset = queryset.annotate(
                like_count=Count("likes", distinct=True),
                liked_by_me=Exists(liked_subquery),
                my_reaction=Subquery(my_reaction_subquery, output_field=CharField()),
                acknowledged_by_me=Exists(ack_subquery),
                acknowledged_at=Subquery(ack_at_subquery, output_field=DateTimeField()),
                **reaction_annots,
            ).annotate(
                sort_priority=Case(
                    When(is_critical=True, acknowledged_by_me=False, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
            )
        else:
            queryset = queryset.annotate(
                like_count=Count("likes", distinct=True),
                liked_by_me=Exists(NewsLike.objects.none()),
                my_reaction=Value(None, output_field=CharField(max_length=16)),
                acknowledged_by_me=Exists(NewsAcknowledgment.objects.none()),
                acknowledged_at=Value(None, output_field=DateTimeField()),
                sort_priority=Value(1, output_field=IntegerField()),
                **reaction_annots,
            )

        return queryset.order_by("sort_priority", "-published_at", "-created_at")

    def perform_create(self, serializer):
        if self.request.user.is_authenticated:
            serializer.save(created_by=self.request.user, updated_by=self.request.user)
        else:
            serializer.save()

    def perform_update(self, serializer):
        if self.request.user.is_authenticated:
            serializer.save(updated_by=self.request.user)
        else:
            serializer.save()

    def filter_queryset(self, queryset):
        qs = super().filter_queryset(queryset)
        ordering = self.request.query_params.get("ordering", "-published_at")
        if ordering in ("published_at", "-published_at"):
            return qs.order_by("sort_priority", ordering, "-created_at")
        return qs.order_by("sort_priority", "-published_at", "-created_at")

    @action(detail=True, methods=["post"])
    def like(self, request, pk=None):
        news = self.get_object()
        user = request.user
        reaction = request.data.get("reaction")
        valid_reactions = {choice.value for choice in NewsLike.Reaction}

        existing = NewsLike.objects.filter(news=news, user=user).first()
        my_reaction = None

        if existing:
            if reaction in valid_reactions and reaction != existing.reaction:
                existing.reaction = reaction
                existing.save(update_fields=["reaction"])
                liked = True
                my_reaction = reaction
            else:
                existing.delete()
                liked = False
        else:
            if reaction not in valid_reactions:
                return Response(
                    {"detail": "Informe uma reação válida: heart, rocket, clap ou party."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            NewsLike.objects.create(news=news, user=user, reaction=reaction)
            liked = True
            my_reaction = reaction

        like_count = NewsLike.objects.filter(news=news).count()
        payload = {
            "like_count": like_count,
            "liked_by_me": liked,
            "my_reaction": my_reaction,
            "reaction_counts": _reaction_counts_for_news(news.id),
        }
        return Response(NewsLikeResponseSerializer(payload).data)

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        news = self.get_object()
        if not news.is_critical:
            return Response(
                {"detail": "Esta notícia não requer aceite."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        existing = NewsAcknowledgment.objects.filter(news=news, user=user).first()
        if not existing:
            existing = NewsAcknowledgment.objects.create(news=news, user=user)

        payload = {
            "acknowledged_by_me": True,
            "acknowledged_at": existing.acknowledged_at,
        }
        return Response(NewsAcknowledgeResponseSerializer(payload).data)

    @action(detail=False, methods=["get"], url_path="scheduled")
    def scheduled(self, request):
        """Lista notícias agendadas (publicação futura) do criador — ou todas se admin."""
        now = timezone.now()
        # Gestores já veem futuras no get_queryset; reforça o filtro explícito.
        qs = (
            News.objects.filter(active=True, published_at__gt=now)
            .prefetch_related("images")
            .order_by("published_at", "title")
        )

        access = resolve_user_access(request.user)
        roles = set(access.get("roles") or [])
        is_admin = access.get("bypass") or ROLE_ADM_PORTAL in roles
        if not is_admin:
            qs = qs.filter(created_by=request.user)

        # Anotações mínimas para o NewsSerializer (sem prioridade de ciência).
        user = request.user
        liked_subquery = NewsLike.objects.filter(news_id=OuterRef("pk"), user=user)
        my_reaction_subquery = NewsLike.objects.filter(
            news_id=OuterRef("pk"), user=user
        ).values("reaction")[:1]
        ack_subquery = NewsAcknowledgment.objects.filter(news_id=OuterRef("pk"), user=user)
        ack_at_subquery = NewsAcknowledgment.objects.filter(
            news_id=OuterRef("pk"), user=user
        ).values("acknowledged_at")[:1]
        reaction_annots = _reaction_count_annotations()
        qs = qs.annotate(
            like_count=Count("likes", distinct=True),
            liked_by_me=Exists(liked_subquery),
            my_reaction=Subquery(my_reaction_subquery, output_field=CharField()),
            acknowledged_by_me=Exists(ack_subquery),
            acknowledged_at=Subquery(ack_at_subquery, output_field=DateTimeField()),
            **reaction_annots,
        )

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = NewsSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)
        serializer = NewsSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)


class PortalFeedbackViewSet(viewsets.ModelViewSet):
    queryset = PortalFeedback.objects.select_related("created_by", "handled_by")
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {"kind": ["exact"], "status": ["exact"]}
    search_fields = [
        "protocol", "subject", "description", "page_path",
        "created_by__username", "created_by__first_name", "created_by__last_name",
    ]
    ordering_fields = ["created_at", "updated_at", "status", "kind"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        permissions = [IsAuthenticated()]
        if self.action != "create":
            permissions.append(portal_perm(R.ADM_CONFIG_HUB)())
        return permissions

    def get_serializer_class(self):
        if self.action == "create":
            return PortalFeedbackCreateSerializer
        return PortalFeedbackSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(handled_by=self.request.user, handled_at=timezone.now())

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        counts = PortalFeedback.objects.aggregate(
            total=Count("id"),
            new=Count("id", filter=Q(status=PortalFeedback.Status.NEW)),
            in_review=Count("id", filter=Q(status=PortalFeedback.Status.IN_REVIEW)),
            answered=Count("id", filter=Q(status=PortalFeedback.Status.ANSWERED)),
            completed=Count("id", filter=Q(status=PortalFeedback.Status.COMPLETED)),
        )
        return Response(counts)
