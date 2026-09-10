from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.accounts.services.profile_location import resolve_profile_location

from .models import User, UserChangeHistorico


class AuthUserSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()
    uf = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "display_name",
            "must_change_password",
            "location",
            "uf",
        ]

    def to_representation(self, instance):
        location, uf = resolve_profile_location(instance)
        self._profile_location = location
        self._profile_uf = uf
        return super().to_representation(instance)

    def get_display_name(self, obj):
        for part in (obj.first_name, obj.last_name):
            token = (part or "").strip().split()
            if token:
                return token[0]
        return obj.username

    def get_location(self, obj):
        return getattr(self, "_profile_location", "")

    def get_uf(self, obj):
        return getattr(self, "_profile_uf", "")


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, max_length=128)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        validate_password(value, user=self.context.get("user"))
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "A confirmação deve ser igual à nova senha."}
            )
        user = self.context.get("user")
        must_change = bool(getattr(user, "must_change_password", False))
        current_password = (attrs.get("current_password") or "").strip()
        if not must_change and not current_password:
            raise serializers.ValidationError(
                {"current_password": "Informe a senha atual."}
            )
        attrs["current_password"] = current_password
        return attrs


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "is_active",
        ]
        read_only_fields = ["id"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class PortalUserListSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_null=True)
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    display_name = serializers.CharField()
    is_active = serializers.BooleanField()
    must_change_password = serializers.BooleanField()
    last_login = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    roles = serializers.ListField(child=serializers.CharField())
    has_agent = serializers.BooleanField()
    agent_name = serializers.CharField(allow_null=True)
    agent_lan_id = serializers.CharField(allow_null=True)
    job_title = serializers.CharField(allow_null=True)
    agent_active = serializers.BooleanField(allow_null=True)
    leader_name = serializers.CharField(allow_null=True)
    leader_lan_id = serializers.CharField(allow_null=True)
    location = serializers.CharField(allow_null=True)
    team = serializers.CharField(allow_null=True)


class PortalUserCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    is_active = serializers.BooleanField(required=False, default=False)
    roles = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        default=list,
    )


class PortalUserUpdateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    is_active = serializers.BooleanField(required=False)
    roles = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
    )


class PortalUserBulkActiveSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)
    is_active = serializers.BooleanField()


class PortalUserBulkResetSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)


class PortalUserBulkReplicateRbacSerializer(serializers.Serializer):
    source_user_id = serializers.UUIDField()
    target_user_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)


class PortalUserBulkRolesSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)
    operation = serializers.ChoiceField(choices=["replace", "add", "remove"])
    roles = serializers.ListField(child=serializers.CharField(max_length=64), allow_empty=True)


class UserChangeHistoricoSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    action = serializers.CharField()
    field_name = serializers.CharField()
    old_value = serializers.CharField()
    new_value = serializers.CharField()
    summary = serializers.CharField()
    actor_username = serializers.CharField()
    created_at = serializers.DateTimeField()
