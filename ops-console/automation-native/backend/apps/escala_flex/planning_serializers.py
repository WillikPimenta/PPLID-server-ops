from rest_framework import serializers



from .models import Escala, EscalaImportBatch





class EscalaImportBatchSerializer(serializers.ModelSerializer):

    uploaded_by_username = serializers.CharField(

        source="uploaded_by.username",

        read_only=True,

        default="",

    )



    class Meta:

        model = EscalaImportBatch

        fields = [

            "id",

            "filename",

            "uploaded_by_username",

            "status",

            "failure_detail",

            "sheets_processed",

            "rows_upserted",

            "schedules_synced",

            "dates_rebuilt",

            "errors",

            "warnings",

            "created_at",

            "finished_at",

        ]

        read_only_fields = fields





class EscalaImportStartedSerializer(serializers.Serializer):

    batch_id = serializers.UUIDField()

    status = serializers.CharField()





class EscalaImportResultSerializer(serializers.Serializer):

    batch_id = serializers.UUIDField()

    sheets_processed = serializers.ListField(child=serializers.CharField())

    rows_upserted = serializers.IntegerField()

    schedules_synced = serializers.IntegerField()

    dates_rebuilt = serializers.ListField(child=serializers.CharField())

    errors = serializers.ListField(child=serializers.DictField())

    warnings = serializers.ListField(child=serializers.DictField())





class EscalaSerializer(serializers.ModelSerializer):

    agent_lan_id = serializers.CharField(source="agent.user_lan_id", read_only=True)

    agent_name = serializers.CharField(source="agent.full_name", read_only=True)

    leader_name = serializers.CharField(source="leader.full_name", read_only=True, default="")

    leader_lan_id = serializers.CharField(source="leader.user_lan_id", read_only=True, default="")

    activity_name = serializers.CharField(source="job_activity.name", read_only=True, default="")

    location_name = serializers.CharField(

        source="location.display_name",

        read_only=True,

        default="",

    )



    class Meta:

        model = Escala

        fields = [

            "id",

            "agent_lan_id",

            "agent_name",

            "leader_name",

            "leader_lan_id",

            "activity_name",

            "location_name",

            "bloco",

            "equipe",

            "horario",

            "data",

            "dia_escala",

            "updated_at",

        ]

