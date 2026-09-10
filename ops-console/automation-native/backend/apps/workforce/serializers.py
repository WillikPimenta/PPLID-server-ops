from rest_framework import serializers

from .models import Agent, AgentHistory, UserProfile


class AgentBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Agent
        fields = ["id", "full_name", "user_lan_id"]


class AgentCurrentHistorySerializer(serializers.ModelSerializer):
    leader_name = serializers.CharField(
        source="leader.full_name",
        read_only=True,
        allow_null=True,
    )
    facilitator_name = serializers.CharField(
        source="facilitator.full_name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = AgentHistory
        fields = [
            "id",
            "team",
            "job_title",
            "job_activity",
            "location",
            "leader",
            "leader_name",
            "facilitator",
            "facilitator_name",
            "journey",
            "journey_shift",
            "band",
            "pcd",
            "team_sector",
            "job_title_sector",
            "job_title_activity",
            "inss_type",
            "productivity_discount",
            "formalization",
            "external_movement_type",
            "start_date",
            "final_date",
            "active",
        ]
        read_only_fields = fields


class AgentBulkUpdateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    hire_date = serializers.DateField(required=False, allow_null=True)
    time_tracking_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    oracle_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    active = serializers.BooleanField(required=False)


class AgentCycleAgentPatchSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, allow_blank=False, max_length=255)
    user_lan_id = serializers.CharField(required=False, allow_blank=False, max_length=64)
    email = serializers.EmailField(required=False, allow_blank=True)
    hire_date = serializers.DateField(required=False, allow_null=True)
    time_tracking_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    oracle_id = serializers.CharField(required=False, allow_blank=True, max_length=64)
    jira_api_token = serializers.CharField(required=False, allow_blank=True, max_length=512)
    active = serializers.BooleanField(required=False)


class AgentCycleFieldsSerializer(serializers.Serializer):
    team = serializers.CharField(required=False, allow_blank=True, max_length=255)
    job_title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    job_activity = serializers.CharField(required=False, allow_blank=True, max_length=255)
    location = serializers.CharField(required=False, allow_blank=True, max_length=255)
    leader = serializers.UUIDField(required=False, allow_null=True)
    facilitator = serializers.UUIDField(required=False, allow_null=True)
    journey = serializers.CharField(required=False, allow_blank=True, max_length=128)
    band = serializers.CharField(required=False, allow_blank=True, max_length=64)
    pcd = serializers.BooleanField(required=False)
    team_sector = serializers.CharField(required=False, allow_blank=True, max_length=255)
    job_title_sector = serializers.CharField(required=False, allow_blank=True, max_length=255)
    job_title_activity = serializers.CharField(required=False, allow_blank=True, max_length=128)
    inss_type = serializers.CharField(required=False, allow_blank=True, max_length=64)
    # Aceita HH:MM:SS ou horas decimais; conversão no serviço.
    productivity_discount = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=32
    )
    formalization = serializers.CharField(required=False, allow_blank=True, max_length=255)


class AgentCycleChangeSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["update", "terminate"])
    movement_date = serializers.DateField(required=False, allow_null=True)
    external_movement_type = serializers.CharField(
        required=False, allow_blank=True, max_length=128
    )
    preview_id = serializers.UUIDField(required=False, allow_null=True)
    agent = AgentCycleAgentPatchSerializer(required=False)
    cycle = AgentCycleFieldsSerializer(required=False)


class AgentSerializer(serializers.ModelSerializer):
    current_history = serializers.SerializerMethodField()
    jira_api_token = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=512,
        help_text="Personal Access Token do Jira (não é retornado na leitura).",
    )
    jira_api_token_configured = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "id",
            "full_name",
            "user_lan_id",
            "email",
            "hire_date",
            "time_tracking_id",
            "oracle_id",
            "jira_api_token",
            "jira_api_token_configured",
            "active",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "current_history",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "current_history",
            "jira_api_token_configured",
        ]

    def validate_user_lan_id(self, value):
        """Normalize user_lan_id (lowercase and trim) like in import_base_xlsx."""
        if value:
            return str(value).strip().lower()
        return value

    def get_jira_api_token_configured(self, obj) -> bool:
        return bool((getattr(obj, "jira_api_token", None) or "").strip())

    def get_current_history(self, obj):
        if not self.context.get("include_current_history"):
            return None
        record = _resolve_current_history(obj)
        if record is None:
            return None
        return AgentCurrentHistorySerializer(record).data

    def update(self, instance, validated_data):
        token = validated_data.pop("jira_api_token", serializers.empty)
        instance = super().update(instance, validated_data)
        if token is not serializers.empty:
            instance.jira_api_token = (token or "").strip()
            instance.save(update_fields=["jira_api_token", "updated_at"])
        return instance

    def create(self, validated_data):
        token = validated_data.pop("jira_api_token", "")
        instance = super().create(validated_data)
        if token:
            instance.jira_api_token = str(token).strip()
            instance.save(update_fields=["jira_api_token", "updated_at"])
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("include_current_history"):
            data.pop("current_history", None)
        return data


def _resolve_current_history(agent):
    prefetched = getattr(agent, "current_histories", None)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return (
        agent.history.filter(active=True, final_date__isnull=True)
        .select_related("leader", "facilitator")
        .order_by("-start_date")
        .first()
    )


class UserProfileSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source="user.username", read_only=True)
    agent_name = serializers.CharField(source="agent.full_name", read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            "id",
            "user",
            "agent",
            "user_username",
            "agent_name",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class AgentHistorySerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.full_name", read_only=True)
    leader_name = serializers.CharField(
        source="leader.full_name",
        read_only=True,
        allow_null=True,
    )
    facilitator_name = serializers.CharField(
        source="facilitator.full_name",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = AgentHistory
        fields = [
            "id",
            "agent",
            "agent_name",
            "leader",
            "leader_name",
            "facilitator",
            "facilitator_name",
            "location",
            "team",
            "job_title",
            "job_activity",
            "journey",
            "team_sector",
            "job_title_sector",
            "job_title_activity",
            "journey_shift",
            "band",
            "inss_type",
            "external_movement_type",
            "start_date",
            "final_date",
            "productivity_discount",
            "pcd",
            "jira",
            "formalization",
            "active",
            "sharepoint_item_id",
            "created_at",
        ]
        read_only_fields = ["id", "sharepoint_item_id", "created_at"]
