import functools
import logging
import re
from contextlib import suppress

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    ParseError,
    ValidationError,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken

from api.serializers.auth import (
    MAX_REFRESH_TOKEN_LENGTH,
    LoginSerializer,
    RefreshSerializer,
    RegisterSerializer,
    token_response,
)
from api.throttling import AuthRateThrottle, RefreshRateThrottle

logger = logging.getLogger(__name__)

# How long a refresh token that was just rotated can still be exchanged for a fresh
# pair (see RefreshView). Override with JWT_REFRESH_REUSE_GRACE_SECONDS; 0 disables.
DEFAULT_REFRESH_REUSE_GRACE_SECONDS = 60

# A user id read out of a token is only logged when it looks like an id. One read from
# a token nobody has verified could otherwise smuggle newlines into the log.
_LOGGABLE_ID = re.compile(r"[\w-]{1,64}")


class RefreshTemporarilyUnavailable(APIException):
    """The token cannot be judged right now: the client should retry, not sign out."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Token refresh is temporarily unavailable. Try again shortly."
    default_code = "service_unavailable"


def _request_data(request):
    """Return ``request.data``, with a body nested too deeply to parse as a 400.

    DRF's JSON parser only expects ValueError from bad JSON, but a body nested past
    the decoder's limit (about 10,000 levels) raises RecursionError: an HTML 500.
    """
    try:
        return request.data
    except RecursionError as exc:
        raise ParseError("JSON parse error - the body is nested too deeply.") from exc


def _json_object(request):
    """Return the request body, which must be a JSON object, or raise the usual 400."""
    data = _request_data(request)
    if not hasattr(data, "get"):
        message = f"Invalid data. Expected a dictionary, but got {type(data).__name__}."
        raise ValidationError({"non_field_errors": [message]})
    return data


def _require_strings(data, *names):
    """Return the named values of a JSON object, all of which must be strings.

    A missing or non-string one raises the usual 400. The values feed calls that
    crash on anything else.
    """
    problems = {}
    for name in names:
        value = data.get(name)
        if value is None:
            problems[name] = ["This field is required."]
        elif not isinstance(value, str):
            problems[name] = ["Not a valid string."]
    if problems:
        raise ValidationError(problems)
    return [data[name] for name in names]


def _form_errors(form):
    """Return a form's errors as ``{field: [message, ...]}`` for a ValidationError."""
    return {
        field: [error["message"] for error in errors]
        for field, errors in form.errors.get_json_data().items()
    }


def _grace_seconds():
    """Return how long a rotated refresh token may be reused, 0 if that is off."""
    seconds = getattr(
        settings,
        "JWT_REFRESH_REUSE_GRACE_SECONDS",
        DEFAULT_REFRESH_REUSE_GRACE_SECONDS,
    )
    return seconds if isinstance(seconds, int | float) and seconds > 0 else 0


def refresh_rotation_marker_key(jti):
    """Return the cache key that marks a refresh token as just rotated."""
    return f"auth:refresh-rotated:{jti}"


def _posted_refresh_token(request):
    """Return the posted ``refresh`` value if it is a plausible token, else None.

    A plausible token is a string of token size that is ASCII: real ones are, and a
    lone surrogate makes PyJWT raise UnicodeEncodeError, an HTML 500. Reading the body
    raises ParseError if it cannot be parsed.
    """
    data = _request_data(request)
    token = data.get("refresh") if hasattr(data, "get") else None
    if isinstance(token, str):
        token = token.strip()
        if 0 < len(token) <= MAX_REFRESH_TOKEN_LENGTH and token.isascii():
            return token
    return None


def _loggable_id(value):
    """Return ``value`` as text if it looks like an id, else None."""
    text = "" if value is None else str(value)
    return text if _LOGGABLE_ID.fullmatch(text) else None


