from django.shortcuts import redirect

EXEMPT_PATHS = [
    "/accounts/login/",
    "/accounts/logout/",
    "/accounts/password-change/",
    "/accounts/password-change/done/",
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

            if request.path not in EXEMPT_PATHS:
                try:
                    if request.user.staff_profile.must_change_password:
                        return redirect("/accounts/password-change/")
                except Exception:
                    pass

        return self.get_response(request)
