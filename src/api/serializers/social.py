from rest_framework import serializers

from social.models import PushDevice


class LikeSerializer(serializers.Serializer):
    """Validate generic like target payloads."""

    target_type = serializers.ChoiceField(choices=["diary", "list"])
    target_id = serializers.IntegerField(min_value=1)


class CommentWriteSerializer(serializers.Serializer):
    """Validate a new comment: trimmed, and not empty."""

    body = serializers.CharField(max_length=2000)


class PushDeviceSerializer(serializers.Serializer):
    """Validate a phone's Apple push token registration."""

    token = serializers.RegexField(r"^[0-9a-fA-F]{32,200}$")
    bundle_id = serializers.CharField(max_length=200)
    environment = serializers.ChoiceField(choices=PushDevice.Environment.choices)
