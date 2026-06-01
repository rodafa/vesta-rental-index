"""
Development settings — DEBUG on, plain-text console logging for readability.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

# Override JSON logging with plain text for local development
LOGGING["formatters"]["plain"] = {  # noqa: F405
    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
}
LOGGING["handlers"]["console"]["formatter"] = "plain"  # noqa: F405
