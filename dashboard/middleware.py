from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect

EXEMPT_PATHS = [
    "/accounts/login/",
    "/accounts/logout/",
    "/accounts/password-change/",
    "/accounts/password-change/done/",
    "/accounts/password-reset/",
    "/accounts/password-reset/done/",
    "/accounts/password-reset/complete/",
]

EXEMPT_PREFIXES = [
    "/accounts/password-reset/confirm/",
]


class LoginRateLimitMiddleware:
    """
    Block IPs that fail login more than 10 times in 10 minutes.
    Uses Django's cache backend — no extra packages required.
    """
    MAX_ATTEMPTS = 10
    WINDOW = 600  # seconds (10 minutes)
    LOCKOUT = 900  # seconds (15 minutes)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/accounts/login/" and request.method == "POST":
            ip = self._get_ip(request)
            lockout_key = "login_lockout_" + ip
            attempt_key = "login_attempts_" + ip

            if cache.get(lockout_key):
                return HttpResponse(
                    "Too many failed login attempts. Please try again in 15 minutes.",
                    status=429,
                    content_type="text/plain",
                )

            response = self.get_response(request)

            # 200 on POST to /login/ means the form was re-rendered (login failed)
            if response.status_code == 200:
                attempts = cache.get(attempt_key, 0) + 1
                cache.set(attempt_key, attempts, self.WINDOW)
                if attempts >= self.MAX_ATTEMPTS:
                    cache.set(lockout_key, True, self.LOCKOUT)
                    cache.delete(attempt_key)
            else:
                # Successful login — clear counters
                cache.delete(attempt_key)
                cache.delete(lockout_key)

            return response

        return self.get_response(request)

    def _get_ip(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "unknown")


class ForcePasswordChangeMiddleware:
    """Redirect users who must change their temporary password."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Skip for non-dashboard paths (API, webhooks, owner portal, static)
            if not request.path.startswith(("/dashboard/", "/accounts/")):
                return self.get_response(request)

            is_exempt = request.path in EXEMPT_PATHS or any(
                request.path.startswith(p) for p in EXEMPT_PREFIXES
            )
            if not is_exempt:
                try:
                    if request.user.staff_profile.must_change_password:
                        return redirect("/accounts/password-change/")
                except Exception:
                    pass

        return self.get_response(request)
