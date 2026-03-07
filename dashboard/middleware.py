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
