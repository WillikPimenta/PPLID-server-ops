"""Serializers da geração automática de escala mensal."""

from __future__ import annotations

from rest_framework import serializers

from apps.escala_flex.models import (
    EscalaGenerationConflict,
    EscalaGenerationEntry,
    EscalaGenerationRun,
)


class EscalaGenerationEntrySerializer(serializers.ModelSerializer):
    agent_lan_id = serializers.CharField(source="agent.user_lan_id", read_only=True)
    agent_name = serializers.CharField(source="agent.full_name", read_only=True)
    leader_lan_id = serializers.CharField(
        source="leader.user_lan_id", read_only=True, default=""
    )
    leader_name = serializers.CharField(
        source="leader.full_name", read_only=True, default=""
    )
    activity_name = serializers.CharField(
        source="job_activity.name", read_only=True, default=""
    )
    location_name = serializers.SerializerMethodField()

    class Meta:
        model = EscalaGenerationEntry
        fields = [
            "id",
            "agent",
            "agent_lan_id",
            "agent_name",
            "date",
            "leader",
            "leader_lan_id",
            "leader_name",
            "job_activity",
            "activity_name",
            "location",
            "location_name",
            "team",
            "sector",
            "schedule",
            "day_value",
            "source",
            "has_conflict",
            "adjusted_manually",
            "observation",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "agent",
            "date",
            "source",
            "has_conflict",
            "adjusted_manually",
            "updated_at",
        ]

    def get_location_name(self, obj) -> str:
        if obj.location_id:
            return str(obj.location)
        return ""


class EscalaGenerationEntryPatchSerializer(serializers.Serializer):
    day_value = serializers.CharField(required=False, allow_blank=True, max_length=32)
    schedule = serializers.CharField(required=False, allow_blank=True, max_length=32)
    observation = serializers.CharField(required=False, allow_blank=True)
    team = serializers.CharField(required=False, allow_blank=True, max_length=255)
    sector = serializers.CharField(required=False, allow_blank=True, max_length=255)


class EscalaGenerationConflictSerializer(serializers.ModelSerializer):
    agent_lan_id = serializers.CharField(
        source="agent.user_lan_id", read_only=True, default=""
    )

    class Meta:
        model = EscalaGenerationConflict
        fields = [
            "id",
            "entry",
            "agent",
            "agent_lan_id",
            "date",
            "activity_name",
            "conflict_type",
            "severity",
            "message",
            "details",
            "resolved",
            "resolution_note",
            "resolved_at",
            "created_at",
        ]


class EscalaGenerationConflictResolveSerializer(serializers.Serializer):
    resolution_note = serializers.CharField(required=True, allow_blank=False)


class EscalaGenerationRunSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True, default=""
    )
    published_by_username = serializers.CharField(
        source="published_by.username", read_only=True, default=""
    )
    blocking_unresolved = serializers.SerializerMethodField()

    class Meta:
        model = EscalaGenerationRun
        fields = [
            "id",
            "reference_month",
            "status",
            "configuration",
            "summary",
            "agents_considered",
            "entries_generated",
            "conflicts_count",
            "blocking_unresolved",
            "failure_detail",
            "created_by",
            "created_by_username",
            "published_by",
            "published_by_username",
            "created_at",
            "finished_at",
            "published_at",
        ]

    def get_blocking_unresolved(self, obj) -> int:
        return obj.conflicts.filter(
            severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
            resolved=False,
        ).count()


class EscalaGenerationRunDetailSerializer(EscalaGenerationRunSerializer):
    entries = EscalaGenerationEntrySerializer(many=True, read_only=True)
    conflicts = EscalaGenerationConflictSerializer(many=True, read_only=True)

    class Meta(EscalaGenerationRunSerializer.Meta):
        fields = EscalaGenerationRunSerializer.Meta.fields + [
            "entries",
            "conflicts",
        ]


class EscalaGenerationPreviewSerializer(serializers.Serializer):
    reference_month = serializers.DateField()
    configuration = serializers.DictField(required=False)


class EscalaGenerationPublishResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    skipped = serializers.IntegerField()
    schedules_synced = serializers.IntegerField()
    dates_rebuilt = serializers.ListField(child=serializers.CharField())