def _unverified_user_id(request):
    """Return the user id claim of the posted refresh token, or None.

    Diagnostics only. The token is decoded without verifying its signature or expiry,
    so expired, blacklisted and forged tokens can still be attributed to a user. That
    makes the value untrusted: log it, never act on it. Never raises.
    """
    with suppress(Exception):
        raw = _posted_refresh_token(request)
        if raw:
            payload = RefreshToken(raw, verify=False).payload
            return _loggable_id(payload.get(api_settings.USER_ID_CLAIM))
    return None


def _validation_problem(exc):
    """Describe a ValidationError as ``field=code`` pairs. Never quotes the input."""
    try:
        codes = exc.get_codes()
        pairs = [
            f"{field}={','.join(sorted(set(field_codes)))}"
            for field, field_codes in sorted(codes.items())
        ]
    except Exception:  # noqa: BLE001 - only ever called while logging
        return "invalid"
    return "; ".join(pairs) or "invalid"


def _log_refresh_rejection(request, reason, blacklisted_ago=None):
    """Warn that a refresh request was rejected, without logging any token."""
    if blacklisted_ago is not None:
        reason = f"{reason}, blacklisted {blacklisted_ago}s ago"
    logger.warning(
        "Auth refresh rejected: %s (claimed_user_id=%s)",
        reason,
        _unverified_user_id(request) or "unknown",
    )


def _set_rotation_marker(jti, window):
    """Record that the refresh token ``jti`` was rotated. Never raises."""
    try:
        cache.set(refresh_rotation_marker_key(jti), 1, timeout=window)
    except Exception as exc:  # noqa: BLE001 - the rotation has already committed
        logger.warning(
            "Auth refresh could not record the rotation marker (%s)",
            type(exc).__name__,
        )


def _schedule_rotation_marker(request):
    """Once this rotation commits, mark the refresh token it replaced as just rotated.

    The marker is what lets a retry of a rotation whose response never reached the
    phone through (see _reissue_within_grace). It is written on commit, so a rolled
    back rotation leaves none, and it never raises: the rotation itself succeeded.
    """
    window = _grace_seconds()
    raw = _posted_refresh_token(request)
    if not window or raw is None:
        return
    with suppress(Exception):
        jti = RefreshToken(raw, verify=False)[api_settings.JTI_CLAIM]
        transaction.on_commit(functools.partial(_set_rotation_marker, jti, window))


def _verified_refresh_claims(raw):
    """Return a refresh token's claims if its signature, expiry and type check out.

    Unlike ``RefreshToken(raw)`` this ignores the blacklist, so it also vouches for
    tokens that were rotated or revoked. None means the token is not what it claims
    to be, and that is all that keeps a forger away from a fresh pair.
    """
    try:
        token = UntypedToken(raw)
    except TokenError:
        return None
    if token.get(api_settings.TOKEN_TYPE_CLAIM) != RefreshToken.token_type:
        return None
    return token.payload


def _blacklisted_seconds_ago(jti):
    """Return how many seconds ago the token was blacklisted, None if it was not."""
    blacklisted_at = (
        BlacklistedToken.objects.filter(token__jti=jti)
        .values_list("blacklisted_at", flat=True)
        .first()
    )
    if blacklisted_at is None:
        return None
    return max(0, int((timezone.now() - blacklisted_at).total_seconds()))


def _active_user(claims):
    """Return the token's user if it still exists and may authenticate, else None."""
    User = get_user_model()
    lookup = {api_settings.USER_ID_FIELD: claims.get(api_settings.USER_ID_CLAIM)}
    try:
        user = User.objects.filter(**lookup).first()
    except (TypeError, ValueError):
        return None
    if user is None or not api_settings.USER_AUTHENTICATION_RULE(user):
        return None
    return user


