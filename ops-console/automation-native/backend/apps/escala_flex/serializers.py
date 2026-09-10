from django.utils import timezone
from rest_framework import serializers

from .models import (
    AgentStatus,
    AbsenceType,
    BreakTime,
    CurrentActivity,
    HierarchicalLevel,
    Holiday,
    JobActivity,
    Location,
    NotifyEntry,
    OccurrenceType,
    OperationalOccurrence,
    OperationalOccurrenceExtension,
    RequestType,
    ScheduleRequest,
    ScheduleToday,
    StatusEvent,
    StatusType,
)
from .services.permissions import get_active_history
from .services.absence_types import normalize_absence_code
from .services.overtime import STANDARD_WORK_HOURS, hours_from_work_schedule
from .services.schedule_utils import is_time_range_schedule, normalize_time_schedule
from .services.swap_request_workflow import validate_swap_date
from .services.occurrence_workflow import (
    get_pending_occurrence_extension,
    is_operational_occurrence_in_progress,
    resolve_operational_occurrence_workflow_status,
)


from .services.overtime import compute_overtime_flag


class StatusTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatusType
        fields = [
            "id",
            "name",
            "color",
            "active",
            "logged_in",
            "observation",
            "deducts_logged_time",
            "default_time_seconds",
            "deducts_production",
        ]


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "city_name", "state_name", "display_name", "active"]


class JobActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = JobActivity
        fields = ["id", "name", "active"]


class HierarchicalLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = HierarchicalLevel
        fields = ["id", "name", "active"]


class RequestTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequestType
        fields = ["id", "name", "icon", "active"]


class ScheduleTodaySerializer(serializers.ModelSerializer):
    status_name = serializers.CharField(source="status.name", read_only=True)
    status_color = serializers.CharField(source="status.color", read_only=True)
    user_lan_id = serializers.CharField(source="agent.user_lan_id", read_only=True)
    leader_name = serializers.CharField(source="leader.full_name", read_only=True)
    hierarchical_level_name = serializers.CharField(
        source="hierarchical_level.name",
        read_only=True,
        default="",
    )
    overtime = serializers.SerializerMethodField()

    def get_overtime(self, obj: ScheduleToday) -> bool:
        if obj.overtime:
            return True
        return compute_overtime_flag(obj.work_schedule or "")

    class Meta:
        model = ScheduleToday
        fields = [
            "id",
            "user_lan_id",
            "full_name",
            "date",
            "work_schedule",
            "working_hour",
            "overtime",
            "status",
            "status_name",
            "status_color",
            "current_activity",
            "hierarchical_level",
            "hierarchical_level_name",
            "start_of_work",
            "week_break",
            "weekend_break",
            "is_previous_night_shift",
            "leader_name",
            "leader_lan_id",
            "location",
            "job_activity",
            "job_title",
            "journey",
            "sector",
            "blocked",
            "blocked_by",
            "blocked_description",
            "blocked_at",
            "last_change",
            "observation",
        ]
        read_only_fields = ["id", "date"]


