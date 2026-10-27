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


class TVValidationError(exceptions.APIException):
    """An invalid TV request (plan 2.3.8): 400 validation_error.

    detail is the user copy, or a {field: [messages]} dict for field errors.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "One or more fields are invalid."
    default_code = "validation_error"


class TVConflictError(exceptions.APIException):
    """A TV action that isn't available now: 409 with the domain's code.

    tv_log_blocks, tv_action_unavailable or tv_state_changed. A stale version carries
    the show's new state version and count in error.tv (TV-705).
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This change isn't available right now."
    default_code = "tv_action_unavailable"

    def __init__(self, detail=None, code=None, tv=None):
        """Keep the domain's code on the instance, as BookTrackingConflict users do."""
        super().__init__(detail=detail)
        if code:
            self.default_code = code
        self.tv = tv or None


class TVDetailsUnavailableError(exceptions.APIException):
    """The provider can't be reached and nothing is stored (TV-103): retryable 503.

    Its code is provider_unavailable, whose message installed builds show (TV-900);
    any other 503 code makes them say the server can't be reached. error.reason names
    the TV case for the new build.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "TV details are unavailable right now"
    default_code = "provider_unavailable"
    reason = "tv_details_unavailable"


def tv_api_error(error):
    """Return the API exception for a TV domain error, or None for anything else.

    tv_tracking.TVConflict and its subclasses are 409s; tv_tracking.TVInvalid and
    tv_listing.InvalidEpisode are 400s; tv_listing.TVDetailsUnavailable is a 503.
    """
    from app import tv_listing, tv_tracking

    if isinstance(error, tv_tracking.TVConflict):
        return TVConflictError(detail=error.message, code=error.code, tv=error.extra or None)
    if isinstance(error, (tv_tracking.TVInvalid, tv_listing.InvalidEpisode)):
        return TVValidationError(detail=error.message)
    if isinstance(error, tv_listing.TVDetailsUnavailable):
        return TVDetailsUnavailableError(detail=error.message)
    return None


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
    exc = tv_api_error(exc) or exc
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
    if getattr(exc, "tv", None):
        # tv_state_changed: the show's new version and count, so the app can ask again.
        response.data["error"]["tv"] = exc.tv
    if getattr(exc, "reason", None):
        response.data["error"]["reason"] = exc.reason
    return response
