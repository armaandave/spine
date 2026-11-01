"""Apple push notifications for follows, follow requests and reports.

Nothing is sent unless the APNS_* settings hold a push key. Sends run in Celery once the
request's transaction commits, so a slow or failing Apple never holds up a follow.
"""

import base64
import logging
import time

import httpx
import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from django.conf import settings
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from social.models import PushDevice

logger = logging.getLogger(__name__)

HOSTS = {
    PushDevice.Environment.SANDBOX: "https://api.sandbox.push.apple.com",
    PushDevice.Environment.PRODUCTION: "https://api.push.apple.com",
}
# Apple accepts a provider token for an hour, and refuses new ones more often than
# every 20 minutes.
TOKEN_LIFETIME = 50 * 60
# Apple's reasons for a device token that will never work again.
DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}

# (the key settings it was signed with, token, signed at)
_token = None


def is_configured():
    """Return whether all three push key settings are set."""
    return all(
        str(value or "").strip()
        for value in (
            settings.APNS_TEAM_ID,
            settings.APNS_KEY_ID,
            settings.APNS_PRIVATE_KEY,
        )
    )


@sensitive_variables("signer", "private_key", "token")
def provider_token():
    """Return a signed provider token, or None when the key can't be read."""
    global _token  # noqa: PLW0603
    signer = (
        str(settings.APNS_TEAM_ID).strip(),
        str(settings.APNS_KEY_ID).strip(),
        str(settings.APNS_PRIVATE_KEY),
    )
    now = int(time.time())
    if _token is not None and _token[0] == signer and now - _token[2] < TOKEN_LIFETIME:
        return _token[1]
    try:
        private_key = base64.b64decode("".join(signer[2].split()), validate=True)
        token = jwt.encode(
            {"iss": signer[0], "iat": now},
            private_key,
            algorithm="ES256",
            headers={"kid": signer[1]},
        )
    except (ValueError, TypeError, jwt.PyJWTError, UnsupportedAlgorithm):
        # Never chain or repeat the error: it could quote the key.
        logger.error("The push key can't be read")  # noqa: TRY400
        return None
    _token = (signer, token, now)
    return token


def notify(user, body, route):
    """Push ``body`` to each of ``user``'s phones once the transaction commits.

    ``route`` tells the app what a tap opens: ``{"kind": "profile", "username": ...}``
    or ``{"kind": "follow_requests"}``.
    """
    if not is_configured():
        return
    # Imported here: the task module imports this one.
    from social.tasks import send_push  # noqa: PLC0415

    transaction.on_commit(lambda: send_push.delay(user.id, body, route))


def send(user_id, body, route):
    """Send one push to each of a user's phones, dropping tokens Apple says are dead."""
    devices = list(PushDevice.objects.filter(user_id=user_id))
    if not devices or not is_configured():
        return 0
    token = provider_token()
    if token is None:
        return 0
    payload = {"aps": {"alert": {"body": body}, "sound": "default"}, "route": route}
    sent = 0
    with _client() as client:
        for device in devices:
            try:
                response = client.post(
                    f"{HOSTS[device.environment]}/3/device/{device.token}",
                    json=payload,
                    headers={
                        "authorization": f"bearer {token}",
                        "apns-topic": device.bundle_id,
                        "apns-push-type": "alert",
                        "apns-priority": "10",
                    },
                )
            except httpx.HTTPError as error:
                logger.warning("Couldn't reach Apple: %s", type(error).__name__)
                continue
            if response.status_code == httpx.codes.OK:
                sent += 1
                continue
            reason = _reason(response)
            if response.status_code == httpx.codes.GONE or reason in DEAD_TOKEN_REASONS:
                device.delete()
            else:
                logger.warning(
                    "Apple refused a push: %s %s",
                    response.status_code,
                    reason,
                )
    return sent


def _client():
    return httpx.Client(http2=True, timeout=10)


def _reason(response):
    try:
        return response.json().get("reason", "")
    except ValueError:
        return ""