def _reissue_within_grace(claims, blacklisted_ago):
    """Return a fresh token pair for a refresh token that was rotated moments ago.

    Rotation blacklists the old token. If the rotation's response never reached the
    phone, its retry presents a dead token and, as far as the client can tell, the
    session is over. Within the grace window such a retry is answered with a
    brand-new pair instead. Every one of these must hold, or this returns None:

    - the token verified (signature, expiry and type),
    - it is blacklisted, and was blacklisted within the window,
    - a rotation marker exists for it (logout writes none, so a revoked token never
      gets grace), and
    - its user still exists and may authenticate.

    If the marker cannot be read the answer is unknown, and a 401 would sign the user
    out over a cache outage, so this raises RefreshTemporarilyUnavailable (a 503).
    """
    window = _grace_seconds()
    if not window or claims is None or blacklisted_ago is None:
        return None
    if blacklisted_ago > window:
        return None
    try:
        marker = cache.get(refresh_rotation_marker_key(claims[api_settings.JTI_CLAIM]))
    except Exception as exc:  # any cache failure means "try again", never a sign-out
        logger.warning(
            "Auth refresh could not read the rotation marker (%s), answering 503 "
            "(user_id=%s)",
            type(exc).__name__,
            claims.get(api_settings.USER_ID_CLAIM),
        )
        raise RefreshTemporarilyUnavailable from exc
    user = _active_user(claims) if marker is not None else None
    if user is None:
        return None
    refresh = RefreshToken.for_user(user)
    logger.warning(
        "Auth refresh reused rotated token within grace (%ss after rotation, "
        "user_id=%s)",
        blacklisted_ago,
        user.pk,
    )
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class RegisterView(APIView):
    """Create an account and return tokens."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=_request_data(request))
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(token_response(user, request=request), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    """Login with username/email and password."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(
            data=_request_data(request),
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        return Response(token_response(serializer.validated_data["user"], request=request))


class RefreshView(APIView):
    """Refresh JWT tokens.

    The iOS client signs the user out on a 401 and treats every other failure as
    temporary, so a dead refresh token must always answer 401, never 500. Each
    rejection is logged with its reason and the unverified user id, never the token.

    Rotation blacklists the old token, so a rotation whose response never reached the
    phone would turn the retry into a 401 and sign the user out. A token rotated
    within the last JWT_REFRESH_REUSE_GRACE_SECONDS (60) is therefore exchanged for a
    brand-new pair instead; _reissue_within_grace lists the conditions. If the cache
    that holds the marker is down, the answer is a 503 rather than a 401.
    """

    permission_classes = [AllowAny]
    # The refresh token in the body is the only credential. Authenticating the request
    # too would let a leftover "Authorization: Bearer <stale access token>" header or
    # session cookie fail it with a 401/403 before this view runs, and a 401 from here
    # reads as "this session is over".
    authentication_classes = []
    throttle_classes = [RefreshRateThrottle]

    def get_authenticate_header(self, request):
        # With no authenticators DRF has no challenge to send and would turn every 401
        # below into a 403. Keep the Bearer challenge, as SimpleJWT's own views do.
        return 'Bearer realm="api"'

    def post(self, request):
        try:
            serializer = RefreshSerializer(data=_request_data(request))
            # SimpleJWT blacklists the old token and stores its successor in separate
            # writes. Committing them together means a failure in between cannot leave
            # the old token dead and the new one undelivered, which would turn the
            # client's retry into a 401 and sign the user out.
            with transaction.atomic():
                serializer.is_valid(raise_exception=True)
                _schedule_rotation_marker(request)
        except TokenError as exc:
            return self._answer_dead_token(request, exc)
        except get_user_model().DoesNotExist as exc:
            # The serializer looks the user up with .get(), so a deleted account
            # crashes it. Answer like it already does for an inactive account.
            error = AuthenticationFailed(
                serializer.error_messages["no_active_account"],
                "no_active_account",
            )
            _log_refresh_rejection(request, "User no longer exists")
            raise error from exc
        except AuthenticationFailed as exc:
            _log_refresh_rejection(request, exc.detail)
            raise
        except ValidationError as exc:
            problem = _validation_problem(exc)
            _log_refresh_rejection(request, f"invalid request: {problem}")
            raise
        except ParseError:
            _log_refresh_rejection(request, "unparseable request body")
            raise
        return Response(serializer.validated_data)

    def _answer_dead_token(self, request, exc):
        """Return a fresh pair for a token rotated moments ago, else raise a 401."""
        raw = _posted_refresh_token(request)
        claims = _verified_refresh_claims(raw) if raw else None
        jti = claims[api_settings.JTI_CLAIM] if claims else None
        blacklisted_ago = _blacklisted_seconds_ago(jti) if jti else None
        fresh_pair = _reissue_within_grace(claims, blacklisted_ago)
        if fresh_pair is not None:
            return Response(fresh_pair)
        # Expired, malformed and blacklisted tokens raise TokenError, which is not an
        # APIException, so DRF would answer 500. This mirrors SimpleJWT's own
        # TokenViewBase.post.
        error = InvalidToken(exc.args[0] if exc.args else None)
        _log_refresh_rejection(request, error.detail["detail"], blacklisted_ago)
        raise error from exc


