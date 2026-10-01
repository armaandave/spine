from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AuthRateThrottle(AnonRateThrottle):
    """Tighter throttle for login/register/password endpoints.

    Keyed by client address whether or not the request is authenticated.
    AnonRateThrottle exempts authenticated requests, which let anyone holding a valid
    access token (an account takes seconds to register) send unlimited login
    attempts, sign-ups or password-reset emails.
    """

    scope = "auth"

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class RefreshRateThrottle(AuthRateThrottle):
    """Throttle for token refresh, in a bucket of its own.

    Refresh cannot be brute-forced the way a password can: it needs an HMAC-signed
    token. It gets a higher limit than login, and a separate bucket so that if
    real-IP detection ever fails (every visitor then shares one address) refreshes
    and logins cannot throttle each other. The rate is a class attribute, so no
    settings entry is needed.
    """

    scope = "auth_refresh"
    rate = "60/min"


class SearchRateThrottle(UserRateThrottle):
    """Throttle provider-backed search/detail endpoints."""

    scope = "search"