class PanelScheduleSerializer(serializers.Serializer):
    """Linha do Painel (headcount + escala + ScheduleToday opcional)."""

    id = serializers.CharField()
    user_lan_id = serializers.CharField()
    full_name = serializers.CharField()
    date = serializers.DateField()
    work_schedule = serializers.CharField(allow_blank=True)
    working_hour = serializers.DecimalField(
        max_digits=4, decimal_places=1, allow_null=True, required=False
    )
    overtime = serializers.BooleanField()
    status = serializers.IntegerField(allow_null=True)
    status_name = serializers.CharField(allow_blank=True)
    status_color = serializers.CharField(allow_blank=True)
    current_activity = serializers.CharField(allow_blank=True)
    hierarchical_level = serializers.UUIDField(allow_null=True, required=False)
    hierarchical_level_name = serializers.CharField(allow_blank=True)
    start_of_work = serializers.DateTimeField(allow_null=True, required=False)
    week_break = serializers.CharField(allow_blank=True)
    weekend_break = serializers.CharField(allow_blank=True)
    is_previous_night_shift = serializers.BooleanField()
    leader_name = serializers.CharField(allow_blank=True)
    leader_lan_id = serializers.CharField(allow_blank=True)
    location = serializers.CharField(allow_blank=True)
    job_activity = serializers.CharField(allow_blank=True)
    job_title = serializers.CharField(allow_blank=True)
    journey = serializers.CharField(allow_blank=True)
    sector = serializers.CharField(allow_blank=True)
    blocked = serializers.BooleanField()
    blocked_by = serializers.CharField(allow_blank=True)
    blocked_description = serializers.CharField(allow_blank=True)
    blocked_at = serializers.DateTimeField(allow_null=True, required=False)
    last_change = serializers.DateTimeField(allow_null=True, required=False)
    has_time_schedule = serializers.BooleanField()
    schedule_display = serializers.CharField(allow_blank=True)


class ScheduleTodayUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleToday
        fields = [
            "status",
            "current_activity",
            "hierarchical_level",
            "start_of_work",
            "week_break",
            "weekend_break",
            "overtime",
            "blocked",
            "blocked_by",
            "blocked_description",
            "blocked_at",
            "observation",
        ]


class BlockedBatchSerializer(serializers.Serializer):
    items = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
    )


class StatusEventSerializer(serializers.ModelSerializer):
    user_lan_id = serializers.CharField(source="agent.user_lan_id", read_only=True)
    agent_name = serializers.CharField(source="agent.full_name", read_only=True)
    status_name = serializers.CharField(source="status.name", read_only=True)
    status_color = serializers.CharField(source="status.color", read_only=True)
    leader_lan_id = serializers.SerializerMethodField()
    current_leader_name = serializers.SerializerMethodField()
    current_leader_lan_id = serializers.SerializerMethodField()
    approved_by_lan_id = serializers.CharField(
        source="approved_by.user_lan_id", read_only=True, allow_null=True
    )
    approved_by_name = serializers.CharField(
        source="approved_by.full_name", read_only=True, allow_null=True
    )
    covered_duration = serializers.SerializerMethodField()
    excess_duration = serializers.SerializerMethodField()
    status_linked = serializers.SerializerMethodField()
    needs_leader_approval = serializers.SerializerMethodField()

    def _coverage(self, obj):
        coverage_map = self.context.get("coverage_map") or {}
        data = coverage_map.get(obj.id)
        if data is not None:
            return data
        from .services.status_event_coverage import coverage_for_event

        return coverage_for_event(obj)

    def get_covered_duration(self, obj):
        return self._coverage(obj).get("covered_duration", 0)

    def get_excess_duration(self, obj):
        return self._coverage(obj).get("excess_duration", 0)

    def get_status_linked(self, obj):
        return bool(self._coverage(obj).get("status_linked", False))

    def get_needs_leader_approval(self, obj):
        from .services.status_event_coverage import PAUSE_STATUS_IDS

        if obj.status_id not in PAUSE_STATUS_IDS:
            return False
        if obj.active_event or obj.approved is not None:
            return False
        if not self.get_status_linked(obj):
            return False
        return self.get_excess_duration(obj) > 0

    def _current_leader(self, obj):
        histories = getattr(obj.agent, "_active_histories", None)
        if histories is not None:
            history = histories[0] if histories else None
        else:
            history = get_active_history(obj.agent)
        return history.leader if history and history.leader else None

    def get_current_leader_name(self, obj):
        leader = self._current_leader(obj)
        return leader.full_name if leader else ""

    def get_leader_lan_id(self, obj):
        if obj.leader:
            return obj.leader.user_lan_id.lower()
        return ""

    def get_current_leader_lan_id(self, obj):
        leader = self._current_leader(obj)
        return leader.user_lan_id.lower() if leader else ""

    class Meta:
        model = StatusEvent
        fields = [
            "id",
            "user_lan_id",
            "agent_name",
            "leader_lan_id",
            "current_leader_name",
            "current_leader_lan_id",
            "status",
            "status_name",
            "status_color",
            "start_date",
            "final_date",
            "active_event",
            "total_duration",
            "covered_duration",
            "excess_duration",
            "status_linked",
            "needs_leader_approval",
            "approved",
            "approved_by",
            "approved_by_lan_id",
            "approved_by_name",
            "approved_duration",
            "approved_at",
            "approval_notes",
        ]


class StatusEventApprovalSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    approved_duration = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    approval_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate(self, attrs):
        if attrs["approved"] and attrs.get("approved_duration") is None:
            raise serializers.ValidationError(
                {"approved_duration": "Informe o tempo aprovado."}
            )
        return attrs


class StatusEventBatchApprovalItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    approved = serializers.BooleanField()
    approved_duration = serializers.IntegerField(min_value=0, required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["approved"] and attrs.get("approved_duration") is None:
            raise serializers.ValidationError(
                {"approved_duration": "Informe o tempo aprovado."}
            )
        return attrs


class StatusEventBatchApprovalSerializer(serializers.Serializer):
    approval_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    items = StatusEventBatchApprovalItemSerializer(many=True, allow_empty=False)


class StatusEventCreateSerializer(serializers.Serializer):
    schedule_today_id = serializers.UUIDField(required=False, allow_null=True)
    user_lan_id = serializers.CharField(required=False, allow_blank=True)
    date = serializers.DateField(required=False, allow_null=True)
    status_id = serializers.IntegerField(required=False, allow_null=True)
    action = serializers.ChoiceField(
        choices=["start_shift", "change_status"],
        default="change_status",
    )

    def validate(self, attrs):
        action = attrs.get("action", "change_status")
        if action == "change_status" and attrs.get("status_id") is None:
            raise serializers.ValidationError(
                {"status_id": "Obrigatório para change_status."}
            )
        has_schedule = attrs.get("schedule_today_id") is not None
        lan = (attrs.get("user_lan_id") or "").strip()
        has_agent_date = bool(lan and attrs.get("date"))
        if not has_schedule and not has_agent_date:
            raise serializers.ValidationError(
                "Informe schedule_today_id ou user_lan_id + date."
            )
        if lan:
            attrs["user_lan_id"] = lan
        return attrs


class NHUpdateItemSerializer(serializers.Serializer):
    user_lan_id = serializers.CharField()
    hierarchical_level_id = serializers.UUIDField(required=False, allow_null=True)
    hierarchical_level = serializers.CharField(required=False, allow_blank=True)
    current_activity = serializers.CharField(required=False, allow_blank=True)
    id_schedule = serializers.UUIDField(required=False)
    id_nh = serializers.UUIDField(required=False)

    def validate(self, attrs):
        level_id = attrs.get("hierarchical_level_id")
        level_name = (attrs.get("hierarchical_level") or "").strip()
        legacy = (attrs.get("current_activity") or "").strip()
        if not level_id and not level_name and not legacy:
            raise serializers.ValidationError(
                "Informe hierarchical_level_id, hierarchical_level ou current_activity."
            )
        return attrs


class NHUpdateBatchSerializer(serializers.Serializer):
    items = NHUpdateItemSerializer(many=True)


class ScheduleRequestSerializer(serializers.ModelSerializer):
    request_type_name = serializers.CharField(
        source="request_type.name",
        read_only=True,
    )

    class Meta:
        model = ScheduleRequest
        fields = [
            "id",
            "request_type",
            "request_type_name",
            "agent_lan_id",
            "agent_lan_id_2",
            "applicant_lan_id",
            "date_swap",
            "date_request",
            "description",
            "new_journey",
            "balance_time",
            "positive",
            "approved",
            "approved_leader",
            "date_approve_leader",
            "approver_leader_lan_id",
            "date_approve_plan",
            "approver_plan_lan_id",
            "validation_days",
            "validation_hour",
            "validation_activity",
        ]


class SwapRequestCreateSerializer(serializers.Serializer):
    swap_kind = serializers.ChoiceField(
        choices=[
            ("shift_schedule", "Troca de turno"),
            ("shift_bh", "Banco de horas"),
            ("peer", "Troca entre colaboradores"),
        ]
    )
    agent_lan_id = serializers.CharField(max_length=64)
    agent_lan_id_2 = serializers.CharField(required=False, allow_blank=True, max_length=64)
    date_swap = serializers.DateField()
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    new_journey = serializers.CharField(required=False, allow_blank=True, max_length=128)

    def validate(self, attrs):
        swap_kind = attrs.get("swap_kind")
        partner = (attrs.get("agent_lan_id_2") or "").strip()
        new_journey = (attrs.get("new_journey") or "").strip()
        date_swap = attrs.get("date_swap")

        date_error = validate_swap_date(date_swap) if date_swap else None
        if date_error:
            raise serializers.ValidationError({"date_swap": date_error})

        if swap_kind == "peer" and not partner:
            raise serializers.ValidationError(
                {"agent_lan_id_2": "Informe o colaborador parceiro para troca entre colaboradores."}
            )
        if swap_kind == "shift_schedule" and not new_journey:
            raise serializers.ValidationError(
                {"new_journey": "Informe o novo horário para troca de turno."}
            )
        if swap_kind == "shift_schedule":
            normalized = normalize_time_schedule(new_journey)
            if not is_time_range_schedule(normalized):
                raise serializers.ValidationError(
                    {"new_journey": "Informe o novo horário no formato HH:MM - HH:MM."}
                )
            computed = hours_from_work_schedule(normalized)
            if computed != STANDARD_WORK_HOURS:
                raise serializers.ValidationError(
                    {"new_journey": "A carga horária deve ser igual a 6 horas."}
                )
            attrs["new_journey"] = normalized
        if swap_kind in ("shift_schedule", "shift_bh") and partner:
            attrs["agent_lan_id_2"] = ""
        return attrs


class SwapRequestApprovalSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    approval_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class SwapRequestSerializer(serializers.ModelSerializer):
    request_type_name = serializers.CharField(source="request_type.name", read_only=True)
    workflow_status = serializers.SerializerMethodField()
    agent_name = serializers.SerializerMethodField()
    agent_name_2 = serializers.SerializerMethodField()
    agent_activity = serializers.SerializerMethodField()
    agent_activity_2 = serializers.SerializerMethodField()
    applicant_name = serializers.SerializerMethodField()
    approver_leader_name = serializers.SerializerMethodField()
    approver_plan_name = serializers.SerializerMethodField()
    leader_auto_approved = serializers.SerializerMethodField()
    leader_fast_tracked = serializers.SerializerMethodField()
    can_approve_leader = serializers.SerializerMethodField()
    can_approve_plan = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleRequest
        fields = [
            "id",
            "request_type",
            "request_type_name",
            "agent_lan_id",
            "agent_name",
            "agent_activity",
            "agent_lan_id_2",
            "agent_name_2",
            "agent_activity_2",
            "applicant_lan_id",
            "applicant_name",
            "date_swap",
            "date_request",
            "description",
            "new_journey",
            "swap_kind",
            "approved",
            "approved_leader",
            "date_approve_leader",
            "approver_leader_lan_id",
            "approver_leader_name",
            "date_approve_plan",
            "approver_plan_lan_id",
            "approver_plan_name",
            "validation_days",
            "validation_hour",
            "validation_activity",
            "workflow_status",
            "leader_auto_approved",
            "leader_fast_tracked",
            "can_approve_leader",
            "can_approve_plan",
            "created_at",
        ]

    def _name_for(self, lan_id: str) -> str:
        lan = (lan_id or "").strip().lower()
        if not lan:
            return ""
        names = self.context.get("agent_names") or {}
        return names.get(lan, "")

    def get_agent_name(self, obj) -> str:
        return self._name_for(obj.agent_lan_id)

    def get_agent_name_2(self, obj) -> str:
        return self._name_for(obj.agent_lan_id_2)

    def _activity_for(self, lan_id: str) -> str:
        lan = (lan_id or "").strip().lower()
        if not lan:
            return ""
        activities = self.context.get("agent_activities") or {}
        return activities.get(lan, "")

    def get_agent_activity(self, obj) -> str:
        return self._activity_for(obj.agent_lan_id)

    def get_agent_activity_2(self, obj) -> str:
        return self._activity_for(obj.agent_lan_id_2)

    def get_applicant_name(self, obj) -> str:
        return self._name_for(obj.applicant_lan_id)

    def get_approver_leader_name(self, obj) -> str:
        return self._name_for(obj.approver_leader_lan_id)

    def get_approver_plan_name(self, obj) -> str:
        return self._name_for(obj.approver_plan_lan_id)

    def get_workflow_status(self, obj) -> str:
        from .services.swap_request_workflow import resolve_swap_workflow_status

        return resolve_swap_workflow_status(obj)

    def get_leader_auto_approved(self, obj) -> bool:
        applicant = (obj.applicant_lan_id or "").strip().lower()
        approver = (obj.approver_leader_lan_id or "").strip().lower()
        return bool(
            obj.approved_leader is True
            and applicant
            and approver
            and applicant == approver
            and obj.date_approve_leader
            and obj.date_request
            and abs((obj.date_approve_leader - obj.date_request).total_seconds()) < 5
        )

    def get_leader_fast_tracked(self, obj) -> bool:
        from .services.swap_request_workflow import should_finalize_peer_after_leader

        return bool(
            obj.approved_leader is True
            and obj.approved is True
            and obj.date_approve_plan is None
            and should_finalize_peer_after_leader(obj)
        )

    def get_can_approve_leader(self, obj) -> bool:
        profile = self.context.get("profile")
        if not profile:
            return False
        from .services.swap_request_workflow import can_approve_swap_leader

        return can_approve_swap_leader(profile, obj)

    def get_can_approve_plan(self, obj) -> bool:
        profile = self.context.get("profile")
        if not profile:
            return False
        from .services.swap_request_workflow import can_approve_swap_plan

        return can_approve_swap_plan(profile, obj)


class NotifyEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = NotifyEntry
        fields = ["id", "action", "status", "time"]


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ["id", "date", "location", "holiday_type", "name"]


class PublishedEscalaUpdateSerializer(serializers.Serializer):
    dia_escala = serializers.CharField(required=False, allow_blank=False)
    week = serializers.CharField(required=False, allow_blank=True)
    weekend = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Informe o horário, o intervalo ou o código de ausência."
            )
        return attrs


