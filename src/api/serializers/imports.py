from rest_framework import serializers


class ImportSerializer(serializers.Serializer):
    """Validate mobile import requests."""

    mode = serializers.ChoiceField(choices=["new", "overwrite"])
    username = serializers.CharField(required=False, allow_blank=True)
    file = serializers.FileField(required=False)

    def validate(self, attrs):
        """Validate source-specific required fields."""
        source = self.context.get("source")
        if source == "letterboxd" and "file" not in attrs:
            raise serializers.ValidationError({"file": "A Letterboxd ZIP file is required."})
        if source == "storygraph" and "file" not in attrs:
            raise serializers.ValidationError({"file": "A StoryGraph CSV file is required."})
        if source == "goodreads" and "file" not in attrs:
            raise serializers.ValidationError({"file": "A Goodreads CSV file is required."})
        if source == "mal_export" and "file" not in attrs:
            raise serializers.ValidationError({"file": "A MyAnimeList XML export file is required."})
        return attrs
