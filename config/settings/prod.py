"""
Production settings — security hardened, JSON logging, no DEBUG.
"""

from .base import *  # noqa: F401, F403

DEBUG = False

# HTTPS / proxy
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
