from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers
from django.utils import timezone

from apps.auditoria.models import AuditoriaCatalogItem
from apps.auditoria.services.catalog_items import seed_catalog_defaults
from apps.access.registry import QUAL_CAPACITACAO_SUPORTE_VIEW
from apps.communication.html_sanitize import sanitize_news_html
from apps.dimensoes_processos.models import ProjecaoSla
from apps.workforce.models import Agent

from .capabilities import compute_capabilities
from .models import OperationalSupportEvent, OperationalSupportNotice, OperationalSupportRequest

User = get_user_model()


def _user_label(user) -> str | None:
    if not user:
        return None
    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return name or getattr(user, "username", None)


class OperationalSupportEventSerializer(serializers.ModelSerializer):
    author_username = serializers.SerializerMethodField()
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = OperationalSupportEvent
        fields = (
            "id",
            "sequence",
            "event_version",
            "event_type",
            "author",
            "author_username",
            "author_name",
            "actor_username",
            "actor_name",
            "actor_roles",
            "from_status",
            "to_status",
            "note",
            "snapshot",
            "created_at",
        )

    def get_author_username(self, obj) -> str | None:
        return obj.actor_username or (obj.author.username if obj.author_id else None)

    def get_author_name(self, obj) -> str | None:
        return obj.actor_name or _user_label(obj.author)


class OperationalSupportNoticeSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = OperationalSupportNotice
        fields = (
            "id",
            "title",
            "message",
            "author_name",
            "active",
            "status",
            "inactive_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "author_name", "status", "inactive_at", "created_at", "updated_at")

    def get_author_name(self, obj) -> str:
        return _user_label(obj.created_by) or "Usuário"

    def get_status(self, obj) -> str:
        return "active" if obj.active else "inactive"

    def validate_title(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Informe o título do recado.")
        return value

    def validate_message(self, value: str) -> str:
        cleaned = sanitize_news_html(value or "")
        if not cleaned.strip():
            raise serializers.ValidationError("Informe o recado.")
        return cleaned


class OperationalSupportNoticeUpdateSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = OperationalSupportNotice
        fields = (
            "id",
            "title",
            "message",
            "author_name",
            "active",
            "status",
            "inactive_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "author_name", "status", "inactive_at", "created_at", "updated_at")

    def get_author_name(self, obj) -> str:
        return _user_label(obj.created_by) or "Usuário"

    def get_status(self, obj) -> str:
        return "active" if obj.active else "inactive"

    def validate_title(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Informe o título do recado.")
        return value

    def validate_message(self, value: str) -> str:
        if value is None:
            return value
        cleaned = sanitize_news_html(value or "")
        if not cleaned.strip():
            raise serializers.ValidationError("Informe o recado.")
        return cleaned

    def update(self, instance, validated_data):
        active = validated_data.pop("active", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if active is not None:
            instance.active = active
            if active:
                instance.inactive_at = None
            elif instance.inactive_at is None:
                instance.inactive_at = timezone.now()
        instance.save()
        return instance


class OperationalSupportRequestSerializer(serializers.ModelSerializer):
    agent_lan_id = serializers.CharField(source="agent.user_lan_id", read_only=True)
    agent_name = serializers.CharField(source="agent.full_name", read_only=True)
    agent_leader_name = serializers.SerializerMethodField()
    agent_office = serializers.SerializerMethodField()
    requester_username = serializers.CharField(source="requester.username", read_only=True)
    requester_name = serializers.SerializerMethodField()
    leader_decider_username = serializers.SerializerMethodField()
    leader_decider_name = serializers.SerializerMethodField()
    assignee_username = serializers.SerializerMethodField()
    assignee_name = serializers.SerializerMethodField()
    presencial_support_user = serializers.UUIDField(
        source="presencial_support_user_id",
        read_only=True,
        allow_null=True,
    )
    presencial_support_user_name = serializers.SerializerMethodField()
    answered_by_username = serializers.SerializerMethodField()
    answered_by_name = serializers.SerializerMethodField()
    cancelled_by_username = serializers.SerializerMethodField()
    cancelled_by_name = serializers.SerializerMethodField()
    events = OperationalSupportEventSerializer(many=True, read_only=True)
    can_approve_leader = serializers.SerializerMethodField()
    can_reject_leader = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    can_assign = serializers.SerializerMethodField()
    can_answer = serializers.SerializerMethodField()
    queue_position = serializers.SerializerMethodField()

    class Meta:
        model = OperationalSupportRequest
        fields = (
            "id",
            "agent",
            "agent_lan_id",
            "agent_name",
            "agent_leader_name",
            "agent_office",
            "protocol",
            "workflow",
            "client",
            "requester",
            "requester_username",
            "requester_name",
            "requester_type",
            "operation_origin",
            "request_type",
            "subject",
            "category",
            "description",
            "reference",
            "status",
            "leader_decider",
            "leader_decider_username",
            "leader_decider_name",
            "leader_decision",
            "leader_justification",
            "leader_decided_at",
            "auto_approved",
            "assignee",
            "assignee_username",
            "assignee_name",
            "assigned_at",
            "presencial_support_user",
            "presencial_support_user_name",
            "answer",
            "answer_option",
            "difficulty_level",
            "document_uf",
            "document_type",
            "answered_by",
            "answered_by_username",
            "answered_by_name",
            "answered_at",
            "cancelled_by",
            "cancelled_by_username",
            "cancelled_by_name",
            "cancel_reason",
            "cancelled_at",
            "created_at",
            "updated_at",
            "queue_position",
            "events",
            "can_approve_leader",
            "can_reject_leader",
            "can_cancel",
            "can_assign",
            "can_answer",
        )
        read_only_fields = fields

    def _caps(self, obj) -> dict[str, bool]:
        cache = self.context.setdefault("_caps_cache", {})
        key = str(obj.pk)
        if key not in cache:
            user = self.context.get("request").user if self.context.get("request") else None
            cache[key] = compute_capabilities(user, obj) if user else {
                "can_approve_leader": False,
                "can_reject_leader": False,
                "can_cancel": False,
                "can_assign": False,
                "can_answer": False,
            }
        return cache[key]

    def get_requester_name(self, obj) -> str | None:
        return _user_label(obj.requester)

    def _agent_current_history(self, obj):
        cache = self.context.setdefault("_agent_history_cache", {})
        key = str(obj.agent_id)
        if key not in cache:
            prefetched = getattr(obj.agent, "current_histories", None)
            if prefetched is not None:
                cache[key] = prefetched[0] if prefetched else None
            else:
                cache[key] = (
                    obj.agent.history.filter(active=True, final_date__isnull=True)
                    .select_related("leader")
                    .order_by("-start_date")
                    .first()
                )
        return cache[key]

    def get_agent_leader_name(self, obj) -> str:
        history = self._agent_current_history(obj)
        if not history or not history.leader_id:
            return ""
        return (history.leader.full_name or "").strip()

    def get_agent_office(self, obj) -> str:
        history = self._agent_current_history(obj)
        return (history.location or "").strip() if history else ""

    def get_leader_decider_username(self, obj) -> str | None:
        return obj.leader_decider.username if obj.leader_decider_id else None

    def get_leader_decider_name(self, obj) -> str | None:
        return _user_label(obj.leader_decider)

    def get_assignee_username(self, obj) -> str | None:
        return obj.assignee.username if obj.assignee_id else None

    def get_assignee_name(self, obj) -> str | None:
        return _user_label(obj.assignee)

    def get_presencial_support_user_name(self, obj) -> str | None:
        return _user_label(obj.presencial_support_user)

    def get_answered_by_username(self, obj) -> str | None:
        return obj.answered_by.username if obj.answered_by_id else None

    def get_answered_by_name(self, obj) -> str | None:
        return _user_label(obj.answered_by)

    def get_cancelled_by_username(self, obj) -> str | None:
        return obj.cancelled_by.username if obj.cancelled_by_id else None

    def get_cancelled_by_name(self, obj) -> str | None:
        return _user_label(obj.cancelled_by)

    def get_can_approve_leader(self, obj) -> bool:
        return self._caps(obj)["can_approve_leader"]

    def get_can_reject_leader(self, obj) -> bool:
        return self._caps(obj)["can_reject_leader"]

    def get_can_cancel(self, obj) -> bool:
        return self._caps(obj)["can_cancel"]

    def get_can_assign(self, obj) -> bool:
        return self._caps(obj)["can_assign"]

    def get_can_answer(self, obj) -> bool:
        return self._caps(obj)["can_answer"]

    def get_queue_position(self, obj) -> int | None:
        positions = self.context.get("queue_positions") or {}
        return positions.get(str(obj.pk))


class OperationalSupportRequestListSerializer(OperationalSupportRequestSerializer):
    class Meta(OperationalSupportRequestSerializer.Meta):
        fields = tuple(f for f in OperationalSupportRequestSerializer.Meta.fields if f != "events")


class OperationalSupportCreateSerializer(serializers.Serializer):
    agent_id = serializers.UUIDField(required=False)
    agent_lan_id = serializers.CharField(required=False, allow_blank=True)
    protocol = serializers.CharField(max_length=100)
    operation_origin = serializers.ChoiceField(
        choices=OperationalSupportRequest.OperationOrigin.choices,
        default=OperationalSupportRequest.OperationOrigin.FRAUD,
    )
    workflow_client_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
    )
    question = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
    )
    description = serializers.CharField()
    request_type = serializers.ChoiceField(
        choices=OperationalSupportRequest.RequestType.choices,
        default=OperationalSupportRequest.RequestType.ONLINE,
    )
    presencial_support_user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.access.services.agents_with_permission import users_with_permission

        allowed_ids = [
            row["id"]
            for row in users_with_permission(QUAL_CAPACITACAO_SUPORTE_VIEW)
            if row.get("id")
        ]
        self.fields["presencial_support_user_id"].queryset = User.objects.filter(
            pk__in=allowed_ids,
            is_active=True,
        )

    def validate(self, attrs):
        protocol = (attrs.get("protocol") or "").strip()
        if not protocol:
            raise serializers.ValidationError({"protocol": "Informe o protocolo."})
        attrs["protocol"] = protocol
        attrs["category"] = "outros"
        attrs["reference"] = ""

        if attrs["operation_origin"] == OperationalSupportRequest.OperationOrigin.CONFER:
            attrs["subject"] = OperationalSupportRequest.CONFER_SUBJECT
            attrs["workflow"] = OperationalSupportRequest.CONFER_WORKFLOW
            attrs["client"] = OperationalSupportRequest.CONFER_CLIENT
            attrs["workflow_id"] = None
            attrs["cliente_id"] = None
        else:
            seed_catalog_defaults()
            question = (attrs.get("question") or "").strip()
            question_item = AuditoriaCatalogItem.objects.filter(
                catalog=AuditoriaCatalogItem.CATALOG_DUVIDA_SUPORTE_OPERACIONAL,
                active=True,
                value__iexact=question,
            ).first()
            if not question_item:
                raise serializers.ValidationError(
                    {"question": "Selecione uma dúvida válida."}
                )

            workflow_client_key = (attrs.get("workflow_client_key") or "").strip()
            try:
                workflow_id_text, client_id_text = workflow_client_key.split(":", 1)
                workflow_id = int(workflow_id_text)
                client_id = int(client_id_text)
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    {"workflow_client_key": "Selecione um Workflow válido."}
                )

            projection = (
                ProjecaoSla.objects.filter(
                    data_fim__isnull=True,
                    workflow_id=workflow_id,
                    cliente_id=client_id,
                    workflow__ind_considerar=True,
                )
                .select_related("workflow", "cliente")
                .order_by("-data_inicio")
                .first()
            )
            if not projection:
                raise serializers.ValidationError(
                    {"workflow_client_key": "Workflow sem Projeção SLA vigente."}
                )

            attrs["subject"] = question_item.value
            attrs["workflow"] = (projection.workflow.nome or "").strip()
            attrs["client"] = (projection.cliente.nome or "").strip()
            attrs["workflow_id"] = workflow_id
            attrs["cliente_id"] = client_id

        agent_id = attrs.get("agent_id")
        agent_lan_id = (attrs.get("agent_lan_id") or "").strip()
        agent = None
        if agent_id:
            agent = Agent.objects.filter(pk=agent_id).first()
        elif agent_lan_id:
            agent = Agent.objects.filter(user_lan_id__iexact=agent_lan_id).first()
        if not agent:
            raise serializers.ValidationError({"agent": "Agente não encontrado."})
        attrs["agent"] = agent

        request_type = (attrs.get("request_type") or OperationalSupportRequest.RequestType.ONLINE).strip().lower()
        presencial_support_user = attrs.pop("presencial_support_user_id", None)
        if request_type == OperationalSupportRequest.RequestType.PRESENCIAL:
            if not presencial_support_user:
                raise serializers.ValidationError(
                    {"presencial_support_user_id": "Selecione o agente de suporte."}
                )
            attrs["presencial_support_user"] = presencial_support_user
        elif presencial_support_user:
            raise serializers.ValidationError(
                {"presencial_support_user_id": "Disponível apenas para solicitações presenciais."}
            )
        return attrs


class LeaderDecisionSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    justification = serializers.CharField(required=False, allow_blank=True, default="")


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField()


class AnswerSerializer(serializers.Serializer):
    answer = serializers.CharField()
    answer_option = serializers.CharField(max_length=200)
    difficulty_level = serializers.ChoiceField(
        choices=OperationalSupportRequest.DifficultyLevel.choices,
    )
    document_uf = serializers.CharField(max_length=32)
    document_type = serializers.CharField(max_length=120)