class AgentScheduleEscalaEditSerializer(serializers.Serializer):
    user_lan_id = serializers.CharField()
    date = serializers.DateField()
    dia_escala = serializers.CharField(required=False, allow_blank=False)
    week = serializers.CharField(required=False, allow_blank=True)
    weekend = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not any(attrs.get(key) for key in ("dia_escala", "week", "weekend")):
            raise serializers.ValidationError(
                "Informe o horário, o intervalo ou o código de ausência."
            )
        return attrs


class AbsenceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AbsenceType
        fields = ["id", "code", "name", "color", "active", "observation"]


class AbsenceTypeWriteSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False, min_value=1, max_value=32767)

    class Meta:
        model = AbsenceType
        fields = ["id", "code", "name", "color", "active", "observation"]

    def validate_code(self, value: str) -> str:
        code = normalize_absence_code(value)
        if not code:
            raise serializers.ValidationError("Informe o código da ausência.")
        if not code.replace("_", "").replace(" ", "").isalnum():
            raise serializers.ValidationError(
                "Use apenas letras, números, espaços e sublinhado no código."
            )
        return code

    def validate_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Informe o nome.")
        return name


class BreakTimeUpdateItemSerializer(serializers.Serializer):
    user_lan_id = serializers.CharField()
    week = serializers.CharField(required=False, allow_blank=True)
    weekend = serializers.CharField(required=False, allow_blank=True)


