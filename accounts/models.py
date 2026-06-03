from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user with a role that gates product access."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        LEASING = "leasing", "Leasing"
        MAINTENANCE = "maintenance", "Maintenance"

    role = models.CharField(max_length=20, choices=Role.choices)

    ROLE_PRODUCT_ACCESS = {
        Role.ADMIN: {"leasing", "maintenance", "monthly_owner_notes", "reporting"},
        Role.LEASING: {"leasing"},
        Role.MAINTENANCE: {"maintenance"},
    }

    def can_access(self, product: str) -> bool:
        """Return True if this user's role grants access to the given product."""
        return product in self.ROLE_PRODUCT_ACCESS.get(self.role, set())
