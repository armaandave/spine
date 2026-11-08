"""Site-wide Home picture: an admin picks it, every user's Home shows it."""

import logging

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.common import (
    MediaRefSerializer,
    find_item,
    get_or_create_item_from_metadata,
    media_ref_from_item,
)
from api.services import media as media_service
from app.curation import is_curator
from app.models import HomePicture
from app.providers import services as provider_services
from social.models import SocialAuditLog

logger = logging.getLogger(__name__)

HOME_PICTURE_MEDIA_TYPES = {"movie", "tv", "anime", "game"}


class HomePictureSerializer(serializers.Serializer):
    """Validate Home picture writes."""

    ref = MediaRefSerializer()
    backdrop_url = serializers.URLField(max_length=1000)


def _picture_dict(picture):
    item = picture.item
    year = item.release_year or (item.release_date.year if item.release_date else None)
    return {
        "ref": media_ref_from_item(item),
        "title": item.display_title or item.title,
        "year": year,
        "backdrop_url": picture.backdrop_url,
        "set_by": picture.set_by.username if picture.set_by else None,
        "updated_at": picture.updated_at,
    }


def _envelope(request):
    picture = HomePicture.objects.select_related("item", "set_by").filter(pk=1).first()
    return Response(
        {
            "picture": _picture_dict(picture) if picture else None,
            "can_change": is_curator(request.user),
        },
    )


def _audit(request, action, item, backdrop_url):
    SocialAuditLog.objects.create(
        actor=request.user,
        action=action,
        target_type="item",
        target_id=item.id,
        metadata={
            "backdrop_url": backdrop_url,
            "ref": {
                "source": item.source,
                "media_type": item.media_type,
                "media_id": item.media_id,
            },
        },
    )


def _resolve_item(ref):
    """Find the title, fetching new metadata if needed; None when not found."""
    try:
        item = find_item(ref)
        if item is None:
            metadata = provider_services.get_media_metadata(
                ref["media_type"],
                ref["media_id"],
                ref["source"],
            )
            item = get_or_create_item_from_metadata(ref, metadata)
    except Exception:
        logger.exception("Home picture metadata lookup failed")
        return None
    return item


def _backdrop_error(request, item, backdrop_url):
    """None when the title offers this backdrop, else the error response."""
    try:
        options = media_service.backdrop_options(
            source=item.source,
            media_type=item.media_type,
            media_id=item.media_id,
            request=request,
            user=request.user,
        )
    except ValueError as error:
        return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        logger.exception("Home picture backdrop lookup failed")
        return Response(
            {"detail": "Could not load backdrops right now."},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    offered = {entry.get("url") for entry in (options or {}).get("backdrops", [])}
    if backdrop_url not in offered:
        return Response(
            {"detail": "That backdrop is not one of this title's options."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _forbidden():
    return Response(
        {"detail": "Only an admin can change the Home picture."},
        status=status.HTTP_403_FORBIDDEN,
    )


class HomePictureView(APIView):
    """Read the site Home picture; admins set or clear it."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return _envelope(request)

    def put(self, request):
        if not is_curator(request.user):
            return _forbidden()
        serializer = HomePictureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ref = serializer.validated_data["ref"]
        backdrop_url = serializer.validated_data["backdrop_url"]
        if ref["media_type"] not in HOME_PICTURE_MEDIA_TYPES:
            return Response(
                {"detail": "Home picture needs a movie, TV, anime or game title with backdrops"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item = _resolve_item(ref)
        if item is None:
            return Response({"detail": "Could not find that title."}, status=status.HTTP_400_BAD_REQUEST)
        error = _backdrop_error(request, item, backdrop_url)
        if error is not None:
            return error
        HomePicture.objects.update_or_create(
            pk=1,
            defaults={"item": item, "backdrop_url": backdrop_url, "set_by": request.user},
        )
        _audit(request, "home_picture_set", item, backdrop_url)
        return _envelope(request)

    def delete(self, request):
        if not is_curator(request.user):
            return _forbidden()
        existing = HomePicture.objects.select_related("item").filter(pk=1).first()
        if existing:
            _audit(request, "home_picture_clear", existing.item, existing.backdrop_url)
            existing.delete()
        return _envelope(request)