class BreakTimeBatchUpdateSerializer(serializers.Serializer):
    items = BreakTimeUpdateItemSerializer(many=True)


class OccurrenceTypeSerializer(serializers.ModelSerializer):
    status_type_ids = serializers.SerializerMethodField()

    class Meta:
        model = OccurrenceType
        fields = ["id", "name", "color", "active", "observation", "status_type_ids"]

    def get_status_type_ids(self, obj: OccurrenceType) -> list[int]:
        return list(obj.status_types.values_list("id", flat=True))


class StatusTypeWriteSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False, min_value=1, max_value=32767)

    class Meta:
        model = StatusType
        fields = [
            "id",
            "name",
            "color",
            "active",
            "logged_in",
            "observation",
            "deducts_logged_time",
            "default_time_seconds",
            "deducts_production",
        ]

    def validate_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Informe o nome.")
        return name


class OccurrenceTypeWriteSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False, min_value=1, max_value=32767)
    status_type_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=StatusType.objects.all(),
        source="status_types",
        required=False,
    )

    class Meta:
        model = OccurrenceType
        fields = ["id", "name", "color", "active", "observation", "status_type_ids"]

    def validate_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Informe o nome.")
        return name


def _format_duration_hhmm(seconds: int | None) -> str:
    if not seconds:
        return "00:00"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h:02d}:{m:02d}"


