from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from app.providers.services import ProviderAPIError


class DiaryHistoryConflict(exceptions.APIException):
    """Consumed tracking is protected by existing diary history."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "Delete your logs before marking this as unwatched."
    default_code = "diary_history_exists"


class BookTrackingConflict(exceptions.APIException):
    """A book transition conflicts with its authoritative journey state."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The book changed before this action could be completed."
    default_code = "book_tracking_conflict"


class AllMediaSearchUnavailable(exceptions.APIException):
    """Every provider in an all-media search failed."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Media search providers are temporarily unavailable."
    default_code = "provider_unavailable"


class SearchReplaced(exceptions.APIException):
    """The same person searched again before this search reached its provider."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "A newer search replaced this one."
    default_code = "search_replaced"


class PeopleSearchUnavailable(exceptions.APIException):
    """Every provider in a people search failed."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "People search providers are temporarily unavailable."
    default_code = "provider_unavailable"


class ListTypeMismatch(exceptions.APIException):
    """A typed list was used through the wrong entry endpoint."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This endpoint does not support the list's entry type."
    default_code = "list_type_mismatch"


class PersonProviderUnavailable(exceptions.APIException):
    """A provider returned an unusable person profile."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The person provider returned an unusable profile."
    default_code = "provider_unavailable"


def _code_for_status(status_code):
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "authentication_required"
    if status_code == status.HTTP_403_FORBIDDEN:
        return "permission_denied"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "not_found"
    if status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return "rate_limited"
    if status_code == status.HTTP_409_CONFLICT:
        return "conflict"
    return "validation_error" if status_code == 400 else "error"


def api_exception_handler(exc, context):
    """Return consistent JSON error envelopes for API clients."""
    if isinstance(exc, ProviderAPIError):
        return Response(
            {
                "error": {
                    "code": "provider_unavailable",
                    "message": str(exc),
                    "fields": None,
                    "request_id": context["request"].headers.get("X-Request-ID"),
                },
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    response = exception_handler(exc, context)
    if response is None:
        if isinstance(exc, Http404):
            response = exception_handler(exceptions.NotFound(), context)
        else:
            return None

    detail = response.data
    fields = None
    message = "Request failed."

    if isinstance(detail, dict):
        if "detail" in detail:
            message = str(detail["detail"])
        else:
            fields = detail
            message = "One or more fields are invalid."
    elif isinstance(detail, list):
        fields = {"non_field_errors": detail}
        message = "One or more fields are invalid."
    else:
        message = str(detail)

    response.data = {
        "error": {
            "code": getattr(exc, "default_code", None)
            or _code_for_status(response.status_code),
            "message": message,
            "fields": fields,
            "request_id": context["request"].headers.get("X-Request-ID"),
        },
    }
    return response