class LogoutView(APIView):
    """Revoke a refresh token. The refresh token is the only credential it needs.

    The app signs the user out locally first and revokes in the background with just
    the refresh token it captured, often after the access token has expired, so asking
    for an access token would lose the revoke. Whatever is posted, the answer is 204.
    """

    permission_classes = [AllowAny]
    # As for RefreshView: no access token, stale header or session cookie may decide
    # the outcome. It shares that view's throttle.
    authentication_classes = []
    throttle_classes = [RefreshRateThrottle]

    def get_authenticate_header(self, request):
        # Keep the Bearer challenge rather than let DRF turn any 401 into a 403.
        return 'Bearer realm="api"'

    def post(self, request):
        token = _posted_refresh_token(request)
        claims = _verified_refresh_claims(token) if token is not None else None
        revoked = False
        if claims is not None:
            with suppress(TokenError):  # already blacklisted: nothing left to revoke
                RefreshToken(token).blacklist()
                revoked = True
        # Whoever is named is the owner of a verified refresh token, never anything
        # merely claimed.
        user_id = (
            _loggable_id(claims.get(api_settings.USER_ID_CLAIM)) if claims else None
        )
        logger.info("Auth logout: user_id=%s revoked=%s", user_id or "unknown", revoked)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetView(APIView):
    """Send a password reset email using Django's built-in form."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        form = PasswordResetForm(data={"email": _json_object(request).get("email")})
        if not form.is_valid():
            raise ValidationError(_form_errors(form))
        # Every well-formed address gets the same answer, whether or not an account
        # exists and whether or not the email could be sent. Anything else would tell
        # a stranger which addresses are registered. Only the exception's type is
        # logged: its message can carry an address or a link.
        try:
            form.save(request=request)
        except Exception as exc:  # noqa: BLE001 - the caller must not see this
            logger.error(  # noqa: TRY400
                "Auth password reset email failed (%s)",
                type(exc).__name__,
            )
        return Response({"detail": "If an account exists, a reset email has been sent."})


class PasswordResetConfirmView(APIView):
    """Confirm a password reset from mobile deep-link params."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        uid, token, new_password = _require_strings(
            _json_object(request),
            "uid",
            "token",
            "new_password",
        )
        User = get_user_model()
        try:
            user = User.objects.get(pk=urlsafe_base64_decode(uid).decode())
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None
        if user is None or not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "Invalid or expired reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        form = SetPasswordForm(
            user=user,
            data={"new_password1": new_password, "new_password2": new_password},
        )
        form.is_valid()
        if form.errors:
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        form.save()
        return Response({"detail": "Password reset complete."})


class AppleAuthView(APIView):
    """Phase 2 hook for Sign in with Apple."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        return Response(
            {"detail": "Sign in with Apple is planned for a later API slice."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