class OperationalOccurrenceExtensionSerializer(serializers.ModelSerializer):
    extra_display = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = OperationalOccurrenceExtension
        fields = [
            "id",
            "extra_seconds",
            "extra_display",
            "description",
            "approved",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "approval_notes",
            "created_by",
            "created_by_name",
            "created_at",
        ]
        read_only_fields = fields

    def get_extra_display(self, obj) -> str:
        return _format_duration_hhmm(obj.extra_seconds)

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.full_name if obj.created_by else ""

    def get_approved_by_name(self, obj) -> str:
        return obj.approved_by.full_name if obj.approved_by else ""


class OperationalOccurrenceSerializer(serializers.ModelSerializer):
    agent_lan_id = serializers.CharField(source="agent.user_lan_id", read_only=True)
    agent_name = serializers.CharField(source="agent.full_name", read_only=True)
    agent_job_activity = serializers.SerializerMethodField()
    agent_current_nh = serializers.SerializerMethodField()
    occurrence_type_name = serializers.CharField(
        source="occurrence_type.name", read_only=True
    )
    occurrence_type_color = serializers.CharField(
        source="occurrence_type.color", read_only=True
    )
    forecast_display = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    leader_name = serializers.SerializerMethodField()
    workflow_status = serializers.SerializerMethodField()
    in_progress = serializers.SerializerMethodField()
    can_request_extension = serializers.SerializerMethodField()
    pending_extension = serializers.SerializerMethodField()

    class Meta:
        model = OperationalOccurrence
        fields = [
            "id",
            "date",
            "agent_lan_id",
            "agent_name",
            "agent_job_activity",
            "agent_current_nh",
            "schedule_today_id",
            "occurrence_type",
            "occurrence_type_name",
            "occurrence_type_color",
            "forecast_seconds",
            "forecast_display",
            "scheduled_time",
            "description",
            "cancelled",
            "approved",
            "workflow_status",
            "in_progress",
            "can_request_extension",
            "pending_extension",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "approval_notes",
            "created_by",
            "created_by_name",
            "leader",
            "leader_name",
            "created_at",
        ]
        read_only_fields = fields

    def get_forecast_display(self, obj) -> str:
        return _format_duration_hhmm(obj.forecast_seconds)

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.full_name if obj.created_by else ""

    def get_approved_by_name(self, obj) -> str:
        return obj.approved_by.full_name if obj.approved_by else ""

    def get_leader_name(self, obj) -> str:
        return obj.leader.full_name if obj.leader else ""

    def get_agent_job_activity(self, obj) -> str:
        if obj.schedule_today and obj.schedule_today.job_activity:
            return obj.schedule_today.job_activity
        history = get_active_history(obj.agent)
        return history.job_activity if history and history.job_activity else ""

    def get_agent_current_nh(self, obj) -> str:
        if obj.schedule_today:
            schedule = obj.schedule_today
            if schedule.hierarchical_level and schedule.hierarchical_level.name:
                return schedule.hierarchical_level.name.strip()
            legacy = (schedule.current_activity or "").strip()
            if legacy:
                return legacy
        record = getattr(obj.agent, "current_activity_record", None)
        if record:
            if record.hierarchical_level and record.hierarchical_level.name:
                return record.hierarchical_level.name.strip()
            legacy = (record.current_activity or "").strip()
            if legacy:
                return legacy
        return ""

    def get_workflow_status(self, obj) -> str:
        return resolve_operational_occurrence_workflow_status(obj)

    def get_in_progress(self, obj) -> bool:
        return is_operational_occurrence_in_progress(obj)

    def get_can_request_extension(self, obj) -> bool:
        return is_operational_occurrence_in_progress(obj) and not get_pending_occurrence_extension(
            obj
        )

    def get_pending_extension(self, obj):
        pending_list = getattr(obj, "_pending_extensions", None)
        if pending_list is not None:
            pending = pending_list[0] if pending_list else None
        else:
            pending = get_pending_occurrence_extension(obj)
        if not pending:
            return None
        return OperationalOccurrenceExtensionSerializer(pending).data


class OperationalOccurrenceCreateItemSerializer(serializers.Serializer):
    schedule_today_id = serializers.UUIDField()
    occurrence_type_id = serializers.IntegerField(min_value=1)
    forecast_seconds = serializers.IntegerField(min_value=0)
    scheduled_time = serializers.TimeField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class OperationalOccurrenceBatchCreateSerializer(serializers.Serializer):
    items = OperationalOccurrenceCreateItemSerializer(many=True, allow_empty=False)


class OperationalOccurrenceApprovalSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    approval_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class OperationalOccurrenceBatchApprovalItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    approved = serializers.BooleanField()


class OperationalOccurrenceBatchApprovalSerializer(serializers.Serializer):
    approval_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    items = OperationalOccurrenceBatchApprovalItemSerializer(many=True, allow_empty=False)


class OperationalOccurrenceCancelSerializer(serializers.Serializer):
    approval_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class OperationalOccurrenceExtensionRequestSerializer(serializers.Serializer):
    extra_seconds = serializers.IntegerField(min_value=60)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class OperationalOccurrenceBatchExtensionItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()


class OperationalOccurrenceBatchExtensionSerializer(serializers.Serializer):
    extra_seconds = serializers.IntegerField(min_value=60)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    items = OperationalOccurrenceBatchExtensionItemSerializer(many=True, allow_empty=False)


class OperationalOccurrenceExtensionApprovalSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    approval_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class OperationalOccurrenceUpdateSerializer(serializers.Serializer):
    scheduled_time = serializers.TimeField(required=False, allow_null=True)
    forecast_seconds = serializers.IntegerField(min_value=0, required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Informe ao menos um campo para atualizar.")
        return attrs
